"""harness.permission — 3 道闸统一入口。"""

import threading

from harness.permission.deny import check_deny_list
from harness.permission.rules import check_rules
from harness.permission.host_policy import MCP_HOST_POLICY

_DENY_ALL = False


def ask_user(tool_name: str, args: dict, reason: str) -> str:
    """交互式审批。非主线程直接拒绝。返回 'allow' / 'deny'。"""
    global _DENY_ALL
    if threading.current_thread() is not threading.main_thread():
        return "deny"
    print(f"\n\033[33m⚠  {reason}\033[0m", flush=True)
    print(f"   {tool_name}({args})", flush=True)
    while True:
        choice = input("   Allow? [y/N/q=deny all] ").strip().lower()
        if choice in ("y", "yes"):
            return "allow"
        if choice in ("q", "quit"):
            _DENY_ALL = True
            return "deny"
        if choice in ("n", "no", ""):
            return "deny"
        print("   Please answer y or n")


def check_permission(tool_name: str, args: dict, workdir, prompt_user: bool = True) -> str | None:
    """3 道闸：deny_list → rules → ask_user。返回拒绝消息或 None（允许）。"""
    global _DENY_ALL
    if _DENY_ALL:
        return "Permission denied by user (deny all)"

    # 第 1 道：硬编码黑名单（仅 bash）
    if tool_name == "bash":
        reason = check_deny_list(args.get("command", ""))
        if reason:
            return f"Permission denied: dangerous command '{reason}'"

    # 第 2 道：规则检查
    from pathlib import Path
    wd = Path(workdir) if not isinstance(workdir, Path) else workdir
    rule_reason = check_rules(tool_name, args, wd)
    if rule_reason:
        if not prompt_user:
            return f"Permission required: {rule_reason}"
        if ask_user(tool_name, args, rule_reason) == "deny":
            return f"Permission denied by user: {rule_reason}"

    # Phase 1: 无 MCP，第 3 道预留
    return None


def reset_deny_all():
    """重置 deny_all 标志（测试用）。"""
    global _DENY_ALL
    _DENY_ALL = False
