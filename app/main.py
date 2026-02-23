from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
import os
import aiofiles
import asyncio
import logging
import uuid
import shutil
from zipfile import ZipFile
import json
import httpx
from datetime import datetime
from evtx import PyEvtxParser
from pydantic import BaseModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, StreamingResponse
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("evtx_uploader")

# App setup
app = FastAPI(title="EVTX Uploader")

# Allow CORS (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
UPLOAD_DIR = "/tmp/uploads"
OUTPUT_DIR = "/tmp/output"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Static frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("static/index.html", "r") as f:
        return f.read()

@app.get("/results/{session_id}", response_class=HTMLResponse)
async def serve_results(session_id: str):
    with open("static/results.html", "r") as f:
        return f.read()

@app.get("/chat", response_class=HTMLResponse)
async def serve_chat():
    with open("static/chat.html", "r") as f:
        return f.read()

@app.get("/api/results/{session_id}")
async def get_results_api(session_id: str):
    results_path = os.path.join(OUTPUT_DIR, session_id, "results.json")
    if os.path.exists(results_path):
        async with aiofiles.open(results_path, "r") as f:
            content = await f.read()
            return JSONResponse(content=json.loads(content))
    return JSONResponse(status_code=404, content={"error": "Results not found"})

@app.get("/download/{zip_name}")
async def download_zip(zip_name: str):
    zip_path = os.path.join(OUTPUT_DIR, zip_name)
    if os.path.exists(zip_path):
        return FileResponse(zip_path, filename=zip_name)
    return JSONResponse(status_code=404, content={"error": "Not found"})

@app.get("/download/{session_id}/{filename}")
async def download_session_file(session_id: str, filename: str):
    file_path = os.path.join(OUTPUT_DIR, session_id, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename)
    return JSONResponse(status_code=404, content={"error": "Not found"})

@app.get("/health/splunk")
async def check_splunk_health():
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get("http://splunk:8088/services/collector/health", timeout=2.0)
            if resp.status_code == 200:
                return JSONResponse(content={"status": "ok"})
            else:
                return JSONResponse(content={"status": "starting"})
    except httpx.RequestError:
        return JSONResponse(content={"status": "starting"})

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]

OLLAMA_HOST = "http://host.docker.internal:11434"
OLLAMA_BASE = OLLAMA_HOST  # keep for /api/tags
MCP_SSE_URL = "http://splunk-mcp:8000/sse"

# Cached MCP tools — fetched once, reused across requests
_cached_mcp_tools = None
_cached_mcp_tools_time = 0

@app.get("/api/models")
async def list_models():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [{"name": m["name"], "size": m.get("size", 0)} for m in data.get("models", [])]
            return JSONResponse(content={"models": models})
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": str(e), "models": []})

def _get_mcp_tools_sync() -> list[dict]:
    """Run MCP tool listing in a fresh event loop (isolated from WebSocket cancel scope)."""
    tools_result = []
    async def _inner():
        nonlocal tools_result
        try:
            async with sse_client(MCP_SSE_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    tools = []
                    for tool in response.tools:
                        props = {}
                        req = []
                        if hasattr(tool.inputSchema, "properties") and tool.inputSchema.properties:
                            props = {k: {"type": v.get("type", "string"), "description": v.get("description", "")} for k, v in tool.inputSchema.properties.items()}
                            req = getattr(tool.inputSchema, "required", [])
                        tools.append({
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description,
                                "parameters": {"type": "object", "properties": props, "required": req}
                            }
                        })
                    tools_result = tools
        except BaseException as e:
            logger.warning(f"MCP cleanup error (tools already captured): {e}")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_inner())
    finally:
        loop.close()
    return tools_result

def _call_mcp_tool_sync(tool_name: str, tool_args: dict) -> str:
    """Run MCP tool execution in a fresh event loop (isolated from WebSocket cancel scope)."""
    result_text = "No results"
    async def _inner():
        nonlocal result_text
        try:
            async with sse_client(MCP_SSE_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=tool_args)
                    result_text = result.content[0].text if result.content else "No results"
        except BaseException as e:
            # SSE cleanup may throw CancelledError/TaskGroup errors — ignore if we got a result
            logger.warning(f"MCP cleanup error (result already captured): {e}")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_inner())
    finally:
        loop.close()
    return result_text

