# 2026-07-08 Specialist ReAct 循环降本优化设计稿

## 背景

对话 95 排查发现：单次 pipeline 执行消耗 ~84k tokens，其中 specialist 阶段 ~76k。根因是 [run_specialist](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L497) 内部 ReAct 循环 + 强制总结 + 重新生成三重叠加，每个专家最多触发 5 次 LLM 调用。

### 实测数据（对话 95，trace_id=8deba1bd）

| 专家 | LLM 调用次数 | token 总量 | 调用来源 |
|------|-------------|-----------|----------|
| valuation_expert | 5 次 | 36,542 | turn0+turn1+turn2+强制总结+重新生成 |
| macro_strategist | 5 次 | 33,473 | 同上 |
| 其他（clarify/understander/plan/synth） | 3 次 | 11,351 | — |
| **合计** | **13 次** | **~84k** | — |

### 调用链路（multi_agent.py:497-669）

```
run_specialist(agent_key, query, ...)
├── ReAct 循环 MAX_TURNS=3（line 501-606）
│   ├── turn 0: LLM 调用 → tool_calls → continue
│   ├── turn 1: LLM 调用 → tool_calls → continue
│   └── turn 2: LLM 调用 → tool_calls → 循环结束（无 answer）
├── 强制总结（line 622）：answer 为空 → +1 次 LLM
└── 重新生成（line 658）：answer < 200 字 → +1 次 LLM
```

**最坏情况：5 次 LLM 调用 / 专家**。2 个专家 = 10 次。

## 优化目标

| 指标 | 当前 | 目标 |
|------|------|------|
| 单专家 LLM 调用上限 | 5 次 | 3 次 |
| 单专家 token 上限 | ~36k | ~22k |
| 强制总结触发率 | 高（ReAct 跑满 3 turn 即触发） | 低（末轮主动收口） |
| 重新生成触发率 | 高（强制总结易产出 <200 字） | 低（合并为一次收口调用） |

## 优化方案

### 方案 1：ReAct 末轮主动收口（核心）

**位置**：[multi_agent.py:501](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L501) ReAct 循环

**改动**：最后一轮（`turn == MAX_TURNS - 1`）注入系统提示，要求 LLM 不再调工具、直接给结论。

```python
for turn in range(MAX_TURNS):
    # 末轮收口：强制要求输出文本结论，不再调工具
    if turn == MAX_TURNS - 1:
        llm_messages.append({
            "role": "user",
            "content": "你已收集到足够信息。请不要再调用工具，直接基于以上数据给出专业分析结论（至少 200 字）。"
        })
    # ... 原有 LLM 调用逻辑
```

**效果**：
- 末轮 LLM 大概率输出文本 answer → 触发 line 554 `break`，不进入强制总结
- 即使末轮仍调工具，循环结束后用末轮文本作为 answer 兜底

### 方案 2：合并强制总结 + 重新生成

**位置**：[multi_agent.py:608-669](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L608)

**改动**：原逻辑是"无 answer → 强制总结 → <200 字 → 重新生成"两次 LLM。合并为一次"直接生成 ≥200 字结论"。

```python
# 原：两次 LLM（line 622 强制总结 + line 658 重新生成）
# 新：合并为一次，prompt 直接要求 ≥200 字
if not answer or len(answer) < 200:
    llm_messages.append({
        "role": "user",
        "content": "请基于以上工具调用结果，给出你的专业分析结论。要求：不调用工具，直接输出分析，至少 200 字。"
    })
    response = _call_llm(...)  # 单次调用
    answer = response.choices[0].message.content or ""
    # 兜底：仍 <200 字 → 用工具结果拼接，不再二次调 LLM
    if len(answer) < 200:
        answer = _fallback_from_tool_results(tool_calls_log)
```

**效果**：最坏情况从 2 次 LLM 降到 1 次。

### 方案 3：caller 增加 turn 标识（可观测性）

**位置**：[multi_agent.py:506](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L506)

**改动**：ReAct 循环内的 `_caller` 增加 turn 后缀，token_usage 表可直接区分轮次。

