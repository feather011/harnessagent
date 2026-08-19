"""harness.tui.widgets.input_box — InputBox（多行输入 + 补全）。"""

from textual.widgets import TextArea
from textual.events import Key
from harness.tui.events import InputEvent


class InputBox(TextArea):
    """多行输入框：Enter 提交，Shift+Enter 换行。"""

    def __init__(self):
        super().__init__(id="input")

    def on_key(self, event: Key):
        if event.key == "enter" and not event.shift:
            text = self.text.strip()
            if text:
                # 粘贴检测：> 50 字符 → 标记
                line_count = text.count("\n") + 1
                if line_count > 5 and len(text) > 200:
                    self.app.post_message(InputEvent(text=f"[pasted {line_count} lines]\n{text}"))
                else:
                    self.app.post_message(InputEvent(text=text))
                self.clear()
            event.prevent_default()

    def action_submit(self):
        """Textual action 提交。"""
        text = self.text.strip()
        if text:
            self.app.post_message(InputEvent(text=text))
            self.clear()
