"""harness.tui.splash — SplashScreen（0.8s 自动切）。"""

from textual.screen import Screen
from textual.widgets import Static
from harness.tui.logo import HARNESS_LOGO


class SplashScreen(Screen):
    """启动画面：logo + 版本号，0.8s 后自动消失。"""

    def compose(self):
        yield Static(HARNESS_LOGO, id="logo", classes="logo")
        yield Static("[dim]v0.1.0 · powered by harnessagent · MiMo mimo-v2.5[/dim]", id="subtitle")

    def on_mount(self):
        self.set_timer(0.8, lambda: self.app.pop_screen())
