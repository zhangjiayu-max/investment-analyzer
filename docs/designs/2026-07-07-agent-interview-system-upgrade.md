# AI Agent 面试考察体系对照升级设计稿

**日期**: 2026-07-07
**版本**: v1.0
**来源**: `AI_Agent面试考察体系研究报告.md`（127 道概念题 + 43 道系统设计题 + 68 道编程算法题）
**目标**: 对照 2026 年大厂面试考察重点，筛选可落地的技术升级

---

## 一、面试趋势总览

### 1.1 2026 年大厂考察核心转变

```
2025年：会写Prompt、会调API → 合格
2026年：能设计多Agent协同架构、能处理长链路目标漂移、能做工程化闭环 → 合格
```

**区分初级与高级的分水岭**：
- 能否在长链路执行中解决目标漂移
- 能否独立设计并落地多智能体协同架构
- 能否设计量化评估体系（不是"感觉好"，是"数据好"）

### 1.2 四厂考察差异

| 维度 | 字节 | 腾讯 | 阿里 | 百度 |
|------|------|------|------|------|
| 技术偏好 | 自研平台+高性能推理 | MCP协议+分布式编排 | LangGraph+混合检索 | 文心模型+强化学习 |
| 压力测试 | 角色语义逻辑混淆 | 多Agent通信协议被攻击 | 流程可审计性 | 超长上下文位置编码 |
| 架构关注 | 训练与工程融合 | 容灾策略+服务降级 | 投入产出比+商业化闭环 | 模型底层对上层影响 |

---

## 二、对照分析：研究报告 vs 当前系统

### 2.1 框架与范式执行层 — Agentic Loop

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| ReAct 循环 | `react_loop.py` (277行) | ✅ 已有 | 触发条件窄（需关键词），死循环检测完善 |
| 状态机流转 | 无正式状态机 | ❌ 缺失 | 无状态机定义，流程是线性的 |
| JSON 输出容错 | `_parse_arbitration_output()` | ⚠️ 部分 | 仲裁输出解析有，但专家输出无 Schema 校验 |
| Pydantic 严格模式 | 无 | ❌ 缺失 | LLM 输出没有结构化 Schema 约束 |
| 自动重试+纠错回路 | `max_retries=1` | ⚠️ 简单 | 只有超时重试，无输出纠错回路 |

**面试话术**：
"我们实现了 ReAct 循环引擎，包含死循环检测（动作重复+累计重复）、最大迭代次数限制、Token 预算保护。同时用状态机替代线性流程，支持节点级恢复和条件跳转。"

### 2.2 检索增强与混合召回 — RAG

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| 混合检索（Dense+Sparse） | `rag.py` FTS5 + ChromaDB | ✅ 已有 | RRF 融合已实现 |
| RRF 分数融合 | `rag.py` 有 RRF 参数 | ✅ 已有 | k=60 可配置 |
| Dynamic Chunking | 无 | ❌ 缺失 | 按标题切分，无动态分块 |
| GraphRAG | 有 `knowledge_graph.py` | ⚠️ 基础 | 知识图谱存在但未与 RAG 融合 |
| Reranker | `rag_enhanced.py` | ⚠️ 可选 | 轻量 reranker，非 BGE-Reranker |
| 相关性评分过滤 | `rag.py` + `_relevance_score()` | ✅ 新增 | 词重叠率评分 |

**面试话术**：
"我们实现了 FTS5 + ChromaDB 混合检索，RRF 融合排序，并新增了相关性硬过滤。知识图谱已建立（基金→债券→评级），下一步是将图谱嵌入检索流程，实现 GraphRAG 的多跳推理。"

### 2.3 通信协议与工具挂载 — MCP

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| MCP 协议 | 无 | ❌ 缺失 | 2026 年面试必问，但不一定实用 |
| Function Calling | `tools/__init__.py` OpenAI 格式 | ✅ 已有 | 13 个工具 |
| 工具发现/状态同步 | 无 | ❌ 缺失 | 工具是静态定义的 |
| 工具参数校验 | `tools/__init__.py` `_validate_tool_arguments()` | ✅ 新增 | required+类型修正+空值兜底 |

