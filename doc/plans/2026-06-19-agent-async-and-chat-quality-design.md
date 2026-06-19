# Agent 异步化与对话质量收敛设计

## 目标

保留 AI 对话的 SSE 实时体验，同时把非聊天场景中触发 Agent 分析的入口改为后台异步执行，避免用户在页面长时间等待。同步收敛对话链路中的路由、上下文、持久化和失败恢复逻辑，提高回复质量和可恢复性。

## 范围

本轮覆盖：

- 指数深度分析 `/api/analysis/run`
- 持仓分散度 AI 解读 `/api/portfolio/analysis/diversification/ai-summary`
- 通用 AI 持仓分析 `/api/portfolio/analysis/ai`
- 已有异步持仓 4 模式的状态返回一致性
- 聊天 SSE 链路中的消息去重、路由结果使用、状态落库、断线恢复一致性

本轮不引入 Celery、Redis 或新的外部队列服务。后台任务继续使用 FastAPI 进程内 `asyncio.create_task`，状态落到 SQLite 现有记录表。

## 后端设计

### 状态模型

持仓分析继续复用 `portfolio_analysis_records`：

- `status`: `running` / `done` / `error`
- `result_data`: 完成后的 Markdown 结果
- `token_usage`: LLM token 用量
- `error_msg`: 失败原因

指数分析扩展 `analysis_history`：

- 新增 `status`
- 新增 `error_msg`
- `result` 允许先写空字符串，任务完成后更新

所有触发分析的 API 都先创建记录并返回：

```json
{"ok": true, "id": 123, "status": "running"}
```

前端通过状态接口轮询：

- 持仓分析：复用 `/api/portfolio/analysis/{record_id}/status`
- 指数分析：新增 `/api/analysis/history/{history_id}/status`

### 异步执行

同步入口改造为“创建记录 -> 后台执行 -> 更新记录”：

- `/api/analysis/run` 创建 `analysis_history(status=running)`，后台执行估值、RAG、新闻、LLM、自动评估。
- `/api/portfolio/analysis/diversification/ai-summary` 创建 `portfolio_analysis_records(status=running)`，后台执行 MCP、估值匹配、LLM。
- `/api/portfolio/analysis/ai` 创建 `portfolio_analysis_records(status=running)`，后台执行 MCP、RAG、LLM。
- 已异步入口保留现状，统一状态字段和前端轮询。

### 对话质量

聊天继续使用 `/api/conversations/{conv_id}/messages/stream` 的 SSE。

本轮收敛点：

- 修复 `chat` 分支重复写用户消息的问题。
- 所有分支保存 assistant metadata 时带 `execution_status=completed`。
- simple 分支优先使用 `clarification.refined_query` 调用专家，但保留原始用户问题用于展示和日志。
- 低置信度或无效专家路由时走关键词兜底。
- 专家上下文统一使用已裁剪的 RAG 与持仓上下文，避免重复注入过长数据。
- 输出审核后的内容再落库，保证页面刷新和 SSE 最终内容一致。

## 前端设计

分析按钮只等待任务创建，不等待 LLM 完成。

用户体验：

- 按钮进入“分析中”状态后立即释放网络长等待。
- 页面显示任务状态文案：“分析已在后台运行，可切换页面。”
- 轮询状态接口，完成后把结果写回当前卡片和历史列表。
- 失败时展示 `error_msg`，允许用户再次触发。

重点组件：

- `frontend/src/api/index.js` 增加指数分析状态查询和轮询工具。
- `ValuationHistory.vue` 的指数深度分析改为创建任务后轮询。
- `PortfolioManagement.vue` 的分散度 AI 和通用 AI 持仓分析改为创建任务后轮询。
- `Dashboard.vue` 的全景诊断按钮接入已有 `pollPanoramaStatus`。

## 测试

后端测试重点：

- 创建指数分析任务立即返回 `running`。
- 后台成功后状态为 `done` 并返回结果。
- 后台失败后状态为 `error` 并写入 `error_msg`。
- 聊天 `chat` 分支不重复写用户消息。

前端测试重点：

- API 路径仍保持 `/api` 前缀规范。
- 轮询工具在 `done/error` 时停止。
- 组件能处理 `running/done/error` 三种状态。

