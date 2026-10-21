"""交付状态回归：综合降级与执行异常分别呈现，读取不改历史数据。"""
import asyncio
import json

import pytest

from db import create_conversation, create_message, get_messages
from db._conn import _get_conn
from db.conversations import get_latest_recoverable_assistant, mark_message_execution_status


def test_degraded_is_valid_terminal_status_and_not_resumable():
    conv = create_conversation("降级终态")
    msg = create_message(conv, "assistant", "可读综合报告")
    assert mark_message_execution_status(msg, "degraded", completion_status="degraded")
    assert get_latest_recoverable_assistant(conv) is None


def test_latest_terminal_delivery_does_not_resume_an_older_failed_turn():
    conv = create_conversation("旧失败轮次")
    create_message(conv, "assistant", "❌ Connection error", json.dumps({"execution_status": "failed"}))
    create_message(conv, "assistant", "已生成的综合报告", json.dumps({"execution_status": "degraded", "completion_status": "degraded"}))
    assert get_latest_recoverable_assistant(conv) is None


def test_legacy_degraded_report_read_normalizes_without_mutating_saved_record():
    from routers.conversation.conversations import get_messages_api
    conv = create_conversation("206、207历史兼容")
    metadata = {"execution_status": "failed", "completion_status": "degraded", "quality_status": "degraded",
                "quality_issues": ["cross_review_budget_exhausted"],
                "reflection": {"status": "budget_exhausted"},
                "specialist_results": [{"agent_key": "asset", "completion_status": "truncated"}]}
    content = "## 综合报告\n现有正文应完整保留。"
    msg = create_message(conv, "assistant", content, json.dumps(metadata))
    response = asyncio.run(get_messages_api(conv, 50, 0))
    item = response["messages"][0]
    assert item["metadata"]["execution_status"] == "degraded"
    assert item["metadata"]["delivery_message"].startswith("分析已生成，部分核验未完成")
    assert "专家输出截断" in item["metadata"]["delivery_reasons"]
    assert "交叉审查因预算不足未完成" in item["metadata"]["delivery_reasons"]
    assert item["content"] == content
    stored = next(m for m in get_messages(conv) if m["id"] == msg)
    assert json.loads(stored["metadata"]) == metadata
    assert get_latest_recoverable_assistant(conv) is None


def test_recovery_persists_nonempty_degraded_synthesis_and_terminal_sse():
    from services.conv_recovery import _finalize_recovery
    from db import list_stream_events
    conv = create_conversation("综合可用待复核")
    msg = create_message(conv, "assistant", "⏳ 分析中")
    result = _finalize_recovery(msg, conv, "完整保留的综合正文", {
        "completion_status": "degraded", "quality_status": "degraded", "quality_issues": ["cross_review_budget_exhausted"]})
    assert result["ok"]
    saved = get_messages(conv)[0]
    meta = json.loads(saved["metadata"])
    assert meta["execution_status"] == meta["completion_status"] == "degraded"
    assert meta["quality_status"] == "degraded"
    assert meta["recovery_type"] == "synthesis"
    events = list_stream_events(result["channel_id"])
    answer = json.loads(next(e["data_json"] for e in events if e["event_type"] == "answer"))
    assert answer["execution_status"] == "degraded"
    assert answer["quality_status"] == "degraded"


@pytest.mark.parametrize("content", ["", "  ", "Connection error", "❌ 执行失败: Connection error",
                                     "专家预算已用尽，未执行追加分析。", "专家执行超时，本轮未取得完整分析。"])
def test_empty_or_connection_error_cannot_be_recovered_as_generated_analysis(content):
    from services.conv_recovery import _finalize_recovery
    conv = create_conversation("真正失败")
    msg = create_message(conv, "assistant", "⏳ 分析中")
    assert not _finalize_recovery(msg, conv, content, {"completion_status": "degraded"})["ok"]