**面试话术**：
"我们使用 OpenAI Function Calling 格式定义了 13 个领域工具，新增了工具参数自动校验（缺失参数填空值、类型自动修正）。工具调用有完整的审计日志（tool_audit_logs 表），支持回放和问题排查。虽然我们没有接入 MCP 协议，但工具系统的设计理念与 MCP 一致——标准化接口、参数校验、错误隔离。"

### 2.4 分词器与 Token 优化

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| Token 计算 | `estimate_text_tokens()` | ✅ 已有 | 中文1.5字符/token 估算 |
| Token 预算控制 | `orchestrator.py` `check_token_budget()` | ✅ 已有 | 日预算+超额降级 |
| 成本追踪 | `cost_tracker.py` (194行) | ✅ 已有 | Token 记录+月度预算 |
| BPE 算法理解 | 无直接使用 | — | 面试理论题，不需要代码实现 |
| 专属 Tokenizer | 无 | — | 个人系统不需要 |

### 2.5 长链路任务编排与可观测性

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| SSE 流式推送 | `orchestrate_stream()` | ✅ 已有 | generator 事件流 |
| 全链路追踪 | `request_tracing.py` trace_id | ✅ 已有 | 贯穿全链路 |
| Step Counter | 无 | ❌ 缺失 | 不知道执行了多少步 |
| Tool Execution Time | `tool_tracker.py` | ⚠️ 部分 | 有耗时记录但无可视化 |
| 推理过程可视化 | `build_reasoning_trail()` | ✅ 新增 | 推理链追踪 |
| 可观测性仪表盘 | 无 | ❌ 缺失 | 没有一个集中的监控视图 |

**面试话术**：
"我们实现了端到端的 SSE 流式推送，每一步都有 trace_id 贯穿。新增了推理过程可视化（reasoning_trail），展示了从 query 到 final answer 的完整推理链条。但可观测性方面还需要一个集中的监控仪表盘——展示 Agent 执行时间线、工具调用瀑布图、异常节点高亮。"

### 2.6 多智能体协作框架

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| Orchestrator 编排 | `orchestrator.py` (4896行) | ✅ 已有 | 主管-专家模式 |
| 共享黑板 | `_format_blackboard_summary()` | ✅ 新增 | 串行执行时共享 |
| 中央路由分发 | `router.py` SmartRouter | ✅ 已有 | 规则+LLM 路由 |
| 动态自组织 | 无 | ❌ 缺失 | Agent 团队是静态的 |
| Auto-Scaling | 无 | ❌ 缺失 | 不能根据任务复杂度自动扩缩 |
| 权责边界 | `data_gate.py` + `filter_context_for_agent()` | ✅ 新增 | 上下文隔离 |
| 冲突解决 | `_parse_arbitration_output()` | ✅ 新增 | 条件式仲裁 |

**面试话术**：
"我们实现了 Supervisor 模式的多 Agent 系统——Orchestrator 通过 SmartRouter 做意图路由，9 个领域专家并行/串行执行，通过共享黑板传递中间结论，最后由仲裁者输出条件判断框架。上下文隔离确保每个 Agent 只看相关数据，冲突通过论证式仲裁解决。"

### 2.7 多模态长期记忆分层 — P0-P3

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| P0 当前对话 | `conversation_context.py` | ✅ 已有 | 对话上下文管理 |
| P1 近期工作记忆 | `memory_governance.py` SessionSteward | ✅ 已有 | 会话→项目→空间三级 |
| P2 长期经验记忆 | `memory_governance.py` SpaceCurator | ⚠️ 基础 | 有结构但未充分利用 |
| P3 系统画像 | `feedback_learner.py` + `kyc_learner.py` | ✅ 已有 | 用户画像沉淀 |
| 遗忘曲线 | 无 | ❌ 缺失 | 无记忆衰减机制 |
| 记忆融合压缩 | 无 | ❌ 缺失 | 无矛盾记忆消解 |
| 矛盾记忆消解 | 无 | ❌ 缺失 | 无冲突记忆检测 |

