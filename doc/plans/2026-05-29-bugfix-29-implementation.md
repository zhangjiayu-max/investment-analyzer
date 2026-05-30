# 2026-05-29 Bugfix #29 实施方案

> 来源：`doc/plans/bugfix_29.md` 用户反馈问题清单

## 总览

| # | 模块 | 问题 | 优先级 | 复杂度 |
|---|------|------|--------|--------|
| 1 | 后端 | 硬编码指标/阈值需改为可配置 | P0 | 高 |
| 2 | 每日看板 | 市场日报 prompt 优化 | P1 | 低 |
| 3 | 每日看板 | 四卡片固定宽高 | P1 | 低 |
| 4 | 每日看板 | 持仓健康度去掉调仓分析，保留全景诊断 | P1 | 中 |
| 5 | 每日看板 | 热门机会改为热门基金/涨幅/讨论度 | P1 | 中 |
| 6 | 每日看板 | 零钱配置充分利用债券温度分析 | P1 | 中 |
| 7 | AI对话 | 切页面执行中断 bug | P0 | 中 |
| 8 | 估值数据 | AI分析按钮缺少 agent hover tooltip | P2 | 低 |
| 9 | 持仓管理 | 交易行为分析 tab 接入复盘 agent | P1 | 低 |
| 10 | Token用量 | 清空数据 + caller 识别 agent | P2 | 中 |
| 11 | 进化系统 | 反馈数据和评测集建设 | P2 | 高 |

---

## 任务 1：硬编码指标/阈值改为可配置（P0）

### 问题
后端代码大量硬编码估值阈值、债券温度、集中度等指标，违反金融严谨性规范。

### 方案
新建 `system_config` 表存储所有业务阈值，提供 API 管理界面。

**数据库层** — `backend/db/core.py`：
```sql
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'general',
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
```

**初始化默认配置** — `backend/db/core.py` `init_db()`：
```python
DEFAULT_CONFIGS = [
    # 估值阈值
    ('valuation.undervalued_percentile', '30', '低估百分位阈值', 'valuation'),
    ('valuation.overvalued_percentile', '70', '高估百分位阈值', 'valuation'),
    ('valuation.extreme_undervalued', '10', '极度低估百分位', 'valuation'),
    # 债券温度阈值
    ('bond.temp_cold', '30', '债券温度-冷', 'bond'),
    ('bond.temp_cool', '50', '债券温度-凉', 'bond'),
    ('bond.temp_warm', '70', '债券温度-温', 'bond'),
    # 集中度阈值
    ('concentration.top3_high', '60', '前3集中度-高', 'portfolio'),
    ('concentration.top3_moderate', '40', '前3集中度-中', 'portfolio'),
    # 现金比例
    ('cash.ratio_warning', '0.20', '现金比例预警', 'portfolio'),
    ('cash.ratio_low', '0.03', '现金比例过低', 'portfolio'),
    # LLM 参数
    ('llm.temperature_default', '0.3', '默认温度', 'llm'),
    ('llm.max_tokens_report', '8192', '报告最大token', 'llm'),
    ('llm.max_tokens_chat', '4000', '对话最大token', 'llm'),
]
```

**API 路由** — `backend/routers/config.py`（新建）：
- `GET /api/system-config` — 获取所有配置（支持 category 过滤）
- `PUT /api/system-config/{key}` — 更新单个配置
- `POST /api/system-config/reset` — 重置为默认值

**后端引用改造** — 用 `get_config(key, default)` 替换硬编码：
- `backend/routers/dashboard.py`：`_assess_valuation()`、`_get_bond_allocation()`、`_get_cash_advice()` 中的阈值
- `backend/llm_service.py`：`temperature`、`max_tokens` 参数
- `backend/db/analysis.py`：默认 prompt 中的阈值用 `{threshold_undervalued}` 占位符，运行时替换

**前端管理页** — 在 AdminAgentsPage 或新页面中添加配置管理 tab。

### 影响文件
- `backend/db/core.py` — 新增表 + 默认数据
- `backend/routers/config.py` — 新建
- `backend/app.py` — 注册路由
- `backend/routers/dashboard.py` — 替换硬编码
- `backend/llm_service.py` — 替换硬编码
- `backend/db/analysis.py` — prompt 占位符

---

## 任务 2：市场日报 Prompt 优化（P1）

### 问题
市场速览看不出近期市场行情。

### 方案
优化 `DEFAULT_MARKET_ANALYST_PROMPT`，增加以下要求：

1. 增加【近期行情趋势】段落要求：必须概括近 1-2 周的市场走势方向（上涨/下跌/震荡）
2. 增加具体指数涨跌幅数据引用要求
3. 增加板块轮动和资金流向描述
4. 保留现有的估值和持仓分析要求

### 影响文件
- `backend/db/analysis.py` — 修改 `DEFAULT_MARKET_ANALYST_PROMPT`

---

## 任务 3：四卡片固定宽高（P1）

