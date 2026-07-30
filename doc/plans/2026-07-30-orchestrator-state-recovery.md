# 对话中断恢复 — 走首次对话相同入口方案

> **日期**: 2026-07-30
> **核心思路**: 恢复中断时，不走特殊的 `resume_synthesis_only`，而是构造和首次对话相同的"管理结构"（`send_message_stream` 等价路径），让 `orchestrate_stream` 继续执行。所有结构和写表自然与首次对话一致。

---

## 一、用户原始思路

> "继续的时候 恢复中断 可以构造 和首次对话时一样的manage 然后 再对话。所有结构都一样，而且写表也一样 不就可以嘛"

**解读**：
- "manage" = 首次对话的管理结构（`send_message_stream` 的执行环境：channel + events + orchestrate_stream）
- "构造和首次对话时一样的manage" = 恢复时走相同的入口，而不是特殊的恢复路径
- "所有结构都一样，写表也一样" = 因为走相同入口，结构和写表自然一致

---

## 二、现状与差距

### 2.1 首次对话路径（send_message_stream）

```
POST /messages/stream
  → 存 user 消息 + 创建 assistant 占位消息
  → 创建 stream_channel（关联 message_id）
  → 执行 clarify + RAG（并行）
  → 调用 orchestrate_stream
     → 各阶段 yield 事件
     → 每个事件 append_stream_event 持久化到 stream_events
  → 完成后 complete_stream_channel
  → 更新 messages.content + metadata
```

**写表**：messages / stream_channels / stream_events / agent_runs / messages.metadata（全套）

### 2.2 现有恢复路径（resume_synthesis_only）

```
recover_message / /resume 接口
  → 从 agent_runs 重建 specialists
  → 从 specialists 重建 blackboard
  → 调用 _phase_synthesis（pipeline 内部函数，不是 orchestrate_stream）
  → 只更新 messages.content + metadata
```

**写表**：仅 messages.content + metadata

### 2.3 差距

| 维度 | 首次对话 | 现有恢复 |
|------|---------|---------|
| 入口 | send_message_stream | resume_synthesis_only（特殊路径） |
| 编排函数 | orchestrate_stream | _phase_synthesis |
| 综合阶段 | _stream_final_synthesis（5段结构+仲裁+止盈不止损） | _phase_synthesis（简化版） |
| 内存状态 | 完整（blackboard/llm_messages/conflicts/cross_review_results） | 仅 blackboard 重建 |
| 事件流 | 完整 SSE + stream_events 持久化 | 只发 answer+done |
| 写表 | stream_channels/stream_events/agent_runs/messages.metadata | 仅 messages.content+metadata |

---

## 三、方案设计

### 3.1 核心思路

**恢复 = 走首次对话相同入口 + checkpoint 增强恢复内存状态**

```
恢复中断:
  → 复用 message_id（不重复创建 assistant 占位消息）
  → 创建新 stream_channel（与首次对话相同）
  → 从 checkpoint 加载已保存的内存状态（blackboard/llm_messages/specialist_results/conflicts）
  → 调用 orchestrate_stream（与首次对话相同）
     → resume_from 参数告知已完成专家（跳过）
     → resume_from_checkpoint 参数恢复内存状态
     → 跳过已完成专家，直接进入综合阶段
     → yield 事件 → append_stream_event 持久化（与首次对话相同）
  → complete_stream_channel
  → 更新 messages.content + metadata
```

**关键**：恢复路径走的是和首次对话**完全相同**的代码（orchestrate_stream + stream_channels + stream_events），而不是单独的 resume_synthesis_only。

### 3.2 与用户思路的对应

| 用户原话 | 方案对应 |
|---------|---------|
| "构造和首次对话时一样的manage" | 创建新 stream_channel + 调用 orchestrate_stream（相同入口） |
| "然后再对话" | orchestrate_stream 继续执行综合阶段 |
| "所有结构都一样" | 走相同代码路径，结构自然一致 |
| "写表也一样" | stream_channels/stream_events/agent_runs/messages.metadata 全套写表 |

---

## 四、详细设计

### 4.1 checkpoint 增强（唯一持久化改动）

