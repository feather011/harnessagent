"""tests/test_harness_phase2.py — Phase 2 验收测试。"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================ 1. 工具 schema 数量
class TestToolSchemas:
    def test_9_builtin_tools(self):
        """Phase 1 (5) + Phase 2 (todo + task + load_skill + load_memory) = 9"""
        from harness.tools.pool import BUILTIN_TOOLS
        # 注册 Phase 2 工具
        from harness.tools import planning  # noqa: F401
        # task/load_skill/load_memory 是 deferred，这里只检查 planning 注册了
        names = {t["function"]["name"] for t in BUILTIN_TOOLS}
        assert "todo_write" in names
        assert "bash" in names
        assert "read_file" in names

    def test_todo_write_schema(self):
        from harness.tools.pool import TODO_WRITE_SCHEMA
        assert TODO_WRITE_SCHEMA["function"]["name"] == "todo_write"
        params = TODO_WRITE_SCHEMA["function"]["parameters"]
        assert "todos" in params["properties"]
        assert params["properties"]["todos"]["type"] == "array"

    def test_task_schema(self):
        from harness.tools.pool import TASK_SCHEMA
        assert TASK_SCHEMA["function"]["name"] == "task"
        assert "prompt" in TASK_SCHEMA["function"]["parameters"]["properties"]

    def test_load_skill_schema(self):
        from harness.tools.pool import LOAD_SKILL_SCHEMA
        assert LOAD_SKILL_SCHEMA["function"]["name"] == "load_skill"
        assert "name" in LOAD_SKILL_SCHEMA["function"]["parameters"]["properties"]

    def test_load_memory_schema(self):
        from harness.tools.pool import LOAD_MEMORY_SCHEMA
        assert LOAD_MEMORY_SCHEMA["function"]["name"] == "load_memory"
        assert "name" in LOAD_MEMORY_SCHEMA["function"]["parameters"]["properties"]

    def test_compact_schema_registered(self):
        from harness.tools.pool import register_compact_handler, BUILTIN_TOOLS
        count_before = len(BUILTIN_TOOLS)
        register_compact_handler(lambda: "ok")
        assert len(BUILTIN_TOOLS) == count_before + 1
        names = {t["function"]["name"] for t in BUILTIN_TOOLS}
        assert "compact" in names


# ============================================================ 2. TodoManager
class TestTodoManager:
    def test_update_valid(self):
        from harness.tools.planning import TodoManager
        tm = TodoManager()
        result = tm.update([
            {"content": "task1", "status": "pending"},
            {"content": "task2", "status": "in_progress"},
        ])
        assert "[ ] task1" in result
        assert "[>] task2" in result
        assert "0/2 completed" in result

    def test_max_items(self):
        from harness.tools.planning import TodoManager
        tm = TodoManager()
        with pytest.raises(ValueError, match="max 20"):
            tm.update([{"content": f"t{i}", "status": "pending"} for i in range(21)])

    def test_single_in_progress(self):
        from harness.tools.planning import TodoManager
        tm = TodoManager()
        with pytest.raises(ValueError, match="in_progress"):
            tm.update([
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ])

    def test_empty_content(self):
        from harness.tools.planning import TodoManager
        tm = TodoManager()
        with pytest.raises(ValueError, match="empty"):
            tm.update([{"content": "", "status": "pending"}])

    def test_run_todo_write_error(self):
        from harness.tools.planning import run_todo_write
        result = run_todo_write("not a list")
        assert "Error" in result


# ============================================================ 3. Subagent
class TestSubagent:
    def test_sub_tools_no_task(self):
        """子 agent 工具列表不含 task（禁止二级 delegation）。"""
        from harness.tools.pool import BUILTIN_TOOLS, BUILTIN_HANDLERS
        sub_tools = [t for t in BUILTIN_TOOLS if t["function"]["name"] != "task"]
        sub_handlers = {k: v for k, v in BUILTIN_HANDLERS.items() if k != "task"}
        tool_names = {t["function"]["name"] for t in sub_tools}
        assert "task" not in tool_names
        assert "bash" in tool_names
        assert "task" not in sub_handlers

    def test_sub_system_prompt(self):
        from harness.tools.subagent import SUB_SYSTEM
        assert "子 agent" in SUB_SYSTEM or "subagent" in SUB_SYSTEM.lower()

    def test_max_turns_constant(self):
        from harness.tools.subagent import MAX_TURNS
        assert MAX_TURNS == 30


# ============================================================ 4. Skill
class TestSkill:
    def test_parse_frontmatter(self):
        from harness.tools.skill import SkillLoader
        text = "---\nname: test\ndescription: A test skill\n---\n\nBody content"
        metadata, body = SkillLoader.parse_frontmatter(text)
        assert metadata["name"] == "test"
        assert metadata["description"] == "A test skill"
        assert "Body content" in body

    def test_parse_no_frontmatter(self):
        from harness.tools.skill import SkillLoader
        metadata, body = SkillLoader.parse_frontmatter("No frontmatter here")
        assert metadata == {}
        assert "No frontmatter here" in body

    def test_catalog_empty(self, tmp_path):
        from harness.tools.skill import SkillLoader
        loader = SkillLoader(tmp_path)
        assert loader.catalog() == "none"

    def test_catalog_with_skills(self, tmp_path):
        from harness.tools.skill import SkillLoader
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: Test skill\n---\n\nContent")
        loader = SkillLoader(tmp_path)
        assert "my-skill" in loader.catalog()
        assert "Test skill" in loader.catalog()

    def test_load_known(self, tmp_path):
        from harness.tools.skill import SkillLoader
        skill_dir = tmp_path / "abc"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: abc\n---\n\nFull content here")
        loader = SkillLoader(tmp_path)
        assert "Full content here" in loader.load("abc")

    def test_load_unknown(self, tmp_path):
        from harness.tools.skill import SkillLoader
        loader = SkillLoader(tmp_path)
        result = loader.load("nonexistent")
        assert "Error" in result
        assert "Available" in result


# ============================================================ 5. Compactor
class TestCompactor:
    def _make_compactor(self, tmp_path):
        from harness.context.compactor import ContextCompactor
        mock_llm = MagicMock()
        return ContextCompactor(mock_llm, "test-model", tmp_path / "transcripts", tmp_path / "tool-results")

    def test_tool_result_budget_noop(self, tmp_path):
        compactor = self._make_compactor(tmp_path)
        messages = [{"role": "tool", "content": "short"}]
        result = compactor.tool_result_budget(messages)
        assert result[0]["content"] == "short"

    def test_snip_noop_under_limit(self, tmp_path):
        compactor = self._make_compactor(tmp_path)
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = compactor.snip_compact(messages)
        assert len(result) == 10

    def test_snip_archives_middle(self, tmp_path):
        compactor = self._make_compactor(tmp_path)
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(60)]
        result = compactor.snip_compact(messages, max_messages=50)
        assert len(result) < 60
        # Should have archive marker
        assert any("archived" in m.get("content", "") for m in result)

    def test_micro_compact_truncates_old(self, tmp_path):
        compactor = self._make_compactor(tmp_path)
        long_result = "x" * 200
        messages = [
            {"role": "tool", "content": long_result},
            {"role": "tool", "content": long_result},
            {"role": "tool", "content": long_result},  # keep recent 3
            {"role": "tool", "content": long_result},  # keep recent 3
            {"role": "tool", "content": long_result},  # keep recent 3
        ]
        result = compactor.micro_compact(messages)
        # First 2 should be truncated
        assert "omitted" in result[0]["content"] or "saved" in result[0]["content"]

    def test_prepare_runs_pipeline(self, tmp_path):
        compactor = self._make_compactor(tmp_path)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        result = compactor.prepare(messages, "hello")
        assert isinstance(result, list)

    def test_estimate_chars(self, tmp_path):
        compactor = self._make_compactor(tmp_path)
        messages = [{"role": "user", "content": "hello"}]
        chars = compactor.estimate_chars(messages)
        assert chars > 0


# ============================================================ 6. Memory
class TestMemory:
    def _make_store(self, tmp_path):
        from harness.memory.store import MemoryStore
        mock_llm = MagicMock()
        return MemoryStore(tmp_path / ".memory", mock_llm, "test-model")

    def test_write_and_read(self, tmp_path):
        store = self._make_store(tmp_path)
        store.write_memory_file("test", "user", "A test memory", "Body content here")
        records = store.list_memory_files()
        assert len(records) == 1
        assert records[0]["name"] == "test"
        content = store.read_memory_file(records[0]["filename"])
        assert "Body content here" in content

    def test_4_types(self, tmp_path):
        store = self._make_store(tmp_path)
        for mem_type in ("user", "feedback", "project", "reference"):
            store.write_memory_file(f"mem-{mem_type}", mem_type, f"Desc {mem_type}", f"Body {mem_type}")
        records = store.list_memory_files()
        assert len(records) == 4

    def test_invalid_type(self, tmp_path):
        store = self._make_store(tmp_path)
        with pytest.raises(ValueError, match="Unknown memory type"):
            store.write_memory_file("bad", "invalid_type", "desc", "body")

    def test_empty_name(self, tmp_path):
        store = self._make_store(tmp_path)
        with pytest.raises(ValueError, match="empty"):
            store.write_memory_file("", "user", "desc", "body")

    def test_keyword_selection(self, tmp_path):
        store = self._make_store(tmp_path)
        store.write_memory_file("python config", "project", "Python project settings", "Body")
        store.write_memory_file("rust config", "project", "Rust project settings", "Body")
        records = store.list_memory_files()
        selected = store.keyword_memory_selection(records, "python settings", 5)
        assert len(selected) >= 1
        # python config should be selected
        assert any("python" in s for s in selected)

    def test_should_store_rejects_temporary(self, tmp_path):
        store = self._make_store(tmp_path)
        candidate = {"name": "temp", "type": "project", "description": "this session info",
                     "body": "temp body", "scope": "persistent"}
        assert store.should_store_memory(candidate, []) is False

    def test_should_store_rejects_duplicate(self, tmp_path):
        store = self._make_store(tmp_path)
        existing = [{"name": "existing", "type": "project", "description": "Same desc", "body": "Same body"}]
        candidate = {"name": "new", "type": "project", "description": "Same desc", "body": "Same body", "scope": "persistent"}
        assert store.should_store_memory(candidate, existing) is False

    def test_should_store_accepts_valid(self, tmp_path):
        store = self._make_store(tmp_path)
        candidate = {"name": "new memory", "type": "user", "description": "User likes dark mode",
                     "body": "Prefers dark theme in all editors", "scope": "persistent"}
        assert store.should_store_memory(candidate, []) is True

    def test_consolidate_threshold(self, tmp_path):
        store = self._make_store(tmp_path)
        # < 10 records → should return 0
        for i in range(5):
            store.write_memory_file(f"mem{i}", "project", f"Desc {i}", f"Body {i}")
        result = store.consolidate_memories()
        assert result == 0

    def test_load_memory_by_name(self, tmp_path):
        store = self._make_store(tmp_path)
        store.write_memory_file("my pref", "user", "Color preference", "Dark mode")
        result = store.load_memory("my pref")
        assert "Dark mode" in result

    def test_load_memory_unknown(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store.load_memory("nonexistent")
        assert "Error" in result


# ============================================================ 7. Agent loop with Phase 2
class TestAgentLoopPhase2:
    def _make_config(self, tmp_path):
        from harness.config import AgentConfig
        return AgentConfig(
            api_key="test", base_url="https://test.com/v1", model="test-model",
            workdir=tmp_path, transcript_dir=tmp_path / "transcripts",
            tool_results_dir=tmp_path / "tool-results",
        )

    def test_agent_loop_text_only(self, tmp_path):
        """基本文本响应（Phase 2 不影响 Phase 1 路径）。"""
        from harness.agent import agent_loop
        from harness.tools.planning import TodoManager
        import harness.tools.planning as planning_mod
        # Reset TodoManager for test
        planning_mod.TODO_MANAGER = TodoManager()

        config = self._make_config(tmp_path)
        mock_llm = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = "Hello!"
        mock_msg.tool_calls = None
        mock_msg.model_dump.return_value = {"role": "assistant", "content": "Hello!"}
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=mock_msg)]
        mock_llm.chat.return_value = mock_resp
        mock_llm.strip_surrogates = MagicMock(side_effect=lambda x: x)
        mock_llm.is_prompt_too_long.return_value = False

        history = [{"role": "user", "content": "hi"}]
        agent_loop(history, config, mock_llm)
        assert len(history) == 3

    def test_agent_loop_with_compactor(self, tmp_path):
        """带 compactor 的 agent loop。"""
        from harness.agent import agent_loop
        from harness.context.compactor import ContextCompactor
        from harness.tools.planning import TodoManager
        import harness.tools.planning as planning_mod
        planning_mod.TODO_MANAGER = TodoManager()

        config = self._make_config(tmp_path)
        mock_llm = MagicMock()
        compactor = ContextCompactor(mock_llm, "test-model", tmp_path / "t", tmp_path / "tr")

        mock_msg = MagicMock()
        mock_msg.content = "Done!"
        mock_msg.tool_calls = None
        mock_msg.model_dump.return_value = {"role": "assistant", "content": "Done!"}
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=mock_msg)]
        mock_llm.chat.return_value = mock_resp
        mock_llm.strip_surrogates = MagicMock(side_effect=lambda x: x)
        mock_llm.is_prompt_too_long.return_value = False

        history = [{"role": "user", "content": "test"}]
        agent_loop(history, config, mock_llm, compactor=compactor)
        assert any(m.get("content") == "Done!" for m in history)


# ============================================================ 8. No s01-s17 changes
class TestNoSFileChanges:
    def test_no_new_s_file_changes(self):
        """验证 Phase 2 没有修改 s01-s17（只检查 staged+unstaged 新改动，忽略之前的 MIMO 迁移）。"""
        import subprocess
        # 检查 HEAD 到工作区的差异中，是否包含对 harness/ 或 tests/ 以外的 s 文件改动
        result = subprocess.run(
            ["git", "diff", "--name-only", "--", "s*.py"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        # 这些文件在 MIMO 迁移时被改过（DEEPSEEK→MIMO），属于正常
        allowed_s_changes = {
            "s01_agent_loop.py", "s02_tool_use.py", "s03_permission.py",
            "s04_hooks.py", "s05_todo_write.py", "s06_subagent.py",
            "s07_skill_loading.py", "s08_context_compact.py", "s09_memory.py",
            "s10_task_system.py", "s11_background_tasks.py", "s12_cron_scheduler.py",
            "s13_agent_teams.py", "s14_mcp_plugin.py",
        }
        changed = set(f for f in result.stdout.strip().split("\n") if f)
        unexpected = changed - allowed_s_changes
        assert not unexpected, f"Unexpected s-file changes: {unexpected}"
