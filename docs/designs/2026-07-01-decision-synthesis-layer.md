# 决策合成层（Decision Synthesis Layer）设计稿

**日期**: 2026-07-01  
**版本**: v1.0  
**前置条件**: Portfolio Fact Layer 已落地（commit `d16970f` ~ `d664fad`）

---

## 1. 问题定义

### 1.1 现状

```
用户触发分析
    ├─ 深度分析(agent_4) → "建议清仓A转投B"
    ├─ 分散度分析(agent_2) → "B占比过高，要降"
    ├─ 每日简报(agent_1)   → "债市偏贵，别加债基"
    ├─ AI对话(orchestrator) → 10个specialist各说各话 → arbitrator缝合
    └─ 用户: 自己读、自己对、自己判断
```

**核心问题**：系统给了信息，没给决策。

### 1.2 目标

用户一次触发 → 一个经过多方论证的统一决策建议。

类比：不是给用户一堆体检报告让他自己看，而是出一份由多位医生会诊后签字的诊断书。

---

## 2. 架构设计

### 2.1 三层架构

```
┌─────────────────────────────────────────────┐
│              决策合成层 (NEW)                │
│  冲突检测 → 置信度加权 → 优先级排序 → 可执行建议  │
├─────────────────────────────────────────────┤
│              组合事实层 (DONE)               │
│  snapshot + constraints + market + recent    │
├─────────────────────────────────────────────┤
│              分析执行层 (DONE)               │
│  9 route agents + 10 orchestrator specialists │
└─────────────────────────────────────────────┘
```

### 2.2 核心模块

#### 模块A: 分析结论收集器 `decision_collector.py`

**功能**: 在任意分析完成后，收集并结构化存储其结论。

```python
def collect_analysis_conclusion(
    analysis_type: str,      # "deep_dive" | "diversification" | "daily_report" | ...
    target_subject: str,     # "博时恒乐债券A" | "整体组合" | ...
    conclusion: {
        "action": str,       # "buy" | "sell" | "hold" | "increase" | "decrease" | "clear"
        "target": str,       # 操作对象（基金代码/类型）
        "confidence": float, # 0-1，数据驱动程度
        "reasoning": str,    # 核心理由（≤100字）
        "risks": [str],      # 主要风险
        "urgent": bool,      # 是否建议立即执行
    },
    source_agent: str,       # "agent_4" | "orchestrator" | ...
)
```

**存储**: `analysis_conclusions` 表，每条结论有有效期（默认24h）

#### 模块B: 冲突检测引擎 `conflict_detector.py`

**功能**: 检测同一分析周期内相互矛盾的结论。

**规则引擎**（零LLM调用）:
```
冲突类型:
1. 方向冲突: A结论说"加仓X"，B结论说"减仓X"
2. 目标冲突: A结论说"转投X"，B结论说"X已超配"
3. 逻辑冲突: A结论基于"债市便宜"，B结论基于"债市贵"
   （需检测底层事实是否一致）

检测算法:
- 同一target/subject的所有结论分组
- 方向相反的标记为"硬冲突"
- 背景事实不一致的标记为"软冲突"
```

**输出**: 冲突标注列表，附双方的具体理由。

#### 模块C: 置信度评估器 `confidence_ranker.py`

**功能**: 给每个分析结论打分，用于决策排序。

**评分维度**:

| 维度 | 权重 | 评分标准 |
|------|------|---------|
| 数据驱动程度 | 30% | 全真实数据=10，含LLM推理=7，含编造=0 |
| 分析深度 | 25% | 多维度论证=10，单一角度=5，一句话=2 |
| 时间新鲜度 | 15% | <1h=10，<6h=7，<24h=5，>24h=3 |
| 历史准确率 | 20% | 从evaluation记录中统计该agent历史评分 |
| 可执行性 | 10% | 有具体操作+价位=10，有方向无价位=5，只描述=2 |

**输出**: 每个结论的置信度分数（0-100）

#### 模块D: 决策合成器 `decision_synthesizer.py`