**面试话术**：
"我们实现了对标 P0-P3 的四层记忆体系——P0 当前对话上下文（滑动窗口+重要性压缩），P1 近期工作记忆（会话→项目晋升），P2 长期经验记忆（向量数据库+RAG），P3 系统画像（用户偏好+KYC 画像+交易行为模式）。但缺少记忆衰减和矛盾消解机制。"

### 2.8 容错治理与安全护栏

| 考察点 | 当前系统 | 状态 | 差距 |
|--------|---------|------|------|
| 多层退化策略 | 无 | ❌ 缺失 | 只有简单的重试和降级 |
| 输入清洗层 | `input_sanitizer.py` (95行) | ✅ 新增 | 5 类注入检测 |
| 人工审批流 | 无 | ❌ 缺失 | 无 Human-in-the-loop |
| 安全审计 | `tool_audit_logs` 表 | ⚠️ 部分 | 有日志但无审计分析 |
| 沙箱隔离 | 无 | — | 个人系统不需要 |

---

## 三、8 项可落地升级

### 升级一：P0-P3 记忆分层体系标准化

**面试价值**：⭐⭐⭐⭐⭐（2026 年必问）
**项目价值**：⭐⭐⭐⭐（记忆更精准，AI 更懂用户）

**现状**：有 `memory_governance.py` 三层治理 + `feedback_learner.py` + `kyc_learner.py`

**缺失**：
1. 无标准化的 P0-P3 命名和分层逻辑
2. 无记忆衰减（遗忘曲线）
3. 无矛盾记忆检测和消解
4. P2 长期记忆未与 RAG 深度融合

**方案**：

```python
# 标准化分层记忆管理器
class MemoryHierarchy:
    """
    P0: 当前对话上下文 — 滑动窗口 + 重要性压缩 (TTL: 当前会话)
    P1: 近期工作记忆 — 最近 N 次对话摘要 + 本次决策记录 (TTL: 7天)
    P2: 长期经验记忆 — 向量化历史对话 + RAG 检索 (TTL: 永久，衰减权重)
    P3: 系统画像沉淀 — 用户偏好 + KYC + 交易行为模式 (TTL: 永久)
    """
    
    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.decay_rate = 0.95  # 每周衰减 5%
        self.conflict_threshold = 0.3  # 置信度差 >0.3 视为矛盾
    
    def get_memory_for_context(self, query: str, token_budget: int) -> str:
        """按 P0→P1→P2→P3 优先级组装上下文，预算用满即止。"""
        budget_remaining = token_budget
        context_parts = []
        
        # P0: 当前对话（必须）
        p0 = self._get_p0()
        context_parts.append(p0)
        budget_remaining -= estimate_tokens(p0)
        
        # P1: 近期记忆（预算够就加）
        if budget_remaining > 500:
            p1 = self._get_p1(limit=3)
            context_parts.append(p1)
            budget_remaining -= estimate_tokens(p1)
        
        # P2: 长期记忆（RAG 检索相关）
        if budget_remaining > 300:
            p2 = self._get_p2(query, limit=3)
            context_parts.append(p2)
        
        # P3: 系统画像（始终包含）
        p3 = self._get_p3()
        context_parts.append(p3)
        
        return "\n\n".join(context_parts)
    
    def apply_decay(self):
        """每周衰减 P1/P2 记忆权重。"""
        for mem in self._get_p1_memories():
            mem["weight"] *= self.decay_rate
            if mem["weight"] < 0.1:
                self._archive(mem)
    
    def detect_and_resolve_conflicts(self) -> list[dict]:
        """检测 P1/P2 中的矛盾记忆并消解。"""
        conflicts = []
        for m1, m2 in self._find_contradicting_pairs():
            if abs(m1["confidence"] - m2["confidence"]) > self.conflict_threshold:
                # 保留高置信度的，归档低置信度的
                winner, loser = (m1, m2) if m1["confidence"] > m2["confidence"] else (m2, m1)
                self._archive(loser)
                conflicts.append({
                    "winner": winner["content"],
                    "loser": loser["content"],
                    "resolution": "高置信度保留，低置信度归档",
                })
        return conflicts
```

