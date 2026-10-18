# 估值分析修复 Implementation Plan

> **For agentic workers:** 使用 dispatching-parallel-agents 按文件边界实现，并执行独立代码审阅。

**Goal:** 修正超性价比筛选、指数 AI 的指标与证据契约、前端任务与刷新，并测试后提交远程分支。

**Architecture:** 规则筛选输出带来源/主指标/日期的候选快照，两个深度分析入口复用该快照。模型仅补充解释，确定性字段由后端控制。指数 AI 使用明确的主指标，前端按指数和指标跟踪任务与结果。

**Tech Stack:** FastAPI、SQLite、Pydantic、Vue 3、pytest、Vitest。

## 1. 筛选与深度分析后端

- [ ] 在 `backend/tests/test_super_value_contract.py` 添加隔离回归，覆盖股息率反向、高估急跌不准入、来源时效、日期范围、PS/股息率在线指标、模型越权字段及失败状态；先运行并确认失败。
- [ ] 修改 `backend/routers/market/valuation.py`，必要时提取 `backend/services/valuation/opportunity_screener.py`：按指标方向统一便宜程度；便宜分位不超过30才准入；同一来源序列独立评分，选新鲜可用来源。明确三类数据覆盖，不混合机构历史。
- [ ] 两个深度入口共用扫描结果；数据补全不得伪装成其他指标，不可比数据单独标识；候选和规则字段不可由模型覆盖，错误/关闭状态明确返回。
- [ ] 运行 `python3 -m pytest backend/tests/test_super_value_contract.py -q`，确认通过。

## 2. 指数 AI 后端

- [ ] 在 `backend/tests/test_index_analysis_contract.py` 添加主指标传递、历史按指标/真实日期描述、证据元数据和缺失降级的失败用例。
- [ ] 修改 `backend/models/analysis.py`、`backend/routers/analysis/index_analysis.py`，必要时给 `backend/db/analysis.py` 增加向后兼容元数据：显式传指标、保留来源日期、检索移出事件循环、保留新闻日期URL、输出缺失警示；任务去重包含指标。
- [ ] 运行对应 pytest，确认通过。

## 3. 前端

- [ ] 在 `frontend/src/__tests__/valuationAnalysisState.test.js` 添加指标请求、多任务跟踪、历史切换竞态、刷新和空状态的失败用例。
- [ ] 修改 `ValuationHistory.vue`、`api/index.js`，需要时增加独立 composable：任务按指数/指标隔离，恢复后台任务，历史响应版本保护，候选刷新、空结果替换与前N解释合并；显示数据质量及分析关闭/失败状态。
- [ ] 运行 `npm test -- src/__tests__/valuationAnalysisState.test.js`，确认通过。

## 4. 集成、审阅与提交

- [ ] 审阅需求覆盖，再审阅实现质量；修正发现的问题。
- [ ] 运行估值/文章相关后端回归、前端全套测试与构建，`git diff --check`，只读访问真实接口核验。
- [ ] 检查提交内容及敏感信息，保留无关工作区修改；提交本次及必要依赖至 `codex/fix-valuation-analysis`，推送 origin 并核对远程SHA。
