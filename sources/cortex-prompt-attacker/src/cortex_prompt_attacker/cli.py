"""
cortex-prompt-attacker CLI.

Subcommands:
    list-mutators     print built-in mutator names as JSON
    list-scorers      print built-in scorer names as JSON
    validate          load probes and report parse errors (no network)
    run               execute probes against a target, write JSONL

Examples:

    cortex-prompt-attacker list-mutators
    cortex-prompt-attacker validate --probes scenarios/airs/probes/llm01/
    cortex-prompt-attacker run \\
        --probes scenarios/airs/probes/llm01/ \\
        --target-url http://127.0.0.1:8089/owasp/llm01/chat \\
        --mutators noop,base64,leetspeak \\
        --scorers system_prompt_leak,instruction_override \\
        --iterations 1 \\
        --out /tmp/airs-001-events.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .loader import load_probes_from_dir, load_probes_from_paths
from .mutators import MUTATOR_REGISTRY
from .pipeline import Pipeline
from .runner import Runner, open_jsonl_writer
from .scorers import SCORER_REGISTRY
from .targets import HTTPTarget


def _csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cortex-prompt-attacker",
        description="Probe → Mutator → Target → Scorer pipeline for AIRS validation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-mutators", help="Print built-in mutator names")
    sub.add_parser("list-scorers", help="Print built-in scorer names")

    p_val = sub.add_parser("validate", help="Load probes and report errors")
    p_val.add_argument("--probes", required=True,
                       help="Probes directory or glob (file/path).")

    p_run = sub.add_parser("run", help="Execute probes against a target.")
    p_run.add_argument("--probes", required=True,
                       help="Probes directory, glob, or file path(s) (comma-sep).")
    p_run.add_argument("--target-url", required=True)
    p_run.add_argument("--mutators", default="",
                       help="Comma-separated mutator chain (default: noop).")
    p_run.add_argument("--scorers", default="",
                       help="Comma-separated default scorer list "
                            "(empty: vulnerable_flag).")
    p_run.add_argument("--iterations", type=int, default=1)
    p_run.add_argument("--timeout", type=float, default=30.0,
                       help="Per-request HTTP timeout in seconds.")
    p_run.add_argument("--out", default="-",
                       help="Output JSONL path; '-' for stdout (default).")
    p_run.add_argument("--header", action="append", default=[],
                       metavar="K=V",
                       help="Extra request header (repeatable).")
    p_run.add_argument("--insecure", action="store_true",
                       help="Skip TLS cert verification.")

    return parser.parse_args(argv)


def cmd_list_mutators(_args: argparse.Namespace) -> int:
    print(json.dumps(sorted(MUTATOR_REGISTRY), indent=2))
    return 0


def cmd_list_scorers(_args: argparse.Namespace) -> int:
    print(json.dumps(sorted(SCORER_REGISTRY), indent=2))
    return 0


def _load_probes(spec: str):
    p = Path(spec)
    if p.is_dir():
        return load_probes_from_dir(p)
    return load_probes_from_paths(_csv(spec))


def cmd_validate(args: argparse.Namespace) -> int:
    result = _load_probes(args.probes)
    print(json.dumps({
        "loaded": len(result.probes),
        "errors": [{"path": str(e.path), "message": e.message} for e in result.errors],
        "probes": [
            {"name": p.name, "type": p.type, "owasp_id": p.owasp_id,
             "severity": p.severity.value}
            for p in result.probes
        ],
    }, indent=2))
    return 0 if result.ok else 1


def _parse_headers(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            print(f"warning: ignoring header '{raw}' (expected K=V)", file=sys.stderr)
            continue
        k, v = raw.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_run(args: argparse.Namespace) -> int:
    loaded = _load_probes(args.probes)
    if not loaded.ok:
        for e in loaded.errors:
            print(f"error: {e.path}: {e.message}", file=sys.stderr)
        return 2
    if not loaded.probes:
        print("error: no probes loaded", file=sys.stderr)
        return 2

    target = HTTPTarget(
        args.target_url,
        timeout_seconds=args.timeout,
        headers=_parse_headers(args.header),
        verify_tls=not args.insecure,
    )
    pipeline = Pipeline(
        target,
        default_mutators=_csv(args.mutators),
        default_scorers=_csv(args.scorers),
    )

    if args.out == "-":
        writer = sys.stdout
        owns_writer = False
    else:
        writer = open_jsonl_writer(args.out)
        owns_writer = True

    try:
        runner = Runner(pipeline, iterations=args.iterations, out_stream=writer)
        summary = runner.run(loaded.probes)
    finally:
        if owns_writer:
            writer.close()
        target.close()

    # Write the human-readable summary to stderr so JSONL on stdout stays clean.
    print(json.dumps({"summary": summary.to_dict()}), file=sys.stderr)
    return 0 if summary.error_count == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return {
        "list-mutators": cmd_list_mutators,
        "list-scorers": cmd_list_scorers,
        "validate": cmd_validate,
        "run": cmd_run,
    }[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
