"""仲裁分歧检测修复测试 — conv#131 根因修复验证。

覆盖：
- _detect_stance 优先级修复（sell > buy > hold）
- hold 关键词收窄（移除"风险""谨慎"）
- sell 关键词移除"止盈"，新增"清仓""赎回"
- buy 关键词新增"定投""补仓"
- hold 门槛改为过半支持
- disagreements 只含 buy/sell（不含 hold）
- key_conflicts 只检测 buy_vs_sell
- 回归：无假冲突
"""
import pytest

from agent.core.arbitration import (
    _detect_stance,
    arbitrate_results,
)


# ── _detect_stance 优先级修复 ──────────────────────

def test_sell_priority_over_hold():
    """专家说"建议卖出，注意风险"应识别为 sell（原识别为 hold）。"""
    assert _detect_stance("建议卖出该基金，注意流动性风险") == "sell"


def test_buy_priority_over_hold():
    """专家说"建议买入，注意风险"应识别为 buy（原识别为 hold）。"""
    assert _detect_stance("建议买入该基金，注意短期波动风险") == "buy"


def test_hold_keyword_narrowed():
    """专家只说"风险较大"不指定操作，应识别为 unknown（原识别为 hold）。"""
    assert _detect_stance("当前板块估值较高，回调风险较大") == "unknown"


def test_hold_keyword_narrowed_cautious():
    """专家只说"谨慎"不指定操作，应识别为 unknown（原识别为 hold）。"""
    assert _detect_stance("建议保持谨慎，关注后续走势") == "unknown"


def test_stop_profit_not_sell():
    """专家说"止盈"应识别为 unknown（原识别为 sell）。

    止盈是盈利后的操作，说明标的在盈利，不应与"止损"混为 sell。
    """
    assert _detect_stance("该基金已盈利15%，可考虑止盈") == "unknown"


def test_stop_loss_is_sell():
    """专家说"止损"应识别为 sell（亏损保护性操作）。"""
    assert _detect_stance("该基金跌破止损线，建议止损") == "sell"


def test_sell_new_keywords():
    """sell 关键词新增"清仓""赎回"覆盖更全。"""
    assert _detect_stance("建议清仓该基金") == "sell"
    assert _detect_stance("建议赎回该基金") == "sell"


def test_buy_new_keywords():
    """buy 关键词新增"定投""补仓"覆盖用户常见操作意图。"""
    assert _detect_stance("建议开启定投计划") == "buy"
    assert _detect_stance("建议补仓该基金") == "buy"


def test_explicit_hold_keyword():
    """专家明确说"持有"应识别为 hold（保留 hold 关键词有效性）。"""
    assert _detect_stance("建议持有该基金，不动") == "hold"


def test_unknown_when_no_keyword():
    """无任何关键词时返回 unknown。"""
    assert _detect_stance("该基金表现一般") == "unknown"


# ── hold 门槛改为过半支持 ──────────────────────────

def test_hold_needs_majority():
    """3 专家中 1 个 hold + 2 个 buy，final_stance 应为 buy（原为 hold）。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议持有，不动"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议买入，分批建仓"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "建议加仓"},
    ]
    summary = arbitrate_results("test query", results)
    assert summary["final_stance"] == "buy"


def test_hold_with_majority():
    """3 专家中 2 个 hold + 1 个 buy，final_stance 应为 hold（过半支持）。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议持有，不动"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议观望"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "建议买入"},
    ]
    summary = arbitrate_results("test query", results)
    assert summary["final_stance"] == "hold"
    # 同时有 buy，应该是 conflict 模式
    assert summary["arbitration_mode"] == "conflict"


def test_all_hold_is_consensus():
    """3 专家全 hold，final_stance 为 hold，consensus 模式。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议持有"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议观望"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "不建议加仓"},
    ]
    summary = arbitrate_results("test query", results)
    assert summary["final_stance"] == "hold"
    assert summary["arbitration_mode"] == "consensus"


def test_sell_minority_beats_hold_minority():
    """3 专家中 1 个 sell + 1 个 hold + 1 个 unknown，final_stance 应为 sell。

    hold 未过半（1/2），sell 明确意图优先。
    """
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议卖出"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议持有"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "该基金表现一般"},
    ]
    summary = arbitrate_results("test query", results)
    assert summary["final_stance"] == "sell"


# ── disagreements 只含 buy/sell ────────────────────

def test_disagreements_exclude_hold():
    """disagreements 不含 hold 立场专家。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议买入"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议卖出"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "建议持有"},
    ]
    summary = arbitrate_results("test query", results)
    stances_in_disagreements = [d["stance"] for d in summary["disagreements"]]
    assert "hold" not in stances_in_disagreements
    assert "buy" in stances_in_disagreements
    assert "sell" in stances_in_disagreements


