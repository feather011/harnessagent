"""harness.config — AgentConfig dataclass + load_config()。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from harness.errors import HarnessError

# 从 HARNESS_ENV_FILE 环境变量读取 .env 路径。
# 由 HarnessRunner.run() 在 subprocess 启动前注入（harness_root/.env）。
# 未设置时尝试从 harnessagent 根目录加载。
_env_file = os.environ.get("HARNESS_ENV_FILE")
if _env_file:
    load_dotenv(_env_file, override=True)
else:
    _default = (Path(__file__).parent.parent / ".env").resolve()
    if _default.exists():
        load_dotenv(_default, override=True)


@dataclass
class AgentConfig:
    api_key: str
    base_url: str = "https://api.xiaomimimo.com/v1"
    model: str = "mimo-v2.6-flash"
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
    """从环境变量加载配置。优先 MiniMax，其次 MiMo。缺少 api_key 时抛出 HarnessError。"""
    api_key = os.getenv("MINIMAX_API_KEY", "")
    # MiniMax 有两个端点：Anthropic 兼容（/anthropic）和 OpenAI 兼容（/v1）。
    # harness 使用 OpenAI SDK，必须用 /v1 端点。
    base_url = os.getenv("MINIMAX_OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    model = os.getenv("MINIMAX_MODEL", "MiniMax-M3")
    if not api_key:
        api_key = os.getenv("MIMO_API_KEY", "")
        base_url = os.getenv("HARNESS_MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
        model = os.getenv("MIMO_MODEL", "mimo-v2.6-flash")
    if not api_key:
        raise HarnessError("MINIMAX_API_KEY nor MIMO_API_KEY set. Add one to .env.")
    return AgentConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
