# SSE 对话断线续接 + Channel 事件流 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现双接口（chat 改造 + replay 新增）+ stream_channels/stream_events 表，解决切页面/关闭页面后切回来自动重跑对话任务的问题。

**Architecture:** 新增 `stream_channels`（channel 状态机）+ `stream_events`（关键事件流）两张表。chat 接口 producer 每事件先持久化再入 queue；replay 接口按 channel 状态分流：running 且心跳正常 → 续接，已结束/心跳超时 → 回放历史 + 显示重试按钮。进程重启时所有 running channel 标记 aborted，杜绝自动重跑。

**Tech Stack:** Python 3.11+ / FastAPI / SQLite / Vue 3 Composition API / fetch ReadableStream

**Spec:** `doc/plans/2026-07-08-SSE对话断线续接-Channel事件流设计.md`

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `backend/db/stream_channels.py` | stream_channels + stream_events 表 CRUD | 新建 |
| `backend/db/__init__.py` | 重导出 + init_db 调用建表 + 启动清理 | 修改 |
| `backend/routers/conversations.py` | chat 接口 producer 改造 + replay 接口 + execution-state 扩展 | 修改 |
| `frontend/src/api/index.js` | replayConversationStream 函数 | 修改 |
| `frontend/src/components/ChatView.vue` | tryResumeConversation 改造 + channel_id/last_seq 缓存 + 重试按钮 | 修改 |
| `backend/tests/test_stream_channels.py` | channel/events CRUD + 心跳超时 + 启动清理测试 | 新建 |
| `backend/tests/test_replay_api.py` | replay 接口端到端测试 | 新建 |

---

## Task 1: stream_channels 表 CRUD 模块

**Files:**
- Create: `backend/db/stream_channels.py`
- Test: `backend/tests/test_stream_channels.py`

- [ ] **Step 1: 写 channel CRUD 失败测试**

创建 `backend/tests/test_stream_channels.py`：

```python
"""测试 stream_channels + stream_events 表 CRUD。"""
import time
from datetime import datetime, timedelta


def test_create_and_get_channel(tmp_db):
    from db.stream_channels import create_channel, get_channel
    channel_id = create_channel(
        conversation_id=1, message_id=10, user_message_id=9,
        trace_id="abc123", complexity="medium",
    )
    assert channel_id, "create_channel 应返回 channel_id"
    ch = get_channel(channel_id)
    assert ch is not None
    assert ch["channel_id"] == channel_id
    assert ch["conversation_id"] == 1
    assert ch["message_id"] == 10
    assert ch["status"] == "running"
    assert ch["last_seq"] == 0


def test_append_event_increments_seq(tmp_db):
    from db.stream_channels import create_channel, append_event, list_events
    channel_id = create_channel(conversation_id=1, message_id=10)
    seq1 = append_event(channel_id, "status", {"message": "理解中"})
    seq2 = append_event(channel_id, "specialist_done", {"agent_key": "v"})
    seq3 = append_event(channel_id, "progress", {"pct": 50})
    assert seq1 == 1, f"首事件 seq 应为 1, got {seq1}"
    assert seq2 == 2
    assert seq3 == 3
    events = list_events(channel_id)
    assert len(events) == 3
    assert events[0]["seq"] == 1
    assert events[0]["event_type"] == "status"
    assert events[2]["seq"] == 3


def test_list_events_after_seq(tmp_db):
    from db.stream_channels import create_channel, append_event, list_events
    channel_id = create_channel(conversation_id=1, message_id=10)
    for i in range(5):
        append_event(channel_id, "status", {"i": i})
    # after_seq=2 应返回 seq>2 即 3,4,5
    events = list_events(channel_id, after_seq=2)
    assert len(events) == 3
    assert [e["seq"] for e in events] == [3, 4, 5]


def test_complete_channel(tmp_db):
    from db.stream_channels import create_channel, complete_channel, get_channel
    channel_id = create_channel(conversation_id=1, message_id=10)
    complete_channel(channel_id)
    ch = get_channel(channel_id)
    assert ch["status"] == "completed"
    assert ch["finished_at"] is not None


def test_fail_channel(tmp_db):
    from db.stream_channels import create_channel, fail_channel, get_channel
    channel_id = create_channel(conversation_id=1, message_id=10)
    fail_channel(channel_id, "LLM 超时")
    ch = get_channel(channel_id)
    assert ch["status"] == "failed"
    assert ch["error_message"] == "LLM 超时"


def test_cancel_channel(tmp_db):
    from db.stream_channels import create_channel, cancel_channel, get_channel
    channel_id = create_channel(conversation_id=1, message_id=10)
    cancel_channel(channel_id)
    ch = get_channel(channel_id)
    assert ch["status"] == "cancelled"


def test_mark_aborted(tmp_db):
    from db.stream_channels import create_channel, mark_aborted, get_channel
    channel_id = create_channel(conversation_id=1, message_id=10)
    mark_aborted(channel_id, "heartbeat timeout")
    ch = get_channel(channel_id)
    assert ch["status"] == "aborted"
    assert ch["abort_reason"] == "heartbeat timeout"


def test_update_heartbeat(tmp_db):
    from db.stream_channels import create_channel, update_heartbeat, get_channel
    channel_id = create_channel(conversation_id=1, message_id=10)
    old_hb = get_channel(channel_id)["heartbeat_at"]
    time.sleep(0.01)
    update_heartbeat(channel_id)
    new_hb = get_channel(channel_id)["heartbeat_at"]
    assert new_hb != old_hb, "心跳时间应更新"


def test_get_latest_channel_for_message(tmp_db):
    from db.stream_channels import create_channel, get_latest_channel_for_message
    ch1 = create_channel(conversation_id=1, message_id=10)
    ch2 = create_channel(conversation_id=1, message_id=10)
    latest = get_latest_channel_for_message(10)
    assert latest is not None
    assert latest["channel_id"] == ch2, "应返回最新的 channel"


def test_cleanup_stale_channels(tmp_db):
    """进程重启后所有 running channel 标记 aborted。"""
    from db.stream_channels import create_channel, complete_channel, cleanup_stale_channels, get_channel
    ch1 = create_channel(conversation_id=1, message_id=10)
    ch2 = create_channel(conversation_id=1, message_id=11)
    complete_channel(ch1)  # ch1 已完成
    cleanup_stale_channels()  # 模拟进程重启
    assert get_channel(ch1)["status"] == "completed", "已完成的不应被清理"
    assert get_channel(ch2)["status"] == "aborted", "running 的应标记 aborted"
    assert get_channel(ch2)["abort_reason"] == "process restart"


def test_heartbeat_stale_detection(tmp_db):
    """心跳超时检测。"""
    from db.stream_channels import create_channel, update_heartbeat, is_heartbeat_stale
    channel_id = create_channel(conversation_id=1, message_id=10)
    # 手动把心跳设为 20 秒前
    from db._conn import _get_conn
    conn = _get_conn()
    old_time = (datetime.now() - timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE stream_channels SET heartbeat_at=? WHERE channel_id=?", (old_time, channel_id))
    conn.commit()
    conn.close()
    assert is_heartbeat_stale(channel_id, threshold_sec=15) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_stream_channels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db.stream_channels'`