def test_trade_execution_issue_uses_chinese_delivery_reason():
    from services.conversation.delivery_status import delivery_metadata
    result = delivery_metadata("可供复核的综合报告", {"completion_status": "degraded", "quality_issues": ["trade_execution_unverified"]})
    assert result["delivery_reasons"] == ["交易是否真实执行尚未核验"]


def test_expert_only_recovery_explicitly_denies_completed_synthesis():
    from services.conv_recovery import _apply_recovery
    conv = create_conversation("205专家恢复")
    msg = create_message(conv, "assistant", "⏳ 分析中")
    conn = _get_conn()
    try:
        assert _apply_recovery(conn, msg, "专家原稿", has_expert_results=True)
        meta = json.loads(conn.execute("SELECT metadata FROM messages WHERE id=?", (msg,)).fetchone()[0])
        assert meta["execution_status"] == "degraded"
        assert meta["recovery_type"] == "experts_only"
        assert "仅专家结果" in meta["delivery_message"]
        assert "综合报告未生成" in meta["delivery_message"]
        assert meta["quality_status"] == "degraded"
    finally:
        conn.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_degraded_cannot_be_resumed_or_synthesized_in_place(legacy):
    from fastapi import HTTPException
    from routers.conversation.conversations import resume_conversation, resume_synthesis_api
    conv = create_conversation("终态不续跑")
    msg = create_message(conv, "assistant", "综合正文保留", json.dumps({
        "execution_status": "failed" if legacy else "degraded", "completion_status": "degraded"}))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(resume_conversation(conv, None))
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException) as caught:
        asyncio.run(resume_synthesis_api(conv, msg))
    assert caught.value.status_code == 409


def test_degraded_retry_creates_new_attempt_and_keeps_original_report(monkeypatch):
    from db import create_agent_run
    from routers.conversation.conversations import retry_conversation_message_api
    from services import conv_recovery
    conv = create_conversation("重新生成")
    create_message(conv, "user", "原始问题")
    msg = create_message(conv, "assistant", "历史综合正文", json.dumps({"execution_status": "failed", "completion_status": "degraded"}))
    create_agent_run(conversation_id=conv, message_id=msg, agent_key="asset", agent_name="配置专家", query="问题", result="原稿", status="success")
    monkeypatch.setattr(conv_recovery, "resume_synthesis_only", lambda *args: pytest.fail("降级终态重试不能覆盖历史正文"))
    result = asyncio.run(retry_conversation_message_api(conv, msg))
    assert result["message_id"] != msg
    assert result["original_query"] == "原始问题"
    assert next(m for m in get_messages(conv) if m["id"] == msg)["content"] == "历史综合正文"


