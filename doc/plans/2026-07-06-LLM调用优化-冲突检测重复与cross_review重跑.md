# 设计稿：LLM 调用优化 — 冲突检测重复 + cross_review 重跑 + ReAct 膨胀

> **创建时间**：2026-07-06
> **背景**：对话 ID 87（用户问"加减仓操作"）单次提问触发 **31 次 LLM 调用**。trace_id=`84ad89ab-fd1`。分析 token_usage 表发现 3 个核心浪费点。

---

## 一、问题诊断

### 问题 1：conflict_detect 重复调用（明确浪费）

**现象**：对话 87 中 `conflict_detect` 被调用 2 次，prompt_tokens 完全相同（7948），间隔 87 秒。

**根因**：
- [orchestrator.py:3788](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py#L3788) `_stream_handle_no_tool_calls` 入口处调一次 `detect_conflicts_smart` → 决定是否做 cross_review
- [orchestrator.py:3877](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py#L3877) cross_review 完成后又调一次 `detect_conflicts_smart` → 用于最终 answer 事件
- [orchestrator.py:1009](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py#L1009) `detect_conflicts_llm` 内部 `original_results = [sr for sr in specialist_results if not sr.get("is_cross_review")]` 会过滤掉 cross_review 结果
- **结果**：两次调用的输入完全相同（都是原始专家列表），输出必然相同 → 第 2 次是纯浪费

**影响**：每次复杂对话多 1 次 LLM 调用（~14000 tokens）。

### 问题 2：cross_review 重跑完整 ReAct 循环（最大开销）

**现象**：cross_review 启用时，每个专家会再产生 2-3 次 LLM 调用（含工具调用）。3 个专家 cross_review = 6-9 次额外调用。

**根因**：
- [orchestrator.py:3825-3829](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py#L3825-3829) `_review_single` 调用 `run_specialist_with_context(max_turns=2)`
- [multi_agent.py:637](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L637) `for turn in range(max_turns)` 会执行完整 ReAct 循环（LLM → 工具 → LLM → 工具 → LLM 总结）
- 交叉审阅的目的是"看 peer 分析后给出意见"，不需要重新调工具查数据（Phase A 已查过）

**影响**：cross_review 启用时，LLM 调用次数接近翻倍。

### 问题 3：ReAct 循环 context 膨胀

**现象**：risk_assessor 首轮 5 次 LLM 调用的 prompt_tokens：`8256 → 10042 → 11531 → 11981 → 12008`，每轮增长 ~1500 tokens。

**根因**：
- [multi_agent.py:456-457](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L456-457) 工具结果截断到 3000 字符后追加到 `llm_messages`
- 每轮 ReAct 把上一轮工具结果塞回 context，prompt 持续膨胀
- 多轮调用不仅多花调用费，单次 prompt 也越来越贵

**影响**：4 轮 ReAct 的总 prompt_tokens ≈ 8256+10042+11531+11981 = 41810，若压缩到 1500 字符可降到 ~30000。

---

## 二、修复方案

### 修复 1：conflict_detect 结果缓存（零风险）

**思路**：在 `_stream_handle_no_tool_calls` 中，第一次调用 `detect_conflicts_smart` 后把结果存入局部变量 `conflicts_cached`。后续若 specialist_results 未发生实质变化（仅追加了 cross_review 结果），直接复用缓存。

**判断"未发生实质变化"的方式**：
- 记录第一次调用时原始专家列表的签名：`_signature = tuple(sorted(sr.get("agent_key","") for sr in specialist_results if not sr.get("is_cross_review")))`
- 第二次调用前重新计算签名，若相同则复用缓存
- 若不同（例如仲裁追加结果），才重新调用

**改动范围**：仅 [orchestrator.py:3788-3934](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py#L3788-3934) 之间，局部变量传递，不改变函数签名。

**预期节省**：每次复杂对话 -1 次 LLM 调用（~14000 tokens）。

### 修复 2：cross_review 改为单轮"分歧意见"（中等改动）

**思路**：新增 `run_cross_review_opinion()` 函数，替代 `run_specialist_with_context()`：
- **不调用任何工具**（Phase A 已查过数据，cross_review 只需对比观点）
- 单次 LLM 调用，输入 = 自己的 Phase A 分析 + peer 分析摘要
- 输出结构化 JSON：`{"agreements": [...], "disagreements": [...], "additions": [...]}`

**新函数签名**：
```python
def run_cross_review_opinion(
    agent_key: str,
    query: str,
    self_analysis: str,        # 自己 Phase A 的分析
    peer_analyses: dict,        # {agent_key: analysis_text}
    trace_id: str = "",
    model: str = None,
) -> dict:
    """单轮 LLM 调用，输出认同/质疑/补充意见。无工具调用。"""
```

**输出格式**：
```python
{
    "agent_key": agent_key,
    "agent": "...",
    "icon": "...",
    "analysis": "基于 peer 分析的审阅意见文本（含认同/质疑/补充）",  # 兼容现有字段
    "opinion": {
        "agreements": ["认同点1", "认同点2"],
        "disagreements": [{"peer": "xxx", "point": "...", "reason": "..."}],
        "additions": ["补充见解1"],
    },
    "tool_calls": [],  # 空数组，保持字段兼容
    "duration_ms": ...,
    "is_cross_review": True,  # 关键：让 detect_conflicts_llm 能过滤
}
```

**prompt 设计**（system + user）：
```
system: 你是{专家名}。以下是其他专家的分析。请从你的专业视角给出审阅意见：
1. 指出你认同的观点（引用具体内容）
2. 指出你有疑问的地方（用数据或逻辑反驳）
3. 补充其他专家未覆盖的见解
输出 JSON：{"agreements":[...], "disagreements":[...], "additions":[...]}
只输出 JSON，不要其他文字。

user: 原始问题：{query}
我的 Phase A 分析：{self_analysis[:1500]}
其他专家分析：
【{peer1_name}】：{peer1_analysis[:1000]}
【{peer2_name}】：{peer2_analysis[:1000]}
```

**改动范围**：
- [multi_agent.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py) 新增 `run_cross_review_opinion()` 函数（~80 行）
- [orchestrator.py:3821-3829](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py#L3821-3829) `_review_single` 改为调用新函数
- 保留 `run_specialist_with_context` 函数（未来可能复用，不删除）

**预期节省**：每次 cross_review 从 6-9 次降到 3 次 LLM 调用（3 个专家 × 1 次）。

### 修复 3：ReAct 工具结果压缩（低风险）

**思路**：两步压缩：
1. **降低单次截断阈值**：[multi_agent.py:456](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L456) `if len(result) > 3000` 改为 `if len(result) > 1500`
2. **历史工具结果摘要**：当 `llm_messages` 中累积超过 2 个 tool 消息时，把前面的 tool 消息合并压缩为 1 条摘要消息

**历史压缩实现**：
```python
# 在 multi_agent.py run_specialist 的 for 循环开头加：
if sum(1 for m in llm_messages if m.get("role") == "tool") >= 2:
    _compress_prior_tool_messages(llm_messages)  # 把前面的 tool 消息合并成摘要

def _compress_prior_tool_messages(llm_messages: list):
    """把已累积的 tool 消息合并为一条摘要，保留最新一条原文。"""
    tool_indices = [i for i, m in enumerate(llm_messages) if m.get("role") == "tool"]
    if len(tool_indices) < 2:
        return
    # 保留最后一个 tool 消息原文，前面的合并
    keep_idx = tool_indices[-1]
    summaries = []
    for i in tool_indices[:-1]:
        msg = llm_messages[i]
        tool_name = msg.get("tool_name", "unknown")
        content = msg.get("content", "")
        summaries.append(f"[{tool_name}] {content[:200]}...")
    # 把前面的 tool 消息替换为一条摘要
    summary_msg = {
        "role": "tool",
        "tool_call_id": llm_messages[tool_indices[0]]["tool_call_id"],
        "content": f"（历史工具结果摘要）\n" + "\n".join(summaries),
    }
    # 移除前面的 tool 消息，在原位插入摘要
    for i in reversed(tool_indices[1:-1]):
        del llm_messages[i]
    llm_messages[tool_indices[0]] = summary_msg
```

**改动范围**：
- [multi_agent.py:456-457](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py#L456-457) 改截断阈值
- [multi_agent.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py) 新增 `_compress_prior_tool_messages` 函数
- 在 `run_specialist` 和 `run_specialist_with_context` 的循环开头调用

**预期节省**：4 轮 ReAct 的总 prompt_tokens 从 ~42000 降到 ~30000（约 -30%）。

---

## 三、配置开关

新增配置开关（默认开启，便于回退）：

| 配置键 | 默认值 | 说明 |
|--------|--------|------|
| `agent.conflict_detect_cache` | `true` | 冲突检测结果缓存（修复 1） |
| `agent.cross_review_opinion_mode` | `true` | cross_review 用单轮意见模式（修复 2）；false 则回退到旧 ReAct 模式 |
| `agent.react_tool_result_max_chars` | `1500` | 工具结果截断阈值（修复 3） |
| `agent.react_compress_history` | `true` | 启用历史工具结果压缩（修复 3） |

写入 [db/config.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/db/config.py) 的默认配置表。

---

## 四、实现步骤

### Step 1：修复 1 — conflict_detect 结果缓存
- 文件：[orchestrator.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py)
- 位置：`_stream_handle_no_tool_calls` 函数内（L3788-3934）
- 改动：
  1. L3788 调用后，记录 `_conflicts_signature` 和 `_conflicts_cached`
  2. L3877 和 L3934 调用前，检查签名是否一致，一致则复用
- 测试：`pytest backend/tests/test_multi_agent_optimizer.py -k conflict`

### Step 2：修复 2 — cross_review 单轮意见模式
- 文件：[multi_agent.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py)
- 改动：
  1. 新增 `run_cross_review_opinion()` 函数
  2. 文件：[orchestrator.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/orchestrator.py) `_review_single` (L3821-3829) 改为调用新函数
  3. 增加 `agent.cross_review_opinion_mode` 开关
- 测试：手动验证 cross_review 事件输出格式不变，前端能正常显示

### Step 3：修复 3 — ReAct 工具结果压缩
- 文件：[multi_agent.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/multi_agent.py)
- 改动：
  1. L456 截断阈值改为配置项 `agent.react_tool_result_max_chars`（默认 1500）
  2. 新增 `_compress_prior_tool_messages` 函数
  3. 在 `run_specialist` 和 `run_specialist_with_context` 循环开头调用
- 测试：`pytest backend/tests/test_multi_agent_optimizer.py`

### Step 4：配置项写入
- 文件：[db/config.py](file:///Users/xiaoyuer/projects/investment-analyzer/backend/db/config.py)
- 在默认配置字典中加入 4 个新开关

### Step 5：回归验证
- 重启后端，发起一个复杂对话（如"加减仓操作"）
- 查 token_usage 表，确认：
  - conflict_detect 只出现 1 次
  - cross_review 专家各只 1 次 LLM 调用
  - 专家 ReAct 单轮 prompt 增长幅度减小

---

## 五、风险评估

| 修复 | 风险 | 回退方案 |
|------|------|---------|
| 修复 1 | 极低（局部变量传递，不改变接口） | 关闭 `agent.conflict_detect_cache` |
| 修复 2 | 中等（cross_review 输出结构变化，但保持 `analysis`/`is_cross_review` 字段兼容） | 关闭 `agent.cross_review_opinion_mode` 回退到 `run_specialist_with_context` |
| 修复 3 | 低（仅压缩文本，不影响逻辑） | 关闭 `agent.react_compress_history` |

---

## 六、预期效果

以对话 87 为例（假设 cross_review 启用）：

| 指标 | 修复前 | 修复后 | 节省 |
|------|--------|--------|------|
| LLM 调用总数 | 31 | ~22 | -9 |
| conflict_detect 调用 | 2 | 1 | -1 |
| cross_review 调用 | 6-9 | 3 | -3 到 -6 |
| ReAct 总 prompt_tokens | ~42000 | ~30000 | -30% |
| 总 token 消耗 | ~180000 | ~130000 | -28% |
