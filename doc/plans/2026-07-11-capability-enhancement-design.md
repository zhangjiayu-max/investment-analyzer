# 投资分析助手能力全面提升设计稿

## 版本信息
- **日期**: 2026-07-11
- **版本**: v1.0
- **作者**: AI Agent
- **状态**: 待评审

---

## 一、设计目标

将系统从"分析工具"升级为"智能投资助理"，实现**分析→决策→执行→验证**的完整闭环。

### 核心指标提升
| 维度 | 当前状态 | 目标状态 |
|------|---------|---------|
| 建议可执行性 | 偏分析性，缺少落地计划 | 每条建议附带交易计划 |
| 策略落地能力 | 回测与持仓脱节 | 回测→监控→执行闭环 |
| 资金管理能力 | 资金桶独立，无联动 | 桶与持仓自动映射 |
| AI记忆能力 | 单轮对话，无长期记忆 | 多轮上下文感知 |
| 移动端体验 | 基础功能完整 | 手势操作+快捷栏+离线缓存 |

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                     智能投资助理 (v2.0)                       │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌───────────────────┐   │
│  │   AI对话     │  │  每日看板   │  │   机会雷达        │   │
│  │  多轮记忆    │  │  决策日历   │  │  关注基金信号     │   │
│  │  上下文感知  │  │  待执行计划  │  │  事件落地验证     │   │
│  └──────┬──────┘  └──────┬──────┘  └────────┬──────────┘   │
│         │                │                  │                │
│         ▼                ▼                  ▼                │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                  交易计划引擎                          │   │
│  │  建议解析 → 金额计算 → 分批策略 → 时机推荐 → 风险检查   │   │
│  └──────────────────┬───────────────────────────────────┘   │
│                     │                                       │
│         ┌───────────┼───────────┐                           │
│         ▼           ▼           ▼                           │
│  ┌─────────┐  ┌─────────┐  ┌───────────────┐              │
│  │ 持仓管理 │  │策略沙盒 │  │   资金桶      │              │
│  │ 交易记录 │  │回测联动 │  │ 跨桶调拨      │              │
│  │ AI诊断   │  │执行监控 │  │ 目标追踪      │              │
│  └─────────┘  └─────────┘  └───────────────┘              │
│                     │                                       │
│                     ▼                                       │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                  数据层 (SQLite)                       │   │
│  │  holdings / recommendations / trade_plans / buckets   │   │
│  │  strategies / monitoring_rules / user_memory          │   │
│  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、详细设计

### 模块1：交易计划生成（P0 — 最高优先级）

#### 3.1.1 业务需求

**问题**: AI 建议偏分析性，用户需要从"知道做什么"到"知道怎么做"的闭环。

**目标**: 每条建议自动生成可执行的交易计划，包含金额、时机、分批策略。

#### 3.1.2 数据模型

```sql
-- 新增 trade_plans 表
CREATE TABLE IF NOT EXISTS trade_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER REFERENCES recommendations(id),
    target_fund_code TEXT NOT NULL,
    target_fund_name TEXT,
    action TEXT NOT NULL,          -- buy / sell / hold / rebalance
    total_amount REAL NOT NULL,     -- 总金额
    batch_count INTEGER DEFAULT 3,  -- 分批次数
    batch_interval_days INTEGER DEFAULT 7,  -- 分批间隔天数
    first_batch_amount REAL,       -- 首笔金额
    price_level TEXT,              -- 估值分位区间 (low/mid/high)
    stop_loss_pct REAL,            -- 止损比例
    take_profit_pct REAL,          -- 止盈比例
    status TEXT DEFAULT 'pending', -- pending / executing / completed / cancelled
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 3.1.3 API 设计

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/trade-plans` | POST | 创建交易计划 |
| `/api/trade-plans/{id}` | GET | 获取单个计划 |
| `/api/trade-plans` | GET | 列表查询 |
| `/api/trade-plans/{id}` | PUT | 更新计划状态 |
| `/api/trade-plans/{id}` | DELETE | 删除计划 |
| `/api/recommendations/{id}/generate-plan` | POST | 从建议生成计划 |

#### 3.1.4 前端设计

**ChatView.vue 增强**：
- 建议卡片新增「生成交易计划」按钮
- 点击后弹出交易计划配置弹窗，自动填充默认值
- 支持调整分批次数、间隔天数、止损/止盈比例

**Dashboard.vue 增强**：
- 新增「待执行决策」时间线模块
- 显示近期需要执行的交易计划
- 支持快速跳转查看详情

#### 3.1.5 核心逻辑

