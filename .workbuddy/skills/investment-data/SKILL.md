---
name: investment-data
description: >-
  投资分析助手（investment-analyzer）本地数据互通层。查询我的持仓/组合、指数估值分位、
  告警雷达、关注列表、市场温度、KYC 画像、健康分、每日建议、决策记录、机会扫描、
  交易计划、家庭财务现金流、理财目标等本地系统数据，串联成理财决策支持分析。
  触发词：持仓、组合、我的基金、估值、分位、百分位、高估、低估、告警、预警、雷达、
  回撤、加仓、减仓、上车、关注列表、自选、市场温度、账户、盈亏、持仓体检、画像、
  风险承受、健康分、每日建议、决策、机会、交易计划、现金流、支出、目标、应急金。
  数据源为本地 FastAPI（127.0.0.1:8000）或 SQLite（data/valuations.db，只读兜底）。
version: 2.0.0
display_name: "投资数据桥"
---

# investment-data — 本地投资数据互通层

把 WorkBuddy 接到投资分析助手的本地数据上：**先取真实数据，再做分析，用全量拼图支撑决策**。
分析方法论（合规红线、免责声明、资产配置逻辑）另行加载 `workbuddy-finance-skill`；
本 skill 只负责"怎么查数据、查什么、口径是什么、怎么把多域数据串成决策依据"。

**核心原则：理财建议 = 画像（能承受什么）× 现金流（能投多少）× 估值（贵不贵）× 风险（有什么雷）× 历史（别重复犯错）。任何一域缺失，结论都要明说"未纳入 XX 考量"。**

## 0. 前置检查（每次会话首次查询前做一次）

```bash
curl -s -m 3 http://127.0.0.1:8000/api/health
# 期望: {"code": 0, "message": "ok", "data": {"status": "ok"}}
```

- **后端在跑** → 走 HTTP 接口（首选，带缓存/口径归一）。
- **后端没跑** → 告知用户后端未启动（`cd backend && python app.py`），或改用第 6 节 SQLite 只读兜底。
- 后端工作目录：`/Users/xiaoyuer/projects/investment-analyzer/backend`，端口 8000。

## 1. 查询配方层（场景 → 端点）

所有接口返回统一包装 `{"code": 0, "message": "ok", "data": {...}}`，下文"返回要点"只描述 `data` 内层。

### 1.1 持仓 / 组合

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 组合总览（最常用） | `GET /api/portfolio/summary` | `total_value` 市值、`total_cost` 成本、`total_profit`/`profit_rate` 盈亏、`total_assets` 含现金总资产、`cash_balance` 现金、`fund_type_breakdown` 基金类型分布、`cash_accounts` 分账户（小鱼儿 / 花无缺） |
| 逐笔持仓明细 | `GET /api/portfolio` | `holdings[]`：`fund_code/fund_name/account/index_code/index_name`、`shares/cost_price/current_price`、`total_cost/current_value/profit_loss/profit_rate` |
| 指定账户 | 上两接口加 `?account=花无缺` | 双账户隔离 |
| 业绩曲线 | `GET /api/portfolio/performance` | 收益走势 |
| 交易流水 | `GET /api/portfolio/transactions` | 申赎记录 |
| 待确认交易 | `GET /api/portfolio/pending-transactions` | 尚未成交确认的单 |
| 现金账户 | `GET /api/portfolio/cash` | 分账户余额/冻结/在途 |

> 持仓里的 `index_code` 是连接估值数据的关键外键（如白酒 → `399997.SZ`），做"持仓估值体检"时用它反查 1.2。

