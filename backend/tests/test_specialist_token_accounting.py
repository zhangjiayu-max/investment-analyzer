"""专家预算必须累计每次模型响应的 usage，而不是只估算最后上下文。"""

from types import SimpleNamespace
import json

import pytest

VALID = "本轮行情证据尚不足以确定唯一原因。已确认事实需要与事件日期对齐，资金机制仍待当期数据验证。" * 8


def response(text=VALID, tokens=1000, reason="stop", tools=None):
    return SimpleNamespace(
        usage=SimpleNamespace(total_tokens=tokens) if tokens is not None else None,
        choices=[SimpleNamespace(finish_reason=reason, message=SimpleNamespace(
            content=text, tool_calls=tools, model_extra={}, reasoning_content=None))],
    )


@pytest.fixture
def core(monkeypatch):
    from agent.core import multi_agent
    import agent.infra.self_reflection as reflection
    monkeypatch.setattr(reflection, "is_self_reflection_enabled", lambda: False)
    monkeypatch.setattr(multi_agent, "_parse_structured_output", lambda *args: {})
    return multi_agent


def run(core):
    return core.run_specialist("market_analyst", "为什么港股一直下跌", from_pipeline=True, prebuilt_context="当期市场证据")


def test_sums_actual_usage_across_tool_turns(core, monkeypatch):
    tool = SimpleNamespace(id="one", function=SimpleNamespace(name="query_policy_news", arguments='{}'))
    responses = iter([response("", 12000, "tool_calls", [tool]), response(tokens=19000)])
    monkeypatch.setattr(core, "_call_llm", lambda **kw: next(responses))
    monkeypatch.setattr(core, "execute_tool", lambda *args, **kw: '{"news": []}')
    result = run(core)
    assert result["tokens_used"] == 31000
    assert result["tokens_source"] == "reported"


def test_continuation_usage_counted_once_including_first_response(core, monkeypatch):
    responses = iter([response(VALID, 12000, "length"), response("核验完毕。", 17000)])
    monkeypatch.setattr(core, "_call_llm", lambda **kw: next(responses))
    result = run(core)
    assert result["tokens_used"] == 29000
    assert result["tokens_source"] == "reported"