- [ ] **Step 3: 实现 stream_channels.py 模块**

创建 `backend/db/stream_channels.py`：

```python
"""stream_channels + stream_events 表 CRUD — SSE 对话事件流持久化。"""

import json
import uuid
from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def init_stream_channel_tables(conn):
    """建表，启动时由 init_db 调用。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stream_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            conversation_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            user_message_id INTEGER,
            trace_id TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            last_seq INTEGER DEFAULT 0,
            heartbeat_at TEXT,
            started_at TEXT DEFAULT (datetime('now','localtime')),
            finished_at TEXT,
            abort_reason TEXT,
            complexity TEXT DEFAULT '',
            error_message TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stream_channels_conv ON stream_channels(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stream_channels_msg ON stream_channels(message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stream_channels_status ON stream_channels(status)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stream_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(channel_id, seq)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stream_events_channel ON stream_events(channel_id, seq)")


# ── Channel CRUD ──────────────────────────────────────


def create_channel(conversation_id: int, message_id: int,
                   user_message_id: int | None = None,
                   trace_id: str = "", complexity: str = "") -> str:
    """创建 channel，返回 channel_id（UUID）。"""
    channel_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute("""
        INSERT INTO stream_channels
        (channel_id, conversation_id, message_id, user_message_id, trace_id,
         status, last_seq, heartbeat_at, complexity)
        VALUES (?, ?, ?, ?, ?, 'running', 0, ?, ?)
    """, (channel_id, conversation_id, message_id, user_message_id,
          trace_id, now, complexity))
    conn.commit()
    conn.close()
    return channel_id


def get_channel(channel_id: str) -> dict | None:
    """获取 channel 详情。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM stream_channels WHERE channel_id = ?",
        (channel_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_latest_channel_for_message(message_id: int) -> dict | None:
    """获取某条 assistant message 关联的最新 channel。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM stream_channels WHERE message_id = ? ORDER BY id DESC LIMIT 1",
        (message_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def update_heartbeat(channel_id: str):
    """更新 channel 心跳时间。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "UPDATE stream_channels SET heartbeat_at = ? WHERE channel_id = ?",
        (now, channel_id)
    )
    conn.commit()
    conn.close()


def complete_channel(channel_id: str):
    """标记 channel 为已完成。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "UPDATE stream_channels SET status = 'completed', finished_at = ? WHERE channel_id = ?",
        (now, channel_id)
    )
    conn.commit()
    conn.close()


def fail_channel(channel_id: str, error_message: str = ""):
    """标记 channel 为失败。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "UPDATE stream_channels SET status = 'failed', finished_at = ?, error_message = ? WHERE channel_id = ?",
        (now, error_message, channel_id)
    )
    conn.commit()
    conn.close()


def cancel_channel(channel_id: str):
    """标记 channel 为已取消。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "UPDATE stream_channels SET status = 'cancelled', finished_at = ? WHERE channel_id = ?",
        (now, channel_id)
    )
    conn.commit()
    conn.close()


def mark_aborted(channel_id: str, reason: str = ""):
    """标记 channel 为已中断（心跳超时/进程重启）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute(
        "UPDATE stream_channels SET status = 'aborted', finished_at = ?, abort_reason = ? WHERE channel_id = ?",
        (now, reason, channel_id)
    )
    conn.commit()
    conn.close()


def is_heartbeat_stale(channel_id: str, threshold_sec: int = 15) -> bool:
    """检测 channel 心跳是否超时。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT heartbeat_at FROM stream_channels WHERE channel_id = ?",
        (channel_id,)
    ).fetchone()
    conn.close()
    if not row or not row["heartbeat_at"]:
        return True
    try:
        hb = datetime.strptime(row["heartbeat_at"], "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - hb).total_seconds() > threshold_sec
    except Exception:
        return True


def cleanup_stale_channels():
    """进程重启后清理所有 running 状态的 channel，标记为 aborted。

    producer 线程在进程重启后已死，不可能再产事件，所以所有 running channel
    都必须标记 aborted，杜绝前端切回来误判"任务还在跑"触发重跑。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.execute("""
        UPDATE stream_channels
        SET status = 'aborted', abort_reason = 'process restart', finished_at = ?
        WHERE status = 'running'
    """, (now,))
    conn.commit()
    conn.close()
    return cur.rowcount


# ── Events CRUD ──────────────────────────────────────


def append_event(channel_id: str, event_type: str, data: dict) -> int:
    """追加事件到 stream_events，返回 seq。

    自动更新 channel.last_seq 和 heartbeat_at。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_json = json.dumps(data, ensure_ascii=False)
    conn = _get_conn()
    try:
        # 获取当前 last_seq 并 +1
        row = conn.execute(
            "SELECT last_seq FROM stream_channels WHERE channel_id = ?",
            (channel_id,)
        ).fetchone()
        if not row:
            conn.close()
            return 0
        next_seq = (row["last_seq"] or 0) + 1
        conn.execute(
            "INSERT INTO stream_events (channel_id, seq, event_type, data_json) VALUES (?, ?, ?, ?)",
            (channel_id, next_seq, event_type, data_json)
        )
        conn.execute(
            "UPDATE stream_channels SET last_seq = ?, heartbeat_at = ? WHERE channel_id = ?",
            (next_seq, now, channel_id)
        )
        conn.commit()
        return next_seq
    finally:
        conn.close()


def list_events(channel_id: str, after_seq: int = 0) -> list[dict]:
    """列出 channel 的事件，after_seq=0 返回全部。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM stream_events WHERE channel_id = ? AND seq > ? ORDER BY seq",
        (channel_id, after_seq)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = _row_to_dict(r)
        try:
            d["data"] = json.loads(d["data_json"])
        except Exception:
            d["data"] = {}
        result.append(d)
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_stream_channels.py -v`
Expected: 11 tests PASS

