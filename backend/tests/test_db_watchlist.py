"""DB Watchlist 层测试。"""
import pytest
from db.watchlist import (
    add_to_watchlist, get_watchlist_item, get_watchlist_by_fund,
    list_watchlist, update_watchlist_item, remove_from_watchlist,
    get_watchlist_summary,
    # P0-3 信号回测 CRUD
    create_signal_backtest, has_signal_backtest_on_date,
    list_pending_signal_backtests, update_signal_backtest,
    get_signal_backtest_stats, get_fund_signal_backtest_history,
)


class TestAddWatchlist:
    def test_add_new(self, tmp_db):
        wid = add_to_watchlist("000001", "测试基金", "股票型", notes="观察中")
        assert wid > 0

    def test_add_duplicate_raises(self, tmp_db):
        add_to_watchlist("000001", "基金A")
        with pytest.raises(Exception):
            add_to_watchlist("000001", "基金A")


class TestGetWatchlist:
    def test_get_existing(self, tmp_db):
        wid = add_to_watchlist("000002", "基金B", "债券型")
        item = get_watchlist_item(wid)
        assert item is not None
        assert item["fund_code"] == "000002"

    def test_get_nonexistent(self, tmp_db):
        assert get_watchlist_item(99999) is None

    def test_get_by_fund(self, tmp_db):
        add_to_watchlist("000003", "基金C")
        item = get_watchlist_by_fund("000003")
        assert item is not None
        assert item["fund_name"] == "基金C"


class TestListWatchlist:
    def test_empty(self, tmp_db):
        assert list_watchlist() == []

    def test_multiple(self, tmp_db):
        add_to_watchlist("001", "A")
        add_to_watchlist("002", "B")
        items = list_watchlist()
        assert len(items) == 2

    def test_status_filter(self, tmp_db):
        add_to_watchlist("001", "A")
        wid = add_to_watchlist("002", "B")
        update_watchlist_item(wid, status="bought")
        items = list_watchlist(status="watching")
        assert len(items) == 1
        assert items[0]["fund_code"] == "001"


class TestUpdateWatchlist:
    def test_update_status(self, tmp_db):
        wid = add_to_watchlist("001", "A")
        update_watchlist_item(wid, status="bought")
        item = get_watchlist_item(wid)
        assert item["status"] == "bought"


class TestRemoveWatchlist:
    def test_remove_existing(self, tmp_db):
        wid = add_to_watchlist("001", "A")
        assert remove_from_watchlist(wid) is True
        assert get_watchlist_item(wid) is None

    def test_remove_nonexistent(self, tmp_db):
        assert remove_from_watchlist(99999) is False


class TestWatchlistSummary:
    def test_empty(self, tmp_db):
        summary = get_watchlist_summary()
        assert summary["total"] == 0

    def test_with_items(self, tmp_db):
        add_to_watchlist("001", "A")
        add_to_watchlist("002", "B")
        summary = get_watchlist_summary()
        assert summary["total"] == 2


# ── P0-3（2026-07-21）信号回测 CRUD 测试 ─────────────────────────────────


class TestSignalBacktestCRUD:
    """P0-3 信号回测表 CRUD 测试。"""

    def test_create_and_has_on_date(self, tmp_db):
        wid = add_to_watchlist("005001", "测试基金A")
        bt_id = create_signal_backtest({
            "watchlist_id": wid,
            "fund_code": "005001",
            "fund_name": "测试基金A",
            "signal_date": "2026-07-21",
            "signal_status": "green",
            "entry_nav": 1.50,
            "entry_percentile": 18.0,
            "review_date": "2026-08-11",
            "signal_confidence": 82,
        })
        assert bt_id > 0
        assert has_signal_backtest_on_date(wid, "2026-07-21") is True
        assert has_signal_backtest_on_date(wid, "2026-07-20") is False

    def test_list_pending(self, tmp_db):
        wid = add_to_watchlist("005002", "测试基金B")
        # 已到期未回测
        create_signal_backtest({
            "watchlist_id": wid, "fund_code": "005002",
            "fund_name": "B", "signal_date": "2026-06-01",
            "signal_status": "green", "entry_nav": 1.0,
            "entry_percentile": 15.0, "review_date": "2026-06-22",
        })
        pending = list_pending_signal_backtests()
        assert len(pending) >= 1
        assert pending[0]["fund_code"] == "005002"

    def test_update_and_stats(self, tmp_db):
        wid = add_to_watchlist("005003", "测试基金C")
        bt_id = create_signal_backtest({
            "watchlist_id": wid, "fund_code": "005003",
            "fund_name": "C", "signal_date": "2026-07-01",
            "signal_status": "green", "entry_nav": 1.00,
            "entry_percentile": 10.0, "review_date": "2026-07-22",
            "signal_confidence": 80,
        })
        update_signal_backtest(bt_id, review_nav=1.05, change_pct=5.0, hit=1,
                                reviewed_at="2026-07-22 10:00:00")
        stats = get_signal_backtest_stats(fund_code="005003")
        assert stats["reviewed"] == 1
        assert stats["hit"] == 1
        assert stats["hit_rate"] == 100.0

    def test_stats_empty(self, tmp_db):
        stats = get_signal_backtest_stats()
        assert stats["total"] == 0
        assert stats["hit_rate"] is None

    def test_history(self, tmp_db):
        wid = add_to_watchlist("005004", "测试基金D")
        for date in ["2026-07-01", "2026-07-05", "2026-07-10"]:
            create_signal_backtest({
                "watchlist_id": wid, "fund_code": "005004",
                "fund_name": "D", "signal_date": date,
                "signal_status": "green", "entry_nav": 1.0,
                "entry_percentile": 15.0, "review_date": "2026-08-01",
            })
        history = get_fund_signal_backtest_history("005004")
        assert len(history) == 3
        # 降序排列，最新在前
        assert history[0]["signal_date"] == "2026-07-10"
