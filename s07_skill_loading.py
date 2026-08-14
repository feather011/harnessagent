#!/usr/bin/env python3
"""s07_skill_loading: 按需加载技能（基于 s06_subagent.py 扩展）。

相对 s06 的改动（其余不动：subagent/task、todo_write、safe_path、UTF-8/stdin、surrogate、
permission、5 hook、双层防御、rounds_since_todo 提醒）：
- skills/ 目录 + SkillLoader（scan/catalog/load/parse_frontmatter）
- SYSTEM 由 build_system_prompt() 在启动时构建：基础指令 + "Skills available:" catalog
- TOOLS 加 load_skill（查字典返回 SKILL.md 全文，不解释为路径）
"""
import ast
import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from dotenv import load_dotenv
from openai import OpenAI

# Windows 终端默认 GBK，强制 UTF-8；stdin 同理由 UTF-8 解码
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

WORKDIR = Path(__file__).resolve().parent
SKILLS_DIR = WORKDIR / "skills"


# ============================================================ SkillLoader
class SkillLoader:
    """扫描 skills/*/SKILL.md，维护 name→内容 注册表；只暴露 name 列表和按 name 取全文。"""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, dict[str, str]] = {}
        self.scan()

    @staticmethod
    def parse_frontmatter(text: str) -> tuple[dict, str]:
        """解析 YAML frontmatter（--- 包裹），返回 (metadata dict, 正文)。"""
        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text
        try:
            metadata = yaml.safe_load(parts[1]) or {}  # safe_load，不执行任意代码
        except yaml.YAMLError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, parts[2].lstrip()

    def scan(self):
        self.skills.clear()
        if not self.skills_dir.exists():
            return
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            content = manifest.read_text(encoding="utf-8", errors="replace")
            metadata, body = self.parse_frontmatter(content)
            name = str(metadata.get("name") or manifest.parent.name).strip()  # frontmatter 优先，目录名兜底
            description = metadata.get("description") or body.splitlines()[0]  # frontmatter 优先，正文首行兜底
            description = " ".join(str(description).lstrip("# ").split())
            self.skills[name] = {
                "name": name,
                "description": description,
                "content": content,
            }

    def catalog(self) -> str:
        if not self.skills:
            return "none"
        return "\n".join(f"- {s['name']}: {s['description']}" for s in self.skills.values())

    def load(self, name: str) -> str:
        """按 name 查注册表返回 SKILL.md 全文。name 不是路径，查字典防越权。"""
        skill = self.skills.get(name)
        if skill:
            return skill["content"]
        available = ", ".join(self.skills) or "none"
        return f"Error: Unknown skill '{name}'. Available: {available}"


SKILL_LOADER = SkillLoader(SKILLS_DIR)


def build_system_prompt() -> str:
    """启动时构建 SYSTEM：基础指令 + skill catalog（静态，不每次 LLM 调用重扫）。"""
    base = ("你是一个 coding agent，工作在 Windows 下的 Git Bash 环境。直接干活，不要解释。"
            "面对多步任务，先用 todo_write 工具列出计划并维护任务清单。")
    if not SKILL_LOADER.skills:
        return base + "\n\nNo skills loaded."  # 空目录不显示 "Skills available: none" 误导
    return (base + "\n\nSkills available:\n" + SKILL_LOADER.catalog()
            + "\n\nUse load_skill to read the full instructions when a skill applies.")


SYSTEM = build_system_prompt()

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


def _strip_surrogates(value):
    """递归清洗 str 中的孤立 surrogate 字符（openai SDK 3.0.0 序列化会崩）。"""
    if isinstance(value, str):
        return "".join(c for c in value if not 0xD800 <= ord(c) <= 0xDFFF)
    if isinstance(value, dict):
        return {k: _strip_surrogates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_surrogates(v) for v in value]
    return value


# ============================================================ 基础 6 工具（s05/s06 原样）
BASE_TOOLS = [
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


# ============================================================ TodoManager（s05 原样）
class TodoManager:
    _MARKERS = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}

    def __init__(self):
        self.items: list[dict] = []

    def update(self, todos) -> str:
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                todos = ast.literal_eval(todos)
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
        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")
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
    try:
        return TODO.update(todos)
    except ValueError as e:
        return f"Error: {e}"


# ============================================================ handler 表
BASE_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
    "todo_write": run_todo_write,
}

# ============================================================ Subagent（s06 原样）
SUB_SYSTEM = ("你是一个子 agent（subagent）。专注完成交给你的单一子任务，直接干活，不要解释。"
              "完成后返回简洁的最终答案。")

SUB_TOOLS = list(BASE_TOOLS)
SUB_HANDLERS = dict(BASE_HANDLERS)


def run_subagent(prompt: str) -> str:
    print("\n\033[35m[Subagent started]\033[0m", flush=True)
    messages = [
        {"role": "system", "content": SUB_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    for _ in range(30):
        resp = client.chat.completions.create(
            model=MODEL, messages=_strip_surrogates(messages), tools=SUB_TOOLS, max_tokens=8000,
        )
        msg = resp.choices[0].message
        messages.append(_strip_surrogates(msg.model_dump(exclude_none=True)))
        if not msg.tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": _strip_surrogates(str(force))})
                continue
            print("\033[35m[Subagent done]\033[0m", flush=True)
            return msg.content or "(no summary)"
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": f"Error: invalid arguments JSON: {e}",
                })
                continue
            blocked = trigger_hooks("PreToolUse", name, args)
            if blocked:
                print(f"  \033[31m[sub] {name}(DENIED): {str(blocked)[:100]}\033[0m", flush=True)
                messages.append({
                    "role": "tool", "tool_call_id": call.id, "content": str(blocked),
                })
                continue
            handler = SUB_HANDLERS.get(name)
            try:
                output = handler(**args) if handler else f"Error: unknown tool '{name}'"
            except Exception as e:
                output = f"Error: {type(e).__name__}: {e}"
            trigger_hooks("PostToolUse", name, args, output)
            print(f"  \033[90m[sub] {name}: {str(output)[:100]}\033[0m", flush=True)
            messages.append({
                "role": "tool", "tool_call_id": call.id, "content": str(output),
            })
    print("\033[35m[Subagent stopped]\033[0m", flush=True)
    return "Subagent stopped after 30 turns without a final answer."


# ============================================================ 工具集（基础 + task + load_skill）
TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "task",
        "description": ("Run a subagent with fresh conversation context and return its final text. "
                        "Use for focused exploration or self-contained subtasks."),
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "minLength": 1,
                       "description": "The task for the subagent. Be specific about what to find/do/return."},
        }, "required": ["prompt"]},
    },
}

LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "Load the full SKILL.md content by skill name from the catalog.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Skill name shown in the Skills available list"},
        }, "required": ["name"]},
    },
}

TOOLS = [*BASE_TOOLS, TASK_TOOL, LOAD_SKILL_TOOL]
TOOL_HANDLERS = {**BASE_HANDLERS, "task": run_subagent, "load_skill": SKILL_LOADER.load}

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
    rounds_since_todo = 0  # 每轮 agent_loop 独立计数
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

        used_todo = False
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

        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            messages.append({"role": "user", "content": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo = 0


if __name__ == "__main__":
    history = [{"role": "system", "content": SYSTEM}]
    print(f"\033[36m使用模型 {MODEL}（8 工具含 load_skill + hooks），输入 q / exit / 空行退出\033[0m", flush=True)
    while True:
        try:
            q = input("\033[36ms07 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", q)
        history.append({"role": "user", "content": q})
        agent_loop(history)
        print(flush=True)
