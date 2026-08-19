"""harness.tools.teams — 8 个 team 工具。"""

import threading
from harness.tools.pool import register_tool

# Module-level state (cli.py sets these)
BUS = None
CONFIG = None
LLM = None
TASK_STORE = None
TEAMMATES: dict = {}  # name → TeammateRuntime
TEAM_LOCK = threading.RLock()


def _is_valid_name(name: str) -> bool:
    return bool(name) and 1 <= len(name) <= 64 and all(c.isalnum() or c in "-_" for c in name)


# 8 个 schema
SPAWN_TEAMMATE_SCHEMA = {"type": "function", "function": {
    "name": "spawn_teammate",
    "description": "Spawn a persistent teammate daemon thread.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Teammate name (1-64, alphanumeric/dash/underscore)"},
        "role": {"type": "string", "description": "Role, e.g. 'config engineer'"},
        "prompt": {"type": "string", "description": "Initial task description"},
        "task_id": {"type": "string", "description": "Optional: task ID to claim"},
        "require_plan": {"type": "boolean", "description": "Optional: require plan approval before work"},
    }, "required": ["name", "role", "prompt"]}
}}

LIST_TEAMMATES_SCHEMA = {"type": "function", "function": {
    "name": "list_teammates",
    "description": "List all active teammates and their status.",
    "parameters": {"type": "object", "properties": {}},
}}

SEND_MESSAGE_SCHEMA = {"type": "function", "function": {
    "name": "send_message",
    "description": "Send a message to a teammate's mailbox.",
    "parameters": {"type": "object", "properties": {
        "to": {"type": "string", "description": "Recipient name"},
        "content": {"type": "string", "description": "Message content"},
    }, "required": ["to", "content"]}
}}

BROADCAST_SCHEMA = {"type": "function", "function": {
    "name": "broadcast",
    "description": "Broadcast a message to all active teammates.",
    "parameters": {"type": "object", "properties": {
        "content": {"type": "string", "description": "Message content"},
    }, "required": ["content"]}
}}

REQUEST_SHUTDOWN_SCHEMA = {"type": "function", "function": {
    "name": "request_shutdown",
    "description": "Request a teammate to shut down after current step.",
    "parameters": {"type": "object", "properties": {
        "teammate": {"type": "string", "description": "Target teammate name"},
    }, "required": ["teammate"]}
}}

REQUEST_PLAN_SCHEMA = {"type": "function", "function": {
    "name": "request_plan",
    "description": "Require a teammate to submit a plan before making changes.",
    "parameters": {"type": "object", "properties": {
        "teammate": {"type": "string", "description": "Target teammate name"},
        "task": {"type": "string", "description": "Task to plan"},
    }, "required": ["teammate", "task"]}
}}

REVIEW_PLAN_SCHEMA = {"type": "function", "function": {
    "name": "review_plan",
    "description": "Approve or reject a teammate's plan submission.",
    "parameters": {"type": "object", "properties": {
        "request_id": {"type": "string", "description": "Plan request ID"},
        "approve": {"type": "boolean", "description": "Approve or reject"},
        "feedback": {"type": "string", "description": "Optional feedback"},
    }, "required": ["request_id", "approve"]}
}}

CREATE_WORKTREE_SCHEMA = {"type": "function", "function": {
    "name": "create_worktree",
    "description": "Create and bind a task worktree (lead-only).",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Worktree name (1-64)"},
        "task_id": {"type": "string", "description": "Task ID to bind"},
    }, "required": ["name", "task_id"]}
}}


def init_team_tools(config, llm, bus, task_store):
    """由 cli.py 调用，注册 8 个 team 工具。"""
    global BUS, CONFIG, LLM, TASK_STORE
    BUS = bus
    CONFIG = config
    LLM = llm
    TASK_STORE = task_store

    def run_spawn_teammate(name: str, role: str, prompt: str,
                           task_id: str = None, require_plan: bool = False) -> str:
        if not _is_valid_name(name):
            return "Invalid name: 1-64 letters, digits, underscores, dashes only"
        with TEAM_LOCK:
            if name in TEAMMATES:
                return f"Teammate '{name}' already exists"
        # Claim task if provided
        if task_id:
            result = TASK_STORE.claim(task_id, owner=name)
            if "Error" in result or "cannot claim" in result:
                return result
        from harness.teams.runtime import TeammateRuntime
        rt = TeammateRuntime(name, role, prompt, task_id, require_plan,
                             CONFIG, LLM, BUS, TASK_STORE)
        with TEAM_LOCK:
            TEAMMATES[name] = rt
        thread = threading.Thread(target=rt.run, name=f"teammate-{name}", daemon=True)
        thread.start()
        print(f"  \033[35m[team] spawned {name} ({role})\033[0m", flush=True)
        return f"Spawned teammate '{name}' ({role})" + (f" for task {task_id}" if task_id else "")

    def run_list_teammates() -> str:
        with TEAM_LOCK:
            if not TEAMMATES:
                return "No active teammates."
            lines = [f"{name}: active" for name in TEAMMATES]
            return "\n".join(lines)

    def run_send_message(to: str, content: str) -> str:
        with TEAM_LOCK:
            if to not in TEAMMATES:
                return f"Teammate '{to}' not found"
        BUS.send("lead", to, content)
        return f"Message sent to {to}"

    def run_broadcast(content: str) -> str:
        with TEAM_LOCK:
            names = list(TEAMMATES.keys())
        if not names:
            return "No teammates to broadcast to."
        for name in names:
            BUS.send("lead", name, content)
        return f"Broadcast to {len(names)} teammate(s): {', '.join(names)}"

    def run_request_shutdown(teammate: str) -> str:
        with TEAM_LOCK:
            if teammate not in TEAMMATES:
                return f"Teammate '{teammate}' not found"
        BUS.send("lead", teammate, "Please shut down.", "shutdown_request")
        return f"Shutdown requested for {teammate}"

    def run_request_plan(teammate: str, task: str) -> str:
        with TEAM_LOCK:
            if teammate not in TEAMMATES:
                return f"Teammate '{teammate}' not found"
        BUS.send("lead", teammate, task, "plan_request")
        return f"Plan requested from {teammate}"

    def run_review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
        BUS.send("lead", "lead", feedback or ("Approved" if approve else "Rejected"),
                 "plan_approval_response", {"request_id": request_id, "approve": approve})
        return f"Plan {'approved' if approve else 'rejected'} ({request_id})"

    def run_create_worktree(name: str, task_id: str) -> str:
        from harness.teams.worktree import create_worktree
        return create_worktree(name, task_id, CONFIG.workdir)

    register_tool(SPAWN_TEAMMATE_SCHEMA, run_spawn_teammate)
    register_tool(LIST_TEAMMATES_SCHEMA, run_list_teammates)
    register_tool(SEND_MESSAGE_SCHEMA, run_send_message)
    register_tool(BROADCAST_SCHEMA, run_broadcast)
    register_tool(REQUEST_SHUTDOWN_SCHEMA, run_request_shutdown)
    register_tool(REQUEST_PLAN_SCHEMA, run_request_plan)
    register_tool(REVIEW_PLAN_SCHEMA, run_review_plan)
    register_tool(CREATE_WORKTREE_SCHEMA, run_create_worktree)
