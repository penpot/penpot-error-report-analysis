#!/usr/bin/env bash
# Runs the analysis platform's two servers alongside the reverse proxy fronting them,
# terminating the container as soon as any of the three processes exits.

set -uo pipefail

WEB_PORT=5100
MCP_PORT=5101

log() {
    echo "ENTRYPOINT $(date -u +%Y-%m-%dT%H:%M:%SZ) - $*" >&2
}

# terminates the processes that are still running
shutdown() {
    trap - INT TERM EXIT
    log "Shutting down"
    kill "${pids[@]}" 2>/dev/null
    wait "${pids[@]}" 2>/dev/null
}

pids=()
trap shutdown INT TERM EXIT

# start the servers, each bound to the loopback interface, such that they are reachable
# through the proxy only
log "Starting the dashboard on port ${WEB_PORT}"
error-analysis-web --host 127.0.0.1 --port "${WEB_PORT}" &
pids+=("$!")

log "Starting the MCP server on port ${MCP_PORT}"
error-analysis-mcp --transport streamable-http --host 127.0.0.1 --port "${MCP_PORT}" &
pids+=("$!")

log "Starting the proxy on port 8080"
nginx -g 'daemon off;' &
pids+=("$!")

# await the first process to exit and propagate its status, the trap terminating the others
wait -n
status=$?
log "A server process exited with status ${status}"
exit "${status}"
