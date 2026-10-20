"""tests/test_noninteractive_permission.py — 非交互模式的权限闸。

背景：`harness/hooks/builtin.py` 的 PreToolUse 权限 hook 只在 CLI 路径
（import builtin）里注册；`python -m harness.noninteractive` 的导入链
（agent → tools.pool → tools.base）从不 import 它，于是
`check_permission` 整个从未执行 —— 包括对 bash 唯一生效的
第 1 道闸 deny_list。`safe_path()` 只约束文件类工具，约束不了 shell 命令。

本文件锁定 `_install_noninteractive_hooks` 的行为：
- PreToolDispatch（解析前）：计数 + 限流，**被拒的调用也计数**
- PreToolUse（解析后）：check_permission(prompt_user=False)
  → 需要人工批准的操作直接拒绝，**绝不调用 input()**
"""

import builtins
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

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
    from harness.llm import LLMClient
    mock = MagicMock(spec=LLMClient)
    mock.model = "test-model"
    mock.strip_surrogates = LLMClient.strip_surrogates
    mock.is_prompt_too_long = LLMClient.is_prompt_too_long
    mock.chat.side_effect = responses
    return mock


def _text_response(text):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    msg.model_dump.return_value = {"role": "assistant", "content": text}
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    return resp


def _tool_response(tool_name, args, tool_id="call_001"):
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


@pytest.fixture(autouse=True)
def _isolated_workdir(tmp_path):
    from harness.tools.base import set_workdir
    set_workdir(tmp_path)
    yield
    set_workdir(Path.cwd())


@pytest.fixture
def install_hooks():
    """返回 _install_noninteractive_hooks 的包装；用后恢复全局 HOOKS。"""
    from harness.hooks import registry
    from harness.noninteractive import _install_noninteractive_hooks

    saved = {event: list(cbs) for event, cbs in registry.HOOKS.items()}
    registry.HOOKS.clear()
    try:
        def _install(max_calls: int = 50) -> dict:
            counters = {"total_calls": 0, "seq": 0}
            _install_noninteractive_hooks(counters, max_calls)
            return counters
        yield _install
    finally:
        registry.HOOKS.clear()
        registry.HOOKS.update(saved)


@pytest.fixture
def bash_spy():
    """替换真实 bash handler：记录命令，永不真的执行。"""
    from harness.tools import pool

    calls: list[str] = []
    original = pool.BUILTIN_HANDLERS["bash"]

    def spy(command: str, run_in_background: bool = False) -> str:
        calls.append(command)
        return "[exit 0] spy-ran"

    pool.BUILTIN_HANDLERS["bash"] = spy
    try:
        yield calls
    finally:
        pool.BUILTIN_HANDLERS["bash"] = original


@pytest.fixture
def input_spy(monkeypatch):
    """input() 一旦被调用立即失败：非交互模式没有 stdin 可供审批。"""
    calls: list[str] = []

    def fake_input(prompt: str = "") -> str:
        calls.append(prompt)
        raise AssertionError("非交互模式不得调用 input()")

    monkeypatch.setattr(builtins, "input", fake_input)
    return calls


def _tool_messages(history: list) -> list[dict]:
    return [m for m in history if m.get("role") == "tool"]


# ---------------------------------------------------------------------------
# 1. 第 1 道闸：deny_list（bash 硬编码黑名单）
# ---------------------------------------------------------------------------

class TestDenyList:
    def test_黑名单命令被拒_handler_零执行(self, tmp_path, install_hooks, bash_spy, input_spy):
        """`rm -rf /` 命中 deny_list：handler 一次都不该跑。"""
        from harness.agent import agent_loop
        counters = install_hooks()
        llm = _make_mock_llm([
            _tool_response("bash", {"command": "rm -rf /"}),
            _text_response("该命令已被安全策略拒绝"),
        ])
        history = [{"role": "user", "content": "清理磁盘"}]

        agent_loop(history, _make_config(tmp_path), llm)

        assert bash_spy == [], "被拒绝的工具调用不得触达 handler"
        assert input_spy == [], "deny_list 是无条件闸，不应走到 ask_user"

        tools = _tool_messages(history)
        assert len(tools) == 1
        assert "Permission denied" in tools[0]["content"]
        # 解析前计数仍生效：被拒的调用照样消耗额度
        assert counters["total_calls"] == 1

    @pytest.mark.parametrize("command", ["sudo ls", "shutdown now", "dd if=/dev/zero of=x"])
    def test_黑名单各条目一律拦截(self, tmp_path, install_hooks, bash_spy, command):
        from harness.agent import agent_loop
        install_hooks()
        llm = _make_mock_llm([
            _tool_response("bash", {"command": command}),
            _text_response("ok"),
        ])
        history = [{"role": "user", "content": "run"}]

        agent_loop(history, _make_config(tmp_path), llm)

        assert bash_spy == []
        assert "Permission denied" in _tool_messages(history)[0]["content"]


