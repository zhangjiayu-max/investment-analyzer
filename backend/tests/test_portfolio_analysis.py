"""测试持仓分析 API。"""
import pytest
from db.portfolio import create_holding, delete_holding
from db.portfolio import (
    get_distribution_analysis, get_profit_by_fund, get_concentration_analysis,
)


def test_distribution_analysis(tmp_db):
    """配置分布分析按账户/类别聚合。"""
    h1 = create_holding(
        "110050", "基金A",
        shares=1000, cost_price=1.0, current_price=1.2, account="花无缺",
    )
    h2 = create_holding(
        "110051", "基金B",
        shares=500, cost_price=2.0, current_price=2.0, account="小鱼儿",
    )
    try:
        result = get_distribution_analysis()
        assert "by_account" in result
        assert "by_category" in result
        assert len(result["by_account"]) >= 2  # 至少两个账户
    finally:
        delete_holding(h1)
        delete_holding(h2)


def test_profit_by_fund(tmp_db):
    """分基金盈亏分析。"""
    h1 = create_holding(
        "110052", "盈利基金",
        shares=1000, cost_price=1.0, current_price=1.5,
    )
    h2 = create_holding(
        "110053", "亏损基金",
        shares=1000, cost_price=2.0, current_price=1.5,
    )
    try:
        result = get_profit_by_fund()
        assert isinstance(result, list)
        assert len(result) >= 2
        # 检查字段
        for item in result:
            assert "fund_code" in item
            assert "profit_loss" in item
            assert "profit_rate" in item
    finally:
        delete_holding(h1)
        delete_holding(h2)


def test_concentration_analysis(tmp_db):
    """集中度分析。"""
    h1 = create_holding(
        "110054", "基金A",
        shares=1000, cost_price=1.0, current_price=1.5,
    )
    try:
        result = get_concentration_analysis()
        assert "holdings" in result
        assert "max_concentration" in result
        assert "top3_concentration" in result
    finally:
        delete_holding(h1)
