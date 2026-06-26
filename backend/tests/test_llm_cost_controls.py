import asyncio


def test_llm_cost_configs_default_to_disabled():
    from db.config import get_config

    expected_disabled = [
        "llm_cost.auto_daily_report",
        "llm_cost.auto_daily_eval",
        "llm_cost.auto_conversation_eval",
        "llm_cost.llm_judge_eval",
        "llm_cost.root_cause_analyzer",
        "llm_cost.auto_conversation_summary",
        "llm_cost.page_llm_summary",
    ]

    for key in expected_disabled:
        assert get_config(key) == "false"


def test_auto_conversation_evaluation_does_not_submit_when_disabled(monkeypatch):
    import agent.orchestrator as orchestrator

    submitted = []
    monkeypatch.setattr(orchestrator._eval_executor, "submit", lambda *args, **kwargs: submitted.append((args, kwargs)))

    orchestrator._schedule_auto_evaluation(1, 2, {"answer": "ok"})

    assert submitted == []


def test_llm_judge_returns_disabled_scores_without_calling_llm(monkeypatch):
    from routers.analysis import eval_system

    def fail_if_called(*args, **kwargs):
        raise AssertionError("LLM should not be called when llm_judge_eval is disabled")

    monkeypatch.setattr(eval_system, "_call_llm", fail_if_called)

    result = asyncio.run(eval_system.run_llm_judge("panorama", "good", "{}", "analysis"))

    assert result["score_total"] == 0
    assert result["judge_comments"] == "LLM Judge 已关闭"


def test_batch_root_cause_analyzer_skips_when_disabled(monkeypatch):
    import root_cause_analyzer

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Bad cases should not be loaded when root cause analyzer is disabled")

    monkeypatch.setattr("db.portfolio.list_all_bad_cases", fail_if_called)

    result = root_cause_analyzer.batch_analyze(limit=10)

    assert result == {
        "total": 0,
        "analyzed": 0,
        "failed": 0,
        "skipped": 0,
        "results": [],
        "message": "自动根因分析已关闭",
    }
