"""harness.cli — python -m harness 入口。"""

import atexit
import sys

# Windows 终端默认 GBK，强制 UTF-8 保证中文/emoji 输出正常
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from harness.agent import SYSTEM_PROMPT_TEMPLATE, agent_loop
from harness.config import load_config
from harness.llm import LLMClient
from harness.tools.base import set_workdir


def parse_goal_command(q: str) -> tuple[str, str] | None:
    """返 ('set', condition) / ('clear', '') / ('inspect', '') / None。"""
    stripped = q.strip()
    if not stripped.lower().startswith("/goal"):
        return None
    rest = stripped[len("/goal"):].strip()
    if not rest or rest.lower() in ("status", "show", "view", "inspect"):
        return ("inspect", "")
    if rest.lower() in ("clear", "stop", "off", "reset", "none", "cancel"):
        return ("clear", "")
    return ("set", rest)


def main():
    config = load_config()

    # 注册 5 个内置 hook（import 即注册）
    from harness.hooks import builtin  # noqa: F401

    set_workdir(config.workdir)
    llm = LLMClient(config)

    # ── Phase 2: compactor ──
    from harness.context.compactor import ContextCompactor
    compactor = ContextCompactor(llm, config.model, config.transcript_dir, config.tool_results_dir)

    # ── Phase 2: memory ──
    from harness.memory.store import MemoryStore
    memory_store = MemoryStore(config.memory_dir, llm, config.model)
    from harness.tools.memory import init_memory_store
    init_memory_store(memory_store)

    # ── Phase 2: skill ──
    from harness.tools.skill import init_skill_loader
    init_skill_loader(config.skills_dir)

    # ── Phase 2: task (subagent) ──
    from harness.tools.subagent import init_task_tool
    init_task_tool(config, llm)

    # ── Phase 2: todo ──
    from harness.tools import planning  # noqa: F401

    # ── Phase 2: compact ──
    from harness.tools.pool import register_compact_handler
    register_compact_handler(lambda: "ok")

    # ── Phase 3: task store ──
    from harness.tools.tasks import init_task_store
    init_task_store(config.workdir / ".tasks")
    from harness.tools.tasks import TASK_STORE

    # ── Phase 3: background manager ──
    from harness.background.manager import BACKGROUND

    # ── Phase 3: cron scheduler ──
    from harness.tools.scheduler import init_scheduler
    init_scheduler(config.workdir / ".scheduled_tasks.json")
    from harness.tools.scheduler import SCHEDULER

    # ── Phase 4: MessageBus + team tools + worktree + MCP ──
    from harness.teams.bus import MessageBus
    bus = MessageBus(config.workdir / ".mailboxes")

    from harness.tools.teams import init_team_tools, TEAMMATES
    init_team_tools(config, llm, bus, TASK_STORE)

    from harness.tools.worktree import init_worktree_tool
    init_worktree_tool(config)

    from harness.tools.mcp import init_mcp_tools
    init_mcp_tools()

    # ── Phase 5: Workflow + Goal ──
    from harness.tools.workflow import init_workflow_tools
    init_workflow_tools(config)

    from harness.workflow.runner import RealAgentRunner
    from harness.goal.evaluator import PromptGoalEvaluator
    from harness.goal.controller import GoalController
    runner = RealAgentRunner(config)
    evaluator = PromptGoalEvaluator(runner)
    goal_controller = GoalController(evaluator)

    # atexit: 停止 daemon 线程 + 杀后台进程
    atexit.register(SCHEDULER.stop)
    atexit.register(BACKGROUND.stop_all)

    system_content = SYSTEM_PROMPT_TEMPLATE.format(model=config.model, workdir=config.workdir)
    history = [{"role": "system", "content": system_content}]

    print(f"harness agent ({config.model}) | q 退出", flush=True)

    while True:
        try:
            q = input("\nharness >> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q or q in ("q", "exit"):
            break

        # Phase 5: /goal 解析
        goal_kind = parse_goal_command(q)
        if goal_kind:
            kind, arg = goal_kind
            if kind == "inspect":
                state = goal_controller.inspect()
                if state:
                    print(f"\033[36mGoal: {state.condition}\nStatus: {state.status} "
                          f"(eval #{state.eval_count}, elapsed {state.elapsed():.1f}s)\033[0m", flush=True)
                else:
                    print("\033[36mNo active goal.\033[0m", flush=True)
                continue
            if kind == "clear":
                goal_controller.clear()
                print("\033[36mGoal cleared.\033[0m", flush=True)
                continue
            if kind == "set":
                goal_controller.set(arg)
                print(f"\033[36mGoal set: {arg}\033[0m", flush=True)
                history.append({"role": "user", "content": arg})
                agent_loop(history, config, llm, compactor=compactor,
                           memory_store=memory_store, background=BACKGROUND, scheduler=SCHEDULER,
                           bus=bus, teammates=TEAMMATES, task_store=TASK_STORE,
                           goal_controller=goal_controller)
                continue

        history.append({"role": "user", "content": q})
        agent_loop(history, config, llm, compactor=compactor,
                   memory_store=memory_store, background=BACKGROUND, scheduler=SCHEDULER,
                   bus=bus, teammates=TEAMMATES, task_store=TASK_STORE,
                   goal_controller=goal_controller)
