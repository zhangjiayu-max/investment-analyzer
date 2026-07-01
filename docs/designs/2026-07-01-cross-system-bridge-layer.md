# 跨系统桥接层（Cross-System Bridge Layer）设计稿

**日期**: 2026-07-01  
**版本**: v2.0  
**前置条件**: Portfolio Fact Layer 已落地（commit `d16970f` ~ `d664fad`）

---

## 1. 问题定义

### 1.1 现状（两大系统各自为政）

```
用户触发的分析分两大块，彼此不知情：

┌───────────────────────────────────────────────┐
│  系统 A：AI 对话（实时多 Agent 协同）           │
│                                               │
│  用户提问 → orchestrator 路由 → specialist     │
│  并行分析 → 交叉审阅 → arbitrator → 回答       │
│                                               │
│  特点：实时、交互、面向用户当下问题             │
└───────────────────────────────────────────────┘

┌───────────────────────────────────────────────┐
│  系统 B：独立分析功能（各自为政的智能体）       │
│                                               │
│  日报分析    单基金深度    持仓交易分析        │
│  全景诊断    分散度分析    调仓策略             │
│  热点分析    指数深度      债券推荐            │
│                                               │
│  特点：可复现、参数固定、面向特定场景            │
└───────────────────────────────────────────────┘
```

### 1.2 核心问题

**信息孤岛导致以下不良后果：**

| 场景 | 问题 | 用户感受 |
|------|------|---------|
| 日报说"债市偏贵"，对话却说"建议加债基" | 两大系统给出矛盾建议 | "你们到底谁是对的？" |
| 对话推荐了某基金，用户去跑个深度分析却"不推荐" | 同一件事结论相反 | "你们的系统是不是有 bug？" |
| 用户按日报建议操作了，3 天后和 AI 聊天，AI 完全不知情 | 系统没有记忆 | "你都不记得我说过什么" |
| 日报里有个"待观察"标的，对话里提了但日报不提 | 跨系统关联缺失 | 错失信息整合机会 |

### 1.3 设计目标

**核心原则：不强制统一，但必须互相感知。**

具体目标：

1. **互相知晓** — 系统 A 跑的时候知道系统 B 今天产出过什么结论，反之亦然
2. **互相引用** — 结论一致时引用对方加强可信度，不一致时解释分歧原因
3. **共用标尺** — 两大系统基于同一组事实数据做判断，分歧来自逻辑而非数据
4. **产生合力** — 系统 A 的洞察能沉淀到系统 B 的分析中，系统 B 的结论能丰富系统 A 的回答
5. **增强决策** — 用户最终得到的不是"谁对谁错"，而是"共识 + 分歧 + 权衡条件"

### 1.4 核心洞察

```
冲突不是 bug，是信息差。

冲突来源         |  隐藏的信息
─────────────────────────────────
时间差           │  日报是早上的数据，对话是现在的
视角差           │  日报看绝对估值，对话看趋势变化
粒度差           │  日报说债市整体，对话说某只具体基金
目标差           │  日报关注风险控制，对话关注收益机会
数据差           │  日报多用估值，对话还看资金流向

每一个冲突背后都藏着一条用户原本不知道的信息。
揭示冲突来源 = 教用户做决策。
```

---

## 2. 架构设计：三层桥接

```
                   用户
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           │
  ┌─────────┐ ┌─────────────┐  │
  │ AI对话层 │ │ 独立分析层  │  │
  │(多Agent)│ │(日报/深度/   │  │
  │         │ │ 调仓/全景等) │  │
  └────┬────┘ └──────┬──────┘  │
       │             │         │
       └──────┬──────┘         │
              │                │
     ┌────────▼────────┐       │
     │  三层桥接层     │       │
     │  (NEW)          │       │
     │                 │       │
     │  桥接A: 分析→对话───────┘
     │  桥接B: 对话→分析
     │  桥接C: 共同事实沉淀
     └────────┬────────┘
              │
              ▼
     ┌────────────────┐
     │  Portfolio     │
     │  Fact Layer    │  ← 已落地
     │  (共用标尺)    │
     └────────────────┘
```

