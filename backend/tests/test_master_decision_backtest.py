"""大师决策回测 — 第四阶段单元测试。

覆盖：
- CRUD基本操作
- T+N验证判定逻辑
- 胜率统计
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# ── CRUD测试 ────────────────────────────────────────────────

def test_save_and_list_master_decision():
    """保存和查询大师决策。"""
    from db.master_decision_history import save_master_decision, list_master_decisions
    rid = save_master_decision(
        master_key="buffett",
        master_name="巴菲特",
        fund_code="161725",
        fund_name="白酒基金",
        action="strong_buy",
        score=75.0,
        reason="测试",
        baseline_price=1.5,
    )
    assert rid > 0
    # 查询
    result = list_master_decisions(master_key="buffett", days=1, limit=10)
    assert len(result) >= 1
    assert result[0]["master_key"] == "buffett"


def test_save_hold_action_not_recorded():
    """hold动作不记录。"""
    from db.master_decision_history import save_master_decision
    rid = save_master_decision(
        master_key="bogle",
        master_name="博格",
        fund_code="161725",
        fund_name="白酒基金",
        action="hold",
        score=60.0,
    )
    assert rid == 0  # hold不记录


# ── 验证逻辑测试 ────────────────────────────────────────────

def test_verify_strong_buy_correct():
    """strong_buy + 涨幅≥2% → correct。"""
    from services.master_decision_backtest import _verify_single_decision
    decision = {
        "fund_code": "161725",
        "action": "strong_buy",
        "baseline_price": 1.0,
        "baseline_date": "2024-01-01",
    }
    # Mock净值：1.0 → 1.05（涨5%）
    import services.master_decision_backtest as mb
    original = mb._get_fund_price_at_date
    mb._get_fund_price_at_date = lambda code, date: 1.05 if "2024-01-08" in date else 1.0
    try:
        result, change = _verify_single_decision(decision, 7)
        assert result == "correct"
        assert change > 2
    finally:
        mb._get_fund_price_at_date = original


def test_verify_strong_buy_wrong():
    """strong_buy + 跌幅≥2% → wrong。"""
    from services.master_decision_backtest import _verify_single_decision
    decision = {
        "fund_code": "161725",
        "action": "strong_buy",
        "baseline_price": 1.0,
        "baseline_date": "2024-01-01",
    }
    import services.master_decision_backtest as mb
    original = mb._get_fund_price_at_date
    mb._get_fund_price_at_date = lambda code, date: 0.95 if "2024-01-08" in date else 1.0
    try:
        result, change = _verify_single_decision(decision, 7)
        assert result == "wrong"
        assert change < -2
    finally:
        mb._get_fund_price_at_date = original


def test_verify_reduce_correct():
    """reduce + 跌幅≥2% → correct。"""
    from services.master_decision_backtest import _verify_single_decision
    decision = {
        "fund_code": "161725",
        "action": "reduce",
        "baseline_price": 1.0,
        "baseline_date": "2024-01-01",
    }
    import services.master_decision_backtest as mb
    original = mb._get_fund_price_at_date
    mb._get_fund_price_at_date = lambda code, date: 0.95 if "2024-01-08" in date else 1.0
    try:
        result, change = _verify_single_decision(decision, 7)
        assert result == "correct"
    finally:
        mb._get_fund_price_at_date = original


def test_verify_flat():
    """涨跌幅<2% → flat。"""
    from services.master_decision_backtest import _verify_single_decision
    decision = {
        "fund_code": "161725",
        "action": "strong_buy",
        "baseline_price": 1.0,
        "baseline_date": "2024-01-01",
    }
    import services.master_decision_backtest as mb
    original = mb._get_fund_price_at_date
    mb._get_fund_price_at_date = lambda code, date: 1.01 if "2024-01-08" in date else 1.0
    try:
        result, change = _verify_single_decision(decision, 7)
        assert result == "flat"
        assert abs(change) < 2
    finally:
        mb._get_fund_price_at_date = original


def test_verify_no_baseline_price():
    """无baseline_price且净值表无数据 → flat。"""
    from services.master_decision_backtest import _verify_single_decision
    decision = {
        "fund_code": "999999",  # 不存在的基金
        "action": "strong_buy",
        "baseline_price": None,
        "baseline_date": "2024-01-01",
    }
    import services.master_decision_backtest as mb
    original = mb._get_fund_price_at_date
    mb._get_fund_price_at_date = lambda code, date: None
    try:
        result, change = _verify_single_decision(decision, 7)
        assert result == "flat"
        assert change == 0.0
    finally:
        mb._get_fund_price_at_date = original


def test_verify_wait_neutral():
    """wait动作 → flat（中性）。"""
    from services.master_decision_backtest import _verify_single_decision
    decision = {
        "fund_code": "161725",
        "action": "wait",
        "baseline_price": 1.0,
        "baseline_date": "2024-01-01",
    }
    import services.master_decision_backtest as mb
    original = mb._get_fund_price_at_date
    mb._get_fund_price_at_date = lambda code, date: 1.10 if "2024-01-08" in date else 1.0
    try:
        result, change = _verify_single_decision(decision, 7)
        assert result == "flat"  # wait始终flat
        assert change == 10.0
    finally:
        mb._get_fund_price_at_date = original


# ── 胜率统计测试 ────────────────────────────────────────────

def test_accuracy_stats_empty():
    """无数据时胜率统计返回空。"""
    from db.master_decision_history import get_master_accuracy_stats
    stats = get_master_accuracy_stats(days=1)  # 只查1天内（测试数据可能不在）
    assert "per_master" in stats
    assert "overall" in stats
    assert stats["overall"]["win_rate"] >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
