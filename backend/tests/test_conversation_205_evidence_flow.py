"""归因问题的证据共享、时效和审查预算回归。"""
import copy
import importlib
import json
import threading
import pytest
from concurrent.futures import ThreadPoolExecutor


def test_parallel_read_tools_share_one_execution_without_mutating_args(monkeypatch):
    import tools
    from agent.infra.tool_dedup import reset_tool_call_cache
    reset_tool_call_cache()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def execute(name, arguments, **kw):
        calls.append(dict(arguments))
        entered.set()
        assert release.wait(2)
        return json.dumps({"index_name": "恒生科技", "current_value": 1.44})
    monkeypatch.setattr(tools, "_execute_tool_impl", execute)
    monkeypatch.setattr(tools, "_log_tool_audit", lambda *a, **k: None)
    import agent.infra.tool_singleflight as singleflight
    real_wait = singleflight.wait_result
    waiting = threading.Condition()
    waiters = []
    def wait(future, timeout):
        with waiting:
            waiters.append(True)
            waiting.notify_all()
        return real_wait(future, timeout)
    monkeypatch.setattr(singleflight, "wait_result", wait)
    args = {"index_name": "恒生科技"}
    original = copy.deepcopy(args)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(tools.execute_tool, "query_valuation", args, "shared-205")
        assert entered.wait(2)
        others = [pool.submit(tools.execute_tool, "query_valuation", args, "shared-205") for _ in range(2)]
        # Wait until callers have reached the coordination boundary, not a timed sleep.
        with waiting:
            assert waiting.wait_for(lambda: len(waiters) == 2, timeout=2)
        release.set()
        results = [first.result(), *[f.result() for f in others]]
    assert len(calls) == 1
    assert len(set(results)) == 1
    assert args == original


def test_cache_isolated_by_message_and_search_does_not_mutate_arguments(monkeypatch):
    import tools
    from agent.infra.tool_dedup import reset_tool_call_cache
    reset_tool_call_cache()
    calls = []
    def search(args):
        calls.append(dict(args))
        return json.dumps({"results": [{"title": "材料", "body": "背景"}]})
    monkeypatch.setattr(tools, "_search_knowledge", search)
    monkeypatch.setattr(tools, "_log_tool_audit", lambda *a, **k: None)
    args = {"query": "港股"}
    for msg in (611, 613):
        tools.execute_tool("search_knowledge", args, "same-trace", conversation_id=205, message_id=msg)
    assert len(calls) == 2
    assert [c["message_id"] for c in calls] == [611, 613]
    assert args == {"query": "港股"}


def test_synthesis_deduplicates_identical_tools_and_uses_stable_evidence_ids():
    from services.conversation.quality_contract import compact_tool_evidence
    call = {"name": "query_valuation", "arguments": {"index_code": "HSTECH"},
            "result": {"metric_type": "市销率", "percentile": .62}}
    result = compact_tool_evidence([call, copy.deepcopy(call)])
    assert len(result) == 1
    assert result[0]["evidence_id"] == compact_tool_evidence([{"name": "other", "result": {"a": 1}}, call])[1]["evidence_id"]


def test_missing_article_publication_date_is_background_not_current_fact(tmp_db):
    from db._conn import _get_conn
    from services.rag.evidence_quality import hydrate_evidence
    conn = _get_conn()
    article_id = conn.execute("INSERT INTO author_articles(url,title,content_text,publish_time) VALUES(?,?,?,?)",
                             ("https://example.test/old", "五月调仓", "5月22号调整", "")).lastrowid
    conn.commit(); conn.close()
    results = [{"content_type": "author_article", "reference_id": str(article_id), "time": "2026-10-20"}]
    hydrate_evidence(results, _get_conn)
    assert results[0]["as_of_date"] is None
    assert results[0]["time"] is None
    assert results[0]["usage_scope"] == "background_only"
    assert "publication_date_unknown" in results[0]["limitations"]


def test_execution_budget_does_not_consume_reserved_review_budget():
    p = importlib.import_module("agent.core.pipeline")
    from agent.state.pipeline_state import PipelineState
    state = PipelineState(token_budget={"execution": 4000, "review": 2000, "synthesis": 2500, "max_total": 12000},
                          tokens_used=4100, tokens_used_by_phase={"execution": 4100})
    assert p._followup_budget_exhausted(state) is True  # no new specialist rerun
    assert p._followup_budget_exhausted(state, for_review=True) is False
    state.tokens_used_by_phase["reflection"] = 2000
    assert p._followup_budget_exhausted(state, for_review=True) is True
    state.tokens_used = 10000
    assert p._followup_budget_exhausted(state, for_review=True) is True


def test_synthesis_uses_attribution_policy_without_trade_template(monkeypatch):
    from types import SimpleNamespace
    from agent.core import pipeline
    from services import llm_service
    from agent.core import arbitration
    from agent.state.pipeline_state import PipelineState
    state = PipelineState()
    captured = []
    def call(**kw):
        captured.append(kw["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="证据不足，需核验当期资金流。"), finish_reason="stop")], usage=SimpleNamespace(total_tokens=123))
    monkeypatch.setattr(llm_service, "_call_llm", call)
    monkeypatch.setattr(arbitration, "arbitrate_results", lambda *a, **k: pytest.fail("归因问题不应做买卖仲裁"))
    pipeline._synthesize_multiple_specialists("为什么港股科技和互联网一直在大跌？", [
        {"agent_key": "market_analyst", "agent": "市场", "analysis": "缺少当期资金数据"},
        {"agent_key": "risk_assessor", "agent": "风险", "analysis": "不能确认主因"}], None, "policy205", state=state)
    prompt = captured[0][0]["content"]
    assert "行情归因任务边界" in prompt
    assert "具体到金额/比例/触发条件" not in prompt
    assert "第 4 段：操作建议" not in prompt
    assert state.tokens_used_by_phase["synthesis"] == 123


def test_react_synthesis_also_obeys_attribution_intent():
    from agent.core.orchestrator import _build_final_synthesis_prompt
    prompt = _build_final_synthesis_prompt([], [], query="为什么港股科技一直在跌")
    assert "行情归因任务边界" in prompt
    assert "具体到金额/比例/触发条件" not in prompt


def test_market_attribution_does_not_prefetch_private_portfolio_or_unrelated_indices(monkeypatch):
    from agent.core import pipeline
    from agent.state.pipeline_state import PipelineState
    import db.portfolio
    import agent.memory.memory
    monkeypatch.setattr(db.portfolio, "get_portfolio_snapshot", lambda *a: pytest.fail("无持仓诉求"))
    monkeypatch.setattr(db.portfolio, "list_holdings", lambda *a: pytest.fail("无持仓诉求"))
    monkeypatch.setattr(agent.memory.memory, "build_user_memory_context", lambda *a: pytest.fail("无持仓诉求"))
    queried = []
    monkeypatch.setattr(pipeline, "_lookup_valuation", lambda target: queried.append(target) or {})
    state = PipelineState(original_query="为什么港股科技一直在跌", refined_query="为什么港股科技一直在跌")
    result = pipeline._phase_info_gather(state, {"targets": ["恒生科技"]}, [], "prefetch205",
                                         prepared_rag_context="", prepared_rag_result={})
    assert result["portfolio"] == ""
    assert queried == ["恒生科技"]
