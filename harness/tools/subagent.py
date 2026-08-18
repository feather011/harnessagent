"""harness.tools.subagent — task 工具（独立子 agent）。"""

import json

from harness.tools.pool import register_tool, assemble_tool_pool, execute_tool, TASK_SCHEMA
from harness.llm import LLMClient

SUB_SYSTEM = ("你是一个子 agent（subagent）。专注完成交给你的单一子任务，直接干活，不要解释。"
              "完成后返回简洁的最终答案。")

MAX_TURNS = 30


def _make_run_task(config, llm):
    """工厂：返回闭包 run_task(prompt)，捕获 config + llm。"""

    def run_task(prompt: str) -> str:
        """用全新 context 跑子 agent，30 轮上限，返回 final text。"""
        print("\033[35m[Subagent started]\033[0m", flush=True)
        tools, handlers = assemble_tool_pool()
        # 子 agent 不含 task 工具（禁止二级 delegation）
        sub_tools = [t for t in tools if t["function"]["name"] != "task"]
        sub_handlers = {k: v for k, v in handlers.items() if k != "task"}

        messages = [
            {"role": "system", "content": SUB_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        for _ in range(MAX_TURNS):
            resp = llm.chat(messages, tools=sub_tools)
            msg = resp.choices[0].message
            messages.append(LLMClient.strip_surrogates(msg.model_dump(exclude_none=True)))

            if not msg.tool_calls:
                print("\033[35m[Subagent done]\033[0m", flush=True)
                return msg.content or "(no summary)"

            for call in msg.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments)
                except json.JSONDecodeError as e:
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": f"Error: invalid arguments JSON: {e}"})
                    continue
                output = execute_tool(sub_handlers, name, args)
                print(f"  \033[90m[sub] {name}: {str(output)[:100]}\033[0m", flush=True)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

        print("\033[35m[Subagent stopped]\033[0m", flush=True)
        return "Subagent stopped after 30 turns without a final answer."

    return run_task


# Deferred registration: cli.py calls init_task_tool(config, llm) after config is ready
_TASK_CONFIG = {"config": None, "llm": None}


def init_task_tool(config, llm):
    """由 cli.py 调用，注册 task 工具。"""
    _TASK_CONFIG["config"] = config
    _TASK_CONFIG["llm"] = llm
    run_task = _make_run_task(config, llm)
    register_tool(TASK_SCHEMA, run_task)
