# backend/tests/test_sell_preview.py
"""测试减仓预览 API。"""
import pytest
from db.portfolio import create_holding, delete_holding, preview_sell


def test_sell_preview_profit_calculation():
    """减仓预览正确计算盈亏。"""
    holding_id = create_holding(
        fund_code="110022", fund_name="易方达消费",
        shares=1000, cost_price=1.0, current_price=1.5,
    )
    try:
        result = preview_sell(holding_id, 500)
        assert result["shares_to_sell"] == 500
        assert result["current_price"] == 1.5
        assert result["expected_proceeds"] == 750.0
        assert result["cost_basis"] == 1.0
        assert result["expected_profit_loss"] == 250.0
        assert abs(result["expected_profit_rate"] - 0.5) < 0.01
        assert result["remaining_shares"] == 500
    finally:
        delete_holding(holding_id)


def test_sell_preview_loss_warning():
    """亏损状态下减仓触发亏损提示。"""
    holding_id = create_holding(
        fund_code="110023", fund_name="亏损基金",
        shares=1000, cost_price=2.0, current_price=1.5,
    )
    try:
        result = preview_sell(holding_id, 500)
        warning_types = [w["type"] for w in result["warnings"]]
        assert "profit_warning" in warning_types
        profit_warning = next(w for w in result["warnings"] if w["type"] == "profit_warning")
        assert "亏损" in profit_warning["message"]
    finally:
        delete_holding(holding_id)


def test_sell_preview_large_amount_warning():
    """单次减仓金额>50000触发分批提示。"""
    holding_id = create_holding(
        fund_code="110024", fund_name="高价基金",
        shares=10000, cost_price=1.0, current_price=60.0,
    )
    try:
        result = preview_sell(holding_id, 1000)
        assert result["expected_proceeds"] == 60000.0
        warning_types = [w["type"] for w in result["warnings"]]
        assert "single_amount" in warning_types
    finally:
        delete_holding(holding_id)


def test_sell_preview_oversell_rejected():
    """卖出份额超过持有份额返回错误。"""
    holding_id = create_holding(
        fund_code="110025", fund_name="测试基金",
        shares=100, cost_price=1.0, current_price=1.0,
    )
    try:
        result = preview_sell(holding_id, 200)
        assert "error" in result
    finally:
        delete_holding(holding_id)