**现有问题**：`orchestration_checkpoints` 表只在 experts_done 阶段保存，且 state_json 只含 specialist_results/all_tool_calls/arbitration_done/complexity。

**改动**：扩展 experts_done 阶段的 state_json，增加内存状态字段：

```python
# orchestrator.py 现有 _save_checkpoint 调用点（experts_done 阶段）
_save_checkpoint(conversation_id, message_id, "experts_done", {
    # ── 现有字段（保留）──
    "specialist_results": specialist_results,
    "all_tool_calls": all_tool_calls,
    "arbitration_done": arbitration_done,
    "complexity": complexity,

    # ── 新增：内存状态字段 ──
    "llm_messages": _truncate_llm_messages(llm_messages),  # 截断后持久化
    "blackboard_dict": blackboard.to_dict(),                 # Blackboard 序列化
    "conflicts": conflicts,
    "cross_review_results": cross_review_results,
    "active_plan": active_plan,

    # ── 新增：上下文字段（综合阶段需要）──
    "refined_query": refined_query,
    "prebuilt_context": prebuilt_context,
    "system_content": system_content,
    "route_result": route_result,
    "clarification": clarification,
    "context_config": context_config,
    "rag_context": rag_context,
    "trace_id": trace_id,
})
```

**大小控制**：
- `llm_messages`：每条 content 截断到 4KB，总条数限制 20 条
- `blackboard_dict`：max_entries=6，单条 ≤300 字，总量可控
- 整体 state_json 预计 50-150KB

### 4.2 Blackboard 序列化

在 `agent/infra/blackboard.py` 新增：

```python
# Blackboard 类新增方法
def to_dict(self) -> dict:
    """序列化为可 JSON 化的 dict（用于 checkpoint 持久化）。"""
    return {
        "entries": [e.to_dict() for e in self.entries],
        "max_entries": self.max_entries,
        "tokens_used_by_agent": dict(self.tokens_used_by_agent),
    }

@classmethod
def from_dict(cls, data: dict) -> "Blackboard":
    """从 dict 重建 Blackboard 实例（用于 checkpoint 恢复）。"""
    bb = cls(max_entries=data.get("max_entries", 6))
    for e_dict in data.get("entries", []):
        bb.entries.append(BlackboardEntry(**e_dict))
    bb.tokens_used_by_agent = dict(data.get("tokens_used_by_agent", {}))
    return bb
```

**注意**：`BlackboardEntry` 已有 `to_dict()`（`blackboard.py:55`），dataclass 支持直接 `**kwargs` 构造。

### 4.3 orchestrate_stream 支持完整状态恢复

#### 4.3.1 新增参数

```python
def orchestrate_stream(
    query, history, rag_context="", cancel_event=None,
    resume_from: dict | None = None,              # 现有：告知已完成专家
    resume_from_checkpoint: dict | None = None,   # 新增：完整 checkpoint 状态
    conversation_id=0, message_id=0, trace_id="",
    target_specialists=None,
):
```

#### 4.3.2 在 _stream_precheck 中处理 checkpoint 恢复

`_stream_precheck` 现有逻辑会处理 `resume_from`（告知已完成专家）。新增逻辑：若 `resume_from_checkpoint` 存在，直接返回 checkpoint 中的状态，跳过 precheck/route/build_context 阶段：

```python
def _stream_precheck(query, history, rag_context, cancel_event, resume_from,
                     resume_from_checkpoint=None, trace_id=""):
    # ── checkpoint 完整恢复：跳过 precheck/route/build_context ──
    if resume_from_checkpoint:
        cp = resume_from_checkpoint
        logger.info(f"[trace:{trace_id}] 从 checkpoint 恢复: phase={cp.get('phase')}")
        return {
            "query": cp.get("refined_query", query),
            "refined_query": cp.get("refined_query", query),
            "budget": cp.get("budget", {"mode": "normal", "remaining": 500000}),
            "completed_specialists": set(sr.get("agent_key", "") for sr in cp.get("specialist_results", [])),
            "resumed_results": cp.get("specialist_results", []),
            "resume_message_id": None,
            "article_context": cp.get("article_context", ""),
            "checkpoint": cp,  # 透传给后续阶段
        }
    # ... 现有 precheck 逻辑 ...
```

