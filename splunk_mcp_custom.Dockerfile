FROM python:3.10-slim

WORKDIR /app

# Install build dependencies, curl for healthcheck, and uv
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Clone the repository
RUN git clone https://github.com/livehybrid/splunk-mcp.git /app

# First freeze dependencies to allow modification
RUN uv pip compile pyproject.toml -o requirements.txt

# Modify the frozen requirements to downgrade fastmcp to 0.4.1 (known compatible version for description kwarg before API change)
RUN sed -i 's/fastmcp==.*/fastmcp==0.4.1/' requirements.txt

# Install the dependencies from the modified requirements
RUN uv pip install --system -r requirements.txt

# Install the package itself in editable mode
RUN uv pip install --system -e .

RUN mkdir -p /app/config

# Run the MCP server
CMD ["python", "splunk_mcp.py"]
