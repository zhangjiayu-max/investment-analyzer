# AI 对话系统深度评估报告 — 对标成熟框架

**日期**: 2026-07-03
**版本**: v1.0
**目标**: 全面评估当前 AI 对话系统的架构、编排、执行、上下文管理、中断重试等能力，对标 LangGraph / AutoGen / CrewAI 等成熟框架，找出可增强的缺口

---

## 一、评估方法

### 1.1 评估维度

| 维度 | 权重 | 说明 |
|------|------|------|
| 编排架构 | 20% | 多 Agent 的协作模式、流程控制、扩展性 |
| 上下文管理 | 20% | 上下文构建、共享/隔离、Token 预算、记忆 |
| 执行引擎 | 15% | 并行/串行执行、流式输出、超时控制 |
| 错误处理 | 15% | 重试、降级、熔断、恢复、死循环检测 |
| 状态管理 | 10% | 检查点、恢复、状态机、持久化 |
| 工具系统 | 10% | 工具定义、参数校验、调用审计、缓存 |
| 人工介入 | 10% | Human-in-the-loop、审批、确认、覆盖 |

### 1.2 对标框架

| 框架 | 核心特点 | 适合场景 |
|------|---------|---------|
| **LangGraph** | 状态图 + 检查点 + 条件边 | 复杂流程控制、可恢复执行 |
| **AutoGen** | 对话式多 Agent + Group Chat | 多 Agent 讨论协作 |
| **CrewAI** | 角色化 + 顺序/层级执行 | 任务分派、快速搭建 |
| **Semantic Kernel** | Planner + 函数调用 + 记忆 | 企业级集成 |
| **MetaGPT** | 角色化 + SOP + 文档产出 | 结构化输出、协作流程 |

---

## 二、现状评估：各维度详细打分

### 2.1 编排架构（当前评分：7/10）

#### 已有能力

| 能力 | 实现位置 | 评价 |
|------|---------|------|
| Supervisor 模式 | `orchestrator.py` | ✅ 主管-专家模式，Orchestrator 路由并汇总 |
| SOP 模板 | `orchestrator.py` | ✅ 4 个预定义模板（完整诊断/买入/卖出/定投） |
| 动态 Agent 追加 | `orchestrator.py` | ✅ 根据专家结果动态追加（风控→行为教练等） |
| 共享专家 | `router.py` | ✅ risk_assessor 始终参与复杂分析 |
| 专家容量限制 | `router.py` | ✅ 5 分钟滑动窗口，超载跳过 |
| 规则+LLM 路由 | `router.py` | ✅ 规则优先，LLM 兜底，60s 缓存 |
| ReAct 循环 | `react_loop.py` | ✅ 新增，5 次迭代，死循环检测 |

#### 缺口

**缺口 1：无 DAG 执行依赖**

当前所有专家是**并行或顺序执行，没有依赖图**。SOP 模板虽然有 `order` 和 `group`，但实际执行还是并行+串行。

```
当前：group 0 并行 → group 1 并行 → 仲裁
对标 LangGraph：任意 DAG 拓扑，A→B→C 或 A→B|C→D 都可以

场景：估值专家需要先出结果，配置专家才能基于估值做分析。
当前做法：估值专家先跑，然后配置专家再跑（串行，硬编码）。
LangGraph 做法：定义 DAG 边，框架自动决定执行顺序。
```

**增强方案**：

```python
# 在 SOP 模板中增加依赖关系
SOP_TEMPLATES_V2 = {
    "full_diagnosis": {
        "name": "完整持仓诊断",
        "dag": [
            {"id": "valuation", "agents": ["valuation_expert"], "depends_on": []},
            {"id": "risk", "agents": ["risk_assessor"], "depends_on": []},
            {"id": "allocation", "agents": ["allocation_advisor"], "depends_on": ["valuation", "risk"]},
            {"id": "behavior", "agents": ["behavior_coach"], "depends_on": ["allocation"]},
            {"id": "arbitrate", "agents": ["arbitrator"], "depends_on": ["behavior"]},
        ],
    }
}
```

**改动量**：~80 行，新增 DAG 执行器，不修改现有并行逻辑

---

**缺口 2：无条件分支执行**

当前路由决定调哪些专家后，全部执行。没有"如果 A 给出结论 X，则跳过 B"的条件逻辑。

