# EVTXorcist

<p align="center">
  <img src="./evtxorcist.jpg" alt="EVTXorcist Logo" width="100%"/>
</p>

## Overview

<p align="center">
  <img src="./screenshot.png" alt="EVTXorcist Interface Overview" width="100%"/>
</p>

**EVTXorcist** is an automated Windows Event Log (EVTX) analysis platform designed for incident responders, threat hunters, and security analysts. It bridges the gap between raw event logs, high-speed Sigma rule matching, and AI-powered analysis in a unified, retro-terminal interface.

Upload your EVTX files and EVTXorcist will seamlessly orchestrate:
1. **High-Speed Parsing**: Analyzing logs using [Chainsaw](https://github.com/WithSecureLabs/chainsaw) to identify malicious activity via Sigma rules.
2. **Automated Ingestion**: Indexing both the raw EVTX data and the enriched Chainsaw detections into a tailored, built-in Splunk instance.
3. **AI-Powered Investigation**: Providing a natural-language AI Analyst (powered by local LLMs via Ollama) equipped with Splunk search capabilities to help you intuitively investigate the data.

---

## Key Features

- **Retro Terminal UI**: A responsive, hacker-themed web interface offering an immersive environment for uploading files and conversing with the AI.
- **Automated Processing**: No manual log parsing or conversion required. EVTXorcist handles EVTX extraction and Sigma matching completely hands-off.
- **Built-in Splunk Environment**: Includes a pre-configured Splunk container specifically tuned for ingesting JSON-formatted EVTX and Chainsaw results automatically.
- **AI Investigation Agent**: Chat with an LLM directly connected to your Splunk data via the Model Context Protocol (MCP). The AI acts autonomously to write queries, analyze results, and investigate potential compromises.
- **Multi-Round Tool Calling**: The AI agent operates in an agentic loop. It iteratively refines its searches, checks available data types, and pivots based on intermediate findings to ensure comprehensive answers.

---

## Architecture

| Component | Technology | Description |
|-----------|------------|-------------|
| **Backend** | FastAPI (Python) | Handles uploads, orchestrates Chainsaw, and manages the WebSocket chat interface. |
| **Frontend** | HTML / CSS / JS | Pure, dependency-free terminal aesthetic. |
| **Log Engine** | Chainsaw (Rust) | Provides high-speed Sigma matching and EVTX-to-JSON conversion. |
| **Data Store**| Splunk Enterprise | Docker-containerized Splunk for robust search and indexing. |
| **AI Brain** | Ollama + MCP | Local LLMs empowered with a custom Splunk Model Context Protocol server for executing searches. |

---

## Quick Start

1. **Deploy**: Start the application stack using Docker Compose.
   ```bash
   docker-compose up -d --build
   ```
2. **Access**: Navigate to the web UI.
3. **Upload**: Drag and drop a folder or ZIP containing EVTX files into the terminal interface.
4. **Investigate**: Once processing is complete, switch to the Chat interface and ask your AI analyst questions about the data.
   - *Example: "What commands were executed by the Administrator?"*
   - *Example: "Are there any PowerShell execution alerts in Chainsaw?"*

---

## Splunk Data Structure

When questioning the data, EVTXorcist automatically stores it under `index=main` with two primary sourcetypes to optimize search performance:

- **`sourcetype=chainsaw`**: Contains pre-processed Sigma detections (fields: `name`, `level`, `tags`, etc.). The AI prioritizes searching this for rapid threat identification.
- **`sourcetype=_json`**: Contains the complete, raw Windows Event Log data (fields: `Event.System.EventID`, `Event.System.Computer`, `Event.EventData.*`, etc.). Used for deep-dive hunts when alerts are not present. 
- *Upload names are stored in the `source` field, representing individual "Cases".*
