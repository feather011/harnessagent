"""tests/test_tui.py — Phase 9 TUI 测试。"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================ 1. HarnessApp
class TestHarnessApp:
    def test_harness_app_creates(self):
        from harness.tui.app import HarnessApp
        from harness.config import AgentConfig
        config = AgentConfig(api_key="test", workdir=Path("/tmp"))
        app = HarnessApp(config)
        assert app.config is config
        assert app.event_bus is not None


# ============================================================ 2. SplashScreen
class TestSplashScreen:
    def test_splash_compose(self):
        from harness.tui.splash import SplashScreen
        screen = SplashScreen()
        # compose 是 generator，不直接调
        assert hasattr(screen, "compose")

    def test_splash_has_logo(self):
        from harness.tui.logo import HARNESS_LOGO
        assert "harness" in HARNESS_LOGO.lower() or ".---." in HARNESS_LOGO
        assert len(HARNESS_LOGO) > 100


# ============================================================ 3. StatusBar
class TestStatusBar:
    def test_status_bar_render(self):
        from harness.tui.widgets.status import StatusBar
        from harness.config import AgentConfig
        config = AgentConfig(api_key="test", model="mimo-v2.5", workdir=Path("/tmp"))
        bar = StatusBar(config)
        rendered = bar.render()
        assert "mimo-v2.5" in rendered
        assert "harness" in rendered

    def test_status_bar_set_state(self):
        from harness.tui.widgets.status import StatusBar
        from harness.config import AgentConfig
        config = AgentConfig(api_key="test", workdir=Path("/tmp"))
        bar = StatusBar(config)
        bar.set_state("running")
        assert bar.state == "running"


# ============================================================ 4. EventBus
class TestEventBus:
    def test_event_bus_put_get(self):
        from harness.tui.events import EventBus, TokenEvent, InputEvent
        bus = EventBus()
        bus.put_nowait(TokenEvent(delta="hello"))
        event = bus._queue.get_nowait()
        assert isinstance(event, TokenEvent)
        assert event.delta == "hello"

    def test_event_bus_clear(self):
        from harness.tui.events import EventBus, TokenEvent
        bus = EventBus()
        bus.put_nowait(TokenEvent(delta="a"))
        bus.put_nowait(TokenEvent(delta="b"))
        bus.clear()
        assert bus._queue.empty()


# ============================================================ 5. Events
class TestEvents:
    def test_token_event(self):
        from harness.tui.events import TokenEvent
        e = TokenEvent(delta="test")
        assert e.delta == "test"

    def test_tool_start_event(self):
        from harness.tui.events import ToolStartEvent
        e = ToolStartEvent(name="bash", args={"command": "ls"}, call_id="c1")
        assert e.name == "bash"
        assert e.call_id == "c1"

    def test_tool_done_event(self):
        from harness.tui.events import ToolDoneEvent
        e = ToolDoneEvent(name="bash", output="file.py", duration=0.5)
        assert e.duration == 0.5

    def test_chat_msg_event(self):
        from harness.tui.events import ChatMsgEvent
        e = ChatMsgEvent(role="user", content="hello")
        assert e.role == "user"

    def test_input_event(self):
        from harness.tui.events import InputEvent
        e = InputEvent(text="test query")
        assert e.text == "test query"


# ============================================================ 6. ChatView
class TestChatView:
    def test_chat_view_instantiates(self):
        from harness.tui.widgets.chat import ChatView
        view = ChatView()
        assert view is not None

    def test_chat_view_tool_events(self):
        from harness.tui.widgets.chat import ChatView
        from harness.tui.events import ToolStartEvent, ToolDoneEvent
        view = ChatView()
        # 验证方法存在
        assert hasattr(view, "on_tool_start")
        assert hasattr(view, "on_tool_done")


# ============================================================ 7. InputBox
class TestInputBox:
    def test_input_box_creates(self):
        from harness.tui.widgets.input_box import InputBox
        box = InputBox()
        assert box is not None

    def test_input_box_submit_action(self):
        from harness.tui.widgets.input_box import InputBox
        box = InputBox()
        assert hasattr(box, "action_submit")

    def test_command_menu_items(self):
        from harness.tui.widgets.input_box import COMMANDS
        assert len(COMMANDS) >= 5
        cmds = [c[0] for c in COMMANDS]
        assert "/help" in cmds
        assert "/goal" in cmds
        assert "/clear" in cmds

    def test_file_completion(self):
        from harness.tui.widgets.input_box import InputBox
        box = InputBox(workdir=PROJECT_ROOT)
        files = box._get_workspace_files(max_files=5)
        assert isinstance(files, list)
        assert len(files) > 0
        assert any("harness" in f for f in files)

    def test_completion_popup(self):
        from harness.tui.widgets.input_box import CompletionPopup
        popup = CompletionPopup(["/help", "/goal"], {"/help": "show help"})
        assert popup.items == ["/help", "/goal"]
        popup.move_down()
        assert popup.selected_index == 1
        popup.move_up()
        assert popup.selected_index == 0
        assert popup.get_selected() == "/help"


# ============================================================ 8. LLM stream
class TestLLMStream:
    def test_llm_stream_method_exists(self):
        from harness.llm import LLMClient
        assert hasattr(LLMClient, "stream")

    def test_llm_stream_signature(self):
        import inspect
        from harness.llm import LLMClient
        sig = inspect.signature(LLMClient.stream)
        params = list(sig.parameters.keys())
        assert "messages" in params
        assert "tools" in params


# ============================================================ 9. Agent loop callbacks
class TestAgentCallbacks:
    def test_agent_loop_accepts_callbacks(self):
        import inspect
        from harness.agent import agent_loop
        sig = inspect.signature(agent_loop)
        params = list(sig.parameters.keys())
        assert "on_token" in params
        assert "on_tool_start" in params
        assert "on_tool_done" in params


# ============================================================ 10. Styles
class TestStyles:
    def test_styles_tcss_exists(self):
        styles_path = PROJECT_ROOT / "harness" / "tui" / "styles.tcss"
        assert styles_path.exists()

    def test_styles_has_required_rules(self):
        content = (PROJECT_ROOT / "harness" / "tui" / "styles.tcss").read_text()
        assert "#status" in content
        assert "#chat" in content
        assert "#input" in content