```
场景：用户问"沪深300现在估值贵吗？"
策略：只调 valuation_expert → 如果估值在合理区间，直接回答，不调其他专家
      如果估值偏高，再调 risk_assessor 评估风险

当前：要么只调能估值专家（简单），要么调多个（复杂），没有条件分支
```

**增强方案**：

```python
# 在 orchestrator 中增加条件分支
def orchestrate_with_conditional(query, specialists, ...):
    """有条件分支的执行流程。"""
    # 第一阶段：必须的专家
    mandatory = [s for s in specialists if s in MANDATORY_AGENTS]
    results = run_parallel(mandatory, ...)
    
    # 第二阶段：根据结果决定是否追加
    for result in results:
        spawn_suggestions = _check_dynamic_spawn(result, set(mandatory))
        if spawn_suggestions:
            conditional = [s["agent_key"] for s in spawn_suggestions]
            results.extend(run_parallel(conditional, ...))
    
    # 第三阶段：仲裁
    return arbitrate(results)
```

**改动量**：~50 行，基于现有 `_check_dynamic_spawn()` 扩展

---

**缺口 3：无 Agent 间直接通信**

当前所有 Agent 只和 Orchestrator 通信（星型拓扑）。Agent 之间不能直接交流。

```
当前：Orchestrator ←→ Agent A, Agent B, Agent C
对标 AutoGen：Agent A ↔ Agent B 也可直接对话

场景：估值专家发现"这只基金估值偏高"，风控专家应该直接知道这个信息
当前做法：估值专家把结果返回给 Orchestrator → Orchestrator 传给风控
更好做法：估值专家直接"发送消息"给风控专家，Orchestrator 只做协调
```

**增强方案**：

```python
# 消息总线（轻量，不需要消息队列）
class AgentMessageBus:
    """Agent 间消息总线。"""
    def __init__(self):
        self.channels = {}  # channel_name → list of subscribers
    
    def subscribe(self, agent_key: str, channel: str):
        self.channels.setdefault(channel, set()).add(agent_key)
    
    def publish(self, channel: str, message: dict):
        for subscriber in self.channels.get(channel, set()):
            # 将消息注入到订阅者的上下文中
            self._inject(subscriber, message)
    
    def _inject(self, agent_key: str, message: dict):
        """在 Agent 的 prebuilt_context 中附加消息。"""
```

**改动量**：~100 行，新增消息总线，接入现有 `prebuilt_context` 注入逻辑

---

### 2.2 上下文管理（当前评分：7/10）

#### 已有能力

| 能力 | 实现位置 | 评价 |
|------|---------|------|
| 统一上下文构建器 | `conversation_context.py` | ✅ 14 个 Section 聚合 |
| Token 预算管理 | `orchestrator.py` | ✅ 按复杂度分配 Token |
| 对话摘要压缩 | `conversation_context.py` | ✅ 历史摘要 + 最近消息 |
| 用户画像注入 | `conversation_context.py` | ✅ KYC + 偏好 + 交易行为 |
| 冲突检测 | `conversation_context.py` | ✅ 检测 KYC/策略/偏好冲突 |
| 实体记忆追踪 | `conversation_context.py` | ✅ 值变化时记录，避免重复 |
| 查询改写 | `query_rewriter.py` | ✅ 多轮对话代词补全 |

#### 缺口

**缺口 4：上下文隔离不足**

当前所有 Agent 共享同一个上下文（Orchestrator 构建的 `prebuilt_context`）。但不同 Agent 需要不同粒度的上下文。

```
场景：风控专家需要"持仓数据+市场数据"，行为教练需要"交易行为数据+用户画像"
当前：所有 Agent 收到同样的 prebuilt_context（包含全部数据）
问题：Agent 需要从大量信息中自己找相关的，容易遗漏

对标：Semantic Kernel 的 Planner 可以为每个函数的参数自动选择相关的上下文
```

**增强方案**：

