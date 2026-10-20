"""顶栏融资数据与全局搜索回归，使用临时数据库及离线行情。"""
import asyncio

import pytest


def search(q, limit=5):
    from routers.knowledge.search import global_search
    result = global_search(q=q, limit=limit)
    if hasattr(result, '__await__'):
        result = asyncio.run(result)
    return result['data']


def test_search_reads_real_holdings_and_combines_accounts():
    from db import _get_conn
    conn = _get_conn()
    conn.executemany('''INSERT INTO portfolio_holdings
        (user_id, account, fund_code, fund_name, shares, current_price, current_value)
        VALUES ('default', ?, ?, ?, ?, ?, ?)''', [
        ('甲', '000001', '测试基金', 10, 2, 20),
        ('乙', '000001', '测试基金', 20, 2, 40),
        ('甲', '000002', '测试基金已清仓', 0, 2, 0),
    ])
    conn.commit(); conn.close()
    rows = search('测试基金')['funds']
    assert len(rows) == 1
    assert rows[0]['fund_code'] == '000001'
    assert rows[0]['shares'] == 30
    assert rows[0]['current_value'] == 60
    assert rows[0]['current_nav'] == 2


@pytest.mark.parametrize('q', ['沪深300', '000300'])
def test_search_returns_latest_valuation_per_metric_and_accepts_code(q):
    from db import _get_conn
    conn = _get_conn()
    conn.executemany('''INSERT INTO index_valuations
        (index_code, index_name, snapshot_date, metric_type, current_value, percentile)
        VALUES ('000300', '沪深300', ?, ?, ?, ?)''', [
        ('2026-10-15', '市盈率', 13, 40),
        ('2026-10-16', '市盈率', 14, 50),
        ('2026-10-16', '市净率', 1.5, None),
    ])
    conn.commit(); conn.close()
    rows = search(q)['valuations']
    assert len(rows) == 2
    assert {row['metric_type'] for row in rows} == {'市盈率', '市净率'}
    assert all(row['snapshot_date'] == '2026-10-16' for row in rows)


def test_knowledge_search_uses_same_chinese_tokenization_as_index():
    from db import _get_conn
    from services.rag.rag import _tokenize
    conn = _get_conn()
    conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(content_type, title, body, reference_id UNINDEXED)')
    conn.execute('INSERT INTO knowledge_fts VALUES (?, ?, ?, ?)',
                 ('concept', _tokenize('资产配置'), _tokenize('长期投资计划'), 'test-1'))
    conn.commit(); conn.close()
    assert search('资产配置')['knowledge']


def test_search_finds_articles_not_yet_in_fts():
    from db import _get_conn
    conn = _get_conn()
    conn.execute("INSERT INTO articles (url, title) VALUES ('https://example.com/test', '资产配置入门')")
    conn.commit(); conn.close()
    rows = search('资产配置')['knowledge']
    assert any(row['content_type'] == 'article' and row['title'] == '资产配置入门' for row in rows)


@pytest.mark.parametrize('q', [' ', '  \n  '])
def test_blank_query_has_no_results(q):
    assert search(q) == {'knowledge': [], 'funds': [], 'valuations': []}


def test_financing_summary_exposes_trade_date(monkeypatch):
    from services.market import institutional_flow as flow
    monkeypatch.setattr(flow, 'get_margin_balance', lambda days: {
        'latest': {'date': '20261016'}, 'series': [{}] * 30,
        'recent_5d_change': -331.87, 'trend': 'outflow', 'strength': 'moderate',
    })
    result = flow.get_institutional_flow_summary()
    assert result['latest_date'] == '2026-10-16'
    assert result['data_status'] == 'ok'
    assert result['recent_5d_change_yi'] == -331.87


@pytest.mark.parametrize('data', [
    {'latest': None, 'series': [], 'recent_5d_change': 0},
    {'latest': {'date': '20261016'}, 'series': [{}] * 3, 'recent_5d_change': 0},
    {'latest': {'date': '20261016'}, 'series': [{}] * 30, 'recent_5d_change': float('nan')},
])
def test_financing_summary_does_not_disguise_missing_data_as_zero(monkeypatch, data):
    from services.market import institutional_flow as flow
    monkeypatch.setattr(flow, 'get_margin_balance', lambda days: data)
    result = flow.get_institutional_flow_summary()
    assert result['data_status'] == 'unavailable'
    assert result['recent_5d_change_yi'] is None


@pytest.mark.parametrize('missing_balance', ['', None, float('nan'), 0, -1])
def test_missing_upstream_balance_does_not_turn_into_a_large_outflow(monkeypatch, missing_balance):
    import pandas as pd
    from types import SimpleNamespace
    from services.market import institutional_flow as flow
    from services.market.leading_indicators import akshare_utils
    frame = pd.DataFrame({
        '信用交易日期': [f'202610{day:02d}' for day in range(11, 17)],
        '融资余额': [1e12] * 5 + [missing_balance],
    })
    monkeypatch.setattr(flow, '_get_cached', lambda key: None)
    monkeypatch.setattr(flow, '_set_cached', lambda *args: None)
    monkeypatch.setattr(flow, '_HAS_AKSHARE', True)
    monkeypatch.setattr(flow, 'ak', SimpleNamespace(stock_margin_sse=lambda: None))
    monkeypatch.setattr(flow, 'pd', pd)
    monkeypatch.setattr(akshare_utils, 'call_akshare_with_timeout', lambda *args, **kwargs: frame)
    result = flow.get_institutional_flow_summary()
    assert result['data_status'] == 'unavailable'
    assert result['recent_5d_change_yi'] is None
