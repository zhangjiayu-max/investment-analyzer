# 多 Agent 对话分析整体增强设计稿

> 日期：2026-07-16
> 触发案例：对话 118（恒生科技"为什么涨/利好政策/外部资金"）
> 目标：彻底解决"5 个专家分析却不透彻"的问题，从路由/专家/审阅/工具/综合/RAG/反思 7 个维度系统增强

---

## 一、问题诊断（基于对话 118 实测）

### 1.1 对话 118 实际情况

| 维度 | 实际 | 问题 |
|---|---|---|
| 用户问题 | "恒生科技最近涨的可以，是有什么利好政策，或者有外部资金注入了吗" | 归因类问题（为什么涨） |
| 路由专家 | 3 个：风险/市场/估值 | 全是估值派，查同一批数据 |
| 交叉审阅 | **0 条** | `should_skip_cross_review` 因"方向一致"跳过 |
| 工具调用 | 全是 `query_valuation` | 没查政策新闻/资金流向 |
| 最终报告 | "未能提供具体政策文件或外部资金流入的定量数据" | 等于没回答 |
| 响应时间 | 98 秒 | 用户等 2 分钟得到"数据缺口" |

### 1.2 七大根因

| # | 根因 | 表现 | 影响 |
|---|---|---|---|
| R1 | **路由盲区** | 恒生科技/港股/归因类问题无显式路由，靠 LLM 兜底 | 专家选错 |
| R2 | **专家同质化** | `market_analyst` 与 `macro_strategist` 政策/资金面 prompt 重叠 30%+；缺行业基本面/行为金融维度 | 视角单一 |
| R3 | **交叉审阅形同虚设** | 专家方向一致时直接跳过；无强制反驳机制 | 无对抗性 |
| R4 | **工具能力缺口** | 无南向资金工具、无政策新闻聚合、`web_search` 不在任何专家 tools 中且数据源不按 query 过滤 | 数据源窄 |
| R5 | **综合报告压缩过度** | 专家各 800-1500 字 → 综合压成一张表格，推理链丢失 | 不透彻 |
| R6 | **Agentic RAG 仅 prompt 指令** | `max_rounds=2` 只是文字提示，无硬性计数器 | 不可靠 |
| R7 | **自我反思只自检** | 不检查"别人对不对"，无跨专家反驳 | 无对抗 |

---

## 二、整体增强方案（7 模块）

### 模块 1：问题类型感知路由（解决 R1）

#### 1.1 新增问题类型分类器

**位置**：`backend/agent/core/router.py` 新增 `_classify_question_type()`

**类型定义**（5 类）：

| 类型 | 关键词特征 | 路由倾向 |
|---|---|---|
| `attribution` 归因 | 为什么涨/跌、原因、驱动、利好、利空、资金注入 | macro_strategist + market_analyst（强制） |
| `prediction` 预测 | 会涨吗、还能涨、见底吗、到顶吗 | valuation_expert + market_analyst |
| `action` 操作 | 买/卖/加仓/减仓/止盈/止损 | allocation_advisor + risk_assessor（强制） |
| `comparison` 对比 | VS、对比、哪个好、A 和 B | fund_analyst + valuation_expert |
| `generic` 通用 | 其他 | 走原关键词路由 |

**实现**：纯规则（关键词匹配），不调用 LLM（零成本）。

#### 1.2 补全路由关键词盲区

**位置**：`backend/agent/core/router.py` `_KEYWORD_ROUTES` + `backend/agent/router_config.yaml`

**新增规则**：

```python
# 港股/恒生（当前完全无路由）
(["恒生", "港股", "恒生科技", "恒生指数", "港股通"], 
 ["macro_strategist", "market_analyst", "valuation_expert"]),

# 归因类（当前完全无路由）
(["为什么涨", "为什么跌", "原因", "驱动", "归因", "怎么回事"],
 ["macro_strategist", "market_analyst"]),

# 资金类（当前完全无路由）
(["资金", "流入", "流出", "外资", "南向", "北向", "主力资金"],
 ["macro_strategist", "market_analyst"]),
```

