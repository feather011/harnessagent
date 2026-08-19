"""harness.tools.pool — 工具 schema 注册 + assemble + execute（双 catch）。"""

from typing import Callable

from harness.tools.base import bash, read_file, write_file, edit_file, run_glob

# ============================================================ 5 个内置工具 schema（OpenAI format）
BUILTIN_TOOLS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "在项目环境的 shell（优先 Git Bash，找不到则回退 cmd）中执行一条命令并返回输出。",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "run_in_background": {"type": "boolean",
                                  "description": "设为 true 则后台执行；Phase 1 暂不支持"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取工作区内的文本文件，返回内容。limit 可选：只返回前 N 行。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "limit": {"type": "integer", "description": "可选，最多返回的行数"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "覆盖写入（或新建）工作区内的文件，自动创建父目录。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "content": {"type": "string", "description": "要写入的完整文件内容"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "在文件中做一次文本替换：把第一处出现的 old_text 替换为 new_text。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "old_text": {"type": "string", "description": "要查找的精确文本"},
            "new_text": {"type": "string", "description": "替换后的新文本"},
        }, "required": ["path", "old_text", "new_text"]},
    }},
    {"type": "function", "function": {
        "name": "glob",
        "description": "用 glob 模式在工作区中列出匹配的文件路径。支持 ** 跨目录匹配。",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "glob 模式，如 '*.py' 或 '**/*.py'"},
        }, "required": ["pattern"]},
    }},
]

# ============================================================ 5 个内置工具 handler（模块级单例）
BUILTIN_HANDLERS: dict = {
    "bash": bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": run_glob,
}


def assemble_tool_pool() -> tuple[list[dict], dict]:
    """返回所有工具：base + MCP 动态（Phase 4）。"""
    tools = list(BUILTIN_TOOLS)
    handlers = dict(BUILTIN_HANDLERS)
    # Phase 4: 合并 MCP 工具
    try:
        from harness.tools.mcp import assemble_tool_pool_v2
        return assemble_tool_pool_v2(tools, handlers)
    except ImportError:
        return tools, handlers


def register_tool(schema: dict, handler: Callable) -> None:
    """追加工具到 BUILTIN_TOOLS / BUILTIN_HANDLERS（import 时调用）。"""
    BUILTIN_TOOLS.append(schema)
    name = schema["function"]["name"]
    BUILTIN_HANDLERS[name] = handler


def register_compact_handler(compact_fn: Callable) -> None:
    """注册 compact 工具（Phase 2 compactor 专用，cli.py 初始化时调用）。"""
    schema = {"type": "function", "function": {
        "name": "compact",
        "description": "Compact conversation context to save tokens. Call when context is getting long.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }}
    register_tool(schema, compact_fn)


def execute_tool(handlers: dict, name: str, args: dict) -> str:
    """执行工具，双 catch：未知工具 → Error；handler 异常 → Error 字符串。"""
    handler = handlers.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"
    try:
        return str(handler(**args))
    except Exception as error:
        return f"Error: {type(error).__name__}: {error}"


# ============================================================ Phase 2 工具 schema（由各模块 import 时注册）
TODO_WRITE_SCHEMA = {"type": "function", "function": {
    "name": "todo_write",
    "description": "Create and manage a task list for your current coding session. Update status as you go: pending → in_progress → completed.",
    "parameters": {"type": "object", "properties": {
        "todos": {"type": "array", "maxItems": 20, "items": {
            "type": "object", "properties": {
                "content": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            }, "required": ["content", "status"],
        }},
    }, "required": ["todos"]}
}}

TASK_SCHEMA = {"type": "function", "function": {
    "name": "task",
    "description": "Spawn a sub-agent to handle a sub-task independently with fresh context.",
    "parameters": {"type": "object", "properties": {
        "prompt": {"type": "string", "description": "The sub-task description"},
    }, "required": ["prompt"]}
}}

LOAD_SKILL_SCHEMA = {"type": "function", "function": {
    "name": "load_skill",
    "description": "Load a skill by name to read its full instructions.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Skill name to load"},
    }, "required": ["name"]}
}}

LOAD_MEMORY_SCHEMA = {"type": "function", "function": {
    "name": "load_memory",
    "description": "Read a specific memory record by name.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Memory name to load"},
    }, "required": ["name"]}
}}

MCP_CONNECT_SCHEMA = {"type": "function", "function": {
    "name": "connect_mcp",
    "description": "连接一个 MCP server 并发现其工具（docs / deploy）。",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "enum": ["docs", "deploy"]},
    }, "required": ["name"]}
}}

WORKFLOW_TOOL_SCHEMA = {"type": "function", "function": {
    "name": "workflow",
    "description": "Run a registered workflow. Returns run_id; final result arrives as <task_notification>.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Workflow name"},
        "args": {"type": "object", "description": "Optional workflow-specific args"},
        "resume_from_run_id": {"type": "string", "description": "Optional: resume existing run"},
    }, "required": ["name"]}
}}
