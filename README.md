# 🚀 harness

**Production 级 coding agent 框架** — 35+ 工具，5 套 daemon 线程，Workflow 编排，Goal 目标循环。

基于 [MiMo mimo-v2.5](https://github.com/XiaoMi/MiMo)（OpenAI 兼容 API）。155 个测试。Python 3.14。

## ✨ 功能一览

| 分类 | 工具 | 说明 |
|------|------|------|
| 🔧 基础 | `bash` `read_file` `write_file` `edit_file` `glob` | 文件与 Shell 操作 |
| 📋 规划 | `todo_write` | 任务清单管理，状态追踪 |
| 🤖 子 Agent | `task` | 启动独立子 agent 执行子任务 |
| 📚 技能 | `load_skill` | 加载 YAML frontmatter 技能文档 |
| 🧠 记忆 | `load_memory` | 4 子系统记忆（存储/召回/提取/整合） |
| 🗜️ 上下文 | `compact` | 4 步上下文压缩管线 |
| 📊 任务系统 | `create_task` `list_tasks` `get_task` `claim_task` `complete_task` | 持久化任务管理，支持依赖 |
| ⏰ 定时任务 | `schedule_cron` `list_crons` `cancel_cron` | 5 字段 cron 表达式，持久化 |
| 👥 团队 | `spawn_teammate` `list_teammates` `send_message` `broadcast` `request_shutdown` `request_plan` `review_plan` `create_worktree` | 多 agent 协作，Plan Gate 审批 |
| 🌲 Worktree | `remove_worktree` | Git worktree 管理 |
| 🔌 MCP | `connect_mcp` + 4 个动态工具 | Mock docs/deploy MCP 服务器 |
| 🔄 Workflow | `workflow` | 6 原语编排引擎 |
| 🎯 Goal | `/goal` CLI | 目标循环，自动评估 |

## 🏗️ 架构

```
用户输入
    │
    ▼
┌─────────────────────────────────────┐
│           agent_loop（6 阶段）       │
│  1. UserPromptSubmit hook           │
│  2. 事件排空（cron/bg/team）         │
│  3. 记忆召回 + 系统提示词            │
│  4. 压缩 + LLM 调用                 │
│  5. Goal Stop hook                  │
│  6. 工具 dispatch（35+ 工具）        │
└──────────┬──────────────────────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
┌────────┐  ┌────────────┐
│ Hooks  │  │ 权限系统    │
│ (5)    │  │ (3 道闸)   │
└────────┘  └────────────┘
           │
    ┌──────┴──────────────────────────┐
    ▼         ▼        ▼       ▼      ▼
┌───────┐ ┌──────┐ ┌─────┐ ┌─────┐ ┌──────┐
│  Cron │ │ 后台 │ │团队 │ │ MCP │ │ 文件 │
│daemon │ │daemon│ │线程 │ │动态 │ │ 锁   │
└───────┘ └──────┘ └─────┘ └─────┘ └──────┘
```

## 🚀 快速开始

```bash
# 1. 克隆
git clone https://github.com/feather011/harnessagent.git
cd harnessagent

# 2. 安装依赖
pip install openai pyyaml python-dotenv

# 3. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 MIMO_API_KEY

# 4. 启动
python -m harness
```

## ⚙️ 配置

`.env` 文件：

```env
MIMO_API_KEY=你的 API Key
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5
```

兼容任何 OpenAI 协议 API。DeepSeek 配置：

```env
MIMO_API_KEY=你的 DeepSeek Key
MIMO_BASE_URL=https://api.deepseek.com
MIMO_MODEL=deepseek-chat
```

## 🎮 演示

### 任务管理
```
harness >> 用 todo_write 列出今天任务
→ 模型调用 todo_write → 渲染 [ ] / [>] / [x] 任务清单
```

### 定时任务
```
harness >> schedule_cron '*/2 * * * *' '检查状态'
→ 注册 cron job → 每 2 分钟触发 [Scheduled] 检查状态
```

### 后台执行
```
harness >> bash(command='sleep 3', run_in_background=true)
→ 返回 [Background task bg_0001 started]
→ 完成后收到 <task_notification> 通知
```

### 多 Agent 团队
```
harness >> spawn_teammate alice
→ alice daemon 线程启动
harness >> send_message alice '请检查代码质量'
→ alice 处理消息，执行工具，返回结果
```

### Goal 目标循环
```
harness >> /goal python -c 'print(1)' 退出码 0
→ 设置目标 → agent 工作 → 每轮评估 pass/block/defer
→ 直到达成或预算耗尽
```

### Workflow 编排
```
harness >> workflow(name='review-changes', args={'target': 'staged'})
→ 5 维度并行审计 + 逐条验证 → 结构化结果
```

## 🧪 测试

```bash
# 运行全部 155 个测试
python -m pytest tests/ -v

# 运行特定阶段
python -m pytest tests/test_harness_phase1.py -v
```

## 📁 项目结构

```
harness/
├── agent.py          # 核心 agent_loop（6 阶段）
├── cli.py            # python -m harness 入口
├── config.py         # AgentConfig + load_config()
├── llm.py            # LLMClient（OpenAI SDK 封装）
├── errors.py         # 异常层级
├── hooks/            # 5 个内置 hook
├── permission/       # 3 道闸权限系统
├── tools/            # 35+ 工具实现
├── context/          # 4 步上下文压缩器
├── memory/           # 4 子系统记忆存储
├── background/       # 后台 bash 管理器
├── teams/            # 多 agent 运行时 + 消息总线
├── workflow/         # Workflow 引擎（6 原语）
└── goal/             # Goal 目标循环控制器
```

## 📄 License

MIT

## 🤝 贡献

欢迎贡献！`harness/README.md` 包含模块级文档。

教学历史（s01-s17，~16,000 行）保留在 `archive/teaching-history` 分支。