#### 1.3 问题类型影响专家选择

**位置**：`backend/agent/core/router.py` `SmartRouter.route()`

在关键词路由后、专家数截断前，插入问题类型修正：

```python
qtype = _classify_question_type(query)
if qtype == "attribution":
    # 归因类强制 macro + market，移除纯估值派
    forced = {"macro_strategist", "market_analyst"}
    specialists = list(set(specialists) | forced)
elif qtype == "action":
    # 操作类强制 allocation + risk
    forced = {"allocation_advisor", "risk_assessor"}
    specialists = list(set(specialists) | forced)
```

#### 1.4 默认开关

```
agent.question_type_routing_enabled = true  （纯规则，无 LLM 成本，默认开）
```

---

### 模块 2：专家体系补全与职责重划（解决 R2）

#### 2.1 新增"行业基本面分析师"

**位置**：`backend/db/agents.py` `_init_wealth_specialists()` specialists 列表新增第 8 个

**定位**：补全估值/风险/配置/择时/基金之外的"行业基本面"维度（白酒批价、渠道库存、消费场景、产业链景气度）。

**tools**：`search_knowledge, yingmi_search_news, eastmoney_search, eastmoney_finance_data, yingmi_latest_quotations, query_earnings_reports, ttfund_stock_price`

**agent_key**：`industry_fundamentalist`

**system_prompt 核心**：
- 自下而上行业基本面分析（区别于宏观策略师的自上而下）
- 消费品：批价/动销/库存周期/渠道反馈
- 周期品：产能利用率/库存周期/价格曲线
- 科技品：订单/产能/技术迭代/产业链景气度
- 明确不与估值分析师重叠（不报估值分位）

#### 2.2 新增"行为金融学专家"

**位置**：`backend/db/agents.py` specialists 列表新增第 9 个

**定位**：识别用户情绪偏差（追涨杀跌/损失厌恶/处置效应/锚定效应），给出行为纠偏建议。

**tools**：`search_knowledge, query_portfolio, query_transaction_history, analyze_holding_performance, diagnose_behavior`

**agent_key**：`behavioral_advisor`

**system_prompt 核心**：
- 6 大偏差识别（追涨/杀跌/损失厌恶/处置效应/锚定/过度自信）
- 基于用户持仓与交易记录判断当前情绪状态
- 给出行为纠偏建议（不重复资产配置师的仓位建议）

#### 2.3 拆分 market_analyst 与 macro_strategist 职责边界

**问题**：两者在政策/资金面重叠 30%+，"政策"关键词会同时路由到两者。

**改动**：

| 专家 | 改前 | 改后 |
|---|---|---|
| `market_analyst` | 短期+结构性都查 | 聚焦**短期**：市场情绪/热点/资金短期动向/技术面/事件冲击 |
| `macro_strategist` | 短期+结构性都查 | 聚焦**结构性**：美林时钟/库存周期/货币财政政策/利率环境 |

**路由调整**：
- `["政策", "利好", "利空"]` 改为只路由 `[macro_strategist]`（不再重复调 market_analyst）
- 短期市场情绪关键词（大盘/行情/走势/热点）才路由 `market_analyst`

#### 2.4 默认开关

```
agent.industry_fundamentalist_enabled = true   （纯 prompt 调整，无额外 LLM 成本，默认开）
agent.behavioral_advisor_enabled = true        （同上，默认开）
```

**风险与反馈点 1**：新增 2 个专家会推高单次对话 LLM 调用次数（从 3-5 次到 5-7 次）。是否接受？如不接受，可只启用 `industry_fundamentalist`（更高优先级），暂缓 `behavioral_advisor`。

---

### 模块 3：强制交叉审阅 + 魔鬼代言人（解决 R3 + R7）

