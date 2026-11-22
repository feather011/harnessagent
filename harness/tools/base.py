"""harness.tools.base — 5 个基础工具实现。"""

import glob as _glob
import shutil
import subprocess
from pathlib import Path

_BASH = shutil.which("bash")

# 默认工作区，由 cli.py 设置
_WORKDIR: Path = Path.cwd()


class ToolOutput(str):
    """工具输出：保持字符串行为，同时携带结构化的进程退出码。

    宿主（Mini DeerFlow）需要的是**被测命令自己的退出码**，而不是从输出
    文本里解析出来的值。``exit_code`` 只由真正执行进程的工具（bash）设置；
    其它工具不设置（取不到就是 None）。
    """

    exit_code: int | None

    def __new__(cls, text: str, exit_code: int | None = None):
        obj = super().__new__(cls, text)
        obj.exit_code = exit_code
        return obj


def set_workdir(path: Path):
    global _WORKDIR
    _WORKDIR = path.resolve()


def get_workdir() -> Path:
    return _WORKDIR


def safe_path(p: str) -> Path:
    """确保路径不逃逸工作区。"""
    base = _WORKDIR.resolve()
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def bash(command: str, run_in_background: bool = False) -> str:
    """执行 shell 命令。Phase 1 不支持 run_in_background。"""
    if run_in_background:
        return ToolOutput("Background not yet implemented in Phase 1")
    try:
        if _BASH:
            r = subprocess.run(
                [_BASH, "-c", command], cwd=_WORKDIR,
                capture_output=True, text=True, errors="replace", timeout=30,
            )
        else:
            r = subprocess.run(
                command, shell=True, cwd=_WORKDIR,
                capture_output=True, text=True, errors="replace", timeout=30,
            )
    except subprocess.TimeoutExpired:
        return ToolOutput("Error: command timed out (30s)")
    out = (r.stdout + r.stderr).strip()
    if not out:
        out = "(no output)"
    if len(out) > 50000:
        out = out[:50000] + "\n... (output truncated)"
    # 保留退出码：宿主（Mini DeerFlow）需要区分「有输出」与「成功」。
    # 同时把 r.returncode 原样放进结构化字段（tool_done.exit_code）。
    return ToolOutput(f"[exit {r.returncode}] {out}", exit_code=r.returncode)


def read_file(path: str, limit: int | None = None) -> str:
    """读取工作区内的文本文件。"""
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


def write_file(path: str, content: str) -> str:
    """覆盖写入（或新建）工作区内的文件。"""
    try:
        p = safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    return f"Wrote {len(content.encode('utf-8'))} bytes to {path}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """在文件中做一次文本替换。"""
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
    """用 glob 模式在工作区中列出匹配的文件。"""
    try:
        base = _WORKDIR.resolve()
        matches = []
        for m in sorted(_glob.glob(str(base / pattern), recursive=True)):
            p = Path(m).resolve()
            if p.is_relative_to(base):
                matches.append(str(Path(m).relative_to(base)))
    except (OSError, ValueError) as e:
        return f"Error: {e}"
    return "\n".join(matches[:200]) if matches else "(no matches)"
