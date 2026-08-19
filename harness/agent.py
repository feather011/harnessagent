"""harness.agent — 唯一 agent_loop（6 阶段 + Phase 2: compaction/memory + Phase 3: cron/background）。"""

import json

from harness.config import AgentConfig
from harness.hooks.registry import trigger_hooks
from harness.llm import LLMClient
from harness.tools.pool import assemble_tool_pool, execute_tool


SYSTEM_PROMPT_TEMPLATE = """你是 harness agent，基于 {model}。工作目录 {workdir}。
面对多步任务，先用 todo_write 工具列出计划并维护任务清单（pending → in_progress → completed）。
对需要依赖跟踪的任务，用 create_task/claim_task/complete_task 管理。
对耗时的独立命令，设置 run_in_background=true 后台执行。
对需要定时执行的工作，用 schedule_cron 安排。
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
    background=None,
    scheduler=None,
    bus=None,
    teammates=None,
    task_store=None,
    goal_controller=None,
    on_token=None,
    on_tool_start=None,
    on_tool_done=None,
) -> None:
    """
    6 阶段 agent 循环（Phase 3 增强）：
    1. UserPromptSubmit hook
    2. drain cron + background 事件
    3. memory recall + rebuild system prompt
    4. compactor.prepare → LLM call + reactive 限流 + cron ack/restore
    5. 无 tool_calls → Stop hook → extract_memories → print → return
    6. tool dispatch（含 bash run_in_background 拦截）
    """
    reactive_retries = 0
    rounds_since_todo = 0
    pending_cron_jobs = []  # Phase 3: drain 出的 cron jobs，用于 ack/restore

    while True:
        # ── 阶段 1：UserPromptSubmit ──
        active = messages[-1]["content"] if messages and messages[-1].get("role") == "user" else ""
        if active and not active.startswith("("):
            trigger_hooks("UserPromptSubmit", active)

        # ── 阶段 2：drain cron + background 事件 ──
        pending_cron_jobs = []
        if scheduler:
            pending_cron_jobs = scheduler.consume_queue()
            for job in pending_cron_jobs:
                messages.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
        if background:
            for notif in background.collect():
                messages.append({"role": "user", "content": notif})

        # Phase 4: drain team mailbox（lead inbox）
        if bus and bus.peek("lead"):
            team_msgs = bus.read_inbox("lead")
            for tm in team_msgs:
                sender = tm.get("from", "?")
                msg_type = tm.get("type", "message")
                content = tm.get("content", "")
                messages.append({"role": "user", "content":
                    f"[Team events] {sender} ({msg_type}): {content}"})

        # ── 阶段 3：memory recall + 重组 system prompt ──
        tools, handlers = assemble_tool_pool()
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            model=config.model, workdir=config.workdir,
        )
        if memory_store:
            recalled = memory_store.load_memories(messages)
            if recalled:
                system_prompt += f"\n\nRelevant memories:\n{recalled}"
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})

        # ── todo reminder ──
        if active and not active.startswith("["):
            rounds_since_todo += 1
            if rounds_since_todo >= TODO_REMINDER_INTERVAL:
                from harness.tools.planning import TODO_MANAGER
                has_todo = any(
                    m.get("role") == "tool" and m.get("content", "").startswith("[")
                    for m in messages[-6:]
                ) or bool(TODO_MANAGER.items)
                if not has_todo:
                    messages.append({"role": "user", "content":
                        "[Reminder] You haven't used todo_write recently. "
                        "For multi-step tasks, maintain a task list to track progress."})
                    rounds_since_todo = 0

        # ── 阶段 4：compactor + LLM + cron ack/restore ──
        if compactor:
            messages[:] = compactor.prepare(messages, active)

        try:
            if on_token:
                # Streaming mode（TUI）
                collected_content = []
                collected_tool_calls = {}  # index → {id, name, arguments}
                stream = llm.stream(messages, tools=tools)
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if not delta:
                        continue
                    if delta.content:
                        collected_content.append(delta.content)
                        on_token(delta.content)
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in collected_tool_calls:
                                collected_tool_calls[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                            if tc.id:
                                collected_tool_calls[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    collected_tool_calls[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    collected_tool_calls[idx]["arguments"] += tc.function.arguments
                # 构造 resp-like 对象
                content = "".join(collected_content)
                tc_list = []
                for idx in sorted(collected_tool_calls):
                    tc = collected_tool_calls[idx]
                    tc_obj = type("TC", (), {
                        "id": tc["id"],
                        "function": type("F", (), {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        })(),
                    })()
                    tc_list.append(tc_obj)

                def _model_dump(msg_content, msg_tc_list, **kw):
                    result = {"role": "assistant", "content": msg_content}
                    if msg_tc_list:
                        result["tool_calls"] = [{
                            "id": t.id,
                            "type": "function",
                            "function": {"name": t.function.name, "arguments": t.function.arguments},
                        } for t in msg_tc_list]
                    return result

                msg_obj = type("Msg", (), {
                    "content": content or None,
                    "tool_calls": tc_list if tc_list else None,
                    "model_dump": lambda self, **kw: _model_dump(content, tc_list, **kw),
                })()
                resp = type("Resp", (), {"choices": [type("C", (), {"message": msg_obj})()]})()
            else:
                # Non-streaming mode（REPL）
                resp = llm.chat(messages, tools=tools)
            reactive_retries = 0
            if scheduler and pending_cron_jobs:
                scheduler.acknowledge(pending_cron_jobs)
        except Exception as error:
            if scheduler and pending_cron_jobs:
                scheduler.restore(pending_cron_jobs)
            if llm.is_prompt_too_long(error) and reactive_retries < MAX_REACTIVE_RETRIES:
                reactive_retries += 1
                print(f"\033[33m[reactive] prompt too long, retry {reactive_retries}/{MAX_REACTIVE_RETRIES}\033[0m", flush=True)
                if compactor:
                    messages[:] = compactor.reactive_compact(messages, active)
                continue
            raise

        msg = resp.choices[0].message
        messages.append(LLMClient.strip_surrogates(msg.model_dump(exclude_none=True)))

        # ── 阶段 5：无 tool_calls → Stop → extract_memories → print → return ──
        if not msg.tool_calls:
            # Phase 5: Goal Stop hook（在 _hook_stop 之前）
            if goal_controller:
                has_pending = _has_pending_async(background)
                decision = goal_controller.evaluate_after_turn(messages, has_pending)
                if decision.action == "block":
                    messages.append({"role": "user", "content":
                        f"[Goal block #{goal_controller.state.eval_count}] {decision.reason}"})
                    continue
                # pass / defer → 走 _hook_stop → return

            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": str(force)})
                continue
            if memory_store:
                memory_store.extract_memories(messages)
            if msg.content:
                if not on_token:
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

            # PreToolUse hook
            blocked = trigger_hooks("PreToolUse", name, args)
            if blocked is not None:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": str(blocked)})
                continue

            # Phase 3: bash run_in_background 拦截
            if on_tool_start:
                on_tool_start(name, args, call.id)
            import time as _time
            _t0 = _time.time()

            if (name == "bash" and args.get("run_in_background") is True
                    and background is not None):
                command = args.get("command", "")
                from harness.permission.deny import check_deny_list
                if check_deny_list(command):
                    output = "Error: Dangerous command blocked"
                else:
                    try:
                        task_id = background.start(command)
                        output = (f"[Background task {task_id} started] "
                                  "Result will be collected on a later turn.")
                    except Exception as e:
                        output = f"Error: {type(e).__name__}: {e}"
            # Phase 2: compact 工具特殊处理
            elif name == "compact" and compactor:
                messages[:] = compactor.compact_history(messages, active)
                output = "Context compacted."
                rounds_since_todo = 0
            else:
                output = execute_tool(handlers, name, args)
                if name == "todo_write":
                    rounds_since_todo = 0

            if on_tool_done:
                on_tool_done(name, args, output, _time.time() - _t0, call.id)
            trigger_hooks("PostToolUse", name, args, output)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

            # Phase 4: spawn 后立即 return（lead 让出，teammate 自主工作）
            if name == "spawn_teammate" and not output.startswith("Error"):
                if msg.content:
                    print(LLMClient.strip_surrogates(f"\033[32m{msg.content}\033[0m"), flush=True)
                return


def _has_pending_async(background=None) -> bool:
    """双查：background tasks running + workflow tasks running。"""
    # Background tasks
    if background:
        bg_tasks = getattr(background, "tasks", {})
        if any(getattr(t, "status", "") == "running" for t in bg_tasks.values()):
            return True
    # Workflow tasks
    try:
        from harness.workflow.task import scheduler_tasks, TaskStatus
        if any(t.status == TaskStatus.RUNNING for t in scheduler_tasks.values()):
            return True
    except ImportError:
        pass
    return False