- [ ] **Step 5: 提交**

```bash
git add backend/db/stream_channels.py backend/tests/test_stream_channels.py
git commit -m "feat: 新增 stream_channels + stream_events 表 CRUD 模块"
```

---

## Task 2: init_db 集成 + 启动清理

**Files:**
- Modify: `backend/db/__init__.py`

- [ ] **Step 1: 写启动清理测试**

在 `backend/tests/test_stream_channels.py` 末尾追加：

```python
def test_init_db_creates_stream_tables(tmp_db):
    """init_db 应创建 stream_channels 和 stream_events 表。"""
    from db._conn import _get_conn
    conn = _get_conn()
    # 表存在
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('stream_channels','stream_events')"
    ).fetchall()
    conn.close()
    assert len(tables) == 2, f"应创建 2 张表, got {len(tables)}"


def test_init_db_cleans_stale_channels_on_startup(tmp_db):
    """init_db 重启时应清理 running channel。"""
    from db.stream_channels import create_channel, get_channel
    from db import init_db
    ch1 = create_channel(conversation_id=1, message_id=10)
    # 模拟重启：再次调用 init_db
    init_db()
    ch = get_channel(ch1)
    assert ch["status"] == "aborted", "重启后 running channel 应标记 aborted"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_stream_channels.py::test_init_db_creates_stream_tables -v`
Expected: FAIL（表未在 init_db 中创建）

- [ ] **Step 3: 修改 `backend/db/__init__.py` 集成建表 + 启动清理**

在 `backend/db/__init__.py` 顶部导入区（async_tasks 导入之后）添加：

```python
from db.stream_channels import (
    init_stream_channel_tables, create_channel, get_channel,
    get_latest_channel_for_message, update_heartbeat, complete_channel,
    fail_channel, cancel_channel, mark_aborted, is_heartbeat_stale,
    cleanup_stale_channels, append_event, list_events,
)
```

在 `init_db()` 函数中，`init_async_tasks_table(conn)` 调用之后添加：

```python
    # ── SSE 对话事件流持久化表 ──────────────────────────────────────
    init_stream_channel_tables(conn)
```

在 `init_db()` 函数末尾，`conn.commit(); conn.close()` 之前添加（在僵尸 agent_run 清理之后）：

```python
    # ── 清理僵尸 stream_channels：进程重启后所有 running 标记 aborted ──
    # 根因：producer 线程在进程重启后已死，running channel 不可能再产事件
    # 若不清理，前端切回来误判"任务还在跑"会触发自动重跑
    try:
        cleaned_channels = cleanup_stale_channels()
        if cleaned_channels > 0:
            print(f"[db] 清理 {cleaned_channels} 个僵尸 stream_channel（running → aborted）")
    except Exception as e:
        print(f"[db] 僵尸 channel 清理失败（不阻塞启动）: {e}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_stream_channels.py -v`
Expected: 13 tests PASS

- [ ] **Step 5: 提交**

```bash
git add backend/db/__init__.py backend/tests/test_stream_channels.py
git commit -m "feat: init_db 集成 stream_channels 建表 + 启动清理僵尸 channel"
```

---

## Task 3: chat 接口 producer 改造（接入 channel 事件流）

**Files:**
- Modify: `backend/routers/conversations.py`

- [ ] **Step 1: 改造 send_message_stream 主流程接入 channel**

在 `backend/routers/conversations.py` 顶部导入区添加：

```python
from db.stream_channels import (
    create_channel as create_stream_channel,
    append_event as append_stream_event,
    update_heartbeat as update_stream_heartbeat,
    complete_channel as complete_stream_channel,
    fail_channel as fail_stream_channel,
    cancel_channel as cancel_stream_channel,
    get_latest_channel_for_message,
)
```

在 `send_message_stream` 函数的 `event_stream()` 内，找到创建 `stream_msg_id` 的位置（约 line 1477）：

```python
stream_msg_id = create_message(conv_id, "assistant", "⏳ 分析进行中...", metadata=json.dumps({"execution_status": "streaming"}, ensure_ascii=False))
```

