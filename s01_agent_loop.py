#!/usr/bin/env python3
"""01.agent_loop: 一个调用 DeepSeek 的简易 coding agent 循环。

相对参考代码的适配：
- openai 3.0.0 移除了 max_temperature，改用其支持的 max_tokens；temperature 不传，走 DeepSeek 默认。
- Windows + Git Bash 环境：run_bash 优先用 bash 执行，避免 cmd.exe 缺少 unix 命令。
- 控制台强制 UTF-8，避免中文输出在 Windows 终端乱码。
- 补充 SYSTEM 进对话、超时/坏参数处理、截断提示等健壮性处理。
"""
import json
import os
import shutil
import subprocess
import sys

from dotenv import load_dotenv
from openai import OpenAI

# Windows 终端默认 GBK，强制 UTF-8 保证中文输出正常
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()
client = OpenAI(
    api_key=os.getenv("MIMO_API_KEY"),
    base_url=os.getenv("MIMO_BASE_URL"),
)
MODEL = os.getenv("MIMO_MODEL", "mimo-v2.5")

SYSTEM = "你是一个 coding agent，工作在 Windows 下的 Git Bash 环境。直接干活，不要解释。"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "在项目环境的 shell（优先 Git Bash，找不到则回退 cmd）中执行一条命令并返回输出。用于读文件、装依赖、跑脚本、git 操作等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                },
                "required": ["command"],
            },
        },
    }
]

DENY = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]

_BASH = shutil.which("bash")


def run_bash(command: str) -> str:
    if any(d in command for d in DENY):
        return "Error: Dangerous command blocked"
    try:
        if _BASH:
            r = subprocess.run(
                [_BASH, "-c", command], capture_output=True,
                text=True, errors="replace", timeout=30,
            )
        else:
            r = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, errors="replace", timeout=30,
            )
    except subprocess.TimeoutExpired:
        return "Error: command timed out (30s)"
    out = (r.stdout + r.stderr).strip()
    if not out:
        out = "(no output)"
    if len(out) > 50000:
        out = out[:50000] + "\n... (output truncated)"
    return out


def agent_loop(messages):
    while True:
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS, max_tokens=8000,
        )
        msg = resp.choices[0].message
        messages.append(msg)
        if not msg.tool_calls:
            # 最终文本回答
            if msg.content:
                print(f"\033[32m{msg.content}\033[0m", flush=True)
            return
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments)
                command = args["command"]
            except (json.JSONDecodeError, KeyError):
                messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": "Error: bad function arguments",
                })
                continue
            print(f"\033[33m$ {command}\033[0m", flush=True)
            output = run_bash(command)
            print(output[:200], flush=True)
            messages.append({
                "role": "tool", "tool_call_id": call.id, "content": output,
            })


if __name__ == "__main__":
    history = [{"role": "system", "content": SYSTEM}]
    print(f"\033[36m使用模型 {MODEL}，输入 q / exit / 空行退出\033[0m", flush=True)
    while True:
        try:
            q = input("\033[36ms01 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": q})
        agent_loop(history)
        print(flush=True)
