# 反事实决策验证（智能补仓假设操作跟踪）

> 日期：2026-07-14
> 目标：记录"假设加仓"操作，跟踪后续走势，验证智能补仓逻辑是否有效
> 触发场景：用户持有医药800基金，近期涨20%但仍亏损（最低点未加仓），想验证"如果当时按系统建议补仓了，现在是否已回本"

---

## 一、问题背景

### 用户痛点
1. 系统给出智能补仓建议时，用户**没有执行**
2. 事后标的上涨，用户后悔"如果当时补了就好了"
3. 但用户**无法验证**：系统建议到底靠不靠谱？补了是否真能回本？

### 当前系统的缺口
| 能力 | 现状 | 缺口 |
|------|------|------|
| 智能补仓建议 | [smart_add_planner.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/services/advisor/smart_add_planner.py) 双引擎实时计算 | **建议不落库**，事后回看不了"30天前建议补多少" |
| 假设交易记录 | [portfolio_transactions](file:///Users/xiaoyuer/projects/investment-analyzer/backend/db/__init__.py) 只有 `is_system`（标记系统补录初始建仓） | **没有 is_hypothetical 字段**，无法区分真实vs假设交易 |
| 反事实验证 | [master_decision_backtest.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/services/master_decision_backtest.py) 有 T+7/T+30 净值对比 | 只验证大师决策方向，**不验证假设补仓的盈亏** |
| 假设vs真实对比 | [decision_accuracy.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/services/decision_accuracy.py) `get_adoption_stats()` 有采纳vs未采纳统计 | 只统计方向收益，**没有假设组合vs真实组合净值对比** |

---

## 二、设计方案

### 核心思路
复用已有的 `master_decision_history` T+N 验证成熟模式，补三个缺口：

```
┌─────────────────────────────────────────────────────────────┐
│  1. 建议快照落库          2. 假设交易标记         3. T+N反事实验证  │
│  (smart_add_snapshots)   (is_hypothetical字段)   (复用净值对比)    │
└─────────────────────────────────────────────────────────────┘
         │                        │                       │
         ▼                        ▼                       ▼
   每次生成建议时           用户对历史建议标记           按假设买入日取净值
   落库快照(金额/估值/     "假设我补了" → 写入          T+7/T+30/回本日
   亏损率/建议日期)        portfolio_transactions       验证 + 对比假设vs
                          (is_hypothetical=1)          真实收益
```

### 2.1 新增表：`smart_add_snapshots`（智能补仓建议快照 + 自动假设交易）

每次 `generate_smart_add_plan()` 被调用时，为每个标的的建议落库一条快照，**同时自动创建一笔假设交易**（is_hypothetical=1）。用户无需任何操作，直接看验证结果。

```sql
CREATE TABLE IF NOT EXISTS smart_add_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',
    fund_code TEXT NOT NULL,
    fund_name TEXT,
    -- 建议内容快照
    suggested_amount REAL,              -- 系统建议补仓金额
    suggested_tier TEXT,                -- 触发的金字塔档位（如"-20%~-30%"）
    -- 决策时刻的市场/持仓快照
    profit_rate_at_snapshot REAL,       -- 当时的亏损率
    valuation_zscore REAL,              -- 当时的估值z-score
    current_price_at_snapshot REAL,     -- 当时的基金净值
    -- 假设操作关联（用户标记"假设补仓"时填入）
    hypothetical_tx_id INTEGER,         -- 关联portfolio_transactions.id
    -- 元数据
    snapshot_date TEXT NOT NULL,        -- 建议生成日期
    created_at TEXT NOT NULL,
    notes TEXT
);
```

**设计要点**：
- 每次生成建议自动落库快照，**同时自动创建假设交易**（用户无感知、无需操作）
- 同一标的同一天只保留最新一条（去重，按 fund_code + snapshot_date 唯一，旧的假设交易一并删除）
- `hypothetical_tx_id` 在快照创建时立即填入（关联自动生成的假设交易）
- 假设交易金额 = 系统建议金额（suggested_amount），买入日 = 快照日期，买入价 = 当日净值

### 2.2 扩展 `portfolio_transactions` 表：新增 `is_hypothetical` 字段

```sql
ALTER TABLE portfolio_transactions ADD COLUMN is_hypothetical INTEGER DEFAULT 0;
-- 0 = 真实交易（默认），1 = 假设交易
```

**设计要点**：
- 假设交易**不影响真实持仓计算**：`_recalculate_holding()` 和所有持仓查询都过滤 `is_hypothetical = 0`
- 假设交易只用于反事实验证，独立计算假设组合盈亏
- 复用现有的 `fund_code / amount / shares / price / transaction_date` 字段，无需新表

### 2.3 反事实验证引擎：`counterfactual_verifier.py`

复用 `master_decision_backtest._get_fund_price_at_date()` 的净值取数逻辑，新增盈亏计算。

```python
def verify_hypothetical_tx(tx_id: int) -> dict:
    """验证单条假设交易的后续盈亏。

    Returns:
        {
            "tx_id": int,
            "fund_code": str,
            "buy_date": str,
            "buy_price": float,         # 假设买入日净值
            "buy_amount": float,
            "buy_shares": float,
            "current_price": float,     # 当前净值
            "current_value": float,     # 假设持仓当前市值
            "profit_loss": float,       # 假设持仓盈亏
            "profit_rate": float,       # 假设持仓收益率
            "holding_days": int,        # 持有天数
            "is_breakeven": bool,       # 是否已回本
            "verified_at": str,
        }
    """
```

**对比维度**（回答用户"如果补了是否回本"）：
1. **假设补仓的独立盈亏**：单笔假设买入 → 当前市值 vs 买入成本
2. **摊薄效果验证**：假设补仓后该标的的**新平均成本** vs 当前价 → 是否回本
3. **假设组合 vs 真实组合**：所有假设交易构成"假设组合"，对比真实组合的净值曲线

### 2.4 API 设计

新增 2 个 API（在 `routers/analysis/smart_add.py` 中）。**假设操作自动生成，无需用户录入**：

| API | 方法 | 用途 |
|-----|------|------|
| `/api/smart-add/snapshots` | GET | 查询历史建议快照列表（支持按fund_code/日期筛选） |
| `/api/smart-add/hypothetical/track` | GET | 查询所有假设交易的当前盈亏 + 假设vs真实组合对比（只读，自动跟踪） |

可选：`DELETE /api/smart-add/hypothetical/{tx_id}` 删除某条假设交易（清理噪声用）。

**GET `/api/smart-add/hypothetical/track` 返回**：
```json
{
    "hypothetical_txs": [
        {
            "fund_code": "161725",
            "fund_name": "招商中证白酒",
            "buy_date": "2026-06-15",
            "buy_amount": 5000,
            "buy_price": 1.23,
            "current_price": 1.45,
            "profit_loss": 894.3,
            "profit_rate": 0.178,
            "holding_days": 29,
            "is_breakeven": true,
            "snapshot_suggested_amount": 4500,
            "matched_suggestion": true   // 假设金额是否与建议一致
        }
    ],
    "summary": {
        "total_hypothetical_invested": 5000,
        "total_hypothetical_value": 5894.3,
        "total_profit_loss": 894.3,
        "total_profit_rate": 0.178,
        "breakeven_count": 1,
        "total_count": 1,
        "suggestion_match_rate": 1.0    // 假设操作与建议一致的比例
    },
    "comparison": {
        "real_portfolio_profit_rate": -0.05,   // 真实组合当前收益率
        "hypothetical_profit_rate": 0.178,      // 假设组合补仓部分收益率
        "improvement": 0.228                    // 如果补了的改善幅度
    }
}
```

---

## 三、改造范围

### 3.1 后端

| 文件 | 改动 |
|------|------|
| `db/__init__.py` | `init_db()` 新增 `smart_add_snapshots` 建表 + `portfolio_transactions` 加 `is_hypothetical` 字段 |
| `db/smart_add_snapshots.py` | **新建**：快照表 CRUD（create/get/list/link_hypothetical_tx） |
| `db/portfolio.py` | 假设交易 CRUD + 持仓查询过滤 `is_hypothetical=0` |
| `services/advisor/smart_add_planner.py` | `generate_smart_add_plan()` 末尾落库快照（可开关） |
| `services/counterfactual_verifier.py` | **新建**：反事实验证引擎（复用净值取数） |
| `routers/analysis/smart_add.py` | 新增 3 个 API |

### 3.2 前端

在智能补仓页面新增"建议历史"和"假设操作验证"两个区块（组件复用现有样式）。

### 3.3 配置项

| 配置键 | 默认值 | 说明 |
|--------|--------|------|
| `smart_add.snapshot_enabled` | `true` | 建议快照落库开关（默认开启） |
| `smart_add.hypothetical_enabled` | `true` | 假设操作功能开关 |

---

## 四、关键设计决策

### 4.1 为什么用"自动影子交易"而非"手动标记"？

**选择方案**：系统每次生成建议时自动创建假设交易
**否决方案**：用户手动回看历史建议并标记"假设我补了"

**理由**：
- 用户无需任何额外操作，打开页面就能看到"如果过去都按建议补仓了，现在赚/亏多少"
- 防噪声：同一标的同一天去重（按 fund_code + snapshot_date 唯一），不会因刷新产生重复
- 快照表完整保留历史建议，假设交易跟随快照自动生成、自动更新关联
- 如果用户实际执行了操作，可后续把 `is_hypothetical` 改为 0 转为真实交易

### 4.2 为什么假设交易复用 `portfolio_transactions` 而非新表？

**选择方案**：`portfolio_transactions` 加 `is_hypothetical` 字段
**否决方案**：新建 `hypothetical_transactions` 表

**理由**：
- 假设交易的字段（fund_code/amount/shares/price/date）与真实交易完全一致
- 复用现有 CRUD 代码，只需在查询时加 `WHERE is_hypothetical = 0` 过滤
- 便于未来"假设转真实"（用户实际执行了操作时，把 `is_hypothetical` 改为 0 即可）

### 4.3 防污染：假设交易绝不影响真实持仓

所有现有持仓计算函数必须过滤假设交易：
- `_recalculate_holding()` — 重算成本时过滤
- `list_holdings()` / `get_portfolio_summary()` — 持仓列表过滤
- `get_holding_by_fund()` — 单标的查询过滤

---

## 五、数据流

### 5.1 建议快照 + 假设交易自动落库
```
用户访问智能补仓页面
  → GET /api/smart-add/plan
  → generate_smart_add_plan() 计算建议
  → 落库 smart_add_snapshots（每个标的一条）
  → 同时自动创建 portfolio_transactions (is_hypothetical=1)
  → 关联 snapshot.hypothetical_tx_id
  → 返回建议给前端
```

### 5.2 反事实跟踪验证（只读，用户无需操作）
```
用户查看假设操作效果
  → GET /api/smart-add/hypothetical/track
  → 遍历所有 is_hypothetical=1 的交易
  → 对每条取 buy_date 净值 vs 当前净值 → 算盈亏
  → 汇总假设组合 vs 真实组合对比
  → 返回结果
```

---

## 六、测试方案

### 6.1 单元测试
- `test_smart_add_snapshots.py`：快照表 CRUD + 去重
- `test_counterfactual_verifier.py`：盈亏计算 + 回本判定 + 净值取数容错
- 持仓查询过滤测试：确保假设交易不污染真实持仓

### 6.2 集成验证
复现用户场景：
1. 构造一条医药800的历史建议快照（日期=1个月前，建议补仓X元）
2. 标记假设操作"假设补了X元"
3. 跑反事实验证 → 确认能算出假设盈亏 + 是否回本
4. 确认真实持仓未受污染

---

## 七、风险与降级

| 风险 | 降级策略 |
|------|---------|
| 基金净值历史数据缺失（新基金） | 验证返回 `status=no_nav_data`，不算盈亏 |
| 快照表膨胀（每天多次访问） | 按 fund_code + snapshot_date 去重，同日只留最新 |
| 假设交易误标为真实 | 所有持仓查询默认过滤 `is_hypothetical=0`，双重保险 |
| 净值取数延迟（QDII基金T+2） | 容错：取最近可用净值日期，标注 `nav_date_lag` |

---

## 八、实施顺序

1. 建表 + 字段扩展（`db/__init__.py` + `db/smart_add_snapshots.py`）
2. 持仓查询过滤假设交易（`db/portfolio.py`）
3. 快照自动落库（`smart_add_planner.py`）
4. 反事实验证引擎（`services/counterfactual_verifier.py`）
5. API 路由（`routers/analysis/smart_add.py`）
6. 单元测试
7. 前端展示
8. 提交推送重启