---

## 3. 核心模块设计

### 模块 A：分析 → 对话注入（桥接 A）

**定位**：当用户和 AI 对话时，让 orchestrator 知道"今天/昨天跑过什么独立分析"。

**实现位置**：`backend/agent/orchestrator.py` → `enrich_query_with_article()` 同级，新增 `_inject_analysis_context()`

```python
def _inject_analysis_context(query: str, user_id: str = "default") -> tuple[str, str]:
    """
    在 orchestrator 编排 specialist 之前，
    拉取当天最近的独立分析结论，注入到 specialist 的上下文中。
    
    返回: (enhanced_query, injected_context)
    """
    from db.decisions import get_latest_analysis_conclusions
    
    conclusions = get_latest_analysis_conclusions(user_id, hours=24)
    if not conclusions:
        return query, ""
    
    # 只取前 5 条，按新鲜度排序
    context_lines = []
    for c in conclusions[:5]:
        analysis_type = c.get("analysis_type", "未知分析")
        target = c.get("target_subject", "")
        action = c.get("action", "")
        reasoning = c.get("reasoning", "")
        
        if target and action:
            context_lines.append(
                f"- {analysis_type}: 关于「{target}」→ {action}（理由: {reasoning}）"
            )
        elif reasoning:
            context_lines.append(f"- {analysis_type}: {reasoning}")
    
    if not context_lines:
        return query, ""
    
    injected_context = (
        "\n\n【今日独立分析结论参考】\n"
        + "\n".join(context_lines) + "\n"
        + "【说明】以上是今日已完成的独立分析结论，供你参考。\n"
        + "如果和你的判断一致，可以引用以加强说服力；\n"
        + "如果有分歧，请明确指出分歧来源及原因。\n"
    )
    
    enhanced_query = query + injected_context
    return enhanced_query, injected_context
```

**注入时机**：在 `build_clarification_prompt()` 之后、`specialists` 被调用之前。

**数据需求**：需要一个轻量的 `get_latest_analysis_conclusions()` 接口，从各分析路由的分析记录表中查询最近 24h 的记录。

```sql
-- 查询示例（聚合各分析路由的结论）
SELECT 'daily_report' as analysis_type, 
       '整体组合' as target_subject,
       'decrease_bond' as action,
       '债市温度75°, 建议降低债基占比' as reasoning,
       created_at
FROM analysis_history WHERE agent_type = 'daily_report' 
  AND created_at >= datetime('now', '-24 hours')
UNION ALL
SELECT 'deep_dive', target_fund, action, reasoning, created_at
FROM portfolio_analysis_records 
  WHERE created_at >= datetime('now', '-24 hours')
ORDER BY created_at DESC
LIMIT 5
```

**关键设计决策**：
- **不强制 specialist 采纳**，只让它"知道" — 避免强绑定导致对话质量下降
- **只取 5 条最新**，避免上下文过长冲淡用户问题
- **标注来源**，用户能看到"这是日报说的"还是"这是对话说的"

---

### 模块 B：对话 → 分析关联（桥接 B）

**定位**：独立分析生成完毕后，检查最近对话中有没有关联内容，如果有则纳入分析报告中。

**实现位置**：`backend/routers/analysis/daily_report.py` 及各分析路由末尾

