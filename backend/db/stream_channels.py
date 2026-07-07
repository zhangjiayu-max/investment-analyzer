"""stream_channels + stream_events 表 CRUD — SSE 对话事件流持久化。

每次对话执行 = 一个 channel（关联一条 assistant message）。
channel 状态机：running → completed/failed/aborted/cancelled。
stream_events 按 seq 记录关键事件，支持断线续接回放。
"""

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
