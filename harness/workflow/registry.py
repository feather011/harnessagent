"""harness.workflow.registry — WORKFLOWS dict + validate_meta + @workflow 装饰器 + review-changes sample。"""

import re
from typing import Callable

from harness.workflow.context import RunContext
from harness.workflow.task import TaskStatus
from harness.workflow.runner import SimpleJsonSchema

_SLUG = re.compile(r"^[a-zA-Z0-9._-]+$")

WORKFLOWS: dict[str, tuple[dict, Callable]] = {}


def validate_meta(meta: dict) -> tuple[bool, str | None]:
    """校验 workflow meta：name slug + description 非空 + phases 非空。"""
    name = meta.get("name")
    if not isinstance(name, str) or not _SLUG.fullmatch(name) or len(name) > 64:
        return False, f"workflow name must be 1-64 chars matching {_SLUG.pattern}; got {name!r}"
    if not isinstance(meta.get("description"), str) or not meta["description"].strip():
        return False, "description must be non-empty string"
    phases = meta.get("phases")
    if not isinstance(phases, list) or not phases or not all(isinstance(p, str) for p in phases):
        return False, "phases must be a non-empty list of strings"
    return True, None


def workflow(meta: dict):
    """装饰器：注册到 WORKFLOWS。"""
    ok, err = validate_meta(meta)
    if not ok:
        raise ValueError(f"workflow meta invalid: {err}")
    name = meta["name"]

    def decorator(script_fn: Callable) -> Callable:
        if name in WORKFLOWS:
            raise ValueError(f"workflow {name!r} already registered")
        WORKFLOWS[name] = (meta, script_fn)
        return script_fn

    return decorator


# ============================================================ Sample: review-changes
DIMENSIONS = ["security", "performance", "style", "error-handling", "testing"]

FINDINGS_SCHEMA = SimpleJsonSchema(required=["findings"], types={"findings": list})
VERDICT_SCHEMA = SimpleJsonSchema(required=["isReal"], types={"isReal": bool})


@workflow({
    "name": "review-changes",
    "description": "Review staged changes across multiple dimensions, then verify each finding.",
    "phases": ["Review", "Verify"],
})
def review_changes(ctx: RunContext) -> None:
    """2 phases: Review（5 dimensions 并行）+ Verify（per finding）。"""
    args = ctx.args or {}
    target = args.get("target", "current working tree")

    def audit(dimension: str) -> dict:
        prompt = (f"Audit the changes ({target}) for {dimension} issues. "
                  f"Return JSON with 'findings' list of objects with 'text' and 'severity'.")
        return ctx.agent(name=f"audit-{dimension}", prompt=prompt, schema=FINDINGS_SCHEMA)

    def verify(finding: dict) -> dict:
        text = finding.get("text", "")
        prompt = f"Verify if this finding is real (not false positive). Return JSON with 'isReal' (bool). Finding: {text}"
        return ctx.agent(name=f"verify-{text[:20]}", prompt=prompt, schema=VERDICT_SCHEMA)

    ctx.phase("Review")
    audits = ctx.parallel(DIMENSIONS, audit)

    ctx.phase("Verify")
    findings = [f for audit_result in audits for f in audit_result.get("findings", [])]
    verdicts = ctx.parallel(findings, verify) if findings else []
    real_findings = [findings[i] for i, v in enumerate(verdicts) if v.get("isReal")]

    ctx.final(TaskStatus.COMPLETED, output={
        "dimensions": DIMENSIONS,
        "audits": audits,
        "findings_count": len(findings),
        "real_findings": real_findings,
    })
