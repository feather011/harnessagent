"""harness.hooks.registry — HOOKS dict + register_hook + trigger_hooks + HookContext。"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class HookContext:
    """钩子上下文（Phase 1 简化版，Phase 2+ 可扩展）。"""
    event: str
    args: tuple = field(default_factory=tuple)


# 模块级单例：import 即注册
HOOKS: dict[str, list[Callable]] = {}


def register_hook(event: str, callback: Callable) -> None:
    """注册钩子回调。同一 event 可注册多个回调，按注册顺序执行。"""
    HOOKS.setdefault(event, []).append(callback)


def trigger_hooks(event: str, *args) -> Any | None:
    """触发指定事件的所有钩子。第一个返回非 None 的钩子中止后续执行。"""
    for callback in HOOKS.get(event, []):
        result = callback(*args)
        if result is not None:
            return result
    return None
