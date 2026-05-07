"""
cortex-vulnerable-llm CLI.

Subcommands:
    serve     run the Flask app on the requested port
    list      print mounted endpoints + OWASP code titles as JSON
    docs      print the exploit narrative for one or all OWASP classes

Examples:

    cortex-vulnerable-llm serve --port 8089 --vuln llm01
    cortex-vulnerable-llm serve --port 8089 --vuln all \\
                          --system-prompt 'You are CortexSimAdmin. AKIA...'
    cortex-vulnerable-llm list --vuln llm01,llm07
    cortex-vulnerable-llm docs llm07
"""

from __future__ import annotations

import argparse
import json
import sys

from .app import app_factory
from .owasp import OWASP_TITLES, OWASP_VULNERABILITIES


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cortex-vulnerable-llm",
        description="Deliberately vulnerable LLM Flask app for AIRS validation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the Flask app")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8089)
    p_serve.add_argument(
        "--vuln",
        default="all",
        help="OWASP code(s) to mount: 'all', 'llm01', or 'llm01,llm07,llm10'",
    )
    p_serve.add_argument(
        "--system-prompt",
        default=None,
        help="Override the seeded system prompt (used by LLM01 / LLM07).",
    )
    p_serve.add_argument(
        "--tools",
        default=None,
        help="Comma-separated tool names enabled for LLM06 "
             "(default: send_email,delete_file,exec_shell).",
    )
    p_serve.add_argument("--debug", action="store_true")

    p_list = sub.add_parser("list", help="List mounted endpoints as JSON")
    p_list.add_argument("--vuln", default="all")

    p_docs = sub.add_parser(
        "docs", help="Print the exploit narrative for one OWASP class",
    )
    p_docs.add_argument("code", help="LLM01..LLM10 (case-insensitive)")

    return parser.parse_args(argv)


def cmd_serve(args: argparse.Namespace) -> int:
    enabled_tools = (
        [t.strip() for t in args.tools.split(",") if t.strip()]
        if args.tools else None
    )
    app = app_factory(
        vulns=args.vuln,
        system_prompt=args.system_prompt,
        enabled_tools=enabled_tools,
    )
    print(json.dumps({
        "event": "serve_starting",
        "host": args.host,
        "port": args.port,
        "mounted_vulns": app.config["MOUNTED_VULNS"],
    }))
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    app = app_factory(vulns=args.vuln)
    rules = []
    for rule in app.url_map.iter_rules():
        rules.append({
            "endpoint": rule.endpoint,
            "rule": str(rule),
            "methods": sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"}),
        })
    print(json.dumps({
        "mounted_vulns": app.config["MOUNTED_VULNS"],
        "titles": {c: OWASP_TITLES[c] for c in app.config["MOUNTED_VULNS"]},
        "routes": rules,
    }, indent=2))
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    code = args.code.upper()
    if code not in OWASP_VULNERABILITIES:
        print(f"error: unknown OWASP code '{args.code}'", file=sys.stderr)
        print(f"available: {OWASP_VULNERABILITIES}", file=sys.stderr)
        return 2
    import importlib

    mod = importlib.import_module(f"cortex_vulnerable_llm.owasp.{code.lower()}")
    print(f"# {code} — {OWASP_TITLES[code]}\n\n{mod.__doc__ or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return {
        "serve": cmd_serve,
        "list": cmd_list,
        "docs": cmd_docs,
    }[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