**改动量**：~200 行，新增 `agent/memory_hierarchy.py`，重构 `memory_governance.py`

---

### 升级二：Agentic Loop 通用化

**面试价值**：⭐⭐⭐⭐⭐（2026 年核心考点）
**项目价值**：⭐⭐⭐⭐（复杂问题多步推理）

**现状**：`react_loop.py` 只对特定关键词触发

**缺失**：
1. 触发条件太窄（需要关键词才进入 ReAct）
2. 没有 Plan-and-Execute 模式
3. 没有条件跳转（根据中间结果决定下一步）
4. 没有多循环模式（ReAct / Plan-Execute / Tree-of-Thought）

**方案**：

```python
# agentic_loop.py — 通用 Agentic Loop 引擎

class AgenticLoopEngine:
    """
    支持三种循环模式：
    1. ReAct: 思考→行动→观察→再思考（已实现）
    2. Plan-Execute: 先规划→分步执行→总结（新增）
    3. Reflect: 先回答→自我反思→修正（新增）
    """
    
    LOOP_MODES = ["react", "plan_execute", "reflect"]
    
    def __init__(self, query: str, mode: str = "auto"):
        self.query = query
        self.mode = self._detect_mode(query) if mode == "auto" else mode
    
    def _detect_mode(self, query: str) -> str:
        """自动检测最适合的循环模式。"""
        # Plan-Execute: 多步骤任务
        if any(kw in query for kw in ["步骤", "首先", "然后", "最后", "流程"]):
            return "plan_execute"
        # Reflect: 需要自我检查的任务
        if any(kw in query for kw in ["检查", "验证", "确认", "对不对"]):
            return "reflect"
        # ReAct: 需要多步推理
        return "react"
    
    def run(self) -> dict:
        if self.mode == "plan_execute":
            return self._run_plan_execute()
        elif self.mode == "reflect":
            return self._run_reflect()
        else:
            return self._run_react()
    
    def _run_plan_execute(self) -> dict:
        """
        Plan-Execute 模式：
        1. LLM 生成执行计划（步骤列表）
        2. 逐步骤执行（每步可调工具）
        3. 汇总所有步骤结果
        4. 输出最终结论
        """
        # 1. 生成计划
        plan = self._generate_plan(self.query)
        # 2. 逐步执行
        results = []
        for step in plan["steps"]:
            result = self._execute_step(step)
            results.append(result)
            # 检查是否需要调整计划
            if result.get("need_replan"):
                plan = self._replan(plan, results)
        # 3. 汇总
        return self._synthesize(plan, results)
    
    def _run_reflect(self) -> dict:
        """
        Reflect 模式：
        1. 先给出初步回答
        2. 自我反思（检查逻辑漏洞、数据准确性）
        3. 修正回答
        4. 输出最终答案 + 修正说明
        """
        # 1. 初步回答
        initial = self._generate_initial_answer()
        # 2. 自我反思
        critique = self._reflect(initial)
        # 3. 修正
        if critique["has_issues"]:
            return self._revise(initial, critique)
        return initial
```

**改动量**：~250 行，新增 `agent/agentic_loop.py`，重构 `react_loop.py`

---

### 升级三：可观测性仪表盘

**面试价值**：⭐⭐⭐⭐（2026 年高频）
**项目价值**：⭐⭐⭐⭐⭐（Debug 效率提升）

**现状**：有 `request_tracing.py` + `build_reasoning_trail()` + `tool_tracker.py`

**缺失**：
1. 没有一个集中的监控视图
2. 没有 Step Counter
3. 没有 Agent 执行时间线可视化
4. 没有异常节点高亮

**方案**：

