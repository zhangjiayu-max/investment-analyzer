"""测试交易记录日期筛选。"""
import pytest
from db.portfolio import create_holding, delete_holding, create_transaction, list_transactions


def test_list_transactions_date_range_filter():
    """list_transactions 支持日期范围筛选。"""
    holding_id = create_holding(
        fund_code="110030", fund_name="测试筛选基金",
        shares=1000, cost_price=1.0, current_price=1.0,
    )
    try:
        # 创建3笔交易：2026-01-15、2026-02-15、2026-03-15
        create_transaction(fund_code="110030", transaction_type="buy", amount=500,
                          transaction_date="2026-01-15", holding_id=holding_id, status="confirmed")
        create_transaction(fund_code="110030", transaction_type="buy", amount=500,
                          transaction_date="2026-02-15", holding_id=holding_id, status="confirmed")
        create_transaction(fund_code="110030", transaction_type="buy", amount=500,
                          transaction_date="2026-03-15", holding_id=holding_id, status="confirmed")

        # 筛选 2026-02-01 ~ 2026-02-28 应只返回1笔
        result = list_transactions(fund_code="110030", start_date="2026-02-01", end_date="2026-02-28")
        assert len(result) == 1
        assert result[0]["transaction_date"] == "2026-02-15"

        # 筛选 2026-01-01 ~ 2026-03-31 应返回3笔
        result = list_transactions(fund_code="110030", start_date="2026-01-01", end_date="2026-03-31")
        assert len(result) == 3
    finally:
        delete_holding(holding_id)
