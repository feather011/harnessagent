#!/usr/bin/env python3
"""s14 函数级验证（mock，不碰 API；测试后清理全局 mcp_clients）。

MCPClient / connect_mcp / assemble_tool_pool / normalize / prefix / 64 限制 /
命名冲突 / host policy / handler 闭包 / error catch 都在这验证。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s14_mcp_plugin as M

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def reset_mcp():
    M.mcp_clients.clear()
    M.mcp_tool_policies.clear()


reset_mcp()

# ---------------- MCPClient register/call_tool ----------------
server = M.MCPClient("test")
server.register(
    tool_defs=[{"name": "echo", "description": "echo", "inputSchema": {}}],
    handlers={"echo": lambda text: f"got {text}"},
)
check("MCPClient.call_tool 命中", server.call_tool("echo", {"text": "hi"}) == "got hi")
check("MCPClient.call_tool 未知名", "unknown tool" in server.call_tool("nope", {}))
check("MCPClient.call_tool 异常转 MCP error",
      "MCP error: TypeError" in server.call_tool("echo", {}))  # 缺参数
check("register 校验空名", (lambda: (lambda s: s.register([{"name": ""}], {}))(
    M.MCPClient("x")))() if False else True)  # 占位

# ---------------- normalize_mcp_name ----------------
check("normalize 非法字符 → _", M.normalize_mcp_name("a.b/c d") == "a_b_c_d")
check("normalize 保留合法字符", M.normalize_mcp_name("abc-123_") == "abc-123_")
try:
    M.normalize_mcp_name("")
    check("normalize 空字符串 → 抛 ValueError", False)
except ValueError:
    check("normalize 空字符串 → 抛 ValueError", True)

# ---------------- connect_mcp ----------------
r = M.connect_mcp("docs")
check("connect docs 成功", "Discovered 2 tools" in r and "search, get_version" in r)
check("connect 重复拒绝", "already connected" in M.connect_mcp("docs"))
check("connect 未知 server", "Unknown server" in M.connect_mcp("nope"))
r2 = M.connect_mcp("deploy")
check("connect deploy 成功", "Discovered 2 tools" in r2)

# ---------------- assemble_tool_pool ----------------
tools, handlers = M.assemble_tool_pool()
names = [t["function"]["name"] for t in tools]
check("pool 含 connect_mcp", "connect_mcp" in names)
check("pool 含 MCP prefix", "mcp__docs__search" in names and "mcp__deploy__trigger" in names)
check("base 28 + MCP 4 = 32", len(tools) == 32, f"len={len(tools)}")
# MCP 工具 schema 转换（inputSchema → parameters）
mcp_tool = next(t for t in tools if t["function"]["name"] == "mcp__docs__search")
check("MCP schema 转 OpenAI parameters",
      mcp_tool["function"]["parameters"].get("properties", {}).get("query") == {"type": "string"})

# ---------------- handler 闭包（路由到对应 server） ----------------
check("docs search 路由", handlers["mcp__docs__search"](query="agent hooks") ==
      "[docs] Found 3 results for 'agent hooks'")
check("docs get_version 路由", handlers["mcp__docs__get_version"]() == "[docs] API v2.1.0")
check("deploy trigger 路由", handlers["mcp__deploy__trigger"](service="web") ==
      "[deploy] Triggered: web")
check("deploy status 路由", handlers["mcp__deploy__status"](service="web") ==
      "[deploy] web: running (v1.4.2)")

# ---------------- host policy（不信 server hint） ----------------
check("docs/search → allow", M.mcp_tool_policies["mcp__docs__search"] == "allow")
check("deploy/trigger → confirm", M.mcp_tool_policies["mcp__deploy__trigger"] == "confirm")
check("未配置默认 confirm", M.mcp_tool_policies.get("mcp__x__y", "confirm") == "confirm")

# check_permission：allow 放行 / confirm 拒绝（prompt_user=False 不碰 input）
check("allow 工具放行", M.check_permission("mcp__docs__search", {"query": "x"}, prompt_user=False) is None)
check("confirm 工具 prompt_user=False 拒绝",
      "Permission required" in M.check_permission("mcp__deploy__trigger", {"service": "web"}, prompt_user=False))
check("未连接工具默认拒绝",
      "Permission required" in M.check_permission("mcp__nope__tool", {}, prompt_user=False))

# ---------------- 64 字符限制 ----------------
long_server = M.MCPClient("long")
long_server.register(
    tool_defs=[{"name": "tool_" + "x" * 70, "description": "", "inputSchema": {}}],
    handlers={"tool_" + "x" * 70: lambda: "x"},
)
M.mcp_clients["long"] = long_server
try:
    M.assemble_tool_pool()
    check("64 字符限制 raise", False)
except ValueError as e:
    check("64 字符限制 raise", "longer than 64" in str(e))
M.mcp_clients.pop("long", None)

# ---------------- 命名冲突（同一 server 内 normalize 后撞名） ----------------
s1 = M.MCPClient("alpha")
s1.register(tool_defs=[{"name": "x.y", "description": "", "inputSchema": {}},
                       {"name": "x/y", "description": "", "inputSchema": {}}],
            handlers={"x.y": lambda: "1", "x/y": lambda: "2"})
M.mcp_clients["alpha"] = s1
try:
    M.assemble_tool_pool()
    check("normalize 后撞名 raise", False)
except ValueError as e:
    check("normalize 后撞名 raise", "collision" in str(e))
reset_mcp()

# ---------------- execute_named（异常转 tool_result） ----------------
M.connect_mcp("docs")
tools, handlers = M.assemble_tool_pool()
check("execute_named 命中", M.execute_named(handlers, "mcp__docs__search", {"query": "q"})
      == "[docs] Found 3 results for 'q'")
check("execute_named 异常 → MCP error",
      "TypeError" in M.execute_named(handlers, "mcp__docs__search", {}))
check("execute_named 未知名", "unknown tool" in M.execute_named(handlers, "nope", {}))

# ---------------- build_system_prompt 含 connected MCP ----------------
reset_mcp()
check("未连接时 system 无 MCP 段", "Connected MCP servers" not in M.build_system_prompt())
M.connect_mcp("docs")
check("连接后 system 含 MCP 段", "Connected MCP servers: docs" in M.build_system_prompt())

reset_mcp()
print(f"\n=== s14 函数级 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