### 问题
四个卡片随内容变宽变高，布局不一致。

### 方案
给 `.dash-card` 添加固定高度和内容溢出滚动：

```css
.dash-card {
  /* 现有样式保持 */
  min-height: 420px;
  max-height: 520px;
}

.dash-card-body {
  flex: 1;
  overflow-y: auto;
  min-height: 0; /* flex 子元素滚动关键 */
}
```

同时为每个卡片的主体内容区域统一用 `.dash-card-body` 包裹，确保内容超出时可滚动。

### 影响文件
- `frontend/src/components/Dashboard.vue` — CSS + 模板结构调整

---

## 任务 4：持仓健康度去掉调仓分析（P1）

### 问题
持仓健康度卡片使用全景分析师，但包含「调仓分析」部分感觉没用。

### 方案
1. 在 Dashboard.vue 模板中移除调仓分析相关的 UI 区域（`rebalance-content` 部分）
2. 保留全景诊断按钮和结果展示
3. 移除 `loadRebalanceSuggestion()` 的自动调用（在 onMounted 中）
4. 后端 `/api/portfolio/rebalance-suggest` 端点保留但不再默认调用

### 影响文件
- `frontend/src/components/Dashboard.vue` — 移除调仓分析 UI 和相关逻辑

---

## 任务 5：热门机会改为热门基金（P1）

### 问题
今日热门机会需要的是热门基金、今日涨幅大的、大家讨论多的，适合短期蹭热度的。

### 方案
修改热点分析专家的 prompt（`DEFAULT_HOTSPOTS_PROMPT`）：

1. 将分析目标从「热点新闻」改为「热门基金机会」
2. 增加要求：关注近期涨幅靠前的基金、讨论热度高的基金
3. 增加短期交易机会识别（适合蹭热度的基金）
4. 增加涨幅排名和热度数据的上下文注入

后端 `routers/dashboard.py` 的 `get_hotspots_analysis()` 需要：
1. 获取更多市场数据（涨幅排名、热门基金列表）作为上下文
2. 调用 MCP 或 akshare 获取热门基金数据

### 影响文件
- `backend/db/analysis.py` — 修改 `DEFAULT_HOTSPOTS_PROMPT`
- `backend/routers/dashboard.py` — 增加热门基金数据上下文

---

## 任务 6：零钱配置充分利用债券温度（P1）

### 问题
零钱配置需要充分利用债券温度、近3月温度市场结合持仓来分析零钱如何买。

### 方案
修改 `DEFAULT_BOND_PROMPT`：

1. 增加近 3 个月债券温度趋势分析要求
2. 要求结合当前持仓的债券配比来给出零钱配置建议
3. 区分货币基金、短债、中长期债券的适用场景
4. 增加温度变化趋势（上升/下降/平稳）对配置策略的影响

后端 `routers/dashboard.py` 的债券推荐端点需要：
1. 注入近 3 个月的债券温度历史数据
2. 注入当前持仓中债券类基金的配比

### 影响文件
- `backend/db/analysis.py` — 修改 `DEFAULT_BOND_PROMPT`
- `backend/routers/dashboard.py` — 增加债券温度历史上下文

---

## 任务 7：切页面执行中断 Bug 修复（P0）

### 问题
AI对话中，切换页面会导致执行中断，消息状态卡在 `streaming`。

### 根因
1. ChatView 被排除在 KeepAlive 之外，切页面时组件销毁
2. `cancelStream()` 只中止客户端 SSE 连接，未通知后端更新消息状态
3. 后端消息永久停留在 `execution_status: "streaming"`

### 方案

**前端修复** — `ChatView.vue`：
```javascript
// cancelStream() 中增加后端通知
const cancelStream = () => {
  if (streamAbort.value) {
    streamAbort.value.abort()
  }
  // 通知后端取消执行
  if (currentConversationId.value && sending.value) {
    fetch(`/api/conversations/${currentConversationId.value}/cancel`, {
      method: 'POST'
    }).catch(() => {}) // fire-and-forget
  }
  // ... 其余清理逻辑
}
```

**后端修复** — `backend/routers/conversations.py`：
```python
@app.post("/api/conversations/{conv_id}/cancel")
async def cancel_conversation_execution(conv_id: int):
    """客户端通知取消执行，更新 streaming 消息状态为 cancelled"""
    from db.conversations import get_messages, update_message_metadata
    messages = get_messages(conv_id, limit=5)
    for msg in reversed(messages):
        meta = msg.get('metadata') or {}
        if meta.get('execution_status') == 'streaming':
            meta['execution_status'] = 'cancelled'
            update_message_metadata(msg['id'], meta)
            break
    return {"ok": True}
```

**后端修复** — `routers/conversations.py` SSE 端点中，客户端断开后确保最终状态写入：
```python
# 在队列消费循环结束后（finally 块中），检查并修复 streaming 状态
if client_disconnected:
    # 查找仍为 streaming 的消息，标记为 cancelled
    ...
```

