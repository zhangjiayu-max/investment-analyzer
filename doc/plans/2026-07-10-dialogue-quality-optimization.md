# 对话质量优化设计稿

> **日期**：2026-07-10
> **目标**：修复澄清机制可观测性缺失 + 续答路径 bug + 工具统计失真

---

## 问题清单

| 优先级 | 问题 | 根因 |
|--------|------|------|
| P0 | 续答路径 reflection/debate 传入未定义的 `query` 变量 | 上一轮加的代码用 `query` 而非 `state.refined_query` |
| P0 | refined_query 未持久化 | Phase 0 改写后未写入数据库 |
| P1 | 澄清事件不传 reason | EVENT_CLARIFICATION 缺少 reason 字段 |
| P1 | 续答 query 纯字符串拼接 | `f"{original_query} {user_answer}"` 不调 LLM |
| P1 | ttfund 工具失败但成功率误报 1.0 | quality_metrics 未把 HTTP 409 计为失败 |

---

## P0-1：修复续答路径 query 变量未定义 bug

### 问题

[pipeline.py:470/476/508](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/pipeline.py) 传入了 `query`，但 `run_pipeline_from_checkpoint` 函数体内没有 `query` 局部变量。

### 修复

将 3 处 `query` 替换为 `state.refined_query`。

**文件**：`backend/agent/pipeline.py`

| 行号 | 原代码 | 改为 |
|------|--------|------|
| 470 | `state, query, phase3_result, blackboard, trace_id` | `state, state.refined_query, phase3_result, blackboard, trace_id` |
| 476 | `state, query, phase3_result, reflection_result,` | `state, state.refined_query, phase3_result, reflection_result,` |
| 508 | `state, query, phase3_result, blackboard, trace_id` | `state, state.refined_query, phase3_result, blackboard, trace_id` |

---

## P0-2：refined_query 持久化

### 问题

Phase 0 改写后的 `refined_query` 只存在内存中，未写入数据库。`orchestration_checkpoints` 表 0 条记录，无法回溯查询改写效果。

### 修复

在 Phase 0 完成后，将 `refined_query` 和 `original_query` 通过 SSE 事件传给 conversations.py，由其写入 messages.metadata。

**文件1**：`backend/agent/pipeline.py`

在 Phase 0 完成后（line 188 EVENT_PHASE_END 之后）新增一个事件：

```python
        yield {"type": EVENT_PHASE_END, "phase": PipelinePhase.PREPROCESS.value,
               "result": {"intent": query_info.get("intent", ""),
                          "complexity": query_info.get("complexity", "medium"),
                          "targets": query_info.get("targets", [])}}

        # P0-2: 持久化 refined_query 供可观测性
        if state.refined_query != state.original_query:
            yield {
                "type": "query_refined",
                "original_query": state.original_query,
                "refined_query": state.refined_query,
                "rewrite_reason": phase0_result.get("rewrite_reason", ""),
            }
```

**文件2**：`backend/routers/conversations.py`

在 SSE 事件处理中新增 `query_refined` 分支，将 refined_query 写入消息 metadata：

```python
        elif event_type == "query_refined":
            # 持久化查询改写结果
            update_message_content_and_metadata(stream_msg_id, None, {
                "original_query": event.get("original_query"),
                "refined_query": event.get("refined_query"),
                "rewrite_reason": event.get("rewrite_reason", ""),
            })
```

---

## P1-1：澄清事件增加 reason 字段

### 问题

EVENT_CLARIFICATION 事件只有 `question/options/checkpoint`，不包含 reason。用户看不到"为什么需要澄清"。

### 修复

**文件1**：`backend/agent/query_understander.py`

1. `_UNDERSTAND_PROMPT` 增加 `clarification_reason` 字段要求

在 prompt 的 JSON 输出格式中增加：
```
"clarification_reason": "为什么需要澄清（如：问题含代词无上下文/意图模糊/缺少具体标的）"
```

2. `understand_query` 返回值增加 `clarification_reason` 字段（默认空字符串）

**文件2**：`backend/agent/pipeline.py`

EVENT_CLARIFICATION 事件增加 reason 字段：

```python
            yield {
                "type": EVENT_CLARIFICATION,
                "question": clarify_q,
                "reason": query_info.get("clarification_reason", ""),
                "options": query_info.get("clarification_options", []),
                "checkpoint": checkpoint,
            }
```

**文件3**：`backend/routers/conversations.py`

持久化 clarification 事件时保存 reason：

```python
update_message_content_and_metadata(stream_msg_id, event.get("question", "请补充更多信息"), {
    "execution_status": "clarification",
    "clarification_options": event.get("options", []),
    "clarification_reason": event.get("reason", ""),
    "clarification_checkpoint": event.get("checkpoint"),
    "trace_id": trace_id,
})
```

**文件4**：`frontend/src/composables/useStreamingState.js`

`case 'clarification'` 传递 reason：