#### 3.1 强制至少 1 条反驳

**位置**：`backend/agent/core/multi_agent.py` `run_cross_review_opinion()` L1280-1285

**改动**：

```python
# 原逻辑：解析 opinion，disagreements 可能为空
# 新逻辑：若 disagreements 为空且专家数 >=2，二次提示强制反驳
if len(disagreements) == 0 and len(specialist_results) >= 2:
    retry_prompt = (
        "请作为魔鬼代言人，对其他专家的结论提出至少 1 条质疑或反驳。"
        "即使方向一致，也要质疑：\n"
        "- 是否存在价值陷阱？\n"
        "- 是否低估了下行风险？\n"
        "- 数据是否有幸存者偏差？\n"
        "- 推理链是否有逻辑跳跃？\n"
        "必须输出至少 1 条 disagreement。"
    )
    # 二次调用 LLM（轻量模型，max_tokens=300）
```

#### 3.2 修改 `should_run_cross_review` 跳过逻辑

**位置**：`backend/agent/core/orchestrator.py` L931-989 + `orchestrator_optimizer.py` L49-70

**改动**：

```python
# 原逻辑：方向一致时 should_skip_cross_review 返回 True
# 新逻辑：仅在专家数 < 2 时跳过；方向一致时仍执行（强制找盲点）
def should_skip_cross_review(specialist_results, ...):
    if len(specialist_results) < 2:
        return True
    # 移除"方向一致跳过"逻辑
    return False
```

#### 3.3 串行黑板最后位设为魔鬼代言人

**位置**：`backend/agent/core/orchestrator.py` L5108-5135 串行执行循环

**改动**：

```python
# 串行模式下，最后执行的专家注入"魔鬼代言人"角色
if idx == len(tool_tasks) - 1 and len(tool_tasks) >= 2:
    ctx += "\n\n【魔鬼代言人角色】你是最后发言的专家，"
    "请对前面专家的结论提出质疑，至少指出 1 个潜在盲点或风险。"
```

#### 3.4 默认开关

```
agent.force_devil_advocate_enabled = true   （强制反驳，每对话+1 次 LLM 调用，默认开）
agent.devil_advocate_model = "deepseek-v4-flash"  （轻量模型，控制成本）
```

**风险与反馈点 2**：强制反驳会增加 1 次 LLM 调用（轻量模型，约 0.01 元/次）。是否接受？若 LLM 输出"我认同"敷衍，需要加质量校验。

---

### 模块 4：工具能力补全（解决 R4）

#### 4.1 新增 `query_southbound_capital` 工具

**位置**：`backend/tools/__init__.py` TOOLS 数组 + `backend/services/market/institutional_flow.py`

**功能**：查询港股通南向资金流向（akshare `stock_hsgt_south_net_flow_in_em` 仍可用）。

**返回**：近 5/20/60 日累计净流入、行业偏好、活跃个股。

**加入专家 tools**：`macro_strategist, market_analyst, industry_fundamentalist`

#### 4.2 新增 `query_policy_news` 工具

**位置**：`backend/tools/__init__.py` TOOLS 数组 + `backend/services/market/policy_news.py`（新文件）

**功能**：聚合政策新闻（央行/国务院/证监会/财政部），按重要性分级。

**数据源**：
- 盈米新闻 API（已有 `yingmi_search_news`，加政策关键词过滤）
- 东方财富政策面分类
- akshare `news_cctv` 央视新闻（已有，加政策标签）

**加入专家 tools**：`macro_strategist, industry_fundamentalist`

#### 4.3 修复 `web_search` 数据源

**位置**：`backend/tools/__init__.py` `_web_search()` L1876-1984

**问题**：东方财富源 `ak.stock_news_em(symbol="A股")` 完全忽略 query 参数，固定拉 A 股新闻流。

**改动**：