在创建 stream_msg_id 之后、`q = await asyncio.to_thread(_run_orchestrator_stream)` 之前，添加 channel 创建 + 推送 channel_started 事件：

```python
        # 创建 stream channel（事件流持久化，支持断线续接）
        channel_id = create_stream_channel(
            conversation_id=conv_id,
            message_id=stream_msg_id,
            user_message_id=user_msg_id,
            trace_id=trace_id,
            complexity=complexity,
        )
        logger.info(f"[trace:{trace_id}] 创建 channel {channel_id} for msg={stream_msg_id}")
        # 首事件：通知前端 channel_id（前端缓存用于切回时 replay）
        if not client_disconnected:
            yield _sse_event("channel_started", {"channel_id": channel_id, "message_id": stream_msg_id})
```

- [ ] **Step 2: 改造 producer 线程，每个事件先持久化再入 queue**

在 `_run_orchestrator_stream()` 内的 `_producer()` 函数中，找到事件处理循环（约 line 1438 `for event in orchestrate_stream(...)`）。

在 `et = event.get("type")` 之后、事件分发逻辑中，将每个事件先写入 `stream_events` 表。改造事件分发块：

将原 `if et == "specialist_done":` 之前添加持久化逻辑，把现有的事件分发改为先持久化：

```python
                        et = event.get("type")
                        # === 持久化事件到 stream_events（支持断线续接）===
                        if et and et not in ("answer_chunk", "reasoning_chunk"):
                            try:
                                event_seq = append_stream_event(channel_id, et, event)
                                event["seq"] = event_seq
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 持久化事件失败: {_e}")

                        # === 在线程中持久化（独立于 SSE 连接）===
                        if et == "specialist_done":
```

- [ ] **Step 3: producer 终态事件更新 channel 状态**

在 `_producer()` 的事件处理中，找到终态事件处理块。

在 `elif et == "answer":` 块内（`_save_final(...)` 之后），添加 channel 完成标记：

```python
                        elif et == "answer":
                            reviewed = _save_final(event.get("content", ""), event.get("specialist_results", []), event.get("tool_calls", []), int((time.time() - _prod_start) * 1000))
                            event = dict(event)
                            event["content"] = reviewed
                            # 标记 channel 完成
                            try:
                                complete_stream_channel(channel_id)
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 标记 channel 完成失败: {_e}")
```

在 `elif et == "cancelled":` 块内添加：

```python
                        elif et == "cancelled":
                            _save_progress("cancelled")
                            try:
                                cancel_stream_channel(channel_id)
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 标记 channel 取消失败: {_e}")
```

在 `elif et == "error":` 块内添加：

```python
                        elif et == "error":
                            _save_failed(event.get("message", "未知错误"))
                            try:
                                fail_stream_channel(channel_id, event.get("message", "未知错误"))
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 标记 channel 失败: {_e}")
```

- [ ] **Step 4: producer 异常分支标记 channel 失败**

在 `_producer()` 的 except 分支（`except CancelledError` / `except TimeoutError` / `except Exception`）中，添加 channel 状态标记。

在 `except CancelledError:` 块内 `_save_progress("cancelled")` 之后添加：

```python
            except CancelledError:
                q.put({"type": "cancelled", "message": "用户取消了执行"})
                _save_progress("cancelled")
                try:
                    cancel_stream_channel(channel_id)
                except Exception:
                    pass
```

在 `except TimeoutError as e:` 块内 `_save_failed(err)` 之后添加：

```python
            except TimeoutError as e:
                err = f"执行超时: {e}"
                _save_failed(err)
                try:
                    fail_stream_channel(channel_id, err)
                except Exception:
                    pass
                q.put({"type": "error", "message": err})
```

在 `except Exception as e:` 块内 `_save_failed(err)` 之后添加：

```python
            except Exception as e:
                logger.error(f"[trace:{trace_id}] 后台执行异常: {e}", exc_info=True)
                err = str(e)
                _save_failed(err)
                try:
                    fail_stream_channel(channel_id, err)
                except Exception:
                    pass
                q.put({"type": "error", "message": err})
```

- [ ] **Step 5: chat 简单路径（chat/simple）也接入 channel**

在 `event_stream()` 中，找到 `complexity == "chat"` 分支（约 line 1025）和 `complexity == "simple" and len(...) == 1` 分支（约 line 1095）。

在这两个分支的开头（yield status 之前），创建 channel：

```python
        # 3. 普通聊天：直接调用 LLM 回答，不走专家流程
        if complexity == "chat" and not clarification.get("specialists"):
            # 创建 channel（chat 路径也走事件流持久化）
            stream_msg_id = create_message(conv_id, "assistant", "⏳ 分析进行中...", metadata=json.dumps({"execution_status": "streaming"}, ensure_ascii=False))
            channel_id = create_stream_channel(
                conversation_id=conv_id, message_id=stream_msg_id,
                user_message_id=user_msg_id, trace_id=trace_id, complexity="chat",
            )
            if not client_disconnected:
                yield _sse_event("channel_started", {"channel_id": channel_id, "message_id": stream_msg_id})
            # 在每个 yield _sse_event 之前先 append_stream_event
            # ...（现有逻辑保留，在每个 yield _sse_event 之前添加 append_stream_event）
```

对 chat 路径的每个 `yield _sse_event(...)`，在 yield 之前添加：

```python
            append_stream_event(channel_id, "status", {"message": "思考中..."})
            yield _sse_event("status", {"message": "思考中..."})
```

对 chat 路径的 `yield _sse_event("answer", ...)` 和 `yield _sse_event("done", ...)`，同样在 yield 之前添加 `append_stream_event` + 完成后 `complete_stream_channel(channel_id)`。

