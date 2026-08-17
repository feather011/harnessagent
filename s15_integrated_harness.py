#!/usr/bin/env python3
"""s15_integrated_harness: 纯 wiring 整合（基于 s14_mcp_plugin，不动 s14 一行）。

s15 只做 3 件事：
1. 抽 6 钩子函数（4 薄包装委托 s14.trigger_hooks + 2 直接函数 drain_events / compaction）
2. assemble_system_prompt 扩展 s14 4 段为 6 段（+ tools / workspace / event_tag）
3. drain_events 收口 3 个事件源（cron / background / team），格式完全沿用 s14

策略：s14 的关键内联逻辑（cron ack/restore、reactive_retries 限流、memory extract、
bg dispatch、compact 工具特殊处理）通过 _s14_inner_loop 单 turn helper 委托。
"""
import json
import queue
import sys
import threading

import s14_mcp_plugin as s14
from openai import OpenAI
from dotenv import load_dotenv

# 沿用 s14 的 client/MODEL/WORKDIR + 已注册 hooks + 工具池
client = s14.client
MODEL = s14.MODEL
WORKDIR = s14.WORKDIR
SYSTEM = s14.SYSTEM  # 启动时一次性 base
MAX_REACTIVE_RETRIES = s14.MAX_REACTIVE_RETRIES

_strip_surrogates = s14._strip_surrogates


# ============================================================ 6 钩子函数 ============================================================
# 钩子 1：UserPromptSubmit（薄包装 → s14.trigger_hooks，s14 既有 context_inject_hook 继续生效）
def _hook_user_prompt_submit(query: str) -> None:
    s14.trigger_hooks("UserPromptSubmit", query)


# 钩子 4：permission（薄包装 → s14.trigger_hooks，含 permission_hook + log_hook）
def _hook_permission(name: str, args: dict):
    return s14.trigger_hooks("PreToolUse", name, args)


# 钩子 5：log tool call（薄包装 → s14.trigger_hooks，含 large_output_hook + log_hook）
def _hook_log_tool_call(name: str, args: dict, output) -> None:
    s14.trigger_hooks("PostToolUse", name, args, output)


# 钩子 6：Stop（薄包装 → s14.trigger_hooks，含 summary_hook）
def _hook_stop(messages):
    return s14.trigger_hooks("Stop", messages)


# ============================================================ 钩子 2 + 3（直接函数，s15 显式化） ============================================================
# 钩子 2：drain 3 事件源（cron / background / team），格式沿用 s14
def drain_events() -> list[dict]:
    """收口 5 个事件源：用户输入 + 工具结果走 OpenAI 工具结果消息；
    cron / background / team 走 drain_events 注入为独立 user 消息。
    返回 [Scheduled] (s12)、<task_notification> (s11)、[Team events] (s13) 三种字符串。"""
    events: list[dict] = []
    # cron：沿用 s14 SCHEDULER.consume_queue + "[Scheduled] {prompt}" 既有格式
    for job in s14.SCHEDULER.consume_queue():
        events.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
    # background：沿用 s14 collect_background_results 既有完整 <task_notification> XML
    bg_msgs = s14.collect_background_results()
    if bg_msgs:
        events.append({"role": "user", "content": "\n\n".join(bg_msgs)})
    # team：沿用 s14 consume_lead_inbox + format_team_events
    inbox = s14.consume_lead_inbox()
    if inbox:
        events.append({"role": "user", "content": s14.format_team_events(inbox)})
    return events


# 钩子 3：compaction（直接函数，包装 s14.COMPACTOR.prepare）
def _compaction_step(messages: list, active_request: str) -> list:
    return s14.COMPACTOR.prepare(messages, active_request)


# ============================================================ assemble_system_prompt（s14 4 段 → s15 6 段） ============================================================
def _format_tools_section(tools: list[dict]) -> str:
    """把 schema 列表转成简短描述（一行一个工具）。"""
    lines = []
    for tool in tools:
        name = tool.get("function", {}).get("name", "?")
        desc = tool.get("function", {}).get("description", "")
        lines.append(f"- {name}: {desc[:120]}")
    return "\n".join(lines[:30]) if lines else "(no tools)"


_EVENT_TAG_EXPLANATION = (
    "Event tags you'll see in user messages:\n"
    "- [Scheduled] <prompt> — a cron job fired; follow the prompt as if the user asked\n"
    "- <task_notification>...</task_notification> — a background bash task finished; treat <status>completed/failed</status> as outcome\n"
    "- [Team events] ... — a teammate sent you a result / idle_notification / plan_approval_request / shutdown_request; respond accordingly\n"
)


def assemble_system_prompt(context: dict) -> str:
    """s15 扩展版：在 s14 build_system_prompt (identity + skills + memory + mcp 4 段) 基础上追加 3 段。"""
    base = s14.build_system_prompt()  # 沿用 s14 4 段
    extra = []
    if context.get("tools"):
        extra.append("Available tools (rebuilt every turn):\n" + _format_tools_section(context["tools"]))
    extra.append(f"Working directory: {WORKDIR}")
    extra.append(_EVENT_TAG_EXPLANATION)
    return base + "\n\n" + "\n\n".join(extra)


