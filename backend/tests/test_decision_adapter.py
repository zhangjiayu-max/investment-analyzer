"""决策档案与量化账本兼容适配测试。"""

from db.decisions import create_decision as create_archive_decision
from db.investment_decisions import create_decision as create_quant_decision
import pytest


def _create_quant_decision():
    return create_quant_decision(
        decision_type="opportunity_buy",
        source_module="agent_pipeline",
        fund_code="000001",
        fund_name="测试基金",
        confidence=0.78,
        verdict="can_buy",
        suggested_amount=1200,
        status="suggested",
        signal_snapshot={"valuation_percentile": 12},
        risk_check={"flags": ["测试风控"]},
        review_days=15,
    )


def test_quant_decision_can_be_linked_to_archive():
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)

    assert archive["investment_decision_id"] == quant_id
    assert archive["target_code"] == "000001"
    assert archive["status"] == "proposed"


def test_quant_to_archive_sync_is_idempotent():
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    first = ensure_decision_record_for_investment(quant_id)
    second = ensure_decision_record_for_investment(quant_id)

    assert second["id"] == first["id"]


def test_quant_archive_keeps_user_scope():
    from db.decisions import get_decision
    from db.investment_decisions import create_decision
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = create_decision(
        decision_type="watch", source_module="agent_pipeline", fund_code="000003",
        user_id="alice", verdict="watch", status="suggested",
    )
    archive = ensure_decision_record_for_investment(quant_id)

    assert get_decision(archive["id"])["user_id"] == "alice"


def test_archive_status_maps_back_to_quant_ledger():
    from db.investment_decisions import get_decision as get_quant_decision
    from services.advisor.decision_adapter import (
        ensure_decision_record_for_investment,
        sync_archive_status,
    )

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)

    result = sync_archive_status(archive["id"], "accepted", "用户确认")

    assert result["status"] == "accepted"
    assert get_quant_decision(quant_id)["status"] == "confirmed"


def test_rejected_archive_does_not_close_quant_ledger():
    from db.investment_decisions import get_decision as get_quant_decision
    from services.advisor.decision_adapter import (
        ensure_decision_record_for_investment,
        sync_archive_status,
    )

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)

    sync_archive_status(archive["id"], "rejected", "暂不执行")

    assert get_quant_decision(quant_id)["status"] == "suggested"


def test_manual_archive_has_no_quant_link():
    from db.decisions import get_decision

    archive_id = create_archive_decision(
        source_type="manual",
        decision_type="watch",
        target_type="fund",
        target_code="000002",
        target_name="手工记录",
        summary="保留观察",
    )

    assert get_decision(archive_id)["investment_decision_id"] is None


def test_pipeline_result_exposes_linked_archive(monkeypatch):
    from db.decisions import get_decision
    from services.advisor import decision_pipeline as pipeline

    holding = {
        "fund_code": "000001",
        "fund_name": "测试基金",
        "index_code": "000300.SH",
        "index_name": "沪深300",
        "cost_price": 1.0,
        "current_value": 1000,
        "current_price": 1.1,
        "price_updated_at": "2026-10-18 10:00:00",
    }
    monkeypatch.setattr(pipeline, "_discovery_layer", lambda *args: {
        "valuation_signal": {"level": "low", "zscore": -1.2},
        "valuation_percentile": 12,
        "data_quality": "ok",
    })
    monkeypatch.setattr(pipeline, "_risk_layer", lambda *args: {"flags": [], "corr_downweight": 1.0})
    monkeypatch.setattr(pipeline, "_sizing_layer", lambda *args, **kwargs: {
        "suggested_amount": 500, "source": "test",
    })
    monkeypatch.setattr(pipeline, "_lookup_opportunity_score", lambda *args: None)
    monkeypatch.setattr(pipeline, "_lookup_opportunity_ic_confidence", lambda *args: None)
    monkeypatch.setattr(pipeline, "build_exit_plan", None, raising=False)

    import db.portfolio
    monkeypatch.setattr(db.portfolio, "get_holding_by_fund", lambda *args: holding)
    monkeypatch.setattr(db.portfolio, "list_holdings", lambda *args: [holding])
    monkeypatch.setattr(db.portfolio, "get_portfolio_summary", lambda *args: {"total_assets": 10000})

    card = pipeline.run_investment_decision("000001")

    assert card["decision_id"]
    assert card["decision_record_id"]
    assert get_decision(card["decision_record_id"])["investment_decision_id"] == card["decision_id"]


