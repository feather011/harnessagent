"""harness.llm — LLMClient 封装 OpenAI SDK + surrogate 清洗。"""

import json
import os
import uuid

import httpx
from openai import OpenAI

from harness.config import AgentConfig
from harness.errors import HarnessError

# 模型 HTTP 请求超时（LLM 调用级别）
_LLM_TIMEOUT_SECONDS = float(os.getenv("HARNESS_MODEL_TIMEOUT_SECONDS", "120"))

# ── Fake LLM（非交互模式测试用）──────────────────────────────────────────────

_HELLO_C_CONTENT = '#include <stdio.h>\n\nint main() {\n    printf("hello world\\n");\n    return 0;\n}\n'


def _make_fake_resp(role: str, content: str | None = None,
                    tool_calls: list | None = None) -> object:
    """构造一个 resp-like 对象（与 OpenAI SDK 返回值结构一致）。

    tool_calls 里的每个元素必须是有 .function 属性的对象，
    与 agent_loop 里 call.function.name / call.function.arguments 一致。
    """
    def _make_tc(tc_dict: dict) -> object:
        fn = tc_dict.get("function", {})
        # call.id + call.function.name / call.function.arguments（agent_loop 期望）
        inner = type("F", (), {
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", ""),
        })()
        return type("TC", (), {
            "id": tc_dict.get("id", f"call_{uuid.uuid4().hex[:8]}"),
            "function": inner,
        })()

    msg_parts: dict = {"role": role}
    if content is not None:
        msg_parts["content"] = content
    if tool_calls is not None:
        msg_parts["tool_calls"] = [_make_tc(tc) for tc in tool_calls]
    msg_obj = type("Msg", (), {
        "content": content,
        "tool_calls": msg_parts.get("tool_calls"),
        "model_dump": lambda self, **kw: {
            k: v for k, v in msg_parts.items() if v is not None
        },
    })()
    return type("Resp", (), {"choices": [type("C", (), {"message": msg_obj})()]})()


class FakeLLMClient:
    """脚本化 LLM，用于测试：第一次写 hello.c，第二次返回答案。"""

    def __init__(self, config: AgentConfig):
        self._model = config.model
        self._call_count = 0
        # 测试用：注入延迟来触发超时（环境变量秒）
        self._delay = float(os.getenv("HARNESS_FAKE_LLM_DELAY", "0"))

    @property
    def model(self) -> str:
        return self._model

    def chat(self, messages: list, tools: list | None = None,
             max_tokens: int | None = None):
        import time
        self._call_count += 1
        if self._delay > 0:
            time.sleep(self._delay)
        # 统计已有 tool 消息
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            # 第一次：返回 write_file 工具调用
            args = json.dumps({"path": "hello.c", "content": _HELLO_C_CONTENT},
                              ensure_ascii=False)
            tc = [{"id": f"call_{uuid.uuid4().hex[:8]}",
                   "type": "function",
                   "function": {"name": "write_file", "arguments": args}}]
            return _make_fake_resp("assistant", tool_calls=tc)
        # 第二次及以后：返回纯文本答案
        return _make_fake_resp("assistant",
                               content="已在 hello.c 中创建 hello world 程序。")

    @staticmethod
    def is_prompt_too_long(error: Exception) -> bool:
        return False


# ── 真实 LLMClient ───────────────────────────────────────────────────────────

class LLMClient:
    """OpenAI SDK 封装，统一 surrogate 清洗和 prompt_too_long 检测。"""

    def __init__(self, config: AgentConfig):
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=httpx.Client(timeout=httpx.Timeout(_LLM_TIMEOUT_SECONDS)),
        )
        self._model = config.model
        self._max_tokens = config.max_tokens

    @property
    def model(self) -> str:
        return self._model

    def chat(self, messages: list, tools: list | None = None, max_tokens: int | None = None):
        """调用 chat completions，输入前自动清洗 surrogates。"""
        clean = self.strip_surrogates(messages)
        kwargs = {"model": self._model, "messages": clean, "max_tokens": max_tokens or self._max_tokens}
        if tools:
            kwargs["tools"] = tools
        return self._client.chat.completions.create(**kwargs)

    @staticmethod
    def strip_surrogates(value):
        """递归清洗 str 中的孤立 surrogate 字符（openai SDK 3.0.0 序列化会崩）。"""
        if isinstance(value, str):
            return "".join(c for c in value if not 0xD800 <= ord(c) <= 0xDFFF)
        if isinstance(value, dict):
            return {k: LLMClient.strip_surrogates(v) for k, v in value.items()}
        if isinstance(value, list):
            return [LLMClient.strip_surrogates(v) for v in value]
        return value

    @staticmethod
    def is_prompt_too_long(error: Exception) -> bool:
        """检查异常是否为 prompt 超长错误。"""
        msg = str(error).lower()
        return "prompt_too_long" in msg or "too many tokens" in msg
