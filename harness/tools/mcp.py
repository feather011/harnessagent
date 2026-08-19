"""harness.tools.mcp — MCPClient + connect_mcp + 动态工具池。"""

import re
from typing import Callable

from harness.tools.pool import register_tool, MCP_CONNECT_SCHEMA


class MCPClient:
    """in-process MCP 客户端：register 存工具，call_tool 是调用边界。"""

    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []
        self._handlers: dict[str, Callable] = {}

    def register(self, tool_defs: list[dict], handlers: dict[str, Callable]):
        names = [t.get("name") for t in tool_defs]
        if any(not isinstance(n, str) or not n for n in names):
            raise ValueError("Every MCP tool needs a non-empty name")
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate tool names on server {self.name!r}")
        missing = [n for n in names if n not in handlers]
        if missing:
            raise ValueError(f"Missing handlers: {', '.join(missing)}")
        self.tools = list(tool_defs)
        self._handlers = dict(handlers)

    def call_tool(self, tool_name: str, args: dict) -> str:
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"MCP error: unknown tool '{tool_name}'"
        try:
            return str(handler(**args))
        except Exception as exc:
            return f"MCP error: {type(exc).__name__}: {exc}"


# Module-level state
mcp_clients: dict[str, MCPClient] = {}
mcp_tool_policies: dict[str, str] = {}

_DISALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_mcp_name(name: str) -> str:
    normalized = _DISALLOWED_CHARS.sub("_", name)
    if not normalized:
        raise ValueError("MCP name cannot normalize to empty string")
    return normalized


# ============================================================ Mock servers
def _mock_server_docs() -> MCPClient:
    server = MCPClient("docs")
    server.register(
        tool_defs=[
            {"name": "search", "description": "Search the documentation.",
             "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
             "annotations": {"readOnlyHint": True}},
            {"name": "get_version", "description": "Get the documentation API version.",
             "inputSchema": {"type": "object", "properties": {}},
             "annotations": {"readOnlyHint": True}},
        ],
        handlers={
            "search": lambda query: f"[docs] Found 3 results for '{query}'",
            "get_version": lambda: "[docs] API v2.1.0",
        },
    )
    return server


def _mock_server_deploy() -> MCPClient:
    server = MCPClient("deploy")
    server.register(
        tool_defs=[
            {"name": "trigger", "description": "Trigger a deployment.",
             "inputSchema": {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
             "annotations": {"destructiveHint": True}},
            {"name": "status", "description": "Check deployment status.",
             "inputSchema": {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
             "annotations": {"readOnlyHint": True}},
        ],
        handlers={
            "trigger": lambda service: f"[deploy] Triggered: {service}",
            "status": lambda service: f"[deploy] {service}: running (v1.4.2)",
        },
    )
    return server


MOCK_SERVERS = {"docs": _mock_server_docs, "deploy": _mock_server_deploy}


def connect_mcp(name: str) -> str:
    """连接 MCP server 并发现工具。"""
    if name in mcp_clients:
        return f"MCP server '{name}' already connected"
    factory = MOCK_SERVERS.get(name)
    if not factory:
        return f"Unknown server '{name}'. Available: {', '.join(MOCK_SERVERS)}"
    server = factory()
    mcp_clients[name] = server
    names = ", ".join(t["name"] for t in server.tools)
    print(f"  \033[90m[mcp] connected: {name} -> {names}\033[0m", flush=True)
    return f"Connected to MCP server '{name}'. Discovered {len(server.tools)} tools: {names}"


# ============================================================ Dynamic tool pool
def assemble_tool_pool_v2(base_tools: list[dict], base_handlers: dict) -> tuple[list[dict], dict]:
    """base + 所有已连接 MCP server 的工具。"""
    global mcp_tool_policies
    tools = list(base_tools)
    handlers = dict(base_handlers)
    policies: dict[str, str] = {}
    origins = {t["function"]["name"]: f"base {t['function']['name']!r}" for t in tools}

    for server_name, server in mcp_clients.items():
        safe_server = normalize_mcp_name(server_name)
        for tool_def in server.tools:
            raw_name = tool_def["name"]
            safe_tool = normalize_mcp_name(raw_name)
            prefixed = f"mcp__{safe_server}__{safe_tool}"
            if len(prefixed) > 64:
                raise ValueError(f"MCP tool name too long: {prefixed}")
            origin = f"MCP {server_name!r}/{raw_name!r}"
            if prefixed in origins:
                raise ValueError(f"MCP tool name collision: {prefixed} maps to both {origins[prefixed]} and {origin}")
            schema = tool_def.get("inputSchema", {})
            origins[prefixed] = origin
            tools.append({"type": "function", "function": {
                "name": prefixed, "description": tool_def.get("description", ""), "parameters": schema,
            }})
            handlers[prefixed] = (lambda *, c=server, t=raw_name, **kw: c.call_tool(t, kw))
            policies[prefixed] = MCP_HOST_POLICY.get((server_name, raw_name), "confirm")

    mcp_tool_policies = policies
    return tools, handlers


# ============================================================ Host policy
MCP_HOST_POLICY = {
    ("docs", "search"): "allow",
    ("docs", "get_version"): "allow",
    ("deploy", "status"): "allow",
    ("deploy", "trigger"): "confirm",
}


def init_mcp_tools():
    """由 cli.py 调用，注册 connect_mcp 工具。"""
    def run_connect_mcp(name: str) -> str:
        return connect_mcp(name)
    register_tool(MCP_CONNECT_SCHEMA, run_connect_mcp)
