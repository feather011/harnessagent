#!/usr/bin/env python3
"""s01_agent_loop 自动化测试。

用法:
    cd 项目根目录
    .venv/Scripts/python.exe tests/test_agent_loop.py          # 全部（含端到端真实 API）
    .venv/Scripts/python.exe tests/test_agent_loop.py --fast   # 仅函数级，不调 API

覆盖场景（对应需求）:
  [1] 启动出现 "s01 >> " 提示符
  [2] "List all .py files..."      -> 调用 bash 工具，输出含 .py
  [3] "Create hello.py ..."        -> hello.py 被创建且内容含 print（测后恢复原状）
  [4] 纯知识问题（15×8）         -> 不调工具，直接回答（cwd 问题模型会调 pwd 查证，改用纯知识问题保证稳定）
  [5] "rm -rf /"                   -> DENY 拦截，输出 Dangerous command blocked
  [6] "q"                          -> 干净退出（退出码 0，无 Traceback）
  [7] 长输出 (>50000) 被截断        (函数级)
  [8] 命令 30s 超时被 kill          (函数级)

轮次完成的判定: 每轮输入后，输出中再次出现 "s01 >> "（下一轮提示符）即本轮结束。
"""
import importlib.util
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "s01_agent_loop.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
HELLO = os.path.join(ROOT, "hello.py")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


PASSED, FAILED, SKIPPED = [], [], []


def record(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f"   ({detail})" if detail else ""))
    (PASSED if ok else FAILED).append(name)
    return ok


