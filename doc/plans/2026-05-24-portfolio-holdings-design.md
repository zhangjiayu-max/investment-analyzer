# 持仓管理功能设计

## 概述

新增持仓管理功能，允许用户录入当前持有的基金/指数信息，包括持仓金额、盈亏情况等。在对话时，专家 Agent 可以结合持仓数据给出更精准的加减仓建议。

---

## 数据库设计

### 1. 持仓表 `portfolio_holdings`

```sql
CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',          -- 用户ID（预留多用户支持）
    fund_code TEXT NOT NULL,                  -- 基金代码（如 "161725"）
    fund_name TEXT NOT NULL,                  -- 基金名称（如 "招商中证白酒指数"）
    index_code TEXT,                          -- 关联的指数代码（如 "399997"）
    index_name TEXT,                          -- 关联的指数名称（如 "中证白酒"）
    shares REAL DEFAULT 0,                    -- 持有份额
    cost_price REAL DEFAULT 0,                -- 成本价（每份）
    current_price REAL,                       -- 当前净值（定期更新）
    total_cost REAL DEFAULT 0,                -- 总成本 = shares * cost_price
    current_value REAL,                       -- 当前市值 = shares * current_price
    profit_loss REAL,                         -- 盈亏 = current_value - total_cost
    profit_rate REAL,                         -- 收益率 = profit_loss / total_cost
    buy_date TEXT,                            -- 首次买入日期
    last_update TEXT,                         -- 最后更新时间
    notes TEXT,                               -- 备注
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(user_id, fund_code)
);
```

### 2. 交易记录表 `portfolio_transactions`

```sql
CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holding_id INTEGER REFERENCES portfolio_holdings(id),
    user_id TEXT DEFAULT 'default',
    fund_code TEXT NOT NULL,
    transaction_type TEXT NOT NULL,           -- 'buy' | 'sell' | 'dividend'
    amount REAL NOT NULL,                     -- 交易金额
    shares REAL,                              -- 交易份额
    price REAL,                               -- 交易价格
    transaction_date TEXT NOT NULL,           -- 交易日期
    notes TEXT,                               -- 备注
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

---

## API 设计

### 持仓 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/portfolio` | 获取所有持仓 |
| POST | `/api/portfolio` | 新增持仓 |
| PUT | `/api/portfolio/{id}` | 更新持仓 |
| DELETE | `/api/portfolio/{id}` | 删除持仓 |
| POST | `/api/portfolio/{id}/refresh` | 刷新持仓价格 |

### 交易记录

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/portfolio/{holding_id}/transactions` | 获取交易记录 |
| POST | `/api/portfolio/transactions` | 新增交易记录 |

### 持仓分析

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/portfolio/summary` | 持仓汇总（总市值、总盈亏、收益率） |
| GET | `/api/portfolio/analysis` | 持仓分析（行业分布、估值分析） |

---

## 与多 Agent 系统集成

### 新增工具：`query_portfolio`

```python
{
    "type": "function",
    "function": {
        "name": "query_portfolio",
        "description": "查询用户当前持仓信息，包括持仓基金、金额、盈亏等。当用户问到持仓相关问题时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["summary", "detail", "by_index"],
                    "description": "查询类型：summary=汇总, detail=详情, by_index=按指数"
                },
                "index_name": {
                    "type": "string",
                    "description": "指数名称（当 query_type=by_index 时使用）"
                }
            },
            "required": ["query_type"]
        }
    }
}
```

### 专家 Agent 使用持仓数据

1. **估值专家**：结合持仓成本和当前估值，判断是否应该加仓/减仓
2. **风险评估师**：分析持仓集中度风险、回撤风险
3. **资产配置师**：基于当前持仓给出再平衡建议

### 对话示例

```
用户: 我的白酒持仓现在应该怎么操作？

Agent 分析流程：
1. 调用 query_portfolio 获取白酒持仓详情
2. 调用 query_valuation 获取白酒当前估值
3. 调用 calculate_metrics 计算风险指标
4. 综合分析：
   - 持仓成本 vs 当前价格
   - 当前估值百分位
   - 持仓占比是否过高
   - 给出具体建议：持有/加仓/减仓
```

---

## 前端设计

### 1. 持仓管理页面

- 持仓列表（基金名称、持仓金额、盈亏、收益率）
- 新增/编辑持仓表单
- 交易记录查看
- 持仓汇总图表（饼图展示行业分布）

### 2. 对话中展示持仓信息

当 Agent 分析持仓相关问题时，在回答中展示：
- 当前持仓概况卡片
- 具体建议（加仓/减仓金额建议）

---

## 实施步骤

1. **Phase 1**：数据库表创建 + 基础 CRUD API
2. **Phase 2**：新增 `query_portfolio` 工具，集成到专家 Agent
3. **Phase 3**：前端持仓管理页面
4. **Phase 4**：持仓分析功能（结合估值数据）

---

## 注意事项

1. **数据安全**：持仓是敏感金融数据，需要考虑数据加密
2. **价格更新**：需要定期更新基金净值（可通过 akshare 获取）
3. **多用户支持**：虽然当前是单用户，但表结构预留了 user_id 字段
4. **盈亏计算**：需要考虑分红再投资的情况
