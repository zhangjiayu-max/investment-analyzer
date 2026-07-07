# SSE 对话断线续接 + Channel 事件流持久化设计稿

- 日期：2026-07-08
- 范围：backend `routers/conversations.py`、`db/` 新增模块、`agent/orchestrator.py`（仅入参扩展）、frontend `ChatView.vue` + `api/index.js`
- 目标：解决"切页面/关闭页面后切回来自动重跑对话任务"的严重问题，实现真正的断线续接

---

## 1. 问题与目标

### 1.1 现状问题

当前 SSE 对话协议在 `POST /api/conversations/{conv_id}/messages/stream` 中通过生产者线程 + queue + 主协程 SSE 推送实现，客户端断开时不取消后台任务，进度通过 `_save_progress` 写入 `messages.metadata.specialist_results`。

切页面/关闭页面后切回来，会**自动重跑对话任务**而非续接已有结果，根因有二：

1. **后端重启/producer 崩溃后 `_running_agents` 内存丢失**：`/resume` 检测不到运行中任务 → 走"真正恢复"分支 → 若无已完成专家则重置 `pending` 从头重跑
2. **任务死了但状态未及时写 `failed`**：`execution_status` 仍是 `streaming`，前端切回来调 `/resume` 又触发重跑

### 1.2 目标行为

| 任务状态 | 切回来时期望行为 |
|---------|----------------|
| 还活着（心跳正常） | 续接实时流，看到剩余专家完成的动画 |
| 已完成 | 直接看到结果，不调任何流接口 |
| 已死（心跳超时/进程重启） | 显示"任务中断，点击重试"按钮，**绝不自动重跑** |
| 已取消 | 显示已取消状态 + 重试按钮 |

### 1.3 非目标

- 不改造 `orchestrate_stream` 内部逻辑（只在外层包装事件持久化）
- 不替换 `messages.metadata.specialist_results`（保留作为 message 级快照，由 channel 聚合同步生成）
- 不动历史对话数据（无 channel 的旧对话仍走 metadata 渲染）

---

## 2. 架构：双接口 + Channel 事件流

### 2.1 接口职责

| 接口 | 路径 | 作用 | 何时调用 |
|------|------|------|---------|
| chat（改造） | `POST /api/conversations/{conv_id}/messages/stream` | 发送用户消息 + 启动 producer + SSE 推送，生产者写 events 表 | 用户主动发新消息 |
| replay（新增） | `GET /api/conversations/{conv_id}/replay?channel_id=xxx&last_seq=N` | 回放续接，SSE 流 | 切回页面时检测到 channel 仍在 running |

### 2.2 整体数据流

```
用户发消息
  ↓
chat 接口
  ├─ 创建 user message + assistant 占位 message (execution_status=streaming)
  ├─ 创建 channel (channel_id=UUID, status=running, message_id=stream_msg_id)
  ├─ 启动 producer 线程
  │    ├─ 跑 orchestrate_stream
  │    ├─ 每事件: 写 stream_events (seq++) + 更新 channel.heartbeat_at + last_seq → 入 queue
  │    ├─ 5s 无事件: 单独心跳
  │    └─ 终态事件: 更新 channel.status + finished_at + 聚合 specialist_results → 同步 messages.metadata
  └─ 主协程从 queue 拉 → SSE 推客户端（断开不取消任务）
       └─ 首事件: channel_started {channel_id, message_id}

用户切回页面
  ↓
前端 GET /execution-state → 返回 channel_id, status, last_seq
  ↓
按 status 分流:
  ├─ completed / 无 channel → 直接渲染历史消息（不调 replay）
  ├─ running → 调 replay(channel_id, last_seq=0) 续接
  ├─ failed / aborted / cancelled → 渲染已有片段 + "任务中断，点击重试"按钮
  └─ chat 期间切走 → 缓存 channel_id + last_seq，切回时 replay(channel_id, last_seq=N)
```

---

## 3. 数据模型

### 3.1 新增表：`stream_channels`

一次执行尝试 = 一个 channel（对应一条 assistant message）。重试会创建新 channel，旧 channel 保留作执行历史。

