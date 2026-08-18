"""harness.cli — python -m harness 入口。"""

import sys

# Windows 终端默认 GBK，强制 UTF-8 保证中文/emoji 输出正常
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from harness.agent import SYSTEM_PROMPT_TEMPLATE, agent_loop
from harness.config import load_config
from harness.llm import LLMClient
from harness.tools.base import set_workdir


def main():
    config = load_config()

    # 注册 5 个内置 hook（import 即注册）
    from harness.hooks import builtin  # noqa: F401

    set_workdir(config.workdir)
    llm = LLMClient(config)

    # ── Phase 2: 初始化 compactor ──
    from harness.context.compactor import ContextCompactor
    compactor = ContextCompactor(llm, config.model, config.transcript_dir, config.tool_results_dir)

    # ── Phase 2: 初始化 memory store + 注册 load_memory 工具 ──
    from harness.memory.store import MemoryStore
    memory_store = MemoryStore(config.memory_dir, llm, config.model)
    from harness.tools.memory import init_memory_store
    init_memory_store(memory_store)

    # ── Phase 2: 初始化 skill loader + 注册 load_skill 工具 ──
    from harness.tools.skill import init_skill_loader
    init_skill_loader(config.skills_dir)

    # ── Phase 2: 注册 task 工具（需要 config + llm）──
    from harness.tools.subagent import init_task_tool
    init_task_tool(config, llm)

    # ── Phase 2: 注册 todo_write 工具（import 即注册）──
    from harness.tools import planning  # noqa: F401

    # ── Phase 2: 注册 compact 工具 ──
    from harness.tools.pool import register_compact_handler
    register_compact_handler(lambda: "ok")  # compact 工具由 agent_loop 特殊处理

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
        history.append({"role": "user", "content": q})
        agent_loop(history, config, llm, compactor=compactor, memory_store=memory_store)
