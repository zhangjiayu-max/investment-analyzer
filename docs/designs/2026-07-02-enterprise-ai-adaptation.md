# 企业级 AI 技术适配设计稿

**日期**: 2026-07-02
**版本**: v1.0
**目标**: 从企业级 AI 面试真题中筛选适用的成熟技术，适配到个人投资分析系统

---

## 1. 背景

### 1.1 来源

本设计稿中所有技术均来自企业级 AI 岗位面试真题（`reports/AI企业级面试真题汇总.md`），涵盖：
- LLM 推理优化、模型训练、RAG 系统设计
- Multi-Agent 系统、MLOps、Prompt Engineering
- 场景设计题中的生产级系统架构

### 1.2 筛选原则

```
┌─────────────────────────────────────────────┐
│  不适用（直接跳过）                           │
│  ├── 模型训练/微调（我们只用 API）             │
│  ├── 推理部署优化（我们不用自部署模型）         │
│  ├── 量化/分布式训练（不相关）                  │
│  └── 大规模系统设计 >10W DAU（超出范围）       │
│                                              │
│  适用（借鉴思想，轻量落地）                     │
│  ├── Multi-Agent 架构优化                      │
│  ├── RAG 系统增强                              │
│  ├── 评估与监控体系                            │
│  ├── Prompt 工程方法论                         │
│  ├── 缓存与成本控制                            │
│  └── 数据质量与防御性设计                       │
└─────────────────────────────────────────────┘
```

### 1.3 与现有系统的关系

```
现有系统
├── orchestrator + multi_agent.py     ← 目标：MoE 路由优化
├── rag.py + 文档解析                   ← 目标：Query Rewriting + Step-back
├── validator.py + eval_system.py       ← 目标：幻觉分层防御 + 统计检验
├── ab_testing.py + ab_test_report.json ← 目标：显著性检验 + 监控告警
├── agent/cache.py                      ← 目标：Pipeline Caching 增强
├── circuit_breaker.py                  ← 目标：链接到成本治理
└── 跨系统桥接层（已设计）              ← 评估结果反馈入口

改造原则：不重构，只增强。每个模块在原文件上升级。
```

---

## 2. 技术升级一：MoE 智能路由优化（Agent Routing）

### 2.1 问题

**企业级做法**（题目 5：MoE 架构）：

```
MoE 用 Router 为每个 token 选 top-k 个专家，
并解决负载不均（某些专家被过度调用，某些闲置）。
```

**我们现状**：`agent/router.py` 有 `_classify_complexity_by_rules()`，但路由逻辑固定，没有动态调整。

```python
# 当前路由（简化）：
complexity_strategy = {
    "high":   {"agents": ALL_AGENTS, "parallel": True, "cross_review": True},
    "medium": {"agents": [3, 4],     "parallel": True, "cross_review": False},
    "simple": {"agents": [0],        "parallel": False,"cross_review": False},
}
```

问题：medium 复杂度永远调 agent_3 + agent_4。如果 agent_3 本周准确率只有 30%，应该降低优先级。

### 2.2 方案：MoE 风格动态路由

**核心思想**：借鉴 MoE 的三个概念——专家容量、共享专家、动态调度，但不用训练，只用规则。

#### 2.2.1 专家容量（Expert Capacity）

```python
# 每个 Agent 的日调用量上限
AGENT_CAPACITY = {
    0:  {"cap": 50,  "used_today": 0, "reset": "daily"},   # quick_analyst
    1:  {"cap": 30,  "used_today": 0, "reset": "daily"},   # daily_brief
    2:  {"cap": 30,  "used_today": 0, "reset": "daily"},   # diversification
    3:  {"cap": 25,  "used_today": 0, "reset": "daily"},   # deep_dive
    4:  {"cap": 20,  "used_today": 0, "reset": "daily"},   # asset_allocation
    5:  {"cap": 20,  "used_today": 0, "reset": "daily"},   # risk_controller
    6:  {"cap": 15,  "used_today": 0, "reset": "daily"},   # macro_economist
    7:  {"cap": 10,  "used_today": 0, "reset": "daily"},   # bond_specialist
    8:  {"cap": 10,  "used_today": 0, "reset": "daily"},   # fund_picker
    9:  {"cap": 10,  "used_today": 0, "reset": "daily"},   # tax_optimizer
}
```

超出容量的 Agent 自动跳过，由后备 Agent 替补。

#### 2.2.2 共享专家（Shared Expert）

```python
# 所有 query，无论复杂度，都过 quick_analyst
# 保证基本回答质量 + 提前提取关键信息给其他 Agent
SHARED_AGENTS = [0]  # quick_analyst

def route_with_moe(query: str, complexity: str) -> dict:
    moe_plan = {"shared": SHARED_AGENTS, "specialists": [], "fallback": []}
    
    # Step 1: 共享专家必定参与
    moe_plan["shared"] = [a for a in SHARED_AGENTS 
                          if AGENT_CAPACITY[a]["used_today"] < AGENT_CAPACITY[a]["cap"]]
    
    # Step 2: 根据历史准确率排序候选人
    candidates = _get_candidate_agents(complexity)
    candidates = _sort_by_accuracy(candidates)  # 历史准确率高的优先
    
    # Step 3: 选择未超出容量的 top-k
    selected = []
    for agent_id in candidates:
        if AGENT_CAPACITY[agent_id]["used_today"] < AGENT_CAPACITY[agent_id]["cap"]:
            selected.append(agent_id)
            if len(selected) >= NEEDED_COUNT[complexity]:
                break
    
    moe_plan["specialists"] = selected
    return moe_plan
```

#### 2.2.3 动态准确率跟踪

```python
# 记录每个 Agent 的历史评分，用于排序
class AgentPerformanceTracker:
    """
    跟踪每个 Agent 的历史表现分数，
    用于 MoE 路由的排序依据。
    
    数据来源：eval_system.py 的 LLM-as-Judge 评分
    """
    def __init__(self, window: int = 50):
        self.window = window  # 滑动窗口，只看最近 50 次
        
    def record(self, agent_id: int, score: float):
        """记录一次评分结果。"""
        # scores = {agent_id: deque(maxlen=50)}
        self.scores[agent_id].append(score)
        
    def get_weighted_score(self, agent_id: int) -> float:
        """
        综合评分 = 平均分 × 数据量系数 × 新鲜度系数
        
        - 数据量系数：最近不足 5 次的数据 → 降权（样本太少不可信）
        - 新鲜度系数：最近 10 次的权重 > 前 40 次
        """
        scores = list(self.scores.get(agent_id, []))
        if not scores:
            return 0.7  # 默认 0.7（无数据时保守估计）
        
        n = len(scores)
        if n < 5:
            confidence = n / 5  # 数据不足，降权
        else:
            confidence = 1.0
        
        avg = sum(scores) / n
        return avg * confidence
```