```python
def _attach_chat_context(report: dict, hours: int = 48) -> dict:
    """
    分析报告生成完后，检查最近对话中有没有相关的结论，
    如果有，在 report 末尾加一个"对话关联参考"区域。
    """
    from db.conversations import get_related_orchestrator_decisions
    
    # 提取报告涉及的关键词
    topics = _extract_report_keywords(report)
    if not topics:
        return report
    
    decisions = get_related_orchestrator_decisions(
        keywords=topics,
        hours=hours,
        limit=3,
    )
    
    if not decisions:
        return report
    
    report["chat_context"] = {
        "title": "💬 AI对话相关内容",
        "note": "以下观点来自近期AI对话，供交叉参考：",
        "items": [
            {
                "time": d["created_at"],
                "content": d["summary"][:200],
            }
            for d in decisions
        ],
    }
    return report


def _extract_report_keywords(report: dict) -> list[str]:
    """从分析报告中提取关键词，用于匹配对话。"""
    keywords = set()
    
    # 基金名称/代码
    for fund in report.get("funds", []):
        if fund.get("fund_code"):
            keywords.add(fund["fund_code"])
        if fund.get("fund_name"):
            keywords.add(fund["fund_name"][:4])  # 取前4字匹配
    
    # 话题标签
    for tag in report.get("tags", []):
        keywords.add(tag)
    
    # 主要的分析结论摘要
    summary = report.get("summary", "")
    for kw in ["债市", "股市", "定投", "减仓", "加仓", "止盈", "调仓"]:
        if kw in summary:
            keywords.add(kw)
    
    return list(keywords)
```

**关键设计决策**：
- **报告末尾追加**，不修改报告主体，保持独立性
- **关键词匹配**，不依赖全文搜索，轻量快速
- **加时间戳**，用户能看到对话信息的新鲜度

---

### 模块 C：共同事实沉淀（桥接 C）

**定位**：两大系统都基于同一组客观数据做判断，分歧只能来自逻辑和视角不同。

**现状**：`portfolio_fact_layer.py` 已有 `build_portfolio_facts()`，但只覆盖持仓快照。需要扩展：

```python
# portfolio_fact_layer.py 增强
def build_portfolio_facts(extended: bool = False) -> dict:
    """
    组合事实快照（所有分析和对话共享）。
    
    Args:
        extended: 是否包含扩展事实（供AI对话层使用）
    
    Returns:
        {
            "snapshot": {  # 持仓快照
                "total_value": 328000,
                "bond_pct": 32.1,
                "equity_pct": 25.4,
                "cash_pct": 18.3,
                "overconcentrated": [...],
            },
            "valuations": {  # 估值标尺
                "sh300_pe_percentile": 42,
                "bond_temp": 75,
                "erp": 5.8,
            },
            "market_state": {  # 市场状态（NEW）
                "regime": "sideways",
                "breadth": {...},
                "sentiment": "fear",
            },
            "recent_decisions": {  # 近期决策记录（NEW）
                "last_3_days": [...],
                "pending_actions": [...],
            },
            "constraints": {
                "risk_tolerance": "balanced",
                "max_single_holding": 0.25,
            },
        }
    """
```

**新增字段说明**：

| 字段 | 用途 | 使用方 |
|------|------|--------|
| `market_state.regime` | 当前市场状态（bull/bear/sideways） | 两大系统 |
| `market_state.sentiment` | 市场情绪（greed/fear/neutral） | AI对话层 |
| `recent_decisions` | 近期已做出的决策 | 两大系统 |
| `recent_decisions.pending_actions` | 已记录但未执行的行动 | 独立分析层 |

**为什么情绪数据对对话层更重要**：

```
日报（独立分析）倾向：估值数据 → 相对稳定，小时级变化
对话（多Agent）需要：情绪数据 → 快速变化，回答"今天"的问题

共同事实层同时提供慢变数据（估值）和快变数据（情绪），
让两大系统在"事实"层面保持同步。
```

---

## 4. 数据模型

### 4.1 分析结论收集表（共享中间层）

