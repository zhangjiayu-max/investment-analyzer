# 持仓管理增强设计 — 风险分析与加减仓建议

## 背景

当前持仓管理已实现基础 CRUD（基金代码、份额、成本价、当前净值、盈亏计算），
但要做好**风险分析**和**加减仓建议**，还需要补充用户资产概况和投资偏好等信息。

---

## 一、分析场景与所需数据

### 场景 1：持仓集中度风险

> "你白酒仓位占比太高了，建议分散"

**已有**：各基金持仓市值
**缺**：用户总资产规模

→ 需要 `total_assets`（总可投资资产），才能算出单只基金占比

### 场景 2：加减仓建议

> "白酒低估，建议加仓 5000"

**已有**：持仓成本和当前估值
**缺**：可用现金、定投计划

→ 需要 `available_cash`（可用资金）和 `investment_plan`（定投频率/金额）

### 场景 3：止损/止盈判断

> "你已盈利 40%，考虑分批止盈"

**已有**：当前收益率
**缺**：用户的目标收益和风险承受力

→ 需要 `target_profit_rate`（目标收益率）、`stop_loss_rate`（止损线）、`risk_tolerance`（风险偏好）

### 场景 4：资产配置分析

> "你全是股票基金，建议配点债"

**已有**：基金持仓
**缺**：其他资产类别（银行理财、货币基金、债券等）

→ 需要 `asset_allocation`（按大类登记资产金额）

---

## 二、数据库设计

### 2.1 持仓表扩展字段（portfolio_holdings）

在现有表基础上新增：

```sql
ALTER TABLE portfolio_holdings ADD COLUMN available_cash REAL DEFAULT 0;
ALTER TABLE portfolio_holdings ADD COLUMN target_profit_rate REAL;
ALTER TABLE portfolio_holdings ADD COLUMN stop_loss_rate REAL;
```

| 字段 | 类型 | 说明 |
|------|------|------|
| available_cash | REAL | 该基金关联的可用现金（用于判断能否加仓） |
| target_profit_rate | REAL | 目标收益率，如 0.3 表示 30%。到达后提醒止盈 |
| stop_loss_rate | REAL | 止损线，如 -0.15 表示亏损 15% 时止损 |

> 注：available_cash 可以放在持仓级别（每只基金关联的备用金），
> 也可以放在用户级别。放在持仓级别更灵活，用户可以为不同基金分配不同的加仓预算。

### 2.2 用户资产概况表（user_profile）

```sql
CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default' UNIQUE,
    total_assets REAL DEFAULT 0,              -- 总可投资资产
    available_cash REAL DEFAULT 0,            -- 全局可用现金
    risk_tolerance TEXT DEFAULT 'moderate',   -- conservative / moderate / aggressive
    investment_horizon TEXT DEFAULT '3年',     -- 投资期限
    monthly_invest REAL DEFAULT 0,            -- 每月可投资金额
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
```

| 字段 | 说明 | 分析用途 |
|------|------|---------|
| total_assets | 总可投资资产 | 计算持仓集中度 |
| available_cash | 全局可用现金 | 判断能否加仓 |
| risk_tolerance | 风险偏好 | 决定建议激进/保守程度 |
| investment_horizon | 投资期限 | 短期偏防守，长期可承受更大波动 |
| monthly_invest | 每月可投资金额 | 定投方案设计 |

### 2.3 资产配置表（asset_allocation）

```sql
CREATE TABLE IF NOT EXISTS asset_allocation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',
    category TEXT NOT NULL,           -- 资产类别
    category_name TEXT,               -- 类别中文名
    amount REAL DEFAULT 0,            -- 金额
    target_ratio REAL,                -- 目标配比（可选）
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(user_id, category)
);
```

预设类别（category 枚举）：

| category | 中文名 | 说明 |
|----------|--------|------|
| stock_fund | 股票基金 | 场外基金（自动从持仓汇总） |
| bond_fund | 债券基金 | 债基 |
| money_market | 货币基金 | 余额宝等 |
| bank_deposit | 银行存款 | 活期/定期 |
| stock | 股票 | 场内股票 |
| bond | 债券 | 国债/企业债 |
| gold | 黄金 | 黄金 ETF/实物 |
| other | 其他 | 房产、保险等 |

> 注：stock_fund 类别的金额可以从 portfolio_holdings 自动汇总，
> 用户只需手动填其他类别。

---

## 三、API 设计

### 3.1 用户资产概况

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/portfolio/profile` | 获取用户资产概况 |
| PUT | `/api/portfolio/profile` | 更新用户资产概况 |

### 3.2 资产配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/portfolio/allocation` | 获取资产配置列表 |
| PUT | `/api/portfolio/allocation` | 批量更新资产配置 |

