#!/usr/bin/env python3
"""s09 验收（真实 API，独立会话）。

A: 启动出现 s09 >> 提示符
B: 写入偏好（tabs 缩进）→ .memory/ 记录 + MEMORY.md 索引 + [Memory: stored
C: 重启后召回（新会话问缩进 → 回答含 tab）
D: current_task 不跨 session（临时约束不落盘为 persistent 记录）
consolidation（≥10/snapshot 兜底）与 recall fallback 走函数级（tests/_s09_functional.py）。
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
T.SCRIPT = os.path.join(ROOT, "s09_memory.py")
MEM_DIR = os.path.join(ROOT, ".memory")


def memory_files():
    if not os.path.isdir(MEM_DIR):
        return []
    return sorted(f for f in os.listdir(MEM_DIR) if f != "MEMORY.md")


def memory_index():
    path = os.path.join(MEM_DIR, "MEMORY.md")
    if not os.path.isfile(path):
        return ""
    return open(path, encoding="utf-8").read()


def run_scenario(prompt, timeout=200):
    r = T.Repl()
    try:
        ok, _ = r.wait_until(lambda t: "s09 >>" in T.strip_ansi(t), timeout=30)
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
            if inc.count("s09 >>") >= 1:
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

    # 从干净状态开始（本仓库 .memory/ 只由测试产生）
    if os.path.isdir(MEM_DIR):
        shutil.rmtree(MEM_DIR)

    # ---- 场景 A + B：启动 + 写入偏好 ----
    r, launched, done, inc = run_scenario(
        "请记住这个偏好：我更喜欢用 tabs 而不是空格来做缩进。以后写代码都用 tabs。",
        timeout=240)
    T.record("[A] 启动出现 s09 >> 提示符", launched)
    files = memory_files()
    index = memory_index()
    T.record("[B1] 偏好写入 .memory/ + 索引更新",
             done and bool(files) and "tabs" in index.lower(),
             f"files={files}")
    T.record("[B2] 轮末 [Memory: stored", "[Memory: stored" in inc)
    r.send("q")
    r.close()

    # ---- 场景 C：重启后召回（独立新进程 = 新会话）----
    r, launched, done, inc = run_scenario(
        "What indentation style do I prefer? 不要调用工具，直接回答。", timeout=180)
    T.record("[C] 重启后召回", done and "tab" in inc.lower(),
             f"hit_tab={'tab' in inc.lower()}")
    r.send("q")
    r.close()

    # ---- 场景 D：current_task 不跨 session ----
    before = memory_files()
    r, launched, done, inc = run_scenario(
        "请记住：本次会话内不要创建任何文件。", timeout=180)
    after = memory_files()
    T.record("[D] current_task 不跨 session", done and after == before,
             f"before={before} after={after}")
    r.send("q")
    r.close()

    print(f"\n=== s09 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
