#!/usr/bin/env python3
"""s05 验收（真实 API，独立会话）。

场景 A: 启动 + 简单任务不调 todo_write
场景 B: 复杂任务第一轮调 todo_write + 输出格式
validator 5 规则 / reminder 计数走函数级（见内联说明）。
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s05_todo_write.py")


def run_scenario(prompt, decisions=None, timeout=180):
    r = T.Repl()
    try:
        ok, _ = r.wait_until(lambda t: "s05 >>" in T.strip_ansi(t), timeout=30)
        if not ok:
            return r, False, False, "(未出现提示符)"
        start = r.mark()
        r.send(prompt)
        deadline = time.time() + timeout
        fed = 0
        done = False
        while time.time() < deadline:
            if r.proc.poll() is not None:
                break
            inc = T.strip_ansi(r.since(start))
            n_allow = inc.count("Allow?")
            while fed < n_allow:
                r.send(decisions[fed] if fed < len(decisions or []) else "n")
                fed += 1
                time.sleep(0.3)
            # done 条件用"喂完当前所有 Ask"而非 len(decisions)：实际 Ask 次数可能少于预设
            if inc.count("s05 >>") >= 1 and fed >= n_allow:
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

    # ---- 场景 A：启动 + 简单任务（glob，明确禁止规划）----
    r, launched, done, inc = run_scenario(
        "用 glob 工具列出工作区所有 .py 文件。这是一个单步任务，不需要使用 todo_write 工具。")
    T.record("[A1] 启动出现 s05 >> 提示符", launched)
    T.record("[A2] 单步任务不调 todo_write", done and "> glob(" in inc
             and "> todo_write(" not in inc)
    r.send("q")
    r.close()

    # ---- 场景 B：复杂任务 → 第一轮调 todo_write + 格式（任务含删除会问用户，喂 y）----
    r, launched, done, inc = run_scenario(
        "在 notes 目录创建 s05_probe.py（内容 print('hello')），运行它，再删除它。"
        "这是一个多步任务，先用 todo_write 工具列出计划。",
        decisions=["y", "y", "y"])
    used_todo = "> todo_write(" in inc
    fmt_ok = any(mark in inc for mark in ["[ ]", "[>]", "[x]"]) and "completed" in inc
    T.record("[B1] 复杂任务调 todo_write", done and used_todo)
    T.record("[B2] 输出格式 [ ] [>] [x] + (N/M completed)", used_todo and fmt_ok)
    r.send("q")
    r.close()

    print(f"\n=== s05 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
