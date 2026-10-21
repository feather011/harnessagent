"""harness.config — AgentConfig dataclass + load_config()。

懒加载策略：不在模块导入时加载 .env，而是在首次调用 load_config() 时
加载 HARNESS_ENV_FILE（由 HarnessRunner 在子进程启动前注入）。
这样测试通过 HarnessRunner(..., env_file=...) 注入的测试控制 .env 能生效，
不会受到上一次运行残留的 os.environ 状态影响。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from harness.errors import HarnessError


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


_loaded: bool = False


def _ensure_env_loaded() -> None:
    """懒加载 .env：只在首次调用 load_config() 前执行一次。

    用 override=False：进程环境里已显式设置的变量优先于 .env 文件，
    避免部署环境的配置被仓库里的 .env 静默覆盖。
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    env_file = os.environ.get("HARNESS_ENV_FILE")
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=False)
    else:
        default = (Path(__file__).parent.parent / ".env").resolve()
        if default.exists():
            load_dotenv(default, override=False)


def load_config() -> AgentConfig:
    """从环境变量加载配置。优先 MiniMax，其次 MiMo。缺少 api_key 时抛出 HarnessError。"""
    _ensure_env_loaded()
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