### 2.3 改动点

| 文件 | 改动 | 行数 |
|------|------|------|
| `agent/router.py` | 新增 MoE 路由逻辑 | ~100 行 |
| `agent/orchestrator_optimizer.py` | 新增 AgentPerformanceTracker | ~60 行 |
| `agent/orchestrator.py` | 路由调用处接入新逻辑 | ~10 行 |

**效果**：
- 表现差的 Agent 自动降低被调用概率
- 热门 Agent 不会超出容量
- 共享专家保证基础质量

---

## 3. 技术升级二：查询改写引擎（Query Rewriting）

### 3.1 问题

**原文**（题目 12：RAG 多轮对话）：

```
用户第1轮："帮我查一下公司的报销政策"
用户第2轮："那差旅标准是多少？"
→ 如果只用"差旅标准"检索，可能找不到
→ 需要改写 query
```

**我们现状**：`orchestrator.py` 没有 query rewriting。用户问"那现在还能买吗？"，specialist 不知道"那"指什么。

### 3.2 方案：规则 + LLM 混合改写

**核心思想**：先用规则判断是否需要改写，再用 LLM（小模型）执行改写，避免不必要的 LLM 调用。

#### 3.2.1 改写触发器

```python
# agent/query_rewriter.py

import re

# 代词触发词
PRONOUN_PATTERNS = [
    r"\b那\b", r"\b这\b", r"\b它\b", r"\b它们\b",
    r"\b他\b", r"\b她\b", r"\b他们\b",
    r"\b这些\b", r"\b那些\b", r"\b该\b",
    r"\b其\b", r"\b刚才说的\b", r"\b上面\b",
    r"\b你的建议\b", r"\b那个\b",
]

# 上下文依赖模式
CONTEXT_PATTERNS = [
    # 没有明确指示词的简短问题
    r"^现在能买吗",
    r"^要卖吗",
    r"^怎么看",
    r"^多少仓位",
    r"^具体说说",
    r"^还有吗",
    r"^然后呢",
    r"^所以\b",
]

def needs_rewrite(query: str) -> tuple[bool, str]:
    """
    判断当前 query 是否依赖上下文。
    
    Returns:
        (True, "pronoun")  → 含代词
        (True, "short")    → 简短问题，缺乏上下文
        (False, "")        → 无需改写
    """
    for pat in PRONOUN_PATTERNS:
        if re.search(pat, query):
            return True, "pronoun"
    
    for pat in CONTEXT_PATTERNS:
        if re.search(pat, query.strip()):
            return True, "short"
    
    return False, ""
```

#### 3.2.2 改写执行

```python
def rewrite_query(query: str, history: list[dict], conn=None) -> str:
    """
    混合改写策略。
    
    低优先级（占 80% 情况）：规则改写
    高优先级（占 20% 情况）：LLM 改写
    
    Returns:
        改写后的 query（如果不需改写，返回原 query）
    """
    need, reason = needs_rewrite(query)
    if not need:
        return query
    
    need_llm, rewrite = _rewrite_by_rules(query, history, reason)
    if not need_llm:
        return rewrite
    
    return _rewrite_by_llm(query, history)


def _rewrite_by_rules(query: str, history: list[dict], reason: str) -> tuple[bool, str]:
    """
    规则改写策略：
    - 对于"那这个基金怎么样" → 替换"这个基金"为上一轮提到的基金名称
    - 对于"具体说说" → 保持原问题 + 附加上文主题
    """
    if not history or reason == "none":
        return True, query  # 没上下文，交给 LLM 写
    
    last_turn = history[-1]
    last_query = last_turn.get("query", "")
    last_answer = last_turn.get("answer", "")
    
    if reason == "pronoun":
        # 抽取上一轮的核心名词
        core_nouns = _extract_core_nouns(last_query + last_answer)
        if core_nouns:
            return False, f"{query}（基于{item['query']}）"
            # 简易方案：把代词替换为核心名词
            # 实际需要更精细 NER
            return False, query.replace("那", f"那个{core_nouns[0]}").replace("这", f"这个{core_nouns[0]}")
    
    if reason == "short":
        # 从上一轮提取主题
        topic = _extract_topic(last_query)
        if topic:
            return False, f"{query}（关于{topic}）"
    
    return True, query  # 规则搞不定，交给 LLM


def _extract_core_nouns(text: str) -> list[str]:
    """
    从文本中抽取核心名词。
    简单规则：寻找"基金代码"、"基金名称"、"XX指数"等模式。
    """
    nouns = []
    patterns = [
        r'\b\d{6}\b',           # 基金代码（6位数字）
        r'[\u4e00-\u9fa5]{2,6}(?:指数|基金)',  # 名称+指数/基金
        r'[\u4e00-\u9fa5]{2,6}(?:ETF|LOF)',     # ETF/LOF
    ]
    for pat in patterns:
        match = re.findall(pat, text)
        nouns.extend(match)
    return list(set(nouns))  # 去重


def _rewrite_by_llm(query: str, history: list[dict]) -> str:
    """
    LLM 改写（规则搞不定的复杂情况）。
    只对改写可信度 > 0.8 的进行替换，否则保留原 query。
    """
    prompt = f"""请将以下对话中的用户问题改写为完整的、不含代词的自包含问题。

历史对话（最近2轮）：
{_format_history(history)}

当前用户问题：{query}

要求：
1. 补充会话历史中已经提到的核心实体（基金名称、代码、策略等）
2. 将代词替换为具体名词
3. 保持原问题的意图不变
4. 只输出改写后的结果，不要解释

改写结果："""
    
    from agent.llm_interface import call_llm_mini  # 小模型，便宜
    rewritten = call_llm_mini(prompt, max_tokens=100, temperature=0)
    
    if rewritten and len(rewritten) < 200:  # 合法性检查
        return rewritten.strip()
    return query  # 改写失败，保底
```

