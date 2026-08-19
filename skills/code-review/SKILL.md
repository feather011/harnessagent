---
name: code-review
description: 审查代码时的检查清单（安全 / 健壮性 / 可观测性）
---

# code-review 技能

审查代码时逐项检查：

1. **安全**：危险命令是否被权限闸门覆盖（DENY_LIST / PERMISSION_RULES）；路径是否受 safe_path 约束
2. **健壮性**：JSON 解析失败、工具抛异常、subagent 超轮次是否有兜底，不崩
3. **可观测性**：关键路径（工具调用、权限拒绝、子 agent 启停）是否有控制台标记
4. **编码**：Windows 下 stdin/stdout UTF-8 适配；messages 入模型前清洗 surrogate
5. **规划**：多步任务是否先 todo_write 列计划、按状态流转