```python
# 原逻辑：固定拉 A 股新闻，不按 query 过滤
# 新逻辑：拉取后按 query 关键词过滤标题
news = ak.stock_news_em(symbol="A股")
if query:
    keywords = extract_keywords(query)  # 提取核心词
    news = [n for n in news if any(k in n["title"] for k in keywords)]
```

#### 4.4 将 `web_search` 加入专家 tools

**位置**：`backend/db/agents.py`

**改动**：`macro_strategist, market_analyst, industry_fundamentalist` 的 tools 列表追加 `web_search`。

#### 4.5 默认开关

```
tool.southbound_capital_enabled = true   （纯数据查询，无 LLM 成本，默认开）
tool.policy_news_enabled = true          （同上，默认开）
```

**风险与反馈点 3**：新工具依赖 akshare/东方财富 API 稳定性。若 API 失败，需有降级策略（已有 `data_missing` 错误分类）。

---

### 模块 5：综合报告深度保留（解决 R5）

#### 5.1 修改综合 prompt 压缩策略

**位置**：`backend/agent/core/orchestrator.py` `_build_final_synthesis_prompt()` L553-682

**改动**：

```python
# 原逻辑：综合 prompt 强调"结论先行/简洁"
# 新逻辑：保留推理链条，分维度呈现

synthesis_instruction = """
请按以下结构综合专家分析：

## 1. 核心结论（1-2 句）
直接回答用户问题，不要绕弯。

## 2. 推理链条（必须保留）
列出支撑结论的关键推理步骤，每步标注数据来源：
- 步骤 A：[数据] → [推理] → [子结论]
- 步骤 B：[数据] → [推理] → [子结论]

## 3. 分歧与反驳（如有）
列出专家间的分歧点，以及魔鬼代言人的质疑。

## 4. 操作建议（如适用）
具体到金额/比例/触发条件。

## 5. 风险提示
标注置信度（高/中/低），列出未覆盖的盲点。

禁止：把多个专家结论压缩成一句话。
禁止：省略推理过程只给结论。
"""
```

#### 5.2 移除专家结论过度压缩

**位置**：`backend/agent/infra/blackboard.py` `_extract_conclusion()` L403-442

**改动**：

```python
# 原逻辑：结论摘要限制 100 字
# 新逻辑：限制 300 字，保留关键推理
MAX_CONCLUSION_LEN = 300  # 原 100
```

#### 5.3 综合报告 token 预算提升

**位置**：`backend/agent/core/orchestrator.py` 综合 LLM 调用参数

**改动**：`max_tokens` 从默认 2000 提升到 3000。

#### 5.4 默认开关

```
agent.deep_synthesis_enabled = true   （增加输出 token，约 +0.02 元/次，默认开）
```

**风险与反馈点 4**：综合报告变长会增加输出 token 成本。是否接受？

---

### 模块 6：Agentic RAG 硬性限制（解决 R6）

#### 6.1 加检索轮次计数器

**位置**：`backend/agent/core/multi_agent.py` `run_specialist()` ReAct 循环内

**改动**：

```python
# 原逻辑：MAX_TURNS=3 限制总工具调用，不区分检索
# 新逻辑：检索类工具单独计数，硬性截断

search_rounds = 0
MAX_SEARCH_ROUNDS = get_config_int("agent.agentic_rag_max_rounds", 2)

for turn in range(MAX_TURNS):
    # ... LLM 决策工具调用 ...
    if tool_name in SEARCH_TOOLS:  # search_knowledge, web_search, yingmi_search_news
        search_rounds += 1
        if search_rounds > MAX_SEARCH_ROUNDS:
            # 注入提示，强制进入分析阶段
            llm_messages.append({
                "role": "user",
                "content": "检索轮次已达上限，请基于已有信息给出分析，标注数据缺口。"
            })
            break
```

#### 6.2 默认开关

```
agent.agentic_rag_hard_limit_enabled = true  （纯规则，无 LLM 成本，默认开）
```

---