#### 3.2.3 接入 orchestrator

```python
# 在 orchestrator.py 的入口处新增
from agent.query_rewriter import needs_rewrite, rewrite_query

def process_query(query: str, history: list[dict], user_id: str = "default"):
    """入口函数。"""
    # 改写
    rewritten = rewrite_query(query, history)
    if rewritten != query:
        logger.info(f"Query 改写: {query} → {rewritten}")
    
    # 原有流程，使用 rewritten 代替 query
    classification = classify_complexity(rewritten)
    ...
```

### 3.3 改动点

| 文件 | 改动 | 行数 |
|------|------|------|
| `agent/query_rewriter.py` | 新增模块 | ~150 行 |
| `agent/orchestrator.py` | 入口处接入改写 | ~10 行 |
| `agent/llm_interface.py` | 新增 `call_llm_mini()`（如果不存在） | ~20 行 |

**效果**：
- 80% 的代词/短问题用规则改写，零成本
- 20% 的复杂情况用 LLM 改写，成本 < 100 tokens/次
- 多轮对话的用户体验提升明显

---

## 4. 技术升级三：幻觉分层防御体系（Hallucination Defense）

### 4.1 问题

**原文**（题目 26：防止 LLM 幻觉）：

```
分层防幻觉策略：
1. 知识层（事前）→ RAG，要求引用
2. Prompt层（事中）→ 明确指令，约束输出
3. 验证层（事后）→ Self-Consistency，Fact Check
4. 监控层（持续）→ Bad case 回流
```

**我们现状**：
- 知识层：RAG ✅
- Prompt层：有，但不够结构化 ⚠️
- 验证层：`validator.py` 有规则检查 ✅
- 监控层：Bad case 回流不自动 ⬜

**金融场景的特殊性**：基金代码编造、历史净值编造、费率数据编造——这些幻觉会造成真实金钱损失。

### 4.2 方案：四层防御 + 金融专用检查

#### 4.2.1 强化的 Prompt 层（事中）

```python
# agent/prompt_defense.py

FORCED_DEFENSE_PROMPT = """
【事实约束 - 必须遵守】
1. 如果不知道具体数据（基金代码、净值、费率），说"无法获取"而非编造
2. 所有数据引用必须标注来源：估值数据/行情数据/持仓数据/KYC
3. 如果两个数据源有冲突，明确指出差异
4. 禁止给出具体买卖价格建议（只给比例、时窗）
5. 基金代码必须为6位数字，如果记不住就说记不住
6. 历史收益率只能引用"数据来源：Wind/天天基金"，不能自己算

【免责声明 - 每段回答末尾可选添加】
"以上分析基于历史数据，不构成投资建议。
市场有风险，投资需谨慎。"
"""

def attach_defense_prompt(system_prompt: str, analysis_type: str) -> str:
    """
    为不同类型分析附加对应的防御指令。
    
    Args:
        system_prompt: 原始 system prompt
        analysis_type: 'deep_dive' | 'daily_report' | 'diversification' | ...
    
    Returns:
        附加防御指令后的 system prompt
    """
    # 通用防御
    defense = FORCED_DEFENSE_PROMPT
    
    # 类型专用防御
    if analysis_type == "deep_dive":
        defense += "\n7. 基金业绩数据只引用最近3年，更早的数据标注'历史业绩不代表未来'"
    elif analysis_type == "daily_report":
        defense += "\n7. 市场评论基于当日收盘数据，标注发布时间"
    elif analysis_type == "diversification":
        defense += "\n7. 相关性数据标注计算区间"
    
    return system_prompt + "\n\n" + defense
```

#### 4.2.2 Self-Consistency 验证（事后）

```python
# agent/validator.py 增强

def verify_self_consistency(output: str, analysis_type: str,
                            conn=None, n_samples: int = 3) -> dict:
    """
    Self-Consistency 检查。
    对同一个问题采样 3 次，看关键结论是否一致。
    
    适用场景：买入/卖出/调仓等关键决策（非日常简报）
    """
    KEY_DECISION_TYPES = {"portfolio_trade", "asset_allocation", "deep_dive"}
    if analysis_type not in KEY_DECISION_TYPES:
        return {"consistent": True, "score": 1.0}  # 非关键场景跳过
    
    # 如果已经有 validator 缓存的结果，跳过
    from agent.cache import semantic_cache_get
    cached = semantic_cache_get(f"self_consistency:{output[:50]}")
    if cached:
        return cached
    
    # 从输出中提取关键结论
    key_claims = _extract_key_claims(output)
    if not key_claims:
        return {"consistent": True, "score": 1.0}
    
    # 对每个关键结论，多次采样
    from agent.llm_interface import call_llm
    agreement_scores = []
    
    for claim in key_claims[:3]:  # 最多检查 3 个关键结论
        verify_prompt = f"""判断以下陈述是否准确：
{claim['text']}
来源：{claim.get('context', '')}
请给出事实准确性打分（0-1）："""
        
        scores = []
        for _ in range(n_samples):
            result = call_llm(verify_prompt, max_tokens=10, temperature=0.5)
            try:
                scores.append(float(result.strip()))
            except (ValueError, TypeError):
                pass
        
        if scores:
            agreement_scores.append(sum(scores) / len(scores))
    
    if not agreement_scores:
        return {"consistent": True, "score": 1.0}
    
    # 综合评分
    final_score = sum(agreement_scores) / len(agreement_scores)
    result = {
        "consistent": final_score > 0.7,
        "score": final_score,
        "checked_claims": key_claims[:3],
        "samples": n_samples,
    }
    
    # 缓存 1 小时
    from agent.cache import semantic_cache_set
    semantic_cache_set(f"self_consistency:{output[:50]}", result, ttl=3600)
    
    return result


# 如果 Self-Consistency 得分低，触发重生成
def _extract_key_claims(output: str) -> list[dict]:
    """提取输出中的关键事实性结论。"""
    claims = []
    sentences = output.split('。')
    for sent in sentences:
        # 检查是否包含关键判断词
        if any(kw in sent for kw in ["建议", "推荐", "应该", "不推荐", "买入", "卖出", "增配", "减配"]):
            claims.append({
                "text": sent.strip(),
                "context": output[:100],
            })
    return claims
```

