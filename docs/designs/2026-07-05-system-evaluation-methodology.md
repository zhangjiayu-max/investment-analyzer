# 系统设计评估方法论与工具链 — 让理财决策更准

**日期**: 2026-07-05
**版本**: v1.0
**目标**: 建立评估体系，系统性地发现设计不合理和可增强空间

---

## 一、评估框架总览

```
┌─────────────────────────────────────────────────────────────┐
│                    评估金字塔                                │
│                                                             │
│         ┌──────────────────────────┐                        │
│         │   投资决策准不准          │ ← 最终目标             │
│         │   (建议 vs 实际收益)      │                        │
│         └────────────┬─────────────┘                        │
│                      │                                      │
│         ┌────────────▼─────────────┐                        │
│         │   AI 分析质量好不好       │ ← 中间环节             │
│         │   (LLM-as-Judge + 回测)  │                        │
│         └────────────┬─────────────┘                        │
│                      │                                      │
│         ┌────────────▼─────────────┐                        │
│         │   数据质量过得去吗        │ ← 基础                 │
│         │   (时效性/完整性/一致性)  │                        │
│         └────────────┬─────────────┘                        │
│                      │                                      │
│         ┌────────────▼─────────────┐                        │
│         │   代码架构合理吗          │ ← 地基                 │
│         │   (耦合/测试/性能)        │                        │
│         └──────────────────────────┘                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 二、各层评估工具与方法

### 2.1 代码架构层 — 地基是否扎实

#### 已有工具

| 工具 | 状态 | 用途 |
|------|------|------|
| pytest | ✅ 已安装 | 16 个测试文件，~104 个测试函数 |
| coverage | ✅ 已安装 | 代码覆盖率 |

#### 缺失工具

| 工具 | 安装 | 功能 | 为什么重要 |
|------|------|------|-----------|
| **pylint** | `pip install pylint` | 代码规范、错误检测 | 发现未使用的变量、参数、潜在 bug |
| **mypy** | `pip install mypy` | 类型检查 | 200+ 文件无类型标注，调用链容易传错参数 |
| **radon** | `pip install radon` | 圈复杂度分析 | 发现哪些函数太复杂需要拆分 |
| **pytest-cov** | ✅ 已有 | 测试覆盖率报告 | 知道哪些代码没被测试覆盖 |

#### 具体评估方法

**方法 1：热力图分析 — 发现"大泥球"**

```bash
# 找到最复杂的文件（按行数）
find backend -name "*.py" -exec wc -l {} + | sort -rn | head -10

# 结果：
# 4419  orchestrator.py    ← 太大了，应该拆
# 2911  db/portfolio.py    ← 太大了
# 2570  services/rag.py    ← 大
# 2347  db/decisions.py    ← 大
```

**评估标准**：
- 单文件 > 2000 行 → 建议拆分（风险区域）
- 单文件 > 3000 行 → 必须拆分（高风险区域）
- 单函数 > 100 行 → 建议拆分

**当前高风险区域**：

| 文件 | 行数 | 风险 | 建议 |
|------|------|------|------|
| `orchestrator.py` | 4419 | 🔴 | 拆分编排逻辑+状态管理+工具调用 |
| `db/portfolio.py` | 2911 | 🔴 | 拆分持仓/交易/估值查询 |
| `services/rag.py` | 2570 | 🟡 | 拆分检索/索引/评分 |
| `db/decisions.py` | 2347 | 🟡 | 拆分决策/评审/回测 |
| `tools/__init__.py` | 2193 | 🟡 | 拆分工具定义/执行/校验 |

---

**方法 2：依赖关系分析 — 发现循环依赖**

```bash
# 安装依赖分析工具
pip install pydeps

# 生成依赖图
pydeps backend --max-bacon 2 --show-deps
```

**问题**：循环依赖会导致：
- 代码难理解（A→B→A，到底谁依赖谁？）
- 测试难写（要 mock 整个链）
- 启动变慢（Python 需要解析所有模块）

**常见循环依赖模式**：

```
orchestrator.py → multi_agent.py → db/agents.py → orchestrator.py
                ↘ db/portfolio.py → tools/__init__.py → ...