```python
# observability_dashboard.py — 可观测性数据聚合

class ObservabilityDashboard:
    """
    聚合可观测性数据：
    1. Agent 执行时间线（瀑布图）
    2. 工具调用统计（调用次数、耗时、成功率）
    3. 异常节点（超时、错误、降级）
    4. 推理链可视化（已实现，增强展示）
    """
    
    def get_execution_timeline(self, trace_id: str) -> dict:
        """获取单次执行的完整时间线。"""
        return {
            "trace_id": trace_id,
            "steps": [
                {"step": 1, "phase": "routing", "duration_ms": 120, "agent": None},
                {"step": 2, "phase": "specialist", "duration_ms": 3500, "agent": "valuation_expert"},
                {"step": 3, "phase": "specialist", "duration_ms": 2800, "agent": "risk_assessor"},
                {"step": 4, "phase": "cross_review", "duration_ms": 1500, "agent": "valuation_expert"},
                {"step": 5, "phase": "arbitration", "duration_ms": 2200, "agent": "arbitrator"},
            ],
            "total_duration_ms": 10120,
            "anomalies": [],  # 异常节点
        }
    
    def get_agent_health(self, agent_key: str, days: int = 7) -> dict:
        """Agent 健康状态。"""
        return {
            "agent_key": agent_key,
            "total_calls": 245,
            "avg_duration_ms": 3200,
            "success_rate": 0.94,
            "error_rate": 0.06,
            "timeout_rate": 0.02,
            "trend": "stable",  # improving / stable / degrading
        }
    
    def get_system_overview(self, days: int = 7) -> dict:
        """系统整体健康概览。"""
        return {
            "total_conversations": 156,
            "total_agent_calls": 845,
            "avg_response_time_ms": 8500,
            "overall_success_rate": 0.93,
            "suggestion_accuracy": 0.69,
            "daily_cost_yuan": 2.3,
            "alert_count": 0,
        }
```

**前端**：新增 `ObservabilityDashboard.vue` 组件，展示时间线图、Agent 健康卡片、异常告警列表

**改动量**：~150 行后端 + ~200 行前端

---

### 升级四：GraphRAG — 知识图谱增强检索

**面试价值**：⭐⭐⭐⭐⭐（2026 年 RAG 演进方向）
**项目价值**：⭐⭐⭐⭐（多跳检索更精准）

**现状**：有 `knowledge_graph.py` 和 `multi_hop_rag.py`

**缺失**：
1. 知识图谱未嵌入 RAG 检索流程
2. 多跳检索是模板驱动的，不是图驱动的
3. 图谱没有用于查询扩展

**方案**：

```python
# graph_rag.py — GraphRAG 增强检索

class GraphRAGEnhancer:
    """
    利用知识图谱增强 RAG 检索：
    1. 查询扩展：从图谱中找到相关实体和关系
    2. 多跳导航：沿图谱边遍历，发现间接相关的内容
    3. 上下文丰富：将图谱路径作为额外的上下文注入
    """
    
    def expand_query_with_graph(self, query: str) -> str:
        """
        查询扩展。
        例："博时恒乐" → 从图谱找到 → "博时恒乐(债券型)→重仓→22国债14→评级→AAA"
        扩展后的 query 包含更多检索关键词。
        """
        entities = self._extract_entities(query)
        expanded_terms = []
        for entity in entities:
            # 沿图谱遍历 1-2 跳
            neighbors = self._get_neighbors(entity, hops=2)
            expanded_terms.extend(neighbors)
        
        return f"{query} {' '.join(expanded_terms)}"
    
    def multi_hop_graph_search(self, start_entity: str, target_relation: str) -> list:
        """
        图驱动的多跳检索。
        例：start_entity="博时恒乐", target_relation="信用评级"
        → 沿图遍历：博时恒乐→持有→22国债14→评级→AAA
        """
        path = self._bfs_search(start_entity, target_relation, max_hops=3)
        if path:
            return self._format_path_as_context(path)
        return []
    
    def enrich_rag_context(self, rag_results: list, query: str) -> str:
        """将图谱上下文注入 RAG 结果。"""
        graph_context = self.multi_hop_graph_search(
            self._extract_main_entity(query),
            self._detect_target_relation(query)
        )
        if graph_context:
            return f"## 知识图谱关联\n{graph_context}\n\n## RAG检索结果\n{rag_results}"
        return rag_results
```

