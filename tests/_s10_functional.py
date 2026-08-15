#!/usr/bin/env python3
"""s10 函数级验证（mock，不碰真实 .tasks/ 与 API）。

TaskStore 要求目录在 WORKDIR 下（防越界设计），故重定向全局 TASKS 到 WORKDIR 内临时目录。
"""
import json
import os
import re
import shutil
import sys
import uuid as _uuid_mod
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s10_task_system as M

tmp = M.WORKDIR / f".tmp_s10_{_uuid_mod.uuid4().hex[:8]}"
M.TASKS = M.TaskStore(tmp)  # 重定向全局 store（create_task/claim/complete 都走它）

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


# ---------------- TaskStore.create / 文件落盘 ----------------
t = M.create_task("setup database schema", "create the schema tables")
check("ID 格式 task_[0-9a-f]{8}", bool(re.fullmatch(r"task_[0-9a-f]{8}", t.id)), f"id={t.id}")
check("写 .tasks/{id}.json", (tmp / f"{t.id}.json").is_file())
check("初始 pending/owner=None/blockedBy=[]", t.status == "pending" and t.owner is None and t.blockedBy == [])
check("JSON 字段完整", set(json.loads((tmp / f"{t.id}.json").read_text("utf-8"))) ==
      {"id", "subject", "description", "status", "owner", "blockedBy"})

# 非法参数
try:
    M.create_task("   ")
    check("空 subject 拒绝", False)
except ValueError:
    check("空 subject 拒绝", True)
try:
    M.create_task("x", blockedBy=["task_zzzzzzzz"])  # 非法 ID 格式
    check("非法依赖 ID 拒绝", False)
except ValueError:
    check("非法依赖 ID 拒绝", True)
try:
    M.create_task("x", blockedBy=["task_00000000"])  # 格式合法但文件不存在
    check("不存在的依赖拒绝", False)
except ValueError:
    check("不存在的依赖拒绝", True)

# ---------------- blockedBy 链 ----------------
schema = M.create_task("schema")
api = M.create_task("api", blockedBy=[schema.id])
tests = M.create_task("tests", blockedBy=[api.id])
check("blockedBy 正确传递", M.load_task(tests.id).blockedBy == [api.id])
check("依赖去重保序", M.create_task("dedup", blockedBy=[schema.id, schema.id]).blockedBy == [schema.id])

# ---------------- can_start / incomplete_dependencies ----------------
check("无依赖可开始", M.can_start(schema.id))
check("有依赖未完成不可开始", not M.can_start(api.id))
check("incomplete 返回未完成依赖", M.incomplete_dependencies(M.load_task(api.id)) == [schema.id])
# 依赖文件缺失 → 也算未完成
(schema_path := tmp / f"{schema.id}.json").unlink()
check("依赖文件缺失算 incomplete", M.incomplete_dependencies(M.load_task(api.id)) == [schema.id])
schema = M.create_task("schema2")  # 重建

# ---------------- claim_task ----------------
indep = M.create_task("independent")
check("claim 成功", M.claim_task(indep.id).startswith("Claimed"))
check("claim 后 in_progress + owner", M.load_task(indep.id).status == "in_progress"
      and M.load_task(indep.id).owner == "agent")
check("claim 非 pending 拒绝", "cannot claim" in M.claim_task(indep.id))
blocked = M.create_task("blocked-by-inprogress", blockedBy=[indep.id])  # indep 是 in_progress
check("claim 依赖未完成拒绝", "Blocked by" in M.claim_task(blocked.id))
check("claim 失败不改状态", M.load_task(blocked.id).status == "pending")

