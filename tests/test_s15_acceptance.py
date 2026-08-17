#!/usr/bin/env python3
"""s15 验收（不依赖 API，纯 wiring 验证）。

覆盖：
- 6 钩子点存在 + 4 个薄包装 + 2 个直接函数
- assemble_system_prompt 在 s14 4 段基础上扩展 3 段（identity/skills/memory/mcp + tools/workspace/event_tag）
- drain_events 收口 3 个事件源（cron / background / team）
- 不动 s14（git diff 校验）
- 事件源格式严格沿用 s14（[Scheduled] / <task_notification> / [Team events]）
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s14_mcp_plugin as s14
import s15_integrated_harness as s15

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


# ============================================================ 1. 不动 s14 ============================================================
diff = subprocess.run(
    ["git", "diff", "--stat", "s14_mcp_plugin.py"], cwd=ROOT, capture_output=True, text=True,
).stdout.strip()
check("s14 零改动（git diff --stat s14_mcp_plugin.py 为空）", diff == "", f"diff={diff!r}")

# ============================================================ 2. 6 钩子点存在 + 4 薄包装 + 2 直接函数 ============================================================
check("_hook_user_prompt_submit 是函数", callable(s15._hook_user_prompt_submit))
check("_hook_permission 是函数", callable(s15._hook_permission))
check("_hook_log_tool_call 是函数", callable(s15._hook_log_tool_call))
check("_hook_stop 是函数", callable(s15._hook_stop))
check("drain_events 是函数", callable(s15.drain_events))
check("_compaction_step 是函数", callable(s15._compaction_step))

# 4 薄包装确实委托 s14.trigger_hooks（callable 后看 __wrapped__ 或源码）
import inspect
src_pps = inspect.getsource(s15._hook_user_prompt_submit)
src_perm = inspect.getsource(s15._hook_permission)
src_log = inspect.getsource(s15._hook_log_tool_call)
src_stop = inspect.getsource(s15._hook_stop)
check("hook1 委托 trigger_hooks UserPromptSubmit", "trigger_hooks" in src_pps and "UserPromptSubmit" in src_pps)
check("hook4 委托 trigger_hooks PreToolUse", "trigger_hooks" in src_perm and "PreToolUse" in src_perm)
check("hook5 委托 trigger_hooks PostToolUse", "trigger_hooks" in src_log and "PostToolUse" in src_log)
check("hook6 委托 trigger_hooks Stop", "trigger_hooks" in src_stop and "Stop" in src_stop)

# 2 直接函数调 s14.COMPACTOR.prepare
check("drain_events 直接函数（不是 HOOK 注册）",
      "register_hook" not in inspect.getsource(s15.drain_events))
check("_compaction_step 直接函数", "s14.COMPACTOR.prepare" in inspect.getsource(s15._compaction_step))

# ============================================================ 3. assemble_system_prompt 6 段 ============================================================
prompt = s15.assemble_system_prompt({"tools": [{"function": {"name": "bash", "description": "Run shell"}}]})
check("含 identity 段（沿用 s14）", "你是一个 coding agent" in prompt)
check("含 skills 段（沿用 s14）", "Skills available:" in prompt or "load_skill" in prompt)
check("含 memory 段（沿用 s14）", "Memory catalog" in prompt or "load_memory" in prompt)
check("含 mcp 段（沿用 s14）", "MCP" in prompt or "connect_mcp" in prompt)
check("s15 新增 tools 段", "Available tools" in prompt and "bash" in prompt)
check("s15 新增 workspace 段", "Working directory:" in prompt and str(s15.WORKDIR) in prompt)
check("s15 新增 event_tag 段（解释 [Scheduled]/<task_notification>/[Team events]）",
      "[Scheduled]" in prompt and "<task_notification>" in prompt and "[Team events]" in prompt)

# 事件 tag 段是 s15 真正新增的内容，s14 没有
s14_prompt = s14.build_system_prompt()
check("s15 prompt 含 s14 prompt 全部内容",
      all(line in prompt for line in s14_prompt.split("\n\n") if line.strip()))

# ============================================================ 4. drain_events 收口 3 事件源 ============================================================
# 4.1 cron：注入 [Scheduled]
# 4.1 cron：手动 push 进 cron_queue（schedule 后立即 consume 不会返 job——s14 pending_delivery+last_fired 设计）
job = s14.SCHEDULER.schedule("0 0 * * *", "test-cron-prompt")
with s14.SCHEDULER.lock:
    s14.SCHEDULER.cron_queue.append(job)
events = s15.drain_events()
check("drain 注入 cron → [Scheduled] 格式（沿用 s14）",
      any("[Scheduled]" in ev["content"] and ev["role"] == "user" and "test-cron-prompt" in ev["content"]
          for ev in events))

# 4.2 background：注入 <task_notification>
def fake_run_bg():
    bg = s14.BACKGROUND
    bg.start("echo drain-bg-test")
    deadline = time.time() + 5
    while time.time() < deadline:
        if bg._ready:
            return
        time.sleep(0.05)
threading.Thread(target=fake_run_bg, daemon=True).start()
time.sleep(2.5)  # 等 echo 完成
events2 = s15.drain_events()
check("drain 注入 background → <task_notification> XML 完整格式",
      any("<task_notification>" in ev["content"] and "<task_id>" in ev["content"]
          and "<status>completed</status>" in ev["content"]
          for ev in events2))

# 4.3 team：注入 [Team events]
s14.active_teammates["fake-teammate"] = "working"
s14.BUS.send("fake-teammate", "lead", "test result payload", "result")
events3 = s15.drain_events()
check("drain 注入 team → [Team events] 格式",
      any("[Team events]" in ev["content"] and "fake-teammate" in ev["content"]
          and "result" in ev["content"] for ev in events3))

# drain_events 无事件时返空列表
check("drain_events 无事件返空", s15.drain_events() == [])

# 清理
s14.active_teammates.pop("fake-teammate", None)

# ============================================================ 5. 事件源格式严格沿用 s14（grep 源码） ============================================================
s15_src = open(s15.__file__, encoding="utf-8").read()
check("s15 cron 格式字符串 = s14", "[Scheduled]" in s15_src and "{job.prompt}" in s15_src)
check("s15 调 s14.collect_background_results（不是新发明）",
      "s14.collect_background_results" in s15_src)
check("s15 调 s14.format_team_events + s14.consume_lead_inbox",
      "s14.format_team_events" in s15_src and "s14.consume_lead_inbox" in s15_src)

# ============================================================ 6. agent_loop 6 钩子点源码位置 ============================================================
agent_loop_src = inspect.getsource(s15.agent_loop)
check("钩子1 UserPromptSubmit 在 agent_loop 调用", "_hook_user_prompt_submit(active_request)" in agent_loop_src)
check("钩子2 drain_events 在 agent_loop 调用", "drain_events()" in agent_loop_src)
check("钩子3 _compaction_step 在 agent_loop 调用", "_compaction_step(messages, active_request)" in agent_loop_src)
check("钩子4 _hook_permission 在 agent_loop 调用", "_hook_permission(name, args)" in agent_loop_src)
check("钩子5 _hook_log_tool_call 在 agent_loop 调用", "_hook_log_tool_call(name, args, output)" in agent_loop_src)
check("钩子6 _hook_stop 在 agent_loop 调用", "_hook_stop(messages)" in agent_loop_src)

# ============================================================ 7. 沿用 s14 BUILTIN_TOOLS 不重写 ============================================================
check("s15 没改 BUILTIN_TOOLS（仍 28 个，含 compact 但无 handler）", len(s14.BUILTIN_TOOLS) == 28)
check("s15 没改 BUILTIN_HANDLERS（27 个，compact 不在 handler 表里——s14 循环特判）",
      len(s14.BUILTIN_HANDLERS) == 27 and "compact" not in s14.BUILTIN_HANDLERS)

# ============================================================ 8. assemble_tool_pool 沿用 ============================================================
tools, handlers = s14.assemble_tool_pool()
check("assemble_tool_pool 仍可独立调用", len(tools) >= 28)
check("assemble_tool_pool 返回 (list, dict)", isinstance(tools, list) and isinstance(handlers, dict))

print(f"\n=== s15 验收 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)