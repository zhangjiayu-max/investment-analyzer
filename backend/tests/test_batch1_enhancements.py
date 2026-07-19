"""Batch1 雷达与关注计划增强 — 单元测试。

覆盖 3 个增强点：
1. 退出机制（止盈/止损信号）：db.watchlist.update_entry_info / get_watchlist_with_exit_status
2. 异常波动预警：routers.portfolio.watchlist._calculate_volatility_alert
3. 事件影响量化：services.market.event_radar.analyze_event_impact
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from db.watchlist import (
    add_to_watchlist, get_watchlist_item, update_watchlist_item,
    update_entry_info, get_watchlist_with_exit_status,
)
from db.market_events import (
    create_market_event, get_market_event, update_market_event_fields,
)
from db.config import update_config as set_config, get_config_bool


# ── 增强点 1：退出机制 ──────────────────────────────────────────────

class TestExitMechanism:
    """退出机制：用户上车后，按止盈/止损百分比触发退出信号。"""

    def test_update_entry_info_sets_bought_status_and_fields(self, tmp_db):
        """update_entry_info 应同时设置 status=bought 与 4 个 entry 字段。"""
        wid = add_to_watchlist("000001", "测试基金A")
        ok = update_entry_info(
            wid,
            entry_price=1.2345,
            entry_date="2026-07-01",
            target_profit_pct=30.0,
            stop_loss_pct=10.0,
        )
        assert ok is True
        item = get_watchlist_item(wid)
        assert item["status"] == "bought"
        assert float(item["entry_price"]) == 1.2345
        assert item["entry_date"] == "2026-07-01"
        assert float(item["target_profit_pct"]) == 30.0
        assert float(item["stop_loss_pct"]) == 10.0
        # 重置退出信号
        assert item["exit_signal"] == "none"
        assert item["exit_signal_reason"] == ""

    def test_update_entry_info_partial_fields(self, tmp_db):
        """update_entry_info 支持只更新部分字段（如只设买入价）。"""
        wid = add_to_watchlist("000002", "测试基金B")
        ok = update_entry_info(wid, entry_price=2.0)
        assert ok is True
        item = get_watchlist_item(wid)
        assert float(item["entry_price"]) == 2.0
        # 未传的字段保持 NULL
        assert item["entry_date"] is None
        assert item["target_profit_pct"] is None
        assert item["stop_loss_pct"] is None

    def test_get_exit_status_profit_target(self, tmp_db):
        """止盈触发：current_nav 涨超 target_profit_pct → exit_signal=profit_target。"""
        wid = add_to_watchlist("000003", "测试基金C")
        update_entry_info(
            wid,
            entry_price=1.0,
            entry_date="2026-06-01",
            target_profit_pct=30.0,
            stop_loss_pct=10.0,
        )
        # 当前净值 1.4 → 涨幅 40% > 30% 止盈
        update_watchlist_item(wid, current_nav=1.4)
        result = get_watchlist_with_exit_status(wid)
        assert result is not None
        assert result["exit_signal"] == "profit_target"
        assert 39 <= result["pnl_pct"] <= 41
        assert "止盈目标" in result["exit_signal_reason"]

    def test_get_exit_status_stop_loss(self, tmp_db):
        """止损触发：current_nav 跌超 stop_loss_pct → exit_signal=stop_loss。"""
        wid = add_to_watchlist("000004", "测试基金D")
        update_entry_info(
            wid,
            entry_price=1.0,
            entry_date="2026-06-01",
            target_profit_pct=30.0,
            stop_loss_pct=10.0,
        )
        # 当前净值 0.85 → 跌幅 15% > 10% 止损
        update_watchlist_item(wid, current_nav=0.85)
        result = get_watchlist_with_exit_status(wid)
        assert result is not None
        assert result["exit_signal"] == "stop_loss"
        assert -16 <= result["pnl_pct"] <= -14
        assert "止损" in result["exit_signal_reason"]

    def test_get_exit_status_no_trigger(self, tmp_db):
        """盈亏在止盈/止损区间内 → exit_signal=none。"""
        wid = add_to_watchlist("000005", "测试基金E")
        update_entry_info(
            wid,
            entry_price=1.0,
            target_profit_pct=30.0,
            stop_loss_pct=10.0,
        )
        # 当前净值 1.05 → 涨幅 5%，未触发止盈或止损
        update_watchlist_item(wid, current_nav=1.05)
        result = get_watchlist_with_exit_status(wid)
        assert result is not None
        assert result["exit_signal"] == "none"
        assert 4 <= result["pnl_pct"] <= 6

    def test_get_exit_status_no_entry_price(self, tmp_db):
        """未设置买入价时，pnl_pct=None 且 exit_signal=none。"""
        wid = add_to_watchlist("000006", "测试基金F")
        update_watchlist_item(wid, current_nav=1.0)
        result = get_watchlist_with_exit_status(wid)
        assert result is not None
        assert result["pnl_pct"] is None
        assert result["exit_signal"] == "none"

    def test_get_exit_status_nonexistent(self, tmp_db):
        """查询不存在的关注记录返回 None。"""
        assert get_watchlist_with_exit_status(99999) is None


# ── 增强点 2：异常波动预警 ──────────────────────────────────────────

class TestVolatilityAlert:
    """异常波动预警：基于近 7 日净值序列计算日跌/周跌幅度，按阈值触发 severe/warning。"""

    def test_severe_daily_drop(self, tmp_db):
        """日跌幅超过 severe_daily_threshold（-3%）→ 触发 severe。"""
        # 准备：用 6 条历史净值构造一个跌 5% 的场景
        history = [
            {"nav": 1.0, "date": "2026-07-13"},
            {"nav": 1.0, "date": "2026-07-14"},
            {"nav": 1.0, "date": "2026-07-15"},
            {"nav": 1.0, "date": "2026-07-16"},
            {"nav": 1.0, "date": "2026-07-17"},
            {"nav": 1.0, "date": "2026-07-18"},  # 昨日净值
        ]
        current_nav = 0.95  # 今日 0.95，跌 5%

        # 默认阈值：severe_daily=-3.0%, warning_daily=-1.5%
        set_config("watchlist.volatility_severe_daily_threshold", "-3.0")
        set_config("watchlist.volatility_warning_daily_threshold", "-1.5")

        from routers.portfolio.watchlist import _calculate_volatility_alert
        with patch(
            "services.fund.fund_data_service.get_fund_nav_history_from_cache",
            return_value=history,
        ):
            alert, reason, daily_pct, weekly_pct = _calculate_volatility_alert(
                "000001", current_nav
            )
        assert alert == "severe"
        assert -5.1 <= daily_pct <= -4.9  # 跌约 5%
        assert "severe" in reason or "大跌" in reason or "跌" in reason

    def test_warning_daily_drop(self, tmp_db):
        """日跌幅介于 warning 与 severe 阈值之间 → 触发 warning。"""
        history = [
            {"nav": 1.0, "date": "2026-07-13"},
            {"nav": 1.0, "date": "2026-07-14"},
            {"nav": 1.0, "date": "2026-07-15"},
            {"nav": 1.0, "date": "2026-07-16"},
            {"nav": 1.0, "date": "2026-07-17"},
            {"nav": 1.0, "date": "2026-07-18"},
        ]
        current_nav = 0.98  # 跌 2%，介于 -1.5% 和 -3% 之间
        set_config("watchlist.volatility_severe_daily_threshold", "-3.0")
        set_config("watchlist.volatility_warning_daily_threshold", "-1.5")

        from routers.portfolio.watchlist import _calculate_volatility_alert
        with patch(
            "services.fund.fund_data_service.get_fund_nav_history_from_cache",
            return_value=history,
        ):
            alert, reason, daily_pct, weekly_pct = _calculate_volatility_alert(
                "000002", current_nav
            )
        assert alert == "warning"
        assert -2.1 <= daily_pct <= -1.9

    def test_no_alert_on_stable_nav(self, tmp_db):
        """净值无明显波动时不触发预警。"""
        history = [
            {"nav": 1.0, "date": "2026-07-13"},
            {"nav": 1.0, "date": "2026-07-14"},
            {"nav": 1.0, "date": "2026-07-15"},
            {"nav": 1.0, "date": "2026-07-16"},
            {"nav": 1.0, "date": "2026-07-17"},
            {"nav": 1.0, "date": "2026-07-18"},
        ]
        current_nav = 1.005  # 涨 0.5%

        from routers.portfolio.watchlist import _calculate_volatility_alert
        with patch(
            "services.fund.fund_data_service.get_fund_nav_history_from_cache",
            return_value=history,
        ):
            alert, reason, daily_pct, weekly_pct = _calculate_volatility_alert(
                "000003", current_nav
            )
        assert alert == "none"
        assert reason == ""

    def test_no_alert_on_insufficient_history(self, tmp_db):
        """历史净值不足 2 条时不触发预警（无法计算日变化）。"""
        history = [{"nav": 1.0, "date": "2026-07-18"}]
        current_nav = 0.9

        from routers.portfolio.watchlist import _calculate_volatility_alert
        with patch(
            "services.fund.fund_data_service.get_fund_nav_history_from_cache",
            return_value=history,
        ):
            alert, reason, daily_pct, weekly_pct = _calculate_volatility_alert(
                "000004", current_nav
            )
        assert alert == "none"
        assert daily_pct is None
        assert weekly_pct is None

    def test_weekly_drop_alert(self, tmp_db):
        """周跌幅超过 severe_weekly_threshold 但日跌幅未触发 → 仍应触发 severe。"""
        # 构造：前 5 天都是 1.0，昨日 0.99，今日 0.97 → 日跌 -2.02%（warning），周跌 -3%（severe）
        history = [
            {"nav": 1.0, "date": "2026-07-13"},
            {"nav": 1.0, "date": "2026-07-14"},
            {"nav": 1.0, "date": "2026-07-15"},
            {"nav": 1.0, "date": "2026-07-16"},
            {"nav": 1.0, "date": "2026-07-17"},
            {"nav": 0.99, "date": "2026-07-18"},
        ]
        current_nav = 0.97
        set_config("watchlist.volatility_severe_daily_threshold", "-3.0")
        set_config("watchlist.volatility_severe_weekly_threshold", "-2.0")

        from routers.portfolio.watchlist import _calculate_volatility_alert
        with patch(
            "services.fund.fund_data_service.get_fund_nav_history_from_cache",
            return_value=history,
        ):
            alert, reason, daily_pct, weekly_pct = _calculate_volatility_alert(
                "000005", current_nav
            )
        # 周跌 3% 超过 severe_weekly=-2%，应触发 severe
        assert alert == "severe"
        assert weekly_pct is not None
        assert weekly_pct <= -2.0


# ── 增强点 3：事件影响量化 ──────────────────────────────────────────

class TestEventImpactAnalysis:
    """事件影响量化：analyze_event_impact 调用 LLM 生成个性化影响分析。"""

    def test_disabled_returns_empty(self, tmp_db):
        """开关关闭时返回 error='深度解读开关未开启'。"""
        eid = create_market_event(
            title="美联储 7 月议息会议",
            summary="预计讨论降息",
            event_type="macro",
            direction="neutral",
            expected_date="2026-07-30",
            affected_sectors=["金融"],
            affected_themes=["利率"],
            confidence=0.8,
            sources=[],
        )
        # 确保开关关闭
        set_config("alerts.event_impact_analysis_enabled", "false")
        from services.market.event_radar import analyze_event_impact
        result = analyze_event_impact(eid)
        assert result["event_id"] == eid
        assert result["analysis"] == ""
        assert "开关" in result["error"]

    def test_event_not_found(self, tmp_db):
        """事件不存在时返回 error。"""
        set_config("alerts.event_impact_analysis_enabled", "true")
        from services.market.event_radar import analyze_event_impact
        result = analyze_event_impact("nonexistent_event_id")
        assert result["analysis"] == ""
        assert "事件不存在" in result["error"]
        assert result["cached"] is False

    def test_cache_hit_returns_cached_analysis(self, tmp_db):
        """缓存命中（impact_analyzed_at 在 7 天内）时直接返回缓存。"""
        eid = create_market_event(
            title="事件 A",
            summary="测试摘要",
            event_type="industry",
            direction="positive",
            expected_date="2026-08-01",
            affected_sectors=["半导体"],
            affected_themes=["芯片"],
            confidence=0.7,
            sources=[],
        )
        # 写入缓存
        cached_analysis = "### 事件核心\n这是缓存的影响分析"
        cached_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_market_event_fields(eid, {
            "impact_analysis": cached_analysis,
            "impact_analyzed_at": cached_at,
        })
        set_config("alerts.event_impact_analysis_enabled", "true")
        set_config("alerts.event_impact_analysis_cache_days", "7")

        from services.market.event_radar import analyze_event_impact
        result = analyze_event_impact(eid)
        assert result["cached"] is True
        assert result["analysis"] == cached_analysis
        assert result["analyzed_at"] == cached_at

    def test_cache_expired_triggers_llm(self, tmp_db):
        """缓存超过 7 天过期时重新调用 LLM。"""
        eid = create_market_event(
            title="事件 B",
            summary="测试摘要",
            event_type="policy",
            direction="positive",
            expected_date="2026-08-01",
            affected_sectors=["军工"],
            affected_themes=["国防"],
            confidence=0.8,
            sources=[],
        )
        # 写入 10 天前的缓存
        expired_at = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        update_market_event_fields(eid, {
            "impact_analysis": "旧的分析",
            "impact_analyzed_at": expired_at,
        })
        set_config("alerts.event_impact_analysis_enabled", "true")
        set_config("alerts.event_impact_analysis_cache_days", "7")

        # mock LLM 调用
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "### 新分析\n这是新生成的影响分析"
        from services.market.event_radar import analyze_event_impact
        with patch("services.market.event_radar._call_llm", return_value=mock_resp):
            with patch("db.portfolio.list_holdings", return_value=[]):
                result = analyze_event_impact(eid)
        assert result["cached"] is False
        assert "新分析" in result["analysis"]
        # 验证新分析已写回 DB
        updated = get_market_event(eid)
        assert "新分析" in updated["impact_analysis"]

    def test_llm_failure_returns_error(self, tmp_db):
        """LLM 调用失败时返回 error，不抛异常。"""
        eid = create_market_event(
            title="事件 C",
            summary="测试摘要",
            event_type="earnings",
            direction="neutral",
            expected_date="2026-08-01",
            affected_sectors=["消费"],
            affected_themes=["白酒"],
            confidence=0.5,
            sources=[],
        )
        set_config("alerts.event_impact_analysis_enabled", "true")

        from services.market.event_radar import analyze_event_impact
        with patch("services.market.event_radar._call_llm", side_effect=Exception("LLM 网络错误")):
            with patch("db.portfolio.list_holdings", return_value=[]):
                result = analyze_event_impact(eid)
        assert result["analysis"] == ""
        assert "LLM 调用失败" in result["error"]
        assert result["cached"] is False

    def test_update_market_event_fields_allows_impact_fields(self, tmp_db):
        """update_market_event_fields 应允许更新 impact_analysis 等影响量化字段。"""
        eid = create_market_event(
            title="事件 D",
            summary="测试",
            event_type="macro",
            direction="up",
            expected_date="2026-08-01",
            affected_sectors=[],
            affected_themes=[],
            confidence=0.5,
            sources=[],
        )
        ok = update_market_event_fields(eid, {
            "expected_impact_pct": 3.5,
            "impact_direction": "up",
            "impact_duration": "short_term",
            "impact_analysis": "测试影响分析",
            "impact_analyzed_at": "2026-07-19 12:00:00",
        })
        assert ok is True
        evt = get_market_event(eid)
        assert float(evt["expected_impact_pct"]) == 3.5
        assert evt["impact_direction"] == "up"
        assert evt["impact_duration"] == "short_term"
        assert evt["impact_analysis"] == "测试影响分析"