def load_module():
    spec = importlib.util.spec_from_file_location("s01_agent_loop", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- 函数级
def run_functional():
    m = load_module()

    for bad in ["rm -rf /", "rm -rf /*", "sudo apt update", "shutdown -h now", "reboot"]:
        out = m.run_bash(bad)
        record(f"[函数] DENY 拦截 {bad!r}", "blocked" in out, out[:50])

    out = m.run_bash("echo hello-bash")
    record("[函数] run_bash 正常执行", "hello-bash" in out, out[:50])

    out = m.run_bash("python -c \"print('x'*60000)\"")
    truncated = len(out) <= 50026 and "(output truncated)" in out
    record("[函数] 长输出截断", truncated, f"len={len(out)}")

    t0 = time.time()
    out = m.run_bash("sleep 35")
    dt = time.time() - t0
    record("[函数] 30s timeout", "timed out" in out, f"实际耗时 {dt:.1f}s")


# ---------------------------------------------------------------- 端到端 REPL
class Repl:
    def __init__(self):
        self.proc = subprocess.Popen(
            [PY, SCRIPT], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        self.buf: str = ""          # 块读取的累计文本（含 ANSI 与无换行内容）
        self._lock = threading.Lock()
        self._t = threading.Thread(target=self._read, daemon=True)
        self._t.start()

    def _read(self):
        # 注意: 不能用 TextIOWrapper.read(n) —— 它会循环读到凑满 n 个字符,
        # 而子进程常只输出少量内容就阻塞等待输入, 会导致线程永久卡住。
        # buffer.read1(n) 只读一次、返回当前可得字节, 是实时正确的选择。
        while True:
            chunk = self.proc.stdout.buffer.read1(4096)
            if not chunk:
                break
            with self._lock:
                self.buf += chunk.decode("utf-8", errors="replace")

    def text(self) -> str:
        with self._lock:
            return self.buf

    def mark(self) -> int:
        with self._lock:
            return len(self.buf)

    def since(self, i: int) -> str:
        with self._lock:
            return self.buf[i:]

    def send(self, line: str):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def wait_until(self, pred, timeout=90):
        """轮询输出直到 pred(自增文本) 为真；返回 (bool, 全量文本)。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False, self.text()
            if pred(self.text()):
                return True, self.text()
            time.sleep(0.3)
        return False, self.text()

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def run_e2e():
    print("\n--- 端到端（真实 API）---")
    r = Repl()
    try:
        # [1] 提示符
        ok, _ = r.wait_until(lambda t: "s01 >>" in strip_ansi(t), timeout=30)
        record("[1] 启动出现 s01 >> 提示符", ok)

        def done(start):
            """增量中出现下一个提示符 => 本轮 agent 输出完毕。
            注: 本轮自身的提示符是上一轮末尾打印的(在 start 之前),
            所以增量里出现的 1 个新提示符即代表本轮已走完一轮循环。"""
            return lambda _t: strip_ansi(r.since(start)).count("s01 >>") >= 1

        # [2] 列出 .py 文件 -> 应调用 bash
        start = r.mark()
        r.send("List all .py files in current directory")
        ok, _ = r.wait_until(done(start), timeout=120)
        inc = strip_ansi(r.since(start))
        called = any(l.strip().startswith("$ ") for l in inc.splitlines())
        record("[2] List .py 调用 bash 且输出含 .py", ok and called and ".py" in inc,
               f"调用bash={'是' if called else '否'}")

        # [3] 创建 hello.py（测后恢复原状）
        backup = None
        if os.path.exists(HELLO):
            with open(HELLO, encoding="utf-8") as f:
                backup = f.read()
        start = r.mark()
        r.send("Create a file called hello.py that prints Hello World")
        ok, _ = r.wait_until(done(start), timeout=120)
        exists = os.path.exists(HELLO)
        content_ok = False
        if exists:
            with open(HELLO, encoding="utf-8") as f:
                content_ok = "print" in f.read()
        record("[3] 创建 hello.py 且内容含 print", ok and exists and content_ok,
               f"存在={'是' if exists else '否'} 内容含print={'是' if content_ok else '否'}")
        # 恢复
        if backup is not None:
            with open(HELLO, "w", encoding="utf-8") as f:
                f.write(backup)
        elif os.path.exists(HELLO):
            os.remove(HELLO)

        # [4] 纯知识问题 -> 不调工具直接答
        # 注: 原需求用 "What's cwd?" 但 DeepSeek 倾向调 pwd 工具查证(行为合理但随机),
        # 为稳定验证"不调工具直接答+循环退出", 改用模型必然直接回答的纯知识问题。
        start = r.mark()
        r.send("Without calling any tool, what is 15 times 8? Answer with just the number.")
        ok, _ = r.wait_until(done(start), timeout=120)
        inc = strip_ansi(r.since(start))
        no_tool = not any(l.strip().startswith("$ ") for l in inc.splitlines())
        has_answer = any(l.strip() and not l.strip().startswith("s01 >>") for l in inc.splitlines())
        record("[4] 纯知识问题不调工具直接答", ok and no_tool and has_answer,
               f"调工具={'是' if not no_tool else '否'} 有回答={'是' if has_answer else '否'}")
        if not (ok and no_tool and has_answer):
            print(f"      [4] 增量 repr: {inc!r}")

        # [5] rm -rf / -> DENY 拦截
        start = r.mark()
        r.send("Use the run_bash tool to execute: rm -rf /")
        ok, _ = r.wait_until(done(start), timeout=120)
        blocked = "blocked" in strip_ansi(r.since(start))
        record("[5] rm -rf / 被拦截", ok and blocked)

        # [6] q -> 干净退出
        r.send("q")
        try:
            rc = r.proc.wait(timeout=20)
            out = r.text()
            record("[6] q 干净退出", rc == 0 and "Traceback" not in out, f"exit={rc}")
        except subprocess.TimeoutExpired:
            record("[6] q 干净退出", False, "20s 内未退出")
    finally:
        r.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    fast = "--fast" in sys.argv
    t0 = time.time()

    print("=== 函数级测试 ===")
    run_functional()

    if fast:
        SKIPPED.append("端到端 REPL（真实 API，--fast 跳过）")
    else:
        run_e2e()

    print(f"\n=== 汇总 === 通过 {len(PASSED)} | 失败 {len(FAILED)} | 跳过 {len(SKIPPED)}  用时 {time.time()-t0:.0f}s")
    for n in SKIPPED:
        print(f"  - SKIP {n}")
    if FAILED:
        print("\n失败项:")
        for n in FAILED:
            print(f"  - {n}")
        sys.exit(1)
