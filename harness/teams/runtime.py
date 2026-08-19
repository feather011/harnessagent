"""harness.teams.runtime — TeammateRuntime（WORK → IDLE 循环 + 独立 messages[]）。"""

import json
import threading
from pathlib import Path

from harness.llm import LLMClient
from harness.tools.pool import assemble_tool_pool, execute_tool

IDLE_SCAN_INTERVAL = 5.0  # seconds

# Teammate 工具子集（10 个：5 base + send_message + submit_plan + 3 task）
TEAMMATE_TOOL_NAMES = [
    "bash", "read_file", "write_file", "edit_file", "glob",
    "send_message", "submit_plan",
    "list_tasks", "claim_task", "complete_task",
]


class TeammateRuntime:
    """一个持久 teammate：独立 system/messages/工具，WORK → IDLE → WORK。"""

    def __init__(self, name: str, role: str, prompt: str,
                 task_id: str | None, require_plan: bool,
                 config, llm, bus, task_store):
        self.name = name
        self.config = config
        self.llm = llm
        self.bus = bus
        self.task_store = task_store
        self._stop_event = threading.Event()
        self._plan_pending = False
        self._plan_request_id = None

        # 构建 system prompt
        self.system = (
            f"You are '{name}', a {role}. Use tools to complete the assigned "
            "Task, then call complete_task and report a concise result. "
            "File and shell tools use the Task's working directory. "
            "Use send_message only for intermediate coordination. "
            "Address the coordinator as 'lead'."
        )
        self.messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": prompt},
        ]
        if task_id:
            try:
                task = task_store.load(task_id)
                self.messages[1]["content"] += (
                    f"\n\n[Assigned task {task.id}] {task.subject}\n"
                    f"{task.description}\nWork directory: {config.workdir}"
                )
            except (FileNotFoundError, ValueError):
                pass
        if require_plan:
            self.messages[1]["content"] += (
                "\n\n[Plan required] Submit a plan and wait for Lead approval "
                "before changing files or using bash."
            )
            self._plan_pending = True

        # 构建 tool handlers
        self._handlers = self._build_handlers()

    def _build_handlers(self) -> dict:
        """构建 10 个 tool handler。"""
        name = self.name

        def send_message(to: str, content: str) -> str:
            self.bus.send(name, to, content)
            return f"Sent to {to}"

        def submit_plan(plan: str) -> str:
            self._plan_pending = True
            req_id = f"req_{id(self) % 1000000:06d}"
            self._plan_request_id = req_id
            self.bus.send(name, "lead", plan, "plan_approval_request",
                          {"request_id": req_id, "task_id": ""})
            return f"Plan submitted (request {req_id}). Waiting for Lead approval."

        def claim_task_fn(task_id: str) -> str:
            return self.task_store.claim(task_id, owner=name)

        def complete_task_fn(task_id: str) -> str:
            return self.task_store.complete(task_id, owner=name)

        return {
            "bash": lambda command, **kw: self._run_tool("bash", {"command": command}),
            "read_file": lambda path, **kw: self._run_tool("read_file", {"path": path}),
            "write_file": lambda path, content, **kw: self._run_tool("write_file", {"path": path, "content": content}),
            "edit_file": lambda path, old_text, new_text, **kw: self._run_tool("edit_file", {"path": path, "old_text": old_text, "new_text": new_text}),
            "glob": lambda pattern, **kw: self._run_tool("glob", {"pattern": pattern}),
            "send_message": send_message,
            "submit_plan": submit_plan,
            "list_tasks": lambda **kw: self._run_tool("list_tasks", {}),
            "claim_task": claim_task_fn,
            "complete_task": complete_task_fn,
        }

    def _run_tool(self, name: str, args: dict) -> str:
        """执行基础工具（用 Phase 1-3 的 handlers）。"""
        _, handlers = assemble_tool_pool()
        return execute_tool(handlers, name, args)

    def _get_teammate_tools(self) -> list[dict]:
        """获取 teammate 可用的工具 schema 子集。"""
        tools, _ = assemble_tool_pool()
        return [t for t in tools if t["function"]["name"] in TEAMMATE_TOOL_NAMES]

    def handle_inbox(self, inbox: list[dict]) -> bool:
        """处理收件箱消息。返回 True = 收到 shutdown。"""
        work_messages = []
        for msg in inbox:
            msg_type = msg.get("type", "message")
            if msg_type == "shutdown_request":
                self.bus.send(self.name, "lead", "Shutdown acknowledged.", "shutdown_response")
                return True
            if msg_type == "plan_approval_response":
                approved = msg.get("metadata", {}).get("approve", False)
                feedback = msg.get("content", "")
                if approved:
                    self._plan_pending = False
                    work_messages.append("[Plan approved] You may proceed.")
                else:
                    work_messages.append(f"[Plan rejected] {feedback}")
                continue
            if msg_type == "plan_request":
                work_messages.append(f"[Plan required] {msg['content']}")
                continue
            work_messages.append(f"[Message from {msg['from']}] {msg['content']}")
        if work_messages:
            self.messages.append({"role": "user", "content": "\n".join(work_messages)})
        return False

    def work(self) -> str:
        """跑一轮 LLM → tool → result。返回 continue / idle / stop。"""
        if self.handle_inbox(self.bus.read_inbox(self.name)):
            return "stop"

        tools = self._get_teammate_tools()
        try:
            resp = self.llm.chat(self.messages, tools=tools)
        except Exception as exc:
            self.bus.send(self.name, "lead", f"{type(exc).__name__}: {exc}", "error")
            return "stop"

        msg = resp.choices[0].message
        self.messages.append(LLMClient.strip_surrogates(msg.model_dump(exclude_none=True)))

        if msg.tool_calls:
            for call in msg.tool_calls:
                tool_name = call.function.name
                try:
                    args = json.loads(call.function.arguments)
                except json.JSONDecodeError as e:
                    output = f"Error: invalid JSON: {e}"
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
                    continue
                handler = self._handlers.get(tool_name)
                if handler:
                    try:
                        output = str(handler(**args))
                    except Exception as e:
                        output = f"Error: {type(e).__name__}: {e}"
                else:
                    output = f"Error: unknown tool '{tool_name}'"
                print(f"  \033[35m[{self.name}] > {tool_name}({str(args)[:80]})\033[0m", flush=True)
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
            return "continue"

        # 无 tool_calls → 发结果给 lead
        summary = (msg.content or "").strip()
        if self._plan_pending:
            # 还在等 plan approval
            return "idle"
        if summary:
            self.bus.send(self.name, "lead", summary, "result")
        self.bus.send(self.name, "lead", "Waiting for more work.", "idle_notification")
        return "idle"

    def wait_for_work(self) -> bool:
        """IDLE：等 bus 消息或超时。返回 True = 有新工作。"""
        inbox = self.bus.wait_for_messages(self.name, timeout=IDLE_SCAN_INTERVAL)
        if inbox:
            before = len(self.messages)
            if self.handle_inbox(inbox):
                return False  # shutdown
            return len(self.messages) > before
        return False

    def run(self):
        """daemon thread 主循环。"""
        try:
            state = "continue"
            while state != "stop":
                if state == "idle" and not self.wait_for_work():
                    break
                state = self.work()
        except Exception as exc:
            try:
                self.bus.send(self.name, "lead", f"{type(exc).__name__}: {exc}", "error")
            except Exception:
                pass
        finally:
            with self._lock_for_team():
                pass  # cleanup handled by caller
            print(f"  [teammate] {self.name} finished", flush=True)

    def _lock_for_team(self):
        """占位：team_lock 由外部管理。"""
        pass
