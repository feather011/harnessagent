"""harness.tui.widgets.chat — ChatView（聊天历史 + streaming）。"""

from textual.widgets import RichLog
from harness.tui.events import TokenEvent, ChatMsgEvent, ToolStartEvent, ToolDoneEvent


class ChatView(RichLog):
    """聊天消息 + 工具调用 + streaming 显示。"""

    def __init__(self):
        super().__init__(markup=True, highlight=True, wrap=True, id="chat")
        self._current_assistant = ""

    async def on_token(self, event: TokenEvent):
        """Streaming token 追加到当前 assistant 消息。"""
        self._current_assistant += event.delta
        # 用 markup 更新最后一条消息（简化版：每次清重写）
        if self._current_assistant:
            # RichLog 不支持原地更新，用 append 模式
            pass  # 在 on_tool_done / on_chat_msg 时统一刷新

    async def on_chat_msg(self, event: ChatMsgEvent):
        """新消息到来。"""
        if event.role == "user":
            self.write(f"[bold cyan]You:[/bold cyan] {event.content}")
        elif event.role == "assistant":
            if event.content:
                self.write(f"[bold green]Agent:[/bold green] {event.content}")
            self._current_assistant = ""
        elif event.role == "system":
            self.write(f"[dim]{event.content}[/dim]")

    async def on_tool_start(self, event: ToolStartEvent):
        """工具开始执行。"""
        args_str = str(event.args)[:60]
        self.write(f"  [dim]⚙ {event.name}({args_str})[/dim]")

    async def on_tool_done(self, event: ToolDoneEvent):
        """工具执行完成。"""
        output_preview = event.output[:100].replace("\n", " ")
        if "Error" in event.output:
            self.write(f"  [red]✗ {event.name} → {output_preview}[/red] [dim]({event.duration:.1f}s)[/dim]")
        else:
            self.write(f"  [green]✓ {event.name}[/green] [dim]({event.duration:.1f}s)[/dim]")
            if output_preview and len(output_preview) > 5:
                self.write(f"    [dim]{output_preview}[/dim]")

    async def flush_assistant(self, content: str):
        """一次性写入完整 assistant 回复（非 streaming 模式）。"""
        if content:
            self.write(f"[bold green]Agent:[/bold green] {content}")
        self._current_assistant = ""
