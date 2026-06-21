# 理财决策闭环增强 — 设计稿

> 创建：2026-06-21
> 状态：待实施
> 优先级：P0 先行，P1 跟进

---

## 一、问题诊断

四个模块各自独立，数据不流通：

| 模块 | 页面 | 数据库 | 真实数据 | 问题 |
|------|------|--------|----------|------|
| 财务总览 | FamilyFinanceDashboard.vue | portfolio_holdings (20条) | 有持仓 | 只展示快照，无趋势 |
| 资金桶 | GoalBucketsPage.vue | goal_buckets (0条) | 空表 | 从未使用，不参与决策 |
| 配置偏离 | AllocationDashboard.vue | rebalance_config (2条) | 有配置 | 建议无法落地为行动 |
| 策略沙盒 | StrategySandboxPage.vue | 无持久化表 | 无 | 回测结果丢弃 |
| 决策档案 | Dashboard内嵌组件 | decision_records (10条) | 10条全proposed | 无独立页面，无执行复盘 |

核心矛盾：**功能写好了但没串起来，用不起来。**

---

## 二、目标

以决策档案为枢纽，把四个模块串成闭环：

```
财务总览（看全局）
    ↓
资金桶（定约束）
    ↓
配置偏离（找问题）
    ↓
策略沙盒（验证方案）
    ↓
决策档案（记行动）
    ↓
复盘（反馈结果）
    ↓
回到财务总览（看变化）
```

---

## 三、导航重构

### 3.1 当前导航（navigation.js）

```
每日看板
财务总览
市场热点
AI 对话
文章管理
估值数据
估值图片
持仓管理
资金桶
配置偏离
策略沙盒
知识库（分组）
债券分析（分组）
Agent 管理
Token 用量
系统配置
进化系统（分组）
```

问题：财务总览、资金桶、配置偏离、策略沙盒散落在不同位置，没有工作流感。

### 3.2 新导航结构

```
每日看板 (dashboard)              [hot]
市场热点 (market-intelligence)
AI 对话 (chat)
───────────────────────────────────
理财决策 (group-decision)          [新增分组]
├── 财务总览 (family-finance)
├── 资金桶 (goal-buckets)
├── 配置偏离 (allocation-dashboard)
├── 策略沙盒 (strategy-sandbox)
└── 决策档案 (decisions)           [新增页面]
───────────────────────────────────
持仓管理 (portfolio)               [hot]
估值数据 (valuation)               [hot]
估值图片 (gallery)
文章管理 (articles)
知识库 (group-knowledge)
债券分析 (group-bond)
Agent 管理 (admin-agents)
Token 用量 (token-usage)
系统配置 (system-config)
进化系统 (group-evolution)
```

### 3.3 改动文件

**`frontend/src/navigation.js`**

```js
export const navItems = [
  { key: 'dashboard', label: '每日看板', icon: 'dashboard', hot: true },
  { key: 'market-intelligence', label: '市场热点', icon: 'fire' },
  { key: 'chat', label: 'AI 对话', icon: 'chat' },
  {
    key: 'group-decision',
    label: '理财决策',
    icon: 'decision',
    children: [
      { key: 'family-finance', label: '财务总览', icon: 'wallet' },
      { key: 'goal-buckets', label: '资金桶', icon: 'bucket' },
      { key: 'allocation-dashboard', label: '配置偏离', icon: 'pie-chart' },
      { key: 'strategy-sandbox', label: '策略沙盒', icon: 'bar-chart' },
      { key: 'decisions', label: '决策档案', icon: 'clipboard', hot: true },
    ],
  },
  { key: 'portfolio', label: '持仓管理', icon: 'portfolio', hot: true },
  { key: 'valuation', label: '估值数据', icon: 'valuation', hot: true },
  { key: 'gallery', label: '估值图片', icon: 'gallery' },
  { key: 'articles', label: '文章管理', icon: 'articles' },
  {
    key: 'group-knowledge',
    label: '知识库',
    icon: 'author',
    children: [
      { key: 'author', label: '作者文章', icon: 'author' },
      { key: 'linked', label: '个人文档', icon: 'link' },
      { key: 'knowledge', label: '蒸馏知识', icon: 'book' },
      { key: 'rag-test', label: '命中测试', icon: 'rag' },
      { key: 'rag', label: 'RAG 分析', icon: 'rag' },
    ],
  },
  {
    key: 'group-bond',
    label: '债券分析',
    icon: 'bond',
    children: [
      { key: 'bond', label: '债市市场温度', icon: 'bond' },
    ],
  },
  { key: 'admin-agents', label: 'Agent 管理', icon: 'admin' },
  { key: 'token-usage', label: 'Token 用量', icon: 'token' },
  { key: 'system-config', label: '系统配置', icon: 'config' },
  {
    key: 'group-evolution',
    label: '进化系统',
    icon: 'evolution',
    children: [
      { key: 'quality-dashboard', label: '质量仪表盘', icon: 'chart' },
      { key: 'bad-cases', label: 'Bad Case', icon: 'bug' },
      { key: 'eval-suite', label: '评测集', icon: 'check' },
    ],
  },
]
```

