# backend/tests/test_dca_rules.py
"""测试 4% 定投法规则注入。"""
import pytest
from agent.orchestrator import _build_dca_rules


def test_dca_rules_contains_key_rules():
    """定投规则应包含基础金额、跌幅档位、最大档数。"""
    rules = _build_dca_rules()
    assert isinstance(rules, str)
    assert "500" in rules  # 基础定投金额
    assert "4%" in rules  # 跌幅档位
    assert "3" in rules  # 最大档数


def test_dca_rules_contains_reduce_limits():
    """规则应包含减仓上限约束。"""
    rules = _build_dca_rules()
    assert "20%" in rules  # 单基金减仓上限
    assert "10%" in rules  # 总减仓上限
    assert "50,000" in rules or "50000" in rules  # 单次减仓金额上限
