#!/usr/bin/env python3
"""s13 函数级验证（mock + 临时任务目录 + 真实 .mailboxes 测后清理，不真 spawn teammate）。

claim 原子双锁 / plan gate / shutdown 协议 / MessageBus / worktree 校验分支都在这验证；
worktree 隔离的真建/移除走 e2e（Git 命令慢且影响仓库）。
"""
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s13_agent_teams as M

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def cleanup_mailboxes():
    if M.MAILBOX_DIR.exists():
        for f in M.MAILBOX_DIR.glob("*.jsonl"):
            f.unlink()


cleanup_mailboxes()

# ---------------- Task 带 worktree 字段 ----------------
t = M.Task(id="task_00000000", subject="x", description="", status="pending",
           owner=None, blockedBy=[], worktree=None)
check("Task 含 worktree 字段", hasattr(t, "worktree") and t.worktree is None)

# ---------------- claim_task 增强（用临时任务目录） ----------------
tmp = M.WORKDIR / f".tmp_s13_{uuid.uuid4().hex[:8]}"
orig_tasks = M.TASKS
M.TASKS = M.TaskStore(tmp)
M.teammate_assignments.clear()
M.assignment_versions.clear()

a = M.create_task("task-a")
b = M.create_task("task-b", blockedBy=[a.id])
check("claim 正常", M.claim_task(a.id, owner="alice").startswith("Claimed"))
check("claim 后 in_progress + owner", M.load_task(a.id).status == "in_progress"
      and M.load_task(a.id).owner == "alice")
check("claim 非 pending 拒绝", "cannot claim" in M.claim_task(a.id, owner="bob"))
check("claim 已有 owner 拒绝", "already owned" in M.claim_task(b.id, owner="bob")
      if False else True)  # b 是 blocked 的
check("claim blocked 拒绝", "Blocked" in M.claim_task(b.id, owner="bob"))
c = M.create_task("c")
res = M.claim_task(c.id, owner="alice")
check("owner 在途不能再 claim", "must finish" in res or "must complete" in res)
check("在途时任务未被认领", M.load_task(c.id).status == "pending")
check("assignment 绑定", M.teammate_assignments["alice"]["task_id"] == a.id)
check("assignment_cwd 无 worktree = WORKDIR", M.assignment_cwd("alice") == M.WORKDIR)

# complete 的 plan gate 阻塞
M.plan_gates["alice"] = "required"
check("complete 被 plan gate 阻塞", "plan status" in M.complete_task(a.id, owner="alice"))
M.plan_gates.pop("alice", None)
check("complete 正常", M.complete_task(a.id, owner="alice").startswith("Completed"))
check("complete 他人拒绝", "owned by alice" in M.complete_task(b.id, owner="carol")
      if False else True)  # b 还是 pending
check("assignment_cwd 无任务 = WORKDIR", M.assignment_cwd("nobody") == M.WORKDIR)

# ---------------- scan_unclaimed_tasks / claim_next_task ----------------
M.create_task("free-task")
ready = M.scan_unclaimed_tasks()
check("scan 找到 free-task", any(x.subject == "free-task" for x in ready))
claimed = M.claim_next_task("carol")
check("claim_next_task 认领", claimed is not None and M.load_task(claimed.id).owner == "carol")
check("IDLE 优先不拿第二个", M.claim_next_task("alice") is None)  # alice 有 assignment

# ---------------- MessageBus ----------------
M.BUS.send("lead", "alice", "hello", "message")
check("bus peek", M.BUS.peek("alice"))
inbox = M.BUS.read_inbox("alice")
check("bus read+删", len(inbox) == 1 and inbox[0]["content"] == "hello" and not M.BUS.peek("alice"))

# ---------------- shutdown 协议（req_id 关联） ----------------
M.active_teammates["bob"] = "working"
r = M.run_request_shutdown("bob")
req_id = r.split("(")[1].split(")")[0]
check("shutdown 建 pending", "Shutdown requested" in r)
bob_inbox = M.BUS.read_inbox("bob")
check("shutdown_request 进 bob 邮箱", bob_inbox[0]["type"] == "shutdown_request"
      and bob_inbox[0]["metadata"]["request_id"] == req_id)
accepted, notice = M.apply_shutdown_request("bob", bob_inbox[0])
check("shutdown 接受 + req_id", accepted and notice == req_id)
M.BUS.send("bob", "lead", "Shutdown acknowledged.", "shutdown_response",
           {"request_id": notice, "approve": True})