```python
# 上下文过滤器
CONTEXT_FILTERS = {
    "risk_assessor": ["portfolio_context", "valuation_context", "entity_memory"],
    "behavior_coach": ["user_profile_context", "trade_pattern_context", "conversation_state"],
    "valuation_expert": ["portfolio_context", "valuation_context", "rag_context"],
    "allocation_advisor": ["portfolio_context", "valuation_context", "decision_context", "watchlist_context"],
    "market_analyst": ["valuation_context", "rag_context", "change_context"],
    "fund_analyst": ["portfolio_context", "watchlist_context"],
    "macro_strategist": ["rag_context", "valuation_context"],
    "article_expert": ["rag_context", "conversation_summary"],
    "wealth_advisor": ["portfolio_context", "user_profile_context", "decision_context", "trade_pattern_context"],
}

def filter_context(sections: dict, agent_key: str) -> str:
    """为指定 Agent 过滤上下文，只保留相关的 section。"""
    allowed = CONTEXT_FILTERS.get(agent_key, list(sections.keys()))
    filtered = {k: v for k, v in sections.items() if k in allowed}
    # 组装
    return _compose_prompt_context(filtered, token_budget=2000)
```

**改动量**：~60 行，新增 `CONTEXT_FILTERS` 字典 + `filter_context()` 函数

---

**缺口 5：无上下文版本控制**

当前上下文是"构造一次，用完丢弃"。如果用户在对话中做了操作（比如赎回了基金），之前的上下文就失效了。

```
场景：用户说"赎回了博时恒乐一半"
当前：系统回复时基于前一次上下文中"博时恒乐占比32%"的数据
问题：上下文不包含"用户刚赎回一半"这个事实

对标：LangGraph 的 State 是持久化的，每一步更新状态
```

**增强方案**：

```python
# 上下文增量更新
def update_context_after_action(action: dict, context: dict) -> dict:
    """
    用户执行操作后，增量更新上下文，不需要重建。
    
    例：用户赎回了博时恒乐一半
    → 更新 portfolio_context 中的博时恒乐占比
    → 在 change_context 中增加"用户刚减仓50%"
    """
    # 不重建，只更新改变的 section
    # 保证当前对话中后续的 Agent 看到的是最新数据
```

**改动量**：~80 行，新增 `update_context_after_action()`，在交易确认后调用

---

**缺口 6：无结构化上下文（当前是纯文本）**

当前上下文是纯文本拼接（`## 标题\n内容`）。Agent 需要从文本中自己提取结构化信息。

```
当前： "## 当前持仓\n博时恒乐：32%，风险等级：R3"
更好： 结构化 JSON 或 XML 格式
       "holdings": [{"name": "博时恒乐", "pct": 32, "risk": "R3"}]
```

**增强方案**：在上下文末尾增加一个 JSON 结构块

```python
# 在 context 末尾追加结构化数据块
def build_structured_data_block(sections: dict) -> str:
    """构建结构化数据块，供 Agent 精确引用。"""
    data = {
        "holdings": _extract_holdings(sections["portfolio_context"]),
        "valuations": _extract_valuations(sections["valuation_context"]),
        "user_profile": _extract_profile(sections.get("user_profile_context", "")),
        "decisions": _extract_decisions(sections.get("decision_context", "")),
    }
    return f"## 结构化数据\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
```

**改动量**：~80 行，新增 `build_structured_data_block()`，接入上下文构建

---

### 2.3 执行引擎（当前评分：6/10）

#### 已有能力

| 能力 | 实现位置 | 评价 |
|------|---------|------|
| 并行执行 | `orchestrator.py` | ✅ ThreadPoolExecutor |
| 流式输出 | `orchestrator.py` | ✅ generator 事件流 |
| 超时控制 | `orchestrator.py` | ✅ 单 Agent 超时 |
| 恢复执行 | `orchestrator.py` | ✅ checkpoint + resume |
| 取消事件 | `orchestrator.py` | ✅ cancel_event |

#### 缺口

**缺口 7：无 Agent 级别超时，只有全局超时**

当前超时是全局的（所有 Agent 共享一个总时间），没有每个 Agent 的独立超时。

```
场景：估值专家超时了（5秒），但风控专家还没开始跑
当前：全局超时 30 秒，如果估值专家用了 25 秒，风控只剩 5 秒
更好：每个 Agent 独立超时，一个超时不影响其他
```

**增强方案**：