**功能**: 将碎片化结论合成为统一决策建议。

**输入**:
- 所有近期`analysis_conclusions`
- `portfolio_facts`（组合约束）
- `conflict_detector` 输出
- `confidence_ranker` 输出

**LLM Prompt 模板**（仅此处调用一次LLM）:

```
你是用户的专属理财决策顾问。以下是你团队中多位分析师的独立结论，
你的任务是综合这些结论，输出一份统一的决策建议。

## 组合约束（最高优先级）
{portfolio_facts}

## 各分析师结论（按置信度排名）
{ranked_conclusions}

## 冲突标注
{conflicts}

## 任务
1. 如果存在冲突，解释冲突来源并给出你的倾向及理由
2. 按优先级排序所有可执行建议（最多3条）
3. 每条建议必须具体、可操作（含方向+目标+模糊价位参考）
4. 标注每条建议的不确定性

## 输出格式
{
  "summary": "一句话核心决策",
  "conflicts_resolved": [...],
  "actionable_advice": [
    {
      "priority": 1,
      "action": "减仓",
      "target": "014846 博时恒乐债券A",
      "from_pct": 32.0,
      "to_pct_range": "20-25%",
      "reason": "集中度过高 + 债市偏贵",
      "confidence": 85,
      "urgency": "本周",
      "alternative_targets": ["XX纯债基金(估值低、费用低)", "货币基金(短期避险)"],
      "risks": ["减仓时机: 当前债市仍在上涨趋势中，可能踏空短期收益"],
      "source_analyses": ["分散度分析", "每日简报", "全景诊断"]
    }
  ],
  "watchlist": [...],
  "uncertainty_notes": [...]
}
```

### 2.3 数据流

```
用户触发任意分析
       │
       ▼
  分析执行（现有流程）
       │
       ├──→ 分析结论自动存储到 analysis_conclusions 表
       │
       ▼
  用户请求"综合决策" 或 定时触发
       │
       ▼
  ① collect: 从 analysis_conclusions 拉取24h内结论
  ② detect: 冲突检测引擎 → 标注矛盾
  ③ rank: 置信度评估器 → 排序
  ④ synthesize: LLM合成 → 统一决策建议
       │
       ▼
  展示: 前端决策面板 / 推送通知
```

---

## 3. 实现计划

### Phase 1: 结论收集（P0，~3h）

| 任务 | 产出 |
|------|------|
| 创建 `analysis_conclusions` 表 | DB schema |
| 创建 `backend/decision/collector.py` | `collect_analysis_conclusion()` |
| 修改9个分析路由 → 分析完成后自动调用 collector | 每个路由 +3行 |
| 修改 orchestrator → arbitrator 完成后自动调用 collector | orchestrator.py +5行 |
| 修改 conversations.py 流式/非流式路径 | conversations.py +3行 |

**验收标准**: 触发任意分析后，`analysis_conclusions` 表有新记录。

### Phase 2: 冲突检测 + 置信度（P0，~2h）

| 任务 | 产出 |
|------|------|
| 创建 `backend/decision/conflict_detector.py` | 规则引擎 |
| 创建 `backend/decision/confidence_ranker.py` | 评分算法 |
| 单元测试 | 至少5个测试用例 |

**验收标准**: 同时运行分散度分析+深度分析后，能检测到"博时恒乐"的冲突。

### Phase 3: 决策合成（P1，~3h）

| 任务 | 产出 |
|------|------|
| 创建 `backend/decision/synthesizer.py` | LLM合成逻辑 |
| 创建路由 `POST /api/decision/synthesis` | API端点 |
| 创建 `backend/db/decisions.py` | `save_decision_synthesis()` |
| 前端决策面板 | 新组件 `DecisionPanel.vue` |

**验收标准**: 调用API后返回结构化JSON，包含优先级建议和冲突解释。

### Phase 4: 自动化（P2，~1h）

