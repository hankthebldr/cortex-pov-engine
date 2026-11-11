"""``python -m mcp_server`` — the stdio transport, plus two offline listings.

    python -m mcp_server [--server URL] [--list-tools] [--manifest]
                         [--max-tenant-calls-per-minute N]

``--server`` defaults to ``$CORTEXSIM_URL`` or ``http://127.0.0.1:8888``.
``--list-tools`` and ``--manifest`` build the server and read the registered
tools back from it; neither makes a request. Otherwise the server speaks MCP
over stdio. stdout is the protocol channel, so logs go to stderr only: one
line per tenant-class call (tool id, tenant, queries_issued, duration).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

DEFAULT_URL = "http://127.0.0.1:8888"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mcp_server",
                                description="CortexSim MCP server over SimCore's REST API")
    p.add_argument("--server", default=os.environ.get("CORTEXSIM_URL") or DEFAULT_URL,
                   help="SimCore base URL (default: $CORTEXSIM_URL or %(default)s)")
    p.add_argument("--list-tools", action="store_true",
                   help="print canonical\\twire\\tclass\\tnetwork per registered tool, then 'N tools'")
    p.add_argument("--manifest", action="store_true", help="print the P-2 manifest as YAML")
    p.add_argument("--max-tenant-calls-per-minute", type=int, default=30, metavar="N",
                   help="tenant-class call ceiling for this process (default: %(default)s)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    from .client import SimCoreClient  # noqa: PLC0415
    from .manifest import build_manifest, validate_manifest  # noqa: PLC0415
    from .naming import wire_name  # noqa: PLC0415
    from .ratelimit import TokenBucket  # noqa: PLC0415
    from .registry import ToolContext  # noqa: PLC0415
    from .server import all_specs, build, registered_ids  # noqa: PLC0415

    ctx = ToolContext(client=SimCoreClient(args.server),
                      bucket=TokenBucket(args.max_tenant_calls_per_minute))
    mcp = build(ctx)
    by_id = {s.id: s for s in all_specs()}
    ids = registered_ids(mcp)

    if args.list_tools:
        for cid in ids:
            spec = by_id[cid]
            tool = mcp._tool_manager.get_tool(wire_name(cid))
            print(f"{cid}\t{tool.name}\t{spec.klass}\t{spec.network}")
        print(f"{len(ids)} tools")
        return 0

    if args.manifest:
        import yaml  # noqa: PLC0415

        doc = build_manifest([by_id[cid] for cid in ids])
        validate_manifest(doc)
        sys.stdout.write(yaml.safe_dump(doc, sort_keys=False))
        return 0

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    log = logging.getLogger("cortexsim.mcp")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    log.info("stdio MCP server: %d tools, SimCore at %s", len(ids), args.server)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
