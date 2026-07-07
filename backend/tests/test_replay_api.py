"""测试 replay 接口的回放续接逻辑。"""
import json
from datetime import datetime, timedelta


def _parse_sse_events(text):
    """解析 SSE 响应文本为事件列表。"""
    events = []
    for line in text.split("\n"):
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except Exception:
                pass
    return events


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
    events = _parse_sse_events(resp.text)
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
    from db.stream_channels import create_channel, append_event, get_channel
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
    events = _parse_sse_events(resp.text)
    types = [e["type"] for e in events]
    assert "error" in types, "应推 error 事件"
    assert "replay_end" in types
    end_event = next(e for e in events if e["type"] == "replay_end")
    assert end_event["data"]["status"] == "aborted"

    # 验证 channel 已标记 aborted
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
    events = _parse_sse_events(resp.text)
    # 应只有 seq=2,3 + replay_end
    seqs = [e.get("seq") for e in events if e["type"] != "replay_end"]
    assert 1 not in seqs, "seq=1 不应出现"


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
    # 创建 streaming 状态的消息（可恢复），但无 channel 关联
    create_message(conv_id, "assistant", "⏳ 分析中...",
                  metadata=_json.dumps({"execution_status": "streaming"}))

    client = TestClient(app)
    resp = client.get(f"/api/conversations/{conv_id}/execution-state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_recoverable"] is True
    item = data["item"]
    assert item["channel_id"] is None
    assert item["channel_status"] is None
