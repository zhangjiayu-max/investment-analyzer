# 多 Agent 过度调度修复方案 v2

> 日期：2026-06-30
> 目标：在不削弱核心分析质量的前提下，系统性收敛多 Agent 过度调度，降低一次问答中的专家数量、交叉审阅次数和仲裁触发率。

## 1. 结论摘要

原方案命中了“专家太多、交叉审阅太频繁、仲裁过度触发”这几个主要症状，但还不够彻底。问题的根源不只是“专家名单太长”，而是：

1. 路由阶段给出的专家建议，没有变成 Orchestrator 的硬边界。
2. stream 与非 stream 两条链路的调度规则不一致。
3. 冲突判断存在多套轻量逻辑，容易出现“一个地方说有冲突，另一个地方说没冲突”。
4. 动态追加专家仍偏自动执行，预算控制不够前置。

因此本次增强版方案的核心不是继续加更多 if 截断，而是引入一层统一的“调度预算闸门”：

1. 路由结果不再只是推荐名单，而是本轮允许调用的专家白名单。
2. Orchestrator 只暴露白名单工具，不再向 LLM 暴露全部专家。
3. 交叉审阅和仲裁统一基于同一份冲突检测结果触发。
4. 动态追加改为“提案制”，默认不自动执行。
5. stream / 非 stream 共用同一套预算与触发策略。

## 2. 背景与问题定义

典型问题如：

```text
结合今日市场行情以及今日持仓盈亏分析，哪些可以加仓，009051可以加仓了吗
```

这类问题本质是“单标的/小范围持仓的操作建议”，通常 2 到 3 个专家已足够，例如：

1. `valuation_expert`：看估值和安全边际。
2. `allocation_advisor`：看仓位和组合位置。
3. `risk_assessor`：看回撤、波动和操作边界。

但当前系统可能把它放大成：

1. 初始命中多个专家。
2. Orchestrator 再次多轮 tool call。
3. Phase B 交叉审阅整轮展开。
4. Phase C 仲裁继续追加一次高成本调用。

最终形成“一个局部操作问题，被当成多方会诊级复杂决策”。

## 3. 本轮设计目标

| 目标 | 验收指标 |
|------|----------|
| 控制专家数量 | 普通加仓/减仓类问题原始专家数 ≤ 3 |
| 降低总调用数 | 典型单标的问题总 LLM 调用数下降 50% 以上 |
| 降低后处理开销 | 无实质冲突时默认跳过交叉审阅和仲裁 |
| 保持分析质量 | 结果仍包含估值依据、仓位建议、风险边界 |
| 统一执行行为 | stream / 非 stream 对同一问题的调度结果尽量一致 |
| 可观测与可回退 | 所有新规则有日志、配置和降级开关 |

非目标：

1. 不重写整个 Agent 框架。
2. 不取消深度会诊能力。
3. 不删除任何现有专家。
4. 不在本轮引入复杂机器学习排序器。

## 4. 方案对比

### 方案 A：继续补截断逻辑

做法：

1. 继续在关键词路由末尾截断。
2. 继续在 tool_calls 执行前截断。
3. 继续降低交叉审阅门槛。

优点：

1. 改动小。
2. 短期见效。

缺点：

1. 只能“事后止损”，不能“事前限权”。
2. 很容易在 stream / 非 stream 两条链路中越修越散。
3. 仍然依赖 LLM 先看到全部工具再做选择。

### 方案 B：统一调度预算闸门，推荐

做法：

1. 引入统一的 `DispatchBudget`。
2. 路由输出升级为专家白名单。
3. Orchestrator 按白名单构建工具，不再暴露全部专家。
4. 动态追加先提案，预算允许才执行。
5. 交叉审阅和仲裁基于统一冲突结果触发。

优点：

1. 解决根因最彻底。
2. 更容易统一 stream / 非 stream。
3. 可观测性更好，便于后续调优。

缺点：

1. 改动范围比简单截断更大。
2. 需要同步更新调试日志与测试样例。

### 方案 C：按场景改成固定工作流

做法：

1. “加仓/减仓”“持仓体检”“文章解读”等分别设计固定工作流。
2. 每个场景固定专家顺序和输出结构。

