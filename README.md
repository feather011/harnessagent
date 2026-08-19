# 🚀 harness

**Production-grade coding agent package** — 35+ tools, 5 daemon threads, workflow orchestration, goal loop.

Built on [MiMo mimo-v2.5](https://github.com/XiaoMi/MiMo) (OpenAI-compatible API). 155 tests. Python 3.14.

## ✨ Features

| Category | Tools | Description |
|----------|-------|-------------|
| 🔧 Base | `bash` `read_file` `write_file` `edit_file` `glob` | Core file & shell operations |
| 📋 Planning | `todo_write` | Task list with status tracking |
| 🤖 Subagent | `task` | Spawn independent sub-agents |
| 📚 Skill | `load_skill` | Load YAML-frontmatter skill docs |
| 🧠 Memory | `load_memory` | 4-subsystem memory (storage/recall/extract/consolidate) |
| 🗜️ Context | `compact` | 4-step context compaction pipeline |
| 📊 Task System | `create_task` `list_tasks` `get_task` `claim_task` `complete_task` | Persistent task management with dependencies |
| ⏰ Cron | `schedule_cron` `list_crons` `cancel_cron` | 5-field cron expressions with durable persistence |
| 👥 Team | `spawn_teammate` `list_teammates` `send_message` `broadcast` `request_shutdown` `request_plan` `review_plan` `create_worktree` | Multi-agent collaboration with plan gates |
| 🌲 Worktree | `remove_worktree` | Git worktree management |
| 🔌 MCP | `connect_mcp` + 4 dynamic tools | Mock docs/deploy MCP servers |
| 🔄 Workflow | `workflow` | 6-primitive orchestration engine |
| 🎯 Goal | `/goal` CLI | Goal loop with automatic evaluation |

## 🏗️ Architecture

```
User Input
    │
    ▼
┌─────────────────────────────────────┐
│           agent_loop (6 phases)     │
│  1. UserPromptSubmit hook           │
│  2. Drain events (cron/bg/team)     │
│  3. Memory recall + system prompt   │
│  4. Compactor + LLM call            │
│  5. Goal Stop hook                  │
│  6. Tool dispatch (35+ tools)       │
└──────────┬──────────────────────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
┌────────┐  ┌────────────┐
│ Hooks  │  │ Permission │
│ (5)    │  │ (3 gates)  │
└────────┘  └────────────┘
           │
    ┌──────┴──────────────────────────┐
    ▼         ▼        ▼       ▼      ▼
┌───────┐ ┌──────┐ ┌─────┐ ┌─────┐ ┌──────┐
│  Cron │ │  BG  │ │Team │ │ MCP │ │fcntl │
│daemon │ │daemon│ │thrd │ │dyn  │ │locks │
└───────┘ └──────┘ └─────┘ └─────┘ └──────┘
```

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/feather011/harnessagent.git
cd harnessagent

# 2. Install dependencies
pip install openai pyyaml python-dotenv

# 3. Configure API key
cp .env.example .env
# Edit .env and set MIMO_API_KEY

# 4. Run
python -m harness
```

## ⚙️ Configuration

`.env` file:

```env
MIMO_API_KEY=your-api-key-here
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5
```

Works with any OpenAI-compatible API. For DeepSeek:

```env
MIMO_API_KEY=your-deepseek-key
MIMO_BASE_URL=https://api.deepseek.com
MIMO_MODEL=deepseek-chat
```

## 🎮 Demo

### Todo Task Management
```
harness >> 用 todo_write 列出今天任务
→ Model calls todo_write tool → renders task checklist
```

### Cron Scheduled Jobs
```
harness >> schedule_cron '*/2 * * * *' 'check status'
→ Registers cron job → fires [Scheduled] every 2 minutes
```

### Background Bash
```
harness >> bash(command='sleep 3', run_in_background=true)
→ Returns [Background task bg_0001 started]
→ <task_notification> arrives after completion
```

### Multi-Agent Team
```
harness >> spawn_teammate alice
→ Alice daemon thread starts
harness >> send_message alice 'review code quality'
→ Alice processes message, executes tools, returns results
```

### Goal Loop
```
harness >> /goal python -c 'print(1)' exits 0
→ Goal set → agent works → evaluator checks each turn
→ pass / block / defer until achieved or budget exhausted
```

### Workflow Orchestration
```
harness >> workflow(name='review-changes', args={'target': 'staged'})
→ Runs 5-dimension parallel audit + verify pipeline
→ Returns structured findings as <task_notification>
```

## 🧪 Testing

```bash
# Run all 155 tests
python -m pytest tests/ -v

# Run specific phase
python -m pytest tests/test_harness_phase1.py -v
```

## 📁 Project Structure

```
harness/
├── agent.py          # Core agent loop (6 phases)
├── cli.py            # python -m harness entry point
├── config.py         # AgentConfig + load_config()
├── llm.py            # LLMClient (OpenAI SDK wrapper)
├── errors.py         # Exception hierarchy
├── hooks/            # 5 built-in hooks
├── permission/       # 3-gate permission system
├── tools/            # 35+ tool implementations
├── context/          # 4-step context compactor
├── memory/           # 4-subsystem memory store
├── background/       # Background bash manager
├── teams/            # Multi-agent runtime + message bus
├── workflow/         # Workflow engine (6 primitives)
└── goal/             # Goal loop controller
```

## 📄 License

MIT

## 🤝 Contributing

This package is a production rewrite of a 17-chapter teaching project. The `archive/teaching-history` branch preserves the full learning history (s01-s17, ~16,000 lines).

Contributions welcome! See `harness/README.md` for module-level documentation.