#### 4.3.3 在 orchestrate_stream 中恢复内存状态

在 precheck 完成后，若检测到 checkpoint，恢复 blackboard/llm_messages 等内存状态：

```python
# orchestrate_stream 中，precheck_result 拿到后
checkpoint = precheck_result.get("checkpoint") if precheck_result else None

# ... route 阶段：若 checkpoint 存在，跳过路由，直接用 checkpoint 中的 specialists/complexity ...
if checkpoint:
    specialists = checkpoint.get("specialists", [])
    complexity = checkpoint.get("complexity", "medium")
    clarification = checkpoint.get("clarification", {})
    route_result = checkpoint.get("route_result", {})
    context_config = checkpoint.get("context_config", {})
    token_budget = checkpoint.get("token_budget", {})
    refined_query = checkpoint.get("refined_query", query)
else:
    # ... 现有 route 逻辑 ...

# ... build_context 阶段：若 checkpoint 存在，跳过上下文构建，直接用 checkpoint 中的 ...
if checkpoint:
    llm_messages = checkpoint.get("llm_messages", [])
    prebuilt_context = checkpoint.get("prebuilt_context", "")
    system_content = checkpoint.get("system_content", "")
else:
    # ... 现有 build_context 逻辑 ...

# 初始化 blackboard（现有代码）
blackboard = Blackboard(max_entries=6)
# 若 checkpoint 存在，从 checkpoint 恢复 blackboard
if checkpoint:
    blackboard = Blackboard.from_dict(checkpoint.get("blackboard_dict", {}))

# 恢复其他状态
specialist_results = checkpoint.get("specialist_results", []) if checkpoint else []
all_tool_calls = checkpoint.get("all_tool_calls", []) if checkpoint else []
arbitration_done = checkpoint.get("arbitration_done", False) if checkpoint else False
conflicts = checkpoint.get("conflicts", {}) if checkpoint else {}
cross_review_results = checkpoint.get("cross_review_results", []) if checkpoint else []
active_plan = checkpoint.get("active_plan") if checkpoint else None
already_called = set(sr.get("agent_key", "") for sr in specialist_results)
```

**关键**：恢复后，所有 `completed_specialists` 已在集合中，专家执行循环会自动跳过这些专家（现有机制），直接进入综合阶段。

#### 4.3.4 综合阶段自然走完整路径

由于走的是 orchestrate_stream 的完整代码路径，当所有专家都已完成（completed_specialists 包含所有专家），循环不会执行新专家，直接进入：

```python
# 现有代码（orchestrator.py:6396）
yield from _stream_final_synthesis(
    query, refined_query, specialists, specialist_results, all_tool_calls,
    llm_messages, prebuilt_context, complexity, arbitration_done,
    route_result, conflicts, perf_metrics,
    cancel_event, start_time,
    conversation_id, message_id, trace_id,
    blackboard=blackboard, plan=active_plan,
    cross_review_performed=bool(cross_review_results))
```

**综合阶段走完整路径**：含交叉审阅 + 仲裁 + 5段结构 + 止盈不止损守卫。

### 4.4 恢复入口改造

#### 4.4.1 新增 `_recover_via_orchestrate_stream`

在 `services/conv_recovery.py` 新增函数，走和首次对话相同的代码路径：