```

**评估方法**：

```bash
# 检测循环依赖（简单版）
find backend -name "*.py" -exec grep -l "import.*orchestrator" {} \;
# 看哪些模块反向依赖了 orchestrator
```

---

**方法 3：圈复杂度分析 — 发现"太聪明的函数"**

```bash
pip install radon
radon cc backend/agent/orchestrator.py -s -n C
```

**评估标准**：
- A（1-5）：简单函数
- B（6-10）：适中
- C（11-20）：复杂，建议拆分
- D（21-30）：太复杂，必须拆分
- E（31-50）：极复杂，重写考虑
- F（51+）：不可维护

**作用**：复杂度高的函数 = bug 概率高 = 测试不充分 = 出问题难排查

---

### 2.2 数据质量层 — 基础是否扎实

#### 问题

AI 分析的好坏，90% 取决于数据质量。代码写得再好，喂进去的是垃圾数据，出来的也是垃圾分析。

#### 评估维度

| 维度 | 评估方法 | 工具 |
|------|---------|------|
| **时效性** | 行情数据是几秒前的？还是几小时前的？ | `db/valuations.py` 的 freshness 检查 |
| **完整性** | 持仓数据完整吗？有没有缺失的字段？ | `data_gate.py` 的完整性校验 |
| **一致性** | 估值数据和持仓数据冲突吗？ | `conversation_context.py` 的冲突检测 |
| **准确性** | 数据来源可靠吗？ | 数据源对比（akshare vs 手动输入） |

#### 具体评估方法

**数据时效性检查**：

```python
# 每天跑一次的数据新鲜度报告
def data_freshness_report() -> dict:
    """
    检查各数据源的最新更新时间。
    
    返回：
    {
        "holdings": {"latest": "2026-07-05 10:00", "age_hours": 0.5, "status": "ok"},
        "valuations": {"latest": "2026-07-04 16:00", "age_hours": 18, "status": "stale"},
        "market_prices": {"latest": "2026-07-05 09:30", "age_hours": 1.5, "status": "ok"},
        "bond_data": {"latest": "2026-07-04", "age_hours": 24, "status": "stale"},
    }
    """
```

**数据缺口分析**：

```python
def data_gap_analysis(conn) -> list[dict]:
    """
    发现数据缺口。
    
    返回：
    [
        {"table": "portfolio_holdings", "field": "fund_category", "null_rate": 0.35,
         "impact": "分散度分析不准确"},
        {"table": "valuation_history", "latest_date": "2026-06-30",
         "gap_days": 5, "impact": "估值趋势分析不可靠"},
    ]
    """
```

**数据一致性检查**：

```python
def data_consistency_check(conn) -> list[dict]:
    """
    检查跨表数据一致性。
    
    例：持仓总市值 vs 各基金市值之和，应该一致。
    例：交易记录中的基金代码 vs 持仓表中的基金代码，应该一致。
    """
```

---

### 2.3 AI 分析质量层 — 中间环节

#### 已有体系

| 能力 | 位置 | 评估 |
|------|------|------|
| LLM-as-Judge 评分 | `eval_system.py` | ✅ 有，但只评最终输出 |
| Shadow Mode 对比 | `shadow_mode.py` | ✅ 有 |
| A/B 测试 | `ab_testing.py` | ✅ 有 |
| 标准化测试套件 | `eval_driven_prompt_iteration.py` | ✅ 新增 |
| Bad Case 管理 | `BadCasePage.vue` | ✅ 有 |
| 回归测试 | `regression.py` | ✅ 有 |

#### 缺失

**1. 建议→实际结果对比（最重要的缺失）**

当前系统评的是"AI 觉得好不好"，不是"投资结果好不好"。

```python
def suggestion_vs_outcome_analysis(days_back=30) -> dict:
    """
    对比 AI 建议和实际结果。
    
    方法：对每个"买入/卖出/持有"建议，
    对比建议后 N 天的实际走势。
    
    返回：
    {
        "total_suggestions": 120,
        "adopted": 42,          # 用户采纳了多少
        "correct": 28,          # 采纳中方向正确的
        "accuracy": 0.67,       # 采纳准确率
        "avg_roi": 3.2,         # 采纳后的平均收益
        "missed_opportunities": [  # 建议了但用户没采纳，结果涨了
            {"date": "2026-06-15", "suggestion": "买入沪深300", "gain": 8.5},
        ],
        "bad_suggestions": [    # 建议了、用户采纳了、结果亏了
            {"date": "2026-06-10", "suggestion": "加仓白酒", "loss": -5.2},
        ],
    }
    """
```

**这是让理财更准最核心的指标**。如果建议准确率只有 50%，系统再花哨也没用。

**2. 根因分析自动化**

当前 Bad Case 需要人工分析。可以自动分析：

```python
def auto_root_cause_analysis(bad_case: dict) -> dict:
    """
    自动分析 Bad Case 的根因。
    
    排查链路：
    1. 用户 query 有问题吗？→ query_rewriter 是否改错了？
    2. 数据有问题吗？→ 数据时效性/完整性检查
    3. Agent 选错了吗？→ router 是否正确？
    4. Agent 推理错了吗？→ 推理链回放
    5. 仲裁错了吗？→ 仲裁逻辑检查
    """
```

---

### 2.4 投资决策准确率层 — 最终目标

#### 方法 1：回测（Backtesting）

**已有**：`strategy_sandbox.py` 支持 5 种策略回测

**缺失**：AI 建议的回测（不是策略回测，是建议回测）

```python
def backtest_ai_suggestions(days=90) -> dict:
    """
    回测 AI 的历史建议。
    
    对过去 90 天里 AI 给出的每个具体建议，
    模拟"如果按建议执行"的结果。
    
    场景：
    - 2026-06-01 AI 说"减仓债基" → 如果减了，收益变化？
    - 2026-06-15 AI 说"加仓沪深300" → 如果加了，收益变化？
    - 2026-06-20 AI 说"持有不动" → 如果没动，收益变化？
    
    返回：
    {
        "period": "2026-04-05 ~ 2026-07-05",
        "total_suggestions": 180,
        "backtest_roi": 8.5,        # 按建议操作的总收益
        "actual_roi": 5.2,          # 用户实际操作的收益
        "gap": 3.3,                 # 差距（建议更好还是实际更好）
        "best_suggestion": {...},
        "worst_suggestion": {...},
        "accuracy_by_type": {
            "buy": 0.65,            # 买入建议准确率
            "sell": 0.55,           # 卖出建议准确率
            "hold": 0.72,           # 持有建议准确率
        },
        "accuracy_by_agent": {
            "valuation_expert": 0.68,
            "risk_assessor": 0.62,
            "allocation_advisor": 0.71,
        },
    }
    """