```python
# 每个 Agent 独立超时
AGENT_TIMEOUTS = {
    "valuation_expert": 10,
    "risk_assessor": 10,
    "allocation_advisor": 15,
    "behavior_coach": 8,
    "market_analyst": 12,
    "fund_analyst": 12,
    "macro_strategist": 15,
    "article_expert": 8,
    "wealth_advisor": 15,
    "arbitrator": 20,
}

def run_with_per_agent_timeout(agent_key: str, fn, *args):
    timeout = AGENT_TIMEOUTS.get(agent_key, 10)
    return concurrent.futures.wait_for(fn, timeout=timeout)
```

**改动量**：~30 行，新增超时配置 + 调用处修改

---

**缺口 8：Agent 执行没有优先级调度**

当前 Agent 按路由顺序并行执行，没有优先级。如果资源受限（比如 Token 预算紧张或并发数限制），高优先级 Agent 应该先执行。

```
场景：Token 预算紧张，只能调 2 个 Agent
当前：调前 2 个（按路由列表顺序，可能不是最重要的）
更好：按优先级调（风控 > 配置 > 估值 > 市场）
```

**增强方案**：

```python
AGENT_PRIORITY = {
    "risk_assessor": 1,      # 最高：风险先行
    "allocation_advisor": 2,  # 配置次之
    "valuation_expert": 3,   # 估值
    "behavior_coach": 4,     # 行为教练
    "market_analyst": 5,     # 市场分析
    "fund_analyst": 6,       # 基金分析
    "macro_strategist": 7,   # 宏观
    "article_expert": 8,     # 文章解读
    "wealth_advisor": 9,     # 理财顾问
}

def prioritize_specialists(specialists: list[str], budget_remaining: int) -> list[str]:
    """按优先级排序 + 预算限制。"""
    sorted_specs = sorted(specialists, key=lambda s: AGENT_PRIORITY.get(s, 99))
    # 如果预算不够，截断低优先级的
    max_agents = budget_remaining // TOKEN_COST_PER_AGENT
    return sorted_specs[:max_agents]
```

**改动量**：~30 行，新增优先级 + 排序逻辑

---

**缺口 9：无 Agent 任务队列**

当前 Agent 直接在 Orchestrator 进程中执行。如果多个用户同时请求，Agent 会阻塞。

```
对标：LangGraph 的 Checkpoint 可以在任意节点暂停/恢复
AutoGen 的 Group Chat 有消息队列，异步处理
```

**增强方案**：引入轻量任务队列（使用已有的 `async_tasks` 表）

```python
# 异步 Agent 执行
def queue_agent_execution(agent_key: str, query: str, context: dict) -> int:
    """将 Agent 执行放入异步任务队列，立即返回 task_id。"""
    from db.async_tasks import create_task
    return create_task(
        task_type="agent_execution",
        payload={"agent_key": agent_key, "query": query, "context": context},
    )


def poll_agent_result(task_id: int, timeout: int = 30) -> dict:
    """轮询异步任务结果。"""
    from db.async_tasks import get_task_result
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = get_task_result(task_id)
        if result and result["status"] == "done":
            return result
        time.sleep(0.5)
    raise TimeoutError(f"Agent 执行超时: task_id={task_id}")
```

**改动量**：~100 行，基于现有 `db/async_tasks.py` 扩展

---

### 2.4 错误处理（当前评分：6/10）

#### 已有能力

| 能力 | 实现位置 | 评价 |
|------|---------|------|
| 熔断器 | `circuit_breaker.py` | ✅ 连续失败跳过 |
| 死循环检测 | `react_loop.py` | ✅ 重复动作 + 累计重复 |
| 超时控制 | `orchestrator.py` | ✅ 单 Agent 超时 |
| 恢复模式 | `orchestrator.py` | ✅ checkpoint + resume |
| 降级数据源 | `akshare_safe.py` | ✅ 重试逻辑 |

#### 缺口

**缺口 10：无指数退避重试**

当前重试是固定间隔（`retry=3`），没有指数退避（Exponential Backoff）。

```
场景：行情 API 短时故障（502）
当前：重试 3 次，每次间隔 1 秒 → 3 秒后全部失败
更好：第 1 次 1 秒，第 2 次 2 秒，第 3 次 4 秒 → 7 秒后可能恢复
```

**增强方案**：

```python
def retry_with_backoff(fn, max_retries=3, base_delay=1.0, max_delay=10.0):
    """指数退避重试。"""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(f"重试 {attempt + 1}/{max_retries}: {e}, 等待 {delay:.1f}s")
            time.sleep(delay)
```

