# HarnessAgent

一个从零构建的 coding agent 实验项目。

## 项目结构

```
harnessagent/
├── harness/          # Production 重写（~3,800 行 + 145 测试）
├── s01_agent_loop.py # 教学版第 1 章
├── s02_tool_use.py   # 教学版第 2 章
├── ...               # s03-s17（共 17 章）
├── tests/            # 测试
└── .env              # API 配置
```

**两套代码并存**：
- **s01-s17**：17 章教学版，每章独立文件，从零逐步构建 agent（~16,000 行）
- **harness/**：Production 重写，借鉴 s01-s17 设计，模块化 package 结构（~3,800 行）

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/feather011/harnessagent.git
cd harnessagent

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 MIMO_API_KEY（MiMo mimo-v2.5）

# 3. 虚拟环境 + 依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install openai pyyaml python-dotenv

# 4. 启动
python -m harness
```

## harness/ 功能

Production-grade coding agent，35+ 工具，5 套 daemon 线程：

- **工具**：bash / 文件操作 / todo / 任务系统 / 定时任务 / 后台执行 / 多 agent 团队 / MCP 插件 / Workflow 编排 / Goal 循环
- **安全**：3 道权限闸（deny_list → rules → ask_user）
- **上下文**：4 步压缩 pipeline + reactive 限流
- **记忆**：4 子系统（Storage / Recall / Extraction / Consolidation）

详细文档见 [harness/README.md](harness/README.md)。

## s01-s17 教学历史

| 章 | 文件 | 内容 |
|----|------|------|
| 01 | s01_agent_loop.py | Minimal agent loop + bash tool |
| 02 | s02_tool_use.py | 5 基础工具 |
| 03 | s03_permission.py | 权限闸门 |
| 04 | s04_hooks.py | 5 个 hook |
| 05 | s05_todo_write.py | Todo 任务清单 |
| 06 | s06_subagent.py | 子 agent 嵌套 |
| 07 | s07_skill_loading.py | Skill 加载 |
| 08 | s08_context_compact.py | 上下文压缩 |
| 09 | s09_memory.py | 记忆系统 |
| 10 | s10_task_system.py | 任务系统 |
| 11 | s11_background_tasks.py | 后台任务 |
| 12 | s12_cron_scheduler.py | 定时调度 |
| 13 | s13_agent_teams.py | 多 agent 团队 |
| 14 | s14_mcp_plugin.py | MCP 插件 |
| 15 | s15_integrated_harness.py | 整合 wiring |
| 16 | s16_workflow_runtime.py | Workflow 编排 |
| 17 | s17_goal_loop.py | Goal 目标循环 |

运行教学版：`python s17_goal_loop.py`（最新章节）

## 技术栈

- **模型**：MiMo mimo-v2.5（小米，OpenAI 协议兼容）
- **SDK**：openai 3.0.0
- **语言**：Python 3.14
- **平台**：Windows + Git Bash
