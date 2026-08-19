"""harness.tools.skill — SkillLoader + load_skill 工具。"""

from pathlib import Path

import yaml

from harness.tools.pool import register_tool, LOAD_SKILL_SCHEMA

# Deferred init: cli.py calls init_skill_loader(skills_dir)
SKILL_LOADER = None


class SkillLoader:
    """扫描 skills/*/SKILL.md，YAML frontmatter 解析，name→内容 注册表。"""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, dict[str, str]] = {}
        self.scan()

    @staticmethod
    def parse_frontmatter(text: str) -> tuple[dict, str]:
        """解析 YAML frontmatter（--- 包裹），返回 (metadata dict, 正文)。"""
        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text
        try:
            metadata = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, parts[2].lstrip()

    def scan(self):
        self.skills.clear()
        if not self.skills_dir.exists():
            return
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            content = manifest.read_text(encoding="utf-8", errors="replace")
            metadata, body = self.parse_frontmatter(content)
            name = str(metadata.get("name") or manifest.parent.name).strip()
            description = metadata.get("description") or body.splitlines()[0]
            description = " ".join(str(description).lstrip("# ").split())
            self.skills[name] = {"name": name, "description": description, "content": content}

    def catalog(self) -> str:
        if not self.skills:
            return "none"
        return "\n".join(f"- {s['name']}: {s['description']}" for s in self.skills.values())

    def load(self, name: str) -> str:
        """按 name 查注册表返回 SKILL.md 全文。name 不是路径，查字典防越权。"""
        skill = self.skills.get(name)
        if skill:
            return skill["content"]
        available = ", ".join(self.skills) or "none"
        return f"Error: Unknown skill '{name}'. Available: {available}"


def init_skill_loader(skills_dir: Path):
    """由 cli.py 调用，初始化 SkillLoader 并注册 load_skill 工具。"""
    global SKILL_LOADER
    SKILL_LOADER = SkillLoader(skills_dir)

    def run_load_skill(name: str) -> str:
        if SKILL_LOADER is None:
            return "Error: SkillLoader not initialized"
        return SKILL_LOADER.load(name)

    register_tool(LOAD_SKILL_SCHEMA, run_load_skill)
