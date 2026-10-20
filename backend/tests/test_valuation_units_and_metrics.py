"""图片估值的单位、逐行指标和历史口径契约。"""
import json
from datetime import date

import pytest

from db import valuations as v
from db.valuation_sources import image_valuation_rows
from services.content.image_parser import ImageParser, LiuyiImageParser


def test_numeric_image_percentile_below_one_stays_percent_points():
    parsed = ImageParser()._normalize({'指数代码': 'HSTECH', '指数名称': '恒生科技',
        '历史窗口': '10Y', '市销率TTM统计指标': {'当前值': 1.44, '分位点': 0.62}})
    v.save_valuation(parsed)
    row = v.get_latest_valuation('HSTECH', '市销率')
    assert row['percentile'] == 0.62
    assert row['percentile_unit'] == 'percent'
    assert row['history_window'] == '10Y'
    assert row['percentile_raw'] == 0.62


@pytest.mark.parametrize('raw,expected', [(0.0062, 0.62), (1, 100)])
def test_ratio_conversion_requires_explicit_unit(raw, expected):
    v.save_valuation({'index_code': 'RATIO', 'percentile': raw, 'percentile_unit': 'ratio'})
    assert v.get_latest_valuation('RATIO')['percentile'] == expected


def test_unlabelled_numeric_percentile_is_documented_percent_points():
    assert v._normalize_percentile(0.62) == 0.62
    assert v._normalize_percentile(0.0062, fractional=True) == 0.62
    assert v._normalize_percentile(1, fractional=True) == 100


def test_invalid_explicit_percentile_unit_is_rejected():
    with pytest.raises(ValueError, match='percentile_unit'):
        v.save_valuation({'index_code': 'BAD', 'percentile': 0.62, 'percentile_unit': 'unknown'})


def test_liuyi_mixed_pe_ps_rows_preserve_metric_and_window():
    parsed = LiuyiImageParser()._normalize({'更新日期': date.today().isoformat(), '数据': [
        {'指数名称': '恒生科技', '指数代码': 'HSTECH', '估值类型': 'PS', 'PS': 1.44,
         'PS百分位': 1.3, 'PB': 2.23, 'PB百分位': 10.1, '历史窗口': '成立以来'},
        {'指数名称': '恒生科技', '指数代码': 'HSTECH', '估值类型': 'PE', 'PE': 24.5,
         'PE百分位': 30, '历史窗口': '10Y'},
    ]})
    v.save_liuyi_valuation(parsed, 'mixed.png')
    rows = image_valuation_rows('六亿')
    ps = next(r for r in rows if r['metric_type'] == '市销率')
    pe = next(r for r in rows if r['metric_type'] == '市盈率')
    assert ps['current_value'] == 1.44 and ps['percentile'] == 1.3
    assert ps['history_window'] == '成立以来'
    assert ps['metric_verified'] is True
    assert pe['current_value'] == 24.5 and pe['history_window'] == '10Y'
    assert parsed['data'][0]['pe'] is None


def test_explicit_ps_row_in_generic_pe_column_does_not_become_pe():
    parsed = LiuyiImageParser()._normalize({'数据': [
        {'指数名称': '恒生科技', '估值类型': 'PS', 'PE': 1.44, 'PE百分位': 1.3}]})
    row = parsed['data'][0]
    assert row.get('ps') == 1.44 and row.get('ps_percentile') == 1.3
    assert row.get('pe') is None


def test_explicitly_verified_low_pe_is_not_retyped():
    parsed = LiuyiImageParser()._normalize({'更新日期': date.today().isoformat(), '数据': [
        {'指数名称': '恒生科技', '指数代码': 'HSTECH', '估值类型': 'PE', 'PE': 1.44}]})
    v.save_liuyi_valuation(parsed, 'actual-pe.png')
    row = v.get_best_valuation('HSTECH', '市盈率', enable_online=False)
    assert row['current_value'] == 1.44
    assert row['metric_verified'] is True
    assert v.get_best_valuation('HSTECH', '市销率', enable_online=False) is None


def test_untyped_suspicious_pe_is_quarantined_without_guessing_ps():
    v.save_liuyi_valuation({'ok': True, 'update_date': date.today().isoformat(), 'data': [
        {'index_code': 'HSTECH', 'index_name': '恒生科技', 'pe': 1.44, 'pe_percentile': 1.3,
         'pb': 2.23, 'pb_percentile': 10.1}]}, 'legacy.png')
    assert v.get_best_valuation('HSTECH', '市盈率', enable_online=False) is None
    assert v.get_best_valuation('HSTECH', '市销率', enable_online=False) is None
    assert v.get_best_valuation('HSTECH', '市净率', enable_online=False)['current_value'] == 2.23


def test_image_source_respects_explicit_ratio_unit():
    v.save_liuyi_valuation({'ok': True, 'update_date': date.today().isoformat(), 'data': [
        {'index_code': 'HSTECH', 'ps': 1.44, 'ps_percentile': 0.013, 'percentile_unit': 'ratio'}]}, 'ratio.png')
    assert image_valuation_rows('六亿')[0]['percentile'] == 1.3


def test_online_provider_contract_preserves_tiny_percentages(monkeypatch):
    from types import SimpleNamespace
    import mcp.ttfund_client as tc
    monkeypatch.setattr(tc, 'get_ttfund_client', lambda: SimpleNamespace(_invoke=lambda *a: {
        'success': True, 'data': {'valuation': {'pe_ttm': 12, 'pe_percentile_10y': 0.62}}}))
    row = v._query_ttfund_valuation('000300', '市盈率', 100)
    assert row['percentile'] == 0.62
    assert row['percentile_unit'] == 'percent'


def test_akshare_ratio_is_converted_once_across_fetch_and_both_caches(monkeypatch):
    import services.market_data as market
    monkeypatch.setattr(market, 'get_index_valuation', lambda code: {
        'pe': 12, 'pe_percentile': 0.0062, 'date': date.today().isoformat()})
    monkeypatch.setattr(v, '_online_cache', {})
    monkeypatch.setattr(v, '_run_with_safe_timeout', lambda fn, *args, **kw: fn(*args))
    row = v._fetch_online_sources('000300', '市盈率', 100)
    assert row['percentile'] == 0.62
    assert row['percentile_unit'] == 'percent' and row['percentile_scale'] == 100
    memory = v.fetch_online_valuation('000300', '市盈率')
    assert memory['percentile'] == 0.62
    v.save_online_valuation('000300', '市盈率', row, 'akshare')
    cached = v.get_online_valuation_cached('000300', '市盈率')
    assert cached['percentile'] == 0.62
    assert cached['percentile_unit'] == 'percent'


def test_explicit_ratio_is_normalized_at_persistent_cache_write():
    v.save_online_valuation('000300', '市盈率', {
        'current_value': 12, 'percentile': 0.0062, 'percentile_unit': 'ratio',
        'snapshot_date': date.today().isoformat()}, 'akshare')
    assert v.get_online_valuation_cached('000300', '市盈率')['percentile'] == 0.62