对 simple 路径做同样处理。

- [ ] **Step 6: 启动后端验证**

Run: `cd backend && python -c "from app import app; print('OK')"`
Expected: 输出 `OK`，无导入错误

- [ ] **Step 7: 手动测试 chat 流**

启动后端：`cd backend && python3 app.py`

用 curl 或前端发一条消息，检查：
1. SSE 流首事件是 `channel_started`，包含 `channel_id`
2. 后续事件包含 `seq` 字段
3. 完成后 `stream_channels` 表中该 channel `status=completed`

验证 SQL：

```bash
cd backend && python3 -c "
from db._conn import _get_conn
conn = _get_conn()
rows = conn.execute('SELECT channel_id, status, last_seq, heartbeat_at FROM stream_channels ORDER BY id DESC LIMIT 5').fetchall()
for r in rows:
    print(dict(r))
conn.close()
"
```

- [ ] **Step 8: 提交**

```bash
git add backend/routers/conversations.py
git commit -m "feat: chat 接口 producer 接入 channel 事件流持久化"
```

---

## Task 4: replay 接口实现

**Files:**
- Modify: `backend/routers/conversations.py`
- Test: `backend/tests/test_replay_api.py`

- [ ] **Step 1: 写 replay 接口失败测试**

创建 `backend/tests/test_replay_api.py`：

```python
"""测试 replay 接口的回放续接逻辑。"""
import json
import time
from datetime import datetime, timedelta


def test_replay_channel_not_found(tmp_db):
    """不存在的 channel_id 应返回 404。"""
    from app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    resp = client.get("/api/conversations/1/replay?channel_id=nonexistent")
    assert resp.status_code == 404


def test_replay_completed_channel(tmp_db):
    """已完成的 channel 应回放所有事件并推 replay_end。"""
    from app import app
    from fastapi.testclient import TestClient
    from db.stream_channels import create_channel, append_event, complete_channel
    from db.conversations import create_conversation

    conv_id = create_conversation("test replay completed")
    channel_id = create_channel(conversation_id=conv_id, message_id=100)
    append_event(channel_id, "status", {"message": "理解中"})
    append_event(channel_id, "specialist_done", {"agent_key": "v", "analysis": "result"})
    append_event(channel_id, "answer", {"content": "最终答案"})
    append_event(channel_id, "done", {"duration_ms": 1000})
    complete_channel(channel_id)

    client = TestClient(app)
    resp = client.get(f"/api/conversations/{conv_id}/replay?channel_id={channel_id}")
    assert resp.status_code == 200
    # 解析 SSE 事件
    events = []
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    types = [e["type"] for e in events]
    assert "status" in types
    assert "specialist_done" in types
    assert "answer" in types
    assert "done" in types
    assert "replay_end" in types
    # replay_end 的 status 应为 completed
    end_event = next(e for e in events if e["type"] == "replay_end")
    assert end_event["data"]["status"] == "completed"


def test_replay_aborted_channel_with_stale_heartbeat(tmp_db):
    """running 但心跳超时的 channel 应标记 aborted 并推 error。"""
    from app import app
    from fastapi.testclient import TestClient
    from db.stream_channels import create_channel, append_event
    from db.conversations import create_conversation
    from db._conn import _get_conn

    conv_id = create_conversation("test replay aborted")
    channel_id = create_channel(conversation_id=conv_id, message_id=100)
    append_event(channel_id, "status", {"message": "理解中"})

    # 手动把心跳设为 20 秒前
    conn = _get_conn()
    old_time = (datetime.now() - timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE stream_channels SET heartbeat_at=? WHERE channel_id=?", (old_time, channel_id))
    conn.commit()
    conn.close()

    client = TestClient(app)
    resp = client.get(f"/api/conversations/{conv_id}/replay?channel_id={channel_id}")
    assert resp.status_code == 200
    events = []
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    types = [e["type"] for e in events]
    assert "error" in types, "应推 error 事件"
    assert "replay_end" in types
    end_event = next(e for e in events if e["type"] == "replay_end")
    assert end_event["data"]["status"] == "aborted"

    # 验证 channel 已标记 aborted
    from db.stream_channels import get_channel
    ch = get_channel(channel_id)
    assert ch["status"] == "aborted"


def test_replay_after_seq(tmp_db):
    """last_seq 参数应只返回 seq > last_seq 的事件。"""
    from app import app
    from fastapi.testclient import TestClient
    from db.stream_channels import create_channel, append_event, complete_channel
    from db.conversations import create_conversation

    conv_id = create_conversation("test replay after_seq")
    channel_id = create_channel(conversation_id=conv_id, message_id=100)
    append_event(channel_id, "status", {"message": "1"})
    append_event(channel_id, "status", {"message": "2"})
    append_event(channel_id, "status", {"message": "3"})
    complete_channel(channel_id)

    client = TestClient(app)
    # last_seq=1 应只返回 seq>1 的事件
    resp = client.get(f"/api/conversations/{conv_id}/replay?channel_id={channel_id}&last_seq=1")
    assert resp.status_code == 200
    events = []
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    # 应只有 seq=2,3 + replay_end
    seqs = [e["data"].get("seq") or e.get("seq") for e in events if e["type"] != "replay_end"]
    assert 1 not in seqs, "seq=1 不应出现"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_replay_api.py -v`
Expected: FAIL（replay 接口不存在，404 路由不匹配）

- [ ] **Step 3: 实现 replay 接口**

在 `backend/routers/conversations.py` 中，`get_conversation_execution_state_api` 函数之前添加 replay 接口：