优点：

1. 最稳定、最可控。
2. 最适合投资决策闭环。

缺点：

1. 工程量最大。
2. 对开放式对话不够灵活。

本轮推荐采用方案 B，并保留后续向方案 C 演进的空间。

## 5. 核心设计

### 5.1 引入统一调度预算对象

新增统一预算概念 `DispatchBudget`，覆盖整个调度周期。

建议字段：

```python
{
    "mode": "quick|standard|decision|deep",
    "max_initial_specialists": 1|2|3|5,
    "max_total_specialists": 1|2|3|5,
    "max_tool_calls_per_turn": 1|1|2|3,
    "max_turns": 1|2|3|4,
    "allow_dynamic_spawn": False|True,
    "allow_cross_review": False|True,
    "allow_arbitration": False|True,
}
```

作用：

1. 路由阶段约束初始专家数量。
2. tool-call 阶段约束单轮可调用工具数。
3. 动态追加阶段约束是否允许新增专家。
4. 后处理阶段约束是否允许交叉审阅和仲裁。

这样所有“要不要再调一个专家”的判断，都能回到同一个预算对象上，而不是散落在各个分支里。

### 5.2 路由结果升级为专家白名单

当前问题不是只命中了多少专家，而是这些专家没有成为硬边界。

增强方案：

1. Smart Router 或关键词路由产出 `allowed_specialists`。
2. `allowed_specialists` 进入本轮上下文，作为调度白名单。
3. Orchestrator 构建工具时只暴露这些专家。
4. LLM 即便想额外调用其他专家，也拿不到对应工具。

推荐规则：

| 模式 | 默认白名单上限 |
|------|----------------|
| `quick` | 1 |
| `standard` | 2 |
| `decision` | 3 |
| `deep` | 5 |

这一步是本方案最关键的增强点，因为它把“事后截断”变成了“事前限权”。

### 5.3 专家选择从静态优先级改为场景优先级

原方案的固定优先级有价值，但仍然偏静态。增强后建议根据场景决定必保留专家。

建议规则：

| 场景 | 必保留专家 | 可选专家 |
|------|------------|----------|
| 单只基金估值 | `valuation_expert` | `fund_analyst` |
| 持仓加仓/减仓 | `allocation_advisor`, `risk_assessor` | `valuation_expert` |
| 明确操作建议 | `risk_assessor`, `counter_argument` | `valuation_expert`, `allocation_advisor` |
| 市场/政策影响 | `market_analyst` | `macro_strategist` |
| 债券与利率问题 | `market_analyst`, `allocation_advisor` | `risk_assessor` |

额外规则：

1. 只要涉及“买入/加仓/建仓/减仓/清仓”这类动作建议，优先确保 `risk_assessor` 在名单内。
2. 涉及高风险动作时，优先保留 `counter_argument`，不要因为固定排序被过早裁掉。
3. `behavior_coach` 仅在明显情绪化表达时进入白名单，不再随操作建议默认出现。

### 5.4 动态追加改为提案制

动态追加是另一个放大器，增强版不建议默认自动执行。

改造方式：

1. `_check_dynamic_spawn()` 不直接返回“立即执行的专家”，而是返回 `spawn_proposals`。
2. 每条 proposal 包含：
   - `agent_key`
   - `reason`
   - `priority`
   - `requires_budget`
   - `requires_user_confirm`
3. 只有满足以下条件才真正执行：
   - 当前总专家数未超 `max_total_specialists`
   - proposal 的 `priority` 足够高
   - 场景允许动态追加
   - 如属高成本或深度会诊，可要求用户确认

默认策略：

1. `quick` / `standard`：只记录提案，不执行。
2. `decision`：仅允许高优先级风险类追加。
3. `deep`：预算内允许执行。

### 5.5 交叉审阅与仲裁统一基于单一冲突源

当前最容易失控的地方，是“轻量分歧检测”和“完整冲突检测”可能并存。

增强建议：

1. 全系统统一由 `detect_conflicts()` 输出冲突结果。
2. `should_skip_cross_review()` 不再自己再做一套独立语义判断，而是消费 `conflicts`。
3. `should_arbitrate()` 同样只消费 `conflicts` 与 `budget`。

