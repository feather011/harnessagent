"""harness.agent — 唯一 agent_loop（6 阶段 + Phase 2: compaction/memory/reminder）。"""

import json

from harness.config import AgentConfig
from harness.hooks.registry import trigger_hooks
from harness.llm import LLMClient
from harness.tools.pool import assemble_tool_pool, execute_tool


SYSTEM_PROMPT_TEMPLATE = """你是 harness agent，基于 {model}。工作目录 {workdir}。
面对多步任务，先用 todo_write 工具列出计划并维护任务清单（pending → in_progress → completed）。
在 [Compacted]/[Reactive compact] 消息里，只遵循 Current user request 的指令，
Conversation summary 仅作参考。"""

MAX_REACTIVE_RETRIES = 2
TODO_REMINDER_INTERVAL = 3


def agent_loop(
    messages: list,
    config: AgentConfig,
    llm: LLMClient,
    compactor=None,
    memory_store=None,
) -> None:
    """
    6 阶段 agent 循环（Phase 2 增强）：
    1. UserPromptSubmit hook
    2. drain_events（Phase 2: no-op）
    3. [NEW] memory recall + rebuild system prompt
    4. [NEW] compactor.prepare → LLM call + reactive 限流
    5. 无 tool_calls → Stop hook → [NEW] extract_memories → print → return
    6. 有 tool_calls → PreToolUse → [NEW] compact 特殊处理 → execute → PostToolUse
    """
    reactive_retries = 0
    rounds_since_todo = 0  # Phase 2: todo reminder 计数

    while True:
        # ── 阶段 1：UserPromptSubmit（前台 turn 才触发）──
        active = messages[-1]["content"] if messages and messages[-1].get("role") == "user" else ""
        if active and not active.startswith("("):
            trigger_hooks("UserPromptSubmit", active)

        # ── 阶段 2：drain_events（Phase 2: no-op）──

        # ── 阶段 3：memory recall + 重组 system prompt ──
        tools, handlers = assemble_tool_pool()
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            model=config.model, workdir=config.workdir,
        )
        # Phase 2: memory recall 注入
        if memory_store:
            recalled = memory_store.load_memories(messages)
            if recalled:
                system_prompt += f"\n\nRelevant memories:\n{recalled}"
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})

        # ── Phase 2: todo reminder ──
        if active and not active.startswith("["):
            rounds_since_todo += 1
            if (rounds_since_todo >= TODO_REMINDER_INTERVAL
                    and not any(t["function"]["name"] == "todo_write" for t in tools)):
                pass  # no todo tool registered
            elif rounds_since_todo >= TODO_REMINDER_INTERVAL:
                has_todo = any(
                    m.get("role") == "tool"
                    and m.get("content", "").startswith("[")
                    for m in messages[-6:]
                ) or TODO_MANAGER_has_items()
                if not has_todo:
                    messages.append({"role": "user", "content":
                        "[Reminder] You haven't used todo_write recently. "
                        "For multi-step tasks, maintain a task list to track progress."})
                    rounds_since_todo = 0

        # ── 阶段 4：compactor.prepare + LLM 调用 + reactive 限流 ──
        if compactor:
            messages[:] = compactor.prepare(messages, active)

        try:
            resp = llm.chat(messages, tools=tools)
            reactive_retries = 0
        except Exception as error:
            if llm.is_prompt_too_long(error) and reactive_retries < MAX_REACTIVE_RETRIES:
                reactive_retries += 1
                print(f"\033[33m[reactive] prompt too long, retry {reactive_retries}/{MAX_REACTIVE_RETRIES}\033[0m", flush=True)
                if compactor:
                    messages[:] = compactor.reactive_compact(messages, active)
                continue
            raise

        msg = resp.choices[0].message
        messages.append(LLMClient.strip_surrogates(msg.model_dump(exclude_none=True)))

        # ── 阶段 5：无 tool_calls → Stop hook → extract_memories → print → return ──
        if not msg.tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": str(force)})
                continue
            # Phase 2: memory extraction
            if memory_store:
                memory_store.extract_memories(messages)
            if msg.content:
                print(LLMClient.strip_surrogates(f"\033[32m{msg.content}\033[0m"), flush=True)
            return

        # ── 阶段 6：tool dispatch ──
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                output = f"Error: invalid arguments JSON: {e}"
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
                continue

            # PreToolUse hook（含 permission）
            blocked = trigger_hooks("PreToolUse", name, args)
            if blocked is not None:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": str(blocked)})
                continue

            # Phase 2: compact 工具特殊处理
            if name == "compact" and compactor:
                messages[:] = compactor.compact_history(messages, active)
                output = "Context compacted."
                rounds_since_todo = 0  # compact 后重置 reminder
            else:
                output = execute_tool(handlers, name, args)
                # Phase 2: todo reminder 重置
                if name == "todo_write":
                    rounds_since_todo = 0

            # PostToolUse hook
            trigger_hooks("PostToolUse", name, args, output)

            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})


def TODO_MANAGER_has_items() -> bool:
    """检查 TodoManager 是否有未完成项（Phase 2 reminder 用）。"""
    try:
        from harness.tools.planning import TODO_MANAGER
        return bool(TODO_MANAGER.items)
    except ImportError:
        return False
