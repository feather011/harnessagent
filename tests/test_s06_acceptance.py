#!/usr/bin/env python3
"""s06 验收（真实 API，独立会话）。

场景 A: 启动 + 简单任务不调 task
场景 B: 复杂任务调 task → [Subagent started/done] + [sub] 日志 + final text
子 permission / 30 轮 / SUB_TOOLS 无 task 走函数级。
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s06_subagent.py")


def run_scenario(prompt, timeout=240):
    r = T.Repl()
    try:
        ok, _ = r.wait_until(lambda t: "s06 >>" in T.strip_ansi(t), timeout=30)
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
            if inc.count("s06 >>") >= 1:
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

    # ---- 场景 A：启动 + 简单任务 ----
    r, launched, done, inc = run_scenario("用 glob 工具列出工作区所有 .py 文件。这是一个单步任务，不需要使用 task 工具。")
    T.record("[A1] 启动出现 s06 >> 提示符", launched)
    T.record("[A2] 单步任务不调 task", done and "> glob(" in inc and "> task(" not in inc)
    r.send("q")
    r.close()

    # ---- 场景 B：复杂任务 → 调 task 子 agent ----
    r, launched, done, inc = run_scenario(
        "用 task 工具跑一个子 agent：让它用 glob 找出 notes 目录下所有 .md 文件，并返回它们叫什么。"
        "这是一个适合委派给子 agent 的独立任务。", timeout=300)
    started = "[Subagent started]" in inc
    done_mark = "[Subagent done]" in inc
    sub_log = "[sub] " in inc
    T.record("[B1] 调 task + [Subagent started]", done and ("> task(" in inc or started))
    T.record("[B2] [Subagent done] 标记", done and done_mark)
    T.record("[B3] 子 agent [sub] 工具日志", sub_log)
    T.record("[B4] 任务有最终回答", done and ("notes" in inc or ".md" in inc))
    r.send("q")
    r.close()

    print(f"\n=== s06 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