### 影响文件
- `frontend/src/components/ChatView.vue` — cancelStream 增加后端通知
- `backend/routers/conversations.py` — 新增 cancel 端点 + 断连状态修复

---

## 任务 8：估值页 AI 分析按钮增加 Agent Tooltip（P2）

### 问题
估值数据页面的「AI 市场分析」按钮鼠标放上去没有 agent 信息，需要和每日看板一样。

### 方案
在 `ValuationHistory.vue` 的 AI 分析按钮上增加 `ai-agent-tooltip` span，复用 Dashboard 的 tooltip 样式：

```html
<button class="btn btn-primary btn-ai-analysis" @click="showConfirmRun = true" :disabled="analysisLoading">
  <svg>...</svg>
  <span>AI 市场分析</span>
  <span class="ai-agent-tooltip">指数深度分析师</span>
</button>
```

添加对应的 CSS（复用 Dashboard 的 `.ai-agent-tooltip` 样式）。

### 影响文件
- `frontend/src/components/ValuationHistory.vue` — 模板 + CSS

---

## 任务 9：交易行为分析 Tab 接入复盘 Agent（P1）

### 问题
交易行为分析 tab 只展示数据，没有接入交易复盘分析师 agent。

### 方案
在交易行为分析 tab 的统计区域下方增加「AI 交易复盘」按钮：

```html
<!-- 在 tx tab 的统计区域之后，交易明细之前 -->
<div class="tx-ai-section" v-if="txAnalysisData">
  <button class="btn-ai-action" @click="confirmTradeReview()" :disabled="modeLoading">
    <svg>...</svg>
    <span>AI 交易复盘</span>
    <span class="ai-agent-tooltip">交易复盘分析师</span>
  </button>
</div>
```

复用已有的 `confirmTradeReview()` 和 `runTradeReviewMode()` 函数。复盘结果在当前 tab 内展示（不需要切换到 AI 分析 tab）。

### 影响文件
- `frontend/src/components/PortfolioManagement.vue` — tx tab 增加 AI 按钮和结果展示区

---

## 任务 10：Token 用量清空 + Caller 识别 Agent（P2）

### 问题
1. Token 用量数据需要全部清空
2. 调用方需要能识别是哪个 agent

### 方案

**清空数据** — 后端新增 API：
```python
@app.post("/api/token-usage/clear")
async def clear_token_usage():
    """清空所有 token 用量数据"""
    conn = _get_conn()
    conn.execute("DELETE FROM token_usage")
    conn.commit()
    return {"ok": True}
```

前端 TokenUsagePage 增加「清空数据」按钮（需 ConfirmDialog 确认）。

**Caller 识别 Agent** — 当前 `caller` 字段已有 agent 标识（如 `specialist:valuation_expert`），但前端 `LABEL_MAP` 是硬编码的。改进方案：

1. 后端 `/api/token-usage/by-caller` 返回时自动映射 agent 名称
2. 前端 LABEL_MAP 从后端动态获取，或直接使用后端返回的中文名

### 影响文件
- `backend/routers/token_usage.py` — 新增 clear 端点
- `frontend/src/components/TokenUsagePage.vue` — 清空按钮 + 动态 label

---

## 任务 11：进化系统 — 反馈数据和评测集（P2）

### 问题
缺少用户反馈数据和评测集，无法分析和提升。

### 方案（分阶段）

**阶段一：反馈数据收集增强**
1. 确保所有 LLM 输出都有点赞/点踩按钮（检查 Dashboard、ChatView、ValuationHistory）
2. 反馈时可选填「不满意原因」标签（不准确/不相关/过时/其他）
3. 后端 `llm_feedback` 表增加 `reason_tag` 字段

**阶段二：评测集建设**
1. 新建 `eval_cases` 表：
```sql
CREATE TABLE IF NOT EXISTS eval_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT, -- 'valuation' | 'portfolio' | 'market' | 'chat'
    input_text TEXT,
    expected_output TEXT,
    expected_agents TEXT, -- JSON array of expected agent keys
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```
2. 前端评测管理页面（EvalSuitePage 已存在，需扩展）
3. 从历史反馈中提取高质量 case 作为评测集种子

### 影响文件
- `backend/db/core.py` — 新增表
- `backend/routers/eval.py` — 评测集 CRUD
- `frontend/src/components/EvalSuitePage.vue` — 扩展管理界面

---

## 实施顺序建议

```
第一批（P0 核心修复）：
  任务 7（切页面中断） → 任务 1（硬编码改造基础）

第二批（P1 看板优化）：
  任务 4（去掉调仓） → 任务 2（prompt优化） → 任务 3（卡片固定）
  任务 5（热门基金） → 任务 6（零钱配置） → 任务 9（复盘接入）

第三批（P2 体验优化）：
  任务 8（tooltip） → 任务 10（token清空） → 任务 11（进化系统）
```
