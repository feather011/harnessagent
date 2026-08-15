#!/usr/bin/env python3
"""s11 函数级验证（真实后台线程 + 短命令，不碰 API）。

注意：BackgroundManager 是模块级单例，测试按"start→等完成→collect"顺序消费干净。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s11_background_tasks as M

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def wait_status(task_id, target, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = M.BACKGROUND.tasks.get(task_id)
        if task and task.get("status") != "running":
            return task["status"]
        time.sleep(0.05)
    return None


# ---------------- should_run_background（显式参数，不猜） ----------------
check("bash+true → 后台", M.should_run_background("bash", {"command": "x", "run_in_background": True}) is True)
check("bash 无参数 → 同步", M.should_run_background("bash", {"command": "x"}) is False)
check("bash+false → 同步", M.should_run_background("bash", {"command": "x", "run_in_background": False}) is False)
check("非 bash → 同步", M.should_run_background("glob", {"run_in_background": True}) is False)
check("字符串 true 不算", M.should_run_background("bash", {"command": "x", "run_in_background": "true"}) is False)

# ---------------- _run_bash_process / _format_bash_result ----------------
out, code = M._run_bash_process("echo bg-test")
check("正常命令 (out, code=0)", code == 0 and "bg-test" in out, f"out={out!r} code={code}")
out, code = M._run_bash_process("false")
check("失败命令 code≠0", code != 0, f"code={code}")
check("_format 成功透传", M._format_bash_result("hello", 0) == "hello")
check("_format 失败带 status", "status 3" in M._format_bash_result("boom", 3))

# ---------------- BackgroundManager.start 即返（不阻塞） ----------------
bg = M.BACKGROUND
t0 = time.time()
task_id = bg.start("sleep 2")
elapsed = time.time() - t0
check("start 立即返 bg_id", task_id == "bg_0001" and elapsed < 1.0, f"id={task_id} elapsed={elapsed:.2f}s")
check("task 注册为 running", bg.tasks[task_id]["status"] == "running" and bg.tasks[task_id]["command"] == "sleep 2")

# ---------------- _run 完成后标 completed ----------------
check("完成后 completed", wait_status(task_id, "completed") == "completed")
notifs = bg.collect()
check("collect 出队 1 条", len(notifs) == 1)
check("notification 格式", "<task_notification>" in notifs[0] and f"<task_id>{task_id}</task_id>" in notifs[0]
      and "<status>completed</status>" in notifs[0])
check("collect 后清空队列", bg.tasks.get(task_id) is None and task_id not in bg.results and not bg._ready)

# ---------------- 失败 → failed（非零 exit_code） ----------------
tid2 = bg.start("false")
check("失败标 failed", wait_status(tid2, "failed") == "failed")
n2 = bg.collect()
check("失败 notification 含 status=failed", len(n2) == 1 and "<status>failed</status>" in n2[0])

# ---------------- 多并发后台任务 ----------------
ta = bg.start("echo job-a")
tb = bg.start("echo job-b")
check("并发两个都在跑", bg.tasks[ta]["status"] == "running" and bg.tasks[tb]["status"] == "running")
check("a completed", wait_status(ta, "completed") == "completed")
check("b completed", wait_status(tb, "completed") == "completed")
n3 = bg.collect()
check("collect 两条", len(n3) == 2 and {ta, tb} == set(
    (n.split("<task_id>")[1].split("</task_id>")[0] for n in n3)))
check("job-a/b 输出都在", "job-a" in n3[0] + n3[1] and "job-b" in n3[0] + n3[1])

# ---------------- inject_background_results ----------------
tid4 = bg.start("echo injected")
wait_status(tid4, "completed")
messages = [{"role": "user", "content": "task"}]
n = M.inject_background_results(messages)
check("inject 返回 1 并追加 user 消息", n == 1 and messages[-1]["role"] == "user"
      and "<task_notification>" in messages[-1]["content"])
check("无完成时 inject 返回 0", M.inject_background_results(messages) == 0 and len(messages) == 2)

# ---------------- run_bash 接受 run_in_background 参数（同步忽略） ----------------
out_sync = M.run_bash("echo sync-only", run_in_background=True)
check("run_bash 同步忽略 bg 参数", "sync-only" in out_sync)

# ---------------- bash schema 含 run_in_background ----------------
bash_tool = next(t for t in M.TOOLS if t["function"]["name"] == "bash")
props = bash_tool["function"]["parameters"]["properties"]
check("bash schema 加 run_in_background", "run_in_background" in props
      and props["run_in_background"]["type"] == "boolean")
check("system 含后台引导", "run_in_background=true" in M.build_system_prompt())

print(f"\n=== s11 函数级 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