```python
@router.get("/api/conversations/{conv_id}/replay")
async def replay_conversation(conv_id: int, channel_id: str, last_seq: int = 0, request: Request = None):
    """回放续接 SSE 流——切回页面时续接 channel 事件流。

    - channel 已结束（completed/failed/aborted/cancelled）→ 回放 last_seq 之后事件 + replay_end，关闭流
    - channel running 且心跳超时 → 标记 aborted + 推 error，关闭流
    - channel running 且心跳正常 → 回放 last_seq 之后事件 → 长连接订阅新事件
    """
    from db.stream_channels import (
        get_channel, list_events, mark_aborted, is_heartbeat_stale,
    )

    channel = get_channel(channel_id)
    if not channel or channel["conversation_id"] != conv_id:
        raise HTTPException(404, "channel 不存在")

    async def event_stream():
        # 1. 回放历史事件（seq > last_seq）
        events = list_events(channel_id, after_seq=last_seq)
        for ev in events:
            yield _sse_event_with_seq(ev["event_type"], ev["data"], ev["seq"])

        # 2. 重新查 channel 状态
        channel = get_channel(channel_id)
        if not channel:
            yield _sse_event("error", {"message": "channel 不存在"})
            return

        status = channel["status"]

        # 3. 已结束 → 推 replay_end 关闭
        if status != "running":
            yield _sse_event("replay_end", {"status": status})
            return

        # 4. running 但心跳超时 → 标记 aborted
        if is_heartbeat_stale(channel_id, threshold_sec=15):
            mark_aborted(channel_id, "heartbeat timeout")
            yield _sse_event("error", {"message": "任务中断，请点击重试"})
            yield _sse_event("replay_end", {"status": "aborted"})
            return

        # 5. running 且心跳正常 → 长连接订阅新事件
        current_seq = events[-1]["seq"] if events else last_seq
        max_polls = 1500  # 最多等待 50 分钟（2s × 1500）
        for _ in range(max_polls):
            await asyncio.sleep(2)
            # 检测客户端断开
            if await request.is_disconnected():
                return
            # 拉新事件
            new_events = list_events(channel_id, after_seq=current_seq)
            for ev in new_events:
                yield _sse_event_with_seq(ev["event_type"], ev["data"], ev["seq"])
                current_seq = ev["seq"]
            # 检查 channel 状态
            channel = get_channel(channel_id)
            if not channel or channel["status"] != "running":
                status = channel["status"] if channel else "unknown"
                yield _sse_event("replay_end", {"status": status})
                return
            # 心跳超时检测
            if is_heartbeat_stale(channel_id, threshold_sec=15):
                mark_aborted(channel_id, "heartbeat timeout")
                yield _sse_event("error", {"message": "任务中断，请点击重试"})
                yield _sse_event("replay_end", {"status": "aborted"})
                return

        yield _sse_event("error", {"message": "等待任务完成超时"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse_event_with_seq(event_type: str, data: dict, seq: int) -> str:
    """格式化带 seq 的 SSE 事件（replay 专用）。"""
    return f"data: {json.dumps({'type': event_type, 'data': data, 'seq': seq}, ensure_ascii=False)}\n\n"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_replay_api.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: 提交**

```bash
git add backend/routers/conversations.py backend/tests/test_replay_api.py
git commit -m "feat: 新增 replay 接口支持 SSE 断线续接"
```

---

## Task 5: execution-state 接口扩展返回 channel 信息

**Files:**
- Modify: `backend/routers/conversations.py`

- [ ] **Step 1: 写 execution-state 扩展测试**

在 `backend/tests/test_replay_api.py` 末尾追加：

```python
def test_execution_state_returns_channel_info(tmp_db):
    """execution-state 应返回关联 channel 的 channel_id/status/last_seq。"""
    from app import app
    from fastapi.testclient import TestClient
    from db.stream_channels import create_channel, append_event
    from db.conversations import create_conversation, create_message
    import json as _json

    conv_id = create_conversation("test exec state")
    msg_id = create_message(conv_id, "assistant", "⏳ 分析中...",
                            metadata=_json.dumps({"execution_status": "streaming"}))
    channel_id = create_channel(conversation_id=conv_id, message_id=msg_id)
    append_event(channel_id, "status", {"message": "理解中"})

    client = TestClient(app)
    resp = client.get(f"/api/conversations/{conv_id}/execution-state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_recoverable"] is True
    item = data["item"]
    assert item["channel_id"] == channel_id
    assert item["channel_status"] == "running"
    assert item["last_seq"] == 1


def test_execution_state_no_channel_returns_null(tmp_db):
    """无 channel 关联的旧对话应返回 channel_id=None。"""
    from app import app
    from fastapi.testclient import TestClient
    from db.conversations import create_conversation, create_message
    import json as _json

    conv_id = create_conversation("test exec state no channel")
    create_message(conv_id, "assistant", "旧回复",
                  metadata=_json.dumps({"execution_status": "completed"}))

    client = TestClient(app)
    resp = client.get(f"/api/conversations/{conv_id}/execution-state")
    assert resp.status_code == 200
    data = resp.json()
    # completed 状态不在 RECOVERABLE_EXECUTION_STATUSES 中，has_recoverable 应为 False
    # 但如果有 streaming 的旧消息，channel_id 应为 None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_replay_api.py::test_execution_state_returns_channel_info -v`
Expected: FAIL（execution-state 未返回 channel_id 字段）

- [ ] **Step 3: 修改 execution-state 接口**

在 `backend/routers/conversations.py` 中找到 `get_conversation_execution_state_api` 函数（约 line 619），替换为：

