"""对话异步执行状态机测试。"""

import json

from db.conversations import (
    create_assistant_placeholder,
    create_conversation,
    create_message,
    get_latest_recoverable_assistant,
    get_messages,
    mark_message_execution_status,
    retry_assistant_message,
)


def test_assistant_placeholder_uses_queued_status(tmp_db):
    conv_id = create_conversation("测试")
    msg_id = create_assistant_placeholder(conv_id, user_message_id=11, content="排队中")

    msg = get_messages(conv_id, limit=10)[0]
    meta = json.loads(msg["metadata"])
    assert msg["id"] == msg_id
    assert msg["role"] == "assistant"
    assert meta["execution_status"] == "queued"
    assert meta["user_message_id"] == 11


def test_mark_message_execution_status_preserves_metadata(tmp_db):
    conv_id = create_conversation("测试")
    msg_id = create_assistant_placeholder(conv_id, user_message_id=12)

    ok = mark_message_execution_status(msg_id, "failed", error_message="网络异常", trace_id="abc")

    assert ok is True
    msg = get_messages(conv_id, limit=10)[0]
    meta = json.loads(msg["metadata"])
    assert meta["execution_status"] == "failed"
    assert meta["error_message"] == "网络异常"
    assert meta["trace_id"] == "abc"
    assert meta["user_message_id"] == 12


def test_latest_recoverable_assistant_includes_queued_streaming_cancelled_failed(tmp_db):
    conv_id = create_conversation("测试")
    create_message(conv_id, "user", "分析一下")
    old_id = create_assistant_placeholder(conv_id, user_message_id=1)
    mark_message_execution_status(old_id, "completed")
    new_id = create_assistant_placeholder(conv_id, user_message_id=2)
    mark_message_execution_status(new_id, "cancelled")

    item = get_latest_recoverable_assistant(conv_id)

    assert item["id"] == new_id
    assert item["execution_status"] == "cancelled"


def test_retry_assistant_message_creates_new_placeholder_and_keeps_failed_record(tmp_db):
    conv_id = create_conversation("测试")
    user_id = create_message(conv_id, "user", "继续分析")
    failed_id = create_assistant_placeholder(conv_id, user_message_id=user_id)
    mark_message_execution_status(failed_id, "failed", error_message="超时")

    retry_id = retry_assistant_message(failed_id)

    msgs = get_messages(conv_id, limit=10)
    failed = next(m for m in msgs if m["id"] == failed_id)
    retry = next(m for m in msgs if m["id"] == retry_id)
    assert json.loads(failed["metadata"])["execution_status"] == "failed"
    assert json.loads(retry["metadata"])["execution_status"] == "queued"
    assert json.loads(retry["metadata"])["retry_of_message_id"] == failed_id