```sql
CREATE TABLE IF NOT EXISTS stream_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT UNIQUE NOT NULL,             -- UUID，对外标识
    conversation_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,                 -- 关联 assistant message
    user_message_id INTEGER,                     -- 关联 user message
    trace_id TEXT,
    status TEXT NOT NULL DEFAULT 'running',      -- running/completed/failed/aborted/cancelled
    last_seq INTEGER DEFAULT 0,                  -- 已写入的最大 seq
    heartbeat_at TEXT,                           -- 生产者最近心跳时间
    started_at TEXT DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    abort_reason TEXT,                           -- aborted 时的原因
    complexity TEXT DEFAULT '',                  -- simple/medium/complex/chat
    error_message TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_stream_channels_conv ON stream_channels(conversation_id);
CREATE INDEX IF NOT EXISTS idx_stream_channels_msg ON stream_channels(message_id);
CREATE INDEX IF NOT EXISTS idx_stream_channels_status ON stream_channels(status);
```

### 3.2 新增表：`stream_events`

按 seq 记录关键事件，per-channel 单调递增。

```sql
CREATE TABLE IF NOT EXISTS stream_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    seq INTEGER NOT NULL,                        -- per-channel 单调递增，从 1 开始
    event_type TEXT NOT NULL,                    -- 见 §3.3 事件类型清单
    data_json TEXT NOT NULL,                     -- 事件 payload，JSON 字符串
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(channel_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_stream_events_channel ON stream_events(channel_id, seq);
```

### 3.3 持久化的事件类型（关键事件）

只持久化以下事件，**不存 `answer_chunk` / `reasoning_chunk`** 增量块（最终全文在 `answer` 事件里）：

| event_type | 触发时机 | data_json 关键字段 |
|-----------|---------|-------------------|
| `status` | 阶段状态文本更新 | `message` |
| `plan` | 编排计划生成 | `complexity`, `reason`, `refined_query` |
| `progress` | 进度更新 | `phase`, `phase_index`, `total_phases`, `phase_label`, `substep`, `pct` |
| `rag_sources` | RAG 检索结果 | `sources[]` |
| `title_updated` | 对话标题更新 | `title` |
| `specialist_start` | 专家开始 | `agent_key`, `agent`, `icon` |
| `specialist_done` | 专家完成 | `agent_key`, `agent`, `icon`, `analysis`, `duration_ms` |
| `cross_review_start` | 交叉审阅开始 | `agent_key`, `agent`, `icon` |
| `cross_review_done` | 交叉审阅完成 | `agent_key`, `agent`, `icon`, `analysis`, `duration_ms` |
| `answer` | 最终答案 | `content`, `specialist_results[]` |
| `done` | 任务完成 | `message_id`, `duration_ms`, `phase_timings` |
| `error` | 任务失败 | `message` |
| `cancelled` | 用户取消 | `message` |
| `user_message` | 用户消息回显 | `content` |

---

## 4. chat 接口改造（`POST /messages/stream`）

### 4.1 流程

1. 现有前置逻辑保留（防重复编排、RAG、clarification、unified_context、portfolio_facts 注入）
2. 创建 user message + assistant 占位 message（`execution_status=streaming`）
3. **创建 channel**：
   ```python
   channel_id = str(uuid.uuid4())
   create_channel(
       channel_id=channel_id,
       conversation_id=conv_id,
       message_id=stream_msg_id,
       user_message_id=user_msg_id,
       trace_id=trace_id,
       status="running",
       heartbeat_at=now(),
       complexity=complexity,
   )
   ```
4. **首事件推送 `channel_started`**（前端缓存 channel_id）：
   ```python
   yield _sse_event("channel_started", {"channel_id": channel_id, "message_id": stream_msg_id})
   ```
