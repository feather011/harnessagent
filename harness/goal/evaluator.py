"""harness.goal.evaluator — PromptGoalEvaluator（独立 LLM 调用）。"""

import json

from harness.workflow.runner import SimpleJsonSchema


class PromptGoalEvaluator:
    """独立 LLM 调用评估 goal condition。"""

    EVAL_SCHEMA = SimpleJsonSchema(
        required=["ok", "reason"],
        types={"ok": bool, "reason": str, "impossible": bool},
    )
    KEEP_RECENT = 20
    TRUNCATE_THRESHOLD = 4000
    TRUNCATE_HEAD = 2000
    TRUNCATE_TAIL = 2000
    PROMPT_CHAR_LIMIT = 60000

    def __init__(self, runner=None):
        self.runner = runner

    def _truncate_messages(self, messages: list) -> list:
        """保留最近 N 条，单条 >4000 字符留首尾各 2000。"""
        recent = messages[-self.KEEP_RECENT:]
        truncated = []
        for m in recent:
            content = m.get("content", "")
            if isinstance(content, str) and len(content) > self.TRUNCATE_THRESHOLD:
                content = (content[:self.TRUNCATE_HEAD]
                           + "\n...[truncated]...\n"
                           + content[-self.TRUNCATE_TAIL:])
            truncated.append({**m, "content": content})
        return truncated

    def evaluate(self, condition: str, messages: list) -> dict:
        """5 paths: ok / impossible / block / error / non-dict。"""
        if self.runner is None:
            return {"ok": False, "reason": "Evaluator error: no runner configured", "impossible": False}

        truncated = self._truncate_messages(messages)
        prompt = (
            f"Your job is to evaluate whether the following goal has been achieved. "
            f"Base your answer ONLY on concrete actions or results shown in the conversation. "
            f"Reject claims with no supporting evidence.\n\n"
            f"Goal condition: {condition}\n\n"
            f"Conversation excerpt:\n{json.dumps(truncated, ensure_ascii=False)[:self.PROMPT_CHAR_LIMIT]}\n\n"
            f"Respond with JSON containing: ok (bool), reason (str), impossible (bool)."
        )
        try:
            result = self.runner.run(prompt, schema=self.EVAL_SCHEMA)
            if not isinstance(result, dict):
                return {"ok": False, "reason": f"Evaluator returned non-dict: {type(result).__name__}",
                        "impossible": False}
            result.setdefault("ok", False)
            result.setdefault("reason", "")
            result.setdefault("impossible", False)
            return result
        except Exception as e:
            return {"ok": False, "reason": f"Evaluator error: {type(e).__name__}: {e}",
                    "impossible": False}