# ---------------------------------------------------------------------------
# 2. 第 2 道闸：rules —— 需要人工批准的操作，非交互下直接拒绝
# ---------------------------------------------------------------------------

class TestRulesWithoutPrompt:
    def test_需人工批准的命令直接拒绝且不询问(self, tmp_path, install_hooks, bash_spy, input_spy):
        """`pip install` 命中 rules；prompt_user=False → 直接拒绝，不问 input()。"""
        from harness.agent import agent_loop
        install_hooks()
        llm = _make_mock_llm([
            _tool_response("bash", {"command": "pip install requests"}),
            _text_response("无法安装新包"),
        ])
        history = [{"role": "user", "content": "装个依赖"}]

        agent_loop(history, _make_config(tmp_path), llm)

        assert bash_spy == []
        assert input_spy == [], "非交互模式不得请求人工审批"

        tools = _tool_messages(history)
        assert len(tools) == 1
        assert tools[0]["content"] == "Permission required: Installing new package"

    def test_越界文件访问被拒(self, tmp_path, install_hooks, input_spy):
        """rules 的越界检查在非交互模式同样生效（read_file 逃出工作区）。"""
        from harness.agent import agent_loop
        install_hooks()
        llm = _make_mock_llm([
            _tool_response("read_file", {"path": "../outside.txt"}),
            _text_response("越界拒绝"),
        ])
        history = [{"role": "user", "content": "读外部文件"}]

        agent_loop(history, _make_config(tmp_path), llm)

        assert input_spy == []
        assert _tool_messages(history)[0]["content"] == (
            "Permission required: Access outside workspace"
        )

    def test_敏感文件写入被拒(self, tmp_path, install_hooks, input_spy):
        from harness.agent import agent_loop
        install_hooks()
        llm = _make_mock_llm([
            _tool_response("write_file", {"path": ".env", "content": "SECRET=1"}),
            _text_response("已拒绝"),
        ])
        history = [{"role": "user", "content": "写配置"}]

        agent_loop(history, _make_config(tmp_path), llm)

        assert not (tmp_path / ".env").exists()
        assert input_spy == []
        assert _tool_messages(history)[0]["content"] == (
            "Permission required: Writing sensitive file"
        )


# ---------------------------------------------------------------------------
# 3. 正向：不在闸内的命令照常执行
# ---------------------------------------------------------------------------

class TestAllowedPathStillWorks:
    def test_普通命令照常执行(self, tmp_path, install_hooks, bash_spy):
        from harness.agent import agent_loop
        counters = install_hooks()
        llm = _make_mock_llm([
            _tool_response("bash", {"command": "echo hello"}),
            _text_response("done"),
        ])
        history = [{"role": "user", "content": "打个招呼"}]

        agent_loop(history, _make_config(tmp_path), llm)

        assert bash_spy == ["echo hello"]
        assert _tool_messages(history)[0]["content"] == "[exit 0] spy-ran"
        assert counters["total_calls"] == 1


# ---------------------------------------------------------------------------
# 4. 回归：解析前计数与限流未被 PreToolUse 影响
# ---------------------------------------------------------------------------

class TestBudgetIntact:
    def test_被拒调用同样消耗额度并在超限时抛错(self, tmp_path, install_hooks):
        """max_calls=1，第二次调用即使被权限拒绝也必须先撞上限。"""
        from harness.agent import agent_loop
        from harness.noninteractive import ToolCallLimitExceeded
        counters = install_hooks(max_calls=1)
        llm = _make_mock_llm([
            _tool_response("bash", {"command": "rm -rf /"}, tool_id="call_1"),
            _tool_response("bash", {"command": "rm -rf /"}, tool_id="call_2"),
        ])
        history = [{"role": "user", "content": "反复尝试"}]

        with pytest.raises(ToolCallLimitExceeded):
            agent_loop(history, _make_config(tmp_path), llm)

        assert counters["total_calls"] == 2