# ---------------- complete_task + unblock（前后对比） ----------------
s2 = M.create_task("schema3")
ep = M.create_task("endpoints", blockedBy=[s2.id])
doc = M.create_task("docs", blockedBy=[s2.id])
M.claim_task(s2.id)
msg = M.complete_task(s2.id)
check("complete 解锁下游", "endpoints" in msg and "docs" in msg, f"msg={msg!r}")
check("完成后 completed", M.load_task(s2.id).status == "completed")
check("解锁后可开始", M.can_start(ep.id) and M.can_start(doc.id))
# 链式：ep 完成 → 解锁 tests
ep2 = M.create_task("endpoints2", blockedBy=[M.create_task("schema4").id])
check("complete 非 in_progress 拒绝", "cannot complete" in M.complete_task(ep2.id))
# 无下游任务 complete → 无 Unblocked
leaf = M.create_task("leaf")
M.claim_task(leaf.id)
check("无下游不报 Unblocked", "Unblocked" not in M.complete_task(leaf.id))

# ---------------- owner 校验（防抢任务） ----------------
owned = M.create_task("owned-task")
M.claim_task(owned.id, owner="alice")
check("他人 complete 拒绝", "owned by alice" in M.complete_task(owned.id, owner="bob"))
check("owner 不匹配不改状态", M.load_task(owned.id).status == "in_progress")
check("owner 本人可完成", M.complete_task(owned.id, owner="alice").startswith("Completed"))

# ---------------- 排他创建：ID 冲突重新生成 ----------------
orig_uuid4 = M.uuid.uuid4
state = {"n": 0}
def fake_uuid4():
    state["n"] += 1
    if state["n"] <= 2:
        return _uuid_mod.UUID(int=0xAB)  # 前两次返回同一个固定 ID → 第二次撞文件
    return orig_uuid4()  # 用 monkeypatch 前保存的原始函数，避免递归
M.uuid.uuid4 = fake_uuid4
a = M.create_task("collide-a")
b = M.create_task("collide-b")
M.uuid.uuid4 = orig_uuid4
check("撞 ID 重新生成不覆盖", a.id == "task_00000000" and b.id != a.id
      and (tmp / f"{a.id}.json").exists() and (tmp / f"{b.id}.json").exists(),
      f"a={a.id} b={b.id}")

# ---------------- 跨 session 持久化（重启 = 新实例读同一目录） ----------------
M.claim_task(ep.id)  # ep 现在 pending→in_progress，留下跨重启状态
store2 = M.TaskStore(tmp)
tasks2 = store2.list()
check("重启后任务可读", len(tasks2) >= 1)
by_id = {x.id: x for x in tasks2}
check("重启后状态保留", by_id[ep.id].status == "in_progress" and by_id[ep.id].owner == "agent")
check("重启后 completed 保留", by_id[s2.id].status == "completed")

# ---------------- 路径安全 ----------------
for bad in ("task_xyz", "task_0000000", "task_1234567g", "../evil", "task_x/sub"):
    try:
        M.TASKS._path(bad)
        check(f"_path 拒绝 {bad}", False)
    except ValueError:
        check(f"_path 拒绝 {bad}", True)

# ---------------- 工具层 ----------------
check("run_create_task 返回 ID", "Created" in M.run_create_task("tool-create"))
check("run_list_tasks 含状态行", "[ ]" in M.run_list_tasks() and "tool-create" in M.run_list_tasks())
check("run_get_task 返回 JSON", "task_" in M.run_get_task(indep.id))
check("run_claim/complete 走 owner=agent",
      M.run_claim_task(doc.id).startswith("Claimed") and M.run_complete_task(doc.id).startswith("Completed"))

# ---------------- TOOLS / SYSTEM ----------------
names = [t.get("function", {}).get("name") for t in M.TOOLS]
check("TOOLS 15 个含 5 task", len(M.TOOLS) == 15 and
      {"create_task", "list_tasks", "get_task", "claim_task", "complete_task"} <= set(names))
check("TOOL_HANDLERS 含 5 task", all(k in M.TOOL_HANDLERS for k in
      ("create_task", "list_tasks", "get_task", "claim_task", "complete_task")))
check("build_system_prompt 含 task 引导", "create_task" in M.build_system_prompt())

shutil.rmtree(tmp, ignore_errors=True)

print(f"\n=== s10 函数级 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
