---
name: agent-builder
description: 构建或扩展 coding agent 时的架构规范（工具 / hook / 权限边界）
---

# agent-builder 技能

构建或扩展 coding agent 时遵循：

1. **新工具** = TOOLS 加一条 function schema + TOOL_HANDLERS 加一行映射，不动 loop
2. **新 hook** = 写 callback + `register_hook` 注册，事件选 UserPromptSubmit / PreToolUse / PostToolUse / Stop
3. **危险命令**必须进 DENY_LIST（硬拒）或 PERMISSION_RULES（问用户）
4. **文件访问**统一走 safe_path，逃逸工作区即拒绝
5. **打印**用户/工具内容前过 `_strip_surrogates`，防 openai SDK 序列化崩溃
