"""harness.tools.workflow — workflow 工具注册。"""

import threading
from pathlib import Path

from harness.tools.pool import register_tool, WORKFLOW_TOOL_SCHEMA


def init_workflow_tools(config):
    """由 cli.py 调用，注册 workflow 工具。"""
    from harness.workflow.registry import WORKFLOWS
    from harness.workflow.task import register_task, emit_event, scheduler_tasks, TaskStatus
    from harness.workflow.context import RunContext

    runtime_dir = config.workdir / ".runtime"

    def _run_workflow_async(run_id, name, args, resume_from_run_id, runner):
        """daemon thread: 执行 workflow script。"""
        from harness.workflow.registry import WORKFLOWS
        from harness.workflow.task import _tasks_lock, scheduler_tasks
        if name not in WORKFLOWS:
            return
        meta, script_fn = WORKFLOWS[name]

        with _tasks_lock:
            task = scheduler_tasks.get(run_id)
        if task is None:
            return
        task.status = TaskStatus.RUNNING
        emit_event(task, "task_started", workflow_name=name, args=args)

        ctx = RunContext(run_id=run_id, args=args, runner=runner,
                         journal_dir=runtime_dir, task=task)
        try:
            script_fn(ctx)
            if task.status == TaskStatus.RUNNING:
                ctx.final(TaskStatus.COMPLETED, output=task.output or {})
        except Exception as e:
            ctx.final(TaskStatus.FAILED, error=f"{type(e).__name__}: {e}")

    def run_workflow_tool(name: str, args: dict | None = None,
                          resume_from_run_id: str | None = None) -> str:
        args = args or {}
        if name not in WORKFLOWS:
            return f"Error: unknown workflow: {name}. Available: {', '.join(WORKFLOWS)}"
        task = register_task(name)
        emit_event(task, "task_started", workflow_name=name, args=args)

        from harness.workflow.runner import RealAgentRunner
        runner = RealAgentRunner(config)

        thread = threading.Thread(
            target=_run_workflow_async,
            args=(task.run_id, name, args, resume_from_run_id, runner),
            daemon=True,
        )
        thread.start()
        return (f"[Workflow task {task.run_id} started] workflow={name}; "
                "the final result arrives as a <task_notification> on a later turn.")

    register_tool(WORKFLOW_TOOL_SCHEMA, run_workflow_tool)
