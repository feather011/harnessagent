"""harness.tools.scheduler — 3 个 cron 工具（schedule/list/cancel）。"""

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from harness.tools.pool import register_tool


@dataclass
class CronJob:
    """一条定时任务。"""
    id: str
    cron: str
    prompt: str
    recurring: bool
    durable: bool
    pending_delivery: bool = False
    last_fired: str | None = None


class CronScheduler:
    """定时调度器：daemon thread 每秒 poll 到点入队。"""

    def __init__(self, durable_path: Path):
        self.durable_path = durable_path
        self.scheduled_jobs: dict[str, CronJob] = {}
        self.cron_queue: list[CronJob] = []
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._start_lock = threading.Lock()

    # ============================================================ cron 解析
    @staticmethod
    def _cron_field_matches(field: str, value: int) -> bool:
        if field == "*":
            return True
        if field.startswith("*/"):
            return value % int(field[2:]) == 0
        if "," in field:
            return any(CronScheduler._cron_field_matches(p.strip(), value)
                       for p in field.split(","))
        if "-" in field:
            start, end = field.split("-", 1)
            return int(start) <= value <= int(end)
        return value == int(field)

    @staticmethod
    def cron_matches(cron_expr: str, moment: datetime) -> bool:
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            return False
        minute, hour, day, month, weekday = fields
        cron_weekday = (moment.weekday() + 1) % 7
        if not (CronScheduler._cron_field_matches(minute, moment.minute)
                and CronScheduler._cron_field_matches(hour, moment.hour)
                and CronScheduler._cron_field_matches(month, moment.month)):
            return False
        day_matches = CronScheduler._cron_field_matches(day, moment.day)
        weekday_matches = CronScheduler._cron_field_matches(weekday, cron_weekday)
        if day == "*" and weekday == "*":
            return True
        if day == "*":
            return weekday_matches
        if weekday == "*":
            return day_matches
        return day_matches or weekday_matches

    @staticmethod
    def _validate_cron_field(field: str, minimum: int, maximum: int) -> str | None:
        if field == "*":
            return None
        if field.startswith("*/"):
            step = field[2:]
            if not step.isdigit() or int(step) <= 0:
                return f"Invalid step: {field}"
            return None
        if "," in field:
            for part in field.split(","):
                err = CronScheduler._validate_cron_field(part.strip(), minimum, maximum)
                if err:
                    return err
            return None
        if "-" in field:
            start, end = field.split("-", 1)
            if not start.isdigit() or not end.isdigit():
                return f"Invalid range: {field}"
            sv, ev = int(start), int(end)
            if sv > ev:
                return f"Range start > end: {field}"
            if sv < minimum or ev > maximum:
                return f"Range {field} outside [{minimum}-{maximum}]"
            return None
        if not field.isdigit():
            return f"Invalid field: {field}"
        value = int(field)
        if value < minimum or value > maximum:
            return f"Value {value} outside [{minimum}-{maximum}]"
        return None

    @staticmethod
    def validate_cron(cron_expr: str) -> str | None:
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            return f"Expected 5 fields, got {len(fields)}"
        rules = [("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31),
                 ("month", 1, 12), ("weekday", 0, 6)]
        for field, (name, lo, hi) in zip(fields, rules):
            err = CronScheduler._validate_cron_field(field, lo, hi)
            if err:
                return f"{name}: {err}"
        return None

    # ============================================================ 持久化
    def save_durable_jobs(self):
        with self.lock:
            payload = [asdict(j) for j in self.scheduled_jobs.values() if j.durable]
        tmp = self.durable_path.with_suffix(f".tmp.{os.getpid()}")
        try:
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.durable_path)
        finally:
            tmp.unlink(missing_ok=True)

    def load_durable_jobs(self):
        if not self.durable_path.exists():
            return
        try:
            payload = json.loads(self.durable_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return
        except (OSError, json.JSONDecodeError):
            return
        loaded = 0
        with self.lock:
            for item in payload:
                try:
                    job = CronJob(**item)
                    if CronScheduler.validate_cron(job.cron):
                        continue
                    self.scheduled_jobs[job.id] = job
                    if job.pending_delivery:
                        self.cron_queue.append(job)
                    loaded += 1
                except (TypeError, ValueError):
                    continue
        if loaded:
            print(f"  [cron] loaded {loaded} durable job(s)", flush=True)

    # ============================================================ 增删
    def _new_job_id(self) -> str:
        for _ in range(100):
            jid = f"cron_{uuid.uuid4().hex[:8]}"
            if jid not in self.scheduled_jobs:
                return jid
        raise RuntimeError("Could not allocate a cron job ID")

    def schedule(self, cron: str, prompt: str, recurring: bool = True,
                 durable: bool = True) -> CronJob | str:
        error = CronScheduler.validate_cron(cron)
        if error:
            return error
        if not prompt.strip():
            return "Prompt cannot be empty"
        with self.lock:
            job = CronJob(id=self._new_job_id(), cron=cron, prompt=prompt,
                          recurring=recurring, durable=durable)
            self.scheduled_jobs[job.id] = job
            try:
                if durable:
                    self.save_durable_jobs()
            except Exception:
                self.scheduled_jobs.pop(job.id, None)
                raise
        print(f"  \033[90m[cron] scheduled {job.id}: {cron} -> {prompt[:60]}\033[0m", flush=True)
        return job

    def list_jobs(self) -> list[CronJob]:
        with self.lock:
            return list(self.scheduled_jobs.values())

    def cancel(self, job_id: str) -> str:
        with self.lock:
            job = self.scheduled_jobs.pop(job_id, None)
            if job is None:
                return f"Job {job_id} not found"
            self.cron_queue[:] = [q for q in self.cron_queue if q.id != job_id]
            try:
                if job.durable:
                    self.save_durable_jobs()
            except Exception:
                self.scheduled_jobs[job_id] = job
                raise
        return f"Cancelled {job_id}"

    # ============================================================ 队列
    def _enqueue_due_job(self, job: CronJob, minute_marker: str):
        old_pending = job.pending_delivery
        old_fired = job.last_fired
        job.pending_delivery = True
        job.last_fired = minute_marker
        try:
            if job.durable:
                self.save_durable_jobs()
        except Exception:
            job.pending_delivery = old_pending
            job.last_fired = old_fired
            raise
        self.cron_queue.append(job)

    def poll_due_jobs(self, moment: datetime):
        marker = moment.strftime("%Y-%m-%d %H:%M")
        with self.lock:
            for job in list(self.scheduled_jobs.values()):
                try:
                    if job.pending_delivery or job.last_fired == marker:
                        continue
                    if CronScheduler.cron_matches(job.cron, moment):
                        self._enqueue_due_job(job, marker)
                        print(f"  \033[90m[cron] due {job.id}: {job.prompt[:60]}\033[0m", flush=True)
                except Exception as e:
                    print(f"  [cron] enqueue error {job.id}: {e}", flush=True)

    def consume_queue(self) -> list[CronJob]:
        with self.lock:
            jobs = list(self.cron_queue)
            self.cron_queue.clear()
        return jobs

    def acknowledge(self, jobs: list[CronJob]):
        with self.lock:
            for delivered in jobs:
                current = self.scheduled_jobs.get(delivered.id)
                if current is None:
                    continue
                if current.recurring:
                    current.pending_delivery = False
                else:
                    self.scheduled_jobs.pop(current.id, None)
            try:
                if any(j.durable for j in jobs):
                    self.save_durable_jobs()
            except Exception:
                pass  # best-effort

    def restore(self, jobs: list[CronJob]):
        with self.lock:
            queued_ids = {j.id for j in self.cron_queue}
            for delivered in jobs:
                current = self.scheduled_jobs.get(delivered.id)
                if current is None:
                    continue
                current.pending_delivery = True
                if current.id not in queued_ids:
                    self.cron_queue.append(current)
                    queued_ids.add(current.id)

    # ============================================================ 线程
    def _scheduler_loop(self):
        while not self._stop.wait(1.0):
            self.poll_due_jobs(datetime.now())

    def start(self):
        with self._start_lock:
            if self._started:
                return
            self.load_durable_jobs()
            self._stop.clear()
            self._thread = threading.Thread(target=self._scheduler_loop,
                                            name="cron-scheduler", daemon=True)
            self._thread.start()
            self._started = True

    def stop(self):
        with self._start_lock:
            if not self._started:
                return
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1)
            self._started = False


