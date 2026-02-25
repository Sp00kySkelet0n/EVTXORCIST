import json
import httpx
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from config import OLLAMA_HOST, OLLAMA_BASE
from services.mcp_client import get_mcp_tools, call_mcp_tool, format_tools_for_ollama

logger = logging.getLogger("evtx_uploader")

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]

class PreloadRequest(BaseModel):
    model: str

@router.get("/api/models")
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

@router.post("/api/preload")
async def preload_model(req: PreloadRequest):
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": req.model, "stream": False, "keep_alive": "5m"}
            )
            resp.raise_for_status()
            return JSONResponse(content={"status": "loaded"})
    except Exception as e:
        logger.error(f"Error preloading model {req.model}: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/api/context")
async def get_chat_context():
    """Fetches a summary of Chainsaw detections from Splunk to seed the AI's context."""
    query = 'search index=main sourcetype=chainsaw | stats count by level, name | sort - count | head 20'
    try:
        result = await call_mcp_tool("search_splunk", {"search_query": query})
        
        if not result or result == "No results" or "count" not in result:
            return JSONResponse(content={"context": ""})
            
        context_prompt = (
            "Here is the summary of high-value Chainsaw detections found in the current EVTX upload:\n"
            f"{result}\n\n"
            "Use this context to guide the user's investigation."
        )
        return JSONResponse(content={"context": context_prompt})
    except Exception as e:
        logger.error(f"Error fetching chat context: {e}")
        return JSONResponse(content={"context": ""})