#### 4.2.3 金融专用数据校验

```python
def validate_financial_data(output: str) -> dict:
    """
    检查 output 中的金融数据是否合理。
    不依赖 LLM，纯规则。
    """
    issues = []
    
    # 基金代码检查
    fund_codes = re.findall(r'\b\d{6}\b', output)
    for code in fund_codes:
        if not _is_valid_fund_code(code):
            issues.append(f"疑似虚构基金代码: {code}")
    
    # 收益率范围检查
    returns = re.findall(r'(\d+\.?\d*)%', output)
    for ret in returns:
        val = float(ret)
        if val > 30 or val < -30:
            issues.append(f"收益率超出合理范围: {val}%")
    
    # 费率检查
    fees = re.findall(r'管理费[约]?(\d+\.?\d*)%', output)
    for fee in fees:
        val = float(fee)
        if val > 2.0 or val < 0.1:
            issues.append(f"管理费率不合理: {val}%")
    
    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "issue_count": len(issues),
    }
```

#### 4.2.4 Bad Case 自动回流

```python
# 在 eval_system.py 新增
def auto_capture_bad_case(output: str, validation_result: dict, 
                          self_consistency: dict) -> None:
    """
    自动将可疑案例加入 bad case 池。
    触发条件（任一个）：
    - Self-Consistency score < 0.7
    - 金融数据校验失败
    - 用户给负面反馈
    """
    is_bad = False
    reasons = []
    
    if self_consistency.get("score", 1.0) < 0.7:
        is_bad = True
        reasons.append(f"self_consistency={self_consistency['score']:.2f}")
    
    if not validation_result.get("passed", True):
        is_bad = True
        reasons.extend(validation_result.get("issues", []))
    
    if is_bad:
        from db.eval import add_bad_case
        add_bad_case({
            "output": output[:500],
            "reasons": reasons,
            "auto_captured": True,
            "created_at": datetime.now().isoformat(),
        })
        logger.warning(f"自动捕获 bad case: {reasons}")
```

### 4.3 改动点

| 文件 | 改动 | 行数 |
|------|------|------|
| `agent/prompt_defense.py` | 新增防御 prompt 模块 | ~80 行 |
| `agent/validator.py` | 新增 Self-Consistency + 金融校验 | ~120 行 |
| `eval_system.py` | 新增 bad case 自动回流 | ~30 行 |
| `agent/multi_agent.py` | synthetic system prompt 时附加防御 | ~5 行 |

**效果**：
- 基金代码编造 → 被规则检测 → 自动标记
- 幻觉 rate 预期降低 50%+
- Bad case 从"人工发现"变成"自动捕获"

---

## 5. 技术升级四：评测体系增强（Eval + Monitoring）

### 5.1 问题

**原文**（题目 9：模型评估 + 题目 23：在线监控 + 题目 24：Prompt 评估）：

```
评估维度：
├── 自动评测（LLM-as-Judge）
├── 人工评测
├── 业务指标
└── 统计检验（paired t-test / Wilcoxon）

监控告警：
├── 延迟 P99 > 阈值 → 告警
├── 错误率 > 1% → 告警
└── 用户负反馈突增 → 告警
```

**我们现状**：
- 自动评测 ✅ 已有 `eval_system.py`，LLM 打分
- 人工评测 ❌ 没有系统化
- 业务指标 ❌ 没有（采纳率、ROI、准确率）
- 统计检验 ❌ A/B 报告只对比平均分，不检验显著性

### 5.2 方案

#### 5.2.1 业务指标追踪

```python
# eval_system.py 新增

class BusinessMetrics:
    """
    跟踪实际业务效果指标。
    
    数据源：
    - 采纳率：分析建议 vs 用户实际持仓变化
    - ROI：被采纳的建议，执行后的 N 天收益率
    - 准确率：建议方向 vs 实际走势
    """
    
    def adoption_rate(self, days: int = 30) -> dict:
        """
        采纳率分析。
        
        方法：对比系统建议的操作和用户实际持仓操作。
        如果建议"减仓债基"且用户确实减仓了 → 采纳
        
        Returns:
            {
                "rate": 0.35,          # 总体采纳率
                "by_type": {           # 按分析类型
                    "daily_report": 0.28,
                    "deep_dive": 0.42,
                },
                "total_suggestions": 120,
                "adopted": 42,
            }
        """
    
    def roi_by_suggestion(self, days: int = 30) -> dict:
        """
        按建议分类的 ROI 分析。
        
        方法：对每个被采纳的建议，
        计算建议执行后 N 天的收益/亏损。
        
        Returns:
            {
                "avg_roi": 3.2,        # 平均收益率 %
                "positive_rate": 0.65,  # 正向收益占比
                "best_suggestion": {
                    "date": "2026-06-15",
                    "action": "increase_equity",
                    "roi": 8.5,
                },
                "worst_suggestion": {...},
            }
        """
    
    def accuracy(self, days: int = 30) -> dict:
        """
        建议方向准确率。
        
        方法：对每个"涨/跌"预测，和实际走势对比。
        
        Returns:
            {
                "overall": 0.58,       # 整体准确率
                "up_accuracy": 0.62,   # 看涨准确率
                "down_accuracy": 0.53, # 看跌准确率
            }
        """
```

#### 5.2.2 统计显著性检验

```python
# eval_system.py 新增

import math

def mann_whitney_u(scores_a: list[float], scores_b: list[float]):
    """
    Mann-Whitney U 检验（非参数检验）。
    
    适用场景：两个版本的评分分布对比。
    不需要正态分布假设。
    样本量 >= 10 即有初步统计效力。
    """
    if len(scores_a) < 3 or len(scores_b) < 3:
        return {"p_value": 1.0, "significant": False, 
                "reason": "样本不足 (需要各>=3)"}
    
    # 合并排序
    combined = [(s, 'a') for s in scores_a] + [(s, 'b') for s in scores_b]
    combined.sort(key=lambda x: x[0])
    
    # 计算 rank 和
    rank_a = sum(i + 1 for i, (s, g) in enumerate(combined) if g == 'a')
    n1, n2 = len(scores_a), len(scores_b)
    
    # U 统计量
    u1 = rank_a - (n1 * (n1 + 1)) / 2
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    
    # 正态近似（n >= 8 时适用）
    mu = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    
    z = (u - mu) / sigma
    p_value = 2 * (1 - _normal_cdf(abs(z)))  # 双尾
    
    mean_a = sum(scores_a) / n1
    mean_b = sum(scores_b) / n2
    
    return {
        "mean_a": round(mean_a, 3),
        "mean_b": round(mean_b, 3),
        "diff": round(mean_b - mean_a, 3),
        "u_statistic": u,
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "sample_sizes": {"a": n1, "b": n2},
        "conclusion": (
            "B 显著优于 A" if p_value < 0.05 and mean_b > mean_a else
            "A 显著优于 B" if p_value < 0.05 else
            "差异不显著，需要更多数据"
        ),
    }


def _normal_cdf(x: float) -> float:
    """标准正态分布 CDF 的近似计算。"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
```

