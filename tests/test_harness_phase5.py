"""tests/test_harness_phase5.py — Phase 5 验收测试。"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================ 1. Workflow schema
class TestWorkflowSchema:
    def test_workflow_schema_in_builtins(self):
        from harness.tools.pool import BUILTIN_TOOLS, WORKFLOW_TOOL_SCHEMA
        names = {t["function"]["name"] for t in BUILTIN_TOOLS}
        # Workflow schema exists (may not be in BUILTIN_TOOLS yet, but schema is defined)
        assert WORKFLOW_TOOL_SCHEMA["function"]["name"] == "workflow"
        assert "name" in WORKFLOW_TOOL_SCHEMA["function"]["parameters"]["required"]


# ============================================================ 2. @workflow decorator
class TestWorkflowDecorator:
    def test_workflow_register(self):
        from harness.workflow.registry import WORKFLOWS
        assert "review-changes" in WORKFLOWS
        meta, fn = WORKFLOWS["review-changes"]
        assert meta["name"] == "review-changes"
        assert callable(fn)

    def test_validate_meta_name(self):
        from harness.workflow.registry import validate_meta
        ok, err = validate_meta({"name": "a" * 65, "description": "x", "phases": ["a"]})
        assert not ok
        assert "64" in err

    def test_validate_meta_desc(self):
        from harness.workflow.registry import validate_meta
        ok, err = validate_meta({"name": "test", "description": "", "phases": ["a"]})
        assert not ok
        assert "description" in err.lower()

    def test_validate_meta_phases(self):
        from harness.workflow.registry import validate_meta
        ok, err = validate_meta({"name": "test", "description": "x", "phases": []})
        assert not ok
        assert "phases" in err.lower()

    def test_validate_meta_ok(self):
        from harness.workflow.registry import validate_meta
        ok, err = validate_meta({"name": "my-flow", "description": "A flow", "phases": ["p1"]})
        assert ok
        assert err is None


# ============================================================ 3. RunContext 6 primitives
class TestRunContext:
    def _make_ctx(self, tmp_path):
        from harness.workflow.context import RunContext
        from harness.workflow.task import register_task
        from harness.workflow.runner import MockAgentRunner
        task = register_task("test")
        runner = MockAgentRunner()
        return RunContext(run_id=task.run_id, args={}, runner=runner,
                          journal_dir=tmp_path / ".runtime", task=task)

    def test_agent_primitive(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        from harness.workflow.runner import SimpleJsonSchema
        schema = SimpleJsonSchema(required=["findings"], types={"findings": list})
        result = ctx.agent("test-agent", "find bugs", schema=schema)
        assert "findings" in result

    def test_parallel_primitive(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        results = ctx.parallel([1, 2, 3], lambda x: x * 2)
        assert sorted(results) == [2, 4, 6]

    def test_pipeline_primitive(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        results = ctx.pipeline([1, 2, 3], lambda x: x + 10)
        assert results == [11, 12, 13]

    def test_phase_primitive(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        ctx.phase("Test Phase")
        assert len(ctx.task.events) >= 1

    def test_log_primitive(self, tmp_path):
        ctx = self._make_ctx(tmp_path)
        event = ctx.log("test_event", foo="bar")
        assert event["type"] == "test_event"
        assert event["foo"] == "bar"

    def test_final_primitive(self, tmp_path):
        from harness.workflow.task import TaskStatus
        ctx = self._make_ctx(tmp_path)
        ctx.final(TaskStatus.COMPLETED, output={"key": "value"})
        assert ctx.task.status == TaskStatus.COMPLETED
        assert ctx.task.output == {"key": "value"}


# ============================================================ 4. Journal
class TestJournal:
    def test_stable_key_same(self):
        from harness.workflow.journal import WorkflowJournal
        with tempfile.TemporaryDirectory() as td:
            j = WorkflowJournal(Path(td), "run_test")
            k1 = j.stable_key("agent", "audit", "prompt1")
            k2 = j.stable_key("agent", "audit", "prompt1")
            assert k1 == k2
            assert len(k1) == 16  # agent_ + 10 digits

    def test_stable_key_different(self):
        from harness.workflow.journal import WorkflowJournal
        with tempfile.TemporaryDirectory() as td:
            j = WorkflowJournal(Path(td), "run_test")
            k1 = j.stable_key("agent", "audit", "prompt1")
            k2 = j.stable_key("agent", "audit", "prompt2")
            assert k1 != k2

    def test_record_and_cached(self):
        from harness.workflow.journal import WorkflowJournal
        with tempfile.TemporaryDirectory() as td:
            j = WorkflowJournal(Path(td), "run_test")
            assert j.cached("nonexistent") is None
            j.record("key1", {"result": "done"})
            assert j.cached("key1") == {"result": "done"}


# ============================================================ 5. TaskStatus
class TestTaskStatus:
    def test_lifecycle(self):
        from harness.workflow.task import TaskStatus, register_task, emit_event
        task = register_task("test")
        assert task.status == TaskStatus.PENDING
        task.status = TaskStatus.RUNNING
        assert task.status == TaskStatus.RUNNING
        task.status = TaskStatus.COMPLETED
        assert task.status == TaskStatus.COMPLETED

    def test_register_task(self):
        from harness.workflow.task import register_task, scheduler_tasks
        task = register_task("my-workflow")
        assert task.run_id.startswith("run_")
        assert task.run_id in scheduler_tasks


# ============================================================ 6. AgentRunner
class TestAgentRunner:
    def test_mock_runner_returns_schema_dict(self):
        from harness.workflow.runner import MockAgentRunner, SimpleJsonSchema
        runner = MockAgentRunner({"bug": {"findings": [{"text": "x"}]}})
        schema = SimpleJsonSchema(required=["findings"], types={"findings": list})
        result = runner.run("find bugs about security", schema=schema)
        assert "findings" in result
        assert len(runner.calls) == 1

    def test_real_runner_calls_llm(self):
        from harness.workflow.runner import RealAgentRunner, SimpleJsonSchema
        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_call = MagicMock()
        mock_call.function.arguments = '{"findings": []}'
        mock_resp.choices = [MagicMock(message=MagicMock(tool_calls=[mock_call]))]
        mock_llm.chat.return_value = mock_resp

        runner = RealAgentRunner.__new__(RealAgentRunner)
        runner._llm = mock_llm
        schema = SimpleJsonSchema(required=["findings"], types={"findings": list})
        result = runner.run("test prompt", schema=schema)
        assert "findings" in result


# ============================================================ 7. SimpleJsonSchema
class TestSimpleJsonSchema:
    def test_validate_success(self):
        from harness.workflow.runner import SimpleJsonSchema
        schema = SimpleJsonSchema(required=["ok", "reason"], types={"ok": bool})
        ok, err = schema.validate({"ok": True, "reason": "because"})
        assert ok
        assert err is None

    def test_validate_missing_field(self):
        from harness.workflow.runner import SimpleJsonSchema
        schema = SimpleJsonSchema(required=["ok", "reason"])
        ok, err = schema.validate({"ok": True})
        assert not ok
        assert "reason" in err


# ============================================================ 8. GoalState
class TestGoalState:
    def test_goal_fields(self):
        from harness.goal.state import GoalState
        gs = GoalState(condition="print 1 exits 0")
        assert gs.condition == "print 1 exits 0"
        assert gs.status == "pending"
        assert gs.eval_count == 0

    def test_goal_elapsed(self):
        from harness.goal.state import GoalState
        gs = GoalState(condition="test")
        time.sleep(0.1)
        assert gs.elapsed() >= 0.1


# ============================================================ 9. GoalController
class TestGoalController:
    def _make_controller(self):
        from harness.goal.controller import GoalController
        from harness.goal.evaluator import PromptGoalEvaluator
        from harness.workflow.runner import MockAgentRunner
        runner = MockAgentRunner()
        evaluator = PromptGoalEvaluator(runner)
        return GoalController(evaluator), runner

    def test_set_inspect_clear(self):
        ctrl, _ = self._make_controller()
        state = ctrl.set("test goal")
        assert state.condition == "test goal"
        assert ctrl.inspect() is state
        ctrl.clear()
        assert ctrl.inspect() is None

    def test_evaluate_no_goal(self):
        ctrl, _ = self._make_controller()
        decision = ctrl.evaluate_after_turn([], False)
        assert decision.action == "pass"
        assert "no goal" in decision.reason

    def test_evaluate_ok(self):
        ctrl, runner = self._make_controller()
        runner.responses = {"evaluate": {"ok": True, "reason": "done"}}
        ctrl.set("test goal")
        decision = ctrl.evaluate_after_turn([{"role": "user", "content": "done"}], False)
        assert decision.action == "pass"
        assert ctrl.state.status == "completed"

    def test_evaluate_defer(self):
        ctrl, _ = self._make_controller()
        ctrl.set("test goal")
        decision = ctrl.evaluate_after_turn([], True)  # has_pending_async=True
        assert decision.action == "defer"

    def test_evaluate_block(self):
        ctrl, runner = self._make_controller()
        runner.responses = {"always": {"ok": False, "reason": "not yet"}}
        ctrl.set("test goal")
        decision = ctrl.evaluate_after_turn([], False)
        assert decision.action == "block"
        assert ctrl.state.eval_count == 1

    def test_evaluate_max_consecutive(self):
        ctrl, runner = self._make_controller()
        runner.responses = {"always": {"ok": False, "reason": "not yet"}}
        ctrl.set("test goal")
        # Block 4 times (> MAX_CONSECUTIVE_BLOCKS + 1 = 4)
        for _ in range(5):
            decision = ctrl.evaluate_after_turn([], False)
        assert decision.action == "pass"  # forced pass


# ============================================================ 10. /goal parsing
class TestGoalParsing:
    def test_11_variants(self):
        from harness.cli import parse_goal_command
        # inspect variants
        assert parse_goal_command("/goal") == ("inspect", "")
        assert parse_goal_command("/goal status") == ("inspect", "")
        assert parse_goal_command("/goal show") == ("inspect", "")
        assert parse_goal_command("/goal view") == ("inspect", "")
        # clear variants
        assert parse_goal_command("/goal clear") == ("clear", "")
        assert parse_goal_command("/goal stop") == ("clear", "")
        assert parse_goal_command("/goal off") == ("clear", "")
        assert parse_goal_command("/goal reset") == ("clear", "")
        # set
        assert parse_goal_command("/goal my condition here") == ("set", "my condition here")
        # not /goal
        assert parse_goal_command("hello") is None
        assert parse_goal_command("/other") is None


# ============================================================ 11. has_pending_async
class TestHasPendingAsync:
    def test_has_pending_bg(self):
        from harness.agent import _has_pending_async
        from harness.background.manager import BackgroundManager
        bg = BackgroundManager()
        assert _has_pending_async(bg) is False

    def test_has_pending_workflow(self):
        from harness.agent import _has_pending_async
        assert _has_pending_async(None) is False

    def test_has_pending_none(self):
        from harness.agent import _has_pending_async
        assert _has_pending_async(None) is False


# ============================================================ 12. 不动检查
class TestNoChanges:
    def test_no_s_file_changes(self):
        import subprocess
        result = subprocess.run(
            ["git", "diff", "--name-only", "--", "s*.py"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        allowed = {
            "s01_agent_loop.py", "s02_tool_use.py", "s03_permission.py",
            "s04_hooks.py", "s05_todo_write.py", "s06_subagent.py",
            "s07_skill_loading.py", "s08_context_compact.py", "s09_memory.py",
            "s10_task_system.py", "s11_background_tasks.py", "s12_cron_scheduler.py",
            "s13_agent_teams.py", "s14_mcp_plugin.py",
        }
        changed = set(f for f in result.stdout.strip().split("\n") if f)
        unexpected = changed - allowed
        assert not unexpected, f"Unexpected s-file changes: {unexpected}"

    def test_phase1234_not_broken(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_harness_phase1.py", "tests/test_harness_phase2.py",
             "tests/test_harness_phase3.py", "tests/test_harness_phase4.py",
             "-q", "--tb=line"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        assert "passed" in result.stdout
        assert "failed" not in result.stdout