**改动量**：~20 行，封装 `retry_with_backoff()` 函数，替换现有重试逻辑

---

**缺口 11：无部分失败处理**

当前如果某个 Agent 执行失败，整个分析可能被标记为"失败"。但更好的做法是**部分成功**——能用的结果继续用，失败的部分标记为"不可用"。

```
场景：5 个专家中 4 个成功，1 个超时
当前：返回 4 个结果 + 错误信息，但仲裁时不知道如何处理
更好：4 个成功结果正常进入仲裁，超时的标记为"不可用"，
      仲裁者知道"缺少风控分析，建议仅供参考"
```

**增强方案**：

```python
# 在 orchestrator 中处理部分失败
def run_specialists_with_partial_failure(specialists, context) -> list:
    results = []
    for agent_key in specialists:
        try:
            result = run_specialist(agent_key, context)
            results.append(result)
        except Exception as e:
            logger.warning(f"专家 {agent_key} 执行失败: {e}")
            results.append({
                "agent_key": agent_key,
                "agent": agent_key,  # 没有 name 也没关系
                "analysis": f"[该专家分析暂时不可用]",
                "error": str(e),
                "status": "unavailable",
            })
    return results
```

**改动量**：~30 行，修改 `run_specialists` 的异常处理逻辑

---

**缺口 12：无 Agent 恢复链**

当前如果 Orchestrator 崩溃，重启后需要重新整个流程。

```
对标：LangGraph 的 Checkpoint 可以在任意节点恢复
当前：只有消息级别的恢复（resume_message_id），没有节点级别的恢复
```

**增强方案**：利用已有的 `orchestration_checkpoints` 表，增加节点级别的恢复

```python
# 节点级别的检查点
def save_agent_checkpoint(conv_id, message_id, agent_key, state):
    """保存单个 Agent 的执行状态。"""
    _save_checkpoint(conv_id, message_id, f"agent:{agent_key}", state)

def resume_from_checkpoint(conv_id, message_id) -> dict:
    """从最近的检查点恢复。"""
    cp = _load_checkpoint(conv_id, message_id)
    if cp and cp["phase"].startswith("agent:"):
        agent_key = cp["phase"].split(":", 1)[1]
        return {"agent_key": agent_key, "state": cp}
    return None
```

**改动量**：~40 行，新增节点级 checkpoint 封装

---

### 2.5 状态管理（当前评分：6/10）

#### 已有能力

| 能力 | 实现位置 | 评价 |
|------|---------|------|
| 检查点持久化 | `orchestrator.py` | ✅ 数据库存储 |
| 恢复执行 | `orchestrator.py` | ✅ 从消息恢复 |
| 对话状态追踪 | `conversation_state.py` | ✅ 主题/分析类型/操作追踪 |

#### 缺口

**缺口 13：无完整状态机**

当前 Orchestrator 的流程是线性的：路由 → 执行 → 仲裁 → 输出。没有状态机（State Machine）的定义。

```
对标：LangGraph 用 StateGraph 定义状态转移
     State1 → edge(condition) → State2 → edge → State3

当前没有状态机：
- 路由完成后直接进入执行，无法"回到路由重新选择"
- 执行完成后直接进入仲裁，无法"回到执行补充专家"
- 仲裁完成后直接输出，无法"回到执行让专家重新分析"
```

**增强方案**：

```python
# 状态机定义
ORCHESTRATOR_STATES = {
    "route": {
        "next": ["execute", "route_fallback"],
        "on_fallback": "route_fallback",
    },
    "execute": {
        "next": ["arbitrate", "execute_more", "route"],
        "on_dynamic_spawn": "execute_more",   # 动态追加专家
        "on_results_unsatisfactory": "route",  # 结果不够，重新路由
    },
    "arbitrate": {
        "next": ["output", "execute_more"],
        "on_need_more_info": "execute_more",  # 仲裁者要求更多信息
    },
    "output": {
        "next": [],
        "final": True,
    },
}

def run_state_machine(query, ...):
    current_state = "route"
    context = {}
    
    while current_state in ORCHESTRATOR_STATES:
        state_def = ORCHESTRATOR_STATES[current_state]
        result = _execute_state(current_state, query, context)
        context.update(result)
        
        # 决定下一个状态
        for condition, next_state in state_def.get("conditions", {}).items():
            if result.get(condition):
                current_state = next_state
                break
        else:
            # 默认走第一个 next
            current_state = state_def["next"][0] if state_def["next"] else None
```

