#!/usr/bin/env python3
"""s17 验收（不依赖 LLM，纯 wiring + MockAgentRunner + GoalController 单元逻辑）。

覆盖：4 组件 / GoalState 字段 / PromptGoalEvaluator truncate + 评估路径 /
GoalController set/inspect/clear/evaluate_after_turn（含 max_consecutive + defer）/
/goal 三变体解析 / 不动 s14/s15/s16 / agent_loop 含 Goal Stop hook。
"""
import inspect
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s14_mcp_plugin as s14
import s15_integrated_harness as s15
import s16_workflow_runtime as s16
import s17_goal_loop as s17

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


# ---------------- 1. 不动前置 ----------------
diff = subprocess.run(
    ["git", "diff", "--stat", "s14_mcp_plugin.py", "s15_integrated_harness.py", "s16_workflow_runtime.py"],
    cwd=ROOT, capture_output=True, text=True,
).stdout.strip()
check("s14/s15/s16 零改动", diff == "", f"diff={diff!r}")

# ---------------- 2. 4 组件存在 ----------------
check("GoalState dataclass", hasattr(s17, "GoalState"))
check("PromptGoalEvaluator 类", callable(s17.PromptGoalEvaluator))
check("GoalController 类", callable(s17.GoalController))
check("GoalDecision dataclass", hasattr(s17, "GoalDecision"))

# ---------------- 3. GoalState 字段 + 初始值 ----------------
st = s17.GoalState(condition="test condition")
check("GoalState 字段齐全",
      set(st.__dataclass_fields__.keys()) == {"condition", "status", "eval_count", "start_time", "latest_reason"})
check("GoalState.status 初始 pending", st.status == "pending")
check("GoalState.eval_count 初始 0", st.eval_count == 0)
check("GoalState.latest_reason 初始空", st.latest_reason == "")
check("GoalState.start_time 自动 > 0", st.start_time > 0)
check("GoalState.elapsed() 返 float > 0", isinstance(st.elapsed(), float) and st.elapsed() >= 0)

# ---------------- 4. PromptGoalEvaluator truncate ----------------
ev = s17.PromptGoalEvaluator()
check("小消息透传", "small" in ev._truncate_messages(
    [{"role": "user", "content": "small message"}])[0]["content"])
big = "x" * 10000
truncated = ev._truncate_messages([{"role": "user", "content": big}])
check("超大消息只留首尾（截断）",
      truncated[0]["content"].startswith("x" * ev.TRUNCATE_HEAD)
      and "...[truncated]..." in truncated[0]["content"]
      and truncated[0]["content"].endswith("x" * ev.TRUNCATE_TAIL))
# KEEP_RECENT: 25 条只保留最近 20
many = [{"role": "user", "content": f"m{i}"} for i in range(25)]
out = ev._truncate_messages(many)
check("KEEP_RECENT=20（25 条只留 20）", len(out) == 20 and out[-1]["content"] == "m24")

# ---------------- 5. PromptGoalEvaluator evaluate（含 MockAgentRunner） ----------------
# ok=True 路径
runner_ok = s16.MockAgentRunner({"the goal": {"ok": True, "reason": "done", "impossible": False}})
check_e1 = s17.PromptGoalEvaluator(evaluator := type("E", (), {"runner": runner_ok, "_truncate_messages": ev._truncate_messages})())
# 上面写法 hack——直接用 runner 实例化
ev_ok = s17.PromptGoalEvaluator(runner=runner_ok)
result_ok = ev_ok.evaluate("achieve the goal", [{"role": "user", "content": "x"}])
check("ok=true 路径", result_ok.get("ok") is True and result_ok.get("reason") == "done")
check("impossible 字段默认 False", result_ok.get("impossible") is False)

# impossible=True 路径
runner_imp = s16.MockAgentRunner({"the goal": {"ok": False, "reason": "impossible", "impossible": True}})
result_imp = s17.PromptGoalEvaluator(runner=runner_imp).evaluate("the goal", [])
check("impossible=true 路径", result_imp.get("impossible") is True and result_imp.get("ok") is False)

