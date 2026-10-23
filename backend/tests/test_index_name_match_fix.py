"""索引名匹配修复回归测试（conv 210 上证指数错配恒生指数）。

背景：query_valuation("上证指数") 曾误匹配到恒生指数（HSI），
根因是 _is_index_name_match 剥离市场前缀后，"上证指数" 与 "恒生指数"
都剩 "指数"，被误判相等。本测试锁定修复后的正确行为。
"""
import pytest

from tools import _is_index_name_match, _is_shanghai_composite, _extract_market_prefix


# ── 市场前缀提取 ──

@pytest.mark.parametrize('name,expected', [
    ("上证指数", "上证"),
    ("上证综指", "上证"),
    ("恒生指数", "恒生"),
    ("恒生科技", "恒生"),
    ("中证白酒", "中证"),
    ("沪深300", ""),  # 无市场前缀
    ("", ""),
    (None, ""),
])
def test_extract_market_prefix(name, expected):
    assert _extract_market_prefix(name) == expected


# ── 跨市场错配必须被拒绝 ──

@pytest.mark.parametrize('query,name', [
    ("上证指数", "恒生指数"),
    ("上证指数", "恒生科技"),
    ("上证综指", "恒生指数"),
    ("上证指数", "深证成指"),
    ("恒生科技", "中证科技"),
])
def test_cross_market_mismatch_rejected(query, name):
    assert _is_index_name_match(query, name) is False


# ── 同市场合理匹配必须保留 ──

@pytest.mark.parametrize('query,name', [
    ("白酒", "中证白酒"),
    ("上证指数", "上证指数"),
    ("恒生科技", "恒生科技"),
    ("中证白酒", "中证白酒指数"),
])
def test_valid_match_preserved(query, name):
    assert _is_index_name_match(query, name) is True


# ── 原有 P0 修复不回归（恒生科技≠恒生生物科技、消费红利≠中证红利） ──

@pytest.mark.parametrize('query,name', [
    ("恒生科技", "恒生生物科技"),
    ("消费红利", "中证红利"),
    ("上证指数", "上证50"),
    ("上证指数", "上证红利"),
    ("上证指数", "上证180"),
    ("上证指数", "沪深300"),
])
def test_legacy_p0_not_regressed(query, name):
    assert _is_index_name_match(query, name) is False


# ── 上证综指识别 ──

@pytest.mark.parametrize('name', [
    "上证指数", "上证综指", "上证综合指数", "大盘", "沪指",
    "000001", "000001.SH", "sh000001", "SH000001",
])
def test_is_shanghai_composite_true(name):
    assert _is_shanghai_composite(name) is True


@pytest.mark.parametrize('name', [
    "上证50", "沪深300", "恒生指数", "深证成指", "中证白酒", "", None,
])
def test_is_shanghai_composite_false(name):
    assert _is_shanghai_composite(name) is False