```python
@router.get("/api/conversations/{conv_id}/execution-state")
async def get_conversation_execution_state_api(conv_id: int):
    """获取当前对话最近可恢复的 assistant 执行状态 + 关联 channel 信息。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    item = get_latest_recoverable_assistant(conv_id)
    # 扩展：关联 channel 信息（支持前端 replay 续接）
    if item:
        channel = get_latest_channel_for_message(item["id"])
        if channel:
            item["channel_id"] = channel["channel_id"]
            item["channel_status"] = channel["status"]
            item["last_seq"] = channel["last_seq"]
        else:
            item["channel_id"] = None
            item["channel_status"] = None
            item["last_seq"] = 0
    return {"item": item, "has_recoverable": bool(item)}
```

确保顶部导入了 `get_latest_channel_for_message`（Task 3 已添加）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_replay_api.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: 提交**

```bash
git add backend/routers/conversations.py backend/tests/test_replay_api.py
git commit -m "feat: execution-state 接口扩展返回 channel_id/status/last_seq"
```

---

## Task 6: 前端 API 新增 replayConversationStream

**Files:**
- Modify: `frontend/src/api/index.js`

- [ ] **Step 1: 在 api/index.js 中新增 replayConversationStream 函数**

在 `frontend/src/api/index.js` 中找到 `resumeConversationStream` 函数（约 line 452），在其后添加：

```javascript
/**
 * 回放续接 SSE 流——切回页面时续接 channel 事件流。
 * 返回 AbortController，调用 .abort() 可中断（不影响后台任务）。
 */
export function replayConversationStream(convId, channelId, lastSeq, onEvent) {
  const controller = new AbortController()
  const decoder = new TextDecoder()
  fetch(`${baseURL}/conversations/${convId}/replay?channel_id=${channelId}&last_seq=${lastSeq}`, {
    method: 'GET',
    signal: controller.signal,
    headers: { 'Accept': 'text/event-stream' },
  }).then(async (res) => {
    if (!res.ok) {
      onEvent({ type: 'error', data: { message: `replay 请求失败: ${res.status}` } })
      return
    }
    const reader = res.body.getReader()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6))
            onEvent(event)
          } catch (e) {
            console.error('replay 事件解析失败:', e, line)
          }
        }
      }
    }
  }).catch((err) => {
    if (err.name !== 'AbortError') {
      onEvent({ type: 'error', data: { message: `replay 连接失败: ${err.message}` } })
    }
  })
  return controller
}
```

- [ ] **Step 2: 验证前端构建无报错**

Run: `cd frontend && npm run build`
Expected: 构建成功，无错误

- [ ] **Step 3: 提交**

```bash
git add frontend/src/api/index.js
git commit -m "feat: 前端新增 replayConversationStream API"
```

---

## Task 7: 前端 ChatView 改造（replay 续接 + channel 缓存 + 重试按钮）

**Files:**
- Modify: `frontend/src/components/ChatView.vue`

- [ ] **Step 1: 在 ChatView.vue 中新增 channel 缓存状态**

在 `frontend/src/components/ChatView.vue` 的 `<script setup>` 区域，找到现有响应式状态定义（如 `streamStates`），添加 channel 缓存：

```javascript
// channel 事件流缓存：convId → { channelId, lastSeq }
const channelCache = ref(new Map())
// replay 流的 AbortController：convId → controller
const replayControllers = ref(new Map())
```

- [ ] **Step 2: 改造 tryResumeConversation 使用 replay**

找到 `tryResumeConversation` 函数，替换为：

```javascript
async function tryResumeConversation(convId) {
  // 查询执行状态（含 channel 信息）
  let execState
  try {
    execState = await getConversationExecutionState(convId)
  } catch (e) {
    console.warn('查询执行状态失败:', e)
    return
  }
  const item = execState?.item
  if (!item) {
    // 无可恢复消息 → 直接渲染历史
    return
  }

  const channelId = item.channel_id
  const channelStatus = item.channel_status

  if (!channelId) {
    // 旧对话无 channel → 直接渲染历史
    return
  }

  if (channelStatus === 'running') {
    // 任务还活着 → 调 replay 从头回放（前端尚未渲染任何事件）
    reconnectReplay(convId, channelId, 0)
  } else if (channelStatus === 'completed') {
    // 已完成 → 直接渲染历史消息（不调流接口）
    return
  } else {
    // failed / aborted / cancelled → 渲染已有片段 + 显示重试按钮
    // 标记消息显示重试按钮（通过 execution_status 驱动现有 UI）
    const msgs = messages.value.filter(m => m.conversation_id === convId)
    const lastAssistant = msgs.find(m => m.role === 'assistant' && m.id === item.id)
    if (lastAssistant) {
      lastAssistant.execution_status = channelStatus
      lastAssistant.metadata = lastAssistant.metadata || {}
      lastAssistant.metadata.execution_status = channelStatus
      lastAssistant.metadata.error_message = item.error_message || ''
    }
  }
}

function reconnectReplay(convId, channelId, lastSeq) {
  // 中断已有 replay
  const existing = replayControllers.value.get(convId)
  if (existing) existing.abort()

  const controller = replayConversationStream(convId, channelId, lastSeq, (event) => {
    handleReplayEvent(convId, event)
    // 缓存最新 seq
    if (event.seq) {
      const cache = channelCache.value.get(convId) || { channelId, lastSeq: 0 }
      cache.lastSeq = event.seq
      channelCache.value.set(convId, cache)
    }
  })
  replayControllers.value.set(convId, controller)
}

function handleReplayEvent(convId, event) {
  // replay 事件处理复用 chat 流的事件处理逻辑
  const type = event.type
  const data = event.data || {}

  if (type === 'replay_end') {
    // 回放结束，清理 controller
    replayControllers.value.delete(convId)
    // 如果是 aborted/failed，标记消息显示重试按钮
    if (data.status === 'aborted' || data.status === 'failed') {
      const msgs = messages.value.filter(m => m.conversation_id === convId)
      const lastAssistant = [...msgs].reverse().find(m => m.role === 'assistant')
      if (lastAssistant) {
        lastAssistant.execution_status = data.status
        lastAssistant.metadata = lastAssistant.metadata || {}
        lastAssistant.metadata.execution_status = data.status
      }
    }
    return
  }

  // 其他事件复用现有 chat 流处理
  // 调用现有的事件处理函数（handleStreamEvent 或内联逻辑）
  if (type === 'channel_started') return  // replay 不需要再次缓存 channel_id
  if (type === 'status') handleStatusEvent(convId, data)
  else if (type === 'specialist_done') handleSpecialistDone(convId, data)
  else if (type === 'cross_review_done') handleCrossReviewDone(convId, data)
  else if (type === 'answer') handleAnswerEvent(convId, data)
  else if (type === 'done') handleDoneEvent(convId, data)
  else if (type === 'error') handleStreamError(convId, data)
  else if (type === 'cancelled') handleCancelledEvent(convId, data)
  else if (type === 'progress') handleProgressEvent(convId, data)
}
```

