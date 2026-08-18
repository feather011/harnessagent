"""harness.tools.planning — todo_write 工具 + TodoManager。"""

import ast
import json

from harness.tools.pool import register_tool, TODO_WRITE_SCHEMA


class TodoManager:
    """跨 query 共享的 task list。module-level 单例。"""

    _MARKERS = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}

    def __init__(self):
        self.items: list[dict] = []

    def update(self, todos) -> str:
        """校验并替换任务清单，返回渲染文本。"""
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                todos = ast.literal_eval(todos)
        if not isinstance(todos, list):
            raise ValueError("todos must be a list")
        if len(todos) > 20:
            raise ValueError("too many todos (max 20)")
        validated = []
        in_progress_count = 0
        for todo in todos:
            if not isinstance(todo, dict):
                raise ValueError("each todo must be an object")
            content = str(todo.get("content", "")).strip()
            if not content:
                raise ValueError("todo content cannot be empty")
            status = str(todo.get("status", "pending")).lower()
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"invalid status: {status}")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content": content, "status": status})
        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = [f"{self._MARKERS[t['status']]} {t['content']}" for t in self.items]
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


# Module-level singleton
TODO_MANAGER = TodoManager()


def run_todo_write(todos) -> str:
    """dispatch: 校验失败转 Error 字符串。"""
    try:
        return TODO_MANAGER.update(todos)
    except (ValueError, TypeError, SyntaxError) as e:
        return f"Error: {e}"


# Register at import time
register_tool(TODO_WRITE_SCHEMA, run_todo_write)