def test_summary_and_tool_compatibility_fallback_usage(core, monkeypatch):
    calls = []
    def llm(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("function compatibility")
        return response("简短回答", 3000) if len(calls) == 2 else response(tokens=7000)
    monkeypatch.setattr(core, "_call_llm", llm)
    result = run(core)
    assert result["tokens_used"] == 10000
    assert result["tokens_source"] == "reported"


def test_missing_usage_is_estimated_and_labeled(core, monkeypatch):
    monkeypatch.setattr(core, "_call_llm", lambda **kw: response(tokens=None))
    result = run(core)
    assert result["tokens_used"] > 0
    assert result["tokens_source"] == "estimated"


def test_cross_review_reports_usage(core, monkeypatch):
    monkeypatch.setattr(core, "_call_llm", lambda **kw: response(tokens=4321))
    result = core.run_specialist_with_context("market_analyst", "为什么港股下跌", {}, prebuilt_context="证据")
    assert result["tokens_used"] == 4321
    assert result["tokens_source"] == "reported"


def test_opinion_review_reports_usage_and_reuses_answer_policy(core, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "_call_llm", lambda **kw: calls.append(kw) or response(json.dumps({"summary": VALID}), 2345))
    result = core.run_cross_review_opinion("market_analyst", "为什么港股下跌", VALID, {})
    assert result["tokens_used"] == 2345
    assert result["tokens_source"] == "reported"
    assert "待验证机制" in calls[0]["messages"][0]["content"]


def test_round_table_revision_reports_usage_and_reuses_policy(core, monkeypatch):
    agents = core.load_specialist_agents()
    calls = []
    def review(key, *args, **kw):
        return {"agent_key": key, "analysis": VALID, "is_cross_review": True,
                "opinion": {"disagreements": [{"peer": agents["market_analyst"]["name"], "point": "证据不足", "reason": "缺当期数据"}]}}
    monkeypatch.setattr(core, "run_specialist_with_context", review)
    monkeypatch.setattr(core, "_call_llm", lambda **kw: calls.append(kw) or response(tokens=3456))
    results = [{"agent_key": key, "analysis": VALID} for key in ["market_analyst", "risk_assessor"]]
    revised = core.run_round_table_discussion("为什么港股下跌", results)
    revision = next(item for item in revised if item.get("is_phase_c_revision"))
    assert revision["tokens_used"] == 3456
    assert revision["tokens_source"] == "reported"
    assert "待验证机制" in calls[0]["messages"][0]["content"]


def test_optional_output_cap_limits_each_call(core, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "_estimate_specialist_tokens", lambda *args: 100)
    monkeypatch.setattr(core, "_call_llm", lambda **kw: calls.append(kw) or response(tokens=250))
    result = core.run_specialist("market_analyst", "为什么港股下跌", from_pipeline=True,
                                 prebuilt_context="证据", token_budget=1000, max_output_tokens=150)
    assert calls[0]["max_tokens"] == 150
    assert result["tokens_used"] == 250


def test_output_only_cap_allows_large_prompt_and_labels_no_strict_budget(core, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "_estimate_specialist_tokens", lambda *args: 20000)
    monkeypatch.setattr(core, "_call_llm", lambda **kw: calls.append(kw) or response(tokens=21000))
    result = core.run_specialist("market_analyst", "为什么港股下跌", from_pipeline=True,
                                 prebuilt_context="证据", max_output_tokens=1333)
    assert calls[0]["max_tokens"] == 1333
    assert result["status"] == "success"
    assert result["tokens_used"] == 21000


def test_actual_usage_exceeding_explicit_budget_is_degraded(core, monkeypatch):
    monkeypatch.setattr(core, "_estimate_specialist_tokens", lambda *args: 100)
    monkeypatch.setattr(core, "_call_llm", lambda **kw: response(tokens=1200))
    result = core.run_specialist("market_analyst", "为什么港股下跌", from_pipeline=True,
                                 prebuilt_context="证据", token_budget=1000)
    assert result["status"] == "budget_exhausted"
    assert result["completion_status"] == "degraded"
    assert result["tokens_used"] == 1200


def test_prompt_over_remaining_budget_never_calls_model(core, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "_estimate_specialist_tokens", lambda *args: 100)
    monkeypatch.setattr(core, "_call_llm", lambda **kw: calls.append(kw) or response())
    result = core.run_specialist("market_analyst", "为什么港股下跌", from_pipeline=True,
                                 prebuilt_context="证据", token_budget=50)
    assert not calls
    assert result["status"] == "budget_exhausted"
    assert result["completion_status"] == "degraded"
    assert result["action_signals"] == []


def test_reported_usage_blocks_next_tool_turn_when_budget_cannot_fit_prompt(core, monkeypatch):
    calls = []
    tool = SimpleNamespace(id="one", function=SimpleNamespace(name="query_policy_news", arguments='{}'))
    monkeypatch.setattr(core, "_estimate_specialist_tokens", lambda *args: 200)
    monkeypatch.setattr(core, "execute_tool", lambda *args, **kw: '{"news": []}')
    monkeypatch.setattr(core, "_call_llm", lambda **kw: calls.append(kw) or response("", 900, "tool_calls", [tool]))
    result = core.run_specialist("market_analyst", "为什么港股下跌", from_pipeline=True,
                                 prebuilt_context="证据", token_budget=1000)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 800
    assert result["tokens_used"] == 900
    assert result["status"] == "budget_exhausted"


def test_opinion_review_accepts_budget_and_blocks_prompt_overflow(core, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "_estimate_specialist_tokens", lambda *args: 200)
    monkeypatch.setattr(core, "_call_llm", lambda **kw: calls.append(kw) or response())
    result = core.run_cross_review_opinion("market_analyst", "为什么港股下跌", VALID, {},
                                         token_budget=100, max_output_tokens=50)
    assert calls == []
    assert result["status"] == "budget_exhausted"
    assert result["completion_status"] == "degraded"
