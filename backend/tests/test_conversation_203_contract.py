"""对话203：原始证据、标的动作和执行质量的端到端边界回归。"""
import json

import pytest


def _tools():
    return [{"name": "query_portfolio", "result": json.dumps({"holdings": [
        {"fund_code": "014846", "current_value": 158674.88},
        {"fund_code": "161725", "current_value": 56111.69}]})}]


def _action(**fields):
    return {"target": "014846", "type": "SELL", "status": "current",
            "reason": "按已核对的集中度约束再平衡", "evidence_refs": ["query_portfolio"],
            "confidence": .65, **fields}


def test_candidates_bind_structured_sell_despite_other_fund_dca():
    from services.conversation.quality_contract import build_chat_candidates
    report = "建议减仓基金014846。基金161725维持持有。其他宽基可考虑定投。"
    candidates = build_chat_candidates(report, [_action()], _tools(), conversation_id=203, message_id=602)
    assert [(c["action_type"], c["target_code"]) for c in candidates] == [("sell", "014846")]
    assert candidates[0]["dedupe_key"]


@pytest.mark.parametrize("action", [
    _action(status="conditional"), _action(negated=True), _action(historical=True),
    _action(evidence_refs=[]), _action(target="999999"),
    _action(source="text_fallback"), _action(type="BUY"),
])
def test_candidates_reject_unverified_or_opposite_actions(action):
    from services.conversation.quality_contract import build_chat_candidates
    assert build_chat_candidates("建议减仓基金014846。", [action], _tools()) == []


def test_no_candidates_from_prose_or_degraded_analysis():
    from services.conversation.quality_contract import build_chat_candidates
    assert build_chat_candidates("014846禁止买入；建议其他基金定投。", [], _tools()) == []
    assert build_chat_candidates("建议减仓基金014846。", [_action()], _tools(), completion_status="degraded") == []
    assert build_chat_candidates("建议减仓基金014846。", [_action(), _action(type="BUY")], _tools()) == []


@pytest.mark.parametrize("text", ["当前不买入014846。", "应避免加仓014846。", "勿买入014846。", "建议不应买入014846。"])
def test_negated_final_action_never_creates_candidate(text):
    from services.conversation.quality_contract import build_chat_candidates
    assert not build_chat_candidates(text, [_action(type="BUY")], _tools())


def test_raw_evidence_overrides_reflection_empty_temperature_claim():
    from services.conversation.quality_contract import reconcile_reflection_evidence
    review = {"status": "completed", "quality_issues": ["get_bond_temperature 查询返回空数据", "风险分析缺失"],
              "missing_perspectives": [], "confidence_adjustment": -.15}
    tools = [{"name": "get_bond_temperature", "result": '{"temperature":93,"date":"2026-10-16"}'}]
    fixed = reconcile_reflection_evidence(review, tools)
    assert fixed["quality_issues"] == ["风险分析缺失"]
    assert fixed["evidence_corrections"][0]["value"] == 93
    assert review["quality_issues"][0].endswith("空数据")


def test_metrics_do_not_count_xml_review_duplicates_or_weak_rag():
    from routers.conversation.conversations import _calculate_quality_metrics
    experts = [{"agent_key": "risk", "status": "success", "analysis": "<tool_call_abc>" + "工具" * 150 + "</tool_call_abc>"},
               {"agent_key": "allocation", "analysis": "持仓和现金应按统一总资产口径评估。" * 20, "status": "success"},
               {"agent_key": "allocation", "analysis": "交叉审阅完成。" * 30, "is_cross_review": True}]
    metrics = _calculate_quality_metrics(experts, {"results": [{"title": "弱相关"}], "rag_low_quality": True}, [])
    assert metrics["specialist_count"] == 2
    assert metrics["specialist_completion"] == .5
    assert metrics["rag_coverage"] == 0


