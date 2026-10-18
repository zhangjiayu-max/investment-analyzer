"""治理数据与工具调试契约回归，全部使用临时数据库。"""
import pytest
from fastapi import HTTPException
from db._conn import _get_conn


def test_feature_events_have_distinct_metrics():
    from db.feature_usage import batch_track_feature_usage, get_feature_usage_stats
    batch_track_feature_usage([dict(session_id='s', page_key='p', feature_key='f', action_type=a, duration_ms=1200 if a == 'page_leave' else None) for a in ['page_enter','page_leave','feature_click','feature_complete']])
    stats = get_feature_usage_stats()
    assert stats['page_ranking'][0]['visits'] == 1
    assert stats['feature_ranking'][0]['clicks'] == 1
    assert stats['feature_ranking'][0]['completions'] == 1


def test_data_health_partial_holdings_not_masked_by_latest():
    from routers.admin.data_health import get_data_health
    conn = _get_conn()
    for i, stamp in enumerate([__import__('datetime').date.today().isoformat(), None, '2020-01-01']):
        conn.execute('INSERT INTO portfolio_holdings (fund_code, fund_name, shares, cost_price, price_updated_at) VALUES (?, ?, 1, 1, ?)', (str(i),str(i),stamp))
    conn.commit(); conn.close()
    item = get_data_health()['health']['holdings']
    assert item['status'] != 'fresh'
    assert item['coverage']['expected'] == 3
    assert item['coverage']['missing'] == 1


def test_reference_content_does_not_expire_and_empty_is_explicit():
    from routers.admin.data_health import get_data_health, _days_since
    health = get_data_health()['health']
    assert health['knowledge']['status'] == 'empty'
    assert _days_since('not-a-date') is None


def test_capabilities_deduplicate_exposed_mcp():
    from routers.admin.capabilities import api_capabilities_tools, api_capabilities_unexposed
    tools = api_capabilities_tools()['tools']
    exposed = {t['name'] for t in tools if t['exposed']}
    assert not any(f"{t['source']}_{t['name']}" in exposed for t in tools if not t['exposed'])
    assert not any(i['name'] == 'fund_manager' for i in api_capabilities_unexposed()['ttfund'])


def test_debug_blocks_disabled_and_requires_write_confirmation(monkeypatch):
    import tools
    from routers.admin.capabilities import api_capabilities_debug
    calls=[]
    monkeypatch.setattr(tools, 'execute_tool', lambda *a, **k: calls.append(a) or '{}')
    with pytest.raises(HTTPException) as exc:
        api_capabilities_debug({'tool_name':'generate_portfolio_alert','arguments':{}})
    assert exc.value.status_code == 409
    assert calls == []
    conn = _get_conn()
    conn.execute("INSERT INTO tool_registry (name, enabled) VALUES ('query_valuation', 0) ON CONFLICT(name) DO UPDATE SET enabled=0")
    conn.commit(); conn.close()
    with pytest.raises(HTTPException) as exc:
        api_capabilities_debug({'tool_name':'query_valuation','arguments':{}})
    assert exc.value.status_code == 403
    assert calls == []


def test_beacon_session_body_is_persisted_without_header(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.admin.feature_usage import router
    app = FastAPI(); app.include_router(router)
    monkeypatch.setattr('routers.admin.feature_usage._is_enabled', lambda: True)
    response = TestClient(app).post('/api/feature-usage/track-batch', json={'session_id':'tab-session','events':[{'page_key':'quality','action_type':'page_enter'}]})
    assert response.status_code == 200
    assert response.json()['ok'] is True
    conn = _get_conn()
    try: assert conn.execute('SELECT session_id FROM feature_usage').fetchone()[0] == 'tab-session'
    finally: conn.close()


@pytest.mark.parametrize('arguments', [[], None, {'index_name':{'bad':'object'}}])
def test_debug_rejects_invalid_parameter_types(arguments,monkeypatch):
    import tools
    from routers.admin.capabilities import api_capabilities_debug
    monkeypatch.setattr(tools, 'execute_tool', lambda *a, **k: pytest.fail('invalid args must not execute'))
    with pytest.raises(HTTPException) as exc:
        api_capabilities_debug({'tool_name':'query_valuation','arguments':arguments})
    assert exc.value.status_code == 422


def test_reference_content_remains_available_after_seven_days():
    from routers.admin.data_health import get_data_health
    conn = _get_conn()
    conn.execute("INSERT INTO conversations (title,created_at) VALUES ('old','2020-01-01')")
    conn.commit(); conn.close()
    assert get_data_health()['health']['conversations']['status'] == 'available'