```

**这个功能是让理财更准的核心**——不是看 AI 说得好不好，是看 AI 说得对不对。

#### 方法 2：归因分析

```python
def attribution_analysis(days=90) -> dict:
    """
    收益归因分析。
    
    当前收益，有多少来自：
    - AI 建议的操作
    - 用户自己的操作
    - 市场自然上涨
    - 运气
    
    如果能区分"哪些操作是 AI 建议的"和"哪些是用户自己做的"，
    就能知道 AI 到底贡献了多少价值。
    """
```

---

## 三、推荐工具清单

### 3.1 安装与命令

```bash
# 代码质量工具
pip install pylint mypy radon pydeps vulture

# 测试工具
pip install pytest-cov pytest-mock

# 数据质量
pip install great-expectations  # 数据质量断言框架（可选，较重）
```

### 3.2 运行命令

```bash
# 1. 圈复杂度分析（发现复杂函数）
radon cc backend -s -n C | head -30

# 2. 死代码检测（发现未使用的代码）
vulture backend --min-confidence 80

# 3. 类型检查（发现参数类型错误）
mypy backend --ignore-missing-imports

# 4. 测试覆盖率（发现未测试的代码）
pytest --cov=backend backend/tests/

# 5. 依赖图（发现循环依赖）
pydeps backend --max-bacon 2 --show-deps --output deps.png

# 6. 代码规范检查
pylint backend --max-line-length=120
```

### 3.3 评估报告模板

```python
# 生成每周评估报告
def weekly_health_report() -> dict:
    """
    生成系统健康周报。
    
    包含：
    1. 代码质量（复杂度、测试覆盖率、lint 错误数）
    2. 数据质量（时效性、完整性、一致性）
    3. AI 质量（建议准确率、Bad Case 数、评分趋势）
    4. 投资效果（回测 ROI、归因分析）
    5. 成本（LLM 费用趋势、预算使用率）
    """
```

---

## 四、最推荐先做的 3 个评估

### 1️⃣ 建议 vs 实际结果对比（最重要的）

**为什么**：系统存在的意义是让理财更准。如果建议准确率 ≤50%，需要先诊断再优化。

**怎么做**：
```python
# 利用已有的 decision_records 和 portfolio_transactions 表
# 对比"AI 建议了什么"和"用户实际做了什么"
# 统计建议方向 vs 实际走势的一致性
```

**工作量**：~200 行，纯数据查询，零 LLM 成本

### 2️⃣ 代码复杂度热力图

**为什么**：4419 行的 orchestrator.py 出 bug 的概率是 500 行文件的 8 倍。

**怎么做**：
```bash
radon cc backend -s -n C
# 找出所有复杂度 C 级以上的函数
# 优先拆分最复杂的
```

**工作量**：0 行（工具安装 + 分析），根据结果决定拆分方案

### 3️⃣ 数据缺口分析

**为什么**：AI 分析不准，大概率不是 AI 的问题，是数据的问题。

**怎么做**：
```python
# 检查每个数据表的字段完整性和时效性
# 找出"数据缺失导致 AI 分析受限"的场景
```

**工作量**：~100 行，纯 SQL 查询

---

## 五、总结

### 评估矩阵

```
评估项              工具/方法          成本      价值    紧急度
────────────────────────────────────────────────────────────
建议 vs 实际结果    自定义查询          ~200行   ⭐⭐⭐⭐⭐  P0
代码复杂度热力图     radon              0行     ⭐⭐⭐⭐   P0
数据缺口分析        自定义SQL          ~100行   ⭐⭐⭐⭐   P0
测试覆盖率          pytest-cov         0行     ⭐⭐⭐    P1
依赖关系分析         pydeps             0行     ⭐⭐⭐    P1
类型检查            mypy               0行     ⭐⭐⭐    P1
圈复杂度分析         radon              0行     ⭐⭐⭐    P1
AI 建议回测        自定义查询          ~300行   ⭐⭐⭐⭐⭐  P1
收益归因分析        自定义查询          ~200行   ⭐⭐⭐⭐   P2
死代码检测          vulture            0行     ⭐⭐     P2
```

### 最关键的一句话

**系统现在有 16 个测试文件、Shadow Mode、A/B 测试、LLM-as-Judge 评分——这些评估的是"AI 产出质量"。但缺少最重要的一个：建议→实际结果对比。没有这个，你不知道系统到底有没有帮你赚钱。**