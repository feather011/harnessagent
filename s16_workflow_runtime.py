#!/usr/bin/env python3
"""s16_workflow_runtime: workflow 编排 runtime（基于 s15_integrated_harness，不动前置任何章节）。

策略（C 方案）：s16 自写 agent_loop，只借 s15 的 6 钩子 + drain_events + assemble_system_prompt
3 个纯函数组件；s15 的 5 个 s14.HOOKS 自动经 _hook_* 薄包装在 s16 生效（context_inject/
log/permission/large_output/summary）。ThreadPoolExecutor 并发，SHA256 stable_key，
.runtime/ 目录存 journal/snapshot/output/lock（已 gitignore）。
"""
import atexit
import hashlib
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

import s14_mcp_plugin as s14
import s15_integrated_harness as s15
from openai import OpenAI
from dotenv import load_dotenv

# 沿用 s15 的 6 钩子 + drain_events + _compaction_step + assemble_system_prompt
_strip_surrogates = s14._strip_surrogates
_drain_events = s15.drain_events
_compaction_step = s15._compaction_step
_assemble_system_prompt = s15.assemble_system_prompt

client = s14.client
MODEL = s14.MODEL
WORKDIR = s14.WORKDIR

# ============================================================ .runtime/ 目录 + 跨平台文件锁 ============================================================
RUNTIME_DIR = WORKDIR / ".runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

_SLUG = re.compile(r"^[a-zA-Z0-9_-]+$")


def _file_lock_acquire(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        if handle.read(1) == "":
            handle.write("x")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _file_lock_release(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_path(run_id: str) -> Path:
    return RUNTIME_DIR / f"{run_id}.lock"


def acquire_run_lock(run_id: str):
    """整次 workflow 执行持锁（跨进程防同 run_id 双跑）。"""
    p = _lock_path(run_id)
    handle = p.open("a+")
    try:
        _file_lock_acquire(handle)
    except Exception:
        handle.close()
        raise
    return handle


def release_run_lock(handle):
    try:
        _file_lock_release(handle)
    finally:
        handle.close()


# ============================================================ 状态机 + 任务模型 ============================================================
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


# scheduler_tasks[run_id] = LocalWorkflowTask（内存，跨重启丢；journal 是真相）
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


# ============================================================ Journal / Resume ============================================================
def journal_path(run_id: str) -> Path:
    return RUNTIME_DIR / f"{run_id}.journal.jsonl"


def snapshot_path(run_id: str) -> Path:
    return RUNTIME_DIR / f"{run_id}.json"


def output_path(run_id: str) -> Path:
    return RUNTIME_DIR / f"{run_id}.output.json"


def write_journal(path: Path, record: dict) -> None:
    """每条立即 append + flush（中断可恢复）。"""
    with path.open("a", encoding="utf-8", errors="replace") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def stable_key(kind: str, label: str, prompt: str, schema_repr: str = "") -> str:
    """SHA256(kind | label | prompt | schema) 截断 10 位数字，并发顺序无关。"""
    h = hashlib.sha256("\x00".join([kind, label, prompt, schema_repr]).encode("utf-8")).hexdigest()
    return f"agent_{int(h, 16) % (10 ** 10):010d}"


# ============================================================ 简单 JSON Schema（带 1 次重试的产出） ============================================================
@dataclass
class SimpleJsonSchema:
    required: list[str]
    types: dict[str, type] = field(default_factory=dict)

    def validate(self, output: Any) -> tuple[bool, str | None]:
        if not isinstance(output, dict):
            return False, f"expected dict, got {type(output).__name__}"
        for f in self.required:
            if f not in output:
                return False, f"missing required field: {f}"
        for f, expected_type in self.types.items():
            if f in output and not isinstance(output[f], expected_type):
                return False, f"field {f!r} expected {expected_type.__name__}, got {type(output[f]).__name__}"
        return True, None

    def schema_repr(self) -> str:
        return json.dumps({"required": self.required, "types": {k: t.__name__ for k, t in self.types.items()}},
                          sort_keys=True)


# ============================================================ Agent Runner 抽象（同步阻塞 API） ============================================================
class AgentRunner(Protocol):
    def run(self, prompt: str, schema: SimpleJsonSchema | None = None) -> dict: ...


class RealAgentRunner:
    """DeepSeek via openai SDK 3.0.0；schema 用 tool_use 强结构化 JSON。"""

    def __init__(self, model: str = MODEL, llm_client=client, max_retries: int = 1):
        self.model = model
        self.client = llm_client
        self.max_retries = max_retries

    def _call(self, prompt: str, schema: SimpleJsonSchema | None) -> dict:
        if schema is None:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
            )
            content = resp.choices[0].message.content or ""
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"result": content}
        tool = {
            "type": "function",
            "function": {
                "name": "submit",
                "description": "Submit the structured result.",
                "parameters": {
                    "type": "object",
                    "properties": {f: {"type": _json_schema_type(t)} for f, t in schema.types.items()},
                    "required": schema.required,
                },
            },
        }
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": "submit"}},
            max_tokens=4000,
        )
        for call in resp.choices[0].message.tool_calls or []:
            try:
                return json.loads(call.function.arguments)
            except json.JSONDecodeError:
                continue
        raise RuntimeError("model did not produce valid JSON via tool_call")

    def run(self, prompt: str, schema: SimpleJsonSchema | None = None) -> dict:
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                output = self._call(prompt, schema)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                output = {}
            if schema is not None:
                ok, err = schema.validate(output)
                if ok:
                    return output
                last_err = err
                prompt = f"{prompt}\n\nYour previous output failed validation: {err}\nPlease retry with valid JSON matching the schema."
                continue
            return output
        raise RuntimeError(f"schema validation failed after retries: {last_err}")