# ============================================================ _s14_inner_loop：单轮委托 ============================================================
def _s14_inner_loop(messages: list, active_request: str) -> None:
    """单轮 agent_loop 委托给 s14.agent_loop（沿用 cron ack/restore/bg dispatch/
    reactive 限流/compact 工具/memory extract 全套内联逻辑）。"""
    # s14.agent_loop 期望 messages 含 system 消息在 [0]；调用前不需要特殊处理
    s14.agent_loop(messages, active_request)


# ============================================================ s15 主循环（agent_loop） ============================================================
def agent_loop(messages: list, active_request: str) -> None:
    reactive_retries = 0
    while True:
        # 钩子 1：UserPromptSubmit（前台 turn 才发，避免 team/cron turn 误触发）
        if active_request and not active_request.startswith("("):
            _hook_user_prompt_submit(active_request)

        # 钩子 2：drain 3 事件源 → 注入 user 消息
        for ev in drain_events():
            messages.append(ev)

        # 钩子 3：compaction（s08 4 步管线）
        messages[:] = _compaction_step(messages, active_request)

        # 每轮重组 system prompt（mcp/memory 会变）
        tools, handlers = s14.assemble_tool_pool()
        system_prompt = assemble_system_prompt({"tools": tools})
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})

        # LLM 调用 + reactive 限流（沿用 s14 模式但不内联；超长才 reactive 重试）
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=_strip_surrogates(messages),
                tools=tools,
                max_tokens=8000,
            )
            reactive_retries = 0
        except Exception as error:
            too_long = any(t in str(error).lower() for t in ("prompt_too_long", "too many tokens"))
            if too_long and reactive_retries < MAX_REACTIVE_RETRIES:
                messages[:] = s14.COMPACTOR.reactive_compact(messages, active_request)
                reactive_retries += 1
                continue
            raise

        msg = resp.choices[0].message
        messages.append(_strip_surrogates(msg.model_dump(exclude_none=True)))

        # 无 tool_calls → 钩子 6 + 记忆提取
        if not msg.tool_calls:
            force = _hook_stop(messages)
            if force:
                messages.append({"role": "user", "content": str(force)})
                continue
            if msg.content:
                print(_strip_surrogates(f"\033[32m{msg.content}\033[0m"), flush=True)
            s14.extract_memories(messages)
            return

        # 钩子 4+5：每个 tool_call 走 PreToolUse + execute + PostToolUse
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                output = f"Error: invalid arguments JSON: {e}"
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
                continue
            if (blocked := _hook_permission(name, args)) is not None:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": str(blocked)})
                continue
            output = s14.execute_named(handlers, name, args)
            _hook_log_tool_call(name, args, output)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})


# ============================================================ CLI 主循环（接管 s14 if __name__） ============================================================
def _stdin_reader(stdin_q: "queue.Queue"):
    """沿用 s14 既有的 stdin reader 实现（不重复）。"""
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
    history = [{"role": "system", "content": assemble_system_prompt({"tools": []})}]
    print(f"\033[36m使用模型 {MODEL}（s15 integrated harness + s14 全量 wiring），输入 q / exit / 空行退出\033[0m", flush=True)
    s14.SCHEDULER.start()
    stdin_q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(stdin_q,), daemon=True).start()
    had_teammates = False
    try:
        print("s15 >> ", end="", flush=True)
        while True:
            # Lead 邮箱优先（teammate 异步事件）
            if s14.BUS.peek("lead"):
                inbox = s14.consume_lead_inbox()
                if inbox:
                    history[0]["content"] = assemble_system_prompt({"tools": []})
                    history.append({
                        "role": "user",
                        "content": s14.format_team_events(inbox),
                    })
                    print(f"\033[33m[wake: {len(inbox)} team event(s) -> new turn]\033[0m", flush=True)
                    with s14.SCHEDULER.agent_lock:
                        agent_loop(history, "(team events)")
                    print(flush=True)
                print("s15 >> ", end="", flush=True)
                continue
            try:
                q = stdin_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if q is None or q in ("q", "exit", ""):
                break
            with s14.SCHEDULER.agent_lock:
                history[0]["content"] = assemble_system_prompt({"tools": []})
                s14.trigger_hooks("UserPromptSubmit", q)
                history.append({"role": "user", "content": q})
                agent_loop(history, q)
            print(flush=True)
            print("s15 >> ", end="", flush=True)
            if s14.active_teammates:
                had_teammates = True
            elif had_teammates and not s14.BUS.peek("lead"):
                print("[all teammates shut down]", flush=True)
                had_teammates = False
            print(flush=True)
    finally:
        s14.SCHEDULER.stop()