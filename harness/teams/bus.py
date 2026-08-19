"""harness.teams.bus — MessageBus（.mailboxes/<name>.jsonl 线程安全文件邮箱）。"""

import json
import threading
import time
from pathlib import Path


def _is_valid_agent_name(name: str) -> bool:
    return bool(name) and len(name) <= 64 and all(c.isalnum() or c in "-_" for c in name)


class MessageBus:
    """线程安全文件邮箱：每个 agent 一个 .mailboxes/<name>.jsonl，destructive read。"""

    def __init__(self, mailbox_dir: Path):
        self._dir = mailbox_dir
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)

    def _path(self, agent: str) -> Path:
        if not _is_valid_agent_name(agent):
            raise ValueError(f"Invalid agent name: {agent!r}")
        path = (self._dir / f"{agent}.jsonl").resolve()
        if not path.is_relative_to(self._dir.resolve()):
            raise ValueError(f"Mailbox path escapes directory: {agent!r}")
        return path

    def _read_unlocked(self, agent: str) -> list[dict]:
        inbox = self._path(agent)
        if not inbox.exists():
            return []
        msgs = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        inbox.unlink(missing_ok=True)
        return msgs

    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict | None = None):
        """发送消息到目标 agent 的邮箱。"""
        msg = {"from": from_agent, "to": to_agent, "content": content,
               "type": msg_type, "ts": time.time(), "metadata": metadata or {}}
        with self._changed:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._path(to_agent).open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=True) + "\n")
            self._changed.notify_all()

    def read_inbox(self, agent: str) -> list[dict]:
        """读取并删除邮箱内容（destructive read）。"""
        with self._lock:
            return self._read_unlocked(agent)

    def peek(self, agent: str) -> bool:
        """检查是否有未读消息（不消费）。"""
        with self._lock:
            path = self._path(agent)
            return path.exists() and path.stat().st_size > 0

    def wait_for_messages(self, agent: str, timeout: float | None = None) -> list[dict]:
        """阻塞等待消息或超时。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._changed:
            while not self.peek(agent):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return []
                self._changed.wait(remaining)
            return self._read_unlocked(agent)

    def notify_all(self):
        """唤醒所有等待的线程。"""
        with self._changed:
            self._changed.notify_all()
