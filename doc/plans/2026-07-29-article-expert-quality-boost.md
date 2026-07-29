# 2026-07-29 文章解读质量提升方案

## 背景

conv_190 / message_id: 561 回复评估发现 3 个问题：
1. **专家协作不足**：文章解读只路由到 1 个 article_expert，未触发多专家协作
2. **工具调用有遗漏**：回答出现红利低波/创业板指估值数据，但工具调用记录里没有查询这两个标的，存在数据来源不明/幻觉风险
3. **交叉审阅缺失**：涉及具体操作建议（加仓白酒5000-10000元），但 cross_review_results 为空，无风险评估师制衡

## 目标

- 文章解读涉及行为金融学/风险/操作建议时，自动加入 behavioral_advisor + risk_assessor 协作
- article_expert 强制查询文章提及的所有板块/标的估值，避免数据来源不明
- 涉及操作建议时强制触发交叉审阅，引入魔鬼代言人制衡

## 改动点

### 1. 路由优化：文章解读关键词扩展强制协作

**文件**：`backend/agent/core/router.py`（第 643-662 行纯链接检测分支）

**现状**：纯链接场景直接返回 `["article_expert"]` 单专家，后续无任何补充路由

**方案**：纯链接场景仍以 article_expert 为主，但**在路由结果里标记 needs_cross_review=True**，让编排器有机会根据文章内容动态决定是否补充专家。

具体改动：
- 纯链接路由结果保留 `["article_expert"]`
- 新增字段 `article_context_pending=True`，表示文章内容未注入，需编排器在抓取文章后做二次路由判断
- `needs_arbitration` 保持 False（单专家无需仲裁），但 `needs_cross_review` 改为 True（涉及操作建议时需审阅）

**编排器侧**（`orchestrator.py` `_stream_build_context`）：
- 文章抓取后，若检测到文章内容含行为金融学关键词（追涨杀跌/贪婪恐惧/波动/暴涨暴跌/散户/羊群/认知偏差/情绪/恐慌/焦虑），追加 behavioral_advisor
- 若文章内容含操作建议关键词（加仓/减仓/清仓/买入/卖出/止盈/止损/补仓/抄底），追加 risk_assessor

### 2. article_expert Prompt 强化：强制查询所有标的估值

**文件**：`backend/db/agents.py`（article_expert 的 system_prompt，第 657-712 行）

**现状**：
```
### 量化验证维度（必做，G-1）
- 对涉及标的/板块，必须调用 query_valuation 工具查询当前估值分位
```

**问题**：prompt 说"涉及标的/板块必须查询"，但实际只查了白酒和半导体，红利低波/创业板指的数据来源不明

**方案**：在"量化验证维度"章节增加强制条款：
- 文章提及的**每个具体板块/指数/行业**都必须调用 query_valuation 查询
- 禁止从持仓上下文带入估值数据而不标注来源（如"来自持仓上下文"）
- 若某标的查询失败，必须在「数据缺口」中明确标注，禁止凭记忆推测
- 输出的所有估值数据必须标注 `[来源: query_valuation工具 / 持仓上下文 / 文章引用]`

### 3. 交叉审阅触发优化：操作建议强制审阅

**文件**：`backend/agent/core/orchestrator.py`（`should_run_cross_review` 第 1249 行 + 调用处）

**现状**：`should_run_cross_review` 要求 `spec_count >= min_specialists(2)`，单专家场景直接跳过

**问题**：article_expert 单专家给出操作建议时无制衡

**方案**：在 `should_run_cross_review` 增加一条规则：
- 若 `specialist_results` 中任一专家的分析内容包含操作建议关键词（加仓/减仓/清仓/买入/卖出/止盈/止损/补仓/抄底/建议买/建议卖），且未达到 min_specialists 门槛，则**强制触发交叉审阅**
- 交叉审阅时自动注入 risk_assessor 作为质疑角色（若未在专家列表中）
- 新增开关 `cross_review_force_on_action_advice`（默认 true）控制此行为

## 验证

- conv_190 场景重放：文章解读应触发 article_expert + behavioral_advisor + risk_assessor 三专家协作
- 工具调用记录应包含 query_valuation("白酒"/"半导体"/"红利低波"/"创业板指") 至少 4 次
- cross_review_results 非空，含 risk_assessor 对白酒加仓建议的质疑

## 开关

- `routing.article_secondary_routing_enabled`（默认 true）：文章内容二次路由开关
- `cross_review_force_on_action_advice`（默认 true）：操作建议强制交叉审阅开关

## 不改动

- article_expert 的 10 维度输出框架（已符合要求）
- 路由器其他关键词匹配规则
- 仲裁系统