def test_disagreements_exclude_unknown():
    """disagreements 不含 unknown 立场专家。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议买入"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "该基金表现一般"},
    ]
    summary = arbitrate_results("test query", results)
    assert len(summary["disagreements"]) == 1
    assert summary["disagreements"][0]["stance"] == "buy"


# ── key_conflicts 只检测 buy_vs_sell ───────────────

def test_key_conflicts_buy_vs_sell():
    """1 buy + 1 sell 应触发 key_conflicts（原因 hold 误判不触发）。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议买入，估值低"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议卖出，趋势走坏"},
    ]
    summary = arbitrate_results("test query", results)
    assert len(summary["key_conflicts"]) == 1
    assert summary["key_conflicts"][0]["type"] == "buy_vs_sell"
    assert "专家A" in summary["key_conflicts"][0]["buy_side"]
    assert "专家B" in summary["key_conflicts"][0]["sell_side"]


def test_key_conflicts_with_risk_keywords():
    """1 buy（含"风险"）+ 1 sell（含"风险"）应触发 key_conflicts。

    原逻辑：hold 关键词含"风险"→ 两专家都被识别为 hold → 无冲突。
    修复后：sell > buy > hold，"风险"不再触发 hold → 真实冲突被检测。
    """
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议买入，注意短期波动风险"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议卖出，流动性风险较大"},
    ]
    summary = arbitrate_results("test query", results)
    assert len(summary["key_conflicts"]) == 1
    assert summary["key_conflicts"][0]["type"] == "buy_vs_sell"


def test_regression_no_false_conflict():
    """3 专家全 buy 不应触发 key_conflicts。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议买入"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议加仓"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "建议定投"},
    ]
    summary = arbitrate_results("test query", results)
    assert len(summary["key_conflicts"]) == 0
    assert summary["arbitration_mode"] == "consensus"


def test_no_false_conflict_all_hold():
    """3 专家全 hold 不应触发 key_conflicts。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议持有"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议观望"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "不建议加仓"},
    ]
    summary = arbitrate_results("test query", results)
    assert len(summary["key_conflicts"]) == 0


def test_no_false_conflict_buy_and_hold():
    """1 buy + 2 hold（过半）不应触发 key_conflicts（hold vs buy 不算冲突）。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议买入"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议持有"},
        {"agent_key": "a3", "agent": "专家C", "analysis": "建议观望"},
    ]
    summary = arbitrate_results("test query", results)
    # final_stance 为 hold（过半），但有 buy，是 conflict 模式
    assert summary["final_stance"] == "hold"
    assert summary["arbitration_mode"] == "conflict"
    # 但 key_conflicts 应为空（buy vs hold 不算真实冲突）
    assert len(summary["key_conflicts"]) == 0


# ── 兼容字段验证 ──────────────────────────────────

def test_verdict_field_consistency():
    """verdict 字段与 final_stance 一致（兼容 _save_final）。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议买入"},
        {"agent_key": "a2", "agent": "专家B", "analysis": "建议加仓"},
    ]
    summary = arbitrate_results("test query", results)
    assert summary["final_stance"] == "buy"
    assert summary["verdict"] == "建议买入/加仓"


def test_reasoning_field_not_empty():
    """reasoning 字段非空（兼容 _save_final）。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议持有"},
    ]
    summary = arbitrate_results("test query", results)
    assert summary["reasoning"]
    assert "【裁决】" in summary["reasoning"]


def test_confidence_field_valid():
    """confidence 字段为 high/medium/low 之一（兼容 _save_final）。"""
    results = [
        {"agent_key": "a1", "agent": "专家A", "analysis": "建议持有"},
    ]
    summary = arbitrate_results("test query", results)
    assert summary["confidence"] in ("high", "medium", "low")