def _json_schema_type(python_type: type) -> str:
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
    return mapping.get(python_type, "string")


class MockAgentRunner:
    """测试用：基于 prompt 关键词返伪数据；calls 记录调用历史。"""

    def __init__(self, responses: dict[str, dict] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str | None]] = []  # (prompt, schema_repr)

    def run(self, prompt: str, schema: SimpleJsonSchema | None = None) -> dict:
        self.calls.append((prompt, schema.schema_repr() if schema else None))
        for key, resp in self.responses.items():
            if key in prompt:
                return resp
        if schema is not None and schema.required:
            return {schema.required[0]: f"mock({prompt[:40]})"}
        return {"result": f"mock({prompt[:40]})"}


# ============================================================ RunContext + 编排原语 ============================================================
@dataclass
class RunContext:
    run_id: str
    args: dict
    runner: AgentRunner
    journal: Path
    snapshot: Path
    output: Path
    task: LocalWorkflowTask
    max_workers: int = 5

    def log(self, event_type: str, **fields) -> dict:
        return emit_event(self.task, event_type, **fields)

    def agent(self, name: str, prompt: str, schema: SimpleJsonSchema | None = None) -> dict:
        key = stable_key("agent", name, prompt, schema.schema_repr() if schema else "")
        # journal 检查：命中 completed → 返历史 output
        for record in read_journal(self.journal):
            if record.get("kind") == "agent_call" and record.get("key") == key and record.get("status") == "completed":
                self.log("task_progress", kind="agent", key=key, status="resumed")
                return record.get("output", {})
        # 未命中 → 写 in_progress + 真跑
        write_journal(self.journal, {"kind": "agent_call", "key": key, "name": name,
                                     "status": "in_progress", "ts": time.time(), "prompt": prompt[:200]})
        self.log("task_progress", kind="agent", key=key, status="started", name=name)
        try:
            output = self.runner.run(prompt, schema)
        except Exception as e:
            write_journal(self.journal, {"kind": "agent_call", "key": key, "status": "failed",
                                         "ts": time.time(), "error": f"{type(e).__name__}: {e}"})
            self.log("task_progress", kind="agent", key=key, status="failed", name=name, error=str(e))
            raise
        write_journal(self.journal, {"kind": "agent_call", "key": key, "status": "completed",
                                     "ts": time.time(), "output": output})
        self.log("task_progress", kind="agent", key=key, status="completed", name=name)
        return output

    def parallel(self, items: list, fn: Callable[[Any], Any]) -> list:
        """等齐屏障：所有 items 并行，全部完成才返。任一失败抛。"""
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [ex.submit(fn, item) for item in items]
            results = []
            for fut in as_completed(futures):
                results.append(fut.result())  # 抛异常即整失败
        return results

    def pipeline(self, items: list, fn: Callable[[Any], Any]) -> list:
        """不等齐独立：fire-and-forget，返 Thread list（不等待）。"""
        threads = [threading.Thread(target=fn, args=(item,), daemon=True) for item in items]
        for t in threads:
            t.start()
        return threads

    def phase(self, label: str, fn: Callable[[], Any]) -> Any:
        write_journal(self.journal, {"kind": "phase", "label": label, "status": "in_progress", "ts": time.time()})
        self.log("task_progress", kind="phase", label=label, status="started")
        try:
            result = fn()
        except Exception as e:
            write_journal(self.journal, {"kind": "phase", "label": label, "status": "failed",
                                         "ts": time.time(), "error": f"{type(e).__name__}: {e}"})
            self.log("task_progress", kind="phase", label=label, status="failed", error=str(e))
            raise
        write_journal(self.journal, {"kind": "phase", "label": label, "status": "completed",
                                     "ts": time.time()})
        self.log("task_progress", kind="phase", label=label, status="completed")
        return result

    def final(self, status: TaskStatus, output: dict | None = None, error: str | None = None) -> None:
        self.task.status = status
        self.task.finished_at = time.time()
        self.task.output = output
        self.task.error = error
        self.output.write_text(json.dumps({"run_id": self.run_id, "status": status.value,
                                          "output": output, "error": error},
                                         ensure_ascii=False, indent=2),
                               encoding="utf-8")
        write_journal(self.journal, {"kind": "workflow", "status": status.value,
                                     "ts": time.time(), "output": output, "error": error})