async def _get_mcp_tools() -> list[dict]:
    """Run MCP tool listing in a separate thread, with 5-minute cache."""
    global _cached_mcp_tools, _cached_mcp_tools_time
    import time
    if _cached_mcp_tools is not None and (time.time() - _cached_mcp_tools_time) < 300:
        return _cached_mcp_tools
    tools = await asyncio.to_thread(_get_mcp_tools_sync)
    _cached_mcp_tools = tools
    _cached_mcp_tools_time = time.time()
    return tools

async def _call_mcp_tool(tool_name: str, tool_args: dict) -> str:
    """Run MCP tool execution in a separate thread to isolate from WebSocket cancel scope."""
    return await asyncio.to_thread(_call_mcp_tool_sync, tool_name, tool_args)

def _mcp_tools_to_ollama(mcp_tools: list[dict]) -> list:
    """Convert MCP tool dicts to ollama Tool objects for native tool calling."""
    ollama_tools = []
    for t in mcp_tools:
        fn = t.get("function", {})
        props = fn.get("parameters", {}).get("properties", {})
        required = fn.get("parameters", {}).get("required", [])
        # Build as plain dicts — ollama accepts these directly
        ollama_tools.append({
            "type": "function",
            "function": {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return ollama_tools

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Receive message from frontend
            data = await websocket.receive_json()
            messages = data.get("messages", [])
            model = data.get("model", "")
            if not model:
                # No hardcoded default — fetch first available model from Ollama
                try:
                    async with httpx.AsyncClient(timeout=5.0) as c:
                        r = await c.get(f"{OLLAMA_BASE}/api/tags")
                        model = r.json().get("models", [{}])[0].get("name", "")
                except Exception:
                    pass
                if not model:
                    await websocket.send_json({"error": "No model selected and none available", "done": True})
                    continue
            
            # Prepend system context
            if not any(m.get("role") == "system" for m in messages):
                messages.insert(0, {
                    "role": "system",
                    "content": (
                        "Respond ONLY in English. Never use any other language.\n"
                        "You are EVTXorcist's Splunk analyst. Call tools directly — never describe queries, never tell users to run queries. "
                        "PERSISTENCE: If a search returns no results, DO NOT give up. Try at least 3 different approaches:\n"
                        "  1. Broaden filters (remove sourcetype/source constraints, use wildcards)\n"
                        "  2. Try different EventIDs or fields (e.g. 4688 for processes, 4624 for logons, 11 for file creation)\n"
                        "  3. Search with wildcards: *keyword* in any field\n"
                        "  4. Check what data exists: | stats count by sourcetype, | stats count by Event.System.Channel\n"
                        "Only conclude 'not found' after exhausting multiple search strategies.\n\n"
                        "CASES: Each uploaded EVTX file set is a 'case' stored in the 'source' field. "
                        "To list cases use search_splunk with search_query='index=main | stats count by source'. "
                        "To query a case: search_query='index=main source=\"CaseName\" ...'\n"
                        "IMPORTANT: The 'source' field is ONLY for the CaseName/upload. Do NOT use it for endpoint hostnames. "
                        "For endpoint hostnames (like 'Client02'), use the 'Computer' or 'Event.System.Computer' field.\n\n"
                        "SEARCH PRIORITY: Always query chainsaw (sourcetype=chainsaw) FIRST — it contains pre-processed Sigma detections "
                        "with rule names, severity, and enriched fields. Only search raw EVTX (sourcetype=_json) if chainsaw doesn't have what you need.\n\n"
                        "DATA FORMAT & FIELDS:\n"
                        "- sourcetype=chainsaw (Sigma alerts): contains fields like name, level, tags, document.data.Event.*\n"
                        "- sourcetype=_json (raw EVTX): contains Windows event data. Example fields: 'Event.System.EventID', "
                        "'Event.System.Computer' (use this for hostnames, e.g., DC01.Main.local), 'Event.System.Channel', "
                        "'Event.EventData.Payload', 'Event.EventData.CommandLine' etc.\n"
                        "Example raw EVTX search: search_query='index=main sourcetype=_json Event.System.Computer=\"Client02\" Event.System.EventID=4103 Event.EventData.Payload=\"*Invoke-Expression*\"'\n\n"
                        "SPL: index=main sourcetype=chainsaw | index=main sourcetype=chainsaw level=critical | stats count by name\n\n"
                        "Rules: English only. Present results as tables/bullets. Never fabricate data."
                    )
                })

            # Trim history — keep last 10 messages + system for speed
            system_msgs = [m for m in messages if m.get("role") == "system"]
            other_msgs = [m for m in messages if m.get("role") != "system"]
            messages = system_msgs + other_msgs[-10:]

            try:
                import re
                from ollama import AsyncClient, ResponseError

                # Load MCP tools once before the loop
                mcp_tools = await _get_mcp_tools()
                ollama_tools = _mcp_tools_to_ollama(mcp_tools)
                for t in mcp_tools:
                    logger.info(f"Loaded tool: {t['function']['name']}")

                ollama_client = AsyncClient(host=OLLAMA_HOST)

                # Agentic tool-calling loop — up to MAX_ROUNDS iterations
                MAX_ROUNDS = 5
                supports_tools = True  # assume yes, flip on error

                for round_num in range(MAX_ROUNDS):
                    tool_calls = []
                    collected_content = ""

                    # Build chat kwargs
                    chat_kwargs = {
                        "model": model,
                        "messages": messages,
                        "stream": True,
                        "options": {"temperature": 0, "num_predict": 1024, "num_ctx": 4096},
                    }
                    if supports_tools and ollama_tools:
                        chat_kwargs["tools"] = ollama_tools

                    try:
                        stream = await ollama_client.chat(**chat_kwargs)
                        async for chunk in stream:
                            msg = chunk.get("message", {})

                            # Native tool calls from ollama library
                            if msg.get("tool_calls"):
                                for tc in msg["tool_calls"]:
                                    fn = tc.get("function", {})
                                    tool_calls.append({
                                        "function": {
                                            "name": fn.get("name", ""),
                                            "arguments": fn.get("arguments", {})
                                        }
                                    })

                            content = msg.get("content", "")
                            if content:
                                collected_content += content
                                await websocket.send_text(content)

                    except (ResponseError, Exception) as e:
                        err_str = str(e).lower()
                        if supports_tools and ("does not support tools" in err_str or "400" in err_str):
                            logger.warning(f"Model '{model}' doesn't support tools. Retrying without.")
                            supports_tools = False
                            chat_kwargs.pop("tools", None)
                            stream = await ollama_client.chat(**chat_kwargs)
                            async for chunk in stream:
                                content = chunk.get("message", {}).get("content", "")
                                if content:
                                    collected_content += content
                                    await websocket.send_text(content)
                        else:
                            raise

                    # Fallback: detect tool calls embedded as text in model output
                    if not tool_calls and collected_content:
                        detected = False

                        # Pattern 1: JSON-style {"name": "tool", "arguments": {...}}
                        json_pattern = re.findall(
                            r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{[^{}]*\})\s*\}',
                            collected_content, re.DOTALL
                        )
                        for tool_name_match, args_match in json_pattern:
                            try:
                                parsed_args = json.loads(args_match)
                                tool_calls.append({"function": {"name": tool_name_match, "arguments": parsed_args}})
                                logger.info(f"Detected JSON tool call: {tool_name_match}({parsed_args})")
                                detected = True
                            except json.JSONDecodeError:
                                pass

                        # Pattern 2: Python function-call style: search_splunk(query="...", key="val")
                        if not detected:
                            func_pattern = re.findall(
                                r'(\w+)\(\s*((?:\w+\s*=\s*"[^"]*"(?:\s*,\s*)?)+)\s*\)',
                                collected_content
                            )
                            for func_name, args_str in func_pattern:
                                kv_pairs = re.findall(r'(\w+)\s*=\s*"([^"]*)"', args_str)
                                if kv_pairs:
                                    parsed_args = dict(kv_pairs)
                                    if "query" in parsed_args and "search_query" not in parsed_args:
                                        parsed_args["search_query"] = parsed_args.pop("query")
                                    tool_calls.append({"function": {"name": func_name, "arguments": parsed_args}})
                                    logger.info(f"Detected function-call tool: {func_name}({parsed_args})")
                                    detected = True

                        # Pattern 3: Bare SPL query — catch index=main, search index=, | stats, etc.
                        # Also handles code-fenced queries (```...```) that thinking models output
                        if not detected:
                            # Strip markdown code fences first
                            clean_content = re.sub(r'```\w*\n?', '', collected_content)
                            # Match any line that looks like SPL
                            spl_matches = re.findall(
                                r'(?:^|\n)\s*((?:search\s+)?index=\S+[^\n]*)',
                                clean_content
                            )
                            if spl_matches:
                                # Execute ALL SPL queries found — thinking models often output multiple
                                for spl_query in spl_matches:
                                    spl_query = spl_query.strip()
                                    if not spl_query.startswith('search '):
                                        spl_query = 'search ' + spl_query
                                    tool_calls.append({"function": {"name": "search_splunk", "arguments": {"search_query": spl_query}}})
                                    logger.info(f"Detected bare SPL query: {spl_query}")

                    # No tool calls → model is done, break the loop
                    if not tool_calls:
                        break

                    # Execute tool calls and collect ALL results
                    all_results = []
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        tool_name = fn.get("name", "")
                        tool_args = fn.get("arguments", {})
                        logger.info(f"[Round {round_num+1}] Executing tool `{tool_name}` with args {tool_args}")
                        # Show the actual query/args in the chatbox
                        args_display = tool_args.get("search_query", json.dumps(tool_args, default=str))
                        await websocket.send_text(f"\n\n_`{tool_name}` → `{args_display}`_\n\n")

                        try:
                            result_text = await _call_mcp_tool(tool_name, tool_args)
                            logger.info(f"Tool `{tool_name}` returned {len(result_text)} chars")
                        except Exception as e:
                            logger.error(f"Tool execution failed: {e}")
                            result_text = f"Error executing tool: {e}"
                        all_results.append(f"[{tool_name}({tool_args})]\n{result_text}")

                    # Append combined results to conversation for next round
                    combined = "\n\n---\n\n".join(all_results)
                    messages.append({"role": "assistant", "content": collected_content})
                    messages.append({
                        "role": "user",
                        "content": f"[TOOL RESULTS — {len(all_results)} queries executed]\n{combined}\n\n"
                                   f"[Analyze ALL results above. If data answers the user's question, present it clearly. "
                                   f"If not, try different search approaches. Round {round_num+1} of {MAX_ROUNDS}.]"
                    })
                    collected_content = ""

                # Signal end of response
                await websocket.send_json({"done": True})

            except Exception as e:
                logger.error(f"Chat error: {e}", exc_info=True)
                await websocket.send_json({"error": str(e), "done": True})

    except WebSocketDisconnect:
        logger.info("Chat WebSocket disconnected")

@app.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    case_name: str = Form("Untitled Case"),
    index: str = Form("evtx_index"),
    destination: str = Form("elasticsearch"),
    splunk_url: str = Form(None),
    splunk_token: str = Form(None),
    es_host: str = Form("elasticsearch"),
    es_port: int = Form(9200)
):
    saved_files = []
    json_files = []
    evtx_paths = []

    # Unique session ID with case name slug
    import re
    case_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', case_name.strip())[:50]
    session_id = f"{case_slug}_{uuid.uuid4().hex[:8]}"
    session_folder = os.path.join(OUTPUT_DIR, session_id)
    os.makedirs(session_folder, exist_ok=True)

    # Process each file — filter for .evtx (supports folder uploads)
    for file in files:
        # Get just the filename (strip folder path from webkitdirectory uploads)
        filename = os.path.basename(file.filename)
        if not filename.lower().endswith(".evtx"):
            logger.info(f"Skipping non-EVTX file: {filename}")
            continue

        path = os.path.join(UPLOAD_DIR, filename)

        # Save uploaded EVTX
        async with aiofiles.open(path, "wb") as buffer:
            content = await file.read()
            await buffer.write(content)
        saved_files.append(filename)
        evtx_paths.append(path)

        logger.info(f"Indexing: {filename} (index: {index})")

        try:
            # Parse EVTX to raw JSON records
            json_records = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: parse_evtx_to_json(path)
            )

            # Write to per-session folder
            json_filename = filename + ".json"
            json_path = os.path.join(session_folder, json_filename)
            async with aiofiles.open(json_path, "w") as jf:
                await jf.write(json.dumps(json_records, indent=2))
            
            json_files.append(json_path)

            logger.info(f"Parsed {file.filename}, pushing to {destination}...")

            # Push to Elasticsearch or Splunk
            if destination == "elasticsearch":
                await push_to_elasticsearch(json_records, es_host, es_port, index)
            elif destination == "splunk":
                s_url = splunk_url or "http://splunk:8088/services/collector/event"
                s_token = splunk_token or "11111111-1111-1111-1111-111111111111"
                s_idx = index or "main"
                await push_to_splunk(json_records, s_url, s_token, s_idx, source=case_name)

            logger.info(f"Pushed: {filename}")

        except Exception as e:
            logger.exception(f"Error processing {filename}: {e}")

    if not saved_files:
        return JSONResponse(status_code=400, content={"error": "No .evtx files found in upload"})

    # Create ZIP: containing the whole folder inside
    zip_name = f"{session_id}.zip"
    zip_path = os.path.join(OUTPUT_DIR, zip_name)

    with ZipFile(zip_path, "w") as zipf:
        for json_file in json_files:
            arcname = f"{session_id}/{os.path.basename(json_file)}"
            zipf.write(json_file, arcname=arcname)

    logger.info(f"ZIP created: {zip_path}")

    # Run Chainsaw analysis
    logger.info("Running Chainsaw analysis...")
    chainsaw_results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: run_chainsaw(UPLOAD_DIR)
    )
    logger.info(f"Chainsaw found {chainsaw_results['summary']['total']} detections")

    # Save Chainsaw JSON output for download
    chainsaw_json_path = os.path.join(session_folder, "chainsaw_results.json")
    async with aiofiles.open(chainsaw_json_path, "w") as cf:
        await cf.write(json.dumps(chainsaw_results, indent=2))

    # Also add Chainsaw JSON to the ZIP
    with ZipFile(zip_path, "a") as zipf:
        zipf.write(chainsaw_json_path, arcname=f"{session_id}/chainsaw_results.json")

    # Push Chainsaw detections to Splunk
    if destination == "splunk" and chainsaw_results.get("detections"):
        s_url = splunk_url or "http://splunk:8088/services/collector/event"
        s_token = splunk_token or "11111111-1111-1111-1111-111111111111"
        s_idx = index or "main"
        await push_chainsaw_to_splunk(chainsaw_results["detections"], s_url, s_token, s_idx, source=case_name)

    # Schedule cleanup
    cleanup_paths = [zip_path, session_folder] + [os.path.join(UPLOAD_DIR, f) for f in saved_files]
    asyncio.create_task(delete_later(cleanup_paths))

    response_data = {
        "session_id": session_id,
        "case_name": case_name,
        "uploaded": saved_files,
        "index": index,
        "destination": destination,
        "zip_url": f"/download/{zip_name}",
        "chainsaw_url": f"/download/{session_id}/chainsaw_results.json",
        "detections": chainsaw_results.get("detections", []),
        "summary": chainsaw_results.get("summary", {})
    }

    # Save full results for the results page API
    results_json_path = os.path.join(session_folder, "results.json")
    async with aiofiles.open(results_json_path, "w") as rf:
        await rf.write(json.dumps(response_data, indent=2))

    return JSONResponse(content=response_data)

