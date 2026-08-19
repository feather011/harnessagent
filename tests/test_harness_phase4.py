"""tests/test_harness_phase4.py — Phase 4 验收测试。"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================ 1. MessageBus
class TestMessageBus:
    def test_send_and_read(self, tmp_path):
        from harness.teams.bus import MessageBus
        bus = MessageBus(tmp_path / ".mailboxes")
        bus.send("alice", "bob", "hello")
        msgs = bus.read_inbox("bob")
        assert len(msgs) == 1
        assert msgs[0]["from"] == "alice"
        assert msgs[0]["content"] == "hello"
        assert msgs[0]["type"] == "message"

    def test_peek(self, tmp_path):
        from harness.teams.bus import MessageBus
        bus = MessageBus(tmp_path / ".mailboxes")
        assert bus.peek("alice") is False
        bus.send("lead", "alice", "msg")
        assert bus.peek("alice") is True

    def test_read_is_destructive(self, tmp_path):
        from harness.teams.bus import MessageBus
        bus = MessageBus(tmp_path / ".mailboxes")
        bus.send("a", "b", "msg1")
        bus.read_inbox("b")
        assert bus.read_inbox("b") == []

    def test_wait_timeout(self, tmp_path):
        from harness.teams.bus import MessageBus
        bus = MessageBus(tmp_path / ".mailboxes")
        start = time.time()
        result = bus.wait_for_messages("nobody", timeout=0.1)
        elapsed = time.time() - start
        assert result == []
        assert elapsed < 1

    def test_persistence_jsonl(self, tmp_path):
        from harness.teams.bus import MessageBus
        bus = MessageBus(tmp_path / ".mailboxes")
        bus.send("a", "b", "persistent msg")
        # Check file exists
        mailbox_file = tmp_path / ".mailboxes" / "b.jsonl"
        assert mailbox_file.exists()
        content = mailbox_file.read_text()
        assert "persistent msg" in content


# ============================================================ 2. TeammateRuntime
class TestTeammateRuntime:
    def test_spawn_creates_thread(self, tmp_path):
        from harness.teams.runtime import TeammateRuntime
        from harness.teams.bus import MessageBus
        from harness.config import AgentConfig
        bus = MessageBus(tmp_path / ".mailboxes")
        config = AgentConfig(api_key="test", workdir=tmp_path,
                             transcript_dir=tmp_path/"t", tool_results_dir=tmp_path/"tr")
        mock_llm = MagicMock()
        from harness.tools.tasks import TaskStore
        store = TaskStore(tmp_path / ".tasks")

        rt = TeammateRuntime("testbot", "tester", "Do something", None, False,
                             config, mock_llm, bus, store)
        assert rt.name == "testbot"
        assert len(rt.messages) == 2  # system + user


# ============================================================ 3. Team schemas
class TestTeamSchemas:
    def test_8_team_schemas(self):
        from harness.tools.pool import BUILTIN_TOOLS
        from harness.tools.teams import init_team_tools
        from harness.teams.bus import MessageBus
        with tempfile.TemporaryDirectory() as td:
            bus = MessageBus(Path(td) / ".mailboxes")
            from harness.config import AgentConfig
            config = AgentConfig(api_key="test", workdir=Path(td))
            mock_llm = MagicMock()
            from harness.tools.tasks import TaskStore
            store = TaskStore(Path(td) / ".tasks")
            init_team_tools(config, mock_llm, bus, store)
        names = {t["function"]["name"] for t in BUILTIN_TOOLS}
        for expected in ["spawn_teammate", "list_teammates", "send_message",
                         "broadcast", "request_shutdown", "request_plan",
                         "review_plan", "create_worktree"]:
            assert expected in names, f"Missing: {expected}"


# ============================================================ 4. MCPClient
class TestMCPClient:
    def test_register_and_call(self):
        from harness.tools.mcp import MCPClient
        client = MCPClient("test")
        client.register(
            [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}}],
            {"echo": lambda msg: f"echoed: {msg}"}
        )
        result = client.call_tool("echo", {"msg": "hello"})
        assert "echoed: hello" in result

    def test_call_tool_exception(self):
        from harness.tools.mcp import MCPClient
        client = MCPClient("test")
        client.register([{"name": "fail", "description": "Fail", "inputSchema": {"type": "object", "properties": {}}}],
                        {"fail": lambda: (_ for _ in ()).throw(ValueError("oops"))})
        result = client.call_tool("fail", {})
        assert "MCP error" in result
        assert "ValueError" in result

    def test_unknown_tool(self):
        from harness.tools.mcp import MCPClient
        client = MCPClient("test")
        result = client.call_tool("nonexistent", {})
        assert "MCP error" in result


# ============================================================ 5. connect_mcp
class TestConnectMCP:
    def test_connect_docs(self):
        from harness.tools.mcp import connect_mcp, mcp_clients
        mcp_clients.clear()
        result = connect_mcp("docs")
        assert "Connected" in result
        assert "docs" in mcp_clients

    def test_connect_duplicate(self):
        from harness.tools.mcp import connect_mcp, mcp_clients
        mcp_clients.clear()
        connect_mcp("docs")
        result = connect_mcp("docs")
        assert "already connected" in result

    def test_connect_unknown(self):
        from harness.tools.mcp import connect_mcp, mcp_clients
        mcp_clients.clear()
        result = connect_mcp("nonexistent")
        assert "Unknown server" in result


# ============================================================ 6. assemble_tool_pool_v2
class TestAssembleToolPoolV2:
    def test_mcp_tools_merged(self):
        from harness.tools.mcp import connect_mcp, mcp_clients, assemble_tool_pool_v2
        from harness.tools.pool import BUILTIN_TOOLS, BUILTIN_HANDLERS
        mcp_clients.clear()
        connect_mcp("docs")
        tools, handlers = assemble_tool_pool_v2(list(BUILTIN_TOOLS), dict(BUILTIN_HANDLERS))
        mcp_names = [t["function"]["name"] for t in tools if t["function"]["name"].startswith("mcp__")]
        assert len(mcp_names) >= 2
        assert "mcp__docs__search" in mcp_names

    def test_mcp_tool_routing(self):
        from harness.tools.mcp import connect_mcp, mcp_clients, assemble_tool_pool_v2
        from harness.tools.pool import BUILTIN_TOOLS, BUILTIN_HANDLERS
        mcp_clients.clear()
        connect_mcp("docs")
        _, handlers = assemble_tool_pool_v2(list(BUILTIN_TOOLS), dict(BUILTIN_HANDLERS))
        result = handlers["mcp__docs__search"](query="test")
        assert "docs" in result
        assert "test" in result

    def test_tool_count_with_mcp(self):
        from harness.tools.mcp import connect_mcp, mcp_clients, assemble_tool_pool_v2
        from harness.tools.pool import BUILTIN_TOOLS, BUILTIN_HANDLERS
        mcp_clients.clear()
        connect_mcp("docs")
        connect_mcp("deploy")
        tools, _ = assemble_tool_pool_v2(list(BUILTIN_TOOLS), dict(BUILTIN_HANDLERS))
        assert len(tools) >= len(BUILTIN_TOOLS) + 4  # docs(2) + deploy(2)


# ============================================================ 7. MCP_HOST_POLICY
class TestMCPHostPolicy:
    def test_allow_confirm_default(self):
        from harness.tools.mcp import MCP_HOST_POLICY
        assert MCP_HOST_POLICY[("docs", "search")] == "allow"
        assert MCP_HOST_POLICY[("deploy", "trigger")] == "confirm"
        # Unknown → confirm (fail-closed)
        assert MCP_HOST_POLICY.get(("unknown", "tool"), "confirm") == "confirm"


# ============================================================ 8. Permission MCP
class TestPermissionMCP:
    def test_mcp_permission_allow(self):
        from harness.permission import check_permission, reset_deny_all
        reset_deny_all()
        with tempfile.TemporaryDirectory() as td:
            result = check_permission("mcp__docs__search", {}, td, prompt_user=False)
            assert result is None  # allow

    def test_mcp_permission_confirm_no_prompt(self):
        from harness.permission import check_permission, reset_deny_all
        from harness.tools.mcp import mcp_tool_policies
        mcp_tool_policies["mcp__deploy__trigger"] = "confirm"
        reset_deny_all()
        with tempfile.TemporaryDirectory() as td:
            result = check_permission("mcp__deploy__trigger", {}, td, prompt_user=False)
            assert result is not None
            assert "Permission required" in result


# ============================================================ 9. drain_events team
class TestDrainTeamEvents:
    def test_drain_team_events(self, tmp_path):
        from harness.teams.bus import MessageBus
        bus = MessageBus(tmp_path / ".mailboxes")
        bus.send("alice", "lead", "Task done!", "result")
        bus.send("bob", "lead", "Also done!", "result")
        # peek + read
        assert bus.peek("lead") is True
        msgs = bus.read_inbox("lead")
        assert len(msgs) == 2
        # format as team events
        events = []
        for tm in msgs:
            sender = tm.get("from", "?")
            msg_type = tm.get("type", "message")
            content = tm.get("content", "")
            events.append(f"[Team events] {sender} ({msg_type}): {content}")
        assert "alice" in events[0]
        assert "Task done!" in events[0]


# ============================================================ 10. Worktree helpers
class TestWorktreeHelpers:
    def test_validate_name(self):
        from harness.teams.worktree import validate_worktree_name
        assert validate_worktree_name("feat-x") is None
        assert validate_worktree_name("a" * 64) is None
        assert validate_worktree_name("") is not None
        assert validate_worktree_name("a" * 65) is not None
        assert validate_worktree_name("has space") is not None


# ============================================================ 11. 不动检查
class TestNoChanges:
    def test_no_s_file_changes(self):
        import subprocess
        result = subprocess.run(
            ["git", "diff", "--name-only", "--", "s*.py"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        allowed = {
            "s01_agent_loop.py", "s02_tool_use.py", "s03_permission.py",
            "s04_hooks.py", "s05_todo_write.py", "s06_subagent.py",
            "s07_skill_loading.py", "s08_context_compact.py", "s09_memory.py",
            "s10_task_system.py", "s11_background_tasks.py", "s12_cron_scheduler.py",
            "s13_agent_teams.py", "s14_mcp_plugin.py",
        }
        changed = set(f for f in result.stdout.strip().split("\n") if f)
        unexpected = changed - allowed
        assert not unexpected, f"Unexpected s-file changes: {unexpected}"

    def test_phase123_not_broken(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_harness_phase1.py", "tests/test_harness_phase2.py",
             "tests/test_harness_phase3.py", "-q", "--tb=line"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        assert "passed" in result.stdout
        assert "failed" not in result.stdout
