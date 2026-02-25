#!/bin/bash
# EVTXorcist Splunk MCP CLI Wrapper
# This script runs the Splunk MCP server over standard input/output (stdio) inside a Docker container.
# It is designed to be plugged directly into MCP clients like Gemini CLI, Claude Desktop, Cursor, etc.
#
# Requirements: 
# - Docker must be running
# - The evtxorcist docker-compose stack should be up to provide the Splunk instance

# Get the absolute path to the directory containing this script
DIR="$(cd "$(dirname "$0")" && pwd)"

# Run an ephemeral container connected to the compose network
exec docker run -i --rm \
  --network evtxorcist_default \
  -e SPLUNK_HOST=splunk \
  -e SPLUNK_PORT=8089 \
  -e SPLUNK_USERNAME=admin \
  -e SPLUNK_PASSWORD=EvtxAdmin123! \
  -e VERIFY_SSL=false \
  -v "${DIR}/splunk_config/splunk_mcp.py:/app/splunk_mcp.py:ro" \
  evtxorcist-splunk-mcp python3 /app/splunk_mcp.py stdio