# Module-level singleton (deferred init)
SCHEDULER: CronScheduler | None = None

# 3 个工具 schema
SCHEDULE_CRON_SCHEMA = {"type": "function", "function": {
    "name": "schedule_cron",
    "description": "安排一个 5 字段 cron 表达式（minute hour day month weekday）在本地时间到点触发。",
    "parameters": {"type": "object", "properties": {
        "cron": {"type": "string", "description": "5 字段 cron，如 '0 9 * * *'"},
        "prompt": {"type": "string", "description": "到点时交给 agent 的任务"},
        "recurring": {"type": "boolean", "description": "是否重复触发（默认 true）"},
        "durable": {"type": "boolean", "description": "是否持久化（默认 true）"},
    }, "required": ["cron", "prompt"]}
}}

LIST_CRONS_SCHEMA = {"type": "function", "function": {
    "name": "list_crons",
    "description": "列出所有已安排的 cron 任务。",
    "parameters": {"type": "object", "properties": {}},
}}

CANCEL_CRON_SCHEMA = {"type": "function", "function": {
    "name": "cancel_cron",
    "description": "按 job ID 取消一个 cron 任务。",
    "parameters": {"type": "object", "properties": {
        "job_id": {"type": "string", "description": "cron 任务 ID，如 cron_abcd1234"},
    }, "required": ["job_id"]}
}}


def init_scheduler(durable_path: Path):
    """由 cli.py 调用，初始化 CronScheduler 并注册 3 个工具。"""
    global SCHEDULER
    SCHEDULER = CronScheduler(durable_path)
    SCHEDULER.start()

    def run_schedule_cron(cron: str, prompt: str, recurring: bool = True,
                          durable: bool = True) -> str:
        result = SCHEDULER.schedule(cron, prompt, recurring, durable)
        if isinstance(result, str):
            return f"Error: {result}"
        return f"Scheduled {result.id}: {cron} -> {prompt}"

    def run_list_crons() -> str:
        jobs = SCHEDULER.list_jobs()
        if not jobs:
            return "No cron jobs."
        lines = []
        for j in jobs:
            freq = "recurring" if j.recurring else "one-shot"
            lines.append(f"{j.id}: {j.cron} -> {j.prompt[:60]} [{freq}]")
        return "\n".join(lines)

    def run_cancel_cron(job_id: str) -> str:
        return SCHEDULER.cancel(job_id)

    register_tool(SCHEDULE_CRON_SCHEMA, run_schedule_cron)
    register_tool(LIST_CRONS_SCHEMA, run_list_crons)
    register_tool(CANCEL_CRON_SCHEMA, run_cancel_cron)
