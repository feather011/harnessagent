"""harness.tui.widgets.status — StatusBar（顶部 1 行）。"""

from textual.widgets import Static


class StatusBar(Static):
    """顶部状态栏：model / workdir / state / tools。"""

    def __init__(self, config):
        super().__init__(id="status")
        self.config = config
        self.state = "idle"
        self._tools_count = 35

    def render(self):
        return (f"[bold]harness[/bold] · "
                f"[dim]{self.config.model}[/dim] · "
                f"[yellow]{self.config.workdir.name}[/yellow] · "
                f"● {self.state} | tools: {self._tools_count}")

    def set_state(self, state: str):
        self.state = state
        self.refresh()
