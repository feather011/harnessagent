"""harness.tui.events — TUIEvent 事件类型 + EventBus + Textual Messages。"""

import asyncio
from dataclasses import dataclass, field
from textual.message import Message


@dataclass
class TUIEvent:
    pass


@dataclass
class TokenEvent(TUIEvent):
    delta: str = ""


@dataclass
class ChatMsgEvent(TUIEvent):
    role: str = ""       # user / assistant / system
    content: str = ""


@dataclass
class ToolStartEvent(TUIEvent):
    name: str = ""
    args: dict = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ToolDoneEvent(TUIEvent):
    name: str = ""
    args: dict = field(default_factory=dict)
    output: str = ""
    duration: float = 0.0
    call_id: str = ""


@dataclass
class SystemEvent(TUIEvent):
    content: str = ""


@dataclass
class StatusEvent(TUIEvent):
    state: str = "idle"  # idle / running / waiting


class InputEvent(Message):
    """用户输入事件（Textual Message 子类，用于 post_message）。"""
    def __init__(self, text: str = ""):
        super().__init__()
        self.text = text


class EventBus:
    """异步事件总线：agent_loop 通过 put_nowait 推事件，TUI 通过 get 消费。"""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()

    async def put(self, event: TUIEvent):
        await self._queue.put(event)

    def put_nowait(self, event: TUIEvent):
        self._queue.put_nowait(event)

    async def get(self) -> TUIEvent:
        return await self._queue.get()

    def clear(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