```python
def _recover_via_orchestrate_stream(msg_id: int, conv_id: int, checkpoint: dict) -> str:
    """通过走 orchestrate_stream 完整恢复（与首次对话相同入口）。

    与首次对话的 send_message_stream 的区别：
    - 不重复存 user 消息（已存在）
    - 不重新创建 assistant 占位消息（复用 msg_id）
    - 其余完全相同：创建 channel + 调用 orchestrate_stream + 事件持久化
    """
    from db.stream_channels import (
        create_stream_channel, append_stream_event, complete_stream_channel,
    )
    from agent.core.orchestrator import orchestrate_stream

    trace_id = checkpoint.get("trace_id") or f"resume-{msg_id}"
    complexity = checkpoint.get("complexity", "medium")

    # 1. 创建新 stream_channel（与首次对话相同）
    channel_id = create_stream_channel(
        conversation_id=conv_id, message_id=msg_id,
        trace_id=trace_id, complexity=complexity,
    )
    logger.info(f"[recover] msg {msg_id} 创建新 channel {channel_id}（走 orchestrate_stream）")

    # 2. 调用 orchestrate_stream，消费事件并持久化（与首次对话相同）
    answer = ""
    try:
        for event in orchestrate_stream(
            query=checkpoint.get("refined_query", ""),
            history=[],  # 恢复场景不需要历史（llm_messages 已在 checkpoint 中）
            rag_context=checkpoint.get("rag_context", ""),
            resume_from={"message_id": msg_id},  # 告知恢复模式
            resume_from_checkpoint=checkpoint,   # 完整 checkpoint 状态
            conversation_id=conv_id, message_id=msg_id, trace_id=trace_id,
        ):
            if not isinstance(event, dict):
                continue
            evt_type = event.get("type", "")
            evt_data = {k: v for k, v in event.items() if k != "type"}

            # 持久化到 stream_events（与首次对话相同）
            append_stream_event(channel_id, evt_type, evt_data)

            # answer 事件：写回 message
            if evt_type == "answer":
                answer = event.get("content", "")

        # 3. 标记 channel 完成（与首次对话相同）
        complete_stream_channel(channel_id)

        # 4. 写回 message（与首次对话相同）
        if answer:
            _apply_recovery(conn, msg_id, answer, has_expert_results=True)
            logger.info(f"[recover] msg {msg_id} 恢复成功（走 orchestrate_stream，answer len={len(answer)})")
        return answer

    except Exception as e:
        logger.warning(f"[recover] msg {msg_id} orchestrate_stream 恢复失败: {e}")
        return ""
```

#### 4.4.2 `recover_message` 改造

```python
def recover_message(message_id: int) -> str:
    """恢复单条中断消息。"""
    # ... 现有占位符检查 ...

    # 1. 优先尝试 checkpoint 完整恢复（走 orchestrate_stream，与首次对话相同入口）
    checkpoint = _load_latest_checkpoint(conv_id, message_id)
    if checkpoint and checkpoint.get("phase") == "experts_done":
        answer = _recover_via_orchestrate_stream(message_id, conv_id, checkpoint)
        if answer:
            return answer
        # 失败则降级

    # 2. 降级：现有 resume_synthesis_only（无 checkpoint 或 checkpoint 恢复失败）
    runs = ...
    if runs:
        resume_result = resume_synthesis_only(msg_id, conv_id)
        ...

    # 3. 最终降级：标记中断
    ...
```

#### 4.4.3 `/resume` 接口改造

`conversations.py` 中 `/resume` 接口的"优先路径"改为走 checkpoint 恢复：

```python
# 现有：检测到占位符 + success agent_runs → 走 resume_synthesis_only
# 改为：检测到占位符 + checkpoint → 走 _recover_via_orchestrate_stream
if _content.startswith("⏳"):
    checkpoint = _load_latest_checkpoint(conv_id, last_assistant_msg["id"])
    if checkpoint and checkpoint.get("phase") == "experts_done":
        # 走 orchestrate_stream 完整恢复（与首次对话相同）
        from services.conv_recovery import _recover_via_orchestrate_stream
        answer = _recover_via_orchestrate_stream(last_assistant_msg["id"], conv_id, checkpoint)
        if answer:
            # 返回 SSE 流（与首次对话相同的事件序列）
            async def _resume_stream():
                # 从 stream_events 回放所有事件
                from db.stream_channels import list_events
                events = list_events(channel_id, last_seq=0)
                for evt in events:
                    yield f"data: {json.dumps({'type': evt['event_type'], **json.loads(evt['data_json'])})}\n\n"
            return StreamingResponse(_resume_stream(), media_type="text/event-stream")
    # 无 checkpoint → 降级到现有 resume_synthesis_only
    ...
```

### 4.5 前端兼容

**无需前端改动**：
- 恢复时创建新 channel，前端通过现有 `replay` 机制恢复显示
- 事件类型与首次对话完全一致（specialist_done/answer/done 等）

