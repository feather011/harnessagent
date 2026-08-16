#!/usr/bin/env python3
"""s12 函数级验证（mock + 临时 durable 路径，不碰真实 .scheduled_tasks.json）。

CronScheduler 可传任意 durable_path，故用系统临时目录；线程只测逻辑方法，不启动线程。
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s12_cron_scheduler as M

tmpdir = Path(tempfile.mkdtemp(prefix="s12_"))
durable = tmpdir / ".scheduled_tasks.json"
sched = M.CronScheduler(durable)

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


# ---------------- validate_cron（5 字段 + 范围） ----------------
check("合法 5 字段", M.validate_cron("0 9 * * *") is None)
check("字段数错", "5 fields" in M.validate_cron("0 9 * *"))
check("minute 越界", "minute" in M.validate_cron("60 9 * * *"))
check("hour 越界", "hour" in M.validate_cron("0 24 * * *"))
check("day 越界", "day-of-month" in M.validate_cron("0 9 32 * *"))
check("month 越界", "month" in M.validate_cron("0 9 * 13 *"))
check("weekday 越界", "day-of-week" in M.validate_cron("0 9 * * 7"))
check("step 非法", "step" in M.validate_cron("*/0 * * * *"))
check("range 反了", "Range start" in M.validate_cron("5-1 * * * *"))
check("*/5 合法", M.validate_cron("*/5 * * * *") is None)
check("列表 合法", M.validate_cron("0 9 * * 1,3,5") is None)

# ---------------- cron_matches ----------------
moment = datetime(2026, 1, 1, 9, 0)
check("* 全匹配", M.cron_matches("* * * * *", moment))
check("精确分钟", M.cron_matches("0 9 * * *", moment) and not M.cron_matches("1 9 * * *", moment))
check("*/5 分钟", M.cron_matches("*/5 * * * *", datetime(2026, 1, 1, 10, 5))
      and not M.cron_matches("*/5 * * * *", datetime(2026, 1, 1, 10, 3)))
check("范围", M.cron_matches("0 8-10 * * *", datetime(2026, 1, 1, 9, 0))
      and not M.cron_matches("0 8-10 * * *", datetime(2026, 1, 1, 11, 0)))
check("列表", M.cron_matches("0 9,18 * * *", datetime(2026, 1, 1, 18, 0)))
wd = (moment.weekday() + 1) % 7
check("weekday 匹配当天", M.cron_matches(f"0 9 * * {wd}", moment))
check("weekday 不匹配", not M.cron_matches(f"0 9 * * {(wd + 1) % 7}", moment))

# ---------------- schedule（校验 + 返回 + durable 落盘） ----------------
result = sched.schedule("*/2 * * * *", "run date")
check("schedule 返回 CronJob", isinstance(result, M.CronJob) and result.id.startswith("cron_"))
check("schedule 后注册", result.id in sched.scheduled_jobs)
check("durable 落盘", durable.is_file())
payload = json.loads(durable.read_text("utf-8"))
check("持久化内容含 job", any(j["id"] == result.id for j in payload))
check("非法 cron 返错误串", isinstance(sched.schedule("bad cron", "x"), str))
check("空 prompt 返错误串", "Prompt cannot be empty" in sched.schedule("* * * * *", "  "))

# durable=False 不写文件
sched2 = M.CronScheduler(tmpdir / "no_durable.json")
sched2.schedule("* * * * *", "session-only", durable=False)
check("durable=False 不落盘", not (tmpdir / "no_durable.json").exists())

# ---------------- cancel（删内存 + 持久化） ----------------
check("cancel 未知名", "not found" in sched.cancel("cron_nope"))
job = sched.schedule("0 0 * * *", "daily thing")
check("cancel 成功", "Cancelled" in sched.cancel(job.id))
check("cancel 后内存删除", job.id not in sched.scheduled_jobs)
check("cancel 后持久化更新", all(j["id"] != job.id for j in json.loads(durable.read_text("utf-8"))))

# ---------------- poll_due_jobs（到点入队 + 防重复） ----------------
s3 = M.CronScheduler(tmpdir / "poll.json")
j1 = s3.schedule("* * * * *", "every-minute")
t1 = datetime(2026, 1, 1, 10, 30)
s3.poll_due_jobs(t1)
check("到点入队", s3.has_queue() and s3.cron_queue[0].id == j1.id)
check("pending_delivery + last_fired", s3.scheduled_jobs[j1.id].pending_delivery
      and s3.scheduled_jobs[j1.id].last_fired == "2026-01-01 10:30")
s3.poll_due_jobs(datetime(2026, 1, 1, 10, 30, 30))
check("同一分钟不重复入队", len(s3.cron_queue) == 1)
# ack 后（recurring 清 pending_delivery）同分钟仍被 last_fired 挡
consumed = s3.consume_queue()
s3.acknowledge(consumed)
check("ack 清 pending_delivery", not s3.scheduled_jobs[j1.id].pending_delivery)
s3.poll_due_jobs(datetime(2026, 1, 1, 10, 30, 59))
check("last_fired 挡同分钟", not s3.has_queue())
s3.poll_due_jobs(datetime(2026, 1, 1, 10, 31))
check("下一分钟再入队", s3.has_queue())

# ---------------- one-shot：ack 后移除 ----------------
s4 = M.CronScheduler(tmpdir / "oneshot.json")
j4 = s4.schedule("* * * * *", "once", recurring=False)
s4.poll_due_jobs(datetime(2026, 1, 1, 11, 0))
fired = s4.consume_queue()
s4.acknowledge(fired)
check("one-shot ack 后移除", j4.id not in s4.scheduled_jobs)

# ---------------- restore（失败回滚：任务退回队列） ----------------
s5 = M.CronScheduler(tmpdir / "restore.json")
j5 = s5.schedule("* * * * *", "restore-me")
s5.poll_due_jobs(datetime(2026, 1, 1, 12, 0))
fired5 = s5.consume_queue()
s5.restore(fired5)
check("restore 退回队列", s5.has_queue() and s5.cron_queue[0].id == j5.id
      and s5.scheduled_jobs[j5.id].pending_delivery)

# ---------------- _enqueue_due_job 持久化失败回滚 ----------------
s6 = M.CronScheduler(tmpdir / "rollback.json")
j6 = s6.schedule("* * * * *", "rollback", durable=True)
def failing_save():
    raise OSError("disk full")
s6.save_durable_jobs = failing_save
s6.poll_due_jobs(datetime(2026, 1, 1, 13, 0))
check("入队失败回滚（不入队、状态复原）", not s6.has_queue()
      and not s6.scheduled_jobs[j6.id].pending_delivery
      and s6.scheduled_jobs[j6.id].last_fired is None)

# ---------------- schedule 持久化失败回滚内存 ----------------
s7 = M.CronScheduler(tmpdir / "rollback2.json")
def failing_save2():
    raise OSError("disk full")
s7.save_durable_jobs = failing_save2
try:
    s7.schedule("* * * * *", "should-not-exist")
    check("schedule 持久化失败抛异常", False)
except OSError:
    check("schedule 持久化失败抛异常", True)
check("schedule 失败回滚内存", not s7.scheduled_jobs)

# ---------------- 跨 session：durable 加载 ----------------
s8 = M.CronScheduler(tmpdir / "cross.json")
s8.schedule("0 9 * * 1", "weekly thing")
s8s = M.CronScheduler(tmpdir / "cross.json")  # 新实例 = 重启
s8s.load_durable_jobs()
check("跨 session durable 还在", any(j.prompt == "weekly thing" for j in s8s.scheduled_jobs.values()))
# 非 durable 任务不落盘 → 新实例没有
s9 = M.CronScheduler(tmpdir / "cross.json")
s9.schedule("0 9 * * 2", "session-only-job", durable=False)
s9s = M.CronScheduler(tmpdir / "cross.json")
s9s.load_durable_jobs()
check("非 durable 跨 session 没了", not any(j.prompt == "session-only-job" for j in s9s.scheduled_jobs.values()))

# ---------------- 损坏文件启动报错不崩 ----------------
broken = tmpdir / "broken.json"
broken.write_text("{not json")
s10 = M.CronScheduler(broken)
s10.load_durable_jobs()
check("损坏文件启动报错不崩", not s10.scheduled_jobs)

# ---------------- TOOLS / SYSTEM ----------------
names = [t["function"]["name"] for t in M.TOOLS]
check("TOOLS 18 含 3 cron", len(M.TOOLS) == 18 and
      {"schedule_cron", "list_crons", "cancel_cron"} <= set(names))
check("TOOL_HANDLERS 含 3 cron", all(k in M.TOOL_HANDLERS for k in
      ("schedule_cron", "list_crons", "cancel_cron")))
check("build_system_prompt 含 cron 引导", "schedule_cron" in M.build_system_prompt())
# ---- 工具层（monkeypatch 全局 SCHEDULER 到临时实例，避免污染真实 .scheduled_tasks.json）----
orig_sched = M.SCHEDULER
M.SCHEDULER = M.CronScheduler(tmpdir / "handler.json")
check("run_schedule_cron 返回", "Scheduled" in M.run_schedule_cron("*/5 * * * *", "handler-job"))
check("run_list_crons 列出", "handler-job" in M.run_list_crons())
jid = next(j.id for j in M.SCHEDULER.scheduled_jobs.values())
check("run_cancel_cron 取消", "Cancelled" in M.run_cancel_cron(jid))
check("run_cancel_cron 后内存删除", jid not in M.SCHEDULER.scheduled_jobs)
M.SCHEDULER = orig_sched

shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n=== s12 函数级 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
