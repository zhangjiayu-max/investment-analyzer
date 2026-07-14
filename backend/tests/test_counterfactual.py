"""反事实决策验证测试 — 快照表 CRUD + 假设交易隔离 + 验证引擎。

覆盖：
- create_snapshot_with_hypothetical 去重 + 假设交易自动创建
- 假设交易不污染真实持仓（list_holdings / get_portfolio_summary）
- verify_hypothetical_tx 盈亏计算
- verify_all_hypothetical 汇总对比
- delete_hypothetical_tx 清理
"""
import pytest
from datetime import datetime, timedelta


# ── 快照 + 假设交易创建 ─────────────────────────────

def test_create_snapshot_creates_hypothetical_tx(tmp_db):
    """创建快照时自动创建假设交易。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical, list_snapshots
    from db._conn import _get_conn

    result = create_snapshot_with_hypothetical(
        fund_code="161725",
        fund_name="招商中证白酒",
        suggested_amount=5000,
        suggested_tier="-20%~-30%档",
        profit_rate_at_snapshot=-0.15,
        valuation_zscore=-1.2,
        current_price_at_snapshot=1.23,
    )
    assert result is not None
    assert result["snapshot_id"] > 0
    assert result["hypothetical_tx_id"] > 0

    # 验证快照已落库
    snapshots = list_snapshots(fund_code="161725")
    assert len(snapshots) >= 1
    assert snapshots[0]["suggested_amount"] == 5000
    assert snapshots[0]["hypothetical_tx_id"] == result["hypothetical_tx_id"]

    # 验证假设交易已落库
    conn = _get_conn()
    tx = conn.execute(
        "SELECT * FROM portfolio_transactions WHERE id = ? AND is_hypothetical = 1",
        (result["hypothetical_tx_id"],),
    ).fetchone()
    conn.close()
    assert tx is not None
    assert tx["fund_code"] == "161725"
    assert tx["amount"] == 5000
    assert tx["is_hypothetical"] == 1
    assert tx["transaction_type"] == "buy"


def test_create_snapshot_dedup_same_day(tmp_db):
    """同标的同日去重：旧的假设交易和快照被删除。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical, list_snapshots

    # 第一条
    r1 = create_snapshot_with_hypothetical(
        fund_code="000001", fund_name="基金A", suggested_amount=3000,
        suggested_tier=None, profit_rate_at_snapshot=-0.1,
        valuation_zscore=None, current_price_at_snapshot=1.5,
    )
    assert r1 is not None

    # 同日第二条（去重）
    r2 = create_snapshot_with_hypothetical(
        fund_code="000001", fund_name="基金A", suggested_amount=4000,
        suggested_tier=None, profit_rate_at_snapshot=-0.12,
        valuation_zscore=None, current_price_at_snapshot=1.4,
    )
    assert r2 is not None

    # 只剩一条快照
    snapshots = list_snapshots(fund_code="000001")
    assert len(snapshots) == 1
    assert snapshots[0]["suggested_amount"] == 4000  # 最新的
    assert snapshots[0]["id"] == r2["snapshot_id"]


def test_create_snapshot_zero_amount_returns_none(tmp_db):
    """建议金额为0时不创建快照。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical

    result = create_snapshot_with_hypothetical(
        fund_code="000002", fund_name="基金B", suggested_amount=0,
        suggested_tier=None, profit_rate_at_snapshot=0,
        valuation_zscore=None, current_price_at_snapshot=1.0,
    )
    assert result is None


# ── 假设交易不污染真实持仓 ──────────────────────────

def test_hypothetical_tx_not_in_list_holdings(tmp_db):
    """假设交易不出现在持仓列表中。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical
    from db.portfolio import list_holdings, create_holding

    # 创建一个真实持仓
    create_holding(fund_code="000003", fund_name="真实基金", shares=1000,
                   cost_price=1.0, current_price=1.1)

    # 创建假设交易（不同基金）
    create_snapshot_with_hypothetical(
        fund_code="000099", fund_name="假设基金", suggested_amount=5000,
        suggested_tier=None, profit_rate_at_snapshot=-0.2,
        valuation_zscore=None, current_price_at_snapshot=1.0,
    )

    holdings = list_holdings()
    fund_codes = [h["fund_code"] for h in holdings]
    assert "000003" in fund_codes  # 真实持仓在
    assert "000099" not in fund_codes  # 假设交易不出现