**`frontend/src/pageRegistry.js`**

```js
export const pageComponentKeys = [
  'dashboard',
  'market-intelligence',
  'chat',
  'family-finance',
  'goal-buckets',
  'allocation-dashboard',
  'strategy-sandbox',
  'decisions',          // ← 新增
  'portfolio',
  'valuation',
  'gallery',
  'author',
  'linked',
  'rag',
  'rag-test',
  'bond',
  'admin-agents',
  'token-usage',
  'quality-dashboard',
  'bad-cases',
  'eval-suite',
  'system-config',
  'knowledge',
  'search',
]
```

---

## 四、决策档案页面（新增）

### 4.1 页面布局

```
┌─────────────────────────────────────────────────────────┐
│  决策档案                                                 │
│  记录每一次买卖决策，追踪执行和复盘                        │
├─────────────────────────────────────────────────────────┤
│  [本月统计]  提案 10 │ 已执行 3 │ 待复盘 2 │ 胜率 67%      │
├──────────────┬──────────────┬───────────────────────────┤
│  待执行 (3)   │  待复盘 (2)   │  已完成 (5)                │
│              │              │                           │
│ ┌──────────┐ │ ┌──────────┐ │ ┌──────────┐              │
│ │医药50    │ │ │消费红利  │ │ │中证白酒  │              │
│ │watch     │ │ │add       │ │ │rebalance │              │
│ │06-21     │ │ │06-15     │ │ │05-28     │              │
│ │[接受]    │ │ │[复盘]    │ │ │已复盘    │              │
│ └──────────┘ │ └──────────┘ │ └──────────┘              │
│ ┌──────────┐ │              │                           │
│ │零钱配置  │ │              │                           │
│ │add       │ │              │                           │
│ └──────────┘ │              │                           │
└──────────────┴──────────────┴───────────────────────────┘
```

### 4.2 决策卡片信息

每张决策卡片显示：

```
┌────────────────────────────┐
│ 📋 医药50 (中证医药50指数)   │
│ ────────────────────────── │
│ 操作：关注                  │
│ 类型：watch                 │
│ 提案时间：2026-06-21        │
│ 来源：AI对话                │
│ ────────────────────────── │
│ 理由：当前PE分位12%，处于   │
│ 极度低估区间...             │
│ ────────────────────────── │
│ ⚠️ 预检查：                 │
│  ✅ 资金桶：长期权益桶       │
│  ✅ 风险等级：匹配           │
│  ⚠️ 占比：加仓后接近上限     │
│ ────────────────────────── │
│ [接受]  [拒绝]  [暂缓]      │
└────────────────────────────┘
```

### 4.3 决策生命周期

```
proposed（提案）
   ├── [接受] → accepted
   │             ├── [执行] → executed
   │             │             ├── [复盘] → reviewed
   │             │             └── [过期] → expired
   │             └── [暂缓] → deferred
   ├── [拒绝] → rejected
   └── [过期] → expired
```

### 4.4 后端API（已存在，无需新增）

