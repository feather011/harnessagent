"""harness.goal.controller — GoalController + GoalDecision。"""

from dataclasses import dataclass

from harness.goal.state import GoalState
from harness.goal.evaluator import PromptGoalEvaluator


@dataclass
class GoalDecision:
    action: str   # pass | block | defer
    reason: str


class GoalController:
    """Goal 循环控制器：set / inspect / clear / evaluate_after_turn。"""

    MAX_CONSECUTIVE_BLOCKS = 3

    def __init__(self, evaluator: PromptGoalEvaluator | None = None):
        self.evaluator = evaluator or PromptGoalEvaluator()
        self.state: GoalState | None = None

    def set(self, condition: str) -> GoalState:
        self.state = GoalState(condition=condition)
        return self.state

    def clear(self) -> None:
        self.state = None

    def inspect(self) -> GoalState | None:
        return self.state

    def evaluate_after_turn(self, messages: list, has_pending_async: bool) -> GoalDecision:
        """6 分支：no_goal / defer / ok / impossible / block / max_consecutive。"""
        if self.state is None:
            return GoalDecision("pass", "no goal set")
        if has_pending_async:
            return GoalDecision("defer", "background/Workflow task running; goal deferred")

        result = self.evaluator.evaluate(self.state.condition, messages)
        if result.get("impossible"):
            self.state.status = "impossible"
            return GoalDecision("block", result.get("reason") or "Goal marked impossible")
        if result.get("ok"):
            self.state.status = "completed"
            return GoalDecision("pass", result.get("reason") or "")

        # 未达 → block
        self.state.eval_count += 1
        self.state.latest_reason = result.get("reason") or "goal not yet satisfied"
        if self.state.eval_count > self.MAX_CONSECUTIVE_BLOCKS + 1:
            return GoalDecision(
                "pass",
                f"Goal exceeded {self.MAX_CONSECUTIVE_BLOCKS} blocks; "
                f"latest: {self.state.latest_reason}. Giving control back to user.",
            )
        return GoalDecision("block", self.state.latest_reason)
