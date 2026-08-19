"""harness.workflow.context — RunContext（6 原语）。"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from harness.workflow.journal import WorkflowJournal
from harness.workflow.task import emit_event, TaskStatus


class RunContext:
    """Workflow 运行上下文：6 原语（agent/parallel/pipeline/phase/log/final）。"""

    def __init__(self, run_id: str, args: dict, runner, journal_dir: Path,
                 task, max_workers: int = 5):
        self.run_id = run_id
        self.args = args
        self.runner = runner
        self.journal = WorkflowJournal(journal_dir, run_id)
        self.task = task
        self.max_workers = max_workers
        self.output_dir = journal_dir
        self.output_path = journal_dir / f"{run_id}.output.json"

    def log(self, event_type: str, **fields) -> dict:
        """emit progress event。"""
        return emit_event(self.task, event_type, **fields)

    def agent(self, name: str, prompt: str, schema=None) -> dict:
        """stable_key → journal cache check → runner.run → schema validate + 1 retry。"""
        from harness.workflow.runner import SimpleJsonSchema
        key = self.journal.stable_key("agent", name, prompt,
                                       schema.schema_repr() if schema else "")

        # Journal cache check
        cached = self.journal.cached(key)
        if cached is not None:
            self.log("task_progress", kind="agent", key=key, status="resumed")
            return cached

        # Write in_progress + run
        self.journal.record(key, {"kind": "agent_call", "status": "in_progress",
                                   "ts": time.time(), "prompt": prompt[:200]})
        self.log("task_progress", kind="agent", key=key, status="started", name=name)
        try:
            output = self.runner.run(prompt, schema)
        except Exception as e:
            self.journal.record(key, {"kind": "agent_call", "status": "failed",
                                       "ts": time.time(), "error": f"{type(e).__name__}: {e}"})
            self.log("task_progress", kind="agent", key=key, status="failed", name=name, error=str(e))
            raise

        # Schema validate + 1 retry
        if schema is not None:
            ok, err = schema.validate(output)
            if not ok:
                retry_prompt = f"{prompt}\n\nYour previous output failed validation: {err}\nPlease retry with valid JSON."
                try:
                    output = self.runner.run(retry_prompt, schema)
                    ok, err = schema.validate(output)
                except Exception:
                    pass
                if not ok:
                    raise RuntimeError(f"schema validation failed: {err}")

        self.journal.record(key, {"kind": "agent_call", "status": "completed",
                                   "ts": time.time(), "output": output})
        self.log("task_progress", kind="agent", key=key, status="completed", name=name)
        return output

    def parallel(self, items: list, fn) -> list:
        """等齐屏障：所有 items 并行，全部完成才返。"""
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(fn, item) for item in items]
            return [fut.result() for fut in as_completed(futures)]

    def pipeline(self, items: list, fn) -> list:
        """顺序执行：每个 item 走 fn。"""
        return [fn(item) for item in items]

    def phase(self, label: str, fn=None):
        """标记阶段。"""
        self.log("task_progress", kind="phase", label=label, status="started")
        if fn is not None:
            result = fn()
            self.log("task_progress", kind="phase", label=label, status="completed")
            return result
        self.log("task_progress", kind="phase", label=label, status="completed")

    def final(self, status: TaskStatus, output: dict | None = None, error: str | None = None):
        """设置最终状态 + 写 output 文件。"""
        self.task.status = status
        self.task.finished_at = time.time()
        self.task.output = output
        self.task.error = error
        result = {"run_id": self.run_id, "status": status.value, "output": output, "error": error}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.journal.record(f"final_{self.run_id}", result)