### 1.2 指数估值

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 全部指数估值总览（首选） | `GET /api/valuation/indexes` | `indexes[]`：`index_code/index_name`、`metric_type`（市盈率等）、`current_value`、**`percentile`（百分位，单位 percent，0-100）**、`latest_date` |
| 某指数历史估值 | `GET /api/valuation/history/{index_code}?days=30&metric_type=市盈率` | 时序，算趋势/变化 |
| 市场温度 | `GET /api/valuation/market-temperature` | `market_temperature`（低温 <5 布局机会 / 中温 / 高温）、`status`、`description` |
| 螺丝钉估值表 | `GET /api/valuation/dd/list`、`/api/valuation/dd/indexes?dd_id=` | 多来源交叉验证 |
| 六亿估值表 | `GET /api/valuation/liuyi/list`、`/api/valuation/liuyi/indexes?liuyi_id=` | 同上 |
| 超性价比筛选 | `GET /api/valuation/super-value` | 全市场低估候选 |
| 指数别名/代码映射 | `GET /api/valuation/code-mappings` | 名称→代码归一（"白酒"→399997.SZ） |

**分位口径**：`percentile` 为 0–100 的百分数（`percentile_unit: percent`）。经验阈值：
- `< 20` 低估区（告警雷达的默认低估阈值）
- `20–80` 正常
- `> 80` 高估区（默认高估阈值）

### 1.3 告警雷达

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 告警列表（最常用） | `GET /api/portfolio/alerts?limit=20&active_only=true` | `alerts[]`：`alert_type`、`severity`（danger/warning/info）、`title/content`、`related_fund_code/related_fund_name`、`risk_status`（active/resolved）、`created_at`、`acknowledged_status` |
| 只看未读 | 加 `&unread_only=true` | |
| 只看严重 | 加 `&severity=danger` | |
| 告警回溯 | `GET /api/portfolio/alerts/{alert_id}/history` | 生命周期事件链 |
| 立即巡检 | `POST /api/portfolio/alerts/scan` | 估值阈值+持仓风险+关注信号+健康分 |
| 事件雷达（市场事件） | `GET /api/alerts/event-radar/events?status=&relevance=` | 市场级事件（非持仓级） |
| 事件雷达准确率 | `GET /api/alerts/event-radar/accuracy` | 预警可信度参考 |

**告警类型速查**：`drawdown_alert` 近期回撤超阈值、`daily_drop` 单日大跌、`valuation_low/high` 估值进极端区、`concentration_*` 单标的占比超阈值、`loss_*` 单标的亏损超阈值、`health_score` 健康分低、`valuation_failure` 估值数据源挂了。

### 1.4 关注列表 / 自选

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 关注列表 | `GET /api/watchlist` | 自选基金及其信号灯 |
| 巡检 | `POST /api/watchlist/patrol`（异步） | 刷新净值/估值、上车价位 |
| 买入时机评分 | `GET /api/watchlist/{item_id}/buy-score` | 纯规则综合分（无 LLM） |
| 信号历史 | `GET /api/watchlist/{fund_code}/signal-history` | 历史触发记录 |
| 共振分析 | `GET /api/watchlist/resonance` | 关注+持仓交叉信号 |

### 1.5 KYC 画像 / 目标（决策的"承受力"边界）

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 增强画像（首选） | `GET /api/kyc/enhanced-profile` | `questionnaire_profile`（`risk_tolerance` 风险承受、`loss_tolerance` 亏损承受、`investment_horizon` 期限、`focus_assets` 关注品类、`kyc_completed`）+ 行为推断画像 |
| 行为画像 | `GET /api/kyc/behavior-profile` | 从交易行为推断的风格（与问卷可能不一致，不一致时点出来） |
| 画像问卷原文 | `GET /api/profile/kyc` | 问卷题目+已填答案 |
| 用户基本盘 | `GET /api/profile` | 收入/支出/家庭状况 |
| 理财目标 | `GET /api/profile/goals` | 目标金额/期限/进度 |
| 目标桶（四桶/五桶） | `GET /api/profile/buckets`、`GET /api/buckets` | 分桶资产、`/api/buckets/allocation-suggestion` 配置建议 |

> **画像约束先于一切**：建议加仓/提高权益占比前必须先查画像。问卷显示 `risk_tolerance: balanced / investment_horizon: long`，但组合权益占比已 40%+ 时，加仓建议要与目标配置对照后再下结论。

