import os

# Paths
UPLOAD_DIR = "/tmp/uploads"
OUTPUT_DIR = "/tmp/output"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# External Services
OLLAMA_HOST = "http://host.docker.internal:11434"
OLLAMA_BASE = OLLAMA_HOST
MCP_SSE_URL = "http://splunk-mcp:8000/sse"