def test_archive_status_route_syncs_linked_ledger():
    import asyncio

    from db.investment_decisions import get_decision as get_quant_decision
    from routers.decision.decisions import DecisionStatusRequest, update_decision_status_api
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)

    response = asyncio.run(update_decision_status_api(
        archive["id"], DecisionStatusRequest(status="accepted", user_note="确认执行"),
    ))

    assert response["sync_status"] == "synced"
    assert get_quant_decision(quant_id)["status"] == "confirmed"


def test_archive_review_keeps_user_feedback_separate_from_backtest():
    import asyncio

    from db.investment_decisions import get_decision as get_quant_decision
    from routers.decision.decisions import DecisionReviewRequest, record_decision_review_api
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)

    response = asyncio.run(record_decision_review_api(
        archive["id"], DecisionReviewRequest(
            outcome="helpful", result_note="执行过程清晰", profit_change=80, lesson="分批执行有效",
        ),
    ))

    assert response["sync_status"] == "synced"
    quant = get_quant_decision(quant_id)
    assert "用户复盘(helpful)" in (quant.get("notes") or "")
    assert quant["reviewed_at"] is None
    assert quant["status"] == "suggested"


def test_quant_ledger_route_can_create_missing_archive():
    import asyncio

    from db.investment_decisions import get_decision as get_quant_decision
    from routers.analysis.decision import create_ledger_archive

    quant_id = _create_quant_decision()
    response = asyncio.run(create_ledger_archive(quant_id))

    assert response["code"] == 0
    assert response["data"]["decision_record_id"]
    assert get_quant_decision(quant_id)["decision_record_id"] == response["data"]["decision_record_id"]


def test_quant_backtest_does_not_mark_user_review_complete():
    from db.decisions import get_decision
    from db.investment_decisions import record_review
    from services.advisor.decision_adapter import (
        ensure_decision_record_for_investment,
        sync_investment_status,
    )

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)
    record_review(quant_id, is_hit=True, excess_return_pct=3.2, status_after="closed")

    sync_investment_status(quant_id)
    updated = get_decision(archive["id"])

    assert updated["status"] == "proposed"
    assert "量化回测已完成" in (updated.get("user_note") or "")


def test_historical_closed_quant_does_not_create_user_review():
    from db.investment_decisions import record_review
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    record_review(quant_id, is_hit=True, status_after="closed")
    archive = ensure_decision_record_for_investment(quant_id)

    assert archive["status"] == "proposed"
    assert not archive["review"]


def test_concurrent_archive_creation_is_idempotent():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from db._conn import _get_conn
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    ready = Barrier(4)

    def create():
        ready.wait(timeout=10)
        return ensure_decision_record_for_investment(quant_id)["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        archive_ids = list(pool.map(lambda _: create(), range(4)))
    assert len(set(archive_ids)) == 1
    conn = _get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM decision_records").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM decision_actions").fetchone()[0] == 1
    finally:
        conn.close()


def test_buy_archive_preserves_trade_amount_and_avoid_has_no_trade_action():
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    archive = ensure_decision_record_for_investment(_create_quant_decision())
    assert archive["actions"][0]["params_json"]["amount"] == 1200
    quant_id = create_quant_decision(
        decision_type="opportunity_buy", source_module="test", fund_code="000002",
        verdict="avoid", suggested_amount=1200,
    )
    avoid = ensure_decision_record_for_investment(quant_id)
    assert avoid["decision_type"] == "watch"
    assert not avoid["actions"]


def test_unified_view_rejects_unrelated_records_and_wrong_user():
    from services.advisor.decision_adapter import ensure_decision_record_for_investment, unified_decision

    first = _create_quant_decision()
    second = _create_quant_decision()
    archive = ensure_decision_record_for_investment(first)
    with pytest.raises(ValueError):
        unified_decision(investment_id=second, archive_id=archive["id"])
    assert unified_decision(investment_id=first, user_id="alice") is None


def test_status_update_is_atomic_when_linked_quant_missing():
    from db._conn import _get_conn
    from db.decisions import get_decision, update_decision_status
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM investment_decisions WHERE id = ?", (quant_id,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError):
        update_decision_status(archive["id"], "accepted")
    assert get_decision(archive["id"])["status"] == "proposed"


def test_common_status_update_syncs_quant_without_route():
    from db.decisions import update_decision_status
    from db.investment_decisions import get_decision
    from services.advisor.decision_adapter import ensure_decision_record_for_investment

    quant_id = _create_quant_decision()
    archive = ensure_decision_record_for_investment(quant_id)
    update_decision_status(archive["id"], "accepted")
    assert get_decision(quant_id)["status"] == "confirmed"
    update_decision_status(archive["id"], "executed")
    assert get_decision(quant_id)["status"] == "executed"
