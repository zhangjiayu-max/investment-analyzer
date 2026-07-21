"""机会雷达深度研究增强测试 — L1 政策解读 + L2 深度评审 + L3 回测基准化。

覆盖：
- L1 _llm_policy_analysis：开关/调用失败/strong/weak/neutral 评分调整
- L2 _llm_deep_review：开关/非 can_buy 跳过/降级/不可升级/调用失败
- L3 review_opportunity_backtests：基准化命中/失败兜底/开关回退
- _get_benchmark_return：正常/失败/空数据
- _build_item 集成：L1+L2 端到端
"""
import json
from unittest.mock import patch, MagicMock

import pytest

from services.advisor.opportunity_engine import (
    _llm_policy_analysis,
    _llm_deep_review,
    _get_benchmark_return,
    THEME_RULES,
)


# ── L1 政策解读 ──────────────────────────────────

def test_llm_policy_analysis_disabled(monkeypatch):
    """开关关闭时不调用 LLM。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: False if k == "opportunity.llm_policy_analysis_enabled" else d)
    result = _llm_policy_analysis(THEME_RULES[0], [{"title": "测试", "summary": "测试"}])
    assert result is None


def test_llm_policy_analysis_no_news(monkeypatch):
    """无新闻时返回 None。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_policy_analysis_enabled" else d)
    result = _llm_policy_analysis(THEME_RULES[0], [])
    assert result is None


def test_llm_policy_analysis_strong(monkeypatch):
    """LLM 返回 strong+high → score_adjust +8。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_policy_analysis_enabled" else d)
    fake_resp = {"content": json.dumps({
        "policy_substance": "strong",
        "beneficiary_alignment": "high",
        "implementation_probability": "high",
        "key_risk": "估值偏高",
        "reasoning": "政策力度强，直接受益"
    })}
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: fake_resp)
    result = _llm_policy_analysis(THEME_RULES[0], [{"title": "测试", "summary": "测试"}])
    assert result is not None
    assert result["score_adjust"] == 8
    assert result["policy_substance"] == "strong"


def test_llm_policy_analysis_weak(monkeypatch):
    """LLM 返回 weak → score_adjust -5。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_policy_analysis_enabled" else d)
    fake_resp = {"content": json.dumps({
        "policy_substance": "weak",
        "beneficiary_alignment": "low",
        "implementation_probability": "low",
        "key_risk": "落地存疑",
        "reasoning": "政策力度弱，受益不直接"
    })}
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: fake_resp)
    result = _llm_policy_analysis(THEME_RULES[0], [{"title": "测试", "summary": "测试"}])
    assert result is not None
    assert result["score_adjust"] == -5


def test_llm_policy_analysis_neutral(monkeypatch):
    """LLM 返回 neutral → score_adjust 0。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_policy_analysis_enabled" else d)
    fake_resp = {"content": json.dumps({
        "policy_substance": "neutral",
        "beneficiary_alignment": "medium",
        "implementation_probability": "medium",
        "key_risk": "无",
        "reasoning": "政策影响中性"
    })}
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: fake_resp)
    result = _llm_policy_analysis(THEME_RULES[0], [{"title": "测试", "summary": "测试"}])
    assert result is not None
    assert result["score_adjust"] == 0


def test_llm_policy_analysis_failure_fallback(monkeypatch):
    """LLM 调用失败时返回 None（保留原 score）。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_policy_analysis_enabled" else d)
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: (_ for _ in ()).throw(Exception("LLM 超时")))
    result = _llm_policy_analysis(THEME_RULES[0], [{"title": "测试", "summary": "测试"}])
    assert result is None