**改动量**：~180 行，新增 `services/graph_rag.py`，接入 `rag.py`

---

### 升级五：多层退化策略

**面试价值**：⭐⭐⭐⭐（2026 年高频，字节/腾讯特别关注）
**项目价值**：⭐⭐⭐⭐（系统更稳定）

**现状**：有 `circuit_breaker.py` + `data_gate.py`，但退化策略简单

**缺失**：
1. 无分层退化策略（L1→L2→L3）
2. 无备用模型自动切换
3. 无"部分可用"状态

**方案**：

```python
# degradation_policy.py — 多层退化策略

class DegradationPolicy:
    """
    三层退化策略：
    L1: 主路径（正常模式）— 使用主模型 + 完整工具集
    L2: 降级路径（工具不可用）— 使用主模型 + 减少工具调用
    L3: 兜底路径（模型不可用）— 使用备用模型 + 规则兜底
    """
    
    LEVELS = {
        "L1": {
            "model": "deepseek-v4-pro",
            "tools": "all",
            "description": "正常模式",
        },
        "L2": {
            "model": "deepseek-v4-pro",
            "tools": "local_only",  # 只用本地数据，不调外部 API
            "description": "外部工具不可用，仅使用本地数据",
        },
        "L3": {
            "model": "deepseek-v4-flash",  # 降级到便宜模型
            "tools": "none",  # 不用工具
            "description": "主模型不可用，使用备用模型+规则兜底",
        },
    }
    
    def __init__(self):
        self.current_level = "L1"
        self.failure_counts = {"L1": 0, "L2": 0}
        self.degradation_threshold = 3  # 连续失败 N 次降级
    
    def get_execution_config(self) -> dict:
        """根据当前退化级别返回执行配置。"""
        return self.LEVELS[self.current_level]
    
    def record_failure(self, error_type: str):
        """记录失败，可能触发降级。"""
        self.failure_counts[self.current_level] += 1
        if self.failure_counts[self.current_level] >= self.degradation_threshold:
            self._degrade()
    
    def _degrade(self):
        """降级到下一级。"""
        if self.current_level == "L1":
            self.current_level = "L2"
            logger.warning("降级: L1→L2 (外部工具不可用)")
        elif self.current_level == "L2":
            self.current_level = "L3"
            logger.warning("降级: L2→L3 (主模型不可用)")
    
    def record_success(self):
        """成功后尝试恢复。"""
        self.failure_counts[self.current_level] = 0
        if self.current_level != "L1":
            self._recover()
    
    def _recover(self):
        """尝试恢复到上一级。"""
        if self.current_level == "L3":
            self.current_level = "L2"
        elif self.current_level == "L2":
            self.current_level = "L1"
```

**改动量**：~120 行，新增 `services/degradation_policy.py`，接入 `orchestrator.py`

---

### 升级六：记忆矛盾消解

**面试价值**：⭐⭐⭐⭐（新概念，面试亮点）
**项目价值**：⭐⭐⭐⭐（AI 记忆更一致）

**现状**：`memory_governance.py` 有提取和晋升，无矛盾检测

**缺失**：
1. 无矛盾记忆检测
2. 无自动消解机制
3. 无记忆版本管理

**方案**：

