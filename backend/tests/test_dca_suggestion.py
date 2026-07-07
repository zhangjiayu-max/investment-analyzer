# backend/tests/test_dca_suggestion.py
"""测试加仓建议 API。"""
import pytest
from db.conversations import create_conversation, delete_conversation
from db.portfolio import create_holding, delete_holding, get_dca_suggestion


def _setup_holding(profit_rate=None, shares=1000, cost_price=1.0, current_price=None):
    """创建测试持仓。profit_rate<0 亏损，>0 盈利。"""
    if current_price is None:
        current_price = cost_price * (1 + (profit_rate or 0))
    holding_id = create_holding(
        fund_code="110022", fund_name="易方达消费",
        shares=shares, cost_price=cost_price, current_price=current_price,
    )
    return holding_id


def test_dca_suggestion_profit_advice_watch():
    """盈利状态建议观望。"""
    holding_id = _setup_holding(profit_rate=0.05)
    try:
        result = get_dca_suggestion(holding_id)
        assert result["advice"] == "watch"
        assert result["suggestion"]["recommended_amount"] == 0
        assert "盈利" in result["suggestion"]["rule"]
    finally:
        delete_holding(holding_id)


def test_dca_suggestion_small_loss_tier1():
    """亏损0~4%建议第1档¥500。"""
    holding_id = _setup_holding(profit_rate=-0.02)
    try:
        result = get_dca_suggestion(holding_id)
        assert result["advice"] == "continue"
        assert result["suggestion"]["recommended_amount"] == 500
        assert result["suggestion"]["tier"] == 1
    finally:
        delete_holding(holding_id)


def test_dca_suggestion_medium_loss_tier2():
    """亏损4~8%建议第2档¥1000。"""
    holding_id = _setup_holding(profit_rate=-0.06)
    try:
        result = get_dca_suggestion(holding_id)
        assert result["advice"] == "continue"
        assert result["suggestion"]["recommended_amount"] == 1000
        assert result["suggestion"]["tier"] == 2
    finally:
        delete_holding(holding_id)


def test_dca_suggestion_large_loss_tier3():
    """亏损8~12%建议第3档¥1500。"""
    holding_id = _setup_holding(profit_rate=-0.10)
    try:
        result = get_dca_suggestion(holding_id)
        assert result["advice"] == "continue"
        assert result["suggestion"]["recommended_amount"] == 1500
        assert result["suggestion"]["tier"] == 3
    finally:
        delete_holding(holding_id)


def test_dca_suggestion_huge_loss_capped():
    """亏损>12%封顶第3档+提示。"""
    holding_id = _setup_holding(profit_rate=-0.15)
    try:
        result = get_dca_suggestion(holding_id)
        assert result["suggestion"]["recommended_amount"] == 1500
        assert result["suggestion"]["tier"] == 3
        assert "超过定投法覆盖范围" in result["suggestion"]["rule"]
    finally:
        delete_holding(holding_id)