```
GET    /api/decisions              — 列出决策（可按status过滤）
GET    /api/decisions/reviews/due  — 待复盘列表
GET    /api/decisions/today        — 今日行动
POST   /api/decisions/from-chat    — 从对话创建决策
PATCH  /api/decisions/:id/status   — 更新状态
POST   /api/decisions/:id/review   — 提交复盘
PATCH  /api/decisions/:id/actions/:actionId — 更新行动状态
```

### 4.5 新增文件

**`frontend/src/components/DecisionRecordsPage.vue`**

核心结构：

```vue
<script setup>
import { ref, computed, onMounted } from 'vue'
import { listDecisions, updateDecisionStatus, submitDecisionReview } from '../api'

// 三栏数据
const pending = ref([])      // proposed + accepted
const reviewing = ref([])    // executed, 待复盘
const completed = ref([])    // reviewed + rejected + expired

// 统计
const stats = computed(() => ({
  proposed: pending.value.length,
  executed: reviewing.value.length,
  reviewed: completed.value.filter(d => d.status === 'reviewed').length,
  // 胜率 = 有利润的复盘 / 总复盘数
  winRate: computedWinRate(),
}))

// 操作
async function accept(id) { await updateDecisionStatus(id, { status: 'accepted' }) }
async function reject(id) { await updateDecisionStatus(id, { status: 'rejected' }) }
async function execute(id) { await updateDecisionStatus(id, { status: 'executed' }) }
async function review(id, payload) { await submitDecisionReview(id, payload) }
</script>

<template>
  <div class="decisions-page">
    <header>
      <h2>决策档案</h2>
      <p>记录每一次买卖决策，追踪执行和复盘</p>
    </header>

    <!-- 月度统计条 -->
    <section class="stats-strip">
      <div>提案 {{ stats.proposed }}</div>
      <div>已执行 {{ stats.executed }}</div>
      <div>待复盘 {{ stats.reviewing }}</div>
      <div>胜率 {{ stats.winRate }}%</div>
    </section>

    <!-- 三栏看板 -->
    <div class="kanban">
      <section class="column pending">
        <h3>待执行</h3>
        <DecisionCard
          v-for="d in pending"
          :key="d.id"
          :decision="d"
          @accept="accept"
          @reject="reject"
          @execute="execute"
        />
      </section>

      <section class="column reviewing">
        <h3>待复盘</h3>
        <DecisionCard
          v-for="d in reviewing"
          :key="d.id"
          :decision="d"
          @review="review"
        />
      </section>

      <section class="column completed">
        <h3>已完成</h3>
        <DecisionCard
          v-for="d in completed"
          :key="d.id"
          :decision="d"
          readonly
        />
      </section>
    </div>
  </div>
</template>
```

### 4.6 注册到 Home.vue

在 `Home.vue` 的 `pageComponents` 中添加：

```js
import DecisionRecordsPage from '../components/DecisionRecordsPage.vue'

const pageComponents = {
  // ... 已有
  decisions: DecisionRecordsPage,  // ← 新增
}
```

---

## 五、资金桶联动决策预检查

### 5.1 当前状态

- `goal_buckets` 表 0 条数据
- 后端 `build_decision_precheck` 函数已存在但无法生效（没有资金桶数据）
- 前端 GoalBucketsPage 只有 CRUD，没有联动

### 5.2 增强设计

**资金桶页面增强：**

```
┌──────────────────────────────────────────────────┐
│  目标账户 / 资金桶                                 │
├──────────────────────────────────────────────────┤
│  [汇总] 桶数 5 │ 已归集 ¥80,000 │ 目标 ¥200,000   │
│         备用金 ✅已覆盖                            │
├──────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────┐  │
│  │ 💰 家庭备用金                    [编辑][删] │  │
│  │ 类型：备用金桶                              │  │
│  │ ¥30,000 / ¥50,000  进度 60%                │  │
│  │ ▓▓▓▓▓▓░░░░░░░░░                            │  │
│  │ 风险：极低 │ 1天内可动用 │ 目标占比 25%      │  │
│  │ ⛔ 护栏：禁止买入高波动资产                   │  │
│  └────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────┐  │
│  │ 📈 长期权益桶                    [编辑][删] │  │
│  │ 类型：长期权益                              │  │
│  │ ¥40,000 / ¥100,000  进度 40%                │  │
│  │ ▓▓▓▓░░░░░░░░░░░░░░                         │  │
│  │ 风险：中高 │ 365天内可动用 │ 目标占比 50%    │  │
│  │ ✅ 护栏：允许高波动资产                      │  │
│  │ 📊 关联持仓：医药50 ¥15,000、白酒 ¥20,000   │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

**决策预检查流程：**

```
用户在AI对话中问："现在加仓医药50怎么样？"
       ↓
