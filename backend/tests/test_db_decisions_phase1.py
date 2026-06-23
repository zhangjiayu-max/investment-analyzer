"""理财决策闭环 Phase 1 数据层测试。"""

from db import (
    add_cash,
    build_decision_precheck,
    create_decision,
    create_holding,
    create_transaction,
    get_decision,
    list_transactions,
)


def _create_buy_decision(amount=1000, fund_code="000001", fund_name="测试基金"):
    return create_decision(
        source_type="manual",
        decision_type="add",
        target_type="fund",
        target_code=fund_code,
        target_name=fund_name,
        summary=f"{fund_name} 加仓草案",
        rationale="测试加仓",
        actions=[
            {
                "action_type": "pre_trade_check",
                "title": "执行前检查",
                "params": {
                    "transaction_type": "buy",
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "amount": amount,
                },
            }
        ],
    )


def test_precheck_blocks_buy_when_cash_is_insufficient(tmp_db):
    add_cash("default", 200)
    decision_id = _create_buy_decision(amount=1000)

    result = build_decision_precheck(decision_id)

    assert result["ok_to_execute"] is False
    assert any("现金余额不足" in item for item in result["blockers"])


def test_precheck_warns_when_pending_trade_exists_for_same_fund(tmp_db):
    add_cash("default", 5000)
    holding_id = create_holding("000001", "测试基金", shares=100, cost_price=1, current_price=1)
    create_transaction(
        fund_code="000001",
        transaction_type="buy",
        amount=0,
        shares=None,
        price=None,
        transaction_date="2026-06-23",
        holding_id=holding_id,
        status="pending",
        submitted_amount=500,
        fund_name="测试基金",
    )
    decision_id = _create_buy_decision(amount=1000)

    result = build_decision_precheck(decision_id)

    assert result["ok_to_execute"] is True
    assert any("已有待确认交易" in item for item in result["warnings"])


def test_precheck_warns_when_single_position_would_exceed_default_limit(tmp_db):
    add_cash("default", 5000)
    create_holding("000001", "测试基金", shares=9000, cost_price=1, current_price=1)
    create_holding("000002", "其他基金", shares=1000, cost_price=1, current_price=1)
    decision_id = _create_buy_decision(amount=1000)

    result = build_decision_precheck(decision_id)

    assert any("单基金占比" in item for item in result["warnings"] + result["blockers"])


def test_create_transaction_draft_from_decision_creates_pending_buy(tmp_db):
    from db.decisions import create_transaction_draft_from_decision

    add_cash("default", 5000)
    decision_id = _create_buy_decision(amount=1200)

    result = create_transaction_draft_from_decision(decision_id)

    assert result["ok"] is True
    tx = result["transaction"]
    assert tx["status"] == "pending"
    assert tx["transaction_type"] == "buy"
    assert tx["fund_code"] == "000001"
    assert tx["submitted_amount"] == 1200
    txs = list_transactions(fund_code="000001", status="pending")
    assert len(txs) == 1
    decision = get_decision(decision_id)
    assert decision["status"] == "accepted"
    assert any(a["action_type"] == "transaction_draft" for a in decision["actions"])


def test_create_transaction_draft_from_decision_refuses_when_precheck_blocked(tmp_db):
    from db.decisions import create_transaction_draft_from_decision

    add_cash("default", 100)
    decision_id = _create_buy_decision(amount=1200)

    result = create_transaction_draft_from_decision(decision_id)

    assert result["ok"] is False
    assert "现金余额不足" in result["error"]
    assert list_transactions(fund_code="000001", status="pending") == []