| 任务 | 产出 |
|------|------|
| 每日定时合成（跟随日报cron） | cron更新 |
| 前端已有分析页面嵌入"综合决策"按钮 | 前端改动 |
| 分析触发后自动提示"查看综合决策" | 前端改动 |

---

## 4. 前端交互设计

### 4.1 决策面板（DecisionPanel.vue）

位置：Dashboard 顶部或 PortfolioManagement 侧边栏

```
┌─────────────────────────────────────────┐
│  📋 综合决策建议          2026-07-01 23:20 │
│                                         │
│  「债基集中度过高，建议适度分散」          │
│                                         │
│  ⚠️ 检测到1处分析冲突，已标注 →           │
│                                         │
│  🔴 优先级1: 减仓博时恒乐                 │
│     从60%降至20-25%                      │
│     理由: 集中度+债市温度                 │
│     置信度: 85%  建议时间: 本周内          │
│     [执行] [忽略] [稍后]                  │
│                                         │
│  🟡 优先级2: 清仓华泰保兴安悦债券C         │
│     理由: 持续亏损+卫星配置                │
│     置信度: 70%  建议时间: 本周内          │
│     [执行] [忽略] [稍后]                  │
│                                         │
│  📌 待观察: 医疗指数(低估)、消费红利(低估) │
└─────────────────────────────────────────┘
```

### 4.2 冲突标注样式

当两个分析对同一标的给出相反建议时：
- 决策面板中该建议旁显示"⚡ 存在不同意见"
- 点击展开 → 显示冲突双方的理由
- 说明决策合成层为什么倾向某一方

---

## 5. 成功指标

| 指标 | 目标 |
|------|------|
| 分析结论收集率 | 100%（每次分析完成后自动存储） |
| 冲突检测准确率 | >95%（规则引擎，不依赖LLM） |
| 合成建议置信度 | >70%（基于数据驱动程度） |
| 用户决策效率 | 从"看5个页面自己做判断" → "看1个面板做判断" |

---

## 6. 数据库变更

```sql
CREATE TABLE analysis_conclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_type TEXT NOT NULL,       -- 'deep_dive', 'diversification', 'daily_report', 'orchestrator' ...
    target_subject TEXT NOT NULL,      -- '014847', '整体组合', '债券型基金' ...
    action TEXT,                       -- 'buy', 'sell', 'hold', 'increase', 'decrease', 'clear'
    target_fund TEXT,                  -- 操作目标基金代码
    confidence REAL DEFAULT 0.5,       -- 0-1
    reasoning TEXT,                    -- 核心理由 ≤200字
    risks TEXT,                        -- JSON array
    urgent INTEGER DEFAULT 0,          -- 是否紧急
    source_agent TEXT,                 -- 'agent_4', 'orchestrator:arbitrator' ...
    source_record_id INTEGER,          -- analysis_history.id 或 conversation_id
    metadata TEXT,                     -- JSON: 完整原始分析
    created_at TEXT DEFAULT (datetime('now','localtime')),
    expires_at TEXT DEFAULT (datetime('now','localtime','+24 hours'))
);

CREATE INDEX idx_conclusions_target ON analysis_conclusions(target_subject);
CREATE INDEX idx_conclusions_time ON analysis_conclusions(created_at);

CREATE TABLE decision_syntheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT,                      -- 一句话核心决策
    actionable_advice TEXT,            -- JSON array
    conflicts_resolved TEXT,           -- JSON array
    watchlist TEXT,                    -- JSON array
    source_conclusion_ids TEXT,        -- JSON array of analysis_conclusions.id
    confidence_score REAL,             -- 综合置信度
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

---

## 7. 风险与注意事项

1. **LLM合成质量**: 只用1次LLM调用（决策合成），结论收集+冲突检测+置信度全用规则引擎，降低幻觉和成本
2. **时效性**: 结论有效期24h，过期自动忽略
3. **降级**: LLM不可用时，退化为规则引擎直接输出（按置信度排序的原始结论列表）
4. **不要变成另一个分析**: 决策合成层不做新分析，只做"综合现有结论"。不做加法，做减法