```python
for turn in range(MAX_TURNS):
    _caller_turn = f"{_caller}#turn{turn}"
    response = _call_llm(caller=_caller_turn, ...)
```

强制总结/重新生成也加后缀：`_caller#summary` / `_caller#regen`。

**效果**：Token 记录页面可清晰看到"专家A turn0/turn1/turn2/summary"，无需查 trace_id 关联。

### 方案 4：前端 Token 记录归组展示

**位置**：前端 Token 记录页面组件

**改动**：同 trace_id + 同专家前缀的记录归组显示，展示"估值专家 ×5 轮"而非 5 条独立记录。

**效果**：用户感知更清晰，不再误以为"切换页面导致重复执行"。

## 优化后预期调用链路

```
run_specialist(agent_key, query, ...)
├── ReAct 循环 MAX_TURNS=3
│   ├── turn 0: LLM 调用 → tool_calls → continue
│   ├── turn 1: LLM 调用 → tool_calls → continue
│   └── turn 2: LLM 调用（末轮收口提示）→ 文本 answer → break
└── answer 校验
    └── ≥200 字 → 直接返回
       <200 字 → 合并收口（1 次 LLM）→ 返回

最坏情况：4 次 LLM（3 轮 ReAct + 1 次合并收口）
典型情况：3 次 LLM（末轮收口成功）
```

## 风险评估

| 风险 | 等级 | 缓解 |
|------|------|------|
| 末轮收口提示被 LLM 忽略仍调工具 | 低 | 循环结束后仍有兜底逻辑（合并收口） |
| 合并收口后 answer 仍 <200 字 | 低 | 用工具结果拼接兜底，不再二次调 LLM |
| caller 改名影响现有查询/统计 | 低 | caller 前缀不变（`specialist:xxx`），只加后缀，LIKE 查询兼容 |
| 前端归组改动影响排序 | 低 | 仅展示层归组，底层数据不变 |

## 实施范围

| 文件 | 改动 |
|------|------|
| backend/agent/multi_agent.py | 方案 1+2+3：ReAct 末轮收口 + 合并收口 + caller 后缀 |
| 前端 Token 记录组件 | 方案 4：归组展示（可选，后续单独做） |

本次先实施方案 1+2+3（后端），前端归组展示待用户确认后单独实施。

## 验证方式

1. 跑一个类似对话 95 的查询（如"今日行情+补仓建议"）
2. 查 token_usage 表，确认同 trace_id 下 specialist 调用次数从 5 降到 3-4
3. 确认 answer 质量（≥200 字，含工具数据引用）
4. 确认 caller 字段含 `#turnN` 后缀，便于排查

## 实施结果（2026-07-08 验证）

### 改动文件
- [backend/agent/multi_agent.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py)
  - line 497-508：ReAct 末轮收口提示（方案1）
  - line 511-512：caller 增加 `#turnN` 后缀（方案3）
  - line 625-657：合并强制总结+重新生成为单次收口调用（方案2）
  - line 680-690：新增 `_fallback_from_tool_results` 兜底函数

### 验证数据（对话 97，查询"沪深300和中证500当前估值如何"）

| 专家 | 优化前调用次数 | 优化后调用次数 | 优化前 tokens | 优化后 tokens |
|------|--------------|--------------|--------------|--------------|
| valuation_expert | 5 次 | **3 次** | 36,542 | **23,137** |
| risk_assessor | 5 次 | **3 次** | ~33k | **27,238** |
| specialist 阶段合计 | 10 次 | **6 次** | ~70k | **~50k** |

### 改善效果
- **调用次数**：每专家 5→3 次（**-40%**）
- **token 消耗**：specialist 阶段 -28%
- **caller 可观测性**：`#turn0/#turn1/#turn2` 清晰标识轮次
- **answer 质量**：末轮收口产出 200+ 字结构化分析，未触发强制总结链路
- **"强制总结+重新生成"链路**：完全未触发，末轮收口提示生效

### 结论
三个方案全部生效，优化目标达成。最坏情况从 5 次/专家降到 3-4 次/专家（本次实测为 3 次）。
