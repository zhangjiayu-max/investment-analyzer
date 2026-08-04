"""机会雷达 P0 统计的关键回归测试。"""

import inspect
import asyncio

from db._conn import _get_conn
from db.opportunities import get_backtest_stats
from services.advisor import opportunity_engine
from routers.decision.opportunities import opportunity_stats_api


def test_backtest_stats_preserve_source_and_deduplicate_overlapping_windows():
    assert "signal_source" in inspect.signature(
        opportunity_engine._create_opportunity_backtest
    ).parameters

    conn = _get_conn()
    rows = [
        ("测试主题", "2026-01-01", "2026-01-16", 1),
        ("测试主题", "2026-01-05", "2026-01-20", 0),
        ("测试主题", "2026-01-20", "2026-02-04", 1),
    ]
    conn.executemany(
        """INSERT INTO theme_opportunity_backtests
           (theme, entry_date, review_date, hit, signal_source, net_return, excess_return)
           VALUES (?, ?, ?, ?, 'valuation', 2.0, 1.0)""",
        rows,
    )
    conn.commit()
    conn.close()

    stats = get_backtest_stats()

    assert stats["raw_reviewed"] == 3
    assert stats["independent_sample_count"] == 2
    assert stats["reviewed"] == 2
    assert stats["hit"] == 2
    assert stats["hit_rate"] == 100.0
    assert stats["by_source"]["valuation"]["reviewed"] == 2
    assert stats["raw_by_source"]["valuation"]["reviewed"] == 3
    assert stats["learning_readiness"]["ready"] is False

    api_result = asyncio.run(opportunity_stats_api())
    assert api_result["backtest_stats"]["independent_sample_count"] == 2


def test_valuation_channel_card_preserves_signal_source(monkeypatch):
    saved = []
    monkeypatch.setattr(opportunity_engine, "_get_active_theme_rules", lambda: [])
    monkeypatch.setattr(
        opportunity_engine,
        "_scan_valuation_channel",
        lambda *args, **kwargs: [{
            "_theme_rule": {},
            "theme": "低估主题",
            "verdict": "avoid",
            "opportunity_score": 40,
        }],
    )
    monkeypatch.setattr(
        opportunity_engine, "_scan_holdings_loss_recovery", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        opportunity_engine,
        "save_opportunity",
        lambda item, user_id="default": saved.append(dict(item)) or 1,
    )

    opportunity_engine.scan_daily_opportunities(
        news_items=[], trade_date="2026-08-04", force_refresh=True
    )

    assert saved[0]["signal_source"] == "valuation"
