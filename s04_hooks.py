#!/usr/bin/env python3
"""s04_hooks: 5 个 hook + 3 处 trigger（基于 s03_permission.py 扩展）。

相对 s03 的改动（其余不动：5 个 tool handler、safe_path、UTF-8、surrogate、DENY_LIST、
PERMISSION_RULES、check_deny_list、check_rules、ask_user）：
- s03 的 check_permission 逻辑搬进 permission_hook（PreToolUse）
- 新增 HOOKS / register_hook / trigger_hooks，5 个 hook 在模块底部一次性注册
- 4 处 trigger：UserPromptSubmit（REPL 入口）/ PreToolUse（handler 前）/
  PostToolUse（handler 后）/ Stop（loop 退出前，返回非 None 强制继续）
"""
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# Windows 终端默认 GBK，强制 UTF-8 保证中文输出正常；stdin 同理由 UTF-8 解码
# （否则测试/管道写入的中文会被 GBK 误解码，偶发产生 surrogate 导致打印崩溃）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
client = OpenAI(
    api_key=os.getenv("MIMO_API_KEY"),
    base_url=os.getenv("MIMO_BASE_URL"),
)
MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5")

SYSTEM = "你是一个 coding agent，工作在 Windows 下的 Git Bash 环境。直接干活，不要解释。"

WORKDIR = Path(__file__).resolve().parent

# ============================================================ 权限闸门（从 s03 搬来）
# Gate 1：危险命令硬拒列表。教程 7 个 + `> /dev/` 兜底。
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda", "> /dev/"]

# Gate 2：规则命中后问用户。教程基线 2 条 + 项目扩展 2 条。
PERMISSION_RULES = [
    {
        "tools": ["read_file", "write_file", "edit_file"],
        "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
        "message": "Access outside workspace",
    },
    {
        "tools": ["bash"],
        "check": lambda args: any(kw in args.get("command", "") for kw in
                                  ["rm ", "unlink ", "del ", "rmdir ", "> /etc/", "chmod 777"]),
        "message": "Potentially destructive command",
    },
    {
        "tools": ["write_file", "edit_file"],
        "check": lambda args: Path(args.get("path", "")).name in [".env", ".env.example", ".gitignore"] or args.get("path", "").endswith((".pem", ".key")),
        "message": "Writing sensitive file",
    },
    {
        "tools": ["bash"],
        "check": lambda args: "pip install" in args.get("command", "") or "npm install" in args.get("command", ""),
        "message": "Installing new package",
    },
]

_DENY_ALL = False


def check_deny_list(command: str):
    """Gate 1：命中 DENY_LIST 返回关键词，否则 None。折叠空白防双空格绕过。"""
    norm = " ".join(command.split())
    for kw in DENY_LIST:
        if kw in norm.split() or kw in norm:
            return kw
    return None


def check_rules(tool_name: str, args: dict):
    """Gate 2：命中 PERMISSION_RULES 返回 message，否则 None。"""
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None


def ask_user(tool_name: str, args: dict, reason: str) -> str:
    """阻塞等用户 y/n。改进版：回车=拒绝，q=本会话全拒，输入错误循环。"""
    global _DENY_ALL
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


# ============================================================ 工具定义（从 s02/s03 原样保留）
TOOLS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "在项目环境的 shell（优先 Git Bash，找不到则回退 cmd）中执行一条命令并返回输出。用于装依赖、跑脚本、git 操作、查看进程等。",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取工作区内的文本文件，返回内容。limit 可选：只返回前 N 行。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "limit": {"type": "integer", "description": "可选，最多返回的行数；不传则返回全部"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "覆盖写入（或新建）工作区内的文件，自动创建父目录。注意：会完全覆盖已有文件的全部内容。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "content": {"type": "string", "description": "要写入的完整文件内容"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "在文件中做一次文本替换：把第一处出现的 old_text 替换为 new_text。old_text 必须精确匹配，找不到会返回错误。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的文件路径"},
            "old_text": {"type": "string", "description": "要查找的精确文本（只替换第一处）"},
            "new_text": {"type": "string", "description": "替换后的新文本"},
        }, "required": ["path", "old_text", "new_text"]},
    }},
    {"type": "function", "function": {
        "name": "glob",
        "description": "用 glob 模式在工作区中列出匹配的文件路径（相对工作区返回）。支持 ** 跨目录匹配，如 '**/*.py'。",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "glob 模式，如 '*.py' 或 '**/*.py'"},
        }, "required": ["pattern"]},
    }},
]