```sql
CREATE TABLE analysis_conclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_system TEXT NOT NULL,         -- 'ai_dialogue' | 'independent_analysis'
    source_type TEXT NOT NULL,           -- 'daily_report' | 'deep_dive' | 'orchestrator' | 'diversification' ...
    source_id INTEGER,                   -- 源记录ID
    target_subject TEXT NOT NULL,        -- '014847' | '整体组合' | '债券型基金' ...
    action TEXT,                         -- 'buy' | 'sell' | 'hold' | 'increase' | 'decrease' | 'clear'
    summary TEXT NOT NULL,               -- 核心结论（≤100字）
    reasoning TEXT,                      -- 核心理由（≤200字）
    key_variables TEXT,                  -- JSON: 驱动该结论的核心变量（如["债市温度","利率趋势"]）
    data_basis TEXT,                     -- JSON: 所引用的数据源列表
    market_context_at_time TEXT,         -- JSON: 当时的市场状态快照（共公事实版本）
    confidence REAL DEFAULT 0.5,         -- 0-1
    urgent INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    expires_at TEXT DEFAULT (datetime('now','localtime','+24 hours'))
);

CREATE INDEX idx_conclusions_target ON analysis_conclusions(target_subject);
CREATE INDEX idx_conclusions_source ON analysis_conclusions(source_system, source_type);
CREATE INDEX idx_conclusions_time ON analysis_conclusions(created_at);
CREATE INDEX idx_conclusions_keywords ON analysis_conclusions(target_subject, created_at);
```

**核心设计点**：
- `key_variables` 字段 = 隐藏信息。记录"为什么是这个结论"背后的关键变量，后续冲突检测时可以**基于变量对比而非方向对比**
- `market_context_at_time` = 结论产生时的市场状态。后续分析可以判断"时间差是否导致结论不同"
- `source_system` = 区分来自哪个大系统——这是桥接层的数据基础

### 4.2 跨系统关联记录表

```sql
CREATE TABLE cross_system_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_conclusion_id INTEGER REFERENCES analysis_conclusions(id),
    target_conclusion_id INTEGER REFERENCES analysis_conclusions(id),
    relationship TEXT NOT NULL,           -- 'confirms' | 'contradicts' | 'extends' | 'provides_context'
    reason TEXT,                         -- 关系说明
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
```

---

## 5. 决策画布（前端输出）

两大系统互相感知后，最终呈现给用户的不再是一个"答案"，而是一张**决策画布**：

```
┌─────────────────────────────────────────────────────┐
│  📋 决策画布                  2026-07-01 23:20       │
│                                                     │
│  ── 共识区（多方验证，可信度高） ──                  │
│  ✅ 博时恒乐集中度32% > 25% → 确实需要分散          │
│     来源：日报分析 ✓  全景诊断 ✓  AI对话 ✓          │
│                                                     │
│  ── 关注区（存在差异，需权衡） ──                    │
│  ⚠️ 现在是否应该减仓债基？                           │
│                                                     │
│  【日报分析说】减仓（债市温度75°，偏贵区间）        │
│  【AI对话说】谨慎持有（利率下行趋势，提前下车可能    │
│              踏空，建议设置条件单）                  │
│                                                     │
│  关键变量：债市温度 vs 利率趋势                      │
│  → 如果你信温度：减仓                                 │
│  → 如果你信趋势：持有，设止损线                      │
│                                                     │
│  ── 建议区（带条件的行动清单） ──                      │
│  🟢 优先级1: 基金替换                                │
│     博时恒乐 → XX纯债指数基金                        │
│     理由：相同风险敞口但费率低1.2%/年                │
│     置信度：90%  |  建议时间：随时                    │
│                                                     │
│  🟡 优先级2: 减仓                                     │
│     如果债市温度突破80°，减至总仓位20%                │
│     置信度：75%  |  建议时间：触发执行                │
│                                                     │
│  ── 学习区（一次决策，一套框架） ──                    │
│  📖 今天学到的：                                      │
│  "估值高位不一定是卖出点，趋势向下才是。              │
│   下次遇到类似情况，先问自己：驱动市场的核心变量      │
│   是估值还是趋势？"                                   │
└─────────────────────────────────────────────────────┘
```

