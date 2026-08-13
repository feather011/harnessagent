#!/usr/bin/env python3
"""s05_todo_write: 任务清单工具 + 3 轮无规划提醒（基于 s04_hooks.py 扩展）。

相对 s04 的改动（其余不动：5 工具、safe_path、UTF-8/stdin、surrogate、permission、5 hook、双层防御）：
- SYSTEM 加 "plan before executing" 引导
- TOOLS / TOOL_HANDLERS 各加 todo_write
- TodoManager（state + validator + renderer）module-level 单例，跨 query 共享
- agent_loop 加 rounds_since_todo 计数，连续 3 轮不调 todo_write 则注入 <reminder>
"""
import ast
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# Windows 终端默认 GBK，强制 UTF-8；stdin 同理由 UTF-8 解码（防中文误解码产生 surrogate）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL"),
)
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

SYSTEM = ("你是一个 coding agent，工作在 Windows 下的 Git Bash 环境。直接干活，不要解释。"
          "面对多步任务，先用 todo_write 工具列出计划并维护任务清单（pending → in_progress → completed）。")

WORKDIR = Path(__file__).resolve().parent

# ============================================================ 权限闸门（从 s03/s04 原样保留）
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda", "> /dev/"]

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
    norm = " ".join(command.split())
    for kw in DENY_LIST:
        if kw in norm.split() or kw in norm:
            return kw
    return None


def check_rules(tool_name: str, args: dict):
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None


def ask_user(tool_name: str, args: dict, reason: str) -> str:
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


# ============================================================ TodoManager
class TodoManager:
    """任务清单：state holder + validator + renderer。module-level 单例，跨 query 共享。"""

    _MARKERS = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}

    def __init__(self):
        self.items: list[dict] = []

    def update(self, todos) -> str:
        """校验并替换任务清单，返回渲染文本。违反约束 raise ValueError。"""
        # 1. 解析（str → list）；JSON 失败用 ast.literal_eval 兜底（绝不 eval）
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                todos = ast.literal_eval(todos)
        # 2. 验证
        if not isinstance(todos, list):
            raise ValueError("todos must be a list")
        if len(todos) > 20:
            raise ValueError("too many todos (max 20)")
        validated = []
        in_progress_count = 0
        for todo in todos:
            if not isinstance(todo, dict):
                raise ValueError("each todo must be an object")
            content = str(todo.get("content", "")).strip()
            if not content:
                raise ValueError("todo content cannot be empty")
            status = str(todo.get("status", "pending")).lower()
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"invalid status: {status}")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content": content, "status": status})
        # 3. 唯一 in_progress 约束
        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")
        # 4. 替换
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = [f"{self._MARKERS[t['status']]} {t['content']}" for t in self.items]
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()


def run_todo_write(todos) -> str:
    """dispatch 入口：TodoManager.update，验证失败转错误字符串（不抛给 loop）。"""
    try:
        return TODO.update(todos)
    except ValueError as e:
        return f"Error: {e}"


# ============================================================ 工具定义
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
    {"type": "function", "function": {
        "name": "todo_write",
        "description": "Create and manage a task list for your current coding session. Update status as you go: pending → in_progress → completed.",
        "parameters": {"type": "object", "properties": {
            "todos": {"type": "array", "maxItems": 20, "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                },
                "required": ["content", "status"],
            }},
        }, "required": ["todos"]},
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
                safe_path(m)
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
    "todo_write": run_todo_write,
}

# ============================================================ Hooks
HOOKS = {}


def register_hook(event: str, callback):
    HOOKS.setdefault(event, []).append(callback)


def trigger_hooks(event: str, *args):
    for callback in HOOKS.get(event, []):
        result = callback(*args)
        if result is not None:
            return result
    return None


# ---- 5 个 hook ----
def context_inject_hook(query: str):
    print(_strip_surrogates(f"\033[90m[HOOK] UserPromptSubmit: query={str(query)[:50]}\033[0m"), flush=True)
    return None


def log_hook(name: str, args: dict):
    args_preview = str(list(args.values())[:2])[:80]
    print(_strip_surrogates(f"\033[90m[HOOK] {name}({args_preview})\033[0m"), flush=True)
    return None


def permission_hook(name: str, args: dict):
    if _DENY_ALL:
        return "Permission denied by user (deny all)"
    if name == "bash":
        reason = check_deny_list(args.get("command", ""))
        if reason:
            return f"Permission denied: dangerous command '{reason}'"
    reason = check_rules(name, args)
    if reason:
        if ask_user(name, args, reason) == "deny":
            return f"Permission denied by user: {reason}"
    return None


def large_output_hook(name: str, args: dict, output):
    n = len(str(output))
    if n > 100000:
        print(f"\033[33m[HOOK] Large output from {name}: {n} chars\033[0m", flush=True)
    return None


def summary_hook(messages: list):
    tool_count = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "tool")
    print(f"\033[90m[HOOK] Stop: {tool_count} tool calls this turn\033[0m", flush=True)
    return None


# ---- 模块加载时注册（不能放 __main__ 里）----
register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", log_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)


def agent_loop(messages):
    rounds_since_todo = 0  # 每轮 agent_loop 独立计数（坑1：不放 module-level，避免跨 query 累计）
    while True:
        resp = client.chat.completions.create(
            model=MODEL, messages=_strip_surrogates(messages), tools=TOOLS, max_tokens=8000,
        )
        msg = resp.choices[0].message
        messages.append(_strip_surrogates(msg.model_dump(exclude_none=True)))
        if not msg.tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": _strip_surrogates(str(force))})
                continue
            if msg.content:
                print(_strip_surrogates(f"\033[32m{msg.content}\033[0m"), flush=True)
            return

        used_todo = False  # 坑2：本轮有任何 todo_write 调用就 reset（不是只看最后一个 tool_call）
        for call in msg.tool_calls:
            name = call.function.name
            if name == "todo_write":
                used_todo = True
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
            trigger_hooks("PostToolUse", name, args, output)
            print(_strip_surrogates(str(output)[:200]), flush=True)
            messages.append({
                "role": "tool", "tool_call_id": call.id, "content": str(output),
            })

        # 连续 3 轮不调 todo_write → 注入 reminder；注入后归零（不连续打扰）
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            messages.append({"role": "user", "content": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo = 0


if __name__ == "__main__":
    history = [{"role": "system", "content": SYSTEM}]
    print(f"\033[36m使用模型 {MODEL}（6 工具含 todo_write + hooks），输入 q / exit / 空行退出\033[0m", flush=True)
    while True:
        try:
            q = input("\033[36ms05 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", q)
        history.append({"role": "user", "content": q})
        agent_loop(history)
        print(flush=True)