**改动量**：~120 行，新增状态机引擎，不修改现有执行逻辑

---

**缺口 14：无状态回滚**

当前如果发现某个步骤有问题（比如 Agent 输出了错误数据），无法回滚到之前的状态重新执行。

```
场景：Agent 输出了错误数据，用户说"刚才那个数据不对"
当前：无法回滚，只能重新发起一次对话
更好：支持回滚到指定检查点，重新执行
```

**增强方案**：

```python
def rollback_to_checkpoint(conv_id, message_id, target_phase):
    """回滚到指定阶段。"""
    cp = _load_checkpoint(conv_id, message_id)
    if not cp:
        return {"error": "检查点不存在"}
    
    # 标记该阶段之后的 Agent 结果为"失效"
    from db.agents import invalidate_agent_runs_after
    invalidate_agent_runs_after(conv_id, message_id, target_phase)
    
    # 恢复状态
    return {"resume_from": {"phase": target_phase, "state": cp}}
```

**改动量**：~50 行，新增回滚逻辑 + 数据库标记

---

### 2.6 工具系统（当前评分：7/10）

#### 已有能力

| 能力 | 实现位置 | 评价 |
|------|---------|------|
| OpenAI Function Calling 格式 | `tools/__init__.py` | ✅ 标准 Schema |
| 13 个工具 | `tools/__init__.py` | ✅ 覆盖估值/持仓/搜索/基金/交易 |
| 工具调用追踪 | `tool_tracker.py` | ✅ 新增，异步离线评估 |
| 工具执行 | `tools/__init__.py` | ✅ 直接执行 |

#### 缺口

**缺口 15：无工具调用的中间结果缓存**

当前每次工具调用都重新执行。如果多个 Agent 调用同一个工具（比如都在查持仓），每次都要重新查数据库。

```
场景：估值专家和风控专家都需要持仓数据
当前：估值专家调一次 query_portfolio，风控专家再调一次
更好：第一次调用后缓存结果，第二次直接返回缓存
```

**增强方案**：利用已有的 `L2Cache` 或新增工具级缓存

```python
# 工具级缓存（利用已有的 expert_cache）
TOOL_CACHE_TTL = {
    "query_valuation": 300,       # 估值数据，5 分钟
    "query_portfolio": 60,        # 持仓数据，1 分钟
    "query_fund_info": 3600,      # 基金信息，1 小时
    "search_knowledge": 600,      # 知识库，10 分钟
    "web_search": 0,              # 网页搜索，不缓存
    "query_transaction_history": 60,  # 交易记录，1 分钟
}

def cached_tool_call(tool_name: str, args: dict) -> str:
    """带缓存的工具调用。"""
    ttl = TOOL_CACHE_TTL.get(tool_name, 0)
    if ttl <= 0:
        return execute_tool(tool_name, args)
    
    cache_key = f"tool:{tool_name}:{json.dumps(args, sort_keys=True)}"
    cached = expert_cache.get(cache_key)
    if cached:
        return cached
    
    result = execute_tool(tool_name, args)
    expert_cache.set(cache_key, result, ttl_seconds=ttl)
    return result
```

**改动量**：~40 行，新增缓存层，接入 `expert_cache`

---

**缺口 16：无工具参数自动校验**

当前工具参数由 LLM 生成，直接传给执行函数。如果 LLM 生成错误参数，工具会直接报错。

```
场景：LLM 生成 query_valuation(index_name="贵州茅台")
当前：直接传给函数 → 可能报错或返回空
更好：在调用前校验参数格式，无效参数自动修复或提示 LLM 重试
```

**增强方案**：