统一返回结构建议：

```python
{
    "detected": True,
    "severity": "low|medium|high",
    "types": ["rating", "action"],
    "items": [...],
}
```

触发规则建议：

| 后处理阶段 | 触发条件 |
|------------|----------|
| 交叉审阅 | `conflicts.detected == True` 且 `severity >= medium` |
| 仲裁 | `conflicts.detected == True` 且 `severity == high` 且模式为 `decision/deep` |

这样可以避免：

1. 方向一致却还走交叉审阅。
2. 只有轻微表述差异却触发仲裁。
3. 两条执行路径各自维护一套分歧判定。

### 5.6 统一 stream / 非 stream 调度规则

这是本轮必须补上的增强点。

统一内容：

1. 共用 `DispatchBudget` 生成逻辑。
2. 共用 `allowed_specialists` 白名单。
3. 共用 `max_tool_calls_per_turn` 限制。
4. 共用 `detect_conflicts()` 结果。
5. 共用 `should_cross_review()` / `should_arbitrate()` 判断。

建议抽出共享 helper：

1. `build_dispatch_budget(query, complexity, route_result)`
2. `build_allowed_specialists(route_result, budget)`
3. `trim_tool_calls(tool_calls, allowed_specialists, budget)`
4. `should_run_cross_review(conflicts, budget)`
5. `should_run_arbitration(conflicts, budget)`

目标不是完全消灭两个入口的差异，而是保证它们对同一问题的核心调度结果一致。

## 6. 配置设计

建议新增或统一以下配置：

| key | 默认值 | 说明 |
|-----|--------|------|
| `dispatch.mode.quick.max_initial_specialists` | `1` | 快速分析初始专家上限 |
| `dispatch.mode.standard.max_initial_specialists` | `2` | 标准分析初始专家上限 |
| `dispatch.mode.decision.max_initial_specialists` | `3` | 决策分析初始专家上限 |
| `dispatch.mode.deep.max_initial_specialists` | `5` | 深度会诊初始专家上限 |
| `dispatch.mode.quick.max_total_specialists` | `1` | 快速分析总专家上限 |
| `dispatch.mode.standard.max_total_specialists` | `2` | 标准分析总专家上限 |
| `dispatch.mode.decision.max_total_specialists` | `3` | 决策分析总专家上限 |
| `dispatch.mode.deep.max_total_specialists` | `5` | 深度会诊总专家上限 |
| `dispatch.mode.quick.max_tool_calls_per_turn` | `1` | 单轮工具调用上限 |
| `dispatch.mode.standard.max_tool_calls_per_turn` | `1` | 单轮工具调用上限 |
| `dispatch.mode.decision.max_tool_calls_per_turn` | `2` | 单轮工具调用上限 |
| `dispatch.mode.deep.max_tool_calls_per_turn` | `3` | 单轮工具调用上限 |
| `dispatch.dynamic_spawn_enabled` | `false` | 是否允许动态追加执行 |
| `dispatch.cross_review_min_severity` | `medium` | 交叉审阅最小冲突等级 |
| `dispatch.arbitration_min_severity` | `high` | 仲裁最小冲突等级 |

原则：

1. 默认保守。
2. 可配置回退。
3. 尽量避免“多个配置控制同一行为”的重叠。

## 7. 执行流程

增强后的主链路：

```text
用户输入
  -> 路由
  -> 构建 DispatchBudget
  -> 生成 allowed_specialists
  -> 仅暴露白名单工具
  -> Orchestrator tool call
  -> trim_tool_calls
  -> 执行原始专家
  -> detect_conflicts
  -> 动态追加提案
  -> 按预算决定是否执行提案
  -> 重新 detect_conflicts
  -> 交叉审阅 Gate
  -> 仲裁 Gate
  -> 最终回答 + 调度摘要
```

## 8. 日志与可观测性

这类优化不只要“少调专家”，还要能解释“为什么没调”。

建议新增调度摘要结构：