def test_metadata_uses_final_expert_fields_instead_of_event_defaults():
    from services.conversation.quality_contract import merge_specialist_results
    events = [{"agent_key": "allocation", "analysis": "旧预览", "verdict": "unknown", "confidence": 0}]
    final = [{"agent_key": "allocation", "analysis": "完整分析", "verdict": "hold", "confidence": .72,
              "status": "success", "asset_actions": [_action()]}]
    merged = merge_specialist_results(events, final)
    assert merged[0]["verdict"] == "hold"
    assert merged[0]["confidence"] == .72
    assert merged[0]["analysis"] == "完整分析"


def test_trace_start_is_derived_from_elapsed_time():
    from routers.conversation.conversations import _save_execution_trace
    from db._conn import _get_conn
    _save_execution_trace("203-test", 203, "风险", "complex", "failed", 429542, {}, "quality")
    with _get_conn() as conn:
        row = conn.execute("SELECT (julianday(finished_at)-julianday(started_at))*86400 elapsed FROM execution_traces WHERE trace_id='203-test'").fetchone()
    assert row["elapsed"] == pytest.approx(429.542, abs=1.1)


def test_invalid_primary_cannot_vote_with_high_confidence():
    from agent.core.arbitration import arbitrate_results
    result = arbitrate_results("组合风险", [
        {"agent_key": "risk_assessor", "analysis": "<tool_call_abc>" + "工具" * 60, "verdict": "hold", "confidence": .9},
        {"agent_key": "allocation", "analysis": "持仓应按风险目标均衡。" * 12, "verdict": "hold", "confidence": .65},
        {"agent_key": "behavior", "analysis": "行为风险需要真实交易证据。" * 12, "verdict": "hold", "confidence": .65}])
    assert next(e for e in result["expert_verdicts"] if e["agent_key"] == "risk_assessor")["verdict"] == "unknown"
    assert result["confidence"] != "high"


def test_hold_does_not_bypass_sell_action_review(monkeypatch):
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    import db.config
    monkeypatch.setattr(db.config, "get_config_bool", lambda *a, **k: True)
    _, warnings = enforce_sell_timing_guard("## 操作建议\n建议减仓基金014846。", arbitration_summary={"verdict": "建议持有/观望", "final_stance": "hold"})
    assert warnings