```python
# services/trade_plan_engine.py

def generate_trade_plan(recommendation_id, params=None):
    """从建议生成交易计划"""
    rec = get_recommendation(recommendation_id)
    fund_info = lookup_fund_info(rec.target_fund_code)
    
    # 根据估值分位计算金额
    valuation = get_best_valuation(fund_info.index_code)
    pct = valuation.get('percentile', 50)
    
    # 估值越低，建议买入越多
    if pct < 20:
        multiplier = 2.0  # 深度低估，加倍买入
    elif pct < 40:
        multiplier = 1.5
    elif pct < 60:
        multiplier = 1.0
    elif pct < 80:
        multiplier = 0.5
    else:
        multiplier = 0.2  # 高估，少买或观望
    
    # 基于用户风险偏好和可用资金计算金额
    risk_pref = get_user_profile().get('risk_preference', 'medium')
    cash = get_cash_balance()
    base_amount = calculate_base_amount(cash, risk_pref)
    
    total_amount = base_amount * multiplier
    
    # 分批策略
    batch_count = 3 if pct < 40 else 1  # 低估时分批
    batch_interval = 7  # 每周一批
    
    return {
        'recommendation_id': recommendation_id,
        'target_fund_code': rec.target_fund_code,
        'target_fund_name': fund_info.fund_name,
        'action': rec.action,
        'total_amount': total_amount,
        'batch_count': batch_count,
        'batch_interval_days': batch_interval,
        'price_level': _get_price_level(pct),
        'stop_loss_pct': 0.15,
        'take_profit_pct': 0.20,
    }
```

---

### 模块2：策略沙盒增强（P1）

#### 3.2.1 业务需求

**问题**: 回测结果与真实持仓脱节，无法将策略应用到实际组合。

**目标**: 支持用真实持仓作为回测起点，增加策略监控和执行追踪。

#### 3.2.2 数据模型

```sql
-- 新增 strategy_monitoring 表
CREATE TABLE IF NOT EXISTS strategy_monitoring (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_id INTEGER REFERENCES backtests(id),
    strategy_name TEXT NOT NULL,
    strategy_type TEXT NOT NULL,      -- dca / valuation_dca / percentile_trade / rebalance
    target_code TEXT NOT NULL,
    target_type TEXT DEFAULT 'index',
    parameters TEXT NOT NULL,         -- JSON 存储策略参数
    current_state TEXT DEFAULT 'running',  -- running / paused / stopped
    last_trigger_at TIMESTAMP,
    next_trigger_at TIMESTAMP,
    performance_metrics TEXT,         -- JSON 存储实时绩效
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 新增 strategy_trades 表（策略执行的交易记录）
CREATE TABLE IF NOT EXISTS strategy_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitoring_id INTEGER REFERENCES strategy_monitoring(id),
    trade_type TEXT NOT NULL,         -- buy / sell / rebalance
    fund_code TEXT NOT NULL,
    amount REAL NOT NULL,
    nav REAL,                        -- 成交净值
    trade_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'executed',   -- pending / executed / failed
    error_message TEXT
);
```

#### 3.2.3 API 设计

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/strategies/monitor` | POST | 创建策略监控 |
| `/api/strategies/monitor/{id}` | GET | 获取监控详情 |
| `/api/strategies/monitor` | GET | 列表查询 |
| `/api/strategies/monitor/{id}` | PUT | 更新状态（启停） |
| `/api/strategies/monitor/{id}/trigger` | POST | 手动触发策略 |
| `/api/strategies/backtest/with-portfolio` | POST | 基于真实持仓回测 |

#### 3.2.4 前端设计

**StrategySandboxPage.vue 增强**：
- 新增"从持仓回测"选项，使用当前持仓作为起点
- 策略对比矩阵：收益率、最大回撤、夏普比率、胜率
- 回测结果页新增"开启监控"按钮
- 策略监控仪表盘：实时追踪各策略状态、最近触发、绩效指标

#### 3.2.5 核心逻辑

```python
# services/strategy_monitor.py

def run_strategy_check(monitoring_id):
    """定时检查策略触发条件"""
    monitor = get_strategy_monitoring(monitoring_id)
    params = json.loads(monitor.parameters)
    
    if monitor.strategy_type == 'valuation_dca':
        # 估值加权定投：定期检查，根据估值分位调整金额
        valuation = get_best_valuation(monitor.target_code)
        pct = valuation.get('percentile', 50)
        
        if pct < params['buy_threshold']:
            # 触发买入
            amount = params['monthly_amount'] * _get_multiplier(pct)
            create_strategy_trade(monitoring_id, 'buy', amount)
            update_next_trigger(monitoring_id, days=params['frequency'])
    
    elif monitor.strategy_type == 'percentile_trade':
        # 分位买卖：低分位买，高分位卖
        valuation = get_best_valuation(monitor.target_code)
        pct = valuation.get('percentile', 50)
        
        if pct < params['buy_threshold']:
            create_strategy_trade(monitoring_id, 'buy', params['buy_amount'])
        elif pct > params['sell_threshold']:
            position = get_position(monitor.target_code)
            sell_amount = position.amount * params['sell_ratio']
            create_strategy_trade(monitoring_id, 'sell', sell_amount)
