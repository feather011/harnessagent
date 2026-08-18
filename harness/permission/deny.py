"""harness.permission.deny — 硬编码危险命令黑名单。"""

DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda", "> /dev/"]


def check_deny_list(command: str) -> str | None:
    """检查命令是否命中黑名单，返回命中的关键词或 None。"""
    norm = " ".join(command.split())
    for kw in DENY_LIST:
        if kw in norm.split() or kw in norm:
            return kw
    return None