def test_hypothetical_tx_not_in_transactions_list(tmp_db):
    """假设交易不出现在交易记录列表中。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical
    from db.portfolio import list_transactions

    create_snapshot_with_hypothetical(
        fund_code="000088", fund_name="假设基金2", suggested_amount=3000,
        suggested_tier=None, profit_rate_at_snapshot=-0.1,
        valuation_zscore=None, current_price_at_snapshot=1.0,
    )

    txs = list_transactions()
    fund_codes = [t.get("fund_code") for t in txs]
    assert "000088" not in fund_codes


# ── 验证引擎 ───────────────────────────────────────

def test_verify_hypothetical_tx_with_nav_history(tmp_db):
    """有净值历史时验证盈亏计算。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical, list_hypothetical_txs
    from db._conn import _get_conn
    from services.counterfactual_verifier import verify_hypothetical_tx

    # 插入净值历史
    conn = _get_conn()
    buy_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    conn.executemany(
        "INSERT INTO fund_nav_history (fund_code, nav_date, nav) VALUES (?, ?, ?)",
        [
            ("000055", buy_date, 1.00),  # 买入日净值
            ("000055", today, 1.15),     # 当前净值（涨15%）
        ],
    )
    conn.commit()
    conn.close()

    # 创建假设交易
    create_snapshot_with_hypothetical(
        fund_code="000055", fund_name="验证基金", suggested_amount=1000,
        suggested_tier=None, profit_rate_at_snapshot=-0.1,
        valuation_zscore=None, current_price_at_snapshot=1.0,
    )

    txs = list_hypothetical_txs()
    assert len(txs) >= 1
    tx = [t for t in txs if t["fund_code"] == "000055"][0]

    result = verify_hypothetical_tx(tx)
    assert result["status"] == "verified"
    assert result["buy_price"] == 1.0
    assert result["current_price"] == 1.15
    assert result["buy_amount"] == 1000
    assert result["buy_shares"] == 1000  # 1000/1.0
    assert result["current_value"] == 1150  # 1000*1.15
    assert result["profit_loss"] == 150  # 1150-1000
    assert result["profit_rate"] == 0.15
    assert result["is_breakeven"] is True
    assert result["holding_days"] >= 29


def test_verify_hypothetical_tx_no_nav_data(tmp_db):
    """无净值历史时返回 no_nav_data 状态。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical, list_hypothetical_txs
    from services.counterfactual_verifier import verify_hypothetical_tx

    # 创建假设交易（无净值历史）
    create_snapshot_with_hypothetical(
        fund_code="000077", fund_name="新基金", suggested_amount=2000,
        suggested_tier=None, profit_rate_at_snapshot=0,
        valuation_zscore=None, current_price_at_snapshot=1.0,
    )

    txs = list_hypothetical_txs()
    tx = [t for t in txs if t["fund_code"] == "000077"][0]

    result = verify_hypothetical_tx(tx)
    # 没有净值历史但有快照价 → 会用快照价作为买入价，但当前净值取不到 → no_nav_data
    assert result["status"] == "no_nav_data"


def test_verify_all_hypothetical_summary(tmp_db):
    """汇总验证：多条假设交易的汇总统计。"""
    from db.smart_add_snapshots import create_snapshot_with_hypothetical
    from db._conn import _get_conn
    from services.counterfactual_verifier import verify_all_hypothetical

    # 插入净值
    conn = _get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    buy_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    conn.executemany(
        "INSERT INTO fund_nav_history (fund_code, nav_date, nav) VALUES (?, ?, ?)",
        [
            ("000066", buy_date, 1.00),
            ("000066", today, 1.10),  # 涨10%
        ],
    )
    conn.commit()
    conn.close()

    create_snapshot_with_hypothetical(
        fund_code="000066", fund_name="汇总基金", suggested_amount=2000,
        suggested_tier=None, profit_rate_at_snapshot=-0.05,
        valuation_zscore=None, current_price_at_snapshot=1.0,
    )

    result = verify_all_hypothetical()
    assert "hypothetical_txs" in result
    assert "summary" in result
    assert "comparison" in result

    summary = result["summary"]
    assert summary["total_count"] >= 1
    assert summary["total_hypothetical_invested"] >= 2000
    assert summary["breakeven_count"] >= 1


# ── 删除假设交易 ───────────────────────────────────

def test_delete_hypothetical_tx(tmp_db):
    """删除假设交易并清理快照关联。"""
    from db.smart_add_snapshots import (
        create_snapshot_with_hypothetical, delete_hypothetical_tx, list_hypothetical_txs
    )

    result = create_snapshot_with_hypothetical(
        fund_code="000044", fund_name="删除基金", suggested_amount=1000,
        suggested_tier=None, profit_rate_at_snapshot=0,
        valuation_zscore=None, current_price_at_snapshot=1.0,
    )
    tx_id = result["hypothetical_tx_id"]

    ok = delete_hypothetical_tx(tx_id)
    assert ok is True

    # 确认已删除
    txs = list_hypothetical_txs()
    fund_codes = [t["fund_code"] for t in txs]
    assert "000044" not in fund_codes


def test_delete_nonexistent_returns_false(tmp_db):
    """删除不存在的假设交易返回 False。"""
    from db.smart_add_snapshots import delete_hypothetical_tx
    assert delete_hypothetical_tx(99999) is False
