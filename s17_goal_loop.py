#!/usr/bin/env python3
"""s17_goal_loop: 目标循环（基于 s16_workflow_runtime，不动前置任何章节）。

策略（C 方案风格）：借 s15 的 4 钩子 + drain + assemble + compaction；借 s16 的
assemble_tool_pool_v2；s17 自写 agent_loop + 加 Goal Stop hook。/goal 三变体在 CLI 解析，
set 后立即追加 user 消息 + 调 agent_loop 触发工作。背景 bash 任务 + Workflow 任务都在跑时
defer（保留 Goal）。
"""
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field

import s14_mcp_plugin as s14
import s15_integrated_harness as s15
import s16_workflow_runtime as s16
from openai import OpenAI
from dotenv import load_dotenv

_strip_surrogates = s16._strip_surrogates
client = s16.client
MODEL = s16.MODEL
WORKDIR = s16.WORKDIR

# ============================================================ GoalState（内存状态，不持久化） ============================================================
@dataclass
class GoalState:
    condition: str
    status: str = "pending"          # pending | completed | impossible
    eval_count: int = 0              # 已评估次数
    start_time: float = field(default_factory=time.time)
    latest_reason: str = ""

    def elapsed(self) -> float:
        return time.time() - self.start_time


# ============================================================ PromptGoalEvaluator（独立 LLM 调用，复用 s16.RealAgentRunner） ============================================================
class PromptGoalEvaluator:
    """独立 LLM 调用评估 goal，prompt 明确要求基于对话中具体证据判断。"""

    EVAL_SCHEMA = s16.SimpleJsonSchema(
        required=["ok", "reason"],
        types={"ok": bool, "reason": str, "impossible": bool},
    )
    KEEP_RECENT = 20
    TRUNCATE_THRESHOLD = 4000
    TRUNCATE_HEAD = 2000
    TRUNCATE_TAIL = 2000
    PROMPT_CHAR_LIMIT = 60000

    def __init__(self, runner: s16.AgentRunner | None = None):
        self.runner = runner or s16.RealAgentRunner()

    def _truncate_messages(self, messages: list) -> list:
        recent = messages[-self.KEEP_RECENT:]
        truncated = []
        for m in recent:
            content = m.get("content", "")
            if isinstance(content, str) and len(content) > self.TRUNCATE_THRESHOLD:
                content = (content[:self.TRUNCATE_HEAD]
                           + "\n...[truncated]...\n"
                           + content[-self.TRUNCATE_TAIL:])
            truncated.append({**m, "content": content})
        return truncated

    def evaluate(self, condition: str, messages: list) -> dict:
        truncated = self._truncate_messages(messages)
        prompt = (f"Your job is to evaluate whether the following goal has been achieved. "
                  f"Base your answer ONLY on concrete actions or results shown in the conversation. "
                  f"Reject claims with no supporting evidence.\n\n"
                  f"Goal condition: {condition}\n\n"
                  f"Conversation excerpt:\n{json.dumps(truncated, ensure_ascii=False)[:self.PROMPT_CHAR_LIMIT]}\n\n"
                  f"Respond with JSON containing: ok (bool), reason (str), impossible (bool).")
        try:
            result = self.runner.run(prompt, schema=self.EVAL_SCHEMA)
            if not isinstance(result, dict):
                return {"ok": False, "reason": f"Evaluator returned non-dict: {type(result).__name__}", "impossible": False}
            # 强制 schema 字段齐全
            result.setdefault("ok", False)
            result.setdefault("reason", "")
            result.setdefault("impossible", False)
            return result
        except Exception as e:
            return {"ok": False, "reason": f"Evaluator error: {type(e).__name__}: {e}", "impossible": False}


# ============================================================ GoalDecision + GoalController ============================================================
@dataclass
class GoalDecision:
    action: str          # pass | block | defer
    reason: str