```

---

### 模块3：资金桶与持仓联动（P2）

#### 3.3.1 业务需求

**问题**: 资金桶是独立的，未与实际持仓关联，缺少资金流转建议。

**目标**: 将持仓自动归类到对应资金桶，支持跨桶调拨建议。

#### 3.3.2 数据模型

```sql
-- 修改 goal_buckets 表，增加关联字段
ALTER TABLE goal_buckets ADD COLUMN linked_holdings TEXT;  -- JSON 存储关联的持仓ID列表
ALTER TABLE goal_buckets ADD COLUMN auto_allocate INTEGER DEFAULT 0;  -- 是否自动分配

-- 修改 holdings 表，增加桶关联
ALTER TABLE holdings ADD COLUMN bucket_id INTEGER REFERENCES goal_buckets(id);
ALTER TABLE holdings ADD COLUMN bucket_allocation REAL;  -- 在桶内的分配比例
```

#### 3.3.3 API 设计

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/buckets/{id}/sync-holdings` | POST | 自动同步持仓到桶 |
| `/api/buckets/allocation-suggestion` | GET | 获取跨桶调拨建议 |
| `/api/buckets/{from_id}/transfer/{to_id}` | POST | 执行跨桶调拨 |
| `/api/holdings/{id}/assign-bucket` | PUT | 手动分配持仓到桶 |

#### 3.3.4 前端设计

**GoalBucketsPage.vue 增强**：
- 每个桶显示当前关联的持仓列表
- 支持拖拽持仓到不同桶
- 显示桶内资金与目标的差距
- 新增"调拨建议"按钮，显示过剩资金和缺口

**PortfolioManagement.vue 增强**：
- 持仓卡片显示所属资金桶标签
- 新增筛选：按资金桶筛选

#### 3.3.5 核心逻辑

```python
# services/bucket_engine.py

def sync_holdings_to_bucket(bucket_id):
    """根据风险等级自动分配持仓到桶"""
    bucket = get_goal_bucket(bucket_id)
    holdings = list_holdings()
    linked_ids = []
    
    for h in holdings:
        # 根据基金类型和风险等级匹配桶
        fund_type = get_fund_type(h.fund_code)
        risk_level = _map_fund_to_risk_level(fund_type)
        
        if risk_level == bucket.risk_level:
            assign_holding_to_bucket(h.id, bucket_id)
            linked_ids.append(h.id)
    
    update_bucket_linked_holdings(bucket_id, linked_ids)


def generate_allocation_suggestion():
    """生成跨桶调拨建议"""
    buckets = list_goal_buckets()
    suggestions = []
    
    for bucket in buckets:
        # 计算桶内实际金额（持仓市值 + 现金）
        actual_amount = calculate_bucket_value(bucket.id)
        target_amount = bucket.target_amount
        gap = actual_amount - target_amount
        
        if gap > bucket.target_amount * 0.2:
            # 资金过剩，建议调拨
            suggestions.append({
                'from_bucket': bucket.id,
                'from_bucket_name': bucket.name,
                'excess_amount': gap,
                'suggested_action': 'transfer',
                'reason': f"{bucket.name}资金过剩{gap:.0f}元，超出目标20%"
            })
        elif gap < -bucket.target_amount * 0.1:
            # 资金不足，建议补充
            suggestions.append({
                'to_bucket': bucket.id,
                'to_bucket_name': bucket.name,
                'deficit_amount': abs(gap),
                'suggested_action': 'add',
                'reason': f"{bucket.name}资金不足，缺口{abs(gap):.0f}元"
            })
    
    return suggestions
```

---

### 模块4：AI 多轮记忆与上下文感知（P1）

#### 3.4.1 业务需求

**问题**: 当前对话历史未充分利用，AI 无法记住用户的长期偏好和历史决策。

**目标**: 增加长期记忆模块，支持上下文感知，避免重复提问。

#### 3.4.2 数据模型

