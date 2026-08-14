#!/usr/bin/env python3
"""s07 验收（真实 API，独立会话）。

A: 启动 + 列出 skill（不调 load_skill 直接答）
B: 加载 skill → load_skill 返完整内容
C: 错误 skill 名 → Unknown skill + Available 列表
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s07_skill_loading.py")


def run_scenario(prompt, timeout=180):
    r = T.Repl()
    try:
        ok, _ = r.wait_until(lambda t: "s07 >>" in T.strip_ansi(t), timeout=30)
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
            if inc.count("s07 >>") >= 1:
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

    # ---- 场景 A/B：启动 + 列出 skill（从 SYSTEM catalog 直接答，不调工具）----
    r, launched, done, inc = run_scenario(
        "列出所有可用的 skill。不要调用任何工具，直接从我给你的技能列表回答。")
    T.record("[A] 启动出现 s07 >> 提示符", launched)
    T.record("[B] 列出 skill 不调 load_skill 直接答",
             done and "> load_skill(" not in inc
             and ("code-review" in inc or "agent-builder" in inc))
    r.send("q")
    r.close()

    # ---- 场景 C：加载 skill → load_skill 返完整内容 ----
    r, launched, done, inc = run_scenario(
        "用 load_skill 工具加载 code-review 技能，然后告诉我它要求检查什么。", timeout=200)
    T.record("[C] load_skill 返回完整内容",
             done and "> load_skill(" in inc and "# code-review 技能" in inc)
    r.send("q")
    r.close()

    # ---- 场景 D：错误 skill 名 → Unknown + Available ----
    r, launched, done, inc = run_scenario(
        "用 load_skill 工具加载一个不存在的技能 nonexistent，看看返回什么。", timeout=200)
    T.record("[D] 未知 skill → Unknown + Available",
             done and "Unknown skill" in inc and "Available" in inc)
    r.send("q")
    r.close()

    print(f"\n=== s07 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