# ============================================================ WORKFLOWS Registry + workflow decorator ============================================================
WORKFLOWS: dict[str, tuple[dict, Callable]] = {}


def validate_meta(meta: dict) -> tuple[bool, str | None]:
    name = meta.get("name")
    if not isinstance(name, str) or not _SLUG.fullmatch(name) or len(name) > 64:
        return False, f"workflow name must be 1-64 chars matching {_SLUG.pattern}; got {name!r}"
    if not isinstance(meta.get("description"), str) or not meta["description"].strip():
        return False, "description must be non-empty string"
    phases = meta.get("phases")
    if not isinstance(phases, list) or not phases or not all(isinstance(p, str) for p in phases):
        return False, "phases must be a non-empty list of strings"
    return True, None


def workflow(meta: dict):
    """Decorator：注册到 WORKFLOWS（启动时 validate_meta）。"""
    ok, err = validate_meta(meta)
    if not ok:
        raise ValueError(f"workflow meta invalid: {err}")
    name = meta["name"]

    def decorator(script_fn: Callable) -> Callable:
        if name in WORKFLOWS:
            raise ValueError(f"workflow {name!r} already registered")
        WORKFLOWS[name] = (meta, script_fn)
        return script_fn

    return decorator


# ============================================================ run_workflow 异步入口（script runtime 实际跑这里） ============================================================
def run_workflow_async(run_id: str, name: str, args: dict,
                       resume_from_run_id: str | None = None,
                       runner: AgentRunner | None = None) -> None:
    if name not in WORKFLOWS:
        _fail_task(run_id, f"unknown workflow: {name}")
        return
    meta, script_fn = WORKFLOWS[name]
    if runner is None:
        runner = RealAgentRunner()
    journal = journal_path(run_id)
    snapshot = snapshot_path(run_id)
    output = output_path(run_id)

    handle = acquire_run_lock(run_id)
    try:
        with _tasks_lock:
            task = scheduler_tasks.get(run_id)
        if task is None:
            return
        task.status = TaskStatus.RUNNING
        emit_event(task, "task_started", workflow_name=name, args=args)
        # snapshot：初始 args + meta（resume 用）
        snapshot.write_text(json.dumps({"run_id": run_id, "workflow_name": name,
                                        "args": args, "meta": meta},
                                       ensure_ascii=False, indent=2),
                            encoding="utf-8")
        ctx = RunContext(run_id=run_id, args=args, runner=runner,
                         journal=journal, snapshot=snapshot, output=output, task=task)
        try:
            script_fn(ctx)
            if task.status == TaskStatus.RUNNING:
                ctx.final(TaskStatus.COMPLETED, output=task.output or {})
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            ctx.final(TaskStatus.FAILED, error=err)
    finally:
        release_run_lock(handle)


def _fail_task(run_id: str, error: str) -> None:
    with _tasks_lock:
        task = scheduler_tasks.get(run_id)
    if task is None:
        return
    task.status = TaskStatus.FAILED
    task.error = error
    task.finished_at = time.time()
    emit_event(task, "task_notification", status=task.status.value, error=error)


# ============================================================ WorkflowTool 适配器 ============================================================
WORKFLOW_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow",
        "description": ("Run a registered workflow. Returns initial tool_result immediately; "
                        "the final <task_notification> arrives on a later turn with status "
                        "completed/failed and the workflow output."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name (slug)."},
                "args": {"type": "object", "description": "Workflow-specific args."},
                "resume_from_run_id": {"type": "string",
                                         "description": "Optional: resume an existing run id (journal lookup)."},
            },
            "required": ["name"],
        },
    },
}


WORKFLOW_HANDLERS = {"workflow": None}  # 实现在 run_workflow_tool 填


