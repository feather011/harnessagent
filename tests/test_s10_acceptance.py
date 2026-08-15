#!/usr/bin/env python3
"""s10 验收（真实 API，独立会话）。

A: 启动出现 s10 >> 提示符
B: create_task 建依赖链 → .tasks/ 落盘 + 依赖传递
C: claim + complete → 解锁下游（Unblocked）
D: 跨 session 重启后状态保留（list_tasks 读出）
路径安全/owner 校验/撞 ID 重生成走函数级（tests/_s10_functional.py）。
"""
import os
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s10_task_system.py")
TASKS_DIR = os.path.join(ROOT, ".tasks")


def task_files():
    if not os.path.isdir(TASKS_DIR):
        return []
    return sorted(f for f in os.listdir(TASKS_DIR) if f.startswith("task_"))


def run_scenario(prompt, timeout=300):
    r = T.Repl()
    try:
        ok, _ = r.wait_until(lambda t: "s10 >>" in T.strip_ansi(t), timeout=30)
        if not ok:
            return r, False, False, "(未出现提示符)"
        start = r.mark()
        r.send(prompt)
        deadline = time.time() + timeout
        done = False
        while time.time() < deadline:
            if r.proc.poll() is not None:
                break
            inc = T.strip_ansi(r.since(start))
            if inc.count("s10 >>") >= 1:
                done = True
                break
            time.sleep(0.3)
        return r, True, done, T.strip_ansi(r.since(start))
    except Exception:
        if "r" in locals():
            r.close()
        raise


if __name__ == "__main__":
    T.PASSED.clear()
    T.FAILED.clear()

    if os.path.isdir(TASKS_DIR):
        shutil.rmtree(TASKS_DIR)

    # ---- 场景 A + B：启动 + 建任务依赖链 ----
    r, launched, done, inc = run_scenario(
        "用 create_task 工具创建这 4 个任务并 list_tasks 展示："
        "setup database schema；create API endpoints（依赖 schema）；"
        "write tests（依赖 endpoints）；write docs（依赖 schema）。", timeout=300)
    T.record("[A] 启动出现 s10 >> 提示符", launched)
    files = task_files()
    T.record("[B1] create_task 落盘 .tasks/", done and "> create_task(" in inc and len(files) >= 4,
             f"files={len(files)}")
    T.record("[B2] list_tasks 展示", done and "> list_tasks(" in inc)
    r.send("q")
    r.close()

    # ---- 场景 C：claim + complete 解锁下游 ----
    r, launched, done, inc = run_scenario(
        "用 list_tasks 找到 setup database schema 那个任务，claim 它，complete 它，"
        "然后告诉我哪些下游任务被解锁了。", timeout=300)
    T.record("[C1] claim + complete 被调用", done and "> claim_task(" in inc and "> complete_task(" in inc)
    T.record("[C2] 解锁下游（Unblocked/endpoints/docs）",
             done and ("Unblocked" in inc or ("endpoints" in inc and "docs" in inc)))
    r.send("q")
    r.close()

    # ---- 场景 D：跨 session 重启后状态保留 ----
    r, launched, done, inc = run_scenario(
        "用 list_tasks 列出所有任务及其状态，不要创建新任务。", timeout=200)
    T.record("[D] 重启后状态保留",
             done and "> list_tasks(" in inc and "completed" in inc and "schema" in inc,
             f"hit={'completed' in inc and 'schema' in inc}")
    r.send("q")
    r.close()

    print(f"\n=== s10 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
