"""对话205审计：恢复保全原文和证据，规则评测不得冒充事实核验。"""
import hashlib
import json

import pytest


def test_recovery_preserves_markdown_first_chapter_and_disagreement():
    from services.conv_recovery import _merge_runs_to_answer
    first = "## 一、先回答核心问题\n没有资金流证据，不能证明做空。\n\n## 二、分析\n仅可提出假设。"
    second = "## 一、风险\n当前不具备加仓条件。\n\n## 二、约束\n不建议定投。"
    answer = _merge_runs_to_answer([{"agent_name": "估值", "result": first}, {"agent_name": "风险", "result": second}])
    assert first in answer and second in answer
    assert "✅" not in answer
    assert "仅专家结果" in answer and "综合报告未生成" in answer and "分歧" in answer


@pytest.mark.parametrize("prefix", ['{"verdict":"hold","confidence":0.5}\n', '```json\n{"verdict":"hold"}\n```\n'])
def test_recovery_only_strips_confirmed_structured_prefix(prefix):
    from services.conv_recovery import _merge_runs_to_answer
    body = "## 一、核心问题\n证据不足。\n## 二、后续\n待核验。"
    answer = _merge_runs_to_answer([{"agent_name": "专家", "result": prefix + body}])
    assert body in answer
    assert '"verdict"' not in answer


def test_recovery_keeps_unrecognized_json_and_normal_prose():
    from services.conv_recovery import _merge_runs_to_answer
    for body in ['{"source":"原始资料"}\n## 一、正文\n报告', "核心回答与证据边界\n## 一、细节\n报告"]:
        assert body in _merge_runs_to_answer([{"agent_name": "专家", "result": body}])


def test_tool_results_are_recoverable_beyond_old_preview_limit():
    from db.agents import serialize_agent_run_payload, deserialize_agent_run_payload
    original = json.dumps({"rows": ["完整证据" * 800], "index": {"price": 4045.91}}, ensure_ascii=False)
    tools, _ = deserialize_agent_run_payload(serialize_agent_run_payload([{"name": "market", "result": original}]))
    assert tools[0]["result"] == original
    storage = tools[0]["result_storage"]["result"]
    assert storage["status"] == "complete"
    assert storage["sha256"] == hashlib.sha256(original.encode()).hexdigest()


