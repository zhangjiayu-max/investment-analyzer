"""用户可见核心链路回归：AI 建议、Agent 路由与异步结果契约。"""

import asyncio


def test_ai_suggestion_to_decision_accepts_pydantic_request(monkeypatch):
    from routers.portfolio import portfolio as module

    captured = {}

    def fake_create(payload):
        captured.update(payload)
        return 123

    import db.decisions
    monkeypatch.setattr(db.decisions, "create_candidate_from_structured_recommendation", fake_create)

    request = module.AiSuggestionToDecisionRequest(
        suggestion="建议买入 000001，投入 1000 元",
    )
    result = asyncio.run(module.ai_suggestion_to_decision_api(request))

    assert result["ok"] is True
    assert result["candidate_id"] == 123
    assert captured["source_type"] == "ai_chat"


def test_market_timer_is_reachable_from_smart_router():
    from agent.core.router import SmartRouter

    result = SmartRouter().route("现在是否适合择时买入，趋势和买点怎么看")

    assert "market_timer" in result["specialists"]


def test_buying_matched_fund_advances_opportunity_to_tracking(tmp_db):
    from db.opportunities import (
        save_opportunity,
        mark_matching_opportunities_bought,
        list_opportunity_tracks,
        get_opportunity,
    )

    opportunity_id = save_opportunity({
        "trade_date": "2026-10-18",
        "theme": "测试闭环主题",
        "verdict": "can_buy",
        "matched_funds": [{"fund_code": "000001", "fund_name": "测试基金"}],
        "exit_plan": {"review_date": "2026-11-08"},
        "status": "watching",
    })

    first = mark_matching_opportunities_bought(
        "000001", amount=1000, entry_price=1.25, entry_date="2026-10-18"
    )
    second = mark_matching_opportunities_bought(
        "000001", amount=1000, entry_price=1.25, entry_date="2026-10-18"
    )

    tracks = list_opportunity_tracks()
    assert len(first) == 1
    assert second == []
    assert tracks[0]["opportunity_id"] == opportunity_id
    assert tracks[0]["entry_price"] == 1.25
    assert get_opportunity(opportunity_id)["status"] == "bought"
