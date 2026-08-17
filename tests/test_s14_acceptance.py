#!/usr/bin/env python3
"""s14 验收（真实 API，单进程多轮）。

A: 启动出现 s14 >> 提示符
B: connect_mcp(docs) → mcp__docs__search / get_version 路由到 docs server
C: connect_mcp(deploy) → mcp__deploy__status 路由
D: 错误参数 → MCP error，loop 不挂
prefix 防冲突 / host policy confirm / 64 限制走函数级（tests/_s14_functional.py）。
"""
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s14_mcp_plugin.py")


class Session:
    def __init__(self, prompt_timeout=40):
        self.r = T.Repl()
        ok, _ = self.r.wait_until(lambda t: "s14 >>" in T.strip_ansi(t), timeout=prompt_timeout)
        self.launched = ok

    def ask(self, prompt, timeout=300):
        start = self.r.mark()
        self.r.send(prompt)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.r.proc.poll() is not None:
                break
            inc = T.strip_ansi(self.r.since(start))
            if inc.count("s14 >>") >= 1:
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
    T.record("[A] 启动出现 s14 >> 提示符", s.launched)

    # ---- B：connect docs + search + get_version ----
    done, inc = s.ask(
        "用 connect_mcp 连接 docs server，然后用 mcp__docs__search 搜索 'agent hooks'，"
        "再调用 mcp__docs__get_version 获取版本。", timeout=300)
    T.record("[B] connect_mcp + MCP 工具路由到 docs",
             done and "> connect_mcp(" in inc and "> mcp__docs__search(" in inc
             and "> mcp__docs__get_version(" in inc and "[docs]" in inc,
             f"hit_search={'mcp__docs__search' in inc} hit_docs={('[docs]' in inc)}")

    # ---- C：connect deploy + status ----
    done, inc = s.ask(
        "用 connect_mcp 连接 deploy server，然后用 mcp__deploy__status 检查 web 服务的状态。",
        timeout=300)
    T.record("[C] mcp__deploy__status 路由到 deploy",
             done and "> connect_mcp(" in inc and "> mcp__deploy__status(" in inc
             and "[deploy]" in inc,
             f"hit={'mcp__deploy__status' in inc}")

    # ---- D：错误参数 → MCP error，loop 不挂 ----
    done, inc = s.ask(
        "调用 mcp__docs__search，但故意不传 query 参数（测试错误处理），"
        "然后告诉我返回了什么错误。", timeout=300)
    T.record("[D] 错误参数不挂 loop",
             done and ("MCP error" in inc or "TypeError" in inc),
             f"hit={'MCP error' in inc or 'TypeError' in inc}")
    s.close()

    print(f"\n=== s14 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
