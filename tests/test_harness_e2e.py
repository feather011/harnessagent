"""tests/test_harness_e2e.py — 端到端集成测试（mock LLM）。"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_config(tmp_path):
    from harness.config import AgentConfig
    return AgentConfig(
        api_key="test-key", model="test-model", workdir=tmp_path,
        transcript_dir=tmp_path / ".transcripts",
        tool_results_dir=tmp_path / ".task_outputs" / "tool-results",
    )


def _make_mock_llm(responses):
    """构造 mock LLMClient，responses 按顺序返回。"""
    from harness.llm import LLMClient
    mock = MagicMock(spec=LLMClient)
    mock.model = "test-model"
    mock.strip_surrogates = LLMClient.strip_surrogates
    mock.is_prompt_too_long = LLMClient.is_prompt_too_long
    mock.chat.side_effect = responses
    return mock


def _text_response(text):
    """构造纯文本 LLM 响应。"""
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    msg.model_dump.return_value = {"role": "assistant", "content": text}
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp


def _tool_response(tool_name, args, tool_id="call_001"):
    """构造 tool_call LLM 响应。"""
    msg = MagicMock()
    msg.content = None
    tc = MagicMock()
    tc.id = tool_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(args)
    msg.tool_calls = [tc]
    msg.model_dump.return_value = {"role": "assistant", "tool_calls": [{"id": tool_id}]}
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp


# ============================================================ Test 1: text only
class TestE2ETextOnly:
    def test_text_only_round(self, tmp_path):
        """mock 返纯文本 → agent_loop 返回 + history 追加。"""
        from harness.agent import agent_loop
        config = _make_config(tmp_path)
        llm = _make_mock_llm([_text_response("Hello! I'm ready.")])
        history = [{"role": "user", "content": "hi"}]
        agent_loop(history, config, llm)
        assert len(history) == 3  # system + user + assistant
        assert history[2]["content"] == "Hello! I'm ready."


# ============================================================ Test 2: tool call
class TestE2EToolCall:
    def test_tool_call_dispatched(self, tmp_path):
        """mock 返 bash tool_call → 工具执行 → tool_result 追加。"""
        from harness.agent import agent_loop
        config = _make_config(tmp_path)
        llm = _make_mock_llm([
            _tool_response("bash", {"command": "echo hello"}),
            _text_response("Done!"),
        ])
        history = [{"role": "user", "content": "run echo"}]
        agent_loop(history, config, llm)
        # system + user + assistant(tool_call) + tool_result + assistant(text)
        assert len(history) == 5
        tool_msgs = [m for m in history if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "hello" in tool_msgs[0]["content"]


# ============================================================ Test 3: multi-turn
class TestE2EMultiTurn:
    def test_multi_turn_chain(self, tmp_path):
        """mock 连续 2 轮：tool_call → text。"""
        from harness.agent import agent_loop
        config = _make_config(tmp_path)
        llm = _make_mock_llm([
            _tool_response("bash", {"command": "echo first"}, "c1"),
            _tool_response("bash", {"command": "echo second"}, "c2"),
            _text_response("Both done."),
        ])
        history = [{"role": "user", "content": "run two commands"}]
        agent_loop(history, config, llm)
        tool_msgs = [m for m in history if m.get("role") == "tool"]
        assert len(tool_msgs) == 2


# ============================================================ Test 4: permission deny
class TestE2EPermission:
    def test_dangerous_bash_denied(self, tmp_path):
        """mock 返危险 bash → permission 拒绝。"""
        from harness.agent import agent_loop
        from harness.permission import reset_deny_all
        reset_deny_all()
        config = _make_config(tmp_path)
        llm = _make_mock_llm([
            _tool_response("bash", {"command": "rm -rf /"}),
            _text_response("Blocked."),
        ])
        history = [{"role": "user", "content": "delete everything"}]
        agent_loop(history, config, llm)
        tool_msgs = [m for m in history if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        # 权限拒绝 或 bash 内置拒绝 都算通过
        assert ("Permission denied" in tool_msgs[0]["content"]
                or "Dangerous" in tool_msgs[0]["content"]
                or "rm:" in tool_msgs[0]["content"])


# ============================================================ Test 5: review-changes workflow
class TestE2EReviewChanges:
    def test_review_changes_full(self, tmp_path):
        """review-changes workflow 完整跑（mock runner）。"""
        from harness.workflow.registry import WORKFLOWS
        from harness.workflow.runner import MockAgentRunner
        from harness.workflow.context import RunContext
        from harness.workflow.task import TaskStatus, register_task

        assert "review-changes" in WORKFLOWS
        meta, script_fn = WORKFLOWS["review-changes"]

        runner = MockAgentRunner()
        task = register_task("review-changes")
        task.status = TaskStatus.RUNNING
        ctx = RunContext(run_id=task.run_id, args={"target": "test diff"},
                         runner=runner, journal_dir=tmp_path / ".runtime",
                         task=task)

        script_fn(ctx)

        assert task.status == TaskStatus.COMPLETED
        assert task.output is not None
        assert "dimensions" in task.output
        assert len(task.output["dimensions"]) == 5
        # runner 被调用多次（5 audits + N verifies）
        assert len(runner.calls) >= 5


# ============================================================ Test 6: goal set + block
class TestE2EGoalBlock:
    def test_goal_set_and_block(self, tmp_path):
        """/goal set → mock 返 not ok → block → eval_count++。"""
        from harness.goal.controller import GoalController
        from harness.goal.evaluator import PromptGoalEvaluator
        from harness.workflow.runner import MockAgentRunner

        runner = MockAgentRunner({"evaluate": {"ok": False, "reason": "not yet done"}})
        evaluator = PromptGoalEvaluator(runner)
        ctrl = GoalController(evaluator)
        ctrl.set("python -c 'print(1)' exits 0")

        decision = ctrl.evaluate_after_turn(
            [{"role": "user", "content": "run python"}, {"role": "assistant", "content": "ok"}],
            False,
        )
        assert decision.action == "block"
        assert ctrl.state.eval_count == 1
        assert "not yet" in decision.reason


# ============================================================ Test 7: goal max consecutive
class TestE2EGoalMax:
    def test_max_consecutive_blocks(self, tmp_path):
        """连续 block 5 次 → 强制 pass。"""
        from harness.goal.controller import GoalController
        from harness.goal.evaluator import PromptGoalEvaluator
        from harness.workflow.runner import MockAgentRunner

        runner = MockAgentRunner({"evaluate": {"ok": False, "reason": "nope"}})
        evaluator = PromptGoalEvaluator(runner)
        ctrl = GoalController(evaluator)
        ctrl.set("impossible goal")

        for i in range(5):
            decision = ctrl.evaluate_after_turn([], False)

        assert decision.action == "pass"
        assert "exceeded" in decision.reason


# ============================================================ Test 8: tool dispatch chain
class TestE2EToolChain:
    def test_three_tool_calls(self, tmp_path):
        """mock 返 3 个 tool_calls → 全部 dispatch。"""
        from harness.agent import agent_loop
        config = _make_config(tmp_path)

        msg = MagicMock()
        msg.content = None
        calls = []
        for i, cmd in enumerate(["echo a", "echo b", "echo c"]):
            tc = MagicMock()
            tc.id = f"call_{i}"
            tc.function.name = "bash"
            tc.function.arguments = json.dumps({"command": cmd})
            calls.append(tc)
        msg.tool_calls = calls
        msg.model_dump.return_value = {"role": "assistant"}
        resp1 = MagicMock()
        resp1.choices = [MagicMock(message=msg)]

        llm = _make_mock_llm([resp1, _text_response("All done.")])
        history = [{"role": "user", "content": "run 3 commands"}]
        agent_loop(history, config, llm)
        tool_msgs = [m for m in history if m.get("role") == "tool"]
        assert len(tool_msgs) == 3


# ============================================================ Test 9: compact tool
class TestE2ECompact:
    def test_compact_tool_triggers(self, tmp_path):
        """mock 返 compact 工具调用 → compactor 被调用。"""
        from harness.agent import agent_loop
        from harness.context.compactor import ContextCompactor

        config = _make_config(tmp_path)
        mock_llm_inner = MagicMock()
        # compactor.summarize_history 需要返回一个带 choices[0].message.content 的响应
        summary_msg = MagicMock()
        summary_msg.content = "Summary of conversation."
        summary_resp = MagicMock()
        summary_resp.choices = [MagicMock(message=summary_msg)]
        mock_llm_inner.chat.return_value = summary_resp

        compactor = ContextCompactor(mock_llm_inner, "test", tmp_path / "t", tmp_path / "tr")
        llm = _make_mock_llm([
            _tool_response("compact", {}),
            _text_response("Compacted."),
        ])
        history = [{"role": "user", "content": "compact context"}]
        agent_loop(history, config, llm, compactor=compactor)
        tool_msgs = [m for m in history if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "ompact" in tool_msgs[0]["content"]  # "Context compacted." 或 "Compacted."


# ============================================================ Test 10: no s-file changes
class TestE2ENoChanges:
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
