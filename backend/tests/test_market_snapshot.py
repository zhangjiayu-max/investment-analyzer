"""交易日行情快照的契约测试。

对应 2026-10-21 复核问题：日报与市场情报各自抓行情，同日得出相反的大盘数字，
且都没标注数据截止日。快照的职责是「同一交易日只有一份口径 + 明示截止日」。
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db.market_snapshot as snap_db  # noqa: E402


def _mem_store(monkeypatch):
    """把快照表替换成内存字典，避免测试写真实库。"""
    store = {}

    def _get(trade_date):
        item = store.get(trade_date)
        return dict(item) if item else None

    def _save(trade_date, as_of, payload, source="", is_intraday=False, fetched_at=""):
        store[trade_date] = {
            "trade_date": trade_date, "as_of": as_of, "is_intraday": is_intraday,
            "payload": payload, "source": source, "fetched_at": fetched_at,
        }
        return True

    monkeypatch.setattr(snap_db, "get_snapshot", _get, raising=False)
    monkeypatch.setattr(snap_db, "save_snapshot", _save, raising=False)
    return store


PAYLOAD = {
    "indices": [
        {"name": "上证指数", "price": 3713.03, "change_pct": -0.6, "volume_yi": 6428},
        {"name": "创业板指", "price": 2694.71, "change_pct": -1.62, "volume_yi": 3980},
    ],
    "breadth": {"up": 1981, "down": 3364, "limit_up": 40, "limit_down": 12, "total_volume_yi": 14421},
    "sectors_top": [{"name": "文化传媒", "change_pct": 2.1}],
    "sectors_bottom": [{"name": "电池", "change_pct": -3.42}],
}


def test_resolve_as_of_uses_latest_history_after_close(monkeypatch):
    from services.market import snapshot_service as svc
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    as_of, intraday = svc.resolve_as_of(datetime.datetime(2026, 10, 21, 23, 46))
    assert (as_of, intraday) == ("2026-10-20", False)


def test_resolve_as_of_uses_today_during_session(monkeypatch):
    from services.market import snapshot_service as svc
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    as_of, intraday = svc.resolve_as_of(datetime.datetime(2026, 10, 21, 10, 30))
    assert (as_of, intraday) == ("2026-10-21", True)


def test_snapshot_is_reused_within_window(monkeypatch):
    from services.market import snapshot_service as svc
    store = _mem_store(monkeypatch)
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return PAYLOAD, "test-source"

    monkeypatch.setattr(svc, "_fetch_overview", _fetch)

    now = datetime.datetime(2026, 10, 21, 23, 46)
    first = svc.get_trading_day_snapshot(now=now)
    second = svc.get_trading_day_snapshot(now=now + datetime.timedelta(minutes=1))
    assert calls["n"] == 1, "同日窗口内不应重复抓取"
    assert first["as_of"] == second["as_of"] == "2026-10-20"
    assert store["2026-10-20"]["payload"]["breadth"]["down"] == 3364


def test_snapshot_refetches_when_forced(monkeypatch):
    from services.market import snapshot_service as svc
    _mem_store(monkeypatch)
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        return PAYLOAD, "test-source"

    monkeypatch.setattr(svc, "_fetch_overview", _fetch)
    now = datetime.datetime(2026, 10, 21, 23, 46)
    svc.get_trading_day_snapshot(now=now)
    svc.get_trading_day_snapshot(force=True, now=now)
    assert calls["n"] == 2


def test_format_text_declares_as_of_and_scope():
    from services.market.snapshot_service import format_snapshot_text
    text = format_snapshot_text({
        "as_of": "2026-10-20", "is_intraday": False, "source": "akshare/eastmoney",
        "payload": PAYLOAD,
    })
    assert "数据截止 2026-10-20" in text
    assert "唯一" in text or "只使用本条快照" in text
    assert "上证指数" in text and "-0.6%" in text
    assert "上涨1981 / 下跌3364" in text


def test_format_text_blocks_hallucination_when_empty():
    from services.market.snapshot_service import format_snapshot_text
    text = format_snapshot_text({"payload": {}})
    assert "未获取到行情数据" in text
    assert "禁止" in text


def test_snapshot_falls_back_to_stale_on_fetch_failure(monkeypatch):
    from services.market import snapshot_service as svc
    store = _mem_store(monkeypatch)
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    store["2026-10-20"] = {
        "trade_date": "2026-10-20", "as_of": "2026-10-20", "is_intraday": False,
        "payload": PAYLOAD, "source": "old", "fetched_at": "2026-10-20 15:10:00",
    }
    monkeypatch.setattr(svc, "_fetch_overview", lambda: ({}, ""))
    snap = svc.get_trading_day_snapshot(now=datetime.datetime(2026, 10, 21, 23, 46))
    assert snap["payload"]["breadth"]["up"] == 1981
    assert "stale" in snap["source"]


def test_peek_never_triggers_fetch(monkeypatch):
    """工具路径用 peek：没有当日快照时也不允许发起网络抓取。"""
    from services.market import snapshot_service as svc
    _mem_store(monkeypatch)
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    called = {"n": 0}

    def _fetch():
        called["n"] += 1
        return PAYLOAD, "test-source"

    monkeypatch.setattr(svc, "_fetch_overview", _fetch)
    snap = svc.get_trading_day_snapshot(peek=True, now=datetime.datetime(2026, 10, 21, 23, 46))
    assert called["n"] == 0
    assert snap["payload"] == {}


def test_peek_returns_cached_snapshot(monkeypatch):
    from services.market import snapshot_service as svc
    store = _mem_store(monkeypatch)
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    monkeypatch.setattr(svc, "_fetch_overview", lambda: (_ for _ in ()).throw(RuntimeError("no net")))
    store["2026-10-20"] = {
        "trade_date": "2026-10-20", "as_of": "2026-10-20", "is_intraday": False,
        "payload": PAYLOAD, "source": "akshare", "fetched_at": "2026-10-20 15:10:00",
    }
    snap = svc.get_trading_day_snapshot(peek=True, now=datetime.datetime(2026, 10, 21, 23, 46))
    assert snap["payload"]["breadth"]["down"] == 3364


def test_peek_never_triggers_fetch(monkeypatch):
    """工具路径用 peek：没有当日快照时也不允许发起网络抓取。"""
    from services.market import snapshot_service as svc
    _mem_store(monkeypatch)
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    called = {"n": 0}

    def _fetch():
        called["n"] += 1
        return PAYLOAD, "test-source"

    monkeypatch.setattr(svc, "_fetch_overview", _fetch)
    snap = svc.get_trading_day_snapshot(peek=True, now=datetime.datetime(2026, 10, 21, 23, 46))
    assert called["n"] == 0
    assert snap["payload"] == {}


def test_peek_returns_cached_snapshot(monkeypatch):
    from services.market import snapshot_service as svc
    store = _mem_store(monkeypatch)
    monkeypatch.setattr(svc, "_latest_history_date", lambda: "2026-10-20")
    monkeypatch.setattr(svc, "_fetch_overview", lambda: (_ for _ in ()).throw(RuntimeError("no net")))
    store["2026-10-20"] = {
        "trade_date": "2026-10-20", "as_of": "2026-10-20", "is_intraday": False,
        "payload": PAYLOAD, "source": "akshare", "fetched_at": "2026-10-20 15:10:00",
    }
    snap = svc.get_trading_day_snapshot(peek=True, now=datetime.datetime(2026, 10, 21, 23, 46))
    assert snap["payload"]["breadth"]["down"] == 3364
