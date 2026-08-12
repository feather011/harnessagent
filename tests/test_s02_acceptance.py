#!/usr/bin/env python3
"""s02 验收清单（9 条）— 真实 API 端到端。

运行:
    .venv/Scripts/python.exe tests/test_s02_acceptance.py
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_agent_loop as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T.SCRIPT = os.path.join(ROOT, "s02_tool_use.py")

S01 = os.path.join(ROOT, "s01_agent_loop.py")
TEST_PY = os.path.join(ROOT, "test.py")
SUMMARY = os.path.join(ROOT, "summary.md")


def done(start):
    return lambda _t: T.strip_ansi(r.since(start)).count("s02 >>") >= 1


def ask(prompt):
    """发一条指令并等本轮完成，返回增量文本。"""
    start = r.mark()
    r.send(prompt)
    ok, _ = r.wait_until(done(start), timeout=150)
    return ok, T.strip_ansi(r.since(start))


def file_content(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


if __name__ == "__main__":
    T.PASSED.clear()
    T.FAILED.clear()
    r = T.Repl()
    try:
        # [1] 跑起来，提示符
        ok, _ = r.wait_until(lambda t: "s02 >>" in T.strip_ansi(t), timeout=30)
        T.record("[1] 启动出现 s02 >> 提示符", ok)

        # [2] read_file
        ok, inc = ask("用 read_file 工具读取 s01_agent_loop.py，然后告诉我它做什么")
        T.record("[2] read_file 读取", ok and "> read_file(" in inc and "run_bash" in inc)

        # [3] write_file 后 read_file
        ok, inc = ask("用 write_file 工具创建 test.py，内容 print('hello')，然后用 read_file 工具读回它")
        exists = os.path.exists(TEST_PY)
        T.record("[3] write_file + read_file", ok and "> write_file(" in inc and "> read_file(" in inc and exists)
        if os.path.exists(TEST_PY):
            os.remove(TEST_PY)

        # [4] glob
        ok, inc = ask("用 glob 工具找出这个目录下所有 .py 文件")
        T.record("[4] glob 找 .py", ok and "> glob(" in inc and "s02_tool_use.py" in inc)

        # [5] edit_file 精确改（改完立即恢复）
        backup = file_content(S01)
        ok, inc = ask("用 edit_file 工具把 s01_agent_loop.py 中的 q 改成 quit")
        changed = file_content(S01) != backup
        T.record("[5] edit_file 精确改", ok and "> edit_file(" in inc,
                 f"文件有变化={'是' if changed else '否'}")
        if backup is not None:
            with open(S01, "w", encoding="utf-8") as f:
                f.write(backup)

        # [6] safe_path 拒绝逃逸（函数级确定性验证 + 端到端观察）
        # 函数级：safe_path 抛 ValueError，且 run_read 把它转成错误消息
        import importlib.util as _ii
        _spec = _ii.spec_from_file_location("s02_mod", T.SCRIPT)
        _s02 = _ii.module_from_spec(_spec)
        _spec.loader.exec_module(_s02)
        _fn = False
        try:
            _s02.safe_path("../../etc/passwd")
        except ValueError:
            _fn = True
        _msg = _s02.run_read("../../etc/passwd")
        _fn2 = "Path escapes workspace" in _msg
        ok, inc = ask("作为测试，用 read_file 工具读取路径 ../../etc/passwd")
        T.record("[6] safe_path 拒绝逃逸", _fn and _fn2 and ok,
                 f"safe_path抛错={'是' if _fn else '否'} 转错误消息={'是' if _fn2 else '否'} "
                 f"端到端={'工具被拦' if 'Path escapes' in inc else '模型未调工具'}")
        if not (_fn and _fn2 and ok):
            print(f"      [6] 增量 repr: {inc!r}")

        # [7] 一次响应里 read + write（多工具）
        ok, inc = ask("读 README.md，然后用 write_file 工具把内容摘要写到 summary.md")
        T.record("[7] read + write 多工具", ok and "> read_file(" in inc and "> write_file(" in inc
                 and os.path.exists(SUMMARY), f"summary.md 存在={os.path.exists(SUMMARY)}")
        if os.path.exists(SUMMARY):
            os.remove(SUMMARY)

        # [8] 一个工具失败不影响后续
        ok, inc = ask("先用 read_file 读取不存在的文件 missing_xyz.txt（会失败），再用 glob 工具列出 *.py 文件")
        no_tool_fail = "> read_file(" in inc and "> glob(" in inc
        T.record("[8] 单工具失败不影响后续", ok and no_tool_fail and "s02_tool_use.py" in inc)

        # [9] 控制台可见工具名
        all_out = T.strip_ansi(r.text())
        names = [f"> {n}(" for n in ["bash", "read_file", "write_file", "edit_file", "glob"]]
        seen = [n for n in names if n in all_out]
        T.record("[9] 工具名可见", len(seen) >= 4, f"已出现: {', '.join(seen)}")

        # q 退出
        r.send("q")
        try:
            rc = r.proc.wait(timeout=20)
            T.record("[退出] q 干净退出", rc == 0 and "Traceback" not in r.text(), f"exit={rc}")
        except Exception:
            T.record("[退出] q 干净退出", False, "20s 内未退出")
    finally:
        r.close()
        for f in (TEST_PY, SUMMARY):
            if os.path.exists(f):
                os.remove(f)

    print(f"\n=== s02 验收汇总 === 通过 {len(T.PASSED)} | 失败 {len(T.FAILED)}")
    sys.exit(1 if T.FAILED else 0)