### 3.3 持仓增强

| 方法 | 路径 | 说明 |
|------|------|------|
| PUT | `/api/portfolio/{id}` | 现有接口，新增可选字段 target_profit_rate / stop_loss_rate |

---

## 四、Agent 工具增强

### 4.1 query_portfolio 工具扩展

新增查询类型 `risk_analysis`：

```json
{
    "query_type": "risk_analysis",
    "description": "综合分析持仓风险，包括集中度、配置合理性、止盈止损触发"
}
```

返回数据结构：

```json
{
    "profile": {
        "total_assets": 1000000,
        "available_cash": 200000,
        "risk_tolerance": "moderate",
        "monthly_invest": 10000
    },
    "concentration": [
        {
            "fund_name": "招商中证白酒指数",
            "market_value": 15000,
            "ratio": 0.015,
            "risk_level": "low"
        }
    ],
    "allocation": {
        "stock_fund": 600000,
        "bond_fund": 200000,
        "money_market": 200000,
        "stock_fund_ratio": 0.6
    },
    "alerts": [
        {"type": "take_profit", "fund": "白酒", "profit_rate": 0.42, "target": 0.3},
        {"type": "stop_loss", "fund": "半导体", "profit_rate": -0.18, "target": -0.15},
        {"type": "concentration", "fund": "白酒", "ratio": 0.35, "threshold": 0.25}
    ],
    "suggestions": [
        "白酒已达到目标收益率 30%，建议分批止盈",
        "半导体触发止损线 -15%，建议评估是否减仓",
        "可用现金 20 万，当前低估品种可适当加仓"
    ]
}
```

### 4.2 专家 Agent 增强

**风险评估师**（risk_assessor）：
- 调用 `query_portfolio` 的 `risk_analysis` 模式
- 结合集中度、止损触发、配置偏离度给出风险评级

**资产配置师**（allocation_advisor）：
- 调用 `query_portfolio` 的 `summary` + `risk_analysis`
- 基于当前配置 vs 目标配置，给出再平衡建议
- 基于可用现金和定投计划，给出具体加仓方案

**估值专家**（valuation_expert）：
- 调用 `query_portfolio` 的 `by_index` 模式
- 结合持仓成本和当前估值，判断是否值得加仓

---

## 五、前端设计

### 5.1 资产概况卡片（持仓页面顶部）

```
┌─────────────────────────────────────────────────┐
│  总资产: ¥1,000,000   可用现金: ¥200,000        │
│  风险偏好: 稳健       投资期限: 3年              │
│  每月可投: ¥10,000    [编辑资产概况]             │
├─────────────────────────────────────────────────┤
│  资产配置                                       │
│  ████████░░ 股票基金 60%                        │
│  ████░░░░░░ 债券基金 20%                        │
│  ████░░░░░░ 货币基金 20%                        │
│  [编辑配置]                                     │
└─────────────────────────────────────────────────┘
```

### 5.2 持仓列表增强

在现有表格基础上新增列：
- 占比（持仓市值 / 总资产）
- 止盈止损状态标记（触发时高亮）

### 5.3 止盈止损提醒

持仓列表上方显示提醒条：
```
⚠️ 触发提醒：
  📈 白酒已盈利 42%（目标 30%），建议分批止盈
  📉 半导体已亏损 18%（止损线 15%），建议评估减仓
```

---

## 六、实施步骤

| Phase | 内容 | 优先级 |
|-------|------|--------|
| Phase 1 | user_profile 表 + API + 前端资产概况卡片 | P0 |
| Phase 2 | asset_allocation 表 + API + 前端配置编辑 | P0 |
| Phase 3 | 持仓表新增止盈止损字段 + 前端表单 | P1 |
| Phase 4 | query_portfolio 扩展 risk_analysis 模式 | P1 |
| Phase 5 | 专家 Agent prompt 更新（结合持仓分析） | P1 |
| Phase 6 | 前端止盈止损提醒 + 集中度可视化 | P2 |

---

## 七、注意事项

1. **数据隐私**：持仓和资产是高度敏感数据，当前单用户模式无需加密，但多用户时需考虑
2. **自动化更新**：current_price 应通过 akshare 定期更新基金净值，减少用户手动操作
3. **stock_fund 自动汇总**：asset_allocation 中 stock_fund 类别可从 portfolio_holdings 自动计算，不需要用户手动填
4. **分红再投资**：已有 transaction_type='dividend' 支持，分红会自动降低持仓成本
5. **渐进式录入**：不要求用户一次性填完所有信息，核心必填项只需基金代码/名称/份额/成本价
