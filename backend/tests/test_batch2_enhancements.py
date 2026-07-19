"""Batch2 雷达与关注计划增强 — 单元测试。

覆盖 3 个增强点（纯计算，无 LLM）：
1. 关注计划自动剔除已上车：db.watchlist.auto_mark_bought_on_trade
2. 事件影响金额估算：services.market.event_radar.estimate_event_impact_amount
3. 事件置信度时间衰减：services.market.event_radar._time_decay_factor / apply_time_decay_to_confidence
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from db.watchlist import (
    add_to_watchlist, get_watchlist_item, list_watchlist,
    auto_mark_bought_on_trade,
)
from db.market_events import create_market_event, get_market_event
from db.config import update_config as set_config


# ── 增强点 1：关注计划自动剔除已上车 ──────────────────────────────────

class TestAutoMarkBought:
    """portfolio 买入时自动把 watching 状态的 watchlist 标为 bought。"""

    def test_auto_mark_single_watching(self, tmp_db):
        """单条 watching 记录被自动标为 bought，并填入 entry_price/entry_date。"""
        wid = add_to_watchlist("000001", "测试基金A")
        affected = auto_mark_bought_on_trade("000001", 1.2345, "2026-07-19")
        assert affected == 1
        item = get_watchlist_item(wid)
        assert item["status"] == "bought"
        assert float(item["entry_price"]) == 1.2345
        assert item["entry_date"] == "2026-07-19"
        assert item["exit_signal"] == "none"
        assert item["exit_signal_reason"] == ""

    def test_auto_mark_skips_already_bought(self, tmp_db):
        """已 bought 的记录不再被更新（避免覆盖用户手动设置的目标止盈/止损）。"""
        wid = add_to_watchlist("000002", "测试基金B")
        # 先标为 bought 并设置目标止盈/止损
        from db.watchlist import update_entry_info
        update_entry_info(wid, entry_price=1.0, entry_date="2026-07-01",
                          target_profit_pct=30.0, stop_loss_pct=10.0)
        # 再次触发自动标记
        affected = auto_mark_bought_on_trade("000002", 2.0, "2026-07-19")
        assert affected == 0  # 没有匹配 watching 状态的记录
        item = get_watchlist_item(wid)
        # 原有数据保持不变
        assert float(item["entry_price"]) == 1.0
        assert item["entry_date"] == "2026-07-01"
        assert float(item["target_profit_pct"]) == 30.0

    def test_auto_mark_multiple_funds_same_code(self, tmp_db):
        """同 fund_code 多条 watching 记录都被标记（理论上不应有多条，但逻辑应正确）。"""
        # watchlist 表有 UNIQUE(fund_code, user_id) 约束，所以同 code 只能一条
        # 这里测试跨 fund_code 多条记录
        add_to_watchlist("000003", "基金C")
        add_to_watchlist("000004", "基金D")
        affected = auto_mark_bought_on_trade("000003", 1.5, "2026-07-19")
        assert affected == 1
        # 另一条仍是 watching
        items = list_watchlist(status="watching")
        codes = [i["fund_code"] for i in items]
        assert "000004" in codes
        assert "000003" not in codes

    def test_auto_mark_no_match_returns_zero(self, tmp_db):
        """没有匹配 fund_code 时返回 0。"""
        add_to_watchlist("000005", "基金E")
        affected = auto_mark_bought_on_trade("999999", 1.0, "2026-07-19")
        assert affected == 0

    def test_auto_mark_idempotent(self, tmp_db):
        """重复调用幂等：第二次因 watching 已变 bought 而 affected=0。"""
        add_to_watchlist("000006", "基金F")
        affected1 = auto_mark_bought_on_trade("000006", 1.0, "2026-07-19")
        affected2 = auto_mark_bought_on_trade("000006", 1.5, "2026-07-20")
        assert affected1 == 1
        assert affected2 == 0  # 第二次无匹配


# ── 增强点 2：事件影响金额估算 ──────────────────────────────────────────

class TestEventImpactAmount:
    """estimate_event_impact_amount 纯计算函数。"""

    def test_no_impact_pct_returns_reason(self, tmp_db):
        """事件无 expected_impact_pct 时返回 reason。"""
        eid = create_market_event(
            title="事件 A", summary="", event_type="industry",
            direction="positive", expected_date="2026-08-01",
            affected_sectors=["半导体"], affected_themes=[],
            confidence=0.8, sources=[],
        )
        from services.market.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(get_market_event(eid))
        assert result["total_impact_amount"] == 0.0
        assert "expected_impact_pct" in result["reason"] or "为空" in result["reason"]

    def test_no_holdings_returns_zero(self, tmp_db):
        """用户无持仓时返回 0。"""
        eid = create_market_event(
            title="事件 B", summary="", event_type="industry",
            direction="positive", expected_date="2026-08-01",
            affected_sectors=["半导体"], affected_themes=[],
            confidence=0.8, sources=[],
        )
        from db.market_events import update_market_event_fields
        update_market_event_fields(eid, {"expected_impact_pct": -3.5})

        from services.market.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(get_market_event(eid), holdings=[])
        assert result["total_impact_amount"] == 0.0
        assert "无持仓" in result["reason"]

    def test_amount_calculation_correct(self, tmp_db):
        """金额计算正确：影响金额 = impact_pct × holding_value / 100。"""
        eid = create_market_event(
            title="事件 C", summary="", event_type="industry",
            direction="negative", expected_date="2026-08-01",
            affected_sectors=["军工"], affected_themes=[],
            confidence=0.8, sources=[],
        )
        from db.market_events import update_market_event_fields
        update_market_event_fields(eid, {"expected_impact_pct": -3.0})

        # 构造持仓：1 只军工基金，市值 10000
        holdings = [{
            "fund_code": "161031", "fund_name": "军工ETF",
            "index_code": "399967", "index_name": "中证军工",
            "current_value": 10000.0,
        }]
        from services.market.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(
            get_market_event(eid), holdings=holdings, portfolio_total=10000.0
        )
        assert result["total_impact_amount"] == -300.0  # -3% × 10000
        assert len(result["affected_holdings"]) == 1
        h = result["affected_holdings"][0]
        assert h["fund_code"] == "161031"
        assert h["impact_amount"] == -300.0
        assert h["holding_value"] == 10000.0
        assert h["weight"] == 100.0  # 10000/10000*100

    def test_affected_holdings_sorted_by_amount_abs(self, tmp_db):
        """受影响持仓按金额绝对值降序。"""
        eid = create_market_event(
            title="事件 D", summary="", event_type="industry",
            direction="negative", expected_date="2026-08-01",
            affected_sectors=["军工"], affected_themes=[],
            confidence=0.8, sources=[],
        )
        from db.market_events import update_market_event_fields
        update_market_event_fields(eid, {"expected_impact_pct": -5.0})

        # 两只军工基金：一大一小
        holdings = [
            {"fund_code": "161031", "fund_name": "军工ETF小",
             "index_code": "399967", "index_name": "中证军工", "current_value": 5000.0},
            {"fund_code": "502006", "fund_name": "军工LOF大",
             "index_code": "399967", "index_name": "中证军工", "current_value": 20000.0},
        ]
        from services.market.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(
            get_market_event(eid), holdings=holdings, portfolio_total=25000.0
        )
        assert len(result["affected_holdings"]) == 2
        # 大的在前面
        assert result["affected_holdings"][0]["fund_code"] == "502006"
        assert result["affected_holdings"][1]["fund_code"] == "161031"

    def test_portfolio_total_zero_returns_reason(self, tmp_db):
        """portfolio_total ≤ 0 时返回 reason。"""
        eid = create_market_event(
            title="事件 E", summary="", event_type="industry",
            direction="positive", expected_date="2026-08-01",
            affected_sectors=["半导体"], affected_themes=[],
            confidence=0.8, sources=[],
        )
        from db.market_events import update_market_event_fields
        update_market_event_fields(eid, {"expected_impact_pct": 3.0})

        holdings = [{"fund_code": "159995", "fund_name": "芯片ETF",
                     "index_code": "990001", "index_name": "国证芯片",
                     "current_value": 0.0}]
        from services.market.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(
            get_market_event(eid), holdings=holdings, portfolio_total=0.0
        )
        assert result["total_impact_amount"] == 0.0
        assert "持仓总市值" in result["reason"]

    def test_no_affected_holdings_returns_zero_total(self, tmp_db):
        """事件 affected_sectors 不匹配任何持仓时返回 0 总额。"""
        eid = create_market_event(
            title="事件 F", summary="", event_type="industry",
            direction="positive", expected_date="2026-08-01",
            affected_sectors=["新能源"], affected_themes=[],
            confidence=0.8, sources=[],
        )
        from db.market_events import update_market_event_fields
        update_market_event_fields(eid, {"expected_impact_pct": 3.0})

        # 持仓中没有新能源相关基金
        holdings = [{"fund_code": "161031", "fund_name": "军工ETF",
                     "index_code": "399967", "index_name": "中证军工",
                     "current_value": 10000.0}]
        from services.market.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(
            get_market_event(eid), holdings=holdings, portfolio_total=10000.0
        )
        assert result["total_impact_amount"] == 0.0
        assert len(result["affected_holdings"]) == 0


# ── 增强点 3：事件置信度时间衰减 ────────────────────────────────────────

class TestConfidenceTimeDecay:
    """_time_decay_factor / apply_time_decay_to_confidence / attach_effective_confidence。"""

    def test_upcoming_no_decay(self, tmp_db):
        """upcoming 状态不衰减，factor=1.0。"""
        from services.market.event_radar import _time_decay_factor
        event = {"status": "upcoming", "verification_result": None,
                 "expired_date": None}
        assert _time_decay_factor(event) == 1.0

    def test_imminent_no_decay(self, tmp_db):
        """imminent 状态不衰减（即将发生）。"""
        from services.market.event_radar import _time_decay_factor
        event = {"status": "imminent", "verification_result": None,
                 "expired_date": None}
        assert _time_decay_factor(event) == 1.0

    def test_materialized_no_decay(self, tmp_db):
        """materialized 状态不衰减（已发生，等待验证）。"""
        from services.market.event_radar import _time_decay_factor
        event = {"status": "materialized", "verification_result": None,
                 "expired_date": None}
        assert _time_decay_factor(event) == 1.0

    def test_expired_verified_no_decay(self, tmp_db):
        """已验证的过期事件不衰减。"""
        from services.market.event_radar import _time_decay_factor
        event = {"status": "expired", "verification_result": '{"result":"correct"}',
                 "expired_date": "2025-01-01"}
        assert _time_decay_factor(event) == 1.0

    def test_expired_unverified_within_30_days_factor_07(self, tmp_db):
        """未验证的过期事件 30 天内 factor=0.7。"""
        from services.market.event_radar import _time_decay_factor
        # 过期 10 天
        expired_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        event = {"status": "expired", "verification_result": None,
                 "expired_date": expired_date}
        assert _time_decay_factor(event) == 0.7

    def test_expired_unverified_beyond_30_days_factor_03(self, tmp_db):
        """未验证的过期事件超过 30 天 factor=0.3。"""
        from services.market.event_radar import _time_decay_factor
        # 过期 45 天
        expired_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        event = {"status": "expired", "verification_result": None,
                 "expired_date": expired_date}
        assert _time_decay_factor(event) == 0.3

    def test_expired_no_expired_date_factor_05(self, tmp_db):
        """无 expired_date 的未验证过期事件 factor=0.5。"""
        from services.market.event_radar import _time_decay_factor
        event = {"status": "expired", "verification_result": None,
                 "expired_date": None}
        assert _time_decay_factor(event) == 0.5

    def test_apply_time_decay_returns_correct_value(self, tmp_db):
        """apply_time_decay_to_confidence 返回 original × factor。"""
        from services.market.event_radar import apply_time_decay_to_confidence
        # expired + 未验证 + 30 天内 + confidence=0.8
        expired_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        event = {"confidence": 0.8, "status": "expired",
                 "verification_result": None, "expired_date": expired_date}
        # 0.8 × 0.7 = 0.56
        assert apply_time_decay_to_confidence(event) == 0.56

    def test_apply_time_decay_keeps_original_when_no_decay(self, tmp_db):
        """无衰减时 effective = original。"""
        from services.market.event_radar import apply_time_decay_to_confidence
        event = {"confidence": 0.7, "status": "upcoming"}
        assert apply_time_decay_to_confidence(event) == 0.7

    def test_attach_effective_confidence_disabled_does_nothing(self, tmp_db):
        """开关关闭时 attach 不修改事件数据。"""
        set_config("alerts.event_confidence_time_decay_enabled", "false")
        from services.market.event_radar import attach_effective_confidence
        expired_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        event = {"confidence": 0.8, "status": "expired",
                 "verification_result": None, "expired_date": expired_date}
        attach_effective_confidence(event)
        assert "effective_confidence" not in event

    def test_attach_effective_confidence_enabled_attaches_field(self, tmp_db):
        """开关开启时附加 effective_confidence 字段。"""
        set_config("alerts.event_confidence_time_decay_enabled", "true")
        from services.market.event_radar import attach_effective_confidence
        expired_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        event = {"confidence": 0.8, "status": "expired",
                 "verification_result": None, "expired_date": expired_date}
        attach_effective_confidence(event)
        assert "effective_confidence" in event
        assert event["effective_confidence"] == 0.56

    def test_attach_effective_confidence_list(self, tmp_db):
        """列表场景：每条事件都附加字段。"""
        set_config("alerts.event_confidence_time_decay_enabled", "true")
        from services.market.event_radar import attach_effective_confidence
        expired_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        events = [
            {"confidence": 0.8, "status": "upcoming"},  # 不衰减 → 0.8
            {"confidence": 0.8, "status": "expired",
             "verification_result": None, "expired_date": expired_date},  # → 0.56
        ]
        attach_effective_confidence(events)
        assert events[0]["effective_confidence"] == 0.8
        assert events[1]["effective_confidence"] == 0.56
