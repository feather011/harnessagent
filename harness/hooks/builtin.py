"""harness.hooks.builtin — 5 个内置 hook（import 即注册）。"""

from harness.hooks.registry import register_hook
from harness.llm import LLMClient


def _context_inject_hook(query: str) -> None:
    """钩子 1：UserPromptSubmit — 打印查询摘要到 stderr。"""
    preview = str(query)[:50]
    print(LLMClient.strip_surrogates(f"\033[90m[HOOK] UserPromptSubmit: query={preview}\033[0m"), flush=True)
    return None


def _log_hook(name: str, args: dict) -> None:
    """钩子 2：PreToolUse — 打印工具调用日志。"""
    args_preview = str(list(args.values())[:2])[:80]
    print(LLMClient.strip_surrogates(f"\033[90m[HOOK] {name}({args_preview})\033[0m"), flush=True)
    return None


def _permission_hook(name: str, args: dict):
    """钩子 3：PreToolUse — 3 道闸权限检查。"""
    from harness.permission import check_permission
    from harness.tools.base import get_workdir
    return check_permission(name, args, get_workdir(), prompt_user=True)


def _large_output_hook(name: str, args: dict, output) -> None:
    """钩子 4：PostToolUse — 大输出警告。"""
    n = len(str(output))
    if n > 100000:
        print(f"\033[33m[HOOK] Large output from {name}: {n} chars\033[0m", flush=True)
    return None


def _summary_hook(messages: list) -> None:
    """钩子 5：Stop — 统计本轮工具调用次数。"""
    tool_count = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "tool")
    print(f"\033[90m[HOOK] Stop: {tool_count} tool calls this turn\033[0m", flush=True)
    return None


# ============================================================ 注册（import 即生效）
register_hook("UserPromptSubmit", _context_inject_hook)
register_hook("PreToolUse", _log_hook)
register_hook("PreToolUse", _permission_hook)
register_hook("PostToolUse", _large_output_hook)
register_hook("Stop", _summary_hook)