AI生成决策草案
       ↓
调用 build_decision_precheck(target="医药50", action="add")
       ↓
检查内容：
  1. 哪个资金桶可以用于加仓？（非备用金桶）
  2. 该桶当前余额是否足够？
  3. 加仓后该桶权益占比是否超限？
  4. 该桶风险等级是否匹配？
       ↓
返回预检查结果：
  ✅ 可使用「长期权益桶」
  ✅ 余额充足（¥40,000 可用）
  ⚠️ 加仓后权益占比将达 55%，接近上限 60%
       ↓
预检查结果展示在决策卡片上
```

### 5.3 数据初始化

需要先录入真实资金桶数据。建议初始化 5 个桶：

```sql
INSERT INTO goal_buckets (name, bucket_type, target_amount, current_amount, target_ratio, risk_level, liquidity_days, priority, notes)
VALUES
('家庭备用金', 'emergency', 50000, 30000, 0.25, 'very_low', 1, 1, '6个月家庭支出'),
('稳健增值', 'stable', 50000, 20000, 0.25, 'low', 90, 2, '1-2年资金，债基为主'),
('长期权益', 'long_term', 100000, 40000, 0.50, 'medium_high', 365, 3, '3年以上，指数基金+主动基金'),
('机会资金', 'opportunity', 20000, 5000, 0.10, 'high', 30, 4, '低估机会加仓专用'),
('学习试错', 'learning', 5000, 2000, 0.02, 'high', 30, 5, '小仓位实验新策略');
```

---

## 六、配置偏离 → 决策档案

### 6.1 当前状态

- AllocationDashboard 显示偏离行和建议
- 建议只是文字，无法转化为行动
- 已有 `rebalance_config` 2个版本

### 6.2 增强设计

在每条偏离建议旁加「创建决策」按钮：

```
┌──────────────────────────────────────────────────┐
│  ⚠️ 显著偏离                                      │
│  消费红利：当前 35% → 目标 20%  偏离 +15%         │
│  建议：减仓消费红利                                │
│                          [创建决策]  [忽略]       │
└──────────────────────────────────────────────────┘
```

点击「创建决策」后：

```
┌──────────────────────────────────────────────────┐
│  创建决策                                         │
│  ──────────────────────────────────────────────  │
│  操作类型：减仓                    [下拉选择]      │
│  标的：消费红利 (161725)           [自动填充]      │
│  理由：当前占比35%，超目标15%，估值偏高            │
│  预检查：                                         │
│    ✅ 减仓后释放资金可转入备用金桶                 │
│    ✅ 配置偏离将降至 5% 以内                      │
│  复盘周期：30天                    [默认30天]      │
│  ──────────────────────────────────────────────  │
│  [确认创建]                    [取消]             │
└──────────────────────────────────────────────────┘
```

### 6.3 实现方式

后端已有 `POST /api/decisions/from-chat`，新增一个通用创建接口：

**新增后端API：**

```python
# backend/routers/decisions.py 新增

class CreateDecisionRequest(BaseModel):
    decision_type: str  # add/reduce/watch/rebalance
    target_type: str = "fund"
    target_code: str = ""
    target_name: str = ""
    summary: str
    rationale: str = ""
    review_days: int = 30

@router.post("/api/decisions/create")
async def create_decision_api(req: CreateDecisionRequest):
    """直接创建决策（不通过对话）。"""
    precheck = build_decision_precheck(
        target_type=req.target_type,
        target_code=req.target_code,
        action=req.decision_type,
    )
    decision_id = create_decision_record(
        source_type="manual",
        decision_type=req.decision_type,
        target_type=req.target_type,
        target_code=req.target_code,
        target_name=req.target_name,
        summary=req.summary,
        rationale=req.rationale,
        precheck=precheck,
        review_days=req.review_days,
    )
    return {"id": decision_id, "precheck": precheck}