def test_oversized_tool_result_is_bounded_with_explicit_loss_and_original_hash():
    from db.agents import serialize_agent_run_payload, deserialize_agent_run_payload
    original = "证据" * 200000
    tools, _ = deserialize_agent_run_payload(serialize_agent_run_payload([{"name": "market", "result": original}]))
    storage = tools[0]["result_storage"]["result"]
    assert tools[0]["result_truncated"] is True
    assert storage["status"] == "truncated"
    assert storage["sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert storage["original_bytes"] == len(original.encode())
    assert len(tools[0]["result"].encode()) <= 128 * 1024


def test_repeated_percentages_from_same_source_never_count_as_verification():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    evaluator = ConversationQualityEvaluator()
    message = {"content": "根据同一图片，PS分位1.3%。"}
    one = {"analysis": "根据图片，PS分位1.3%。", "data_refs": [{"source": "image:1"}]}
    base = evaluator._evaluate_accuracy(message, {"specialist_results": [one]})
    repeated = evaluator._evaluate_accuracy(message, {"specialist_results": [one, one, one]})
    assert repeated.score == base.score <= 60
    assert repeated.metrics["cross_validated_count"] == 0
    assert repeated.metrics["verification_status"] == "unverified"


def test_rule_accuracy_reuses_raw_fact_checker():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    dimension = ConversationQualityEvaluator()._evaluate_accuracy({"content": "债市温度无数据。"}, {
        "tool_calls": [{"name": "get_bond_temperature", "result": {"temperature": 93}}]})
    assert "bond_temperature_falsely_missing" in dimension.metrics["factual_issues"]
    assert dimension.metrics["verification_status"] == "failed"
    assert dimension.score <= 40


def test_claimed_validation_without_type_unit_date_causal_and_intent_checks_is_unverified():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    dimension = ConversationQualityEvaluator()._evaluate_accuracy({"content": "根据数据来源，这是资金撤离导致的下跌。"}, {
        "original_query": "为什么港股一直大跌", "quality_status": "validated",
        "factual_validation": {"status": "verified", "checks": {"source_type": "verified"}}})
    assert dimension.score <= 60
    assert dimension.metrics["verification_status"] == "unverified"
    for key in ("metric_type", "unit", "date", "causal_evidence", "intent"):
        assert dimension.metrics["quality_gates"][key] == "unverified"


def test_unknown_duration_does_not_receive_fast_execution_score():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    dimension = ConversationQualityEvaluator()._evaluate_execution({}, [])
    assert dimension.metrics["duration_seconds"] is None
    assert dimension.metrics["duration_status"] == "unknown"
    assert dimension.metrics["duration_score"] < 100


@pytest.mark.parametrize("status,cap", [("failed", 49), ("degraded", 59)])
def test_failed_or_degraded_message_total_score_is_capped(status, cap):
    from db.conversations import create_conversation, create_message
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    from agent.eval.conversation_evaluator import EvalDimension
    conv = create_conversation("评测状态")
    msg = create_message(conv, "assistant", "正文", json.dumps({"completion_status": status}))
    evaluator = ConversationQualityEvaluator()
    for name, weight in evaluator.WEIGHTS.items():
        method = {"execution": "execution", "data": "data_utilization", "collaboration": "collaboration",
                  "response": "response_quality"}.get(name, name)
        setattr(evaluator, "_evaluate_" + method,
                lambda *args, name=name, weight=weight: EvalDimension(name, 100, weight, {}, []))
    result = evaluator.evaluate(conv, msg, trigger_evolution=False)
    assert result.auto_score <= cap
    assert result.metadata["score_cap"] == cap


def test_evaluation_uses_recorded_trace_duration_when_metadata_missing():
    from db.conversations import create_conversation, create_message
    from db._conn import _get_conn
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    conv = create_conversation("耗时")
    msg = create_message(conv, "assistant", "正文", json.dumps({"trace_id": "original"}))
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO stream_channels(channel_id,conversation_id,message_id,trace_id,status,started_at,finished_at) VALUES ('timing',?,?, 'original','completed','2026-10-20 19:10:36','2026-10-20 19:13:58')", (conv, msg))
        conn.commit()
    finally:
        conn.close()
    result = ConversationQualityEvaluator().evaluate(conv, msg, trigger_evolution=False)
    assert result.metadata["duration_ms"] == 202000
    execution = next(d for d in result.dimensions if d["name"] == "execution")
    assert execution["metrics"]["duration_source"] == "stream_channels"


def test_apply_recovery_preserves_failed_synthesis_review_and_cancelled_terminal():
    from db.conversations import create_conversation, create_message
    from db._conn import _get_conn
    from services.conv_recovery import _apply_recovery
    conv = create_conversation("恢复")
    msg = create_message(conv, "assistant", "占位", json.dumps({"execution_status": "streaming"}))
    synthesis = {"completion_status": "degraded", "quality_issues": ["unresolved_conflict"],
                 "reflection": {"status": "failed", "quality_issues": ["单位未核验"]}}
    conn = _get_conn()
    try:
        assert _apply_recovery(conn, msg, "降级正文", True, synthesis)
        row = conn.execute("SELECT metadata FROM messages WHERE id=?", (msg,)).fetchone()
        meta = json.loads(row["metadata"])
        assert meta["completion_status"] == "degraded"
        assert meta["reflection"] == synthesis["reflection"]
        assert meta["quality_issues"] == synthesis["quality_issues"]
        conn.execute("UPDATE messages SET metadata=? WHERE id=?", (json.dumps({"execution_status": "cancelled"}), msg))
        assert not _apply_recovery(conn, msg, "迟到结果", True, synthesis)
        assert conn.execute("SELECT content FROM messages WHERE id=?", (msg,)).fetchone()[0] == "降级正文"
    finally:
        conn.close()


def test_degraded_recovery_cannot_be_finalized_as_completed():
    from db.conversations import create_conversation, create_message
    from db._conn import _get_conn
    from services.conv_recovery import _finalize_recovery
    conv = create_conversation("失败综合")
    msg = create_message(conv, "assistant", "占位")
    assert _finalize_recovery(msg, conv, "待复核综合", {"completion_status": "degraded"})["ok"]
    conn = _get_conn()
    try:
        row = conn.execute("SELECT content,metadata FROM messages WHERE id=?", (msg,)).fetchone()
        assert row["content"] == "待复核综合"
        assert json.loads(row["metadata"])["execution_status"] == "degraded"
    finally:
        conn.close()


def test_checkpoint_recovery_does_not_erase_degraded_answer_status(monkeypatch):
    from agent.core import orchestrator
    from db.conversations import create_conversation, create_message
    from db._conn import _get_conn
    from services.conv_recovery import _recover_via_orchestrate_stream
    conv = create_conversation("检查点降级")
    msg = create_message(conv, "assistant", "占位")
    monkeypatch.setattr(orchestrator, "orchestrate_stream", lambda **kwargs: iter([
        {"type": "answer", "content": "未通过综合", "completion_status": "degraded", "quality_issues": ["单位冲突"]}]))
    assert _recover_via_orchestrate_stream(msg, conv, {}) == "未通过综合"
    conn = _get_conn()
    try:
        row = conn.execute("SELECT content,metadata FROM messages WHERE id=?", (msg,)).fetchone()
        assert row["content"] == "未通过综合"
        assert json.loads(row["metadata"])["execution_status"] == "degraded"
    finally:
        conn.close()


def test_recovery_skips_explicit_review_phase():
    from services.conv_recovery import _merge_runs_to_answer
    answer = _merge_runs_to_answer([
        {"agent_name": "审查者", "result": "中间意见", "run_phase": "cross_review"},
        {"agent_name": "专家", "result": "## 一、首章\n完整报告", "run_phase": "primary"}])
    assert "中间意见" not in answer


def test_tool_results_share_total_storage_budget_and_repeated_save_keeps_hash():
    from db.agents import serialize_agent_run_payload, deserialize_agent_run_payload
    original = [{"name": f"market{i}", "result": "x" * 200000} for i in range(12)]
    tools, _ = deserialize_agent_run_payload(serialize_agent_run_payload(original))
    assert sum(len(c["result"].encode()) for c in tools) <= 1024 * 1024
    restored, _ = deserialize_agent_run_payload(serialize_agent_run_payload(tools))
    assert restored[0]["result_storage"]["result"]["sha256"] == hashlib.sha256(original[0]["result"].encode()).hexdigest()
    assert all(c["result_storage"]["result"]["status"] == "truncated" for c in restored)


@pytest.mark.parametrize("gate", ["source_type", "metric_type", "unit", "date", "causal_evidence", "intent"])
def test_failed_quality_gate_prevents_high_accuracy_even_with_other_checks_verified(gate):
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    checks = {key: "verified" for key in ("source_type", "metric_type", "unit", "date", "causal_evidence", "intent")}
    checks[gate] = "failed"
    dimension = ConversationQualityEvaluator()._evaluate_accuracy({"content": "根据来源，证据记录。"}, {
        "original_query": "为什么下跌", "factual_validation": {"status": "verified", "checks": checks}})
    assert dimension.metrics["verification_status"] == "failed"
    assert dimension.score <= 40


def test_causal_answer_does_not_misclassify_no_trade_boundary_as_unrequested_plan():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    dimension = ConversationQualityEvaluator()._evaluate_accuracy(
        {"content": "原因尚不明确，不建议加仓。需要核实卖空数据与资金流向。"},
        {"original_query": "为什么港股一直在大跌"})
    assert "unrequested_trading_plan" not in dimension.metrics["factual_issues"]


def test_duplicate_tool_detection_supports_new_evidence_envelope():
    from db.agents import serialize_agent_run_payload
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    call = [{"name": "query_fund_info", "arguments": {"fund_code": "013128", "detail_type": "holdings"}}]
    payload = serialize_agent_run_payload(call, {"verdict": "hold"})
    assert ConversationQualityEvaluator()._detect_duplicate_calls([
        {"agent_key": "a", "tool_calls": payload}, {"agent_key": "b", "tool_calls": payload}]) == 1


def test_failed_factual_verification_status_is_not_overridden_by_individual_checks():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    checks = {key: "verified" for key in ("source_type", "metric_type", "unit", "date", "causal_evidence", "intent")}
    dimension = ConversationQualityEvaluator()._evaluate_accuracy({"content": "根据来源，PS1.44，分位0.62%。"}, {
        "factual_validation": {"status": "failed", "checks": checks}})
    assert dimension.metrics["verification_status"] == "failed"


def test_numeric_causal_answer_cannot_waive_source_type_unit_date_or_intent_gates():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    checks = {key: "not_applicable" for key in ("source_type", "metric_type", "unit", "date", "causal_evidence", "intent")}
    dimension = ConversationQualityEvaluator()._evaluate_accuracy({"content": "根据来源，PS1.44，分位0.62%。"}, {
        "original_query": "估值多少", "factual_validation": {"status": "verified", "checks": checks}})
    assert dimension.metrics["verification_status"] == "unverified"


def test_auto_retry_does_not_trigger_after_cancellation(monkeypatch):
    from db.conversations import create_conversation, create_message
    from services import conv_recovery
    conv = create_conversation("取消重试")
    create_message(conv, "user", "原问题")
    msg = create_message(conv, "assistant", "已取消", json.dumps({"execution_status": "cancelled"}))
    monkeypatch.setattr(conv_recovery.threading, "Thread", lambda **kwargs: pytest.fail("取消后不应启动重试线程"))
    assert conv_recovery._auto_retry_one({"msg_id": msg, "conv_id": conv}) is False


@pytest.mark.parametrize("terminal", ["cancelled", "degraded"])
def test_late_auto_retry_respects_cancellation_and_quality_terminal(monkeypatch, terminal):
    from agent import orchestrator
    from db.conversations import create_conversation, create_message
    from db._conn import _get_conn
    from services import conv_recovery, rag
    conv = create_conversation("重试终态")
    create_message(conv, "user", "原问题")
    msg = create_message(conv, "assistant", "占位", json.dumps({"execution_status": "failed"}))
    class ImmediateThread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): self.target()
    def fake_orchestrate(**kwargs):
        if terminal == "cancelled":
            conn = _get_conn()
            conn.execute("UPDATE messages SET content='已取消',metadata=? WHERE id=?", (json.dumps({"execution_status": "cancelled"}), msg))
            conn.commit()
            conn.close()
        yield {"type": "answer", "content": "迟到综合", "completion_status": "degraded", "quality_status": "degraded"}
    monkeypatch.setattr(conv_recovery.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(rag, "build_rag_context_with_details", lambda *args: {})
    monkeypatch.setattr(orchestrator, "orchestrate", fake_orchestrate)
    assert conv_recovery._auto_retry_one({"msg_id": msg, "conv_id": conv})
    conn = _get_conn()
    try:
        row = conn.execute("SELECT content,metadata FROM messages WHERE id=?", (msg,)).fetchone()
        meta = json.loads(row["metadata"])
        if terminal == "cancelled":
            assert meta["execution_status"] == "cancelled" and row["content"] == "已取消"
        else:
            assert meta["execution_status"] == "degraded" and meta["completion_status"] == "degraded"
            assert row["content"] == "迟到综合"
            assert meta["recovery_type"] == "synthesis"
    finally:
        conn.close()


def test_recovery_does_not_drop_quality_status_when_completion_says_completed():
    from db.conversations import create_conversation, create_message
    from db._conn import _get_conn
    from services.conv_recovery import _apply_recovery
    conv = create_conversation("综合质量失败")
    msg = create_message(conv, "assistant", "占位")
    conn = _get_conn()
    try:
        assert _apply_recovery(conn, msg, "降级回答", True,
                               {"completion_status": "completed", "quality_status": "degraded", "quality_issues": ["unverified_unit"]})
        metadata = json.loads(conn.execute("SELECT metadata FROM messages WHERE id=?", (msg,)).fetchone()[0])
        assert metadata["execution_status"] == "degraded"
        assert metadata["quality_issues"] == ["unverified_unit"]
    finally:
        conn.close()