#### 5.2.3 告警规则引擎

```python
# eval_system.py 新增

import json
from datetime import datetime, timedelta

class AlertEngine:
    """
    告警规则引擎。
    每天跑完 eval 后检查预设阈值。
    """
    
    def __init__(self, conn=None):
        self.rules = [
            {
                "name": "score_drop",
                "description": "质量评分同比昨日下降超过 0.5 分",
                "check": self._check_score_drop,
                "cooldown_hours": 24,
            },
            {
                "name": "hallucination_spike",
                "description": "幻觉率超过 15%",
                "check": self._check_hallucination_rate,
                "cooldown_hours": 12,
            },
            {
                "name": "negative_feedback_spike",
                "description": "用户负面反馈率超过 20%",
                "check": self._check_negative_feedback,
                "cooldown_hours": 12,
            },
            {
                "name": "latency_spike",
                "description": "平均响应延迟超过 15s",
                "check": self._check_latency,
                "cooldown_hours": 6,
            },
            {
                "name": "zero_confidence",
                "description": "置信度 < 0.3 的结论超过 10 条",
                "check": self._check_low_confidence,
                "cooldown_hours": 24,
            },
        ]
        self.last_alerts = {}  # rule_name: last_alert_time
    
    def run_checks(self, daily_stats: dict, prev_stats: dict = None) -> list[dict]:
        """执行所有告警检查。"""
        alerts = []
        now = datetime.now()
        
        for rule in self.rules:
            rule_name = rule["name"]
            last_time = self.last_alerts.get(rule_name)
            
            if last_time and (now - last_time).total_seconds() < rule["cooldown_hours"] * 3600:
                continue  # 冷却期未过，跳过
            
            result = rule["check"](daily_stats, prev_stats)
            if result:
                self.last_alerts[rule_name] = now
                alerts.append({
                    "rule": rule_name,
                    "description": rule["description"],
                    "detail": result,
                    "time": now.isoformat(),
                })
        
        return alerts
    
    def _check_score_drop(self, stats, prev):
        if not prev:
            return None
        current_avg = stats.get("avg_score", 0)
        prev_avg = prev.get("avg_score", 0)
        if prev_avg - current_avg > 0.5:
            return f"评分从 {prev_avg:.2f} 降至 {current_avg:.2f}"
        return None
    
    def _check_hallucination_rate(self, stats, prev):
        rate = stats.get("hallucination_rate", 0)
        if rate > 0.15:
            return f"幻觉率 {rate:.1%} > 阈值 15%"
        return None
    
    def _check_negative_feedback(self, stats, prev):
        rate = stats.get("negative_feedback_rate", 0)
        if rate > 0.20:
            return f"负面反馈率 {rate:.1%} > 阈值 20%"
        return None
    
    def _check_latency(self, stats, prev):
        latency = stats.get("avg_latency", 0)
        if latency > 15:
            return f"平均延迟 {latency:.1f}s > 阈值 15s"
        return None
    
    def _check_low_confidence(self, stats, prev):
        count = stats.get("low_confidence_count", 0)
        if count > 10:
            return f"低置信度结论 {count} 条 > 阈值 10"
        return None
```

### 5.3 改动点

| 文件 | 改动 | 行数 |
|------|------|------|
| `eval_system.py` | 新增 BusinessMetrics + Mann-WhitneyU + AlertEngine | ~250 行 |
| `db/eval.py` | 新增告警记录表 | ~30 行 |
| `ab_test_report.json` | 增加统计检验结果字段 | ~10 行 |

**效果**：
- A/B 测试不再靠"平均分高就换"
- 质量下降自动告警
- 能回答"我的系统最近准确率多少？"

---

## 6. 技术升级五：流水线缓存增强（Pipeline Caching）

### 6.1 问题

**企业级做法**（题目 4：KV Cache + 题目 19 不直接相关，但缓存思想通用）：

```
缓存是"不要重复造轮子"的工程实践。
相同的输入 → 相同的输出 → 不应该重复调用 LLM。
```

**我们现状**：`agent/cache.py` 有语义缓存，但只缓存 LLM 调用结果。

问题：
1. 同一天跑 3 次日报分析 → 每次重新调 LLM，虽然有缓存但命中率低
2. 中间结果不缓存（embedding、特征提取等）
3. 缓存失效策略简单

### 6.2 方案：三级缓存体系

#### 6.2.1 L1：接口级缓存

