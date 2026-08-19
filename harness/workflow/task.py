"""harness.workflow.task — LocalWorkflowTask + TaskStatus + scheduler_tasks。"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class LocalWorkflowTask:
    run_id: str
    workflow_name: str
    status: TaskStatus = TaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    output: dict | None = None
    error: str | None = None
    events: list[dict] = field(default_factory=list)

    def append_event(self, event: dict) -> None:
        self.events.append(event)

    def to_summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output": self.output,
            "error": self.error,
        }


scheduler_tasks: dict[str, LocalWorkflowTask] = {}
_tasks_lock = threading.Lock()


def register_task(workflow_name: str) -> LocalWorkflowTask:
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    task = LocalWorkflowTask(run_id=run_id, workflow_name=workflow_name)
    with _tasks_lock:
        scheduler_tasks[run_id] = task
    return task


def emit_event(task: LocalWorkflowTask, event_type: str, **fields) -> dict:
    event = {"type": event_type, "run_id": task.run_id, "ts": time.time(), **fields}
    task.append_event(event)
    return event
