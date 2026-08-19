"""harness.teams.worktree — Worktree 创建/移除（host-side helpers）。"""

import re
import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    """执行 git 命令，返回 (success, output)。"""
    try:
        r = subprocess.run(
            ["git"] + args, capture_output=True, text=True, errors="replace",
            cwd=cwd, timeout=30,
        )
        output = (r.stdout + r.stderr).strip()
        return r.returncode == 0, output or "(no output)"
    except Exception as e:
        return False, f"Error: {e}"


def validate_worktree_name(name: str) -> str | None:
    """校验 worktree 名称：1-64 字母数字下划线短横线。返回错误或 None。"""
    if not name:
        return "Worktree name cannot be empty"
    if len(name) > 64:
        return "Worktree name too long (max 64)"
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        return "Worktree name must contain only letters, digits, underscores, dashes"
    return None


def _worktree_path(name: str, workdir: Path) -> Path:
    return (workdir / ".worktrees" / name).resolve()


def _worktree_branch(name: str) -> str:
    return f"wt/{name}"


def create_worktree(name: str, task_id: str, workdir: Path) -> str:
    """创建并绑定 worktree。校验 + git worktree add + 任务绑定。"""
    error = validate_worktree_name(name)
    if error:
        return f"Error: {error}"

    path = _worktree_path(name, workdir)
    branch = _worktree_branch(name)

    # 检查 workdir 是 git 仓库
    ok, root = _run_git(["rev-parse", "--show-toplevel"], cwd=workdir)
    if not ok or Path(root).resolve() != workdir.resolve():
        return "Error: Working directory must be the root of a Git repository"

    # 检查分支名
    ok, _ = _run_git(["check-ref-format", "--branch", branch])
    if not ok:
        return f"Error: Invalid branch name '{branch}'"

    # 检查分支不存在
    ok, _ = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=workdir)
    if ok:
        return f"Error: Branch '{branch}' already exists"

    # 检查路径不存在
    if path.exists():
        return f"Error: Worktree path already exists: {path}"

    # 创建
    worktrees_dir = workdir / ".worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    ok, result = _run_git(["worktree", "add", "-b", branch, str(path), "HEAD"], cwd=workdir)
    if not ok:
        return f"Git error: {result}"

    return f"Worktree '{name}' created at {path} for task {task_id}"


def remove_worktree(name: str, workdir: Path, discard_changes: bool = False) -> str:
    """移除 worktree。检查状态后 git worktree remove。"""
    error = validate_worktree_name(name)
    if error:
        return f"Error: {error}"

    path = _worktree_path(name, workdir)
    if not path.exists():
        return f"Error: Worktree '{name}' does not exist"

    # 检查未提交更改
    if not discard_changes:
        ok, status = _run_git(["status", "--porcelain"], cwd=path)
        if ok and status.strip() and status != "(no output)":
            changed = len(status.strip().splitlines())
            return (f"Error: Worktree '{name}' has {changed} uncommitted change(s). "
                    "Use discard_changes=true to force removal.")

    # 移除
    ok, result = _run_git(["worktree", "remove", str(path), "--force"], cwd=workdir)
    if not ok:
        return f"Error removing worktree: {result}"

    return f"Worktree '{name}' removed (branch '{_worktree_branch(name)}' preserved)"


def assignment_cwd(teammate_name: str) -> Path:
    """获取 teammate 的工作目录。Phase 4 简化：返回 workdir。"""
    # Phase 4 简化版：所有 teammate 共享主工作目录
    # 完整版会根据 task assignment 返回 worktree 路径
    import os
    return Path.cwd()
