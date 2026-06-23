"""DB Portfolio 层测试 — 持仓 CRUD、批量 upsert、查询。"""
import pytest
from db.portfolio import (
    create_holding, get_holding, get_holding_by_fund, list_holdings,
    delete_holding, update_holding, get_portfolio_summary,
    create_transaction, list_transactions,
)


class TestCreateHolding:
    def test_create_new(self, tmp_db):
        """新建持仓返回 ID。"""
        hid = create_holding("000001", "测试基金", shares=100, cost_price=1.5, current_price=1.5)
        assert hid > 0

    def test_create_duplicate_raises(self, tmp_db):
        """重复 fund_code 应报错。"""
        create_holding("000001", "基金A", shares=100, cost_price=1.0, current_price=1.0)
        with pytest.raises(ValueError, match="已存在"):
            create_holding("000001", "基金A", shares=200, cost_price=1.0, current_price=1.0)


class TestGetHolding:
    def test_get_existing(self, tmp_db):
        hid = create_holding("000002", "基金B", shares=500, cost_price=1.2, current_price=1.2)
        h = get_holding(hid)
        assert h is not None
        assert h["fund_code"] == "000002"
        assert h["shares"] == 500

    def test_get_nonexistent(self, tmp_db):
        assert get_holding(99999) is None

    def test_get_by_fund(self, tmp_db):
        create_holding("000003", "基金C", shares=100, cost_price=1.0, current_price=1.0)
        h = get_holding_by_fund("000003")
        assert h is not None
        assert h["fund_name"] == "基金C"


class TestListHoldings:
    def test_empty(self, tmp_db):
        assert list_holdings() == []

    def test_multiple(self, tmp_db):
        create_holding("001", "A", shares=100, cost_price=1.0, current_price=1.0)
        create_holding("002", "B", shares=200, cost_price=1.0, current_price=1.0)
        holdings = list_holdings()
        assert len(holdings) == 2

    def test_user_filter(self, tmp_db):
        """不同 user_id 隔离。"""
        create_holding("001", "A", shares=100, cost_price=1.0, current_price=1.0)
        from db._conn import _get_conn
        conn = _get_conn()
        conn.execute(
            "INSERT INTO portfolio_holdings (fund_code, fund_name, shares, user_id) VALUES (?, ?, ?, ?)",
            ("002", "B", 50, "user2"),
        )
        conn.commit()
        conn.close()

        holdings = list_holdings(user_id="default")
        assert len(holdings) == 1
        assert holdings[0]["fund_code"] == "001"


class TestDeleteHolding:
    def test_delete_existing(self, tmp_db):
        hid = create_holding("001", "A", shares=100, cost_price=1.0, current_price=1.0)
        assert delete_holding(hid) is True
        assert get_holding(hid) is None

    def test_delete_nonexistent(self, tmp_db):
        assert delete_holding(99999) is False


class TestUpdateHolding:
    def test_update_shares(self, tmp_db):
        hid = create_holding("001", "A", shares=100, cost_price=1.0, current_price=1.0)
        update_holding(hid, shares=200)
        h = get_holding(hid)
        assert h["shares"] == 200


class TestGetPortfolioSummary:
    def test_empty_portfolio(self, tmp_db):
        summary = get_portfolio_summary()
        assert summary["total_value"] == 0
        assert summary["holding_count"] == 0

    def test_with_holdings(self, tmp_db):
        create_holding("001", "A", shares=100, cost_price=1.0, current_price=1.0)
        create_holding("002", "B", shares=200, cost_price=1.5, current_price=1.5)
        summary = get_portfolio_summary()
        assert summary["holding_count"] == 2


class TestTransactions:
    def test_create_and_list(self, tmp_db):
        hid = create_holding("001", "A", shares=100, cost_price=1.0, current_price=1.0)
        create_transaction("001", "buy", 100, 100, 1.0, "confirmed", "2026-01-01", hid)
        txns = list_transactions()
        assert len(txns) >= 1
        assert txns[0]["transaction_type"] == "buy"

    def test_list_by_fund(self, tmp_db):
        hid = create_holding("001", "A", shares=100, cost_price=1.0, current_price=1.0)
        create_transaction("001", "buy", 50, 50, 1.0, "confirmed", "2026-01-01", hid)
        create_transaction("001", "buy", 30, 30, 1.0, "confirmed", "2026-02-01", hid)
        txns = list_transactions(fund_code="001")
        assert len(txns) == 2