# run_bash 内置 DENY（s02 保留，作为权限闸门之后的兜底）
DENY = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]

_BASH = shutil.which("bash")


def run_bash(command: str) -> str:
    if any(d in command for d in DENY):
        return "Error: Dangerous command blocked"
    try:
        if _BASH:
            r = subprocess.run(
                [_BASH, "-c", command], capture_output=True,
                text=True, errors="replace", timeout=30,
            )
        else:
            r = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, errors="replace", timeout=30,
            )
    except subprocess.TimeoutExpired:
        return "Error: command timed out (30s)"
    out = (r.stdout + r.stderr).strip()
    if not out:
        out = "(no output)"
    if len(out) > 50000:
        out = out[:50000] + "\n... (output truncated)"
    return out


def safe_path(p: str) -> Path:
    """把相对工作区的路径解析为绝对路径；逃逸出工作区的路径抛 ValueError。"""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int | None = None) -> str:
    try:
        text = safe_path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    if limit is not None:
        lines = text.splitlines()
        if len(lines) > limit:
            body = "\n".join(lines[:limit])
            return body + f"\n... ({len(lines) - limit} more lines)"
    if len(text) > 50000:
        text = text[:50000] + "\n... (output truncated)"
    return text


def run_write(path: str, content: str) -> str:
    try:
        p = safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    return f"Wrote {len(content.encode('utf-8'))} bytes to {path}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        p = safe_path(path)
        text = p.read_text(encoding="utf-8")
        if old_text not in text:
            return f"Error: old_text not found in {path}"
        p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    return f"Edited {path}: replaced first occurrence"


def run_glob(pattern: str) -> str:
    matches = []
    try:
        for m in glob.glob(pattern, root_dir=WORKDIR, recursive=True):
            try:
                safe_path(m)  # 逃逸工作区的匹配会被 safe_path 拒绝
                matches.append(m)
            except ValueError:
                pass
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    matches.sort()
    return "\n".join(matches) if matches else "(no matches)"


def _strip_surrogates(value):
    """递归清洗 str 中的孤立 surrogate 字符（openai SDK 3.0.0 序列化会崩）。"""
    if isinstance(value, str):
        return "".join(c for c in value if not 0xD800 <= ord(c) <= 0xDFFF)
    if isinstance(value, dict):
        return {k: _strip_surrogates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_surrogates(v) for v in value]
    return value


TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
}

# ============================================================ Hooks
HOOKS = {}


def register_hook(event: str, callback):
    """注册一个 hook 到事件。同事件按注册顺序依次触发。"""
    HOOKS.setdefault(event, []).append(callback)


def trigger_hooks(event: str, *args):
    """触发一个事件的所有 hook。*args 全量解包传给 callback；
    第一个返回非 None 的 callback 结果即为事件结果（用于拦截/强制继续）。"""
    for callback in HOOKS.get(event, []):
        result = callback(*args)
        if result is not None:  # 注意：用 is not None，空串/0/False 不算拦截
            return result
    return None


# ---- 5 个 hook ----
def context_inject_hook(query: str):
    """UserPromptSubmit：记录用户输入。截断 50 字符防刷屏，不影响控制流。"""
    print(_strip_surrogates(f"\033[90m[HOOK] UserPromptSubmit: query={str(query)[:50]}\033[0m"), flush=True)
    return None


def log_hook(name: str, args: dict):
    """PreToolUse：记录工具调用。灰色 ANSI 不抢戏，返回 None。"""
    args_preview = str(list(args.values())[:2])[:80]
    print(_strip_surrogates(f"\033[90m[HOOK] {name}({args_preview})\033[0m"), flush=True)
    return None