```sql
-- user_memory 表已存在，增强字段
ALTER TABLE user_memory ADD COLUMN memory_type TEXT DEFAULT 'preference';  -- preference / decision / risk / behavior
ALTER TABLE user_memory ADD COLUMN relevance_score REAL;  -- 相关性评分
ALTER TABLE user_memory ADD COLUMN last_accessed_at TIMESTAMP;

-- 新增 conversation_context 表（对话级上下文）
CREATE TABLE IF NOT EXISTS conversation_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER REFERENCES conversations(id),
    context_key TEXT NOT NULL,      -- holdings / preferences / risk / recent_decisions
    context_value TEXT NOT NULL,    -- JSON 存储
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 3.4.3 API 设计

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/memory/user` | GET | 获取用户长期记忆 |
| `/api/memory/user` | POST | 写入用户记忆 |
| `/api/memory/context/{conversation_id}` | GET | 获取对话上下文 |
| `/api/memory/context/{conversation_id}` | POST | 更新对话上下文 |

#### 3.4.4 前端设计

**ChatView.vue 增强**：
- 对话时自动注入用户偏好（风险偏好、投资目标、常用策略）
- 显示"记忆中"指示器，提示当前对话使用了历史记忆
- 支持手动编辑用户记忆

**KycWizard.vue 增强**：
- 投资画像数据与记忆系统打通
- 增加更多偏好设置：投资期限、流动性需求、心理承受能力

#### 3.4.5 核心逻辑

```python
# services/user_memory_service.py

def build_context_for_conversation(conversation_id, query):
    """为对话构建上下文（用户记忆 + 最近决策 + 持仓）"""
    context = {}
    
    # 1. 用户长期记忆（风险偏好、投资目标等）
    memories = get_user_memory()
    for m in memories:
        context[m.memory_type] = json.loads(m.content)
    
    # 2. 最近决策记录（最近3条）
    recent_decisions = list_recent_decisions(limit=3)
    context['recent_decisions'] = recent_decisions
    
    # 3. 当前持仓摘要
    portfolio_summary = build_portfolio_summary_line()
    context['portfolio'] = portfolio_summary
    
    # 4. 相关性过滤：基于查询关键词筛选记忆
    relevant_memories = _filter_relevant_memories(memories, query)
    context['relevant_memories'] = relevant_memories
    
    return context


def _filter_relevant_memories(memories, query):
    """基于查询关键词筛选相关记忆"""
    import jieba
    keywords = set(jieba.lcut(query))
    relevant = []
    
    for m in memories:
        content = m.content
        content_keywords = set(jieba.lcut(content))
        overlap = keywords & content_keywords
        
        if overlap:
            score = len(overlap) / len(keywords)
            m.relevance_score = score
            relevant.append(m)
    
    return sorted(relevant, key=lambda x: x.relevance_score, reverse=True)[:5]
```

---

### 模块5：家庭财务深度整合（P2）

#### 3.5.1 业务需求

**问题**: 家庭财务数据较为静态，缺少动态规划和现金流预测能力。

**目标**: 增加现金流预测、资产配置建议、风险压力测试。

#### 3.5.2 API 设计

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/finance/cash-flow-forecast` | GET | 现金流预测 |
| `/api/finance/allocation-suggestion` | GET | 基于目标的配置建议 |
| `/api/finance/stress-test` | POST | 风险压力测试 |
| `/api/finance/goals` | GET | 财务目标进度追踪 |

#### 3.5.3 前端设计

**FamilyFinanceDashboard.vue 增强**：
- 现金流预测图表：未来12个月收支预测
- 资产配置建议卡片：根据目标自动推荐
- 风险压力测试模块：模拟市场下跌、利率变化影响

#### 3.5.4 核心逻辑

```python
# services/finance_planner.py

def forecast_cash_flow(months=12):
    """预测未来现金流"""
    # 历史收支数据
    historical_income = get_historical_income()
    historical_expense = get_historical_expense()
    
    # 持仓预期收益
    portfolio_yield = estimate_portfolio_yield()
    
    # 固定支出（房贷、车贷、生活费）
    fixed_expenses = calculate_fixed_expenses()
    
    forecast = []
    for month in range(months):
        date = datetime.now() + timedelta(days=30 * month)
        forecast.append({
            'month': date.strftime('%Y-%m'),
            'income': historical_income + portfolio_yield / 12,
            'expense': fixed_expenses,
            'net_cash_flow': (historical_income + portfolio_yield / 12) - fixed_expenses,
            'balance': None,  # 累积计算
        })
    
    # 计算累积余额
    balance = get_current_cash_balance()
    for f in forecast:
        balance += f['net_cash_flow']
        f['balance'] = balance
    
    return forecast