# runner 抛异常 → 返 ok=False + error 信息
class _Exploder(s16.AgentRunner):
    def run(self, prompt, schema=None):
        raise RuntimeError("model 502")
result_err = s17.PromptGoalEvaluator(runner=_Exploder()).evaluate("x", [])
check("runner 异常 → ok=False 带错误信息",
      result_err["ok"] is False and "RuntimeError" in result_err["reason"])

# 返非 dict → ok=False
runner_str = s16.MockAgentRunner({"x": "not a dict"})
result_str = s17.PromptGoalEvaluator(runner=runner_str).evaluate("x", [])
check("runner 返非 dict → ok=False 带信息",
      result_str["ok"] is False and "non-dict" in result_str["reason"])

# ---------------- 6. GoalController set/inspect/clear ----------------
gc = s17.GoalController(evaluator=type("E", (), {})())  # placeholder，evaluate 会抛
gc._evaluator = s17.PromptGoalEvaluator(runner=s16.MockAgentRunner({}))  # 用真 evaluator
check("set 后 state 非空", gc.set("achieve X") is not None and gc.inspect() is not None)
check("inspect 返 state 引用相等", gc.inspect() is gc.state)
gc.clear()
check("clear 后 state=None None", gc.inspect() is None)

# ---------------- 7. GoalController evaluate_after_turn（核心逻辑） ----------------
gc2 = s17.GoalController()
check("无 goal → pass", gc2.evaluate_after_turn([], False).action == "pass")

# ok=true → pass + state.status=completed
gc2.set("x")
gc2._evaluator = s17.PromptGoalEvaluator(runner=s16.MockAgentRunner({"cond": {"ok": True, "reason": "r", "impossible": False}}))
gc2._evaluator._truncate_messages = ev._truncate_messages
gc2.evaluator = gc2._evaluator  # type: ignore
d_pass = gc2.evaluate_after_turn([{"role":"user","content":"cond"}], False)
check("ok=true → pass", d_pass.action == "pass" and gc2.state.status == "completed")

# impossible=true → block
gc2.clear()
gc2.set("x")
gc2.evaluator = s17.PromptGoalEvaluator(runner=s16.MockAgentRunner({"cond": {"ok": False, "reason": "nope", "impossible": True}}))
gc2.evaluator._truncate_messages = ev._truncate_messages
d_imp = gc2.evaluate_after_turn([], False)
check("impossible=true → block", d_imp.action == "block")

# block → eval_count += 1
gc2.clear()
gc2.set("x")
gc2.evaluator = s17.PromptGoalEvaluator(runner=s16.MockAgentRunner({"cond": {"ok": False, "reason": "not done", "impossible": False}}))
gc2.evaluator._truncate_messages = ev._truncate_messages
d1 = gc2.evaluate_after_turn([], False)
check("block 路径第 1 次（eval_count=1）",
      gc2.state.eval_count == 1 and d1.action == "block")
d2 = gc2.evaluate_after_turn([], False)
check("第 2 次（eval_count=2）", gc2.state.eval_count == 2 and d2.action == "block")
d3 = gc2.evaluate_after_turn([], False)
check("第 3 次（eval_count=3）", gc2.state.eval_count == 3 and d3.action == "block")
d4 = gc2.evaluate_after_turn([], False)
check("第 4 次（eval_count=4）", gc2.state.eval_count == 4 and d4.action == "block")
d5 = gc2.evaluate_after_turn([], False)
check("第 5 次强制 pass（超 MAX_CONSECUTIVE_BLOCKS）", d5.action == "pass")
check("pass 时 reason surface 'exceeded'",
      "exceeded" in d5.reason or "Giving control" in d5.reason)