### 1.6 健康分 / 每日建议（系统的"结论层"）

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 健康分 | `GET /api/health-v2/dashboard` | **注意：返回 `{"task_id", "status"}` 是异步任务句柄**，结果需再取（或改用 `/api/health-v2/history` 拿最近一次落库结果） |
| 健康分历史 | `GET /api/health-v2/history` | 趋势 |
| 今日每日建议 | `GET /api/daily-advice/today` | `run`（今日扫描汇总，如"32 条提示，观察 17，风险拦截 8，持有 7"）+ 明细 |
| 建议信号列表 | `GET /api/daily-advice/signals` | 逐条信号（可读/可忽略/可转候选） |
| 周报 | `POST /api/daily-advice/weekly-report` | 一周汇总 |

> 每日建议是系统自己的"预消化结论"，回答"今天有什么要注意的"时**先看 daily-advice，再看 raw 告警**，避免和系统已有结论重复/矛盾。

### 1.7 决策 / 机会 / 交易计划（决策的"历史与在途"）

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 决策列表 | `GET /api/decisions` | `items[]`：`decision_type`（buy/sell/watch…）、`target_name/summary/rationale`、执行状态 |
| 今日决策 | `GET /api/decisions/today` | 今天新落的决策 |
| 决策统计 | `GET /api/decisions/stats` | 各类型决策分布 |
| 待复盘决策 | `GET /api/decisions/reviews/due` | 到期该回看的决策 |
| 决策详情 | `GET /api/decisions/{decision_id}`、`/{id}/timeline` | 完整链路（precheck/peer-review/execution） |
| 今日机会 | `GET /api/opportunities/today` | `items[]`：`theme`、`verdict`（can_buy/watch/blocked…）、`opportunity_score`、`summary` |
| 机会统计 | `GET /api/opportunities/stats` | |
| 在途交易计划 | `GET /api/trade-plans/pending/summary` | 未执行的买入/卖出计划、金额 |

> 回答"要不要买 XX"前**必查**：①是否已有 watch/can_buy 决策或机会（别推翻系统结论而不自知）②是否有在途 trade-plan（避免重复建仓建议）。

### 1.8 家庭财务 / 现金流（决策的"弹药库"）

| 想查什么 | 调用 | 返回要点 |
|---|---|---|
| 家庭财务总览 | `GET /api/finance-dashboard` | `net_worth`（总资产/分账户现金/`cash_scope: household_accounts`）、收入支出、资产负债 |
| 现金流预测 | `GET /api/expense/cashflow` | `monthly_income`、`monthly_expense`、`recorded_expense`、`data_gaps`（数据缺口会明示） |
| 支出汇总 | `GET /api/expense/summary`、`/api/expense/trend` | 支出结构与趋势 |
| 固定账单 | `GET /api/expense/bills` | 车贷/保费等周期性支出 |
| 财务趋势 | `GET /api/finance-dashboard/trend` | 净值变化 |

> **加仓建议的资金上限 = 现金流结余 + 现金账户可动用部分 − 近期固定账单**。用 `expense/cashflow` 的结余说话，不要拍脑袋说"用闲钱"。

## 2. 分析 Cookbook（常见问题 → 取数 → 分析）

### "我的持仓现在什么情况？"
1. `GET /api/portfolio/summary` —— 总资产/市值/盈亏/现金垫
2. `GET /api/portfolio` —— 找出亏损最大 / 占比最高的前几个标的
3. `GET /api/portfolio/alerts?active_only=true` —— 活动风险告警
4. `GET /api/daily-advice/today` —— 系统今日结论
5. 回答结构：总资产概况 → 盈亏归因（哪只拖累）→ 活动告警 → 系统建议摘要 → 结合 finance-skill 给观察

### "XX 指数贵不贵？现在能买吗？"
1. `GET /api/valuation/code-mappings` 名称归一 → 得 index_code
2. `GET /api/valuation/indexes` 过滤该 index_code —— 当前值 + 分位
3. `GET /api/valuation/history/{index_code}?days=90` —— 分位趋势
4. `GET /api/valuation/market-temperature` —— 大盘水温
5. `GET /api/kyc/enhanced-profile` —— 期限与风险承受是否匹配
6. 回答：分位结论 + 趋势 + 温度 + 期限匹配度。**分位低 ≠ 立刻买**。