5. 启动 producer 线程跑 `orchestrate_stream`，改造 producer：
   - **每个事件先持久化再入 queue**：
     ```python
     seq = append_event(channel_id, event_type, data)  # 写 stream_events + 更新 channel.last_seq/heartbeat_at
     event_with_seq = {**event, "seq": seq}
     q.put(event_with_seq)
     ```
   - **心跳**：5 秒内无事件时单独写 `update_heartbeat(channel_id)`
   - **终态事件处理**：
     - `done` → `complete_channel(channel_id)` + 聚合 `specialist_results` 同步写 `messages.metadata`
     - `error` → `fail_channel(channel_id, message)` + 同步 metadata
     - `cancelled` → `cancel_channel(channel_id)` + 同步 metadata
6. 主协程从 queue 拉 → SSE 推客户端（断开不取消任务，现有逻辑保留）

### 4.2 channel_started 事件

新增事件类型，在所有其他事件之前推送，让前端缓存 `channel_id`：

```json
{"type": "channel_started", "data": {"channel_id": "abc-123", "message_id": 456}}
```

### 4.3 兼容现有简单/chat 路径

`complexity == "chat"` 和 `complexity == "simple"` 且单专家的快速路径同样走 channel 流程（创建 channel + 写 events），保证行为一致。

---

## 5. replay 接口（新增 `GET /replay`）

### 5.1 请求

```
GET /api/conversations/{conv_id}/replay?channel_id=xxx&last_seq=N
```

- `channel_id`：必填，要回放的 channel
- `last_seq`：可选，默认 0，表示客户端已收到 seq ≤ N 的事件，从 N+1 开始回放

### 5.2 响应流程

1. 查 channel，不存在 → 404
2. **检测任务死活**：
   - `status in (completed, failed, aborted, cancelled)` → 已结束，回放 `seq > last_seq` 的所有事件（含终态），关闭流
   - `status = running`：
     - `heartbeat_at` 距 now > **15 秒** → 标记 `aborted`（`abort_reason="heartbeat timeout"`），回放已有事件 + 推 `error {message: "任务中断，请点击重试"}`，关闭流
     - 心跳正常 → 回放 `seq > last_seq` 的事件 → 长连接订阅新事件（无感切换到实时流）
3. 每个回放/实时事件带 `seq` 字段：
   ```json
   {"type": "specialist_done", "data": {...}, "seq": 5}
   ```
4. 已结束 channel 回放完所有事件后，推一个 `replay_end {status: "completed|failed|aborted|cancelled"}` 事件并关闭流

### 5.3 长连接订阅实现

```python
async def event_stream():
    # 1. 回放历史事件
    events = list_events(channel_id, after_seq=last_seq)
    for ev in events:
        yield _sse_event_with_seq(ev)
    
    # 2. 如果 channel 已结束，推 replay_end 并返回
    channel = get_channel(channel_id)
    if channel["status"] != "running":
        yield _sse_event("replay_end", {"status": channel["status"]})
        return
    
    # 3. 心跳超时检测
    if heartbeat_stale(channel, threshold_sec=15):
        mark_aborted(channel_id, "heartbeat timeout")
        yield _sse_event("error", {"message": "任务中断，请点击重试"})
        yield _sse_event("replay_end", {"status": "aborted"})
        return
    
    # 4. 长连接订阅新事件（轮询 stream_events 表）
    current_seq = events[-1]["seq"] if events else last_seq
    max_polls = 1500  # 最多等待 50 分钟
    for _ in range(max_polls):
        await asyncio.sleep(2)
        # 检测断开
        if await request.is_disconnected():
            return
        # 拉新事件
        new_events = list_events(channel_id, after_seq=current_seq)
        for ev in new_events:
            yield _sse_event_with_seq(ev)
            current_seq = ev["seq"]
        # 检查 channel 状态
        channel = get_channel(channel_id)
        if channel["status"] != "running":
            yield _sse_event("replay_end", {"status": channel["status"]})
            return
        # 心跳超时检测
        if heartbeat_stale(channel, threshold_sec=15):
            mark_aborted(channel_id, "heartbeat timeout")
            yield _sse_event("error", {"message": "任务中断，请点击重试"})
            yield _sse_event("replay_end", {"status": "aborted"})
            return
    
    yield _sse_event("error", {"message": "等待任务完成超时"})
```

### 5.4 与现有 `/resume` 的关系