```python
# agent/cache.py 增强

"""
三级缓存体系：

L1（短期）：接口级缓存，TTL=5分钟
  key: f"{analysis_type}:{user_id}:{date}"
  用途：用户 5 分钟内多次请求同一个分析 → 直接从缓存拿
  
L2（中期）：语义缓存，TTL=24小时
  key: embedding_similarity(query, cached_queries) > 0.95
  用途：不同用户问了非常类似的问题 → 复用结果
  
L3（长期）：分析级缓存，TTL=7天
  key: f"report:{analysis_type}:{date}"
  用途：日报/周报这些固定输出 → 存到数据库，支持历史回顾
"""

from typing import Any
from datetime import datetime, timedelta
import hashlib
import json


class L1Cache:
    """接口级缓存（内存，进程内）。"""
    def __init__(self, ttl_seconds: int = 300):
        self.store = {}
        self.ttl = timedelta(seconds=ttl_seconds)
    
    def get(self, key: str) -> Any | None:
        if key in self.store:
            value, expiry = self.store[key]
            if datetime.now() < expiry:
                return value
            del self.store[key]
        return None
    
    def set(self, key: str, value: Any):
        self.store[key] = (value, datetime.now() + self.ttl)
    
    def invalidate(self, key_prefix: str):
        """按前缀批量失效（比如用户修改了持仓）。"""
        keys_to_del = [k for k in self.store if k.startswith(key_prefix)]
        for k in keys_to_del:
            del self.store[k]


class L2Cache:
    """语义缓存（用 embedding 相似度匹配）。"""
    
    def __init__(self, similarity_threshold: float = 0.95, max_items: int = 100):
        self.threshold = similarity_threshold
        self.items = []  # [(query_embedding, response, timestamp)]
        self.max_items = max_items
    
    def search(self, query_embedding: list[float]) -> tuple[str | None, float]:
        """
        在缓存中搜索相似 query。
        
        Returns:
            (response, similarity) 或 (None, 0)
        """
        from agent.vector import cosine_similarity
        
        best_sim = 0
        best_response = None
        
        for stored_emb, response, ts in self.items:
            sim = cosine_similarity(query_embedding, stored_emb)
            if sim > best_sim and sim > self.threshold:
                best_sim = sim
                best_response = response
        
        return best_response, best_sim
    
    def add(self, query_embedding: list[float], response: str):
        """添加新条目，超出上限时淘汰最旧的。"""
        if len(self.items) >= self.max_items:
            self.items.pop(0)  # FIFO
        self.items.append((query_embedding, response, datetime.now()))


class L3Cache:
    """分析级缓存（数据库持久化）。"""
    
    def __init__(self, conn=None):
        self.conn = conn
    
    def get_report(self, analysis_type: str, date_str: str) -> dict | None:
        """获取之前生成的分析报告。"""
        import sqlite3
        cur = self.conn.execute(
            "SELECT content FROM analysis_cache WHERE type=? AND date=?",
            (analysis_type, date_str)
        )
        row = cur.fetchone()
        if row:
            return json.loads(row[0])
        return None
    
    def save_report(self, analysis_type: str, date_str: str, content: dict):
        """保存分析报告。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO analysis_cache (type, date, content, created_at) "
            "VALUES (?, ?, ?, datetime('now','localtime'))",
            (analysis_type, date_str, json.dumps(content))
        )
        self.conn.commit()
```

#### 6.2.2 缓存键设计

```python
def make_cache_key(analysis_type: str, **params) -> str:
    """
    统一缓存键生成。
    
    例：
    make_cache_key("daily_report", user_id="default", date="2026-07-02")
    → "daily_report:default:2026-07-02"
    
    make_cache_key("deep_dive", fund_code="014847")
    → "deep_dive:014847"
    """
    parts = [analysis_type]
    for key, value in sorted(params.items()):
        parts.append(str(value))
    return ":".join(parts)
```

#### 6.2.3 缓存失效策略

```python
def invalidate_related_caches(event_type: str, target: str = None):
    """
    缓存失效策略：
    
    触发事件 → 失效相关缓存
    ├── 用户买入 → 失效 L1(持仓分析) + L3(首页日报)
    ├── 用户卖出 → 失效 L1(持仓分析) + L3(首页日报)
    ├── 新的一天 → 失效所有 L1
    └── 强制 → 失效所有
    """
    l1_cache = L1Cache()
    
    if event_type == "position_change":
        l1_cache.invalidate("diversification:")
        l1_cache.invalidate("health_score:")
        # L3 缓存设新 TTL（保留历史，但标记为"持仓已变"）
```

### 6.3 改动点

| 文件 | 改动 | 行数 |
|------|------|------|
| `agent/cache.py` | 新增 L1/L2/L3 三级缓存 | ~180 行 |
| `agent/orchestrator.py` | 接入缓存查询 | ~20 行 |
| `db/__init__.py` | 新增 analysis_cache 表 | ~15 行 |

**效果**：
- 相同分析 5 分钟内重复请求 → 零延迟返回
- 语义相似的问题 → 命中缓存，省 token
- 日报/周报持久化 → 支持历史对比

---

## 7. 技术升级六：成本治理（Cost Governance）

### 7.1 问题

**企业级做法**（题目 16：多 Agent 系统成本控制）：

```
6 层成本控制体系：
1. Token 预算控制（超限拒绝）
2. 成本感知路由（不同 Agent 用不同模型）
3. 按需触发（关键词预检）
4. 语义缓存（相同问题跳过 LLM）
5. 跳过非必要环节（方向一致跳过交叉审阅）
6. 开关控制（所有耗 token 功能可关闭）
```

**我们现状**：
- Token 预算控制 ✅ `agent/orchestrator_optimizer.py` 已有
- 成本感知路由 ✅ 但模型选择固定
- 语义缓存 ✅ 但不够高效
- 按需触发 ⚠️ 部分有
- 跳过非必要 ⚠️ `skip_if_unnecessary=True` 但没细粒度

### 7.2 方案：成本仪表盘 + 精细化控制

#### 7.2.1 成本追踪

```python
# backend/cost_tracker.py

import json
from datetime import datetime, timedelta
from collections import defaultdict


class CostTracker:
    """
    Token 成本追踪。
    
    每个 LLM 调用都记录：
    - 调用的 Agent/功能
    - 模型名称（不同模型价格不同）
    - Prompt/Completion tokens
    - 估算成本（元）
    """
    
    # 价格表（每百万 tokens）
    MODEL_PRICES = {
        "deepseek-chat": {"input": 0.5, "output": 2.0},
        "deepseek-reasoner": {"input": 1.0, "output": 4.0},
        "mimo": {"input": 0.3, "output": 1.2},
    }
    
    def __init__(self, conn=None):
        self.conn = conn
    
    def record(self, source_type: str, model: str, 
                prompt_tokens: int, completion_tokens: int):
        """记录一次调用。"""
        prices = self.MODEL_PRICES.get(model, {"input": 0.5, "output": 2.0})
        cost = (prompt_tokens * prices["input"] + 
                completion_tokens * prices["output"]) / 1_000_000
        
        self.conn.execute(
            "INSERT INTO cost_logs (source_type, model, prompt_tokens, "
            "completion_tokens, cost, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))",
            (source_type, model, prompt_tokens, completion_tokens, cost)
        )
        self.conn.commit()
    
    def daily_summary(self, date_str: str) -> dict:
        """每日成本汇总。"""
        cur = self.conn.execute(
            "SELECT source_type, SUM(cost), SUM(prompt_tokens), SUM(completion_tokens) "
            "FROM cost_logs WHERE date(created_at) = ? "
            "GROUP BY source_type",
            (date_str,)
        )
        rows = cur.fetchall()
        
        total = sum(r[1] for r in rows)
        by_type = {r[0]: {
            "cost": round(r[1], 4),
            "tokens": r[2] + r[3],
        } for r in rows}
        
        return {
            "date": date_str,
            "total_cost": round(total, 4),
            "by_type": by_type,
            "estimate_monthly": round(total * 30, 2),
        }
```

