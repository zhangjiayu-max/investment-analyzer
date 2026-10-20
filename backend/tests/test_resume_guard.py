# backend/tests/test_resume_guard.py
"""测试 resume 接口的取消/失败守卫。"""
import pytest
import json
from db.conversations import create_conversation, create_message, mark_conversation_cancelled


def test_cancelled_conversation_not_auto_resumed():
    """已取消的对话不应自动 resume，应返回 409 Conflict。"""
    from app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    conv_id = create_conversation("test resume guard")
    try:
        create_message(conv_id, "user", "测试问题")
        create_message(conv_id, "assistant", "⏳ 分析进行中...",
                       metadata=json.dumps({"execution_status": "streaming"}))
        mark_conversation_cancelled(conv_id)
        # 刷新页面触发 resume
        resp = client.post(f"/api/conversations/{conv_id}/resume")
        assert resp.status_code == 409, f"已取消的对话不应自动恢复: {resp.status_code} {resp.text}"
        assert "已取消" in resp.json()["message"]
    finally:
        from db.conversations import delete_conversation
        delete_conversation(conv_id)


def test_failed_conversation_not_auto_resumed():
    """上次执行失败的对话不应自动 resume，应返回 409 Conflict。"""
    from app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    conv_id = create_conversation("test resume guard failed")
    try:
        create_message(conv_id, "user", "测试问题")
        create_message(conv_id, "assistant", "❌ 分析失败",
                       metadata=json.dumps({"execution_status": "failed"}))
        # 刷新页面触发 resume
        resp = client.post(f"/api/conversations/{conv_id}/resume")
        assert resp.status_code == 409, f"失败的对话不应自动恢复: {resp.status_code} {resp.text}"
        detail = resp.json()["message"]
        assert "失败" in detail, f"返回 detail 应包含'失败': {detail}"
    finally:
        from db.conversations import delete_conversation
        delete_conversation(conv_id)

def test_recovery_finalization_is_cancel_safe_and_emits_terminal_events(tmp_db):
    from services.conv_recovery import _finalize_recovery
    from db.stream_channels import list_events, get_latest_channel_for_message
    conv_id = create_conversation('recovery')
    msg_id = create_message(conv_id, 'assistant', '⏳ 分析进行中...', metadata=json.dumps({'execution_status':'streaming'}))
    result = _finalize_recovery(msg_id, conv_id, '完整回答', {'specialist_count': 2})
    assert result['ok'] is True
    channel = get_latest_channel_for_message(msg_id)
    assert channel['status'] == 'completed'
    types = [e['event_type'] for e in list_events(channel['channel_id'])]
    assert types == ['answer', 'recovered', 'done']
    cancelled_id = create_message(conv_id, 'assistant', '⏳ 分析进行中...', metadata=json.dumps({'execution_status':'cancelled'}))
    blocked = _finalize_recovery(cancelled_id, conv_id, '迟到回答', {})
    assert blocked['ok'] is False