- 现有 `/resume` 接口标记 **deprecated**，前端不再调用（保留一段时间兼容旧前端缓存）
- `/continue` 和 `/retry-message/{message_id}` 保留，重试时创建新 assistant message + 新 channel，再走 chat 流程

---

## 6. 前端改造

### 6.1 `api/index.js` 新增

```javascript
// 回放续接 SSE 流
export function replayConversationStream(convId, channelId, lastSeq, onEvent) {
  const controller = new AbortController()
  const decoder = new TextDecoder()
  fetch(`${baseURL}/conversations/${convId}/replay?channel_id=${channelId}&last_seq=${lastSeq}`, {
    method: 'GET',
    signal: controller.signal,
    headers: { 'Accept': 'text/event-stream' }
  }).then(async (res) => {
    const reader = res.body.getReader()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 解析 SSE 事件，调用 onEvent(event)
      // ...
    }
  })
  return controller
}
```

### 6.2 `ChatView.vue` 改造

**打开对话时**（替换 `tryResumeConversation`）：

```javascript
async function tryResumeConversation(convId) {
  // 1. GET /execution-state（扩展返回 channel_id, status, last_seq）
  const state = await getConversationExecutionState(convId)
  const item = state.item
  if (!item || !item.channel_id) {
    // 无 channel（旧对话或已完成）→ 直接渲染历史消息，不调 replay
    return
  }
  
  const { channel_id, status, last_seq } = item
  
  // 2. 按 status 分流
  if (status === 'running') {
    // 任务还活着 → 调 replay 从头回放所有历史事件（前端尚未渲染任何事件）
    reconnectReplay(convId, channel_id, 0)
  } else if (status === 'completed') {
    // 已完成 → 直接渲染（不调流接口）
  } else {
    // failed / aborted / cancelled → 渲染已有片段 + 显示"任务中断，点击重试"按钮
    showRetryButton(item.message_id, status)
  }
}

function reconnectReplay(convId, channelId, lastSeq) {
  const controller = replayConversationStream(convId, channelId, lastSeq, (event) => {
    // 处理事件（同 chat 流的事件处理逻辑）
    handleStreamEvent(event)
    // 缓存最新 seq
    if (event.seq) lastSeqMap.set(channelId, event.seq)
  })
  // 切走时 abort fetch（不影响后台）
  streamControllers.set(convId, controller)
}
```

**chat 期间**：

- 监听首事件 `channel_started`，缓存 `channel_id` + 初始化 `lastSeq=0`
- 每事件更新 `lastSeq`（`lastSeqMap.set(channelId, event.seq)`）
- 切走/关闭页面时 `controller.abort()`（不影响后台任务）

**重试按钮**：

- 调现有 `/retry-message/{message_id}` 创建新 assistant message
- 再调 `/messages/stream` 发送原始查询（带 `retry_of_message_id`）走 chat 流程创建新 channel

### 6.3 扩展 `/execution-state` 返回

```python
@router.get("/api/conversations/{conv_id}/execution-state")
async def get_conversation_execution_state_api(conv_id: int):
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    item = get_latest_recoverable_assistant(conv_id)
    # 扩展：关联 channel 信息
    if item:
        channel = get_latest_channel_for_message(item["id"])
        if channel:
            item["channel_id"] = channel["channel_id"]
            item["channel_status"] = channel["status"]
            item["last_seq"] = channel["last_seq"]
    return {"item": item, "has_recoverable": bool(item)}
```

---

## 7. 心跳与 aborted 检测

### 7.1 producer 心跳

- 每个事件写入时更新 `channel.heartbeat_at`
- 5 秒内无事件时单独写一次 `update_heartbeat(channel_id)`（producer 内部计时）

### 7.2 replay 检测

- `heartbeat_at` 距 now > **15 秒** → 标记 `aborted`（`abort_reason="heartbeat timeout"`）
- 阈值 15 秒 = 心跳间隔 5 秒 × 3（容错 3 次心跳丢失）

### 7.3 启动时清理（关键）

`init_db()` 完成后调用 `cleanup_stale_channels()`：