```javascript
    case 'clarification':
      state.streamStatus = 'clarifying'
      const reason = data.reason ? `（${data.reason}）` : ''
      state.statusMessage = (data.question || '需要更多信息') + reason
      if (callbacks.onClarification) {
        callbacks.onClarification(convId, data, state)
      }
      break
```

**文件5**：`frontend/src/components/ChatView.vue`

`onClarification` 回调中保存 reason：

```javascript
lastMsg.clarification = {
  options: data.options || [],
  messageId: data.message_id,
  reason: data.reason || '',  // 新增
}
```

**文件6**：`frontend/src/components/chat/ChatMessage.vue`

澄清 UI 展示 reason（在问题文本下方、选项按钮上方）：

```html
<div v-if="msg.clarification?.reason" class="clarification-reason">
  💡 {{ msg.clarification.reason }}
</div>
```

---

## P1-2：续答 query 改为 LLM 语义融合

### 问题

[pipeline.py:404](file:///Users/xiaoyuer/projects/investment-analyzer/backend/agent/pipeline.py) 的续答改写是纯字符串拼接：

```python
state.refined_query = f"{original_query} {user_answer}".strip()
```

不调 LLM，不做语义融合，如"白酒怎么样" + "我想看估值和买卖建议" → "白酒怎么样 我想看估值和买卖建议"（机械拼接）。

### 修复

改为 LLM 语义融合，复用 `query_rewriter._rewrite_by_llm` 的思路，新增专门的续答融合函数。

**文件**：`backend/agent/query_rewriter.py`

新增函数：

```python
def fuse_clarified_query(original_query: str, user_answer: str, trace_id: str = "") -> str:
    """将原始问题与用户澄清回答用 LLM 语义融合为一个自完整查询。

    Args:
        original_query: 用户原始问题
        user_answer: 用户选择的澄清选项文本
        trace_id: 追踪 ID

    Returns:
        融合后的查询（失败时降级为字符串拼接）
    """
    if not user_answer or not user_answer.strip():
        return original_query

    # 快速路径：用户回答就是原始问题的补充信息，直接拼接即可
    # 如 original="白酒怎么样" + answer="估值和买卖建议" → 直接拼接可接受
    # 仅当原始问题含代词或回答较长时才调 LLM
    if len(user_answer) < 10 and not any(
        re.search(pat, original_query) for pat in PRONOUN_PATTERNS
    ):
        return f"{original_query} {user_answer}".strip()

    prompt = f"""请将用户的原始问题与补充回答融合为一个完整的、自包含的投资分析问题。

原始问题：{original_query}
用户补充：{user_answer}

要求：
1. 融合后的问题应包含原始问题和补充回答的全部信息
2. 语言通顺，不是机械拼接
3. 保持投资分析的专业语境
4. 只输出融合后的结果，不要解释，不要加引号"""

    try:
        from services.llm_service import _call_llm, MODEL
        resp = _call_llm(
            caller="query_fuser",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        fused = (resp.choices[0].message.content or "").strip()
        if fused and 5 < len(fused) < 300:
            fused = fused.strip("\"'""「」")
            if fused and fused != original_query:
                logger.info(
                    f"[query_fuser:{trace_id}] 融合: '{original_query}' + '{user_answer}' → '{fused}'"
                )
                return fused
    except Exception as e:
        logger.warning(f"[query_fuser:{trace_id}] LLM 融合失败，降级为拼接: {e}")

    # 降级：字符串拼接
    return f"{original_query} {user_answer}".strip()
```

**文件**：`backend/agent/pipeline.py`

替换 line 404：

```python
    # 用回答融合 query（LLM 语义融合，失败降级为拼接）
    from agent.query_rewriter import fuse_clarified_query
    state.refined_query = fuse_clarified_query(original_query, user_answer, trace_id)
    logger.info(
        f"[pipeline:{trace_id}] 澄清续答恢复: query='{original_query}' → '{state.refined_query}', "
        f"answer='{user_answer[:50]}'"
    )
```

---

## P1-3：ttfund 工具成功率统计修正

### 问题

`ttfund_index_info` 工具 6 次全部失败（HTTP 409 skill_version），但 quality_metrics 误报 `tool_success_rate: 1.0`。

### 修复

需要找到 quality_metrics 的统计逻辑，将 HTTP 4xx/5xx 错误计为失败。

**调研待做**：搜索 quality_metrics / tool_success_rate 的计算位置，确认其是否只统计异常抛出而不统计 HTTP 错误码。

---

## 执行顺序

1. P0-1（query 变量 bug，改动最小）→ 先修
2. P0-2（refined_query 持久化）→ 后端 + 前端
3. P1-1（澄清事件 reason）→ 后端 + 前端
4. P1-2（续答 LLM 语义融合）→ 后端
5. P1-3（工具统计修正）→ 调研后修复
6. 构建前端 + 重启后端验证
