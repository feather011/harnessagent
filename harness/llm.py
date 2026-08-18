"""harness.llm — LLMClient 封装 OpenAI SDK + surrogate 清洗。"""

from openai import OpenAI

from harness.config import AgentConfig
from harness.errors import HarnessError


class LLMClient:
    """OpenAI SDK 封装，统一 surrogate 清洗和 prompt_too_long 检测。"""

    def __init__(self, config: AgentConfig):
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
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