**四个区域的设计意图：**

| 区域 | 作用 | 对应桥接 |
|------|------|---------|
| 共识区 | 建立信任 — "看，大家的判断是一致的" | 桥接A+B |
| 关注区 | 展示权衡 — "存在分歧，但分歧是有原因的" | 桥接A+B |
| 建议区 | 可执行 — "在共识基础上给出带条件的具体行动" | 桥接C |
| 学习区 | 长期成长 — "这次决策教会你什么框架" | 桥接C |

---

## 6. 实现计划

### Phase 1：数据基础设施（1天）

| 任务 | 产出 |
|------|------|
| 创建 `analysis_conclusions` 表 + DDL | DB schema |
| 创建 `get_latest_analysis_conclusions()` 查询函数 | DB 查询 |
| 扩展 `portfolio_fact_layer.py`（增加市场状态、近期决策） | 升级版事实层 |

### Phase 2：桥接 A — 分析→对话注入（半天）

| 任务 | 产出 |
|------|------|
| 实现 `_inject_analysis_context()` | 对话注入函数 |
| 在 orchestrator 流程中接入 | orchestrator 增强 |
| 修改 `build_clarification_prompt()` 注入渠道 | 需求路由增强 |

### Phase 3：桥接 B — 对话→分析关联（半天）

| 任务 | 产出 |
|------|------|
| 实现 `_attach_chat_context()` | 分析关联函数 |
| 在日报/深度/全景/分散度/调仓 等路由接入 | 各分析路由末尾 +3 行 |
| 实现 `_extract_report_keywords()` | 关键词提取 |

### Phase 4：前端决策画布（1天）

| 任务 | 产出 |
|------|------|
| 设计共识区/关注区/建议区/学习区组件 | `DecisionCanvas.vue` |
| 集成到 Dashboard 和 PortfolioManagement | 前端改动 |
| 决策画布与已有分析页面的联动 | 交互设计 |

### Phase 5：学习区内容生成（后续迭代）

| 任务 | 产出 |
|------|------|
| 每次决策画布生成时，LLM 总结一条"今天学到的框架" | 学习区内容 |
| 累积到"投资框架库"，用户可回顾 | 学习日志 |

---

## 7. 成功指标

| 指标 | 当前 | 目标 |
|------|------|------|
| AI对话是否知晓今日独立分析 | ❌ 完全不知 | ✅ 自动注入前5条 |
| 独立分析是否引用对话结论 | ❌ 从不 | ✅ 匹配关键词后显示关联 |
| 两大系统共用的客观数据 | 仅持仓快照 | ✅ 估值+情绪+状态 |
| 用户看到冲突时是否知道原因 | ❌ "谁对谁错" | ✅ "分歧来自XX变量" |
| 决策是否带条件 | ❌ 无条件建议 | ✅ "如果XX则YY" |
| 用户每次决策是否学到东西 | ❌ 没有 | ✅ 学习区一句话总结 |

---

## 8. 与 v1.0 设计稿的对比

| 维度 | v1.0（决策合成层） | v2.0（跨系统桥接层） |
|------|-------------------|---------------------|
| 问题假设 | 冲突是问题，需要消歧 | 冲突是信息差，需要揭示 |
| 核心机制 | 冲突检测→置信度排名→LLM合成 | 互相注入→揭示分歧→条件化建议 |
| 输出形式 | 一个统一答案 | 决策画布（共识+分歧+条件） |
| 用户角色 | 被动接受建议 | 主动理解权衡 |
| 长期价值 | 每次赚一个建议 | 每次学一套框架 |
| LLM依赖 | 合成器1次调用（单点脆弱） | 无新增LLM调用（纯规则注入+匹配） |
| 降级方案 | LLM不可用退化为规则列表 | 无新增依赖，自然降级 |

**核心转变**：从"决策合成"到"决策增强"。不替用户做决策，而是让用户拥有做出更好决策的能力。