# harness agent package

Production-grade coding agent harness，基于 MiMo mimo-v2.5。

## 架构

```
用户输入 → agent_loop (6 阶段) → 工具 dispatch (35+ 工具) → daemon 线程池
                ↓                      ↓
          hooks (5 事件)         tools (12 类)
                ↓                      ↓
          permission (3 道闸)    MCP 动态工具
```

## 模块

| 模块 | 说明 |
|------|------|
| config | AgentConfig dataclass + load_config() 读 .env |
| errors | HarnessError / ToolError / PermissionDenied 异常层级 |
| llm | LLMClient 封装 OpenAI SDK + surrogate 清洗 |
| agent | 唯一 agent_loop（6 阶段 + reactive 限流 + Goal Stop） |
| cli | `python -m harness` 入口 + /goal CLI 解析 |
| hooks | 5 内置 hook（UserPromptSubmit / PreToolUse×2 / PostToolUse / Stop） |
| permission | 3 道闸（deny_list → rules → ask_user）+ MCP 权限 |
| tools/* | 35 工具实现（12 类） |
| context | ContextCompactor 4 步压缩 pipeline |
| memory | MemoryStore 4 子系统（Storage/Recall/Extraction/Consolidation） |
| background | BackgroundManager 后台 bash 任务 |
| teams | TeammateRuntime + MessageBus 线程安全文件邮箱 |
| workflow | RunContext 6 原语 + Journal + @workflow 装饰器 + review-changes |
| goal | GoalController + PromptGoalEvaluator 目标循环 |

## 35+ 工具

### Base (5)
bash, read_file, write_file, edit_file, glob

### Planning (1)
todo_write

### Subagent (1)
task

### Skill (1)
load_skill

### Memory (1)
load_memory

### Context (1)
compact

### Task System (5)
create_task, list_tasks, get_task, claim_task, complete_task

### Cron (3)
schedule_cron, list_crons, cancel_cron

### Team (8)
spawn_teammate, list_teammates, send_message, broadcast, request_shutdown, request_plan, review_plan, create_worktree

### Worktree (1)
remove_worktree

### MCP (1 + 4 动态)
connect_mcp → mcp__docs__search, mcp__docs__get_version, mcp__deploy__trigger, mcp__deploy__status

### Workflow (1)
workflow

## 5 个 Daemon 线程

| 线程 | 职责 |
|------|------|
| CronScheduler | 每秒 poll cron 表达式，到点入队 |
| BackgroundManager | 每个后台 bash 一个 daemon thread |
| TeammateRuntime | 每个 teammate 一个 daemon thread |
| Workflow runner | 每个 workflow 执行一个 daemon thread |
| Cron queue processor | 消费 cron 队列 |

## 快速开始

```bash
pip install openai pyyaml python-dotenv
cp .env.example .env  # 填入 MIMO_API_KEY
python -m harness
```

## 5 个 Demo 场景

### 场景 1: Todo 任务管理
```
harness >> 用 todo_write 列出今天任务
→ 模型调 todo_write 工具
→ 渲染 [ ] / [>] / [x] 任务清单
```

### 场景 2: Cron 定时任务
```
harness >> schedule_cron '*/2 * * * *' 'check status'
→ 注册 cron job
→ 等 2 分钟后收到 [Scheduled] check status
```

### 场景 3: 后台 Bash
```
harness >> bash(command='sleep 3', run_in_background=true)
→ 返回 [Background task bg_0001 started]
→ 3 秒后收到 <task_notification> 完成通知
```

### 场景 4: Teammate 多 Agent
```
harness >> spawn_teammate alice
→ alice daemon 线程启动
harness >> send_message alice '请检查代码质量'
→ alice 收到消息，执行工具，返回结果
```

### 场景 5: Goal 目标循环
```
harness >> /goal python -c 'print(1)' 退出码 0
→ Goal 设置: python -c 'print(1)' 退出码 0
→ agent_loop 立即触发工作
→ 每轮后评估: pass / block / defer
```

## 技术栈

MiMo mimo-v2.5 / OpenAI SDK 3.0.0 / Python 3.14
