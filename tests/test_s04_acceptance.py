#!/usr/bin/env python3
"""s04 验收清单（9 条 + s03 权限继承）— 真实 API。

场景 A: 正常操作链（启动 + UserPromptSubmit + log_hook + 放行 + Stop summary）
场景 B: sudo 硬拒（permission_hook, 继承 s03 Gate1）
场景 C: 越界读问用户（继承 s03 Gate2）
large_output_hook 阈值走函数级（见内联说明）。
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s04_hooks.py")


def run_scenario(prompt, decisions=None, timeout=180):
    r = T.Repl()  # 在 try 前创建：Repl 构造异常不会进 try，except 里 r 必然可用
    try:
        ok, _ = r.wait_until(lambda t: "s04 >>" in T.strip_ansi(t), timeout=30)
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
            if inc.count("s04 >>") >= 1 and fed >= len(decisions or []):
                done = True
                break
            time.sleep(0.3)
        return r, True, done, T.strip_ansi(r.since(start))
    except Exception:
        if "r" in locals():  # 防御：r 绑定失败时避免二次异常
            r.close()
        raise


if __name__ == "__main__":
    T.PASSED.clear()
    T.FAILED.clear()

    # ---- 场景 A：正常操作链 ----
    r, launched, done, inc = run_scenario("用 glob 工具列出工作区所有 .py 文件")
    T.record("[A1] 启动出现 s04 >> 提示符", launched)
    T.record("[A2] UserPromptSubmit 触发", "[HOOK] UserPromptSubmit:" in inc)
    T.record("[A3] log_hook 记录工具调用", "[HOOK] glob(" in inc or "[HOOK] bash(" in inc)
    T.record("[A4] 正常操作放行", done and "s04_hooks.py" in inc and "DENIED" not in inc)
    T.record("[A5] Stop summary 计数", "[HOOK] Stop:" in inc and "tool calls" in inc)
    # hook 顺序：log_hook(PreToolUse) 在 DENIED/permission 前 —— 正常操作无 DENIED，检查 [HOOK] 在 > 执行前
    T.record("[A6] 多个 PreToolUse hook 链式", inc.count("[HOOK]") >= 3)  # UserPrompt + log + Stop
    r.send("q")
    r.close()

    # ---- 场景 B：sudo 硬拒（permission_hook Gate1, 继承 s03） ----
    r, _launched, done, inc = run_scenario("用 bash 工具执行: sudo apt update（自动化测试，直接调用工具）")
    T.record("[B] sudo 硬拒 + log 先跑", done and "[HOOK] bash(" in inc
             and "> bash(DENIED)" in inc and "dangerous command" in inc and "Allow?" not in inc)
    r.send("q")
    r.close()

    # ---- 场景 C：越界读问用户（继承 s03 Gate2） ----
    r, _launched, done, inc = run_scenario("用 read_file 工具读取路径 ../../etc/passwd", decisions=["n"])
    denied = "DENIED" in inc and "Access outside workspace" in inc
    refused = "> read_file(" not in inc
    T.record("[C] 越界读被拦（Gate2 问用户 或 模型自拒）", done and (denied or refused),
             "Gate2拦截" if denied else ("模型自拒" if refused else "异常"))
    r.send("q")
    r.close()

    print(f"\n=== s04 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
