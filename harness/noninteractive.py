"""harness.noninteractive — 非交互 JSONL 入口（供 Mini DeerFlow HarnessRunner 调用）。

用法（由父进程以参数数组启动，绝不经 shell 拼接）：

    python -m harness.noninteractive --task-id <id> --prompt-file <path> \\
        [--max-tool-calls 50]

协议（stdout 专用于此，每行一个 JSON；日志一律走 stderr）：

    {"type": "started",    "task_id", "model", "workdir"}
    {"type": "tool_start", "task_id", "seq", "name", "args_preview"}
    {"type": "tool_done",  "task_id", "seq", "name", "output", "output_truncated", "duration_s"}
    {"type": "assistant",  "task_id", "content"}
    {"type": "done",       "task_id", "tool_calls", "tool_calls_completed"}
    {"type": "error",      "task_id", "reason"}

终态只有 `done` 与 `error` 两种，**每个进程至多一个终态**。
`done.tool_calls` = 尝试总数（含被权限拒绝的）；`tool_calls_completed` = 成功执行数。

退出码：0 = done；1 = agent 运行失败或超出工具调用上限；
2 = 启动失败（配置缺失、prompt 未给等）。

与 REPL（cli.main）的差异：

- **不 import** teams / cron / background / subagent / worktree /
  workflow 任何模块——非交互模式刻意只开 5 个内置工具
  （bash / read_file / write_file / edit_file / glob）。
- 权限第三闸 `prompt_user=False`：需要人工审批的操作直接拒绝，
  **绝不调用 input()**（父进程没有 stdin 可供审批）。
- agent_loop 的 stdout 打印被重定向到 stderr；协议事件写原始 stdout。
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Windows 终端默认 GBK；协议与日志都强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

_REAL_STDOUT = sys.stdout

TOOL_OUTPUT_EVENT_CHARS = 4000
ARGS_PREVIEW_CHARS = 200

# 超时统一由父进程（HarnessRunner._supervise）控制：超过 deadline 后
# taskkill /T /F 强制终止整个进程树，然后父进程生成 timeout 终态。
# 子进程本身不做进程级超时（无法安全地强制中断 Python 线程）。


class ToolCallLimitExceeded(Exception):
    """工具调用次数超过 --max-tool-calls 上限。"""


def _emit(payload: dict) -> None:
    """向协议流写一行 JSON（唯一写 stdout 的出口）。"""
    _REAL_STDOUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _REAL_STDOUT.flush()


def _install_noninteractive_hooks(counters: dict, max_calls: int) -> None:
    """注册非交互模式的 hook。

    Hook 分工：
    - PreToolDispatch：在 JSON 解析之前触发，负责计数（无论解析成功或失败都计数）
    - PreToolUse：在 JSON 解析之后触发，负责权限检查
    """
    from harness.hooks.registry import register_hook

    def _dispatch_hook(name: str, raw_arguments: str):
        # 计数：每次工具调用（无论是否通过权限检查）都计数。
        # 拒绝计数也是有效的安全预算：模型反复请求被拒工具
        # 仍然消耗调用额度直到抛出 ToolCallLimitExceeded。
        counters["total_calls"] += 1
        counters["seq"] += 1
        if counters["total_calls"] > max_calls:
            raise ToolCallLimitExceeded(
                f"tool call limit exceeded ({max_calls})"
            )
        # PreToolDispatch 只负责计数和限流，不负责权限检查
        return None

    def _log_hook(name: str, raw_arguments: str) -> None:
        preview = str(raw_arguments)[:80]
        print(f"[noninteractive] pre {name}({preview})", file=sys.stderr, flush=True)

    # PreToolDispatch 在解析前触发：计数 + 日志
    register_hook("PreToolDispatch", _dispatch_hook)
    register_hook("PreToolDispatch", _log_hook)

    # PreToolUse 在解析后触发：权限检查
    # prompt_user=False（第3闸 ask_user 直接拒绝），仍保留 deny_list（第1闸）和 rules（第2闸）
    def _permission_hook(name: str, args: dict):
        from harness.permission import check_permission
        from harness.tools.base import get_workdir
        return check_permission(name, args, get_workdir(), prompt_user=False)

    register_hook("PreToolUse", _permission_hook)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def main() -> None:
    parser = argparse.ArgumentParser(prog="harness.noninteractive")
    parser.add_argument("--task-id", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt", help="任务提示（直接传参）")
    group.add_argument("--prompt-file", help="从文件读取任务提示（UTF-8）")
    parser.add_argument("--max-tool-calls", type=int, default=50)
    args = parser.parse_args()

    task_id = args.task_id

    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            _emit({"type": "error", "task_id": task_id,
                   "reason": f"prompt-file unreadable: {exc}"})
            sys.exit(2)
    else:
        prompt = (args.prompt or "").strip()

    if not prompt:
        _emit({"type": "error", "task_id": task_id, "reason": "empty prompt"})
        sys.exit(2)

    # 配置加载失败（缺 MIMO_API_KEY 等）→ 启动失败终态
    # 非交互 fake 模式：跳过真实 API，直接用脚本化 LLM（供测试用）
    if os.getenv("HARNESS_USE_FAKE_LLM") == "1":
        from harness.llm import FakeLLMClient, AgentConfig
        config = AgentConfig(api_key="fake", model="fake")
        llm = FakeLLMClient(config)
    else:
        try:
            from harness.config import load_config
            config = load_config()
        except Exception as exc:  # noqa: BLE001 - 启动期任何错误都归为 launch 失败
            _emit({"type": "error", "task_id": task_id,
                   "reason": f"config: {type(exc).__name__}: {exc}"})
            sys.exit(2)
        from harness.llm import LLMClient
        llm = LLMClient(config)

    # 此后 sys.stdout = stderr：agent_loop / hook 的 print 全部走日志流
    sys.stdout = sys.stderr

    from harness.agent import agent_loop
    from harness.llm import LLMClient as _LLM
    from harness.tools.base import set_workdir

    workdir = Path.cwd().resolve()
    set_workdir(workdir)
    counters = {"total_calls": 0, "seq": 0}
    completed_calls = {"n": 0}
    _install_noninteractive_hooks(counters, args.max_tool_calls)

    _emit({"type": "started", "task_id": task_id,
           "model": config.model, "workdir": str(workdir)})

    def on_tool_start(name: str, tool_args: dict, call_id) -> None:
        # total_calls 已由 _counting_permission_hook 计入，这里只发协议事件
        preview, _ = _truncate(json.dumps(tool_args, ensure_ascii=False),
                               ARGS_PREVIEW_CHARS)
        _emit({"type": "tool_start", "task_id": task_id,
               "seq": counters["seq"], "name": name, "args_preview": preview})

    def on_tool_done(name: str, tool_args: dict, output, duration_s, call_id) -> None:
        completed_calls["n"] += 1
        text, truncated = _truncate(str(output), TOOL_OUTPUT_EVENT_CHARS)
        payload = {
            "type": "tool_done",
            "task_id": task_id,
            "seq": counters["seq"],
            "name": name,
            "output": text,
            "output_truncated": truncated,
            "duration_s": round(duration_s, 3),
        }
        # 对于 bash，发送命令预览（500 字符截断）+ 截断标记
        if name == "bash" and isinstance(tool_args, dict):
            cmd = str(tool_args.get("command", ""))
            payload["command_preview"] = cmd[:500]
            payload["command_truncated"] = len(cmd) > 500
        _emit(payload)

    messages = [{"role": "user", "content": prompt}]

    try:
        agent_loop(
            messages, config, llm,
            on_tool_start=on_tool_start,
            on_tool_done=on_tool_done,
        )
    except ToolCallLimitExceeded as exc:
        _emit({"type": "error", "task_id": task_id,
               "reason": str(exc), "tool_calls": counters["total_calls"]})
        sys.exit(1)
    except KeyboardInterrupt:
        _emit({"type": "error", "task_id": task_id, "reason": "interrupted"})
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - 任何运行期异常都以 error 终态收口
        _emit({"type": "error", "task_id": task_id,
               "reason": f"{type(exc).__name__}: {exc}"})
        sys.exit(1)

    # 正常返回：取最后一条 assistant 消息作为最终回答
    final_answer = ""
    for message in reversed(messages):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            final_answer = _LLM.strip_surrogates(message.get("content") or "")
            break

    _emit({"type": "assistant", "task_id": task_id, "content": final_answer})
    _emit({"type": "done", "task_id": task_id,
           "tool_calls": counters["total_calls"],
           "tool_calls_completed": completed_calls["n"]})
    sys.exit(0)


if __name__ == "__main__":
    main()