class GoalController:
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
        """每轮 agent 后评估。active goal 缺失 → pass；后台在跑 → defer；评估 ok → pass；
        impossible → block；普通未达 → block；超 max_consecutive → pass（强制出口）。"""
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
            # 超过预算 → 强制 pass 但 surface 信息给用户（教材 L144）
            return GoalDecision(
                "pass",
                f"Goal exceeded {self.MAX_CONSECUTIVE_BLOCKS} blocks; "
                f"latest: {self.state.latest_reason}. Giving control back to user.",
            )
        return GoalDecision("block", self.state.latest_reason)


# ============================================================ has_pending_async（s11 bg + s16 workflow） ============================================================
def has_pending_async() -> bool:
    """背景 bash（s14.BACKGROUND.tasks 有 running 状态）+ Workflow（s16.scheduler_tasks 有 RUNNING）。"""
    bg_tasks = getattr(s14.BACKGROUND, "tasks", {})
    if any(t.get("status") == "running" for t in bg_tasks.values()):
        return True
    return any(t.status == s16.TaskStatus.RUNNING for t in s16.scheduler_tasks.values())


# ============================================================ /goal CLI 解析 ============================================================
def parse_goal_command(q: str) -> tuple[str, str] | None:
    """返 ('set', condition) / ('clear', '') / ('inspect', '') / None（不是 /goal）。
    接受 /goal 大小写变体；保留 condition 原始大小写。"""
    stripped = q.strip()
    if not stripped.lower().startswith("/goal"):
        return None
    rest = stripped[len("/goal"):].strip()
    if not rest or rest.lower() in ("status", "show", "view", "inspect"):
        return ("inspect", "")
    if rest.lower() in ("clear", "stop", "off", "reset", "none", "cancel"):
        return ("clear", "")
    return ("set", rest)


# ============================================================ agent_loop（s17 自有，加 Goal Stop hook） ============================================================
GOAL_STATE_HINT = (
    "\n\nGoal loop active: if a goal condition is set, the system will evaluate after each turn and "
    "block the loop until the goal is achieved, impossible, or the block budget is exhausted."
)


def _build_system_prompt(tools: list[dict], goal_active: bool) -> str:
    base = s15.assemble_system_prompt({"tools": tools})
    extra = (s16._format_workflows_section()
            + "\n\nUse the workflow tool to run a registered workflow. "
            + "The tool returns immediately with run_id; final <task_notification> arrives on a later turn.")
    prompt = base + "\n\nWorkflows available:\n" + extra
    if goal_active:
        prompt += GOAL_STATE_HINT
    return prompt


def agent_loop(messages: list, active_request: str, goal_controller: GoalController) -> None:
    reactive_retries = 0
    while True:
        # 钩子 1：UserPromptSubmit
        if active_request and not active_request.startswith("("):
            s15._hook_user_prompt_submit(active_request)

        # 钩子 2：drain 3 事件源（含 s16 workflow notifications）
        for ev in s15.drain_events():
            messages.append(ev)
        for ev in s16._drain_workflow_notifications():
            messages.append(ev)

        # 钩子 3：compaction
        messages[:] = s15._compaction_step(messages, active_request)

        # 系统 prompt 每轮重拼（含 goal hint）
        tools, handlers = s16.assemble_tool_pool_v2()
        system_prompt = _build_system_prompt(tools, goal_active=goal_controller.state is not None)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})

        # LLM + reactive 限流
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=_strip_surrogates(messages), tools=tools, max_tokens=8000,
            )
            reactive_retries = 0
        except Exception as error:
            too_long = any(t in str(error).lower() for t in ("prompt_too_long", "too many tokens"))
            if too_long and reactive_retries < s14.MAX_REACTIVE_RETRIES:
                messages[:] = s14.COMPACTOR.reactive_compact(messages, active_request)
                reactive_retries += 1
                continue
            raise

        msg = resp.choices[0].message
        messages.append(_strip_surrogates(msg.model_dump(exclude_none=True)))

        # 钩子 4+5：tool dispatch（沿用 s16 模式）
        if not msg.tool_calls:
            # Goal Stop hook（s17 新增）—— 排在 _hook_stop 之前
            decision = goal_controller.evaluate_after_turn(messages, has_pending_async())
            if decision.action == "block":
                messages.append({"role": "user", "content": f"[Goal block #{goal_controller.state.eval_count}] {decision.reason}"})
                continue
            # defer / pass / error → 退出当前 turn
            force = s15._hook_stop(messages)
            if force:
                messages.append({"role": "user", "content": str(force)})
                continue
            if msg.content:
                print(_strip_surrogates(f"\033[32m{msg.content}\033[0m"), flush=True)
            s14.extract_memories(messages)
            return

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args_dict = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": f"Error: invalid arguments JSON: {e}"})
                continue
            if (blocked := s15._hook_permission(name, args_dict)) is not None:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": str(blocked)})
                continue
            try:
                output = s16.s14.execute_named(handlers, name, args_dict)
            except Exception as e:
                output = f"Error: {type(e).__name__}: {e}"
            s15._hook_log_tool_call(name, args_dict, output)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})


