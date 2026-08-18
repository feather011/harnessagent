"""harness.permission.host_policy — MCP 工具 host 级审批策略。Phase 1 为空。"""

# Phase 1: 无 MCP，结构预留。
# 格式: {(server_name, tool_name): "allow" | "confirm"}
MCP_HOST_POLICY: dict[tuple[str, str], str] = {}