def permission_hook(name: str, args: dict):
    """PreToolUse：s03 的 check_permission 逻辑搬到这里。
    返回非 None 字符串 = 拦截 + 给模型看的拒绝消息。"""
    if _DENY_ALL:
        return "Permission denied by user (deny all)"
    if name == "bash":
        reason = check_deny_list(args.get("command", ""))
        if reason:
            return f"Permission denied: dangerous command '{reason}'"  # Gate 1 硬拒，不问
    reason = check_rules(name, args)
    if reason:
        if ask_user(name, args, reason) == "deny":
            return f"Permission denied by user: {reason}"
    return None


def large_output_hook(name: str, args: dict, output):
    """PostToolUse：大输出警告。阈值 100000（与 run_bash 的 50000 截断两层独立）。"""
    n = len(str(output))
    if n > 100000:
        print(f"\033[33m[HOOK] Large output from {name}: {n} chars\033[0m", flush=True)
    return None


def summary_hook(messages: list):
    """Stop：退出前打印本轮工具调用统计。返回 None = 放行退出。"""
    tool_count = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "tool")
    print(f"\033[90m[HOOK] Stop: {tool_count} tool calls this turn\033[0m", flush=True)
    return None


# ---- 模块加载时注册（不能放 __main__ 里，否则 import 时不注册、hook 不工作）----
register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", log_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)


def agent_loop(messages):
    while True:
        resp = client.chat.completions.create(
            model=MODEL, messages=_strip_surrogates(messages), tools=TOOLS, max_tokens=8000,
        )
        msg = resp.choices[0].message
        messages.append(_strip_surrogates(msg.model_dump(exclude_none=True)))
        if not msg.tool_calls:
            # Stop hook：返回非 None 则强制继续（当前只 log，放行退出）
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": _strip_surrogates(str(force))})
                continue
            if msg.content:
                print(_strip_surrogates(f"\033[32m{msg.content}\033[0m"), flush=True)
            return
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                output = f"Error: invalid arguments JSON: {e}"
                print(_strip_surrogates(f"\033[33m> {name}(BAD JSON)\033[0m"), flush=True)
                print(_strip_surrogates(f"\033[31m{output}\033[0m"), flush=True)
                messages.append({
                    "role": "tool", "tool_call_id": call.id, "content": output,
                })
                continue

            # ---- PreToolUse：hook 链（log_hook 先跑，permission_hook 决定拦截）----
            blocked = trigger_hooks("PreToolUse", name, args)
            if blocked:
                print(_strip_surrogates(f"\033[31m> {name}(DENIED)\033[0m"), flush=True)
                print(_strip_surrogates(str(blocked)), flush=True)
                messages.append({
                    "role": "tool", "tool_call_id": call.id, "content": str(blocked),
                })
                continue

            print(_strip_surrogates(f"\033[33m> {name}({args})\033[0m"), flush=True)
            handler = TOOL_HANDLERS.get(name)
            if handler is None:
                output = f"Error: unknown tool '{name}'"
            else:
                try:
                    output = handler(**args)
                except Exception as e:
                    output = f"Error: {type(e).__name__}: {e}"
            # ---- PostToolUse：handler 真正执行后才触发（被 PreToolUse 拦的不触发）----
            trigger_hooks("PostToolUse", name, args, output)
            print(_strip_surrogates(str(output)[:200]), flush=True)
            messages.append({
                "role": "tool", "tool_call_id": call.id, "content": str(output),
            })


if __name__ == "__main__":
    history = [{"role": "system", "content": SYSTEM}]
    print(f"\033[36m使用模型 {MODEL}（5 工具 + hooks），输入 q / exit / 空行退出\033[0m", flush=True)
    while True:
        try:
            q = input("\033[36ms04 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", q)  # UserPromptSubmit：用户输入后
        history.append({"role": "user", "content": q})
        agent_loop(history)
        print(flush=True)
