#!/usr/bin/env python3
"""s13 验收（真实 API，单进程多轮 + 独立进程验证跨 session）。

A: 启动出现 s13 >> 提示符
B: Lead 创建任务 + 提议团队
C: 用户确认 → spawn teammate + 各自 claim
D: 邮件箱双向通信（result / idle_notification）
E: shutdown 协议（req_id 关联）→ teammate finished
F: 跨 session durable 恢复（任务状态保留）
plan gate 状态机 / worktree 校验分支 / claim 双锁走函数级（tests/_s13_functional.py）。
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
T.SCRIPT = os.path.join(ROOT, "s13_agent_teams.py")
MAILBOX_DIR = os.path.join(ROOT, ".mailboxes")
TASKS_DIR = os.path.join(ROOT, ".tasks")


class Session:
    def __init__(self, prompt_timeout=40):
        self.r = T.Repl()
        ok, _ = self.r.wait_until(lambda t: "s13 >>" in T.strip_ansi(t), timeout=prompt_timeout)
        self.launched = ok

    def ask(self, prompt, timeout=300):
        start = self.r.mark()
        self.r.send(prompt)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.r.proc.poll() is not None:
                break
            inc = T.strip_ansi(self.r.since(start))
            if inc.count("s13 >>") >= 1:
                return True, T.strip_ansi(self.r.since(start))
            time.sleep(0.3)
        return False, T.strip_ansi(self.r.since(start))

    def wait(self, seconds):
        start = self.r.mark()
        time.sleep(seconds)
        return T.strip_ansi(self.r.since(start))

    def close(self):
        try:
            self.r.send("q")
        finally:
            self.r.close()


if __name__ == "__main__":
    T.PASSED.clear()
    T.FAILED.clear()

    if os.path.isdir(MAILBOX_DIR):
        shutil.rmtree(MAILBOX_DIR)
    if os.path.isdir(TASKS_DIR):
        shutil.rmtree(TASKS_DIR)  # 清掉上次 daemon 强退残留的 in_progress 任务

    s = Session()
    T.record("[A] 启动出现 s13 >> 提示符", s.launched)

    # ---- B：Lead 创建任务 + 提议团队（等用户确认）----
    done, inc = s.ask(
        "创建任务 'setup demo task'（描述：echo hello 并报告）。然后告诉我你建议怎么分工处理，"
        "等我的确认再开始。", timeout=300)
    T.record("[B] 创建任务 + 提议团队", done and "> create_task(" in inc
             and "task_" in inc, f"hit={'create_task' in inc}")

    # ---- C：确认 → spawn teammate（claim 初始任务）----
    done, inc = s.ask(
        "确认，请用 list_tasks 找到刚才的任务，spawn 一个 teammate 'worker'"
        "（role: demo，prompt: 先 echo hello-from-teammate，然后 complete_task 汇报结果）。", timeout=360)
    T.record("[C] spawn teammate + claim", done and "> spawn_teammate(" in inc
             and "[teammate] worker spawned" in inc,
             f"hit={'worker spawned' in inc}")

    # ---- D：teammate 自主工作 + 邮件箱通信 ----
    waited = s.wait(50)
    T.record("[D] 邮件箱双向通信（result/echo/complete）",
             ("[bus]" in waited and "worker" in waited)
             or "hello-from-teammate" in waited or "Completed" in waited,
             f"hit_bus={'[bus]' in waited} hit_echo={'hello-from-teammate' in waited}")

    # ---- E：shutdown 协议（握手是异步的，需额外等待 worker 线程退出）----
    done, inc = s.ask("用 request_shutdown 关闭 worker，然后 list_teammates 确认。", timeout=300)
    waited_e = s.wait(20)
    both = inc + waited_e
    T.record("[E] shutdown 协议", done and "> request_shutdown(" in inc
             and ("[teammate] worker finished" in both or "No active teammates" in both),
             f"hit_shutdown={'request_shutdown' in inc} hit_finished={'[teammate] worker finished' in both}")
    s.close()

    # ---- F：跨 session durable 恢复（任务状态保留）----
    s2 = Session()
    done, inc = s2.ask("用 list_tasks 列出所有任务及其状态。", timeout=200)
    T.record("[F] 跨 session 任务状态保留", done and "> list_tasks(" in inc
             and "task_" in inc and ("completed" in inc or "pending" in inc))
    s2.close()

    print(f"\n=== s13 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