def parse_evtx_to_json(path: str) -> list[dict]:
    parser = PyEvtxParser(path)
    records = []
    for record in parser.records_json():
        try:
            records.append(json.loads(record['data']))
        except json.JSONDecodeError:
            pass
    return records

import subprocess

def run_chainsaw(evtx_dir: str) -> dict:
    """Run Chainsaw hunt against EVTX files and return parsed JSON results."""
    try:
        cmd = [
            "chainsaw", "hunt", evtx_dir,
            "-s", "/opt/sigma/rules/",
            "--mapping", "/opt/chainsaw/mappings/sigma-event-logs-all.yml",
            "-r", "/opt/chainsaw/rules/",
            "--json",
            "--skip-errors"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            logger.warning(f"Chainsaw exited with code {result.returncode}: {result.stderr[:500]}")
        
        output = result.stdout.strip()
        if not output:
            return {"detections": [], "summary": {"total": 0}}
        
        detections = json.loads(output)
        
        # Build summary
        severity_counts = {}
        rule_counts = {}
        for det in detections:
            group = det.get("group", "Unknown")
            level = det.get("level", "unknown")
            name = det.get("name", "Unknown Rule")
            severity_counts[level] = severity_counts.get(level, 0) + 1
            rule_counts[name] = rule_counts.get(name, 0) + 1
        
        # Top detections sorted by count
        top_rules = sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        
        return {
            "detections": detections,
            "summary": {
                "total": len(detections),
                "by_severity": severity_counts,
                "top_rules": [{"name": n, "count": c} for n, c in top_rules]
            }
        }
    except subprocess.TimeoutExpired:
        logger.error("Chainsaw timed out")
        return {"detections": [], "summary": {"total": 0, "error": "Chainsaw timed out"}}
    except Exception as e:
        logger.error(f"Chainsaw error: {e}")
        return {"detections": [], "summary": {"total": 0, "error": str(e)}}

async def push_to_elasticsearch(records: list[dict], host: str, port: int, index: str):
    es_url = f"http://{host}:{port}/_bulk"
    bulk_data = ""
    for record in records:
        action = {"index": {"_index": index}}
        bulk_data += json.dumps(action) + "\n"
        bulk_data += json.dumps(record) + "\n"
    
    if bulk_data:
        # Use httpx for asynchronous requests
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    es_url, 
                    content=bulk_data, 
                    headers={"Content-Type": "application/x-ndjson"},
                    timeout=30.0
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Failed to push to Elasticsearch: {e}")

async def push_to_splunk(records: list[dict], url: str, token: str, index: str, source: str = "evtxorcist"):
    # Prepare HEC batch
    batch_data = ""
    for record in records:
        payload = {
            "index": index,
            "sourcetype": "_json",
            "source": source,
            "event": record
        }
        batch_data += json.dumps(payload) + "\n"
    
    if batch_data:
        async with httpx.AsyncClient(verify=False) as client:
            try:
                resp = await client.post(
                    url,
                    content=batch_data,
                    headers={"Authorization": f"Splunk {token}"},
                    timeout=30.0
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Failed to push to Splunk HEC: {e}")

async def push_chainsaw_to_splunk(detections: list[dict], url: str, token: str, index: str, source: str = "evtxorcist"):
    """Push Chainsaw detection results to Splunk with sourcetype 'chainsaw'."""
    batch_data = ""
    for det in detections:
        payload = {
            "index": index,
            "sourcetype": "chainsaw",
            "source": source,
            "event": det
        }
        batch_data += json.dumps(payload) + "\n"
    
    if batch_data:
        async with httpx.AsyncClient(verify=False) as client:
            try:
                resp = await client.post(
                    url,
                    content=batch_data,
                    headers={"Authorization": f"Splunk {token}"},
                    timeout=30.0
                )
                resp.raise_for_status()
                logger.info(f"Pushed {len(detections)} Chainsaw detections to Splunk")
            except Exception as e:
                logger.error(f"Failed to push Chainsaw to Splunk HEC: {e}")

# Delete all files/folders after 1 hour
async def delete_later(paths: list[str], delay_seconds=3600):
    await asyncio.sleep(delay_seconds)
    for path in paths:
        try:
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            logger.info(f"Deleted: {path}")
        except Exception as e:
            logger.warning(f"Failed to delete {path}: {e}")