```

**前端 AllocationDashboard.vue 增加：**

```js
async function createDecisionFromSuggestion(suggestion) {
  const { data } = await createDecision({
    decision_type: suggestion.action,  // 'sell' → 'reduce'
    target_type: 'fund',
    target_code: suggestion.target_code,
    target_name: suggestion.target_name,
    summary: suggestion.title,
    rationale: suggestion.reason,
    review_days: 30,
  })
  showToast('决策已创建，可在决策档案中查看', 'success')
}
```

---

## 七、策略沙盒结果持久化

### 7.1 当前状态

- 回测结果只返回给前端，不保存
- 无法查看历史回测
- 无法对比多次回测

### 7.2 数据库设计

```sql
-- backend/db/ 新增 backtest_results.py

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',
    name TEXT NOT NULL,
    target_code TEXT NOT NULL,
    target_type TEXT NOT NULL,
    strategy TEXT NOT NULL,
    params_json TEXT NOT NULL,
    -- 关键指标
    initial_cash REAL,
    final_value REAL,
    total_return REAL,
    annual_return REAL,
    max_drawdown REAL,
    sharpe_ratio REAL,
    -- 净值曲线（JSON数组）
    nav_curve_json TEXT,
    -- 关联的决策ID（如果基于此回测创建了决策）
    decision_id INTEGER,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

### 7.3 后端API

```python
# backend/routers/strategy_sandbox.py 新增

class SaveBacktestRequest(BaseModel):
    name: str
    target_code: str
    target_type: str
    strategy: str
    params: dict
    result: dict
    notes: str = ""

@router.post("/api/strategy-sandbox/save")
async def save_backtest(req: SaveBacktestRequest):
    """保存回测结果。"""
    # ...

@router.get("/api/strategy-sandbox/history")
async def list_backtests(limit: int = 20):
    """列出历史回测。"""
    # ...

@router.delete("/api/strategy-sandbox/{backtest_id}")
async def delete_backtest(backtest_id: int):
    """删除回测记录。"""
    # ...

@router.post("/api/strategy-sandbox/compare")
async def compare_backtests(ids: list[int]):
    """对比多次回测结果。"""
    # ...
```

### 7.4 前端页面增强

StrategySandboxPage.vue 增加：

1. **回测结果区新增「保存」按钮**

```
┌──────────────────────────────────────────────────┐
│  回测结果                                         │
│  ──────────────────────────────────────────────  │
│  总收益：+45.2%  年化：+13.1%                     │
│  最大回撤：-12.3%  夏普：1.2                      │
│  ──────────────────────────────────────────────  │
│  [净值曲线图]                                     │
│  ──────────────────────────────────────────────  │
│  名称：[医药50估值加权定投______]                  │
│  备注：[低估多投策略3年回测______]                 │
│  [💾 保存回测]  [📋 创建定投决策]                  │
└──────────────────────────────────────────────────┘
```

2. **历史回测列表**

```
┌──────────────────────────────────────────────────┐
│  历史回测                          [刷新]         │
│  ──────────────────────────────────────────────  │
│  ☐ 医药50估值加权  +45.2%  06-21  [对比][删]     │
│  ☐ 白酒分位买卖    +32.1%  06-20  [对比][删]     │
│  ☐ 沪深300定投     +18.5%  06-18  [对比][删]     │
│  ──────────────────────────────────────────────  │
│  [选中2项对比]                                    │
└──────────────────────────────────────────────────┘
```

3. **对比视图**

```
┌──────────────────────────────────────────────────┐
│  策略对比                                         │
│  ──────────────────────────────────────────────  │
│  指标          估值加权定投   分位买卖    定投     │
│  总收益        +45.2%         +32.1%     +18.5%   │
│  年化          +13.1%         +9.3%      +5.4%    │
│  最大回撤      -12.3%         -18.5%     -8.2%    │
│  夏普          1.2            0.8        0.6      │
│  ──────────────────────────────────────────────  │
│  [净值曲线叠加图]                                 │
└──────────────────────────────────────────────────┘
```

---

## 八、财务总览趋势增强（P2）