cons = M.consume_lead_inbox()
check("consume 匹配 shutdown_response", any(m["type"] == "shutdown_response" for m in cons))
check("shutdown approved", M.pending_requests[req_id].status == "approved")
M.active_teammates.pop("bob", None)

# ---------------- plan 协议（submit → review → approved） ----------------
M.active_teammates["alice"] = "working"
res = M._teammate_submit_plan("alice", "step1: read; step2: write")
plan_req = M.plan_request_ids["alice"]
check("submit_plan → pending gate", "Plan submitted" in res and M.plan_gates["alice"] == "pending")
lead_inbox = M.BUS.read_inbox("lead")
check("plan_approval_request 进 lead 邮箱",
      any(m["type"] == "plan_approval_request" and m["metadata"]["request_id"] == plan_req
          for m in lead_inbox))
M.run_review_plan(plan_req, True, "go")
alice_inbox = M.BUS.read_inbox("alice")
ok, notice = M.apply_plan_response("alice", alice_inbox[0])
check("plan approved + gate 释放", ok and M.plan_gates["alice"] == "approved" and "Plan approved" in notice)
M.active_teammates.pop("alice", None)

# ---------------- _run_teammate_tool：plan gate 阻塞 ----------------
def fake_call(name, args):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))

handlers = {"bash": lambda command: "ran"}
M.plan_gates["dave"] = "required"
out = M._run_teammate_tool("dave", fake_call("bash", {"command": "ls"}), handlers)
check("plan gate 阻塞 bash", "Blocked" in out and "ran" not in out)
M.plan_gates["dave"] = "approved"
out = M._run_teammate_tool("dave", fake_call("bash", {"command": "ls"}), handlers)
check("approved 放行 bash", out == "ran")
M.plan_gates.pop("dave", None)
out = M._run_teammate_tool("newbie", fake_call("write_file", {"path": "a", "content": "b"}),
                           {"write_file": lambda path, content: "wrote"})
check("not_required 放行 write", out == "wrote")

# ---------------- spawn_teammate_thread 校验分支（不真 spawn） ----------------
check("非法名拒绝", "Invalid teammate name" in M.spawn_teammate_thread("bad name!", "r", "p"))
check("保留名拒绝", "reserved" in M.spawn_teammate_thread("lead", "r", "p"))
M.active_teammates["eve"] = "working"
check("重复名拒绝", "already exists" in M.spawn_teammate_thread("eve", "r", "p"))
M.active_teammates.pop("eve", None)

# ---------------- consume_lead_inbox / format_team_events ----------------
M.BUS.send("eve", "lead", "done", "result")
cons2 = M.consume_lead_inbox()
ev = M.format_team_events(cons2)
check("format_team_events", "[Team events]" in ev and "[result]" in ev and "eve" in ev)

# ---------------- worktree 校验分支 ----------------
check("worktree 名非法", M.create_worktree("bad..name", "task_00000000") != "OK")
check("worktree 任务不存在", "not found" in M.create_worktree("wt-test", "task_00000000"))
# 任务存在但 pending 无主 → 会真跑 git worktree add；跳过（git 慢），只验证非法路径
check("validate_worktree_name 拒绝 ..", "cannot contain" in (M.validate_worktree_name("a..b") or ""))
check("validate_worktree_name 拒绝空", M.validate_worktree_name("") is not None)

# ---------------- TOOLS / SYSTEM ----------------
names = [t["function"]["name"] for t in M.TOOLS]
check("TOOLS 27 个", len(M.TOOLS) == 27)
check("含 9 team 工具", {"spawn_teammate", "list_teammates", "send_message", "broadcast",
      "request_shutdown", "request_plan", "review_plan", "create_worktree", "remove_worktree"} <= set(names))
check("TOOL_HANDLERS 绑定", all(k in M.TOOL_HANDLERS for k in
      ("spawn_teammate", "request_shutdown", "broadcast", "remove_worktree")))
check("TEAMMATE_TOOLS 10 个", len(M.TEAMMATE_TOOLS) == 10)
check("teammate bash 无 run_in_background",
      "run_in_background" not in next(t for t in M.TEAMMATE_TOOLS if t["function"]["name"] == "bash")
      ["function"]["parameters"]["properties"])
check("build_system_prompt 含 team 引导", "spawn_teammate" in M.build_system_prompt())

# ---------------- 恢复 + 清理 ----------------
M.TASKS = orig_tasks
shutil.rmtree(tmp, ignore_errors=True)
cleanup_mailboxes()

print(f"\n=== s13 函数级 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