def test_llm_policy_analysis_markdown_code_block(monkeypatch):
    """LLM 返回带 markdown 代码块时能正确解析。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_policy_analysis_enabled" else d)
    fake_resp = {"content": "```json\n" + json.dumps({
        "policy_substance": "strong",
        "beneficiary_alignment": "high",
        "implementation_probability": "high",
        "key_risk": "无",
        "reasoning": "测试"
    }) + "\n```"}
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: fake_resp)
    result = _llm_policy_analysis(THEME_RULES[0], [{"title": "测试", "summary": "测试"}])
    assert result is not None
    assert result["score_adjust"] == 8


# ── L2 深度评审 ──────────────────────────────────

def test_llm_deep_review_disabled(monkeypatch):
    """开关关闭时不调用 LLM。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: False if k == "opportunity.llm_deep_review_enabled" else d)
    item = {"verdict": "can_buy", "theme": "测试", "opportunity_score": 80}
    result = _llm_deep_review(item, None, "bull", "inflow", "fear")
    assert result is None


def test_llm_deep_review_not_can_buy(monkeypatch):
    """非 can_buy 候选不调用 LLM。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_deep_review_enabled" else d)
    item = {"verdict": "watch", "theme": "测试", "opportunity_score": 55}
    result = _llm_deep_review(item, None, "bull", "inflow", "fear")
    assert result is None


def test_llm_deep_review_downgrade(monkeypatch):
    """LLM 返回 watch → final_verdict 为 watch（降级）。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_deep_review_enabled" else d)
    fake_resp = {"content": json.dumps({
        "final_verdict": "watch",
        "confidence": "medium",
        "key_pros": ["估值低"],
        "key_cons": ["资金流出"],
        "net_assessment": "估值低但资金流出，建议观察",
        "timing_note": "等资金回流再入场"
    })}
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: fake_resp)
    item = {"verdict": "can_buy", "theme": "测试", "opportunity_score": 80, "summary": "测试"}
    result = _llm_deep_review(item, None, "bull", "outflow", "fear")
    assert result is not None
    assert result["final_verdict"] == "watch"


def test_llm_deep_review_no_upgrade_for_non_can_buy(monkeypatch):
    """LLM 返回 can_buy 但原 verdict 是 watch → 不应触发（因为非 can_buy 直接返回 None）。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_deep_review_enabled" else d)
    item = {"verdict": "watch", "theme": "测试", "opportunity_score": 55}
    # watch 不会调用 L2，直接返回 None
    result = _llm_deep_review(item, None, "bull", "inflow", "fear")
    assert result is None


def test_llm_deep_review_maintain_can_buy(monkeypatch):
    """LLM 返回 can_buy（维持原 verdict）。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_deep_review_enabled" else d)
    fake_resp = {"content": json.dumps({
        "final_verdict": "can_buy",
        "confidence": "high",
        "key_pros": ["估值低", "资金流入"],
        "key_cons": ["短期波动"],
        "net_assessment": "多维度支撑，可上车",
        "timing_note": "可立即入场"
    })}
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: fake_resp)
    item = {"verdict": "can_buy", "theme": "测试", "opportunity_score": 85, "summary": "测试"}
    result = _llm_deep_review(item, None, "bull", "inflow", "fear")
    assert result is not None
    assert result["final_verdict"] == "can_buy"