```python
def cleanup_stale_channels():
    """进程重启后清理所有 running 但心跳超时的 channel，标记为 aborted。"""
    conn = _get_conn()
    conn.execute("""
        UPDATE stream_channels
        SET status = 'aborted',
            abort_reason = 'process restart',
            finished_at = datetime('now','localtime')
        WHERE status = 'running'
    """)
    conn.commit()
    conn.close()
```

**所有 `running` 状态的 channel 在进程重启后都标记 `aborted`**（producer 已死，不可能再产事件）。这是杜绝"切回来自动重跑"的核心保障。

---

## 8. 兼容与迁移

### 8.1 messages.metadata 关系

- `stream_events` 是 source of truth，生产者只写 events 表
- channel 完成/失败/取消时，由后台从 events 聚合 `specialist_results` → 同步写入 `messages.metadata`
- **现有读 `messages.metadata` 的代码（前端历史渲染/export/trace/重试）不改**
- `execution_status` 字段保留，作为 message 级别快照（由 channel 同步）

### 8.2 旧对话兼容

- 旧对话（无 channel 关联）仍走 metadata 渲染，不受影响
- `/resume` 接口保留但标记 deprecated，前端不再调用
- 旧前端缓存用户刷新后加载新前端，自动走新流程

### 8.3 数据清理

- 定期清理 7 天前的 `stream_events`（保留 `stream_channels` 元数据作执行历史）
- 清理脚本可挂在现有定时任务中

---

## 9. 错误处理

| 场景 | 处理 |
|------|------|
| producer 异常退出未写终态 | replay 检测心跳超时 → 标记 aborted → 前端显示重试按钮 |
| 进程重启 | init_db 后 cleanup_stale_channels 标记所有 running 为 aborted |
| channel_id 无效 | replay 返回 404 |
| replay 期间 producer 完成 | 长连接订阅检测到 status != running → 推 replay_end 关闭流 |
| 数据库写入失败 | producer 记录 warning 日志，事件入 queue 继续推 SSE（不影响用户体验，仅断线续接受影响） |
| SSE 推送失败 | 现有逻辑保留，断开不取消任务 |

---

## 10. 测试要点

1. **正常流**：发消息 → 收到 channel_started → 专家逐个完成 → done → channel.status=completed → messages.metadata 同步
2. **切走切回（任务还在跑）**：chat 期间切走 → 切回 → GET /execution-state 返回 running → 调 replay → 续接剩余事件
3. **切走切回（任务已完成）**：chat 期间切走 → 任务完成 → 切回 → GET /execution-state 返回 completed → 直接渲染，不调 replay
4. **producer 崩溃**：模拟 producer 异常退出 → 等待 15 秒 → 切回 → replay 检测心跳超时 → 标记 aborted → 前端显示重试按钮
5. **进程重启**：跑任务 → 重启后端 → 切回 → 所有 running channel 标记 aborted → 前端显示重试按钮
6. **重试**：点击重试按钮 → 创建新 assistant message + 新 channel → 走 chat 流程
7. **取消**：发消息 → 调 /cancel → channel.status=cancelled → 前端显示已取消 + 重试按钮
8. **旧对话兼容**：打开无 channel 的旧对话 → 直接渲染历史消息，不报错

---

## 11. 实施步骤（高层）

1. **DB 层**：`db/stream_channels.py` 新增模块（建表 + CRUD），`db/core.py` init_db 调用建表 + `cleanup_stale_channels`
2. **chat 接口改造**：`routers/conversations.py` 的 `send_message_stream` + 简单/chat 路径接入 channel 流程
3. **replay 接口**：`routers/conversations.py` 新增 `GET /replay`
4. **execution-state 扩展**：返回 channel_id/status/last_seq
5. **前端 API**：`api/index.js` 新增 `replayConversationStream`
6. **前端 ChatView**：替换 `tryResumeConversation` 为 replay 流程，chat 期间缓存 channel_id + last_seq，新增重试按钮逻辑
7. **测试**：覆盖第 10 节所有场景

详细实施计划由 writing-plans skill 生成。
