"""harness.tools.tasks — 5 个任务工具（create/list/get/claim/complete）。"""

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from harness.tools.pool import register_tool

TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{8}$")


@dataclass
class Task:
    """一条可持久化的任务。"""
    id: str
    subject: str
    description: str
    status: str          # pending | in_progress | completed | failed
    owner: str | None
    blockedBy: list[str]
    created_at: str


class TaskStore:
    """任务文件存储：.tasks/{id}.json。"""

    def __init__(self, directory: Path):
        self.directory = directory

    def _ensure_dir(self):
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"Invalid task ID: {task_id!r}")
        self._ensure_dir()
        path = (self.directory / f"{task_id}.json").resolve()
        if not path.is_relative_to(self.directory.resolve()):
            raise ValueError(f"Invalid task ID: {task_id!r}")
        return path

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).is_file()

    def create(self, subject: str, description: str = "",
               blocked_by: list[str] | None = None) -> Task:
        subject = subject.strip()
        if not subject:
            raise ValueError("Task subject cannot be empty")
        dependencies = list(dict.fromkeys(blocked_by or []))
        for dep in dependencies:
            if not self.exists(dep):
                raise ValueError(f"Dependency not found: {dep}")
        self._ensure_dir()
        for _ in range(100):
            task = Task(
                id=f"task_{uuid.uuid4().hex[:8]}",
                subject=subject, description=description,
                status="pending", owner=None,
                blockedBy=dependencies,
                created_at=datetime.now().isoformat(),
            )
            try:
                with self._path(task.id).open("x", encoding="utf-8") as f:
                    json.dump(asdict(task), f, indent=2, ensure_ascii=False)
                return task
            except FileExistsError:
                continue
        raise RuntimeError("Could not allocate a unique task ID")

    def save(self, task: Task) -> None:
        self._path(task.id).write_text(
            json.dumps(asdict(task), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load(self, task_id: str) -> Task:
        data = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        task = Task(**data)
        if task.id != task_id:
            raise ValueError(f"Task file ID mismatch: {task_id}")
        return task

    def list_all(self) -> list[Task]:
        if not self.directory.exists():
            return []
        return [self.load(p.stem) for p in sorted(self.directory.glob("task_*.json"))]

    def claim(self, task_id: str, owner: str = "agent") -> str:
        task = self.load(task_id)
        if task.status != "pending":
            return f"Task {task_id} is {task.status}, cannot claim"
        incomplete = [dep for dep in task.blockedBy if not self._dep_completed(dep)]
        if incomplete:
            return f"Blocked by: {', '.join(incomplete)}"
        task.owner = owner
        task.status = "in_progress"
        self.save(task)
        return f"Claimed {task.id} ({task.subject})"

    def complete(self, task_id: str, owner: str = "agent") -> str:
        task = self.load(task_id)
        if task.status != "in_progress":
            return f"Task {task_id} is {task.status}, cannot complete"
        if task.owner != owner:
            return f"Task {task_id} is owned by {task.owner}, not {owner}"
        task.status = "completed"
        self.save(task)
        return f"Completed {task.id} ({task.subject})"

    def _dep_completed(self, dep_id: str) -> bool:
        try:
            return self.load(dep_id).status == "completed"
        except (FileNotFoundError, ValueError):
            return False


# Module-level singleton (deferred init)
TASK_STORE: TaskStore | None = None

# 5 个工具 schema
CREATE_TASK_SCHEMA = {"type": "function", "function": {
    "name": "create_task",
    "description": "创建一个持久化任务。blockedBy 声明依赖（必须是已存在的任务 ID）。",
    "parameters": {"type": "object", "properties": {
        "subject": {"type": "string", "description": "任务主题"},
        "description": {"type": "string", "description": "可选，详细描述"},
        "blockedBy": {"type": "array", "items": {"type": "string"},
                      "description": "可选，依赖的任务 ID 列表"},
    }, "required": ["subject"]}
}}

LIST_TASKS_SCHEMA = {"type": "function", "function": {
    "name": "list_tasks",
    "description": "列出所有任务及其状态。",
    "parameters": {"type": "object", "properties": {}},
}}

GET_TASK_SCHEMA = {"type": "function", "function": {
    "name": "get_task",
    "description": "按 ID 获取任务完整信息。",
    "parameters": {"type": "object", "properties": {
        "task_id": {"type": "string", "description": "任务 ID，如 task_abcd1234"},
    }, "required": ["task_id"]}
}}

CLAIM_TASK_SCHEMA = {"type": "function", "function": {
    "name": "claim_task",
    "description": "认领一个 pending 且依赖已满足的任务。",
    "parameters": {"type": "object", "properties": {
        "task_id": {"type": "string", "description": "任务 ID"},
    }, "required": ["task_id"]}
}}

COMPLETE_TASK_SCHEMA = {"type": "function", "function": {
    "name": "complete_task",
    "description": "完成当前认领的任务。",
    "parameters": {"type": "object", "properties": {
        "task_id": {"type": "string", "description": "任务 ID"},
    }, "required": ["task_id"]}
}}


def init_task_store(directory: Path):
    """由 cli.py 调用，初始化 TaskStore 并注册 5 个工具。"""
    global TASK_STORE
    TASK_STORE = TaskStore(directory)

    def run_create_task(subject: str, description: str = "",
                        blockedBy: list[str] | None = None) -> str:
        try:
            task = TASK_STORE.create(subject, description, blockedBy)
            deps = f" (blockedBy: {', '.join(task.blockedBy)})" if task.blockedBy else ""
            return f"Created {task.id}: {task.subject}{deps}"
        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"

    def run_list_tasks() -> str:
        tasks = TASK_STORE.list_all()
        if not tasks:
            return "No tasks."
        lines = []
        for t in tasks:
            owner = f" (owner: {t.owner})" if t.owner else ""
            blocked = f" (blockedBy: {', '.join(t.blockedBy)})" if t.blockedBy else ""
            lines.append(f"{t.id}: [{t.status}] {t.subject}{owner}{blocked}")
        return "\n".join(lines)

    def run_get_task(task_id: str) -> str:
        try:
            task = TASK_STORE.load(task_id)
            return json.dumps(asdict(task), indent=2, ensure_ascii=False)
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"

    def run_claim_task(task_id: str, owner: str = "agent") -> str:
        return TASK_STORE.claim(task_id, owner)

    def run_complete_task(task_id: str, owner: str = "agent") -> str:
        return TASK_STORE.complete(task_id, owner)

    register_tool(CREATE_TASK_SCHEMA, run_create_task)
    register_tool(LIST_TASKS_SCHEMA, run_list_tasks)
    register_tool(GET_TASK_SCHEMA, run_get_task)
    register_tool(CLAIM_TASK_SCHEMA, run_claim_task)
    register_tool(COMPLETE_TASK_SCHEMA, run_complete_task)