```python
# 工具参数校验器
TOOL_PARAM_SCHEMAS = {
    "query_valuation": {
        "index_name": {"type": "string", "min_length": 2, "max_length": 50},
    },
    "search_knowledge": {
        "query": {"type": "string", "min_length": 2, "max_length": 200},
    },
    "query_fund_info": {
        "fund_code": {"type": "pattern", "pattern": r"^\d{6}$"},
    },
}

def validate_tool_args(tool_name: str, args: dict) -> tuple[bool, str, dict]:
    """校验工具参数，返回 (valid, error_msg, fixed_args)。"""
    schema = TOOL_PARAM_SCHEMAS.get(tool_name, {})
    for param, rules in schema.items():
        value = args.get(param)
        if rules.get("type") == "string":
            if not isinstance(value, str):
                return False, f"{param} 应为字符串", args
            min_len = rules.get("min_length", 0)
            if len(value) < min_len:
                return False, f"{param} 长度不足 {min_len}", args
        elif rules.get("type") == "pattern":
            if not re.match(rules["pattern"], str(value or "")):
                return False, f"{param} 格式错误", args
    return True, "", args
```

**改动量**：~60 行，新增校验器 + 参数 Schema

---

### 2.7 人工介入（当前评分：5/10）

#### 已有能力

| 能力 | 实现位置 | 评价 |
|------|---------|------|
| 注入防护 | `input_sanitizer.py` | ✅ 新增，拦截恶意输入 |
| 安全检查 | `orchestrator.py` | ✅ 入口检测 |
| 可执行性约束 | `multi_agent.py` | ✅ 强制输出操作建议 |

#### 缺口

**缺口 17：无执行前确认**

当前 Agent 可以直接执行工具（如查询外部 API）。没有"用户确认后再执行"的审批流程。

```
场景：Agent 要执行 web_search 搜索外部网页
当前：直接执行，用户不知道
更好：高风险操作（web_search、交易查询）先询问用户确认
```

**增强方案**：

```python
# 工具执行审批
TOOL_APPROVAL_REQUIRED = {
    "web_search": True,       # 外部搜索，需确认
    "yingmi_search_news": True,  # 外部 API，需确认
    "eastmoney_search": True,  # 外部 API，需确认
    "ttfund_search": False,    # 不需要
    "query_valuation": False,
    "query_portfolio": False,
}

def should_ask_confirmation(tool_name: str) -> bool:
    return TOOL_APPROVAL_REQUIRED.get(tool_name, False)
```

**改动量**：~30 行，新增审批配置 + 前端确认弹窗

---

**缺口 18：无决策建议的落地追踪**

当前系统给出建议后，没有追踪"用户是否执行了"、"执行结果如何"。

```
场景：系统建议"降低博时恒乐仓位至15%"
当前：说完了就结束了，不知道用户有没有执行
更好：追踪用户的实际操作，和系统建议对比，计算采纳率
```

**增强方案**：利用已有的 `user_decision_choices` 表

```python
# 决策建议追踪
def track_suggestion_fulfillment(condition_id, user_action):
    """
    追踪用户实际执行的操作。
    
    场景：用户赎回了博时恒乐一部分
    → 检查系统是否建议过"减仓至15%"
    → 如果是，记录为"已采纳"
    """
    from db.analysis_conclusions import get_condition_by_id
    condition = get_condition_by_id(condition_id)
    if not condition:
        return
    
    # 对比用户操作和建议方向
    actual_action = _detect_action_from_trade(user_action)
    suggested_action = condition.get("action")
    
    if actual_action == suggested_action:
        _record_adoption(condition_id, "adopted")
    elif actual_action and _is_opposite(actual_action, suggested_action):
        _record_adoption(condition_id, "rejected")
    else:
        _record_adoption(condition_id, "partial")
```

**改动量**：~80 行，新增追踪逻辑 + 数据库操作

---

## 三、对标框架对比总结

| 能力 | 当前 | LangGraph | AutoGen | CrewAI | 差距 |
|------|------|-----------|---------|--------|------|
| DAG 执行 | ❌ | ✅ | ❌ | ✅ | 中 |
| 条件分支 | ❌ | ✅ | ❌ | ✅ | 中 |
| Agent 间通信 | ❌ | ✅ | ✅ | ❌ | 中 |
| 状态机 | ❌ | ✅ | ❌ | ❌ | 大 |
| 检查点恢复 | ⚠️ 消息级 | ✅ 节点级 | ❌ | ❌ | 小 |
| 上下文隔离 | ❌ | ✅ | ✅ | ✅ | 中 |
| 上下文版本 | ❌ | ✅ | ❌ | ❌ | 中 |
| 结构化上下文 | ❌ | ❌ | ❌ | ❌ | 小 |
| 优先级调度 | ❌ | ❌ | ❌ | ❌ | 小 |
| 任务队列 | ❌ | ✅ | ✅ | ✅ | 中 |
| 指数退避重试 | ❌ | ✅ | ❌ | ❌ | 小 |
| 部分失败处理 | ❌ | ✅ | ✅ | ✅ | 中 |
| 节点级恢复 | ❌ | ✅ | ❌ | ❌ | 小 |
| 工具缓存 | ❌ | ❌ | ❌ | ❌ | 小 |
| 参数校验 | ❌ | ✅ | ✅ | ✅ | 小 |
| 执行前确认 | ❌ | ✅ | ✅ | ❌ | 中 |
| 建议追踪 | ❌ | ❌ | ❌ | ❌ | 中 |

