"""harness.errors — 异常层级。"""


class HarnessError(Exception):
    """harness 包的基础异常。"""


class ToolError(HarnessError):
    """工具执行错误。"""


class PermissionDenied(HarnessError):
    """权限拒绝。"""


class WorkflowInputError(HarnessError):
    """工作流输入错误。"""


class GoalStateError(HarnessError):
    """目标状态错误。"""
