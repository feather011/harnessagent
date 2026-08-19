"""harness.tui.widgets.input_box — InputBox（多行输入 + @ 补全 + / 命令菜单）。"""

from pathlib import Path

from textual.widgets import TextArea, Static
from textual.events import Key
from textual.containers import Vertical
from textual.app import ComposeResult

from harness.tui.events import InputEvent


COMMANDS = [
    ("/help", "显示帮助"),
    ("/goal", "设置目标 /goal <condition>"),
    ("/clear", "清空聊天记录"),
    ("/compact", "压缩上下文"),
    ("/memory", "查看记忆"),
    ("/status", "查看状态"),
]


class CompletionPopup(Vertical):
    """补全弹出窗口。"""

    def __init__(self, items: list[str], help_texts: dict[str, str] | None = None, **kwargs):
        self.items = items
        self.help_texts = help_texts or {}
        self.selected_index = 0
        super().__init__(id="completion-popup", **kwargs)

    def compose(self) -> ComposeResult:
        for i, item in enumerate(self.items):
            help_text = self.help_texts.get(item, "")
            label = f"{item}  [dim]{help_text}[/dim]" if help_text else item
            yield Static(label, id=f"opt-{i}", classes="completion-option")

    def on_mount(self):
        if self.items:
            self._highlight(0)

    def _highlight(self, index: int):
        self.selected_index = index
        for i in range(len(self.items)):
            try:
                widget = self.query_one(f"#opt-{i}", Static)
                widget.classes = "completion-option selected" if i == index else "completion-option"
            except Exception:
                pass

    def move_up(self):
        if self.selected_index > 0:
            self._highlight(self.selected_index - 1)

    def move_down(self):
        if self.selected_index < len(self.items) - 1:
            self._highlight(self.selected_index + 1)

    def get_selected(self) -> str:
        if self.items:
            return self.items[self.selected_index]
        return ""


class InputBox(TextArea):
    """多行输入框：Enter 提交，/ 命令菜单，@ 文件补全。"""

    def __init__(self, workdir: Path | None = None):
        super().__init__(id="input")
        self.workdir = workdir or Path.cwd()
        self._popup: CompletionPopup | None = None
        self._popup_mode: str | None = None

    def on_key(self, event: Key):
        if self._popup:
            if event.key == "up":
                self._popup.move_up()
                event.prevent_default()
                return
            elif event.key == "down":
                self._popup.move_down()
                event.prevent_default()
                return
            elif event.key == "enter":
                self._apply_completion()
                event.prevent_default()
                return
            elif event.key == "escape":
                self._dismiss_popup()
                event.prevent_default()
                return

        if event.key == "enter":
            text = self.text.strip()
            if text:
                line_count = text.count("\n") + 1
                if line_count > 5 and len(text) > 200:
                    self.app.post_message(InputEvent(text=f"[pasted {line_count} lines]\n{text}"))
                else:
                    self.app.post_message(InputEvent(text=text))
                self.clear()
            event.prevent_default()
            return

        if event.character == "/" and not self.text.strip():
            self._show_command_menu()
            return

        if event.character == "@" and not self.text.strip():
            self._show_file_menu()
            return

    def _show_command_menu(self):
        help_map = {cmd: desc for cmd, desc in COMMANDS}
        self._popup = CompletionPopup(
            [cmd for cmd, _ in COMMANDS],
            help_texts=help_map,
        )
        self._popup_mode = "command"
        self.app.screen.mount(self._popup, before="#input")

    def _show_file_menu(self):
        files = self._get_workspace_files()
        if not files:
            return
        self._popup = CompletionPopup(files)
        self._popup_mode = "file"
        self.app.screen.mount(self._popup, before="#input")

    def _get_workspace_files(self, max_files: int = 15) -> list[str]:
        files = []
        skip = {".venv", "__pycache__", ".git", ".memory", ".tasks", ".runtime",
                ".transcripts", ".mailboxes", ".worktrees", ".pytest_cache"}
        try:
            for item in sorted(self.workdir.rglob("*")):
                if item.is_file() and not item.name.startswith("."):
                    rel = str(item.relative_to(self.workdir))
                    if any(s in rel for s in skip):
                        continue
                    files.append(rel)
                    if len(files) >= max_files:
                        break
        except Exception:
            pass
        return files

    def _apply_completion(self):
        if not self._popup:
            return
        selected = self._popup.get_selected()
        self._dismiss_popup()
        if self._popup_mode == "command":
            self.text = selected + " "
        elif self._popup_mode == "file":
            self.text = selected

    def _dismiss_popup(self):
        if self._popup:
            try:
                self._popup.remove()
            except Exception:
                pass
            self._popup = None
            self._popup_mode = None

    def action_submit(self):
        text = self.text.strip()
        if text:
            self.app.post_message(InputEvent(text=text))
            self.clear()
