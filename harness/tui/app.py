"""harness.tui.app — HarnessApp（Textual App 主类）。"""

import asyncio
from textual.app import App, ComposeResult
from textual.screen import Screen

from harness.tui.splash import SplashScreen
from harness.tui.widgets.chat import ChatView
from harness.tui.widgets.status import StatusBar
from harness.tui.widgets.input_box import InputBox
from harness.tui.events import (
    EventBus, InputEvent, TokenEvent, ChatMsgEvent,
    ToolStartEvent, ToolDoneEvent, StatusEvent,
)


class MainScreen(Screen):
    """主界面：StatusBar + ChatView + InputBox。"""

    def __init__(self, config):
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield StatusBar(self.config)
        yield ChatView()
        yield InputBox()


class HarnessApp(App):
    """harness TUI 主应用。"""

    CSS_PATH = "styles.tcss"
    TITLE = "harness"
    BINDINGS = [
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+l", "clear_chat", "Clear"),
    ]

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.event_bus = EventBus()
        self.history: list[dict] = []
        self._agent_task = None
        self._chat_view = None
        self._status_bar = None

    def on_input_event(self, event: InputEvent):
        """接收 InputBox 的 Textual Message，转发到 EventBus。"""
        self.event_bus.put_nowait(InputEvent(text=event.text))

    async def on_mount(self):
        """安装 screens，启动 splash → 0.8s 后切 main。"""
        self.install_screen(SplashScreen(), name="splash")
        self.install_screen(MainScreen(self.config), name="main")
        await self.push_screen("splash")
        # splash 0.8s 自动 pop → 我们在 0.9s push main
        self.set_timer(0.9, self._show_main)

    def _show_main(self):
        """显示主界面 + 启动 agent 循环。"""
        try:
            self.push_screen("main")
        except Exception:
            return  # splash 已经被替换了
        self._chat_view = self.app.screen.query_one(ChatView)
        self._status_bar = self.app.screen.query_one(StatusBar)
        self._chat_view.write("[bold cyan]harness agent ready. Type your message below.[/bold cyan]")
        self._agent_task = asyncio.create_task(self._agent_loop_tui())

    async def _agent_loop_tui(self):
        """TUI agent 循环：从 event_bus 读用户输入，跑 agent_loop，推事件回 bus。"""
        from harness.agent import agent_loop
        from harness.llm import LLMClient
        from harness.config import load_config

        llm = LLMClient(self.config)
        system = ("你是 harness agent，基于 MiMo mimo-v2.5。直接干活，不要解释。"
                  "工作目录: " + str(self.config.workdir))
        self.history = [{"role": "system", "content": system}]

        # Phase 2-5 组件初始化（简化版）
        compactor = None
        memory_store = None
        try:
            from harness.context.compactor import ContextCompactor
            compactor = ContextCompactor(llm, self.config.model,
                                         self.config.transcript_dir,
                                         self.config.tool_results_dir)
        except Exception:
            pass

        while True:
            try:
                event = await self.event_bus.get()
            except Exception:
                break

            if isinstance(event, InputEvent) and event.text:
                text = event.text
                self._chat_view.write(f"[bold cyan]You:[/bold cyan] {text}")
                self.history.append({"role": "user", "content": text})
                self._status_bar.set_state("running")

                try:
                    chat = self._chat_view
                    def on_token(t):
                        pass  # streaming tokens 不逐个显示，等 tool/result 一次性展示
                    def on_tool_start(n, a, i):
                        args_str = str(a)[:60]
                        chat.write(f"  [dim]⚙ {n}({args_str})[/dim]")
                    def on_tool_done(n, a, o, d, i):
                        preview = o[:100].replace("\n", " ")
                        if "Error" in o:
                            chat.write(f"  [red]✗ {n} → {preview}[/red] [dim]({d:.1f}s)[/dim]")
                        else:
                            chat.write(f"  [green]✓ {n}[/green] [dim]({d:.1f}s)[/dim]")
                            if preview and len(preview) > 5:
                                chat.write(f"    [dim]{preview}[/dim]")

                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: agent_loop(
                            self.history, self.config, llm,
                            compactor=compactor,
                            on_token=on_token,
                            on_tool_start=on_tool_start,
                            on_tool_done=on_tool_done,
                        )
                    )
                    # agent_loop 结束后，提取最后一条 assistant 消息
                    if self.history and self.history[-1].get("role") == "assistant":
                        content = self.history[-1].get("content", "")
                        if content:
                            self._chat_view.write(f"[bold green]Agent:[/bold green] {content}")
                except Exception as e:
                    self._chat_view.write(f"[red]Error: {type(e).__name__}: {e}[/red]")

                self._status_bar.set_state("idle")

    def action_quit(self):
        """Ctrl+D 退出。"""
        if self._agent_task:
            self._agent_task.cancel()
        self.exit()

    def action_clear_chat(self):
        """Ctrl+L 清屏。"""
        if self._chat_view:
            self._chat_view.clear()
