"""DB Watchlist 层测试。"""
import pytest
from db.watchlist import (
    add_to_watchlist, get_watchlist_item, get_watchlist_by_fund,
    list_watchlist, update_watchlist_item, remove_from_watchlist,
    get_watchlist_summary,
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