#### 7.2.2 月度预算控制

```python
class BudgetController:
    """
    预算控制器。
    
    每月设定预算上限，超额时自动降级。
    """
    
    def __init__(self, monthly_budget: float = 30.0, conn=None):
        self.budget = monthly_budget  # 月预算上限（元）
        self.conn = conn
    
    def get_remaining_budget(self) -> dict:
        """查询当月预算使用情况。"""
        cur = self.conn.execute(
            "SELECT SUM(cost) FROM cost_logs "
            "WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
        )
        row = cur.fetchone()
        used = row[0] or 0
        remaining = self.budget - used
        pct = used / self.budget if self.budget > 0 else 0
        
        return {
            "budget": self.budget,
            "used": round(used, 2),
            "remaining": round(remaining, 2),
            "usage_pct": round(pct * 100, 1),
        }
    
    def should_use_expensive_model(self, analysis_type: str) -> bool:
        """
        是否还能用高成本模型。
        
        预算 > 50% → 正常
        预算 20-50% → 核心分析用贵模型，非核心降级
        预算 < 20% → 全部降级为便宜模型
        """
        info = self.get_remaining_budget()
        pct = info["usage_pct"]
        
        if pct < 50:
            return True
        elif pct < 80:
            return analysis_type in {"deep_dive", "portfolio_trade"}
        else:
            return False
```

### 7.3 改动点

| 文件 | 改动 | 行数 |
|------|------|------|
| `cost_tracker.py` | 新增成本追踪 + 预算控制 | ~150 行 |
| `agent/llm_interface.py` | 每个调用记录 cost | ~10 行 |
| `db/__init__.py` | 新增 cost_logs 表 | ~15 行 |

**效果**：
- 每月明确知道花了多少钱
- 预算快用完时自动降级（贵模型 → 便宜模型）
- 可以看到"哪个 Agent 最花钱"

---

## 8. 扩展技术（面试题外，但企业级常见）

### 8.1 数据质量门禁（Data Quality Gate）

```python
# agent/data_gate.py

class DataQualityGate:
    """
    在 LLM 调用前检查数据质量。
    
    问题：垃圾进 → 垃圾出
    LLM 花大量 token 处理脏数据，成本浪费 + 结果差。
    """
    
    CHECKS = {
        "fund_code": {
            "type": "regex",
            "pattern": r"^\d{6}$",
            "error": "基金代码格式错误（应为6位数字）",
        },
        "date": {
            "type": "date",
            "min": "2020-01-01",
            "max": "today",
            "error": "日期超出合理范围",
        },
        "percentage": {
            "type": "range",
            "min": -100,
            "max": 100,
            "error": "百分比超出合理范围",
        },
        "holding_ratio": {
            "type": "range",
            "min": 0,
            "max": 100,
            "error": "持仓比例超出 0-100%",
        },
    }
    
    @classmethod
    def validate_input(cls, data: dict, schema: dict) -> dict:
        """
        验证输入数据。
        
        Args:
            data: 待验证的数据
            schema: 字段定义 {field_name: check_name}
            
        Returns:
            {"passed": bool, "errors": list[str]}
        """
        errors = []
        for field, check_name in schema.items():
            if field not in data:
                errors.append(f"缺少必需字段: {field}")
                continue
            
            check = cls.CHECKS.get(check_name)
            if not check:
                continue
            
            value = data[field]
            
            if check["type"] == "regex":
                import re
                if not re.match(check["pattern"], str(value)):
                    errors.append(f"{field}: {check['error']}")
            
            elif check["type"] == "range":
                try:
                    v = float(value)
                    if v < check["min"] or v > check["max"]:
                        errors.append(f"{field}: {check['error']} (实际值={v})")
                except (ValueError, TypeError):
                    errors.append(f"{field}: 无法转换为数字")
            
            elif check["type"] == "date":
                try:
                    from datetime import date
                    d = datetime.strptime(str(value), "%Y-%m-%d").date()
                    if d < datetime.strptime(check["min"], "%Y-%m-%d").date():
                        errors.append(f"{field}: {check['error']}")
                    elif d > date.today():
                        errors.append(f"{field}: {check['error']}")
                except ValueError:
                    errors.append(f"{field}: 日期格式错误")
        
        return {
            "passed": len(errors) == 0,
            "errors": errors,
            "error_count": len(errors),
        }
```

**效果**：
- LLM 不会收到格式错误的数据
- 避免了"无效基金代码 → LLM 乱猜 → 幻觉"的级联错误

### 8.2 Prompt 版本管理（Prompt as Code）

```python
# agent/prompt_registry.py

class PromptRegistry:
    """
    Prompt 版本管理。
    
    每个 prompt 都有版本号、变更记录。
    支持回滚和 A/B 测试。
    """
    
    def __init__(self, conn=None):
        self.conn = conn
    
    def register(self, name: str, version: str, content: str, 
                 author: str = "admin", notes: str = "") -> int:
        """注册一个新版本的 prompt。"""
        self.conn.execute(
            "INSERT INTO prompt_registry (name, version, content, author, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, version, content, author, notes)
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    
    def get_active(self, name: str) -> dict:
        """获取当前活跃版本的 prompt。"""
        cur = self.conn.execute(
            "SELECT id, version, content, notes, created_at "
            "FROM prompt_registry WHERE name=? AND active=1 "
            "ORDER BY created_at DESC LIMIT 1",
            (name,)
        )
        row = cur.fetchone()
        if row:
            return {
                "id": row[0], "version": row[1], "content": row[2],
                "notes": row[3], "created_at": row[4],
            }
        return None
    
    def diff(self, name: str, v1: str, v2: str) -> list[str]:
        """对比两个版本的差异。"""
        c1 = self.conn.execute(
            "SELECT content FROM prompt_registry WHERE name=? AND version=?",
            (name, v1)
        ).fetchone()
        c2 = self.conn.execute(
            "SELECT content FROM prompt_registry WHERE name=? AND version=?",
            (name, v2)
        ).fetchone()
        if not c1 or not c2:
            return ["版本不存在"]
        
        import difflib
        diff = difflib.unified_diff(
            c1[0].splitlines(), c2[0].splitlines(),
            fromfile=v1, tofile=v2, lineterm=""
        )
        return list(diff)
```