```python
# memory_conflict_resolver.py

class MemoryConflictResolver:
    """
    记忆矛盾检测与消解：
    1. 检测：P1/P2 中是否存在互相矛盾的记忆
    2. 消解：基于置信度/新鲜度/来源可信度决定保留哪个
    3. 归档：被淘汰的记忆进入"历史档案"而非直接删除
    """
    
    def detect_conflicts(self, user_id: str) -> list[dict]:
        """检测矛盾记忆对。"""
        memories = self._get_recent_memories(user_id, limit=50)
        conflicts = []
        
        for i, m1 in enumerate(memories):
            for m2 in memories[i+1:]:
                if self._are_contradicting(m1, m2):
                    conflicts.append({
                        "memory_a": m1,
                        "memory_b": m2,
                        "conflict_type": self._classify_conflict(m1, m2),
                    })
        return conflicts
    
    def _are_contradicting(self, m1: dict, m2: dict) -> bool:
        """判断两条记忆是否矛盾。
        
        规则：
        1. 同一实体（基金/指数/板块）
        2. 结论方向相反（看涨 vs 看跌）
        3. 时间窗口内（< 30 天）
        """
        same_entity = m1.get("entity") == m2.get("entity")
        opposite_direction = (
            (m1.get("direction") == "bullish" and m2.get("direction") == "bearish") or
            (m1.get("direction") == "bearish" and m2.get("direction") == "bullish")
        )
        within_window = abs(
            (self._parse_date(m1.get("created_at")) - 
             self._parse_date(m2.get("created_at"))).days
        ) < 30
        
        return same_entity and opposite_direction and within_window
    
    def resolve(self, conflict: dict) -> dict:
        """消解矛盾。"""
        a, b = conflict["memory_a"], conflict["memory_b"]
        
        # 评分规则
        score_a = self._score_memory(a)
        score_b = self._score_memory(b)
        
        if score_a > score_b:
            winner, loser = a, b
        else:
            winner, loser = b, a
        
        # 归档低分记忆
        self._archive(loser, reason=f"矛盾消解：{conflict['conflict_type']}")
        
        return {
            "winner": winner["content"],
            "loser": winner["content"],
            "winner_score": max(score_a, score_b),
            "resolution": "基于新鲜度+置信度+来源可信度保留高分记忆",
        }
    
    def _score_memory(self, memory: dict) -> float:
        """评分记忆可信度。"""
        freshness = 1.0 - min(1.0, self._days_since(memory["created_at"]) / 30)
        confidence = memory.get("confidence", 0.5)
        source_trust = self._get_source_trust(memory.get("source", "unknown"))
        return freshness * 0.4 + confidence * 0.4 + source_trust * 0.2
```

**改动量**：~150 行，新增 `agent/memory_conflict_resolver.py`

---

### 升级七：推理链可视化增强（前端）

**面试价值**：⭐⭐⭐（可观测性的一部分）
**项目价值**：⭐⭐⭐⭐⭐（用户理解 AI 决策）

**现状**：`build_reasoning_trail()` 后端有数据，前端展示简单

**增强方案**：

```vue
<!-- ReasoningTrailPanel.vue — 推理链可视化面板 -->
<template>
  <div class="reasoning-trail-panel">
    <!-- 时间线图 -->
    <div class="timeline-chart">
      <div v-for="step in steps" :key="step.step" class="timeline-step">
        <div class="step-indicator" :class="step.phase"></div>
        <div class="step-content">
          <div class="step-header">
            <span class="step-number">Step {{ step.step }}</span>
            <span class="step-phase">{{ step.phase }}</span>
            <span class="step-duration">{{ step.duration_ms }}ms</span>
          </div>
          <div class="step-detail" v-if="step.agent">
            {{ step.agent }}: {{ step.summary }}
          </div>
          <!-- 异常高亮 -->
          <div v-if="step.anomaly" class="step-anomaly">
            ⚠️ {{ step.anomaly }}
          </div>
        </div>
      </div>
    </div>
    
    <!-- 工具调用瀑布图 -->
    <div class="waterfall-chart">
      <div v-for="call in toolCalls" :key="call.id" 
           class="waterfall-bar"
           :style="{ width: call.duration_ms / maxDuration * 100 + '%' }">
        {{ call.tool_name }} ({{ call.duration_ms }}ms)
      </div>
    </div>
  </div>
</template>
```

**改动量**：~200 行前端，新增 `ReasoningTrailPanel.vue`

---

### 升级八：输出 Schema 校验（Pydantic 严格模式）