def stress_test(scenarios=None):
    """风险压力测试"""
    scenarios = scenarios or [
        {'name': '市场下跌20%', 'equity_drop': 0.2, 'bond_drop': 0.03},
        {'name': '市场下跌30%', 'equity_drop': 0.3, 'bond_drop': 0.05},
        {'name': '利率上升1%', 'equity_drop': 0.1, 'bond_drop': 0.08},
    ]
    
    results = []
    portfolio = get_portfolio_summary()
    
    for scenario in scenarios:
        impact = {
            'scenario': scenario['name'],
            'equity_impact': portfolio.equity_value * scenario['equity_drop'],
            'bond_impact': portfolio.bond_value * scenario['bond_drop'],
            'total_impact': (portfolio.equity_value * scenario['equity_drop']) + 
                           (portfolio.bond_value * scenario['bond_drop']),
            'new_total': portfolio.total_value - 
                         (portfolio.equity_value * scenario['equity_drop']) - 
                         (portfolio.bond_value * scenario['bond_drop']),
            'impact_pct': -((portfolio.equity_value * scenario['equity_drop'] + 
                           portfolio.bond_value * scenario['bond_drop']) / 
                          portfolio.total_value * 100),
        }
        results.append(impact)
    
    return results
```

---

### 模块6：移动端体验优化（P2）

#### 3.6.1 业务需求

**问题**: 移动端已有基本功能，但交互细节可优化。

**目标**: 提升移动端操作效率和用户体验。

#### 3.6.2 设计要点

**手势操作**：
- 持仓卡片左右滑动：快速操作（加仓/减仓/删除）
- 对话消息左滑：复制/转发/收藏

**快捷操作栏**：
- ChatView 底部增加快捷按钮组：查估值、看持仓、发指令
- 支持自定义快捷按钮顺序

**离线数据**：
- 关键数据（持仓、估值）本地缓存
- 支持离线浏览，联网后同步

**通知优化**：
- 支持推送通知（通过 WebSocket/SSE）
- 提醒类型：估值越限、事件验证、策略触发

#### 3.6.3 前端修改

**MobileApp.vue 增强**：
- 底部快捷操作栏组件
- 手势操作支持（使用 touch 事件）

**ChatView.vue 增强**：
- 移动端专用快捷按钮
- 消息卡片滑动操作

---

## 四、实施计划

### 时间线

| 阶段 | 时间 | 内容 |
|------|------|------|
| Phase 1 | 第1周 | 交易计划生成 + 一键执行链路 |
| Phase 2 | 第2周 | 策略沙盒增强 + 策略监控 |
| Phase 3 | 第3周 | 资金桶与持仓联动 + AI多轮记忆 |
| Phase 4 | 第4周 | 家庭财务深度整合 + 移动端优化 |

### 依赖关系

```
交易计划生成 ──────────────┐
    │                      │
    ▼                      │
策略监控 ──────────────┐    │
    │                  │    │
    ▼                  │    │
资金桶联动 ──────┐     │    │
    │            │     │    │
    ▼            ▼     ▼    ▼
AI记忆 ────────────► 综合测试
```

---

## 五、风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 交易计划金额计算不准确 | 导致用户损失 | 增加人工确认环节，默认使用保守参数 |
| 策略监控误触发 | 导致不必要交易 | 增加阈值校验，支持暂停/手动确认 |
| 资金桶自动分配错误 | 影响资产配置 | 首次分配需人工确认，提供手动调整 |
| 记忆系统数据污染 | 影响AI决策 | 定期清理低质量记忆，支持人工删除 |

---

## 六、验证方案

### 功能验证

1. **交易计划生成**: 创建建议→生成计划→确认计划→查看计划详情
2. **策略监控**: 创建策略→开启监控→触发条件验证→查看交易记录
3. **资金桶联动**: 创建桶→同步持仓→查看调拨建议→执行调拨
4. **AI记忆**: 修改偏好→新对话验证记忆生效→查看记忆引用
5. **移动端优化**: 手势操作测试→快捷栏操作→离线浏览测试

### 性能验证

| 指标 | 目标 | 验证方法 |
|------|------|---------|
| 交易计划生成耗时 | < 1s | 接口响应时间测量 |
| 策略监控检查频率 | 每5分钟 | 日志验证 |
| AI对话响应时间 | < 30s | 对话计时 |

---

## 七、后续扩展

1. **外部接口集成**：对接券商 API，实现真正的一键下单
2. **社交功能**：投资组合分享、策略社区
3. **智能客服**：7x24 小时 AI 客服，处理常见问题
4. **多账户支持**：支持管理多个投资账户

---

**文档结束**
