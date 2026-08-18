"""harness.config — AgentConfig dataclass + load_config()。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from harness.errors import HarnessError

load_dotenv(override=True)


@dataclass
class AgentConfig:
    api_key: str
    base_url: str = "https://api.xiaomimimo.com/v1"
    model: str = "mimo-v2.5"
    max_tokens: int = 8000
    workdir: Path = field(default_factory=Path.cwd)
    # Phase 2
    skills_dir: Path = field(default=None)
    memory_dir: Path = field(default=None)
    transcript_dir: Path = field(default=None)
    tool_results_dir: Path = field(default=None)

    def __post_init__(self):
        if self.skills_dir is None:
            self.skills_dir = self.workdir / "skills"
        if self.memory_dir is None:
            self.memory_dir = self.workdir / ".memory"
        if self.transcript_dir is None:
            self.transcript_dir = self.workdir / ".transcripts"
        if self.tool_results_dir is None:
            self.tool_results_dir = self.workdir / ".task_outputs" / "tool-results"


def load_config() -> AgentConfig:
    """从环境变量加载配置。缺少 api_key 时抛出 HarnessError。"""
    api_key = os.getenv("MIMO_API_KEY", "")
    if not api_key:
        raise HarnessError("MIMO_API_KEY not set. Add it to .env or environment.")
    return AgentConfig(
        api_key=api_key,
        base_url=os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
        model=os.getenv("MIMO_MODEL", "mimo-v2.5"),
    )