**效果**：
- 每次 prompt 修改都有记录
- 回滚到老版本只需一条 SQL
- 能对"改了 prompt 后评分涨了 0.3"做因果分析

### 8.3 标准化测试套件（Graded Eval Suite）

```python
# eval/test_suite.py

"""
标准化测试套件（借鉴 MMLU / C-Eval 思想）。

每个分析类型有独立的测试集：
- daily_report: 20 条标准测试场景
- deep_dive: 15 条
- diversification: 10 条
- health_score: 10 条

每次运行 = 测试套件 + 跑分 + 增量对比
"""

TEST_SUITES = {
    "daily_report": [
        {
            "id": "DR-001",
            "input": {"funds": [...持仓数据], "market_data": {...}},
            "expected": {
                "must_mention": ["集中度", "费率"],
                "must_not": ["具体买入价格"],
                "style": "summary",
            },
            "weight": 2.0,  # 加权：重要的 test case
        },
        {
            "id": "DR-002",
            "input": {"funds": [...空持仓]},
            "expected": {
                "must_mention": ["暂无持仓"],
                "style": "empty_analysis",
            },
            "weight": 1.0,
        },
        # ... 更多 test cases
    ],
    
    "deep_dive": [
        {
            "id": "DD-001",
            "input": {"fund_code": "014847"},
            "expected": {
                "must_mention": ["基金类型", "规模", "费率"],
                "must_be_factual": ["基金代码应正确"],
            },
        },
    ],
}


def run_test_suite(analysis_type: str, version_label: str) -> dict:
    """
    运行标准化测试套件。
    
    Returns:
        {
            "version": version_label,
            "analysis_type": analysis_type,
            "timestamp": "",
            "total_cases": 20,
            "passed": 17,
            "failed": 3,
            "score": 0.85,
            "details": [
                {"id": "DR-001", "passed": True, "score": 0.9},
                {"id": "DR-002", "passed": False, "score": 0.4, "failure": "未提及空持仓"},
            ],
            "prev_score": 0.82,  # 上次测试的成绩
            "regression": False,  # 是否退步
        }
    """
```

**效果**：
- 每次改动 prompt 后跑一遍 → 知道有没有退步
- 质量不是一个模糊感觉，是一个具体分数
- 回归检测：退步了阻止合并（如果和 CI 联动）

---

## 9. 实现计划

### Phase 1：幻觉防御 + 查询改写（1-2天）

| 优先级 | 技术 | 文件 | 行数 |
|--------|------|------|------|
| P0 | 金融数据校验 | `agent/validator.py` | 40 |
| P0 | Self-Consistency 验证 | `agent/validator.py` | 80 |
| P0 | 防御 Prompt 注入 | `agent/prompt_defense.py` | 80 |
| P1 | Query Rewriting 规则引擎 | `agent/query_rewriter.py` | 150 |

### Phase 2：评测增强 + 成本追踪（1天）

| 优先级 | 技术 | 文件 | 行数 |
|--------|------|------|------|
| P1 | 统计显著性检验 | `eval_system.py` | 80 |
| P1 | 业务指标追踪 | `eval_system.py` | 80 |
| P1 | 成本追踪 | `cost_tracker.py` | 150 |
| P2 | 告警规则引擎 | `eval_system.py` | 90 |

### Phase 3：缓存 + 路由 + 数据门禁（1天）

| 优先级 | 技术 | 文件 | 行数 |
|--------|------|------|------|
| P1 | 三级缓存体系 | `agent/cache.py` | 180 |
| P2 | MoE 动态路由 | `agent/router.py` | 160 |
| P2 | 数据质量门禁 | `agent/data_gate.py` | 100 |

### Phase 4：基础设施（后续迭代）

| 优先级 | 技术 | 文件 | 行数 |
|--------|------|------|------|
| P2 | Prompt 版本管理 | `agent/prompt_registry.py` | 100 |
| P2 | 标准化测试套件 | `eval/test_suite.py` | 150 |

---

## 10. 优先级与收益矩阵

```
效果大 ┼───────────────────────────────────
       │                               ▲
       │                               │
       │  幻觉防御 (P0)               │ 收益最大
       │  ├─ 减少编造数据             │ 投入最少
       │  └─ 金融专用校验             │
       │                               │
       │  查询改写 (P1)               │
       │  └─ 对话体验提升               │
       │                               │
       │  统计检验 (P1)               │
       │  成本追踪 (P1)               │
       │  三级缓存 (P1)               │
       │                               │
       │  MoE 路由 (P2)               │
       │  数据门禁 (P2)               │
       │  Prompt 版本 (P2)             │
       │                               │
       │  标准化测试 (P3)             │
       │  告警规则 (P3)               │
       └───────────────────────────────→
          投入少                   投入多
```

**建议执行顺序**：

```
幻觉防御 → 查询改写 → 成本追踪
  ↓
统计检验 + 三级缓存
  ↓
MoE 路由 + 数据门禁
  ↓
Prompt 版本管理 + 标准化测试
```

---

## 11. 与现有架构的关系

```
现有文件                   新增/修改
────────────────────────────────────────────
agent/orchestrator.py → 接入 query rewriting
agent/multi_agent.py  → 注入防御 prompt
agent/router.py       → MoE 动态路由
agent/validator.py    → Self-Consistency + 金融校验
agent/cache.py        → 三级缓存体系
eval_system.py        → 统计检验 + 业务指标 + 告警
└─ 无                 → cost_tracker.py (新增)
└─ 无                 → query_rewriter.py (新增)
└─ 无                 → prompt_defense.py (新增)
└─ 无                 → data_gate.py (新增)
└─ 无                 → prompt_registry.py (新增)
└─ 无                 → test_suite.py (新增)
```

**核心原则：不伤现有逻辑，只叠加增强层。** 所有新增模块可以独立开关，不影响核心分析流程。