### "要不要给 XX 加仓？"（全拼图决策）
1. `GET /api/portfolio` —— 现有仓位占比、浮亏幅度
2. `GET /api/valuation/indexes` —— 对应指数分位
3. `GET /api/portfolio/alerts?active_only=true` —— 是否有回撤/亏损告警（回撤 ≠ 买点）
4. `GET /api/opportunities/today` + `GET /api/decisions` —— 系统是否已有该标的的结论/在途计划
5. `GET /api/expense/cashflow` —— 本月结余能支撑多少弹药
6. `GET /api/kyc/enhanced-profile` —— 亏损承受与期限
7. 回答：估值位置 + 仓位集中度 + 系统已有结论 + 资金可行性 + 承受力边界，给出**观察与权衡点**，不给指令式建议

### "今天有什么要注意的？"
1. `GET /api/daily-advice/today` —— 系统预消化的今日结论（首选）
2. `GET /api/portfolio/alerts?active_only=true&limit=20` —— 活动告警补充
3. `GET /api/alerts/event-radar/events` —— 市场级事件（如涉及）
4. 按 severity 分组播报：danger 在前，warning 次之，info 一句话带过

### "组合估值体检"（持仓有没有高估风险）
1. `GET /api/portfolio` 取全部 `index_code`
2. `GET /api/valuation/indexes` 逐个对分位
3. 输出表：标的 | 占比 | 浮盈亏 | 估值分位 | 提示（分位>80 且占比高 → 高估集中风险；分位<20 且有现金 → 有加仓观察空间）

### "我的投资做得怎么样？/ 该复盘了"
1. `GET /api/decisions/stats` + `GET /api/decisions?limit=20` —— 决策分布与近期决策
2. `GET /api/decisions/reviews/due` —— 到期待复盘项
3. `GET /api/portfolio/performance` —— 业绩对照
4. 回答：决策执行率 → 待复盘项 → 与业绩的对照反思

## 3. 决策支持流程（多域数据怎么串）

给任何"能不能买/要不要卖/怎么调整"类问题，按此顺序组装事实，**缺哪域就在结论里声明未纳入**：

```
① 承受力边界  ← kyc/enhanced-profile（风险/亏损/期限）
② 资金弹药    ← expense/cashflow（月结余）+ portfolio/cash（可动用现金）
③ 当前持仓    ← portfolio/summary + portfolio（仓位结构、集中度、浮盈亏）
④ 估值位置    ← valuation/indexes（分位）+ market-temperature（水温）
⑤ 风险在途    ← portfolio/alerts?active_only（回撤/亏损/集中度告警）
⑥ 系统已有结论 ← opportunities/today + decisions + trade-plans/pending（避免与在途计划打架）
⑦ 结论呈现    ← 以上 6 项的交集与冲突点，配合 finance-skill 合规表述
```

**冲突要明说**：比如估值分位很低（想加）但权益占比已超目标配置（该控），这时结论应是"低估与配置超限冲突，观察或定投摊薄比一次性加仓更符合你的画像"，而不是只报一个维度。

## 4. 输出规范

- **口径**：涨红跌绿（中国惯例）；金额用 ¥；分位用"百分位 XX%"表述并注明数据日期
- **免责声明**：凡涉及投资判断的内容，先加载 `workbuddy-finance-skill` 获取合规红线（不承诺收益、不给指令式买卖建议、提示风险自担）
- **数据时效**：估值/净值有日期戳，回答中必须带 `latest_date`；超过 3 个交易日未更新要明说
- **数字诚实**：直接引用接口返回值，不估算、不脑补缺失数据；查不到就说查不到
- **缺口声明**：某域没查到/字段为空（如 `capital_scale` 空、`data_gaps` 非空），结论中注明"基于现有数据，未纳入 XX"

## 5. 坑位提示