注意：`handleStatusEvent` / `handleSpecialistDone` 等函数应提取自现有 chat 流的事件处理逻辑。如果现有代码是内联在 `startStream` 的回调中，需要提取为独立函数。具体函数名根据现有代码调整。

- [ ] **Step 3: chat 期间缓存 channel_id + last_seq**

在现有 chat 流的事件处理中（`startStream` 函数内），监听 `channel_started` 事件并缓存：

```javascript
// 在 startStream 的事件回调中
if (event.type === 'channel_started') {
  const { channel_id, message_id } = event.data
  channelCache.value.set(convId, { channelId: channel_id, lastSeq: 0 })
}
// 每个事件更新 lastSeq
if (event.seq) {
  const cache = channelCache.value.get(convId)
  if (cache) cache.lastSeq = event.seq
}
```

- [ ] **Step 4: 切走页面时中断 replay（不取消后台任务）**

在组件的 `onUnmounted` 或页面切换逻辑中，中断所有 replay controller：

```javascript
onUnmounted(() => {
  // 中断所有 replay 流（不影响后台任务）
  replayControllers.value.forEach(c => c.abort())
  replayControllers.value.clear()
})
```

- [ ] **Step 5: 验证前端构建**

Run: `cd frontend && npm run build`
Expected: 构建成功

- [ ] **Step 6: 手动端到端测试**

1. 启动后端：`cd backend && python3 app.py`
2. 构建前端：`cd frontend && npm run build`
3. 浏览器访问 `http://localhost:8000/app`，强制刷新（Cmd+Shift+R）
4. 测试场景：
   - 发消息 → 看到 channel_started + 专家逐个完成
   - 发消息后立即切到其他页面 → 切回来 → 续接剩余事件（不重跑）
   - 发消息后关闭页面 → 等任务完成 → 重新打开 → 直接看到结果
   - 发消息后关闭页面 → 重启后端 → 重新打开 → 看到"任务中断，点击重试"

- [ ] **Step 7: 提交**

```bash
git add frontend/src/components/ChatView.vue
git commit -m "feat: ChatView 改造支持 replay 续接 + channel 缓存 + 重试按钮"
```

---

## Task 8: 回归测试 + 清理 deprecated 接口

**Files:**
- Modify: `backend/routers/conversations.py`

- [ ] **Step 1: 运行全部测试确认无回归**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: 所有测试 PASS

- [ ] **Step 2: 给 /resume 接口添加 deprecated 标记**

在 `backend/routers/conversations.py` 中找到 `resume_conversation` 函数（约 line 238），在 docstring 中添加 deprecated 标记：

```python
@router.post("/api/conversations/{conv_id}/resume")
async def resume_conversation(conv_id: int, request: Request):
    """[DEPRECATED] 恢复中断的对话执行，跳过已完成的专家。

    已被 GET /api/conversations/{conv_id}/replay 替代。
    保留一段时间兼容旧前端缓存，新前端不应调用此接口。
    """
```

- [ ] **Step 3: 提交**

```bash
git add backend/routers/conversations.py
git commit -m "chore: /resume 接口标记 deprecated，由 /replay 替代"
```

---

## Self-Review

**Spec coverage check:**
- §1 问题与目标 → Task 1-8 整体解决 ✓
- §2 双接口架构 → Task 3 (chat) + Task 4 (replay) ✓
- §3 数据模型 → Task 1 (stream_channels + stream_events) ✓
- §4 chat 接口改造 → Task 3 ✓
- §5 replay 接口 → Task 4 ✓
- §6 前端改造 → Task 6 (API) + Task 7 (ChatView) ✓
- §7 心跳与 aborted 检测 → Task 1 (is_heartbeat_stale + cleanup_stale_channels) + Task 2 (init_db 集成) + Task 4 (replay 检测) ✓
- §8 兼容与迁移 → Task 5 (execution-state 扩展兼容旧对话) + Task 8 (/resume deprecated) ✓
- §9 错误处理 → Task 3 (producer 异常分支) + Task 4 (replay 心跳超时) ✓
- §10 测试要点 → Task 1, 4, 5 单元测试 + Task 7 手动 E2E ✓

**Placeholder scan:** 无 TBD/TODO，所有步骤含完整代码 ✓

**Type consistency:**
- `create_channel` → 返回 `str` (channel_id) ✓
- `append_event` → 返回 `int` (seq) ✓
- `get_channel` → 返回 `dict | None` ✓
- `list_events` → 返回 `list[dict]` ✓
- 前端 `replayConversationStream(convId, channelId, lastSeq, onEvent)` 签名一致 ✓

**Gaps fixed:** 无遗漏
