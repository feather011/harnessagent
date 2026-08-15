#!/usr/bin/env python3
"""s11 验收（真实 API，同一进程多轮会话——后台任务是进程内状态，跨进程会丢）。

A: 启动出现 s11 >> 提示符
B: bash 同步即返
C+D: bash(run_in_background=true) 即返 bg_id + 后台跑时 agent 继续干别的
E: 后台跑完后续轮次注入 <task_notification>
F: 失败命令标 failed
G: 多个并发后台任务
should_run_background 判定 / collect 出队 / 进程组清理走函数级（tests/_s11_functional.py）。
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s11_background_tasks.py")


class Session:
    """单进程多轮会话：后台任务在进程内持续存活，后续轮次才能 collect。"""

    def __init__(self, prompt_timeout=30):
        self.r = T.Repl()
        ok, _ = self.r.wait_until(lambda t: "s11 >>" in T.strip_ansi(t), timeout=prompt_timeout)
        self.launched = ok

    def ask(self, prompt, timeout=300):
        start = self.r.mark()
        self.r.send(prompt)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.r.proc.poll() is not None:
                break
            inc = T.strip_ansi(self.r.since(start))
            if inc.count("s11 >>") >= 1:
                return True, T.strip_ansi(self.r.since(start))
            time.sleep(0.3)
        return False, T.strip_ansi(self.r.since(start))

    def close(self):
        try:
            self.r.send("q")
        finally:
            self.r.close()


if __name__ == "__main__":
    T.PASSED.clear()
    T.FAILED.clear()

    s = Session()
    T.record("[A] 启动出现 s11 >> 提示符", s.launched)

    # ---- B：同步即返 ----
    done, inc = s.ask("用 bash 同步执行一条命令：echo hello-sync，然后告诉我输出。")
    T.record("[B] bash 同步即返", done and "> bash(" in inc and "hello-sync" in inc,
             f"hit={'hello-sync' in inc}")

    # ---- C+D：后台即返 bg_id + 后台跑时继续干别的 ----
    done, inc = s.ask(
        "用 bash 执行 sleep 5，run_in_background 必须设为 true（后台执行）。"
        "启动后不要等它，立刻用 glob 工具列出所有 .py 文件。", timeout=360)
    T.record("[C] 后台即返 bg_id", done and "> bash(" in inc
             and ("bg_" in inc or "run_in_background" in inc),
             f"hit_bg={'bg_' in inc}")
    T.record("[D] 后台跑时继续干别的", done and "> glob(" in inc)

    # ---- E：后台跑完 → 下一轮注入 <task_notification> ----
    done, inc = s.ask("检查一下刚才后台的 sleep 任务结果出来了没有，告诉我它是否完成。")
    T.record("[E] 后续轮次注入 notification", done and
             ("<task_notification>" in inc or "bg_0001" in inc or "completed" in inc
              or "[Background notification" in inc),
             f"hit={'<task_notification>' in inc or 'bg_0001' in inc}")

    # ---- F：失败命令标 failed（两轮：启动后台 → 下一轮检查注入的 notification）----
    done, inc = s.ask(
        "用 bash 执行一条会失败的命令：false（退出码 1），run_in_background=true。", timeout=360)
    done2, inc2 = s.ask("刚才后台的 false 任务结果出来了吗？告诉我它的状态。", timeout=360)
    T.record("[F] 失败标 failed", done2 and ("failed" in inc2 or "<status>failed" in inc2),
             f"hit={'failed' in inc2}")

    # ---- G：多个并发后台任务 ----
    done, inc = s.ask(
        "用 bash 同时后台执行两条命令：sleep 1 和 sleep 2（都 run_in_background=true），"
        "两个都完成后汇报它们的状态。", timeout=360)
    T.record("[G] 多个并发后台任务",
             done and (inc.count("bg_") >= 2 or inc.count("<task_notification>") >= 2),
             f"bg_count={inc.count('bg_')} notif={inc.count('<task_notification>')}")

    s.close()

    print(f"\n=== s11 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
