"""harness.goal.state — GoalState dataclass。"""

import time
from dataclasses import dataclass, field


@dataclass
class GoalState:
    """目标状态（内存，不持久化）。"""
    condition: str
    status: str = "pending"          # pending | completed | impossible
    eval_count: int = 0
    start_time: float = field(default_factory=time.time)
    latest_reason: str = ""

    def elapsed(self) -> float:
        return time.time() - self.start_time
