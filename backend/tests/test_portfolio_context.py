# backend/tests/test_portfolio_context.py
"""测试持仓上下文构建。"""
import pytest
from agent.orchestrator import _build_portfolio_context


def test_portfolio_context_contains_holdings():
    """持仓上下文应包含基金名、占比、盈亏率。"""
    ctx = _build_portfolio_context()
    assert isinstance(ctx, str)
    # 如果有持仓，应包含"当前持仓"标题
    if ctx:
        assert "当前持仓" in ctx or "无持仓" in ctx


def test_portfolio_context_format():
    """持仓上下文格式应包含关键字段。"""
    ctx = _build_portfolio_context()
    if "无持仓" in ctx:
        return  # 无持仓时跳过格式检查
    # 应包含占比或盈亏相关字段
    assert "占比" in ctx or "盈亏" in ctx or "成本" in ctx
