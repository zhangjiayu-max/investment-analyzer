# 持仓感知 AI 对话系统 — 设计方案

## Context

当前系统的三大痛点：
1. **AI 对话不感知持仓** — Orchestrator 和专家 Agent 不知道用户持有什么基金、盈亏多少、现金多少，全靠工具调用才能获取，延迟高且决策不准
2. **没有调仓逻辑** — "AI 调仓建议"按钮实际只是跑全景诊断，没有目标配比、偏离检测、具体调仓方案
3. **零钱闲置** — 现金建议只看债市温度推荐债券/货基，不考虑权益低估机会，也不考虑持仓整体配比

核心目标：**所有 AI 分析都结合持仓数据和盈亏来考虑**。

---

## 第一部分：持仓上下文注入（最高优先级）

### 1.1 构建统一的持仓上下文函数

新增 `backend/portfolio_context.py`：

```python
def build_portfolio_context(user_id="default") -> str:
    """构建持仓+现金+估值的紧凑上下文，供所有 AI 流程使用。"""
```

包含：
- **持仓摘要**：基金名称、市值、占比、盈亏率、持仓天数
- **资产分布**：股票型/债券型/货币型/其他 各占比
- **现金余额** + 年化收益
- **总市值** = 持仓市值 + 现金
- **集中度**：前3/前5占比

### 1.2 注入 Orchestrator System Prompt

**文件**: `backend/agent/orchestrator.py` 的 `orchestrate_stream()` (line 788)

在 RAG 上下文注入后、用户偏好注入前，插入：
```python
from portfolio_context import build_portfolio_context
portfolio_ctx = build_portfolio_context()
if portfolio_ctx:
    system_content += f"\n\n## 用户当前持仓\n{portfolio_ctx}"
```

### 1.3 注入 clarify_requirement

**文件**: `backend/agent/orchestrator.py` 的 `clarify_requirement()` (line 43)

在 prompt 中加入持仓摘要，让澄清 Agent 知道用户持有什么：
```
用户当前持仓：沪深300ETF(30%), 中证500ETF(20%), 恒乐债券A(40%), 现金(10%)
```

### 1.4 注入所有专家 Agent

**文件**: `backend/agent/multi_agent.py` 的 `run_specialist()` (line 189)

在构建 specialist 的 system message 时，追加持仓上下文：
```python
system_content = agent["prompt"]
if portfolio_context:
    system_content += f"\n\n## 用户当前持仓（分析时务必结合）\n{portfolio_context}"
```

### 1.5 注入估值上下文

**文件**: `backend/agent/orchestrator.py`

复用 `app.py` 中已有的 `_build_valuation_context()` (line 332)，提取为独立函数 `build_valuation_summary()`，注入 Orchestrator system prompt：
```
## 当前市场估值
- 沪深300: PE 11.5, 百分位 25% (低估)
- 中证500: PE 22.3, 百分位 45% (合理)
- 创业板: PE 35.2, 百分位 68% (偏高)
- 中证全债: 百分位 82% (高估)
```

### 1.6 `get_portfolio_summary` 加入现金

**文件**: `backend/db.py` 的 `get_portfolio_summary()` (line 1958)

当前只汇总持仓，不含现金。改为：
```python
def get_portfolio_summary(user_id="default"):
    # ... 现有持仓汇总 ...
    cash = get_cash_balance(user_id)
    cash_balance = cash.get("balance", 0) if cash else 0
    return {
        ...existing fields...,
        "cash_balance": cash_balance,
        "total_assets": total_value + cash_balance,  # 总资产
    }
```

---

## 第二部分：智能调仓系统

### 2.1 新增调仓分析函数

**文件**: `backend/rebalancer.py`（新建）

```python
def analyze_rebalancing_need(user_id="default") -> dict:
    """分析是否需要调仓，返回偏离度和建议。"""
```

核心逻辑：
1. 获取当前持仓分布（含现金）
2. 根据估值数据计算目标配比（低估多配、高估少配）
3. 计算偏离度 = |当前占比 - 目标占比|
4. 偏离度 > 阈值（如 5%）时生成调仓建议

目标配比规则：
- 估值百分位 < 30%（低估）→ 目标占比 = 基础配比 × 1.3
- 估值百分位 30-70%（合理）→ 目标占比 = 基础配比
- 估值百分位 > 70%（高估）→ 目标占比 = 基础配比 × 0.7
- 现金目标：总资产的 5-15%（根据市场整体估值调整）

### 2.2 调仓建议 API

**文件**: `backend/app.py`

```python
@app.get("/api/portfolio/rebalancing")
async def get_rebalancing_suggestion():
    """获取 AI 调仓建议。"""
```

