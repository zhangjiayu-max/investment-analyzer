# 多 Agent 过度调度修复方案

## 背景

用户问题"结合今日市场行情以及今日持仓盈亏分析，哪些可以加仓，009051可以加仓了吗"触发了 **10 个 Agent 运行**（6 个 Phase A + 6 次交叉审阅 + 4 个 Phase C = ~16 次 LLM 调用），而「能否加仓」这种问题 2-3 个专家完全足够。

## 根因

1. **`route_to_specialists_by_keywords()` 无上限累加**：关键词匹配到"加仓+持仓+买入"等就被一波塞了 6 个专家
2. **交叉审阅硬门槛过低**：`len(specialist_results) >= 3` 无条件触发，且 `should_skip_cross_review()` 在方向一致时也不跳过
3. **仲裁忽略冲突检测**：`conflicts` 参数传入 `should_arbitrate()` 但函数体内未使用
4. **Orchestrator LLM 全量工具暴露**：所有专家作为 tool 暴露，LLM 可在同一轮调多个专家且无上限控制
5. **`max_specialists` 仅在 stream 版生效**，非 stream 版不截断

## 修复方案

所有改动在 `backend/agent/orchestrator.py` 和 `backend/agent/orchestrator_optimizer.py`。

### 1. 关键词路由上限封顶

**文件**: `backend/agent/orchestrator.py`
**函数**: `route_to_specialists_by_keywords()`
**变更**: 函数末尾加 cap 截断

```python
# 末尾新增：根据复杂度上限截断
MAX_SPECIALISTS = {
    "simple": 1,
    "medium": 2,
    "complex": 4,
}
max_allowed = MAX_SPECIALISTS.get(complexity, 4)
if len(specialists) > max_allowed:
    # 保留估值+配置+风控核心专家，移除低优先级专家
    priority = ["valuation_expert", "allocation_advisor", "risk_assessor", "market_analyst"]
    prioritized = [s for s in specialists if s in priority]
    others = [s for s in specialists if s not in priority]
    specialists = (prioritized + others)[:max_allowed]
```

### 2. 交叉审阅改为分歧检测触发

**文件**: `backend/agent/orchestrator_optimizer.py`
**函数**: `should_skip_cross_review()`
**变更**: 当专家方向一致时跳过交叉审阅

```python
@staticmethod
def should_skip_cross_review(specialist_results: list, complexity: str) -> bool:
    # 原有条件
    if len(specialist_results) < 2:
        return True
    if complexity in ("simple", "chat"):
        return True
    # 新增：方向一致时跳过
    if not _has_disagreement(specialist_results):
        return True
    return False
```

新增辅助函数：

```python
def _has_disagreement(specialist_results: list) -> bool:
    """检测专家之间是否存在方向性分歧。"""
    buy_kw = ["买入", "加仓", "建仓", "推荐买入", "可以买"]
    sell_kw = ["卖出", "减仓", "清仓", "不建议买", "不建议加"]
    holds = []
    for sr in specialist_results:
        text = (sr.get("analysis", "") or "")[:300]
        is_buy = any(kw in text for kw in buy_kw)
        is_sell = any(kw in text for kw in sell_kw)
        if is_buy:
            holds.append("buy")
        elif is_sell:
            holds.append("sell")
        else:
            holds.append("hold")
    # 既有 buy 又有 sell → 有分歧
    return "buy" in holds and "sell" in holds
```

### 3. 仲裁函数使用 conflicts 参数

**文件**: `backend/agent/orchestrator.py`
**函数**: `should_arbitrate()`
**变更**: 检查 conflicts.detected 为 True 才返回 True

```python
def should_arbitrate(complexity: str, specialist_results: list, conflicts: dict = None) -> bool:
    # ... 原有配置检查 ...
    
    # 新增：无冲突时不仲裁
    if conflicts and not conflicts.get("detected"):
        logger.info("专家无方向冲突，跳过仲裁")
        return False
    
    return True
```

### 4. Orchestrator LLM 工具调用上限

**文件**: `backend/agent/orchestrator.py`
**函数**: `orchestrate()` 主循环
**变更**: 限制每轮 tool_calls 数量

在工具调用处（line ~2018-2023）新增上限：

```python
# 限制单轮工具调用数
MAX_TOOLS_PER_TURN = {
    "simple": 1,
    "medium": 1,
    "complex": 3,
}
max_tools = MAX_TOOLS_PER_TURN.get(complexity, 3)
if len(msg.tool_calls) > max_tools:
    logger.warning(f"Orchestrator 请求 {len(msg.tool_calls)} 个工具(上限 {max_tools})，截断取前 {max_tools} 个")
    msg.tool_calls = msg.tool_calls[:max_tools]
```

### 5. 非 stream 版也应用 max_specialists 截断

**文件**: `backend/agent/orchestrator.py`
**函数**: `orchestrate()` 中 specialists 使用处
**变更**: 在路由结果应用后截断

在 line ~1720 后（`specialists = route_result.get("specialists", [])`）新增：

```python
# 按复杂度截断 specialist 数量
max_spec = context_config.get("max_specialists", 4)
if len(specialists) > max_spec:
    logger.info(f"specialists 超出上限({len(specialists)} > {max_spec})，截断")
    specialists = specialists[:max_spec]
```

### 6. 数据库配置：max_specialists_per_complexity

**文件**: `backend/db/config.py`
**变更**: DEFAULT_CONFIGS 新增

```python
('max_specialists.simple', '1', '简单任务最大专家数', 'orchestrator'),
('max_specialists.medium', '2', '中等任务最大专家数', 'orchestrator'),
('max_specialists.complex', '4', '复杂任务最大专家数', 'orchestrator'),
```

并在 `get_context_config()` 中读取，替代硬编码。

### 配置变更汇总

| key | 默认值 | 说明 |
|-----|--------|------|
| `max_specialists.simple` | `1` | 简单任务最大专家数 |
| `max_specialists.medium` | `2` | 中等任务最大专家数 |
| `max_specialists.complex` | `4` | 复杂任务最大专家数 |

### 预期效果

对于"009051 可以加仓了吗"这类问题：
- 路由阶段：从 6 个专家降为 **2-3 个**（估值+配置+风控）
- 交叉审阅：专家方向一致 → **跳过**
- 仲裁：无冲突 → **跳过**
- 总调用：从 **16 次** 降至 **2-3 次**

## 变更文件清单

| 文件 | 改动 |
|------|------|
| `backend/agent/orchestrator.py` | `route_to_specialists_by_keywords()` cap、`orchestrate()` tool call 上限、max_specialists 截断、`should_arbitrate()` conflicts 检查 |
| `backend/agent/orchestrator_optimizer.py` | `should_skip_cross_review()` 分歧检测、新增 `_has_disagreement()` |
| `backend/db/config.py` | DEFAULT_CONFIGS 新增 3 个 max_specialists.* 条目 |

## 验证方法

1. 启动后端后发同样的问题："结合今日市场行情以及今日持仓盈亏分析，哪些可以加仓，009051可以加仓了吗"
2. 检查 `agent_runs` 表记录数，预期 ≤4 个
3. 检查 orchestrator 日志：应有"跳过交叉审阅"或"跳过仲裁"日志
4. 确认结论质量不下降（答案仍包含估值+配置核心分析）