def run_workflow_tool(name: str, args: dict | None = None,
                      resume_from_run_id: str | None = None) -> str:
    args = args or {}
    if name not in WORKFLOWS:
        return f"Error: unknown workflow: {name}. Available: {', '.join(WORKFLOWS)}"
    task = register_task(name)
    emit_event(task, "task_started", workflow_name=name, args=args)
    # 异步起 script runtime（daemon，不阻塞主循环）
    threading.Thread(
        target=run_workflow_async,
        args=(task.run_id, name, args, resume_from_run_id),
        daemon=True,
    ).start()
    return (f"[Workflow task {task.run_id} started] workflow={name}; "
            "the final result arrives as a <task_notification> on a later turn.")


WORKFLOW_HANDLERS["workflow"] = run_workflow_tool


# ============================================================ assemble_tool_pool_v2（= s14 + Workflow 工具） ============================================================
def assemble_tool_pool_v2() -> tuple[list[dict], dict[str, Callable]]:
    """s14 既有 base + task + team + cron + MCP + workflow。"""
    tools, handlers = s14.assemble_tool_pool()
    tools = list(tools) + [WORKFLOW_TOOL_SCHEMA]
    handlers = dict(handlers)
    handlers.update(WORKFLOW_HANDLERS)
    return tools, handlers


# ============================================================ agent_loop（s16 自有，借 s15 钩子） ============================================================
def agent_loop(messages: list, active_request: str) -> None:
    reactive_retries = 0
    while True:
        # 钩子 1：UserPromptSubmit
        if active_request and not active_request.startswith("("):
            s15._hook_user_prompt_submit(active_request)

        # 钩子 2：drain 3 事件源（含 s16 workflow notifications）
        for ev in _drain_events():
            messages.append(ev)
        for ev in _drain_workflow_notifications():
            messages.append(ev)

        # 钩子 3：compaction
        messages[:] = _compaction_step(messages, active_request)

        # 系统 prompt 每轮重拼（含 workflow 工具段）
        tools, handlers = assemble_tool_pool_v2()
        system_prompt = _assemble_system_prompt({"tools": tools}) + (
            "\n\nWorkflows available:\n" + _format_workflows_section() +
            "\n\nUse the workflow tool to run a registered workflow. "
            "The tool returns immediately with run_id; final <task_notification> arrives on a later turn."
        )
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})

        # LLM + reactive 限流
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=_strip_surrogates(messages), tools=tools, max_tokens=8000,
            )
            reactive_retries = 0
        except Exception as error:
            too_long = any(t in str(error).lower() for t in ("prompt_too_long", "too many tokens"))
            if too_long and reactive_retries < s14.MAX_REACTIVE_RETRIES:
                messages[:] = s14.COMPACTOR.reactive_compact(messages, active_request)
                reactive_retries += 1
                continue
            raise

        msg = resp.choices[0].message
        messages.append(_strip_surrogates(msg.model_dump(exclude_none=True)))
        if not msg.tool_calls:
            force = s15._hook_stop(messages)
            if force:
                messages.append({"role": "user", "content": str(force)})
                continue
            if msg.content:
                print(_strip_surrogates(f"\033[32m{msg.content}\033[0m"), flush=True)
            s14.extract_memories(messages)
            return

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args_dict = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": f"Error: invalid arguments JSON: {e}"})
                continue
            if (blocked := s15._hook_permission(name, args_dict)) is not None:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": str(blocked)})
                continue
            try:
                output = s14.execute_named(handlers, name, args_dict)
            except Exception as e:
                output = f"Error: {type(e).__name__}: {e}"
            s15._hook_log_tool_call(name, args_dict, output)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})


def _drain_workflow_notifications() -> list[dict]:
    """把已 completed/failed 的 workflow 任务转成 <task_notification> 注入。"""
    events = []
    with _tasks_lock:
        finished = [t for t in scheduler_tasks.values()
                    if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED) and t.finished_at is not None
                    and not getattr(t, "_notified", False)]
        for t in finished:
            t._notified = True
    for t in finished:
        status = t.status.value
        events.append({
            "role": "user",
            "content": (f"<task_notification>\n"
                        f"  <task_id>{t.run_id}</task_id>\n"
                        f"  <status>{status}</status>\n"
                        f"  <command>workflow {t.workflow_name}</command>\n"
                        f"  <summary>{_summary_for_task(t)}</summary>\n"
                        f"</task_notification>"),
        })
    return events


