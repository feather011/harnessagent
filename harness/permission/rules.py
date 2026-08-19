"""harness.permission.rules — 基于规则的权限检查。"""

from pathlib import Path


def _check_out_of_workspace(args: dict, workdir: Path) -> bool:
    p = args.get("path", "")
    if not p:
        return False
    try:
        return not (workdir / p).resolve().is_relative_to(workdir.resolve())
    except (OSError, ValueError):
        return True


def _check_destructive_bash(args: dict, workdir: Path) -> bool:
    cmd = args.get("command", "")
    return any(kw in cmd for kw in ["rm ", "unlink ", "del ", "rmdir ", "> /etc/", "chmod 777"])


def _check_sensitive_file(args: dict, workdir: Path) -> bool:
    p = args.get("path", "")
    name = Path(p).name if p else ""
    return name in (".env", ".env.example", ".gitignore") or p.endswith((".pem", ".key"))


def _check_install_package(args: dict, workdir: Path) -> bool:
    cmd = args.get("command", "")
    return "pip install" in cmd or "npm install" in cmd


# rules: (tool_names, check_func, message)
PERMISSION_RULES = [
    (["read_file", "write_file", "edit_file"], _check_out_of_workspace, "Access outside workspace"),
    (["bash"], _check_destructive_bash, "Potentially destructive command"),
    (["write_file", "edit_file"], _check_sensitive_file, "Writing sensitive file"),
    (["bash"], _check_install_package, "Installing new package"),
]


def check_rules(tool_name: str, args: dict, workdir: Path) -> str | None:
    """逐条检查规则，返回拒绝消息或 None。"""
    for tool_names, check, message in PERMISSION_RULES:
        if tool_name in tool_names and check(args, workdir):
            return message
    return None
