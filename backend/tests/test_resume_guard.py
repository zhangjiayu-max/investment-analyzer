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
        assert "已取消" in resp.json()["detail"]
    finally:
        from db.conversations import delete_conversation
        delete_conversation(conv_id)