# defer：has_pending_async=True → defer（不评估）
gc2.clear()
gc2.set("x")
# evaluator 用 Exploder；defer 应该不调到 evaluator
gc2.evaluator = s17.PromptGoalEvaluator(runner=_Exploder())
d_defer = gc2.evaluate_after_turn([], has_pending_async=True)
check("后台在跑 → defer（不评估，不报错）", d_defer.action == "defer")
check("defer 不增 eval_count", gc2.state.eval_count == 0)

# ---------------- 8. /goal 三变体 ----------------
check("parse /goal 裸 → inspect", s17.parse_goal_command("/goal") == ("inspect", ""))
check("parse /goal status → inspect", s17.parse_goal_command("/goal status") == ("inspect", ""))
check("parse /goal view → inspect", s17.parse_goal_command("/goal view") == ("inspect", ""))
check("parse /goal clear → clear", s17.parse_goal_command("/goal clear") == ("clear", ""))
check("parse /goal stop → clear", s17.parse_goal_command("/goal stop") == ("clear", ""))
check("parse /goal cancel → clear", s17.parse_goal_command("/goal cancel") == ("clear", ""))
check("parse /goal set 'do X' → set",
      s17.parse_goal_command("/goal do X") == ("set", "do X"))
check("parse /goal 大小写变体 → set",
      s17.parse_goal_command("/Goal write tests") == ("set", "write tests"))
check("parse /GOAL 全大写 → set",
      s17.parse_goal_command("/GOAL build X") == ("set", "build X"))
check("parse 非 /goal → None", s17.parse_goal_command("hello") is None)
check("parse /goal set 保留 condition 大小写",
      s17.parse_goal_command("/goal Write Tabs In code") == ("set", "Write Tabs In code"))

# ---------------- 9. agent_loop 6+1 钩子点源码位置 ----------------
src = inspect.getsource(s17.agent_loop)
check("agent_loop 含 _hook_user_prompt_submit", "_hook_user_prompt_submit" in src)
check("agent_loop 含 drain_events", "drain_events" in src)
check("agent_loop 含 _compaction_step", "_compaction_step" in src)
check("agent_loop 含 _hook_permission", "_hook_permission" in src)
check("agent_loop 含 _hook_log_tool_call", "_hook_log_tool_call" in src)
check("agent_loop 含 _hook_stop", "_hook_stop" in src)
# Goal Stop hook
check("agent_loop 含 goal_controller.evaluate_after_turn",
      "goal_controller.evaluate_after_turn" in src)
check("agent_loop 含 has_pending_async 检查",
      "has_pending_async" in src)
check("agent_loop block 路径 'continue'",
      'continue' in src and '"block"' in src)

# ---------------- 10. has_pending_async 覆盖 bg + workflow ----------------
check("has_pending_async 是函数", callable(s17.has_pending_async))
check("无任务 → False", s17.has_pending_async() is False)

# 模拟 background running
fake_bg_id = "bg_test"
s14.BACKGROUND.tasks[fake_bg_id] = {"tool_call_id": "x", "command": "ls", "status": "running"}
check("background running → True", s17.has_pending_async() is True)
del s14.BACKGROUND.tasks[fake_bg_id]
check("background 清掉 → False（若无 workflow running）",
      s17.has_pending_async() is False)

# 模拟 workflow running
fake_wf = s16.LocalWorkflowTask(run_id="run_fake", workflow_name="test")
fake_wf.status = s16.TaskStatus.RUNNING
with s16._tasks_lock:
    s16.scheduler_tasks[fake_wf.run_id] = fake_wf
check("workflow running → True", s17.has_pending_async() is True)
del s16.scheduler_tasks[fake_wf.run_id]

# ---------------- 11. /goal set 立即触发工作（教材要求） ----------------
# 检查 __main__ 源码：set 分支后紧跟 history.append + agent_loop 调用
main_src = open(s17.__file__, encoding="utf-8").read()
check("/goal set 分支立即 history.append（不只 set state）",
      'kind == "set"' in main_src and 'history.append({"role": "user", "content": arg})' in main_src)
check("/goal set 后立即调 agent_loop", 'agent_loop(history, arg, goal_controller)' in main_src)

print(f"\n=== s17 验收 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)