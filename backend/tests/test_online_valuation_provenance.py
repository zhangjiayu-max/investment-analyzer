"""在线来源日期与缓存来源不得由抓取时刻或调用路径伪造。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('source', ['akshare', 'ttfund'])
def test_missing_provider_date_stays_missing(monkeypatch, source):
    from db import valuations as v
    if source == 'akshare':
        import services.market_data as market
        monkeypatch.setattr(market, 'get_index_valuation', lambda code: {'pe': 12, 'pe_percentile': .2})
        result = v._query_akshare_valuation('000300', '市盈率', 100)
    else:
        import mcp.ttfund_client as ttfund
        monkeypatch.setattr(ttfund, 'get_ttfund_client', lambda: SimpleNamespace(_invoke=lambda *a: {
            'success': True, 'data': {'valuation': {'pe_ttm': 12, 'pe_percentile_10y': 20}, 'quote': {}}}))
        result = v._query_ttfund_valuation('000300', '市盈率', 100)
    assert result['snapshot_date'] is None


def test_active_query_preserves_cached_provider_and_date(monkeypatch):
    from db import valuations as v
    from db.config import update_config
    update_config('tool.online_valuation_query_enabled', 'true')
    monkeypatch.setattr(v, '_online_cache', {'000300:市盈率': {
        'index_code': '000300', 'metric_type': '市盈率', 'source': 'ttfund_online',
        'snapshot_date': None, 'current_value': 12, '_cached_at': datetime.now(),
    }})
    # 日期未知的缓存现在必须先刷新；模拟来源失败，验证降级仍保留真实元数据。
    monkeypatch.setattr(v, '_run_with_safe_timeout', lambda *a, **kw: None)
    result = v.fetch_online_valuation('000300', '市盈率')
    assert result['source'] == 'ttfund_online'
    assert result['cached'] is True
    assert result['snapshot_date'] is None


def test_source_age_is_not_reset_when_fetched_again(monkeypatch):
    from db import valuations as v
    from db.config import update_config
    update_config('tool.online_valuation_query_enabled', 'true')
    monkeypatch.setattr(v, '_online_cache', {})
    monkeypatch.setattr(v, '_run_with_safe_timeout', lambda *a, **kw: {
        'metric_type': '市盈率', 'current_value': 12, 'percentile': .2,
        'snapshot_date': (datetime.now()-timedelta(days=12)).date().isoformat(),
    })
    result = v.fetch_online_valuation('000300', '市盈率')
    assert result['days_old'] == 12
    assert result['is_expired'] is True


def test_undated_source_age_remains_unknown(monkeypatch):
    from db import valuations as v
    from db.config import update_config
    update_config('tool.online_valuation_query_enabled', 'true')
    monkeypatch.setattr(v, '_online_cache', {})
    monkeypatch.setattr(v, '_run_with_safe_timeout', lambda *a, **kw: {
        'metric_type': '市盈率', 'current_value': 12, 'percentile': .2, 'snapshot_date': None,
    })
    result = v.fetch_online_valuation('000300', '市盈率')
    assert result['days_old'] is None
    assert result['date_verified'] is False


def test_market_adapter_keeps_actual_date_instead_of_fetch_date(monkeypatch):
    import pandas as pd
    import services.market_data as market
    monkeypatch.setattr(market.ak, 'stock_zh_index_value_csindex', lambda **kw: pd.DataFrame([
        {'日期': '2026-10-08', '市盈率1': 12, '股息率1': 3, '指数中文简称': '沪深300'},
    ]))
    monkeypatch.setattr(market.ak, 'stock_zh_index_hist_csindex', lambda **kw: pd.DataFrame())
    assert market.get_index_valuation('000300')['date'] == '2026-10-08'
