"""治理配置、Agent 发布与成本统计的隔离回归。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def client():
    from routers.admin.agents import router as agents
    from routers.admin.config import router as config
    from routers.admin.token_usage import router as tokens
    app = FastAPI()
    for router in (agents, config, tokens):
        app.include_router(router)
    return TestClient(app)


def test_config_update_preserves_metadata_and_repairs_legacy_loss():
    from db.config import update_config, list_configs, init_default_configs
    from db import _get_conn
    update_config('llm.temperature_default', '0.6')
    row = next(c for c in list_configs() if c['key'] == 'llm.temperature_default')
    assert (row['description'], row['category']) == ('默认温度', 'llm')
    conn = _get_conn()
    conn.execute("UPDATE system_config SET description='', category='general' WHERE key='llm.temperature_default'")
    conn.commit(); conn.close()
    init_default_configs()
    row = next(c for c in list_configs() if c['key'] == 'llm.temperature_default')
    assert (row['value'], row['description'], row['category']) == ('0.6', '默认温度', 'llm')


def test_reset_restores_default_metadata_without_removing_extension_keys():
    from db.config import update_config, reset_configs, list_configs
    update_config('llm.temperature_default', '0.9')
    update_config('extension.key', 'custom')
    reset_configs()
    rows = {c['key']: c for c in list_configs()}
    assert rows['llm.temperature_default']['category'] == 'llm'
    assert rows['llm.temperature_default']['value'] == '0.3'
    assert rows['extension.key']['value'] == 'custom'


@pytest.mark.parametrize(('key', 'value'), [
    ('llm.temperature_default', '3'), ('llm.timeout_default', '-1'),
    ('llm.max_tokens_chat', 'oops'), ('llm.max_tokens_chat', '800.0'),
    ('llm_cost.auto_daily_report', 'maybe'),
    ('opportunity.regime.theme_category', '{broken'),
    ('alerts.event_radar_scan_time', '25:99'),
    ('valuation.undervalued_percentile', '95'),
])
def test_invalid_config_is_rejected_without_changing_value(key, value):
    from db.config import get_config
    old = get_config(key)
    response = client().put('/api/system-config/' + key, json={'value': value})
    assert response.status_code == 422
    assert get_config(key) == old


def test_agent_customizations_survive_initialization():
    from db import init_db, list_agents
    from db.agents import update_agent, get_agent
    agents = list_agents()
    targets = [next(a for a in agents if a.get('agent_key') == 'fund_analyst'),
               next(a for a in agents if a['name'] == '需求澄清')]
    for agent in targets:
        update_agent(agent['id'], system_prompt='用户提示词', description='用户描述', knowledge_scope='{}')
    init_db()
    for agent in targets:
        actual = get_agent(agent['id'])
        assert actual['system_prompt'] == '用户提示词'
        assert actual['description'] == '用户描述'


@pytest.mark.parametrize('version_type', ['conversation', 'analysis'])
def test_rollback_requires_version_agent_and_type_ownership(version_type):
    from db.agents import create_agent, save_prompt_version, list_prompt_versions, get_agent
    a = create_agent('A', 'promptA'); b = create_agent('B', 'promptB')
    save_prompt_version(a, version_type, 'oldA')
    version = list_prompt_versions(a, version_type)[0]
    assert client().post(f'/api/agents/{b}/rollback/{version["id"]}').status_code == 404
    if version_type == 'analysis':
        assert client().post(f'/api/agents/{a}/rollback/{version["id"]}').status_code == 404
    assert get_agent(b)['system_prompt'] == 'promptB'


def test_specialist_rollback_invalidates_runtime_cache(monkeypatch):
    from db import list_agents
    from db.agents import load_specialist_agents, save_prompt_version, list_prompt_versions
    import agent.regression as regression
    async def no_llm(*args):
        pass
    monkeypatch.setattr(regression, 'run_regression_tests', no_llm)
    agent = next(a for a in list_agents() if a.get('agent_key') == 'fund_analyst')
    load_specialist_agents()
    save_prompt_version(agent['id'], 'conversation', '历史提示词')
    version = list_prompt_versions(agent['id'])[0]
    assert client().post(f'/api/agents/{agent["id"]}/rollback/{version["id"]}').status_code == 200
    assert load_specialist_agents()['fund_analyst']['system_prompt'] == '历史提示词'


def add_token(caller='chat', prompt=100, completion=50, created_at=None, trace='trace-test'):
    from db import _get_conn
    conn = _get_conn()
    conn.execute("INSERT INTO token_usage(model,caller,prompt_tokens,completion_tokens,total_tokens,trace_id,created_at) VALUES ('mimo-v2.6-pro',?,?,?,?,?,COALESCE(?,datetime('now','localtime')))",
                 (caller, prompt, completion, prompt+completion, trace, created_at))
    conn.commit(); conn.close()


def test_trace_works_without_optional_tables_and_returns_top_level_contract():
    add_token()
    response = client().get('/api/token-usage/trace/trace-test')
    assert response.status_code == 200
    data = response.json()
    assert data['trace_id'] == 'trace-test'
    assert len(data['token_usage']) == 1
    assert data['rag_logs'] == []


def test_hourly_query_rejects_injection_and_only_counts_llm():
    add_token(); add_token('embedding_index', 1000, 0)
    c = client()
    assert c.get('/api/token-usage/hourly', params={'date': "1900-01-01' OR 1=1 --"}).status_code == 422
    assert sum(row['tokens'] for row in c.get('/api/token-usage/hourly').json()['items']) == 150


def test_token_views_share_local_calendar_window_and_exclude_embeddings():
    from db import _get_conn
    from db.token_usage import get_token_usage_summary, get_token_usage_by_caller, list_token_usage, get_token_usage_daily
    conn = _get_conn()
    yesterday = conn.execute("SELECT datetime('now','localtime','start of day','-1 second')").fetchone()[0]
    conn.close()
    add_token(); add_token('embedding_index', 1000, 0); add_token(created_at=yesterday)
    assert get_token_usage_summary(1)['total_tokens'] == 150
    assert sum(row['total_tokens'] for row in get_token_usage_by_caller(1)) == 150
    assert len(list_token_usage(1)) == 1
    assert get_token_usage_daily(1)[0]['prompt_tokens'] == 100
    assert get_token_usage_daily(1)[0]['completion_tokens'] == 50


def test_cost_windows_are_independent_of_selected_window_and_retain_small_costs():
    from db.token_usage import get_cost_estimate
    from db import _get_conn
    conn = _get_conn()
    old = conn.execute("SELECT datetime('now','localtime','start of day','-10 days')").fetchone()[0]
    conn.close()
    add_token(prompt=1000, completion=0)
    add_token(prompt=1000000, completion=0, created_at=old)
    data = get_cost_estimate(30)
    assert data['total_cost'] == pytest.approx(.3003)
    assert data['today_cost'] == pytest.approx(.0003)
    assert data['week_cost'] == pytest.approx(.0003)
    assert data['days'] == 30
    assert data['month_cost'] >= data['today_cost']


def test_performance_counts_success_aliases_and_excludes_running_from_success_rate():
    from db import _get_conn
    from db.token_usage import get_performance_stats, get_performance_by_agent
    conn = _get_conn()
    for status in ('success', 'completed', 'done', 'failed', 'running', 'pending'):
        conn.execute("INSERT INTO agent_runs(agent_key,agent_name,status,duration_ms) VALUES ('audit','审查',?,1000)", (status,))
    conn.commit(); conn.close()
    for data in (get_performance_stats(7), get_performance_by_agent(7)[0]):
        assert data['success_count'] == 3
        assert data['error_count'] == 1
        assert data['active_count'] == 2
        assert data['success_rate'] == 75


def test_configured_llm_timeout_reaches_client_without_network(monkeypatch):
    from db.config import update_config
    from services.llm import llm_service as service
    seen = []
    monkeypatch.setattr(service, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(service.client.chat.completions, 'create', lambda **kw: seen.append(kw) or SimpleNamespace(usage=None, model=service.MODEL))
    update_config('llm.timeout_default', '9')
    service._call_llm(model=service.MODEL, messages=[])
    assert 0 < seen[0]['timeout'] <= 9


def test_disabled_analysis_agent_can_be_rolled_back_without_changing_active_state():
    from db import list_analysis_agents, update_analysis_agent, save_prompt_version, list_prompt_versions
    from db.analysis import get_analysis_agent
    agent = list_analysis_agents()[0]
    update_analysis_agent(agent['id'], is_active=0)
    save_prompt_version(agent['id'], 'analysis', '旧分析提示词')
    version = list_prompt_versions(agent['id'], 'analysis')[0]
    response = client().post(f'/api/analysis-agents/{agent["id"]}/rollback/{version["id"]}')
    assert response.status_code == 200
    actual = get_analysis_agent(agent['id'], include_inactive=True)
    assert actual['system_prompt'] == '旧分析提示词'
    assert actual['is_active'] == 0


def test_hourly_data_function_rejects_malformed_date_even_without_http():
    from db.token_usage import get_token_usage_hourly
    with pytest.raises(ValueError):
        get_token_usage_hourly("2020-01-01' OR 1=1 --")


def test_chat_shadow_receives_exact_messages_base_prompt_and_real_result(monkeypatch):
    from services.llm import llm_service as service
    import infra.shadow_mode as shadow
    seen = []
    monkeypatch.setattr(service, '_call_llm', lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='真实对话结果'))]))
    monkeypatch.setattr(shadow, 'schedule_shadow', lambda *args: seen.append(args))
    result = service.chat_with_agent('基础提示词', [{'role': 'user', 'content': '问题'}], 'RAG证据')
    assert result == '真实对话结果'
    agent_type, payload, production_output = seen[0]
    assert agent_type == 'chat'
    assert payload['base_prompt'] == '基础提示词'
    assert payload['messages'][0]['content'].startswith('基础提示词')
    assert 'RAG证据' in payload['messages'][0]['content']
    assert payload['messages'][-1] == {'role': 'user', 'content': '问题'}
    assert production_output == result
