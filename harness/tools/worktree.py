"""harness.tools.worktree — remove_worktree 工具。"""

from harness.tools.pool import register_tool

REMOVE_WORKTREE_SCHEMA = {"type": "function", "function": {
    "name": "remove_worktree",
    "description": "Remove a task worktree (lead-only, preserves branch).",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Worktree name to remove"},
        "discard_changes": {"type": "boolean", "description": "Force remove even with uncommitted changes"},
    }, "required": ["name"]}
}}


def init_worktree_tool(config):
    """由 cli.py 调用，注册 remove_worktree 工具。"""
    def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
        from harness.teams.worktree import remove_worktree
        return remove_worktree(name, config.workdir, discard_changes)
    register_tool(REMOVE_WORKTREE_SCHEMA, run_remove_worktree)