### 模块 7：自我反思跨专家增强（解决 R7）

#### 7.1 自我反思增加"跨专家盲点检查"

**位置**：`backend/agent/infra/self_reflection.py` `evaluate_analysis()` prompt L24-43

**改动**：4 维度 → 5 维度

```python
# 新增第 5 维度：跨专家盲点
"""
5. 跨专家盲点检查：
   - 其他专家是否已覆盖你的结论？若已覆盖，你的独特贡献是什么？
   - 是否存在其他专家应覆盖但未覆盖的维度？
   - 你的结论是否与其他专家冲突？冲突点是否已说明？
"""
```

#### 7.2 输出结构增加 `cross_blind_spots` 字段

**位置**：`backend/agent/infra/self_reflection.py` 输出 JSON schema L44-60

**改动**：

```python
{
  "sufficient": true/false,
  "confidence": 0.0-1.0,
  "gaps": [...],
  "issues": [...],
  "need_retry": true/false,
  "reflection_score": 0.0-1.0,
  "cross_blind_spots": ["盲点1", "盲点2"]  # 新增
}
```

#### 7.3 跨专家盲点触发二轮检索

**位置**：`backend/agent/core/multi_agent.py` `run_specialist()` L802-829 重试逻辑

**改动**：

```python
if reflection.get("need_retry") and reflection.get("gaps"):
    # 原逻辑：基于 gaps 重试
    # 新逻辑：若 cross_blind_spots 非空，追加检索指令
    retry_prompt = build_retry_prompt(reflection)
    if reflection.get("cross_blind_spots"):
        retry_prompt += "\n\n请针对以下跨专家盲点补充检索：" + 
                        "；".join(reflection["cross_blind_spots"])
```

#### 7.4 默认开关

```
agent.self_reflection_cross_check_enabled = false  （新增 LLM 维度，默认关，需手动开）
```

**风险与反馈点 5**：跨专家盲点检查会略微增加反思 LLM 输入 token。默认关，用户可手动开。

---

## 三、实施清单

### 后端改动

| 文件 | 改动 | 模块 |
|---|---|---|
| `backend/agent/core/router.py` | 新增 `_classify_question_type()`；`_KEYWORD_ROUTES` 加港股/归因/资金规则；`route()` 加问题类型修正 | M1 |
| `backend/agent/router_config.yaml` | 加港股/归因/资金/行为金融/行业基本面路由 | M1, M2 |
| `backend/db/agents.py` | 新增 `industry_fundamentalist`、`behavioral_advisor`；拆分 market/macro 职责；`web_search` 加入相关专家 tools | M2, M4 |
| `backend/agent/core/multi_agent.py` | `run_cross_review_opinion()` 加强制反驳；`run_specialist()` 加检索轮次计数器 | M3, M6 |
| `backend/agent/core/orchestrator.py` | `should_skip_cross_review` 移除方向一致跳过；串行最后位设魔鬼代言人；`_build_final_synthesis_prompt()` 改结构 | M3, M5 |
| `backend/agent/infra/orchestrator_optimizer.py` | `_has_disagreement()` 不再作为跳过依据 | M3 |
| `backend/agent/infra/blackboard.py` | `_extract_conclusion()` 长度 100→300 | M5 |
| `backend/agent/infra/self_reflection.py` | prompt 加第 5 维度；输出加 `cross_blind_spots` | M7 |
| `backend/tools/__init__.py` | 新增 `query_southbound_capital`、`query_policy_news`；修复 `_web_search` 按 query 过滤 | M4 |
| `backend/services/market/institutional_flow.py` | 加南向资金查询函数 | M4 |
| `backend/services/market/policy_news.py` | 新文件：政策新闻聚合 | M4 |
| `backend/db/config.py` | 新增 7 个开关（均默认 true，除 `self_reflection_cross_check_enabled` 默认 false） | 全模块 |

### 数据库改动

