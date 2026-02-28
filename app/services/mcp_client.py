import asyncio
import logging
from mcp import ClientSession
from mcp.client.sse import sse_client
from config import MCP_SSE_URL, MCP_TOKEN

logger = logging.getLogger("evtx_uploader")

# Cached MCP tools
_cached_mcp_tools = None
_cached_mcp_tools_time = 0

def _get_mcp_tools_sync() -> list[dict]:
    """Run MCP tool listing in a fresh event loop (isolated from WebSocket cancel scope)."""
    tools_result = []
    async def _inner():
        nonlocal tools_result
        try:
            async with sse_client(MCP_SSE_URL, headers={"Authorization": f"Bearer {MCP_TOKEN}"}) as (read, write):
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
            async with sse_client(MCP_SSE_URL, headers={"Authorization": f"Bearer {MCP_TOKEN}"}) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=tool_args)
                    result_text = result.content[0].text if result.content else "No results"
        except BaseException as e:
            logger.warning(f"MCP cleanup error (result already captured): {e}")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_inner())
    finally:
        loop.close()
    return result_text

async def get_mcp_tools() -> list[dict]:
    """Run MCP tool listing in a separate thread, with 5-minute cache."""
    global _cached_mcp_tools, _cached_mcp_tools_time
    import time
    if _cached_mcp_tools is not None and (time.time() - _cached_mcp_tools_time) < 300:
        return _cached_mcp_tools
    tools = await asyncio.to_thread(_get_mcp_tools_sync)
    _cached_mcp_tools = tools
    _cached_mcp_tools_time = time.time()
    return tools

async def call_mcp_tool(tool_name: str, tool_args: dict) -> str:
    """Run MCP tool execution in a separate thread to isolate from WebSocket cancel scope."""
    return await asyncio.to_thread(_call_mcp_tool_sync, tool_name, tool_args)

def format_tools_for_ollama(mcp_tools: list[dict]) -> list:
    """Convert MCP tool dicts to ollama Tool format."""
    ollama_tools = []
    for t in mcp_tools:
        fn = t.get("function", {})
        props = fn.get("parameters", {}).get("properties", {})
        required = fn.get("parameters", {}).get("required", [])
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
