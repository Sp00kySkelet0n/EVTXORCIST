FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Install system deps (for evtx-rs and chainsaw)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Chainsaw + Sigma rules
ARG TARGETARCH
RUN CHAINSAW_VERSION="v2.14.1" && \
    if [ "$TARGETARCH" = "arm64" ]; then \
    CHAINSAW_ARCH="aarch64-unknown-linux-gnu"; \
    else \
    CHAINSAW_ARCH="x86_64-unknown-linux-gnu"; \
    fi && \
    curl -L "https://github.com/WithSecureLabs/chainsaw/releases/download/${CHAINSAW_VERSION}/chainsaw_${CHAINSAW_ARCH}.tar.gz" \
    -o /tmp/chainsaw.tar.gz && \
    tar xzf /tmp/chainsaw.tar.gz -C /usr/local/bin/ --strip-components=1 && \
    chmod +x /usr/local/bin/chainsaw && \
    rm /tmp/chainsaw.tar.gz

# Clone Sigma rules & Chainsaw mappings
RUN git clone --depth 1 https://github.com/SigmaHQ/sigma.git /opt/sigma && \
    git clone --depth 1 https://github.com/WithSecureLabs/chainsaw.git /tmp/chainsaw-repo && \
    mkdir -p /opt/chainsaw && \
    cp -r /tmp/chainsaw-repo/mappings /opt/chainsaw/mappings && \
    cp -r /tmp/chainsaw-repo/rules /opt/chainsaw/rules && \
    rm -rf /tmp/chainsaw-repo

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Expose FastAPI port
EXPOSE 8000

# Run app with hot reload
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--ws-ping-interval", "300", "--ws-ping-timeout", "300"]