"""tests/test_harness_phase1.py — Phase 1 验收测试。"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================ 1. config
class TestConfig:
    def test_loads_from_env(self):
        """验证 load_config 正确读取环境变量（patch load_dotenv 防止 .env 覆盖）。"""
        from importlib import reload
        import harness.config
        import dotenv
        saved_fn = dotenv.load_dotenv
        try:
            dotenv.load_dotenv = lambda *a, **kw: None  # no-op during reload
            os.environ["MIMO_API_KEY"] = "test-key-123"
            os.environ["MIMO_BASE_URL"] = "https://t.com/v1"
            os.environ["MIMO_MODEL"] = "tm"
            reload(harness.config)
            cfg = harness.config.load_config()
            assert cfg.api_key == "test-key-123"
            assert cfg.base_url == "https://t.com/v1"
            assert cfg.model == "tm"
        finally:
            dotenv.load_dotenv = saved_fn
            reload(harness.config)

    def test_missing_key_raises(self):
        from importlib import reload
        import harness.config
        import harness.errors as err_mod
        import dotenv
        saved_fn = dotenv.load_dotenv
        saved_key = os.environ.get("MIMO_API_KEY")
        try:
            dotenv.load_dotenv = lambda *a, **kw: None
            os.environ.pop("MIMO_API_KEY", None)
            reload(harness.config)
            with pytest.raises(err_mod.HarnessError, match="MIMO_API_KEY"):
                harness.config.load_config()
        finally:
            dotenv.load_dotenv = saved_fn
            if saved_key is not None:
                os.environ["MIMO_API_KEY"] = saved_key
            reload(harness.config)


# ============================================================ 2. tools
class TestBuiltinTools:
    def test_5_schemas(self):
        from harness.tools.pool import BUILTIN_TOOLS
        assert len(BUILTIN_TOOLS) == 5
        names = {t["function"]["name"] for t in BUILTIN_TOOLS}
        assert names == {"bash", "read_file", "write_file", "edit_file", "glob"}

    def test_all_have_required_fields(self):
        from harness.tools.pool import BUILTIN_TOOLS
        for tool in BUILTIN_TOOLS:
            assert "type" in tool
            assert "function" in tool
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            assert "required" in func["parameters"]

    def test_5_handlers(self):
        from harness.tools.pool import BUILTIN_HANDLERS
        assert len(BUILTIN_HANDLERS) == 5
        for name in ["bash", "read_file", "write_file", "edit_file", "glob"]:
            assert name in BUILTIN_HANDLERS
            assert callable(BUILTIN_HANDLERS[name])


# ============================================================ 3. hooks
class TestHooks:
    def test_5_events_registered(self):
        from harness.hooks.registry import HOOKS
        # import builtin to ensure registration
        from harness.hooks import builtin  # noqa: F401
        assert "UserPromptSubmit" in HOOKS
        assert "PreToolUse" in HOOKS
        assert "PostToolUse" in HOOKS
        assert "Stop" in HOOKS
        # PreToolUse has 2 hooks (log + permission)
        assert len(HOOKS["PreToolUse"]) >= 2

    def test_register_and_trigger(self):
        from harness.hooks.registry import HOOKS, register_hook, trigger_hooks
        called = []

        def test_cb(x):
            called.append(x)
            return "ok"

        register_hook("_test_event", test_cb)
        result = trigger_hooks("_test_event", 42)
        assert result == "ok"
        assert called == [42]
        # cleanup
        HOOKS.pop("_test_event", None)


# ============================================================ 4. permission
class TestPermission:
    def test_deny_list_blocks(self):
        from harness.permission.deny import check_deny_list
        assert check_deny_list("rm -rf /") is not None
        assert check_deny_list("sudo reboot") is not None
        assert check_deny_list("echo hello") is None
        assert check_deny_list("ls -la") is None

    def test_rules_out_of_workspace(self):
        from harness.permission.rules import check_rules
        from pathlib import Path
        workdir = Path("/workspace")
        result = check_rules("write_file", {"path": "/etc/passwd"}, workdir)
        assert result is not None
        assert "outside" in result.lower() or "workspace" in result.lower()

    def test_rules_destructive_bash(self):
        from harness.permission.rules import check_rules
        from pathlib import Path
        workdir = Path("/workspace")
        result = check_rules("bash", {"command": "rm important_file"}, workdir)
        assert result is not None

    def test_rules_safe_passes(self):
        from harness.permission.rules import check_rules
        from pathlib import Path
        workdir = Path("/workspace")
        result = check_rules("bash", {"command": "echo hello"}, workdir)
        assert result is None

    def test_permission_3_gates(self):
        """deny_list → rules → ask_user 的流程。"""
        from harness.permission import check_permission
        from harness.permission import reset_deny_all
        from pathlib import Path
        reset_deny_all()
        workdir = Path("/workspace")
        # Gate 1: deny list
        result = check_permission("bash", {"command": "rm -rf /"}, workdir, prompt_user=False)
        assert result is not None
        assert "dangerous" in result.lower()
        # Gate 2: rules (no prompt)
        result = check_permission("write_file", {"path": "/etc/passwd"}, workdir, prompt_user=False)
        assert result is not None
        # Safe command passes
        result = check_permission("bash", {"command": "echo hi"}, workdir, prompt_user=False)
        assert result is None


# ============================================================ 5. execute_tool
class TestExecuteTool:
    def test_success(self):
        from harness.tools.pool import BUILTIN_HANDLERS, execute_tool
        output = execute_tool(BUILTIN_HANDLERS, "bash", {"command": "echo hello"})
        assert "hello" in output

    def test_unknown_tool(self):
        from harness.tools.pool import execute_tool
        output = execute_tool({}, "nonexistent", {})
        assert "Error" in output
        assert "unknown tool" in output

    def test_exception_to_string(self):
        from harness.tools.pool import execute_tool
        def bad_handler(**kwargs):
            raise ValueError("something broke")
        output = execute_tool({"bad": bad_handler}, "bad", {})
        assert "Error" in output
        assert "ValueError" in output
        assert "something broke" in output


# ============================================================ 6. LLMClient
class TestLLMClient:
    def test_strip_surrogates(self):
        from harness.llm import LLMClient
        result = LLMClient.strip_surrogates("hello\udcbfworld")
        assert result == "helloworld"
        # dict
        result = LLMClient.strip_surrogates({"a": "x\udcbfy", "b": [1, "z\udcff"]})
        assert result == {"a": "xy", "b": [1, "z"]}
        # clean string passes through
        assert LLMClient.strip_surrogates("normal") == "normal"

    def test_is_prompt_too_long(self):
        from harness.llm import LLMClient
        assert LLMClient.is_prompt_too_long(Exception("prompt_too_long error"))
        assert LLMClient.is_prompt_too_long(Exception("too many tokens"))
        assert not LLMClient.is_prompt_too_long(Exception("network error"))


# ============================================================ 7. agent_loop (mock LLM)
class TestAgentLoop:
    def _make_config(self):
        from harness.config import AgentConfig
        return AgentConfig(api_key="test", base_url="https://test.com/v1", model="test-model")

    def _make_mock_llm(self, responses):
        """responses: list of mock response objects, consumed in order."""
        from harness.llm import LLMClient
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.model = "test-model"
        mock_llm.strip_surrogates = LLMClient.strip_surrogates
        mock_llm.is_prompt_too_long = LLMClient.is_prompt_too_long
        mock_llm.chat.side_effect = responses
        return mock_llm

    def test_text_only_response(self):
        """Mock LLM 返回纯文本（无 tool_calls）→ 打印并返回。"""
        from harness.agent import agent_loop
        from harness.tools.base import set_workdir
        set_workdir(Path.cwd())

        config = self._make_config()
        mock_msg = MagicMock()
        mock_msg.content = "Hello! I'm a test agent."
        mock_msg.tool_calls = None
        mock_msg.model_dump.return_value = {"role": "assistant", "content": "Hello! I'm a test agent."}

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=mock_msg)]

        mock_llm = self._make_mock_llm([mock_resp])
        history = [{"role": "user", "content": "hi"}]

        agent_loop(history, config, mock_llm)
        # 应该有 system + user + assistant 3 条消息
        assert len(history) == 3
        assert history[2]["role"] == "assistant"

    def test_tool_call_then_text(self):
        """Mock LLM 先调用 bash 工具，再返回文本。"""
        from harness.agent import agent_loop
        from harness.tools.base import set_workdir
        set_workdir(Path.cwd())

        config = self._make_config()

        # 第一轮：tool_call
        mock_tool_call = MagicMock()
        mock_tool_call.id = "call_1"
        mock_tool_call.function.name = "bash"
        mock_tool_call.function.arguments = json.dumps({"command": "echo test_output"})

        mock_msg1 = MagicMock()
        mock_msg1.content = None
        mock_msg1.tool_calls = [mock_tool_call]
        mock_msg1.model_dump.return_value = {"role": "assistant", "tool_calls": [{"id": "call_1"}]}

        mock_resp1 = MagicMock()
        mock_resp1.choices = [MagicMock(message=mock_msg1)]

        # 第二轮：纯文本
        mock_msg2 = MagicMock()
        mock_msg2.content = "Done! The output was test_output."
        mock_msg2.tool_calls = None
        mock_msg2.model_dump.return_value = {"role": "assistant", "content": "Done!"}

        mock_resp2 = MagicMock()
        mock_resp2.choices = [MagicMock(message=mock_msg2)]

        mock_llm = self._make_mock_llm([mock_resp1, mock_resp2])
        history = [{"role": "user", "content": "run echo"}]

        agent_loop(history, config, mock_llm)
        # system + user + assistant(tool_call) + tool_result + assistant(text) = 5
        assert len(history) == 5
        tool_result = history[3]
        assert tool_result["role"] == "tool"
        assert "test_output" in tool_result["content"]

    def test_permission_blocks_tool(self):
        """权限拒绝的工具调用不会执行，直接返回拒绝消息。"""
        from harness.agent import agent_loop
        from harness.tools.base import set_workdir
        set_workdir(Path.cwd())

        config = self._make_config()

        mock_tool_call = MagicMock()
        mock_tool_call.id = "call_danger"
        mock_tool_call.function.name = "bash"
        mock_tool_call.function.arguments = json.dumps({"command": "rm -rf /"})

        mock_msg1 = MagicMock()
        mock_msg1.content = None
        mock_msg1.tool_calls = [mock_tool_call]
        mock_msg1.model_dump.return_value = {"role": "assistant", "tool_calls": [{"id": "call_danger"}]}

        mock_resp1 = MagicMock()
        mock_resp1.choices = [MagicMock(message=mock_msg1)]

        mock_msg2 = MagicMock()
        mock_msg2.content = "OK, blocked."
        mock_msg2.tool_calls = None
        mock_msg2.model_dump.return_value = {"role": "assistant", "content": "OK, blocked."}

        mock_resp2 = MagicMock()
        mock_resp2.choices = [MagicMock(message=mock_msg2)]

        mock_llm = self._make_mock_llm([mock_resp1, mock_resp2])
        history = [{"role": "user", "content": "delete everything"}]

        agent_loop(history, config, mock_llm)
        # tool_result should contain permission denied
        tool_results = [m for m in history if m.get("role") == "tool"]
        assert len(tool_results) == 1
        assert "Permission denied" in tool_results[0]["content"]
