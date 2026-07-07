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
    # 心跳时间戳精度为秒，需 sleep > 1s 才能看到变化
    time.sleep(1.1)
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
    from db.stream_channels import create_channel, is_heartbeat_stale
    from db._conn import _get_conn
    channel_id = create_channel(conversation_id=1, message_id=10)
    # 手动把心跳设为 20 秒前
    conn = _get_conn()
    old_time = (datetime.now() - timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE stream_channels SET heartbeat_at=? WHERE channel_id=?", (old_time, channel_id))
    conn.commit()
    conn.close()
    assert is_heartbeat_stale(channel_id, threshold_sec=15) is True


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
