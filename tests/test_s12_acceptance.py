#!/usr/bin/env python3
"""s12 验收（真实 API，两个独立进程）。

Session 1（进程 1）：schedule durable 任务 → 等到点触发注入 [Scheduled] → list_crons
Session 2（进程 2，重启）：durable 任务跨 session 还在 → cancel 删内存+持久化
agent_lock 互斥 / 防重复入队 / 入队回滚走函数级（tests/_s12_functional.py）。
"""
import json
import os
import shutil
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s12_cron_scheduler.py")
SCHEDULED = os.path.join(ROOT, ".scheduled_tasks.json")


class Session:
    def __init__(self, prompt_timeout=30):
        self.r = T.Repl()
        ok, _ = self.r.wait_until(lambda t: "s12 >>" in T.strip_ansi(t), timeout=prompt_timeout)
        self.launched = ok

    def ask(self, prompt, timeout=300):
        start = self.r.mark()
        self.r.send(prompt)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.r.proc.poll() is not None:
                break
            inc = T.strip_ansi(self.r.since(start))
            if inc.count("s12 >>") >= 1:
                return True, T.strip_ansi(self.r.since(start))
            time.sleep(0.3)
        return False, T.strip_ansi(self.r.since(start))

    def wait(self, seconds):
        """固定等待（scheduler/queue processor 线程在后台跑，不产生 s12 >> 信号）。"""
        start = self.r.mark()
        time.sleep(seconds)
        return T.strip_ansi(self.r.since(start))

    def close(self):
        try:
            self.r.send("q")
        finally:
            self.r.close()


def scheduled_payload():
    if not os.path.isfile(SCHEDULED):
        return []
    try:
        data = json.loads(open(SCHEDULED, encoding="utf-8").read())
        return data if isinstance(data, list) else []
    except Exception:
        return []


if __name__ == "__main__":
    T.PASSED.clear()
    T.FAILED.clear()

    if os.path.exists(SCHEDULED):
        os.remove(SCHEDULED)  # 从干净状态开始

    # ---- Session 1 ----
    s = Session()
    T.record("[A] 启动出现 s12 >> 提示符", s.launched)

    done, inc = s.ask(
        "用 schedule_cron 工具安排任务：cron='* * * * *'，prompt='run date'，"
        "recurring=true，durable=true。安排后告诉我 job ID。", timeout=300)
    T.record("[B] schedule_cron 校验并落盘", done and "Scheduled" in inc and "cron_" in inc
             and scheduled_payload() and any("run date" in str(j.get("prompt", ""))
                                            for j in scheduled_payload()),
             f"payload={scheduled_payload()}")

    # 等到下一个分钟边界触发（最多 ~75s）
    now = time.localtime()
    wait_sec = 60 - now.tm_sec + 15
    waited = s.wait(wait_sec)
    T.record("[C] 到点触发注入 [Scheduled]",
             "[Scheduled]" in waited or "[cron] due" in waited or "run date" in waited,
             f"hit={'[Scheduled]' in waited or '[cron] due' in waited}")

    done, inc = s.ask("用 list_crons 列出所有 cron 任务。", timeout=200)
    T.record("[D] list_crons 展示", done and "cron_" in inc and "run date" in inc)
    s.close()

    # ---- Session 2（重启）：durable 跨 session 还在 ----
    s2 = Session()
    done, inc = s2.ask("用 list_crons 列出所有 cron 任务。", timeout=200)
    T.record("[E] 跨 session durable 任务还在", done and "cron_" in inc and "run date" in inc)

    done, inc = s2.ask("用 list_crons 找到刚才那个任务的 job ID，然后用 cancel_cron 取消它。", timeout=300)
    T.record("[F] cancel_cron 删内存+持久化", done and "Cancelled" in inc
             and not any("run date" in str(j.get("prompt", "")) for j in scheduled_payload()),
             f"payload_after={scheduled_payload()}")
    s2.close()

    print(f"\n=== s12 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