def test_llm_deep_review_invalid_verdict_fallback(monkeypatch):
    """LLM 返回无效 verdict → 保守降为 watch。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_deep_review_enabled" else d)
    fake_resp = {"content": json.dumps({
        "final_verdict": "invalid",
        "confidence": "low",
        "key_pros": [],
        "key_cons": [],
        "net_assessment": "异常",
        "timing_note": "异常"
    })}
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: fake_resp)
    item = {"verdict": "can_buy", "theme": "测试", "opportunity_score": 80, "summary": "测试"}
    result = _llm_deep_review(item, None, "bull", "inflow", "fear")
    assert result is not None
    assert result["final_verdict"] == "watch"


def test_llm_deep_review_failure_fallback(monkeypatch):
    """LLM 调用失败时返回 None（保留原 verdict）。"""
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.llm_deep_review_enabled" else d)
    monkeypatch.setattr("services.llm.llm_service._call_llm", lambda **kw: (_ for _ in ()).throw(Exception("LLM 超时")))
    item = {"verdict": "can_buy", "theme": "测试", "opportunity_score": 80, "summary": "测试"}
    result = _llm_deep_review(item, None, "bull", "inflow", "fear")
    assert result is None


# ── L3 回测基准化 ──────────────────────────────────

def test_get_benchmark_return_success(monkeypatch):
    """正常获取沪深300涨幅。"""
    import pandas as pd
    fake_df = pd.DataFrame({"收盘": [4000.0, 4100.0]})
    mock_ak = MagicMock()
    mock_ak.index_zh_a_hist = MagicMock(return_value=fake_df)
    monkeypatch.setitem(__import__("sys").modules, "akshare", mock_ak)
    result = _get_benchmark_return("2026-07-01", "2026-07-15")
    assert result is not None
    assert abs(result - 2.5) < 0.01  # (4100-4000)/4000*100 = 2.5


def test_get_benchmark_return_empty(monkeypatch):
    """akshare 返回空数据 → None。"""
    import pandas as pd
    mock_ak = MagicMock()
    mock_ak.index_zh_a_hist = MagicMock(return_value=pd.DataFrame())
    monkeypatch.setitem(__import__("sys").modules, "akshare", mock_ak)
    result = _get_benchmark_return("2026-07-01", "2026-07-15")
    assert result is None


def test_get_benchmark_return_failure(monkeypatch):
    """akshare 调用失败 → None。"""
    mock_ak = MagicMock()
    mock_ak.index_zh_a_hist = MagicMock(side_effect=Exception("网络错误"))
    monkeypatch.setitem(__import__("sys").modules, "akshare", mock_ak)
    result = _get_benchmark_return("2026-07-01", "2026-07-15")
    assert result is None


def test_get_benchmark_return_empty_dates():
    """空日期 → None。"""
    assert _get_benchmark_return("", "2026-07-15") is None
    assert _get_benchmark_return("2026-07-01", "") is None


def test_review_opportunity_backtests_benchmark_hit(monkeypatch):
    """L3 基准化：超额收益 >= 2% → hit=1。"""
    # 模拟：entry_price=100, review_price=106 (涨6%), benchmark=3% → 超额3% → hit
    from services.advisor import opportunity_engine
    from db.opportunities import update_opportunity_backtest

    # mock list_pending_backtests
    fake_pending = [{
        "id": 1, "theme": "人工智能", "review_date": "2026-07-15",
        "entry_date": "2026-07-01", "entry_price": 100.0,
    }]
    monkeypatch.setattr("db.opportunities.list_pending_backtests", lambda: fake_pending)

    # mock update
    updated = {}
    def fake_update(bid, fields):
        updated.update(fields)
        return True
    monkeypatch.setattr("db.opportunities.update_opportunity_backtest", fake_update)

    # mock 价格和基准
    monkeypatch.setattr(opportunity_engine, "_get_theme_index_price_at", lambda rule, date: 106.0)
    monkeypatch.setattr(opportunity_engine, "_get_benchmark_return", lambda e, r: 3.0)
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.benchmark_backtest_enabled" else d)

    result = opportunity_engine.review_opportunity_backtests()
    assert result["reviewed"] == 1
    assert result["hit"] == 1
    assert updated["hit"] == 1
    assert updated["benchmark_pct"] == 3.0
    assert updated["excess_return"] == 3.0  # 6 - 3 = 3


def test_review_opportunity_backtests_benchmark_miss(monkeypatch):
    """L3 基准化：超额收益 < 2% → hit=0（即使绝对涨幅 >= 3%）。"""
    from services.advisor import opportunity_engine

    fake_pending = [{
        "id": 1, "theme": "人工智能", "review_date": "2026-07-15",
        "entry_date": "2026-07-01", "entry_price": 100.0,
    }]
    monkeypatch.setattr("db.opportunities.list_pending_backtests", lambda: fake_pending)
    updated = {}
    monkeypatch.setattr("db.opportunities.update_opportunity_backtest", lambda bid, fields: updated.update(fields) or True)

    # 模拟：entry=100, review=104 (涨4%), benchmark=3% → 超额1% < 2% → miss
    monkeypatch.setattr(opportunity_engine, "_get_theme_index_price_at", lambda rule, date: 104.0)
    monkeypatch.setattr(opportunity_engine, "_get_benchmark_return", lambda e, r: 3.0)
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.benchmark_backtest_enabled" else d)

    result = opportunity_engine.review_opportunity_backtests()
    assert result["reviewed"] == 1
    assert result["hit"] == 0
    assert updated["hit"] == 0


def test_review_opportunity_backtests_disabled(monkeypatch):
    """开关关时回退原逻辑：绝对涨幅 >= 3% → hit=1。"""
    from services.advisor import opportunity_engine

    fake_pending = [{
        "id": 1, "theme": "人工智能", "review_date": "2026-07-15",
        "entry_date": "2026-07-01", "entry_price": 100.0,
    }]
    monkeypatch.setattr("db.opportunities.list_pending_backtests", lambda: fake_pending)
    updated = {}
    monkeypatch.setattr("db.opportunities.update_opportunity_backtest", lambda bid, fields: updated.update(fields) or True)

    # 涨 4%，原逻辑 >= 3% → hit
    monkeypatch.setattr(opportunity_engine, "_get_theme_index_price_at", lambda rule, date: 104.0)
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: False if k == "opportunity.benchmark_backtest_enabled" else d)

    result = opportunity_engine.review_opportunity_backtests()
    assert result["hit"] == 1
    assert updated["hit"] == 1
    # 开关关时不写 benchmark/excess_return
    assert "benchmark_pct" not in updated


def test_review_opportunity_backtests_benchmark_fallback(monkeypatch):
    """基准获取失败时回退原逻辑。"""
    from services.advisor import opportunity_engine

    fake_pending = [{
        "id": 1, "theme": "人工智能", "review_date": "2026-07-15",
        "entry_date": "2026-07-01", "entry_price": 100.0,
    }]
    monkeypatch.setattr("db.opportunities.list_pending_backtests", lambda: fake_pending)
    updated = {}
    monkeypatch.setattr("db.opportunities.update_opportunity_backtest", lambda bid, fields: updated.update(fields) or True)

    # 涨 4%，基准获取失败 → 回退原逻辑 >= 3% → hit
    monkeypatch.setattr(opportunity_engine, "_get_theme_index_price_at", lambda rule, date: 104.0)
    monkeypatch.setattr(opportunity_engine, "_get_benchmark_return", lambda e, r: None)
    monkeypatch.setattr("db.config.get_config_bool", lambda k, d: True if k == "opportunity.benchmark_backtest_enabled" else d)

    result = opportunity_engine.review_opportunity_backtests()
    assert result["hit"] == 1
    assert updated["hit"] == 1
    # 基准失败时不写 benchmark/excess_return
    assert "benchmark_pct" not in updated


# ── 配置注册验证 ──────────────────────────────────

def test_config_registration():
    """3 个新配置已注册到 DEFAULT_CONFIGS。"""
    from db.config import DEFAULT_CONFIGS
    keys = {k for k, _, _, _ in DEFAULT_CONFIGS}
    assert "opportunity.llm_policy_analysis_enabled" in keys
    assert "opportunity.llm_deep_review_enabled" in keys
    assert "opportunity.benchmark_backtest_enabled" in keys


def test_config_defaults():
    """LLM 相关默认 false，非 LLM 相关默认 true。"""
    from db.config import DEFAULT_CONFIGS
    config_dict = {k: v for k, v, _, _ in DEFAULT_CONFIGS}
    assert config_dict["opportunity.llm_policy_analysis_enabled"] == "false"
    assert config_dict["opportunity.llm_deep_review_enabled"] == "false"
    assert config_dict["opportunity.benchmark_backtest_enabled"] == "true"