---

## 五、实施步骤

### Phase 1：基础能力建设

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1.1 | `agent/infra/blackboard.py` | 新增 `Blackboard.to_dict()` 和 `Blackboard.from_dict()` |
| 1.2 | `agent/core/orchestrator.py` | 扩展 experts_done 阶段 checkpoint 保存内容（增加 llm_messages/blackboard_dict/conflicts/cross_review_results/上下文字段） |
| 1.3 | `agent/core/orchestrator.py` | 新增 `_load_latest_checkpoint(conv_id, message_id)` 替代 `_load_checkpoint` |

**验证**：首次对话后检查 `orchestration_checkpoints` 表，state_json 包含完整字段。

### Phase 2：orchestrate_stream 支持状态恢复

| 步骤 | 文件 | 说明 |
|------|------|------|
| 2.1 | `agent/core/orchestrator.py` | `orchestrate_stream` 新增 `resume_from_checkpoint` 参数 |
| 2.2 | `agent/core/orchestrator.py` | `_stream_precheck` 支持 checkpoint 恢复（跳过 precheck，返回 checkpoint 状态） |
| 2.3 | `agent/core/orchestrator.py` | route/build_context 阶段支持 checkpoint 恢复（跳过，用 checkpoint 数据） |
| 2.4 | `agent/core/orchestrator.py` | blackboard/specialist_results/conflicts/cross_review_results 从 checkpoint 恢复 |

**验证**：构造 mock checkpoint，调用 `orchestrate_stream(resume_from_checkpoint=mock)`，确认能从 experts_done 继续执行综合阶段。

### Phase 3：恢复入口改造

| 步骤 | 文件 | 说明 |
|------|------|------|
| 3.1 | `services/conv_recovery.py` | 新增 `_recover_via_orchestrate_stream` 函数 |
| 3.2 | `services/conv_recovery.py` | `recover_message` 优先尝试 checkpoint 恢复，失败降级 |
| 3.3 | `services/conv_recovery.py` | `recover_interrupted_conversations` 同步改造 |
| 3.4 | `routers/conversation/conversations.py` | `/resume` 接口优先路径改为走 checkpoint 恢复 |

**验证**：
1. 模拟中断（专家完成后 kill 进程）
2. 重启后自动恢复，检查：
   - `stream_channels` 表有新 channel
   - `stream_events` 表有完整事件序列
   - `messages.content` 是完整综合报告（5段结构）
   - `messages.metadata` 包含 specialist_results/cross_review_results/phase_timings
3. 前端 replay 恢复显示

### Phase 4：兜底与清理

| 步骤 | 文件 | 说明 |
|------|------|------|
| 4.1 | `services/conv_recovery.py` | checkpoint 恢复失败降级到 resume_synthesis_only |
| 4.2 | `db/__init__.py` | checkpoint 表清理：7 天前记录自动删除 |

---

## 六、风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| llm_messages 过大 | 每条 content 截断 4KB，总条数限制 20 条 |
| checkpoint 恢复失败 | 降级到现有 resume_synthesis_only，保留基础功能 |
| 重复创建 agent_runs | 恢复路径跳过专家执行（completed_specialists），不会创建新 agent_runs |
| 前端 replay 兼容 | 事件类型与首次对话一致，现有 replay 无需修改 |

---

## 七、兼容性

- **数据库**: 无新表，仅扩展 `orchestration_checkpoints.state_json` 内容（向后兼容）
- **API**: 无新接口，`/resume` 内部逻辑优化
- **前端**: 无需改动
- **配置**: 无新开关

---

## 八、验收标准

1. **中断恢复走 orchestrate_stream**（不走 resume_synthesis_only），综合报告走 `_stream_final_synthesis`（5段结构+仲裁+止盈不止损）
2. **stream_events 表有完整事件序列**，前端 replay 可恢复显示
3. **messages.metadata 包含完整字段**（specialist_results/cross_review_results/phase_timings）
4. **checkpoint 表 experts_done 阶段保存完整状态**（含 blackboard_dict/llm_messages/conflicts 等）
5. **降级路径有效**：checkpoint 缺失时降级到 resume_synthesis_only