返回结构：
```json
{
  "total_assets": 100000,
  "cash_balance": 5000,
  "current_allocation": {"stock": 0.45, "bond": 0.40, "cash": 0.05, "other": 0.10},
  "target_allocation": {"stock": 0.50, "bond": 0.35, "cash": 0.10, "other": 0.05},
  "drift": {"stock": -0.05, "bond": 0.05, "cash": -0.05, "other": 0.05},
  "suggestions": [
    {"action": "buy", "fund_code": "510300", "fund_name": "沪深300ETF", "reason": "低估加仓", "amount_range": "3000-5000"},
    {"action": "sell", "fund_code": "014846", "fund_name": "恒乐债券A", "reason": "配比偏高", "amount_range": "2000-4000"},
    {"action": "hold_cash", "amount": 2000, "reason": "保持流动性"}
  ],
  "valuation_basis": {...}
}
```

### 2.3 调仓前端卡片

**文件**: `frontend/src/components/Dashboard.vue`

在"持仓健康度"卡片旁新增"智能调仓"卡片：
- 显示当前配比 vs 目标配比的环形图
- 偏离度指示器（绿色=平衡，黄色=轻微偏离，红色=严重偏离）
- 调仓建议列表（买入/卖出/持有）
- "一键应用"按钮（生成交易计划）

---

## 第三部分：智能现金管理

### 3.1 增强现金建议函数

**文件**: `backend/app.py` 的 `_get_cash_advice()` (line 4930)

当前只看债市温度，改为综合考虑：
1. **债市温度** → 债券型建议（保留现有逻辑）
2. **权益估值** → 低估指数可建议定投
3. **现金占比** → 过高时（>15%）提醒配置，过低时（<3%）提醒保留
4. **持仓集中度** → 过高时建议分散

```python
def _get_cash_advice(temperature, balance, total_assets, valuation_data, holdings):
    cash_ratio = balance / total_assets if total_assets > 0 else 0
    advice = []

    # 1. 现金占比检查
    if cash_ratio > 0.15:
        advice.append({"type": "deploy", "message": f"现金占比{cash_ratio:.0%}偏高，建议配置", ...})
    elif cash_ratio < 0.03:
        advice.append({"type": "reserve", "message": f"现金占比仅{cash_ratio:.0%}，建议保留流动性", ...})

    # 2. 债券方向（保留现有逻辑）
    ...

    # 3. 权益低估机会
    undervalued = [v for v in valuation_data if v["percentile"] <= 30]
    if undervalued and cash_ratio > 0.05:
        advice.append({"type": "equity_opportunity", "indexes": undervalued[:3], ...})
```

### 3.2 现金预警

**文件**: `backend/app.py`

新增后台任务，在持仓刷新后检查：
- 现金占比 > 20% → 创建 `info` 级别预警
- 现金占比 > 30% → 创建 `warning` 级别预警
- 现金余额 < 1000 → 创建 `info` 级别预警

---

## 第四部分：前端改动

### 4.1 Dashboard 调仓卡片

新增"智能调仓"区域，替代当前的"AI 调仓建议"按钮：
- 配比对比图（当前 vs 目标）
- 偏离度指示
- 具体建议列表

### 4.2 Dashboard 现金管理增强

在现有现金管理卡片中：
- 显示现金占总资产比例
- 权益低估机会推荐（如有）
- 债券配置建议（保留现有）

### 4.3 对话中展示持仓感知

当 AI 回复涉及持仓时，自动展示持仓数据卡片（当前持仓列表 + 盈亏），让用户知道 AI 是基于真实数据分析的。

---

## 关键文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `backend/portfolio_context.py` | **新建** | 统一持仓上下文构建 |
| `backend/rebalancer.py` | **新建** | 调仓分析逻辑 |
| `backend/agent/orchestrator.py` | 修改 | 注入持仓+估值上下文 |
| `backend/agent/multi_agent.py` | 修改 | 专家 Agent 注入持仓上下文 |
| `backend/db.py` | 修改 | `get_portfolio_summary` 加入现金 |
| `backend/app.py` | 修改 | 调仓 API + 增强现金建议 + 现金预警 |
| `frontend/src/api/index.js` | 修改 | 新增调仓 API 函数 |
| `frontend/src/components/Dashboard.vue` | 修改 | 调仓卡片 + 现金增强 |

---

## 实施顺序

1. **Phase 1**（最高优先级）：持仓上下文注入 — 让所有 AI 分析都能看到持仓
   - `portfolio_context.py` → `orchestrator.py` → `multi_agent.py` → `db.py` 加现金

2. **Phase 2**：智能调仓 — 结合估值给出具体调仓建议
   - `rebalancer.py` → 调仓 API → Dashboard 调仓卡片

3. **Phase 3**：现金管理增强 — 综合估值的现金建议 + 预警
   - 增强 `_get_cash_advice` → 现金预警 → Dashboard 现金卡片

---

## 验证方案

1. 发送"我的持仓怎么样" → AI 回复应直接引用具体基金名称、盈亏数据，无需调用工具
2. 发送"需要调仓吗" → 返回具体偏离度和调仓建议
3. Dashboard 调仓卡片 → 显示配比对比和建议
4. Dashboard 现金管理 → 显示权益低估机会（如有）
5. 现金占比 > 20% → 自动产生预警