@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            messages = data.get("messages", [])
            model = data.get("model", "")
            if not model:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as c:
                        r = await c.get(f"{OLLAMA_BASE}/api/tags")
                        model = r.json().get("models", [{}])[0].get("name", "")
                except Exception:
                    pass
                if not model:
                    await websocket.send_json({"error": "No model selected and none available", "done": True})
                    continue
            
            if not any(m.get("role") == "system" for m in messages):
                messages.insert(0, {
                    "role": "system",
                    "content": (
                        "Respond ONLY in English. Never use any other language.\n"
                        "You are EVTXorcist's Splunk analyst. You MUST search the data using tools before answering. NEVER guess or hallucinate answers based on CTF knowledge.\n"
                        "CRITICAL INSTRUCTION: You DO NOT know the answer to the user's question until you run a search. ALWAYS start your response with a tool call.\n"
                        "TO SEARCH, YOU MUST OUTPUT EXACTLY THIS SYNTAX AND NOTHING ELSE BEFORE IT:\n"
                        "search_splunk(search_query=\"your splunk query here\")\n"
                        "Do not describe the query, just output the tool call.\n\n"
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
                        "For endpoint hostnames (like 'Client02'), use the 'Computer' or 'Event.System.Computer' field AND ALWAYS wrap the hostname in wildcards (e.g., Computer=\"*Client02*\") to catch full domains like Client02.Main.local.\n\n"
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

            system_msgs = [m for m in messages if m.get("role") == "system"]
            other_msgs = [m for m in messages if m.get("role") != "system"]
            messages = system_msgs + other_msgs[-10:]

            try:
                import re
                from ollama import AsyncClient, ResponseError

                mcp_tools = await get_mcp_tools()
                ollama_tools = format_tools_for_ollama(mcp_tools)
                for t in mcp_tools:
                    logger.debug(f"Loaded tool: {t['function']['name']}")

                ollama_client = AsyncClient(host=OLLAMA_HOST)

                MAX_ROUNDS = 5
                supports_tools = True

                for round_num in range(MAX_ROUNDS):
                    tool_calls = []
                    collected_content = ""

                    # For the initial round, strongly remind smaller models at the very end of context
                    if round_num == 0 and messages and messages[-1]["role"] == "user":
                        messages[-1]["content"] += "\n\n[SYSTEM DIRECTIVE: You do not know the answer. You MUST begin your response by outputting `search_splunk(search_query=\"...\")` to query the Splunk database.]"

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

                    # Fallbacks
                    if not tool_calls and collected_content:
                        detected = False

                        # Pattern 1: Any valid JSON block
                        blocks = re.findall(r'```(?:json)?\n(.*?)\n```', collected_content, re.DOTALL)
                        if not blocks:
                            start = collected_content.find('{')
                            end = collected_content.rfind('}')
                            if start != -1 and end != -1 and end > start:
                                blocks = [collected_content[start:end+1]]
                        
                        for block in blocks:
                            try:
                                data = json.loads(block)
                                if isinstance(data, dict):
                                    t_name = data.get("name") or data.get("tool")
                                    t_args = data.get("arguments") or data.get("command")
                                    if t_name and t_args:
                                        if isinstance(t_args, str):
                                            if "=" in t_args and "search_query=" in t_args:
                                                match = re.search(r'search_query\s*=\s*(?:"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\')', t_args)
                                                if match:
                                                    val = match.group(1) if match.group(1) else match.group(2)
                                                    t_args = {"search_query": val.replace('\\"', '"').replace("\\'", "'")}
                                                else:
                                                    t_args = {"search_query": t_args.replace('search_query=', '').strip('"\' ')}
                                            else:
                                                t_args = {"search_query": t_args}
                                        if "query" in t_args and "search_query" not in t_args:
                                            t_args["search_query"] = t_args.pop("query")
                                        
                                        # Only add if we haven't already parsed this exact block
                                        tool_calls.append({"function": {"name": t_name, "arguments": t_args}})
                                        logger.info(f"Detected JSON block tool call: {t_name}({t_args})")
                                        detected = True
                            except json.JSONDecodeError:
                                pass

                        # Pattern 2
                        if not detected:
                            func_pattern = re.findall(
                                r'(\w+)\(\s*((?:\w+\s*=\s*(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')(?:\s*,\s*)?)+)\s*\)',
                                collected_content
                            )
                            for func_name, args_str in func_pattern:
                                kv_pairs = re.findall(r'(\w+)\s*=\s*(?:"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\')', args_str)
                                if kv_pairs:
                                    parsed_args = {}
                                    for k, v1, v2 in kv_pairs:
                                        val = v1 if v1 else v2
                                        val = val.replace('\\"', '"').replace("\\'", "'")
                                        parsed_args[k] = val
                                    if "query" in parsed_args and "search_query" not in parsed_args:
                                        parsed_args["search_query"] = parsed_args.pop("query")
                                    tool_calls.append({"function": {"name": func_name, "arguments": parsed_args}})
                                    logger.info(f"Detected function-call tool: {func_name}({parsed_args})")
                                    detected = True

                        # Pattern 3
                        if not detected:
                            clean_content = re.sub(r'```\w*\n?', '', collected_content)
                            spl_matches = re.findall(
                                r'(?:^|\n)\s*((?:search\s+)?index=\S+[^\n]*)',
                                clean_content
                            )
                            if spl_matches:
                                for spl_query in spl_matches:
                                    spl_query = spl_query.strip()
                                    if not spl_query.startswith('search '):
                                        spl_query = 'search ' + spl_query
                                    tool_calls.append({"function": {"name": "search_splunk", "arguments": {"search_query": spl_query}}})
                                    logger.info(f"Detected bare SPL query: {spl_query}")

                    if not tool_calls:
                        break

                    all_results = []
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        tool_name = fn.get("name", "")
                        tool_args = fn.get("arguments", {})
                        logger.info(f"[Round {round_num+1}] Executing tool `{tool_name}` with args {tool_args}")
                        args_display = tool_args.get("search_query", json.dumps(tool_args, default=str))
                        await websocket.send_text(f"\n\n_`{tool_name}` → `{args_display}`_\n\n")

                        try:
                            result_text = await call_mcp_tool(tool_name, tool_args)
                            logger.info(f"Tool `{tool_name}` returned {len(result_text)} chars")
                        except Exception as e:
                            logger.error(f"Tool execution failed: {e}")
                            result_text = f"Error executing tool: {e}"
                        all_results.append(f"[{tool_name}({tool_args})]\n{result_text}")

                    combined = "\n\n---\n\n".join(all_results)
                    messages.append({"role": "assistant", "content": collected_content})
                    messages.append({
                        "role": "user",
                        "content": f"[TOOL RESULTS — {len(all_results)} queries executed]\n{combined}\n\n"
                                   f"[Analyze ALL results above. If data answers the user's question, present it clearly. "
                                   f"If not, try different search approaches. Round {round_num+1} of {MAX_ROUNDS}.]"
                    })
                    collected_content = ""

                await websocket.send_json({"done": True})

            except Exception as e:
                logger.error(f"Chat error: {e}", exc_info=True)
                await websocket.send_json({"error": str(e), "done": True})

    except WebSocketDisconnect:
        logger.info("Chat WebSocket disconnected")