- ❌ **`GET /api/valuation/unified` 会挂起超时，不要用**，改用 `/api/valuation/indexes` + `/dd/list` + `/liuyi/list` 组合
- 分位单位是 percent（0-100），不是 0-1 小数——写分析时别再乘 100
- 持仓是双账户（小鱼儿 / 花无缺），默认返回全家合并；按人分析时加 `?account=`
- `alerts` 的"已读"与"风险恢复"是两回事：`is_read` 只管通知红点，`risk_status` 才是风险是否仍在
- 手动巡检 `POST .../scan` 是触发扫描，结果要再 GET 列表才能看到
- `health-v2/dashboard` 返回的是**异步任务句柄** `{"task_id", "status": "running"}`，不是健康分本身；要结果用 `/api/health-v2/history`
- KYC 数据已知缺口：`capital_scale` 可能为空、`monthly_surplus` 历史值有误写记录（应以 `expense/cashflow` 的实时结余为准）；现金分布跨多个账户，家庭口径以 `finance-dashboard` 的 `cash_scope: household_accounts` 为准
- 分析中引用系统结论（daily-advice / opportunities / decisions）时注明来源是"系统已有结论"，与自己的分析区分开

## 6. SQLite 只读兜底（后端未启动时）

数据库：`/Users/xiaoyuer/projects/investment-analyzer/data/valuations.db`（WAL 模式）。
**只查询 SELECT，禁止任何写入**。打开方式二选一：

```bash
# 方式 A（推荐，普通打开；WAL 库 mode=ro 会报错 14，勿用 file:?mode=ro）
sqlite3 /Users/xiaoyuer/projects/investment-analyzer/data/valuations.db "SELECT ..."

# 方式 B（严格只读快照，但看不到最新未 checkpoint 的 WAL 数据）
sqlite3 "file:/Users/xiaoyuer/projects/investment-analyzer/data/valuations.db?immutable=1" "SELECT ..."
```

核心表：
| 表 | 内容 | 对应接口 |
|---|---|---|
| `portfolio_holdings` | 持仓明细 | /api/portfolio |
| `portfolio_cash` | 现金账户 | /api/portfolio/cash |
| `portfolio_alerts` | 告警 | /api/portfolio/alerts |
| `alert_lifecycle_events` | 告警生命周期 | alerts/{id}/history |
| `index_valuations` | 指数估值时序 | /api/valuation/indexes |
| `index_code_mapping` | 代码/别名映射 | /api/valuation/code-mappings |
| `dd_valuations` / `liuyi_valuations` | 多源估值表 | dd/liuyi 系列 |
| `watchlist` | 关注列表 | /api/watchlist |
| `market_events` | 市场事件 | event-radar/events |
| `daily_advice_runs` / `daily_advice_signals` | 每日建议 | /api/daily-advice/* |
| `investment_decisions` / `master_decision_history` | 决策记录 | /api/decisions |
| `opportunities` | 机会扫描 | /api/opportunities |
| `trade_plans` | 交易计划 | /api/trade-plans |
| `expense_records` / `recurring_expenses` | 支出 | /api/expense |
| `goal_buckets` | 目标桶 | /api/buckets |
| `valuations_online` | 在线估值缓存 | — |

兜底示例——组合概览：
```sql
SELECT fund_name, current_value, profit_loss, profit_rate
FROM portfolio_holdings ORDER BY current_value DESC LIMIT 10;
```

兜底示例——活动告警：
```sql
SELECT severity, alert_type, title, created_at FROM portfolio_alerts
WHERE risk_status='active' ORDER BY created_at DESC LIMIT 10;
```

兜底示例——指数估值分位：
```sql
SELECT index_code, index_name, metric_type, percentile, snapshot_date
FROM index_valuations
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM index_valuations)
ORDER BY percentile ASC;
```

> **口径差异警告**：`index_valuations.percentile` 原始表存的是 **0–1 小数**（如 0.6198），
> 而 API `/api/valuation/indexes` 已归一为 **0–100 百分数**。走兜底 SQL 时必须自行 ×100，
> 并在回答中换算成"百分位 XX%"表述。其余字段量纲以实际查出值为准，勿脑补。
