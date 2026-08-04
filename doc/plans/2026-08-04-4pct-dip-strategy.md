# 雷牛牛4%定投法 — 系统集成设计稿

> 来源：[雷牛牛4%定投法Pro版](https://mp.weixin.qq.com/s/8Dhofw5taUPL_teHoYJ8-w)
> 核心：估值锁底 + 跌幅触发 + 固定份数 + 机械纪律

## 一、方法本质

| 维度 | 约束 |
|---|---|
| 估值前提 | 百分位 < 20%（默认，可调）才允许启动 |
| 触发条件 | 相对上一买入点跌幅 ≥ 4%（可调3%/4%/5%） |
| 资金管理 | 总预算÷10份，每次触发买1份 |
| 买入基准 | 递归用上一买入点（不是当前价） |
| 卖出闭环 | 估值百分位 > 80% 分批卖 |

**与现有3种智能补仓信号的关系**：并列第四种策略，互不替代。

## 二、数据模型

### dip_investment_plans 表（建仓时用户配置）

```sql
CREATE TABLE IF NOT EXISTS dip_investment_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_code TEXT NOT NULL,              -- 基金代码
    fund_name TEXT,                       -- 基金名称
    max_amount REAL NOT NULL,            -- 投资上限（用户填写，如50000）
    total_shares INTEGER DEFAULT 10,     -- 总份数（固定10，保留可调）
    dip_pct REAL DEFAULT 4.0,            -- 单次触发跌幅%（默认4，可调3/5）
    valuation_threshold REAL DEFAULT 20, -- 起始估值百分位门槛（默认20%）
    metric_type TEXT,                    -- 估值指标（市盈率/市净率/市销率，自动匹配）
    enabled INTEGER DEFAULT 1,           -- 是否启用
    status TEXT DEFAULT 'active',        -- active/completed/closed
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(fund_code)
);
```

### dip_investment_triggers 表（每次触发记录）

```sql
CREATE TABLE IF NOT EXISTS dip_investment_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,             -- 关联 plan
    fund_code TEXT NOT NULL,
    trigger_num INTEGER NOT NULL,         -- 第几次触发（1-10）
    trigger_date TEXT NOT NULL,           -- 触发日期
    prev_buy_price REAL,                  -- 上一买入价
    current_price REAL,                   -- 当前价
    actual_dip_pct REAL,                  -- 实际跌幅%
    valuation_percentile REAL,           -- 触发时估值百分位
    suggested_amount REAL,                -- 建议金额（max_amount/total_shares）
    executed INTEGER DEFAULT 0,          -- 是否已执行买入
    executed_date TEXT,                  -- 执行日期
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (plan_id) REFERENCES dip_investment_plans(id),
    UNIQUE(plan_id, trigger_num)
);
```

## 三、策略引擎

### DipStrategy4Pct 类

```python
class DipStrategy4Pct:
    """4%定投法策略引擎"""

    def check_trigger(self, plan, current_price, valuation) -> dict:
        """检查是否触发4%定投"""
        # 1. 估值前置检查：百分位必须 < threshold
        if valuation.percentile >= plan.valuation_threshold:
            return {"triggered": False, "reason": f"估值{valuation.percentile}%≥{plan.valuation_threshold}%门槛"}

        # 2. 查上一买入点
        prev_buy = self._get_last_buy_price(plan.fund_code)
        if prev_buy is None:
            # 首次建仓：当前价即基准
            return {"triggered": True, "reason": "首次建仓", "trigger_num": 1,
                    "prev_buy_price": None, "current_price": current_price,
                    "suggested_amount": plan.max_amount / plan.total_shares}

        # 3. 计算跌幅
        dip_pct = (prev_buy - current_price) / prev_buy * 100
        if dip_pct < plan.dip_pct:
            return {"triggered": False, "reason": f"跌幅{dip_pct:.2f}%<{plan.dip_pct}%未触发",
                    "next_trigger_price": prev_buy * (1 - plan.dip_pct/100)}

        # 4. 检查是否已完成所有份数
        executed_count = self._count_executed(plan.id)
        if executed_count >= plan.total_shares:
            return {"triggered": False, "reason": "已达总份数上限", "status": "completed"}

        # 5. 触发！
        return {"triggered": True, "trigger_num": executed_count + 1,
                "prev_buy_price": prev_buy, "current_price": current_price,
                "actual_dip_pct": dip_pct, "suggested_amount": plan.max_amount / plan.total_shares,
                "valuation_percentile": valuation.percentile}
```

### 触发逻辑流程

```
1. 用户在前端配置：基金A，上限50000，10份，跌幅4%，估值门槛20%
2. 系统每日扫描（复用alert_scanner）：
   a. 查基金A当前估值百分位（复用已修复的query_valuation）
   b. 若百分位 < 20% → 进入跌幅检查
   c. 查transactions表最近一次买入价 → 作为prev_buy_price
   d. 计算跌幅 = (prev_buy - current) / prev * 100
   e. 跌幅 ≥ 4% → 触发，建议金额 = 50000/10 = 5000
3. 输出到智能补仓页：第X次/共10份，累计XX/50000
```

## 四、API 设计

### 配置管理

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/dip-plan/` | 创建4%定投配置 |
| GET | `/api/dip-plan/{fund_code}` | 查询单个配置 |
| GET | `/api/dip-plan/list` | 列出所有配置 |
| PUT | `/api/dip-plan/{id}` | 更新配置 |
| DELETE | `/api/dip-plan/{id}` | 删除配置 |

### 触发检查

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/dip-plan/{fund_code}/check` | 手动检查是否触发 |
| GET | `/api/dip-plan/{fund_code}/status` | 查询进度（已投/总额/剩余/下次触发价） |

### 集成到智能补仓

`GET /api/smart-add/plan` 返回中新增 `dip_4pct` 字段，与现有 signal_a/b/c 并列。

## 五、前端设计

### 建仓弹窗新增配置区

在持仓管理 → 新增持仓弹窗中，新增"4%定投法配置"折叠面板：

```
┌─ 4%定投法配置（可选） ─────────────┐
│ ☑ 启用4%定投法                     │
│                                     │
│ 投资上限：[50000    ] 元            │
│ 总份数：  [10  ] 份（固定）         │
│ 单次跌幅：[4   ] %（可调3/4/5）     │
│ 估值门槛：[20  ] %百分位（默认20）  │
│                                     │
│ 预计单次金额：¥5,000               │
│ 预计占总资产：6.2%                  │
└─────────────────────────────────────┘
```

### 智能补仓页新增4%定投卡片

```
┌─ 🎯 4%定投法 ─────────────────────────┐
│ 基金：招商中证白酒 (161725)           │
│ 投资上限：¥50,000                    │
│ 进度：██████░░░░ 6/10份              │
│ 累计投入：¥30,000 / ¥50,000          │
│ 剩余预算：¥20,000                    │
│                                       │
│ ✅ 触发第7次定投                      │
│ 上一买入点：0.9216 (2026-07-15)       │
│ 当前净值：0.8847                      │
│ 跌幅：-4.0% ≥ 4%门槛 ✓               │
│ 估值百分位：3.72% < 20%门槛 ✓        │
│ 建议金额：¥5,000                     │
│ 下次触发价：0.8493 (再跌4%)           │
│                                       │
│ [确认执行] [跳过本次] [查看历史]     │
└───────────────────────────────────────┘
```

## 六、与现有系统的关系

| 模块 | 复用 |
|---|---|
| 估值查询 | 复用 `query_valuation`（已修复后缀兼容+别名兜底） |
| 买入记录 | 复用 `transactions` 表查最近买入价 |
| 智能补仓扫描 | 复用 `alert_scanner` 每日扫描机制 |
| 前端展示 | 复用智能补仓页的卡片布局和ECharts |

**独立开关**：`dip_4pct.enabled`（默认false），不影响现有信号A/B/C。

## 七、实施顺序

1. 后端：建表 + CRUD + 策略引擎
2. 后端：集成到 smart_add_planner
3. 后端：API 路由
4. 前端：建仓弹窗配置区
5. 前端：智能补仓页卡片
6. 验证 + 推送