# ============================================================ CLI 主循环（借 s16._stdin_reader / s14 钩子） ============================================================
def _stdin_reader(stdin_q: "queue.Queue"):
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            break
        if line == "":
            stdin_q.put(None)
            break
        stdin_q.put(line.rstrip("\n"))


if __name__ == "__main__":
    history = [{"role": "system",
                "content": _build_system_prompt([], goal_active=False)}]
    goal_controller = GoalController()
    print(f"\033[36m使用模型 {MODEL}（s17 goal loop + s16 全量），输入 /goal ... 或 q 退出\033[0m", flush=True)
    s14.SCHEDULER.start()
    stdin_q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(stdin_q,), daemon=True).start()
    try:
        print("s17 >> ", end="", flush=True)
        while True:
            # Lead 邮箱优先（team 事件）
            if s14.BUS.peek("lead"):
                inbox = s14.consume_lead_inbox()
                if inbox:
                    history[0]["content"] = _build_system_prompt([], goal_controller.state is not None)
                    history.append({"role": "user", "content": s14.format_team_events(inbox)})
                    print(f"\033[33m[wake: {len(inbox)} team event(s) -> new turn]\033[0m", flush=True)
                    with s14.SCHEDULER.agent_lock:
                        agent_loop(history, "(team events)", goal_controller)
                    print(flush=True)
                print("s17 >> ", end="", flush=True)
                continue
            try:
                q = stdin_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if q is None or q in ("q", "exit", ""):
                break
            # /goal 命令解析
            cmd = parse_goal_command(q)
            if cmd is not None:
                kind, arg = cmd
                if kind == "inspect":
                    st = goal_controller.inspect()
                    if st is None:
                        print("\n[goal] no active goal")
                    else:
                        print(f"\n[goal] active | condition={st.condition!r} | "
                              f"eval={st.eval_count} | status={st.status} | elapsed={st.elapsed():.1f}s")
                elif kind == "clear":
                    goal_controller.clear()
                    print("[goal] cleared")
                elif kind == "set":
                    goal_controller.set(arg)
                    history[0]["content"] = _build_system_prompt([], True)
                    history.append({"role": "user", "content": arg})
                    print(f"[goal] set: {arg!r}")
                    with s14.SCHEDULER.agent_lock:
                        s14.trigger_hooks("UserPromptSubmit", arg)
                        agent_loop(history, arg, goal_controller)
                    print(flush=True)
                print("s17 >> ", end="", flush=True)
                continue
            # 普通输入
            with s14.SCHEDULER.agent_lock:
                history[0]["content"] = _build_system_prompt([], goal_controller.state is not None)
                s14.trigger_hooks("UserPromptSubmit", q)
                history.append({"role": "user", "content": q})
                agent_loop(history, q, goal_controller)
            print(flush=True)
            print("s17 >> ", end="", flush=True)
    finally:
        s14.SCHEDULER.stop()