def test_synthesis_degrades_when_risk_primary_is_invalid(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    monkeypatch.setattr(pipeline, "_detect_data_gaps", lambda *a: [])
    monkeypatch.setattr(pipeline, "_synthesize_multiple_specialists", lambda *a, **k: (
        "总体置信度：中高。" + "根据持仓事实核对风险与配置。" * 15,
        {"confidence": "high", "asset_actions": []}, "completed"))
    experts = [{"agent_key": "risk_assessor", "analysis": "<tool_call_abc>" + "工具" * 80, "status": "success"},
               {"agent_key": "allocation", "analysis": "按当前目标检查集中度和现金用途。" * 15, "status": "success"}]
    result = pipeline._phase_synthesis(PipelineState(), "风险", {"specialists": experts}, None, "203-test",
                                       reflection_result={"status": "completed", "confidence_adjustment": -.15})
    assert result["completion_status"] == "degraded"
    assert result["confidence"] < .6
    assert "中高" not in result["answer"]
    assert result["quality_status"] == "degraded"


def test_failed_reflection_prevents_saving_executable_recommendations(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    monkeypatch.setattr(pipeline, "_detect_data_gaps", lambda *a: [])
    monkeypatch.setattr(pipeline, "_synthesize_multiple_specialists", lambda *a, **k: ("风险分析。" * 30, {}, "completed"))
    result = pipeline._phase_synthesis(PipelineState(), "风险", {"specialists": [
        {"agent_key": "a", "analysis": "风险分析。" * 30}, {"agent_key": "b", "analysis": "配置分析。" * 30}]},
        None, "test", reflection_result={"status": "failed", "confidence_adjustment": -.3})
    assert result["completion_status"] == "degraded"


def test_final_fact_error_degrades_report_and_preserves_snapshot(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    monkeypatch.setattr(pipeline, "_detect_data_gaps", lambda *a: [])
    state = PipelineState()
    snapshot = {"funds": [{"fund_code": "014846"}], "concentration_policy": {"max_single_position_pct": 15}}
    state.set_phase_result("info_gather", {"portfolio_snapshot": snapshot})
    execution = {"specialists": [{"agent_key": "risk", "analysis": "用户KYC单基金上限为25%持仓市值。" + "应根据持仓核对配置风险。" * 10}]}
    result = pipeline._phase_synthesis(state, "风险", execution, None, "t")
    assert result["completion_status"] == "degraded"
    assert "kyc_single_limit_value_mismatch" in result["quality_issues"]
    assert execution["tool_calls"][0]["result"] is snapshot


def test_synthesis_receives_same_gathered_context_and_all_evidence(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    monkeypatch.setattr(pipeline, "_detect_data_gaps", lambda *a: [])
    captured = {}
    def synth(*args, **kwargs):
        captured.update(kwargs)
        return "按当前快照评估配置风险。" * 15, {}, "completed"
    monkeypatch.setattr(pipeline, "_synthesize_multiple_specialists", synth)
    state = PipelineState()
    snapshot = {"funds": [{"fund_code": "014846"}], "cash_balance": 505898.39}
    state.set_phase_result("info_gather", {"portfolio_snapshot": snapshot, "portfolio": "本轮KYC上限15%总资产"})
    execution = {"specialists": [{"agent_key": key, "analysis": "按当前快照评估配置风险。" * 15} for key in ("a", "b")], "tool_calls": _tools()}
    pipeline._phase_synthesis(state, "风险", execution, None, "t")
    assert captured["portfolio_context"] == "本轮KYC上限15%总资产"
    assert captured["tool_calls"][-1]["result"] is snapshot


def test_missing_planned_expert_cannot_disappear_from_quality_gate(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    monkeypatch.setattr(pipeline, "_detect_data_gaps", lambda *a: [])
    monkeypatch.setattr(pipeline, "_synthesize_multiple_specialists", lambda *a, **k: ("根据本轮证据评估配置风险。" * 12, {}, "completed"))
    execution = {"specialists": [{"agent_key": "allocation", "analysis": "按当前持仓分布评估配置风险。" * 12}]}
    result = pipeline._phase_synthesis(PipelineState(), "风险", execution, None, "t",
        plan={"plan": {"steps": [{"agent_key": "allocation"}, {"agent_key": "risk_assessor"}]}})
    assert result["completion_status"] == "degraded"
    assert any(s["agent_key"] == "risk_assessor" and s["status"] == "failed" for s in execution["specialists"])


def test_exhausted_budget_stops_followup_model_work():
    from agent.core.pipeline import _run_followup_phase
    from agent.state.pipeline_state import PipelineState
    state = PipelineState(token_budget={"max_total": 1000}, tokens_used=1000)
    def forbidden(*args):
        pytest.fail("must not invoke model after exhausted budget")
    assert _run_followup_phase(forbidden, (state,), None, "reflection")["status"] == "budget_exhausted"


def test_stream_finalization_rechecks_status_and_requires_explicit_quality():
    from services.conversation.quality_contract import finalize_answer_event
    event = {"content": "当前按持仓事实评估风险。" * 10, "completion_status": "completed",
             "specialist_results": [{"agent_key": "risk", "analysis": "<tool_call_abc>" + "工具" * 80}]}
    result = finalize_answer_event(event)
    assert result["completion_status"] == "degraded"
    assert result["quality_status"] == "degraded"
    event["specialist_results"] = []
    assert finalize_answer_event(event)["quality_status"] == "unverified"


def test_recommendations_cannot_reextract_opposite_action(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    import services.llm_service
    monkeypatch.setattr(services.llm_service, "_call_llm", lambda **kw: pytest.fail("must reuse validated actions"))
    monkeypatch.setattr(pipeline, "_lookup_valuation", lambda *a: None)
    state = PipelineState(answer="建议减仓基金014846。" + "本轮按用户集中度上限评估。" * 8)
    state.set_phase_result("execution", {"tool_calls": _tools()})
    state.set_phase_result("synthesis", {"completion_status": "completed", "quality_status": "validated",
                                        "arbitration": {"asset_actions": [_action(amount=4526.70)]}})
    result = pipeline._extract_and_save_recommendations(state, {}, None, "t")
    assert [r["direction"] for r in result["recommendations"]] == ["down"]
    assert result["recommendations"][0]["suggested_amount"] == 4526.70


@pytest.mark.parametrize("direction, amount", [("BUY", None), ("BUY", 10000), ("SELL", 200000)])
def test_candidate_must_obey_known_snapshot_limits(direction, amount):
    from services.conversation.quality_contract import build_chat_candidates
    snapshot = {"funds": [{"fund_code": "014846", "current_value": 158674.88}],
                "invested_value": 521756.13, "cash_balance": 505898.39,
                "concentration_policy": {"max_single_position_pct": 15}}
    tools = [{"name": "get_portfolio_snapshot", "evidence_id": "portfolio_snapshot", "result": snapshot}]
    text = ("建议加仓014846。" if direction == "BUY" else "建议减仓014846。")
    action = _action(type=direction, amount=amount, evidence_refs=["portfolio_snapshot"])
    assert not build_chat_candidates(text, [action], tools)


def test_correctly_quoted_kyc_still_rejects_violating_action(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    monkeypatch.setattr(pipeline, "_detect_data_gaps", lambda *a: [])
    snapshot = {"funds": [{"fund_code": "014846", "current_value": 158674.88}],
                "invested_value": 521756.13, "cash_balance": 505898.39,
                "concentration_policy": {"max_single_position_pct": 15}}
    state = PipelineState()
    state.set_phase_result("info_gather", {"portfolio_snapshot": snapshot})
    result = pipeline._phase_synthesis(state, "风险", {"specialists": [{"agent_key": "risk",
        "analysis": "用户单只基金上限为总资产15%。建议加仓014846。" + "核对本轮现金和持仓风险。" * 10,
        "asset_actions": [_action(type="BUY", amount=10000)]}]}, None, "t")
    assert result["completion_status"] == "degraded"
    assert "asset_action_limit_violation" in result["quality_issues"]


def test_rerun_retains_gathered_snapshot_and_kyc(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    state = PipelineState()
    state.set_phase_result("planning", {"specialists": [{"agent_key": "risk", "system_prompt": "配置的风险专家角色"}]})
    state.set_phase_result("info_gather", {"portfolio": "本轮单基金上限15%总资产", "rag_context": "证据ID:book:5",
                                          "portfolio_snapshot": {"cash_balance": 505898.39}})
    captured = {}
    def run(*args, **kwargs):
        captured.update(kwargs)
        return {"agent_key": "risk", "analysis": "按本轮持仓和用户画像评估风险。" * 12}
    monkeypatch.setattr(pipeline, "_run_pipeline_specialist", run)
    result = pipeline._maybe_rerun_specialist(state, "风险", {"specialists": [{"agent_key": "risk", "analysis": "<tool_call_abc>"}]},
                                             {"confidence_adjustment": -.1}, None, "t")
    assert result
    assert "15%总资产" in captured["prebuilt_context"]
    assert "505898.39" in captured["prebuilt_context"]
    assert "book:5" in captured["prebuilt_context"]
    assert "配置的风险专家角色" in captured["prebuilt_context"]


@pytest.mark.parametrize("category,cash,value,target", [("bond", 100, 450, None), ("index", 800, 100, 30)])
def test_multiple_buys_obey_combined_cash_and_equity_limits(category, cash, value, target):
    from services.conversation.quality_contract import build_chat_candidates, action_constraint_issues
    snapshot = {"funds": [{"fund_code": code, "current_value": value, "fund_category": category} for code in ("014846", "161725")],
                "invested_value": 2 * value, "cash_balance": cash,
                "target_equity_ratio": target, "concentration_policy": {"max_single_position_pct": 60}}
    tools = [{"name": "get_portfolio_snapshot", "evidence_id": "portfolio_snapshot", "result": snapshot}]
    actions = [_action(target=code, type="BUY", amount=75, evidence_refs=["portfolio_snapshot"]) for code in ("014846", "161725")]
    assert action_constraint_issues(actions, tools) == ["asset_action_limit_violation"]
    assert build_chat_candidates("建议加仓014846。建议加仓161725。", actions, tools) == []