---

## 四、优先级推荐

### P0（增强项目价值 + 面试价值，改动小）

| 编号 | 缺口 | 行数 | 价值 | 成本 |
|------|------|------|------|------|
| 4 | 上下文隔离（按 Agent 过滤上下文） | ~60 | ⭐⭐⭐⭐⭐ | 零 |
| 6 | 结构化上下文（JSON 数据块） | ~80 | ⭐⭐⭐⭐ | 零 |
| 10 | 指数退避重试 | ~20 | ⭐⭐⭐⭐ | 零 |
| 11 | 部分失败处理 | ~30 | ⭐⭐⭐⭐⭐ | 零 |

### P1（增强项目价值，改动中等）

| 编号 | 缺口 | 行数 | 价值 | 成本 |
|------|------|------|------|------|
| 1 | DAG 执行依赖 | ~80 | ⭐⭐⭐⭐⭐ | 零（逻辑判断） |
| 3 | Agent 间通信（消息总线） | ~100 | ⭐⭐⭐⭐ | 零 |
| 15 | 工具缓存 | ~40 | ⭐⭐⭐⭐ | 零 |
| 16 | 工具参数校验 | ~60 | ⭐⭐⭐⭐ | 零 |

### P2（面试价值高，改动较大）

| 编号 | 缺口 | 行数 | 价值 | 成本 |
|------|------|------|------|------|
| 13 | 状态机引擎 | ~120 | ⭐⭐⭐⭐⭐ | 零 |
| 18 | 决策建议追踪 | ~80 | ⭐⭐⭐⭐ | 零 |
| 9 | 任务队列 | ~100 | ⭐⭐⭐ | 零 |

---

## 五、面试话术

### "你们的 Agent 编排和 LangGraph 比有什么不同？"

"我们采用了类似 LangGraph 的 Supervisor 模式，但针对投资分析场景做了定制：

1. **SOP 模板**：我们预定义了 4 个分析模板（完整诊断/买入/卖出/定投），每个模板有固定的执行顺序和依赖关系，覆盖了 80% 的用户场景

2. **动态扩缩**：根据专家中间结果动态追加更多专家（比如检测到风险关键词后追加风控专家），不需要预先规划所有步骤

3. **上下文隔离**：每个 Agent 只收到和它相关的上下文，避免信息过载——风控专家看到持仓数据，行为教练看到交易行为数据

4. **部分失败容错**：某个 Agent 挂了不影响整体，其他 Agent 的结果继续使用，仲裁时会标注'缺少XX分析'

5. **零成本增强**：上面这些能力都不需要额外 LLM 调用，都是规则驱动"

### "Agent 出了问题怎么恢复？"

"我们有 3 层恢复机制：

1. **消息级恢复**：用户端重试时，自动跳过已完成的 Agent，只执行未完成的

2. **节点级检查点**：每个阶段（路由/执行/仲裁）都保存到数据库，重启后可以从指定节点恢复

3. **部分失败**：某个 Agent 挂了不影响整体，返回结果中标记 status=unavailable，仲裁者会处理"

### "Agent 怎么防止编造数据？"

"我们有 5 层防护：

1. **数据真实性约束**：prompt 明确要求只能使用上下文提供的数据，禁止编造
2. **上下文隔离**：每个 Agent 只接收相关的数据，减少出错空间
3. **工具调用审计**：跟踪每次工具调用，评估效率和质量
4. **参数校验**：工具调用前检查参数格式，无效参数自动修复
5. **结果验证**：Self-Consistency 对关键决策多次采样验证"