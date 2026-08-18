#!/usr/bin/env python3
"""s03_permission: 在工具执行前加 Permission 闸门（基于 s02_tool_use.py 扩展）。

相对 s02 的改动（其余不动：loop 主体、5 个 tool handler、safe_path、UTF-8、surrogate 清洗）：
- 三道闸门：Gate1 DENY_LIST 硬拒（不问）→ Gate2 PERMISSION_RULES 问用户 → Gate3 拒绝时回传
- 新增 DENY_LIST / PERMISSION_RULES / check_deny_list / check_rules / ask_user / check_permission
- ask_user 支持 q = deny all（本会话后续全拒）
- 可观测：被拒时打印 "> <工具名>(DENIED)"
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

# Windows 终端默认 GBK，强制 UTF-8 保证中文输出正常
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()
client = OpenAI(
    api_key=os.getenv("MIMO_API_KEY"),
    base_url=os.getenv("MIMO_BASE_URL"),
)
MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5")

SYSTEM = "你是一个 coding agent，工作在 Windows 下的 Git Bash 环境。直接干活，不要解释。"

# 工作区根：脚本在哪儿，工作区就在哪儿
WORKDIR = Path(__file__).resolve().parent

# ============================================================ 权限闸门
# Gate 1：危险命令硬拒列表。教程 7 个 + `> /dev/` 兜底（拦任意设备重定向）。
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda", "> /dev/"]

# Gate 2：规则命中后问用户。教程基线 2 条 + 项目扩展 2 条（敏感文件 / 装包）。
PERMISSION_RULES = [
    # 教程基线 1：文件工具越出工作区
    {
        "tools": ["read_file", "write_file", "edit_file"],
        "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
        "message": "Access outside workspace",
    },
    # 教程基线 2：bash 里疑似破坏性命令（含删除类，防 unlink/del/rmdir 绕过 rm 检测）
    {
        "tools": ["bash"],
        "check": lambda args: any(kw in args.get("command", "") for kw in
                                  ["rm ", "unlink ", "del ", "rmdir ", "> /etc/", "chmod 777"]),
        "message": "Potentially destructive command",
    },
    # 扩展 B：写敏感文件（.env、密钥、.gitignore）
    {
        "tools": ["write_file", "edit_file"],
        "check": lambda args: Path(args.get("path", "")).name in [".env", ".env.example", ".gitignore"] or args.get("path", "").endswith((".pem", ".key")),
        "message": "Writing sensitive file",
    },
    # 扩展 C：装包提示（边界操作）
    {
        "tools": ["bash"],
        "check": lambda args: "pip install" in args.get("command", "") or "npm install" in args.get("command", ""),
        "message": "Installing new package",
    },
]

# 用户输入 q 后置真：本会话剩余所有工具调用直接拒绝
_DENY_ALL = False


def check_deny_list(command: str):
    """Gate 1：命令命中 DENY_LIST 任一关键词则返回该关键词，否则 None。
    先折叠连续空白（防 'rm -rf  /' 双空格绕过），再做全词 + 子串匹配。"""
    norm = " ".join(command.split())
    for kw in DENY_LIST:
        if kw in norm.split() or kw in norm:
            return kw
    return None


def check_rules(tool_name: str, args: dict):
    """Gate 2：命中任一 PERMISSION_RULES 返回 message，否则 None。"""
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


def check_permission(tool_name: str, args: dict) -> tuple:
    """三道闸门串起来。返回 (是否允许, 拒绝原因)；允许时原因为空。"""
    if _DENY_ALL:
        return False, "Denied by user (deny all)"
    if tool_name == "bash":
        reason = check_deny_list(args.get("command", ""))
        if reason:
            return False, f"dangerous command: {reason}"  # Gate 1 硬拒，不问
    reason = check_rules(tool_name, args)
    if reason:
        if ask_user(tool_name, args, reason) == "deny":
            return False, reason
    return True, ""


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
]

# run_bash 内置 DENY（s02 保留，作为 check_permission 之后的兜底）
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


def agent_loop(messages):
    while True:
        resp = client.chat.completions.create(
            model=MODEL, messages=_strip_surrogates(messages), tools=TOOLS, max_tokens=8000,
        )
        msg = resp.choices[0].message
        messages.append(_strip_surrogates(msg.model_dump(exclude_none=True)))
        if not msg.tool_calls:
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

            # ---- Permission 闸门：放行才 dispatch，拒绝则回传消息继续 ----
            allowed, reason = check_permission(name, args)
            if not allowed:
                denied_msg = f"Permission denied: {reason}" if reason else "Permission denied by user."
                print(_strip_surrogates(f"\033[31m> {name}(DENIED)\033[0m"), flush=True)
                print(_strip_surrogates(denied_msg), flush=True)
                messages.append({
                    "role": "tool", "tool_call_id": call.id, "content": denied_msg,
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
            print(_strip_surrogates(str(output)[:200]), flush=True)
            messages.append({
                "role": "tool", "tool_call_id": call.id, "content": str(output),
            })


if __name__ == "__main__":
    history = [{"role": "system", "content": SYSTEM}]
    print(f"\033[36m使用模型 {MODEL}（5 工具 + 权限闸门），输入 q / exit / 空行退出\033[0m", flush=True)
    while True:
        try:
            q = input("\033[36ms03 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": q})
        agent_loop(history)
        print(flush=True)