### 8.1 净值趋势图

新增 `net_worth_snapshots` 表：

```sql
CREATE TABLE IF NOT EXISTS net_worth_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'default',
    snapshot_date TEXT NOT NULL,
    total_assets REAL,
    cash_balance REAL,
    holding_value REAL,
    total_cost REAL,
    float_pnl REAL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX idx_nw_snapshot_user_date ON net_worth_snapshots(user_id, snapshot_date);
```

后端定时任务（或每日看板首次加载时）写入快照。
前端用 LineChart 展示 30天/90天/1年 净值曲线。

### 8.2 健康度评分

综合评分卡片：

```
┌──────────────────────────┐
│  财务健康度               │
│  ──────────────────────  │
│        78 / 100          │
│  ┌────────────────────┐  │
│  │ 备用金覆盖    ✅ 90 │  │
│  │ 负债收入比    ✅ 95 │  │
│  │ 配置偏离      ⚠️ 65 │  │
│  │ 决策执行率    ⚠️ 60 │  │
│  └────────────────────┘  │
│  建议：执行待处理决策，   │
│  修复配置偏离             │
└──────────────────────────┘
```

---

## 九、实施计划

### P0 — 打通入口（1.5天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 导航分组重构 | navigation.js, pageRegistry.js | 0.5天 |
| 决策档案页面 | DecisionRecordsPage.vue (新建) | 1天 |
| Home.vue注册 | Home.vue | 0.1天 |
| API补充 | api/index.js 补充决策相关函数 | 0.2天 |

### P1 — 串联闭环（2.5天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 资金桶数据初始化 | SQL脚本 | 0.1天 |
| 资金桶关联持仓 | GoalBucketsPage.vue 增强 | 0.5天 |
| 配置偏离→决策 | AllocationDashboard.vue, decisions.py | 0.5天 |
| 策略沙盒持久化 | backtest_results.py (新建), strategy_sandbox.py | 1天 |
| 策略沙盒历史+对比 | StrategySandboxPage.vue 增强 | 0.5天 |

### P2 — 趋势与统计（2.5天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 净值快照表+定时任务 | net_worth_snapshots.py (新建) | 0.5天 |
| 净值趋势图 | FamilyFinanceDashboard.vue 增强 | 1天 |
| 健康度评分 | 后端聚合 + 前端卡片 | 0.5天 |
| 决策统计看板 | DecisionRecordsPage.vue 增强 | 0.5天 |

---

## 十、文件变更清单

### 新增文件

| 文件路径 | 说明 |
|----------|------|
| frontend/src/components/DecisionRecordsPage.vue | 决策档案看板页面 |
| backend/db/backtest_results.py | 回测结果数据层 |
| backend/db/net_worth_snapshots.py | 净值快照数据层（P2） |
| scripts/init_goal_buckets.sql | 资金桶初始化数据 |

### 修改文件

| 文件路径 | 改动 |
|----------|------|
| frontend/src/navigation.js | 导航分组建 |
| frontend/src/pageRegistry.js | 注册 decisions 页面 |
| frontend/src/views/Home.vue | 导入并注册 DecisionRecordsPage |
| frontend/src/api/index.js | 补充决策和回测相关API函数 |
| frontend/src/components/AllocationDashboard.vue | 偏离建议加创建决策按钮 |
| frontend/src/components/StrategySandboxPage.vue | 保存回测、历史列表、对比 |
| frontend/src/components/GoalBucketsPage.vue | 关联持仓显示 |
| frontend/src/components/FamilyFinanceDashboard.vue | 净值趋势图、健康度评分（P2） |
| backend/routers/decisions.py | 新增 /api/decisions/create |
| backend/routers/strategy_sandbox.py | 新增保存/历史/对比API |

---

## 附录：设计约束

1. **遵循现有UI规范** — 使用 `var(--color-*)` 设计token，支持双主题
2. **红涨绿跌** — 中国市场惯例
3. **移动端兼容** — 三栏看板在移动端折叠为Tab切换
4. **KeepAlive** — 决策档案页面需要缓存，切换不丢状态
5. **API风格** — 遵循现有 `/api/xxx` 路径风格，FastAPI router
