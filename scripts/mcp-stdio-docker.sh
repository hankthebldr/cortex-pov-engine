#!/usr/bin/env bash
# Exec the stdio MCP server inside the running SimCore container.
#
#   scripts/mcp-stdio-docker.sh [--print]
#
# Selects the container labelled com.docker.compose.service=simcore, narrowed
# by com.docker.compose.project=$CORTEXSIM_COMPOSE_PROJECT when that is set.
# Exactly one match is required. --print shows the command instead of running
# it. CORTEXSIM_MCP_ALLOW_HOST_ACTIONS is forwarded only when set here.
# stdio runs inside the container, so it needs no CORTEXSIM_MCP_TOKEN.
set -euo pipefail

# Exit codes: 0 ok (or exec), 2 no/several containers or docker unusable, 64 bad usage.
print_only=0
case "${1:-}" in
  "") ;;
  --print) print_only=1 ;;
  *) echo "usage: $0 [--print]" >&2; exit 64 ;;
esac
if [ "$#" -gt 1 ]; then echo "usage: $0 [--print]" >&2; exit 64; fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found on PATH" >&2
  exit 2
fi

filters=(--filter "label=com.docker.compose.service=simcore")
if [ -n "${CORTEXSIM_COMPOSE_PROJECT:-}" ]; then
  filters+=(--filter "label=com.docker.compose.project=${CORTEXSIM_COMPOSE_PROJECT}")
fi

if ! ps_out="$(docker ps -q "${filters[@]}")"; then
  echo "docker ps failed: is Docker running?" >&2
  exit 2
fi
ids=()
while IFS= read -r line; do
  [ -n "$line" ] && ids+=("$line")
done <<< "$ps_out"

if [ "${#ids[@]}" -eq 0 ]; then
  echo "no SimCore container is running (docker ps found none)" >&2
  exit 2
fi
if [ "${#ids[@]}" -gt 1 ]; then
  echo "${#ids[@]} SimCore containers; set CORTEXSIM_COMPOSE_PROJECT" >&2
  exit 2
fi

cmd=(docker exec -i)
if [ -n "${CORTEXSIM_MCP_ALLOW_HOST_ACTIONS:-}" ]; then
  cmd+=(-e CORTEXSIM_MCP_ALLOW_HOST_ACTIONS)
fi
cmd+=("${ids[0]}" python -m mcp_server)

if [ "$print_only" -eq 1 ]; then
  echo "${cmd[*]}"
  exit 0
fi
exec "${cmd[@]}"
