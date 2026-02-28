<div align="center">
  <img src="img/evtxorcist.jpg" alt="EVTXorcist Logo" width="400"/>

  # EVTXorcist
  **Automated DFIR & AI-Powered Threat Hunting Platform**

  
[![Sp00kySkelet0n](https://img.shields.io/badge/Sp00kySkelet0n-GitHub-181717?style=for-the-badge&logo=github)](https://github.com/Sp00kySkelet0n)

</div>


## ⚡ What is EVTXorcist?

**EVTXorcist** is a self-contained, automated Windows Event Log (EVTX) analysis platform designed for incident responders, threat hunters, and security analysts. 

It bridges the gap between raw event logs, high-speed Sigma rule matching, and AI-powered investigation through the Model Context Protocol (MCP) in a unified, retro-terminal interface.

<div align="center">
  <img src="img/screenshot.png" alt="EVTXorcist Interface Overview" width="800" style="border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.5);"/>
</div>

---

## Key Features

*   🎯 **Zero-Touch Processing** — Drag and drop a folder or ZIP of EVTX files. EVTXorcist handles extraction, conversion, and Sigma matching entirely hands-off.
*   ⚡ **High-Speed Parsing** — Leverages [Chainsaw](https://github.com/WithSecureLabs/chainsaw) (Rust) for lightning-fast analysis of raw logs against the Sigma rule repository.
*   💾 **Built-in Splunk Analytics** — Includes a pre-configured, Dockerized Splunk instance tailored for ingesting JSON-formatted EVTX and Chainsaw alerts.
*   🤖 **AI Investigation Agent** — Chat natively with local LLMs (via Ollama) directly connected to your Splunk data.
*   🔌 **Model Context Protocol (MCP)** — Exposes a fully-functional MCP Server so your AI can autonomously write SPL queries, analyze results, and iteratively hunt for context.

---

## The Workflow

### 1. 🔍 Automated Sigma Hunting
Instantly see Critical, High, and Medium severity matches mapped securely to the MITRE ATT&CK framework moments after upload thanks to Chainsaw.

<div align="center">
  <img src="img/chainsaw_results.png" alt="Chainsaw Results Dashboard" width="700" style="border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.3);"/>
</div>

### 2. 💬 Agentic Chat Investigation
Switch to the Chat interface to interrogate your data. Ask natural language questions, and the AI will formulate Splunk queries, execute them via MCP, and summarize the findings.

<div align="center">
  <img src="img/chat.png" alt="Agentic Chat Interface" width="700" style="border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.3);"/>
</div>

### 3. 🧩 Universal MCP Compatibility
Don't want to use the web UI? No problem. The included `mcp-cli.sh` wrapper allows you to mount the Splunk MCP server into external AI tools like **Cursor**, **Gemini CLI**, or **Claude Desktop**.

<div align="center">
  <img src="img/mcp.png" alt="MCP Server Integration" width="700" style="border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.3);"/>
</div>

---

## 🛠️ Architecture Overview

| Component | Technology | Role |
| :--- | :--- | :--- |
| **Backend API** | [FastAPI (Python)](https://fastapi.tiangolo.com/) | Upload orchestration, Chainsaw subprocess management, and WebSocket loop. |
| **Frontend UI** | HTML / CSS / JS | Pure, terminal-themed interface utilizing `localStorage` caching. |
| **Data Store** | [Splunk](https://www.splunk.com/) | Containerized indexer for robust search capabilities. |
| **AI Brain** | [Ollama](https://ollama.com/) + [MCP](https://modelcontextprotocol.io/) | Local LLMs empowered with a custom Model Context Protocol server for executing autonomous Splunk searches. |


## Quick Start

### 1. Deploy the Environment
Ensure Docker and Docker Compose are installed. Spin up the application stack:
```bash
docker-compose up -d --build
```
*Note: The Splunk container may take a minute or two to fully initialize on the first run.*

### 2. Access the Platform
Navigate to `http://localhost:8000` in your web browser.

### 3. Upload & Investigate
* Drag and drop a folder or `.zip` file containing Windows `.evtx` files into the terminal interface.
* Review the extracted Chainsaw Alerts dashboard.
* Click **⬡ ASK AI AGENT** to start an automated hunt!
  * *Example: "What commands were executed by the Administrator?"*
  * *Example: "Are there any PowerShell execution alerts in Chainsaw?"*

---

## 🔌 External MCP Integration

You can easily plug the built-in Splunk MCP server into external tools like Cursor, Claude Desktop, or the Gemini CLI using the included wrapper script.

Simply configure your MCP client to execute:
```json
{
  "mcpServers": {
    "multivol": {
            "serverUrl": "http://127.0.0.1:8080/mcp",
            "headers": {
                "Authorization": "Bearer my-super-secret-token"
            }
        }
    }
}
```

*Note: The EVTXorcist docker-compose stack must be running to provide the backend Splunk instance.*


## Understanding the Splunk Data Structure

When questioning the data, EVTXorcist automatically stores it under `index=main` with two primary sourcetypes to optimize search performance for the AI:

*   🪚 **`sourcetype=chainsaw`**: Contains pre-processed Sigma detections (fields: `name`, `level`, `tags`, etc.). The AI prioritizes searching this for rapid threat identification.
*   📜 **`sourcetype=_json`**: Contains the complete, raw Windows Event Log data (fields: `Event.System.EventID`, `Event.System.Computer`, `Event.EventData.*`, etc.). Used for deep-dive hunts when alerts are not present. 

*Upload names are stored in the `source` field, representing individual "Cases".*