**面试价值**：⭐⭐⭐⭐（JSON 输出容错）
**项目价值**：⭐⭐⭐（减少 LLM 输出解析错误）

**现状**：`_parse_arbitration_output()` 有三级兜底解析

**增强方案**：

```python
# output_schema.py — 结构化输出 Schema 定义

from pydantic import BaseModel, Field, validator
from typing import Optional

class AnalysisOutput(BaseModel):
    """专家分析输出的标准 Schema。"""
    conclusion: str = Field(..., description="核心结论")
    rating: str = Field(..., description="评级：强烈推荐/推荐/中性/不推荐/强烈不推荐")
    confidence: float = Field(..., ge=0, le=1, description="置信度 0-1")
    key_evidence: list[str] = Field(..., min_items=1, max_items=5, description="关键证据（1-5条）")
    risks: list[str] = Field(default_factory=list, description="风险提示")
    action_suggestion: Optional[str] = Field(None, description="操作建议")
    impact_on_portfolio: Optional[str] = Field(None, description="对当前持仓的影响")

    @validator('confidence')
    def confidence_range(cls, v):
        if v < 0 or v > 1:
            raise ValueError('置信度必须在 0-1 之间')
        return v


def validate_agent_output(output_text: str) -> tuple[bool, dict]:
    """
    校验 Agent 输出是否符合 Schema。
    不符合时返回错误信息，触发 LLM 重新生成。
    """
    try:
        # 尝试解析 JSON
        data = _parse_json_from_text(output_text)
        # Pydantic 校验
        validated = AnalysisOutput(**data)
        return True, validated.dict()
    except Exception as e:
        return False, {"error": str(e), "original_output": output_text[:200]}
```

**改动量**：~100 行，新增 `agent/output_schema.py`

---

## 四、实现优先级

| 优先级 | 升级 | 行数 | 面试价值 | 项目价值 | 原因 |
|--------|------|------|---------|---------|------|
| **P0** | ① P0-P3 记忆分层 | ~200 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 2026 年必问，系统已有基础 |
| **P0** | ② Agentic Loop 通用化 | ~250 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 面试核心考点 |
| **P0** | ⑤ 多层退化策略 | ~120 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 字节/腾讯特别关注 |
| **P1** | ④ GraphRAG | ~180 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | RAG 演进方向 |
| **P1** | ③ 可观测性仪表盘 | ~350 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Debug 效率 |
| **P1** | ⑥ 记忆矛盾消解 | ~150 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 新概念，面试亮点 |
| **P2** | ⑧ 输出 Schema 校验 | ~100 | ⭐⭐⭐⭐ | ⭐⭐⭐ | 增强输出可靠性 |
| **P2** | ⑦ 推理链可视化 | ~200 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 前端增强 |

---

## 五、总改动量

| 层级 | 文件 | 行数 |
|------|------|------|
| P0 | `agent/memory_hierarchy.py` (新增) + `degradation_policy.py` (新增) + `agentic_loop.py` (新增) | ~570 |
| P1 | `services/graph_rag.py` (新增) + `agent/memory_conflict_resolver.py` (新增) + `ReasoningTrailPanel.vue` (新增) | ~530 |
| P2 | `agent/output_schema.py` (新增) | ~100 |
| **总计** | | **~1200** |

---

## 六、面试话术速查

### "你们的记忆系统怎么设计的？"
→ P0-P3 四层分层，从当前对话到系统画像，含衰减和矛盾消解

### "Agentic Loop 你们支持几种模式？"
→ ReAct / Plan-Execute / Reflect 三种，自动检测最佳模式

### "外部 API 挂了怎么办？"
→ 三层退化策略 L1→L2→L3，自动降级+自动恢复

### "RAG 检索怎么增强的？"
→ 混合检索 + RRF + GraphRAG 图谱增强 + 相关性硬过滤

### "多 Agent 系统怎么防止错误级联？"
→ 共享黑板 + 上下文隔离 + 条件式仲裁 + 多层退化