- `agents` 表新增 2 条记录（`industry_fundamentalist`, `behavioral_advisor`），由 `_init_wealth_specialists()` 幂等写入
- 无 schema 变更（`route_keywords` 不是 DB 字段，路由在 YAML/Python 配置）

### 前端改动

- 无（本次增强纯后端逻辑）

---

## 四、默认开关汇总

| 开关 | 默认 | 成本影响 | 模块 |
|---|---|---|---|
| `agent.question_type_routing_enabled` | true | 零成本（纯规则） | M1 |
| `agent.industry_fundamentalist_enabled` | true | +1 专家 LLM 调用 | M2 |
| `agent.behavioral_advisor_enabled` | true | +1 专家 LLM 调用 | M2 |
| `agent.force_devil_advocate_enabled` | true | +1 LLM 调用（轻量模型） | M3 |
| `tool.southbound_capital_enabled` | true | 零成本（数据查询） | M4 |
| `tool.policy_news_enabled` | true | 零成本（数据查询） | M4 |
| `agent.deep_synthesis_enabled` | true | +输出 token（约 0.02 元/次） | M5 |
| `agent.agentic_rag_hard_limit_enabled` | true | 零成本（纯规则） | M6 |
| `agent.self_reflection_cross_check_enabled` | **false** | +反思 LLM 输入 token | M7 |

**单次对话成本预估**：
- 改前：3-5 次 LLM 调用
- 改后：5-8 次 LLM 调用（+2-3 次，其中 1 次轻量模型）
- 成本增加：约 0.05-0.08 元/次

---

## 五、验证方案

### 5.1 回归测试用例

| 用例 | 用户问题 | 期望路由 | 期望交叉审阅 | 期望工具 |
|---|---|---|---|---|
| 归因类 | "恒生科技为什么涨" | macro + market + industry | >=1 条反驳 | southbound_capital + policy_news |
| 操作类 | "该加仓吗" | allocation + risk + behavioral | >=1 条反驳 | query_portfolio |
| 对比类 | "A 基金 vs B 基金" | fund + valuation | >=1 条反驳 | query_fund_info |
| 预测类 | "还会涨吗" | valuation + market | >=1 条反驳 | query_valuation |

### 5.2 对话 118 回放验证

用对话 118 的原问题"恒生科技最近涨的可以，是有什么利好政策，或者有外部资金注入了吗"重新发起对话，验证：
1. 路由命中 macro_strategist + market_analyst + industry_fundamentalist（非原 3 估值派）
2. 工具调用包含 query_southbound_capital + query_policy_news
3. cross_review_results >= 1 条
4. 综合报告保留推理链条，不再出现"数据缺口"敷衍

### 5.3 成本监控

通过 `agent_analysis_log` 表对比改前改后单次对话 LLM 调用次数与 token 消耗。

---

## 六、风险与反馈点汇总

| # | 风险 | 需用户确认 |
|---|---|---|
| F1 | 新增 2 专家推高 LLM 调用次数 | 是否两个都启用？或只启用 industry_fundamentalist？ |
| F2 | 强制反驳可能产生"为反驳而反驳"的低质量内容 | 是否接受？需要加质量校验吗？ |
| F3 | 新工具依赖外部 API 稳定性 | 是否接受 API 失败时的降级策略？ |
| F4 | 综合报告变长增加输出 token 成本 | 是否接受？或设上限 2500 token？ |
| F5 | 跨专家盲点检查默认关 | 是否默认开？ |

---

## 七、实施顺序建议

1. **Phase 1（无 LLM 成本）**：M1 路由 + M4 工具 + M6 RAG 硬限制
2. **Phase 2（轻量 LLM 成本）**：M3 强制交叉审阅 + M5 综合报告深度
3. **Phase 3（中等 LLM 成本）**：M2 新增专家 + M7 自我反思跨专家

每个 Phase 完成后回归验证对话 118，确认改善效果再推进下一 Phase。