@pytest.mark.parametrize("failed_content", [None, "", "Connection error", "专家预算已用尽，未执行追加分析。", "专家执行超时，本轮未取得完整分析。"])
def test_live_degraded_answer_and_database_and_channel_agree(monkeypatch, failed_content):
    from agent import multi_hop_rag, hooks, orchestrator, memory_governance
    from db import get_latest_channel_for_message, list_stream_events
    from routers.conversation import conversations as routes
    conv = create_conversation("SSE降级终态")
    specialist = {"agent_key": "allocation", "analysis": "根据原始证据检查配置，但本段输出尚未完成。" * 5,
                  "status": "degraded", "completion_status": "truncated",
                  "context_usage": {"original_chars": 120000, "compacted_chars": 24000},
                  "tokens_used": 18000, "tokens_source": "provider", "budget_exhausted": True}
    report = "## 综合报告\n" + "现有证据仅能支持谨慎观察，缺失的盘中资金数据需要进一步核验。" * 5
    expected_status = "degraded" if failed_content is None else "failed"
    if failed_content is not None:
        report = failed_content
    usage = {"cost_budget": {"execution": 4000}, "tokens_used": 22000, "by_phase": {"execution": 20000}}
    arbitration = {"arbitration_mode": "insufficient_evidence", "supporting_agents": [],
                   "valid_specialist_count": 0, "voting_specialist_count": 0,
                   "verdict": "证据不足，暂时观察", "confidence": "low", "asset_actions": []}
    async def no_alert(*args): pass
    monkeypatch.setattr(routes, "clarify_requirement", lambda *args: {"complexity": "medium", "specialists": [], "reason": "test"})
    monkeypatch.setattr(routes, "build_rag_context_with_details", lambda *a, **kw: {"context": "", "results": [], "keywords": []})
    monkeypatch.setattr(routes, "_build_unified_context_safe", lambda **kw: "")
    monkeypatch.setattr(routes, "build_portfolio_facts", lambda: {})
    monkeypatch.setattr(routes, "_proactive_alert_check", no_alert)
    monkeypatch.setattr(routes, "_calculate_quality_metrics", lambda *a: {})
    monkeypatch.setattr(routes, "review_output", lambda content, experts: {"content": content, "warnings": []})
    monkeypatch.setattr(hooks, "run_hooks", lambda kind, context: context)
    monkeypatch.setattr(memory_governance.SessionSteward, "extract_and_save_candidates", lambda *a: None)
    monkeypatch.setattr(multi_hop_rag, "multi_hop_search", lambda *a: {})
    monkeypatch.setattr(orchestrator, "_inject_analysis_context", lambda *a: ("", ""))
    monkeypatch.setattr(routes, "orchestrate_stream", lambda *a, **kw: iter([{
        "type": "answer", "content": report, "specialist_results": [specialist],
        "completion_status": "degraded", "quality_status": "degraded",
        "quality_issues": ["cross_review_budget_exhausted"], "cross_review_status": "budget_exhausted",
        "budget_usage": usage,
        "arbitration": arbitration,
    }]))
    class ConnectedRequest:
        async def is_disconnected(self): return False
    async def consume():
        response = await routes.send_message_stream(conv, routes.SendMessageRequest(content="检查风险"), ConnectedRequest())
        return [json.loads(chunk[6:].strip()) async for chunk in response.body_iterator if chunk.startswith("data: ")]
    events = asyncio.run(consume())
    answer = next(e["data"] for e in events if e["type"] == "answer")
    assert answer["execution_status"] == expected_status
    assert answer["quality_status"] == "degraded"
    if expected_status == "degraded":
        assert "专家输出截断" in answer["delivery_reasons"]
        assert "交叉审查因预算不足未完成" in answer["delivery_reasons"]
    saved = next(m for m in get_messages(conv) if m["role"] == "assistant")
    assert json.loads(saved["metadata"])["execution_status"] == expected_status
    assert json.loads(saved["metadata"])["budget_usage"] == usage
    stored_specialist = json.loads(saved["metadata"])["specialist_results"][0]
    for key in ("context_usage", "tokens_used", "tokens_source", "budget_exhausted"):
        assert stored_specialist[key] == specialist[key]
    saved_arbitration = json.loads(saved["metadata"])["arbitration"]
    for key in ("arbitration_mode", "supporting_agents", "valid_specialist_count", "voting_specialist_count"):
        assert saved_arbitration[key] == arbitration[key]
    channel = get_latest_channel_for_message(saved["id"])
    assert channel["status"] == ("completed" if expected_status == "degraded" else "failed")
    persisted_answer = next(e for e in list_stream_events(channel["channel_id"]) if e["event_type"] == "answer")
    assert json.loads(persisted_answer["data_json"])["execution_status"] == expected_status
    assert events[-1]["type"] == "done"
    if expected_status == "degraded":
        assert get_latest_recoverable_assistant(conv) is None
    conn = _get_conn()
    try:
        assert conn.execute("SELECT status FROM execution_traces WHERE conversation_id=? ORDER BY id DESC", (conv,)).fetchone()[0] == expected_status
        assert conn.execute("SELECT status FROM agent_runs WHERE message_id=? AND agent_key='chat_turn'", (saved["id"],)).fetchone()[0] == expected_status
    finally:
        conn.close()
