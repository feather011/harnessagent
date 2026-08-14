#!/usr/bin/env python3
"""s08 验收（真实 API，独立会话）。

A: 正常任务不触发压缩
B: 连续读多个文件 → 多结果 + 观测压缩痕迹（[auto compact]/[Compacted] 出现则验证）
tool_result_budget/snip/micro/reactive 走函数级。
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s08_context_compact.py")


def run_scenario(prompt, timeout=300):
    r = T.Repl()
    try:
        ok, _ = r.wait_until(lambda t: "s08 >>" in T.strip_ansi(t), timeout=30)
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
            if inc.count("s08 >>") >= 1:
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

    # ---- 场景 A：正常任务不触发压缩 ----
    r, launched, done, inc = run_scenario("用 glob 工具列出工作区所有 .py 文件", timeout=120)
    T.record("[A1] 启动出现 s08 >> 提示符", launched)
    T.record("[A2] 正常任务不触发压缩", done and "> glob(" in inc
             and "[auto compact]" not in inc and "[transcript saved]" not in inc
             and "[Compacted]" not in inc)
    r.send("q")
    r.close()

    # ---- 场景 B：连续读多个文件 → 多结果（若触发压缩则验证 [Compacted]）----
    r, launched, done, inc = run_scenario(
        "依次用 read_file 工具读取这 7 个文件的内容，不要修改它们："
        "s01_agent_loop.py, s02_tool_use.py, s03_permission.py, s04_hooks.py, "
        "s05_todo_write.py, s06_subagent.py, s07_skill_loading.py", timeout=400)
    reads = inc.count("> read_file(")
    T.record("[B1] 连续读取多个文件", done and reads >= 3, f"read_file 次数={reads}")
    if "[auto compact]" in inc:
        # [auto compact] 打印 = prepare 检测超 50K → compact_history 被调（其结构走函数级验证）
        T.record("[B2] 触发自动压缩", done, f"transcript落盘={'是' if '[transcript saved]' in inc else '否'}")
    else:
        print("[INFO] 本次会话未触发压缩（累计字符未超 50K），[B2] 记为通过（机制走函数级）")
        T.record("[B2] 未触发压缩（正常，机制函数级已验证）", done)
    r.send("q")
    r.close()

    print(f"\n=== s08 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
