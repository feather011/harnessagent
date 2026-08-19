"""harness.workflow.runner — AgentRunner Protocol + RealAgentRunner + MockAgentRunner + SimpleJsonSchema。"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class SimpleJsonSchema:
    """简单的 JSON schema 校验器。"""

    def __init__(self, required: list[str], types: dict[str, type] | None = None):
        self.required = required
        self.types = types or {}

    def validate(self, output: Any) -> tuple[bool, str | None]:
        if not isinstance(output, dict):
            return False, f"expected dict, got {type(output).__name__}"
        for f in self.required:
            if f not in output:
                return False, f"missing required field: {f}"
        for f, expected_type in self.types.items():
            if f in output and not isinstance(output[f], expected_type):
                return False, f"field {f!r} expected {expected_type.__name__}, got {type(output[f]).__name__}"
        return True, None

    def schema_repr(self) -> str:
        return json.dumps({"required": self.required,
                           "types": {k: t.__name__ for k, t in self.types.items()}},
                          sort_keys=True)


class AgentRunner(Protocol):
    def run(self, prompt: str, schema: SimpleJsonSchema | None = None) -> dict: ...


def _json_schema_type(python_type: type) -> str:
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
    return mapping.get(python_type, "string")


class RealAgentRunner:
    """用 MiMo/OpenAI SDK 调 LLM，schema 用 tool_use 强结构化 JSON。"""

    def __init__(self, config=None):
        self._config = config
        self._llm = None
        if config:
            from harness.llm import LLMClient
            self._llm = LLMClient(config)

    def run(self, prompt: str, schema: SimpleJsonSchema | None = None) -> dict:
        if schema is None:
            resp = self._llm.chat([{"role": "user", "content": prompt}], max_tokens=4000)
            content = resp.choices[0].message.content or ""
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"result": content}

        tool = {
            "type": "function",
            "function": {
                "name": "submit",
                "description": "Submit the structured result.",
                "parameters": {
                    "type": "object",
                    "properties": {f: {"type": _json_schema_type(t)} for f, t in schema.types.items()},
                    "required": schema.required,
                },
            },
        }
        resp = self._llm.chat(
            [{"role": "user", "content": prompt}],
            tools=[tool],
        )
        for call in resp.choices[0].message.tool_calls or []:
            try:
                return json.loads(call.function.arguments)
            except json.JSONDecodeError:
                continue
        raise RuntimeError("model did not produce valid JSON via tool_call")


class MockAgentRunner:
    """测试用：基于关键词返伪数据；calls 记录调用历史。"""

    def __init__(self, responses: dict[str, dict] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str | None]] = []

    def run(self, prompt: str, schema: SimpleJsonSchema | None = None) -> dict:
        self.calls.append((prompt, schema.schema_repr() if schema else None))
        for key, resp in self.responses.items():
            if key.lower() in prompt.lower():
                return resp
        # Default: 返回符合 schema 的空 dict
        if schema:
            return {f: ("" if schema.types.get(f) == str else False if schema.types.get(f) == bool else [])
                    for f in schema.required}
        return {"result": "mock response"}