```json
{
  "mode": "decision",
  "allowed_specialists": ["valuation_expert", "allocation_advisor", "risk_assessor"],
  "executed_specialists": ["valuation_expert", "allocation_advisor", "risk_assessor"],
  "skipped_tool_calls": [
    {"agent_key": "market_analyst", "reason": "not_in_allowlist"},
    {"agent_key": "behavior_coach", "reason": "over_budget"}
  ],
  "spawn_proposals": [
    {"agent_key": "counter_argument", "reason": "action_advice", "executed": false}
  ],
  "conflicts": {
    "detected": false,
    "severity": "low"
  },
  "cross_review": false,
  "arbitration": false
}
```

建议打的关键日志：

1. 路由后白名单。
2. tool call 截断原因。
3. 动态追加提案与放弃原因。
4. 冲突检测结果。
5. 跳过交叉审阅和仲裁的具体原因。

## 9. 分阶段实施建议

### Phase 1：前置限权

1. 引入 `DispatchBudget`。
2. 路由输出升级为 `allowed_specialists`。
3. 工具构建改为只暴露白名单。
4. stream / 非 stream 都接入单轮 tool call 上限。

收益最大，风险也最可控，建议优先做。

### Phase 2：统一冲突判定与后处理 Gate

1. 统一使用 `detect_conflicts()`。
2. 交叉审阅、仲裁只消费统一冲突结果。
3. 去掉重复和分叉的轻量判断。

### Phase 3：动态追加提案制

1. 追加专家从自动执行改为提案。
2. 决策类场景允许高优先级风险追加。
3. 深度模式允许预算内追加。

### Phase 4：前端与诊断可视化

1. 区分原始专家、追加专家、交叉审阅、仲裁。
2. 暴露调度摘要，方便排查和用户理解。

## 10. 验证方案

不能只看 `agent_runs` 条数，建议至少验证以下四类指标。

### 10.1 调度规模

1. 原始专家数。
2. 总专家数。
3. 交叉审阅次数。
4. 仲裁触发率。

### 10.2 成本

1. 总 token。
2. orchestrator token。
3. specialist token。
4. 单次问答总耗时。

### 10.3 质量

针对“可以加仓吗”类问题，检查答案是否仍包含：

1. 估值判断。
2. 仓位或配置建议。
3. 风险边界或反例提示。
4. 不确定性说明。

### 10.4 一致性

同一输入分别走 stream / 非 stream，检查：

1. 原始专家集合是否一致或近似一致。
2. 是否都跳过或都触发交叉审阅。
3. 是否都跳过或都触发仲裁。

## 11. 样例预期

对于：

```text
结合今日市场行情以及今日持仓盈亏分析，哪些可以加仓，009051可以加仓了吗
```

推荐进入 `decision` 模式。

预期行为：

1. 白名单专家控制在 3 个以内。
2. 优先 `valuation_expert`、`allocation_advisor`、`risk_assessor`。
3. `market_analyst` 仅在问题明显依赖短期市场判断时进入白名单。
4. 默认不自动追加 `behavior_coach`。
5. 若无实质冲突，直接跳过交叉审阅与仲裁。

预期结果：

1. 总调用从过去的多轮多专家，收敛到 2 到 3 个原始专家。
2. 总 LLM 调用数显著下降。
3. 答案仍保留“能不能加仓”所需的核心依据。

## 12. 风险与回退

主要风险：

1. 白名单过窄，导致回答覆盖面不足。
2. 冲突判定过严，误跳过交叉审阅。
3. 动态追加过于保守，少数复杂问题分析不够深。

回退策略：

1. 保留全量工具暴露开关。
2. 保留动态追加执行开关。
3. 保留交叉审阅与仲裁独立开关。
4. 出现异常时可回退到“原始路由 + 后置截断”模式。

## 13. 最终建议

这次优化不要再沿着“多补几处截断”继续堆 patch 了，建议明确转向“统一预算闸门 + 前置白名单限权”的思路。

优先级建议：

1. 先做前置白名单和工具限权。
2. 再统一 stream / 非 stream 的调度判断。
3. 然后统一冲突检测与后处理触发。
4. 最后把动态追加改成提案制。

如果这四步做完，多 Agent 的“过度调度”问题会比单纯调阈值更稳定，也更容易长期维护。