def _summary_for_task(t: LocalWorkflowTask) -> str:
    if t.status == TaskStatus.COMPLETED:
        return json.dumps(t.output, ensure_ascii=False)[:500]
    return f"Error: {t.error or 'unknown'}"


def _format_workflows_section() -> str:
    if not WORKFLOWS:
        return "(no workflows registered)"
    return "\n".join(f"- {name}: {meta.get('description', '')[:100]}" for name, (meta, _) in WORKFLOWS.items())


# ============================================================ CLI 主循环（接管 s15 __main__） ============================================================
def _stdin_reader(stdin_q: "queue.Queue"):
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            break
        if line == "":
            stdin_q.put(None)
            break
        stdin_q.put(line.rstrip("\n"))


if __name__ == "__main__":
    history = [{"role": "system",
                "content": _assemble_system_prompt({"tools": []}) + "\n\nWorkflows available:\n" + _format_workflows_section()}]
    print(f"\033[36m使用模型 {MODEL}（s16 workflow runtime + s15 全量），输入 q / exit / 空行退出\033[0m", flush=True)
    s14.SCHEDULER.start()
    stdin_q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(stdin_q,), daemon=True).start()
    had_teammates = False
    try:
        print("s16 >> ", end="", flush=True)
        while True:
            if s14.BUS.peek("lead"):
                inbox = s14.consume_lead_inbox()
                if inbox:
                    history[0]["content"] = _assemble_system_prompt({"tools": []}) + (
                        "\n\nWorkflows available:\n" + _format_workflows_section())
                    history.append({"role": "user", "content": s14.format_team_events(inbox)})
                    print(f"\033[33m[wake: {len(inbox)} team event(s) -> new turn]\033[0m", flush=True)
                    with s14.SCHEDULER.agent_lock:
                        agent_loop(history, "(team events)")
                    print(flush=True)
                print("s16 >> ", end="", flush=True)
                continue
            try:
                q = stdin_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if q is None or q in ("q", "exit", ""):
                break
            with s14.SCHEDULER.agent_lock:
                history[0]["content"] = _assemble_system_prompt({"tools": []}) + (
                    "\n\nWorkflows available:\n" + _format_workflows_section())
                s14.trigger_hooks("UserPromptSubmit", q)
                history.append({"role": "user", "content": q})
                agent_loop(history, q)
            print(flush=True)
            print("s16 >> ", end="", flush=True)
            if s14.active_teammates:
                had_teammates = True
            elif had_teammates and not s14.BUS.peek("lead"):
                print("[all teammates shut down]", flush=True)
                had_teammates = False
            print(flush=True)
    finally:
        s14.SCHEDULER.stop()


# ============================================================ sample workflow: review-changes ============================================================
DIMENSIONS = ["security", "performance", "style", "error-handling", "testing"]

FINDINGS_SCHEMA = SimpleJsonSchema(
    required=["findings"],
    types={"findings": list},
)
VERDICT_SCHEMA = SimpleJsonSchema(
    required=["isReal"],
    types={"isReal": bool},
)


@workflow({
    "name": "review-changes",
    "description": "Review staged changes across multiple dimensions, then verify each finding independently.",
    "phases": ["Review", "Verify"],
})
def review_changes(ctx: RunContext) -> None:
    """2 phases: Review（5 个 dimension 并行 audit）+ Verify（每条 finding 独立 verdict）。"""
    args = ctx.args or {}
    target = args.get("target", "current working tree")

    def audit(dimension: str) -> dict:
        prompt = f"Audit the staged changes ({target}) for {dimension} issues. Return a JSON object with 'findings' (a list of objects with 'text' and 'severity' fields)."
        return ctx.agent(name=f"audit-{dimension}", prompt=prompt, schema=FINDINGS_SCHEMA)

    def verify(finding: dict) -> dict:
        text = finding.get("text", "")
        prompt = f"Verify whether this finding is real (not a false positive). Return JSON with 'isReal' (bool). Finding: {text}"
        return ctx.agent(name=f"verify-{stable_key('verify', text, '')[:10]}",
                        prompt=prompt, schema=VERDICT_SCHEMA)

    ctx.phase("Review", lambda: None)
    audits = ctx.parallel(DIMENSIONS, audit)

    ctx.phase("Verify", lambda: None)
    findings = [f for audit in audits for f in audit.get("findings", [])]
    verdicts = ctx.parallel(findings, verify) if findings else []
    real_findings = [findings[i] for i, v in enumerate(verdicts) if v.get("isReal")]

    ctx.final(TaskStatus.COMPLETED, output={
        "dimensions": DIMENSIONS,
        "audits": audits,
        "findings_count": len(findings),
        "real_findings": real_findings,
    })