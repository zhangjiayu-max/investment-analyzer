# backend/tests/test_cancel_persist.py
"""测试取消标记持久化。"""
import pytest
from db.conversations import create_conversation, mark_conversation_cancelled, get_conversation_cancel_status


def test_cancel_mark_persists():
    """mark_conversation_cancelled 应写入 cancel_requested=1。"""
    conv_id = create_conversation("test cancel persist")
    try:
        assert get_conversation_cancel_status(conv_id) is False  # 初始未取消
        mark_conversation_cancelled(conv_id)
        assert get_conversation_cancel_status(conv_id) is True  # 取消后标记为 True
    finally:
        from db.conversations import delete_conversation
        delete_conversation(conv_id)
