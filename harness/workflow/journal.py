"""harness.workflow.journal — WorkflowJournal（SHA256 stable_key + jsonl append）。"""

import hashlib
import json
import time
from pathlib import Path


class WorkflowJournal:
    """每条 record：{kind, key, status, ts, ...} 追加到 .runtime/<run_id>.journal.jsonl。"""

    def __init__(self, journal_dir: Path, run_id: str):
        self.journal_dir = journal_dir
        self.run_id = run_id
        self.path = journal_dir / f"{run_id}.journal.jsonl"
        self._cache: dict[str, dict] = {}
        self._load_cache()

    def _load_cache(self):
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                key = record.get("key")
                if key and record.get("status") == "completed":
                    self._cache[key] = record.get("output", {})
            except json.JSONDecodeError:
                continue

    def stable_key(self, kind: str, label: str, prompt: str, schema_repr: str = "") -> str:
        """SHA256(kind|label|prompt|schema) → 10 位数字字符串。"""
        basis = "\x00".join([kind, label, prompt, schema_repr])
        h = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        return f"agent_{int(h, 16) % (10 ** 10):010d}"

    def record(self, key: str, value: dict):
        """写一条 journal record + 更新 cache。"""
        entry = {"key": key, "value": value, "ts": time.time()}
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
        self._cache[key] = value

    def cached(self, key: str) -> dict | None:
        """返回缓存的 output 或 None（MISS）。"""
        return self._cache.get(key)
