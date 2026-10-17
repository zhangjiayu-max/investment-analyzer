"""分析配置、任务归属和过期状态的集成回归。"""
import concurrent.futures
import json

import pytest


def test_default_analysis_prompt_survives_initialization(tmp_db):
    from db import init_db, get_analysis_agent_by_name, update_analysis_agent
    agent = get_analysis_agent_by_name('指数深度分析师')
    update_analysis_agent(agent['id'], system_prompt='用户自定义分析框架')
    init_db()
    assert get_analysis_agent_by_name(agent['name'])['system_prompt'] == '用户自定义分析框架'


def test_specialist_loader_keeps_scope_identity_and_database_isolation(tmp_db, monkeypatch, tmp_path):
    import db._conn as connection
    from db import init_db
    from db.agents import load_specialist_agents, clear_specialist_cache
    clear_specialist_cache()
    agents = load_specialist_agents()
    agent = agents['fund_analyst']
    assert agent['agent_key'] == 'fund_analyst'
    assert 'knowledge_scope' in agent
    monkeypatch.setattr(connection, 'DB_PATH', tmp_path / 'other.db')
    init_db()
    with connection._get_conn() as conn:
        conn.execute("UPDATE agents SET system_prompt='另一个数据库' WHERE agent_key='fund_analyst'")
    assert load_specialist_agents()['fund_analyst']['system_prompt'] == '另一个数据库'


def test_fund_memory_cache_isolated_between_databases(tmp_db, monkeypatch, tmp_path):
    import db._conn as connection
    from services.fund import fund_data_service as service
    service._mc_set('metadata:health', {'fund_name': '库A'})
    monkeypatch.setattr(connection, 'DB_PATH', tmp_path / 'other.db')
    assert service._mc_get('metadata:health') is None


def _start(code='000300'):
    from db.async_tasks import start_analysis_task
    return start_analysis_task(
        'index_analysis', 'health_test', {'index_code': code, 'agent_id': 10},
        'analysis_history', {'index_code': code, 'index_name': code, 'agent_id': 10,
                             'agent_name': '指数深度分析师', 'prompt_used': '分析',
                             'news_context': '', 'valuation_context': ''})


def test_task_dedup_matches_target_and_returns_same_record(tmp_db):
    first = _start('000300')
    same = _start('000300')
    different = _start('000905')
    assert same['reused'] and same['id'] == first['id']
    assert same['task_id'] == first['task_id']
    assert different['task_id'] != first['task_id']
    assert different['id'] != first['id']


def test_same_target_concurrent_submissions_create_one_task_and_record(tmp_db):
    from db._conn import _get_conn
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: _start(), range(4)))
    assert len({r['task_id'] for r in results}) == 1
    assert len({r['id'] for r in results}) == 1
    with _get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM analysis_history').fetchone()[0] == 1


def test_expired_analysis_task_record_and_log_reach_error_together(tmp_db):
    from db.async_tasks import start_analysis_task, get_async_task
    from db.portfolio import get_analysis_record_status
    from db.agent_analysis_log import create_analysis_log, get_analysis_log_by_trace
    from db._conn import _get_conn
    task = start_analysis_task('panorama_analysis', 'test', {'portfolio': 'default'},
                              'portfolio_analysis_records', {'analysis_type': 'panorama',
                              'summary': '检查', 'input_data': '{}'})
    create_analysis_log('health_trace', 'panorama', 'portfolio_analysis_records', source_id=task['id'])
    with _get_conn() as conn:
        conn.execute("UPDATE async_tasks SET updated_at=datetime('now','localtime','-45 minutes')")
    assert get_async_task(task['task_id'])['status'] == 'error'
    assert get_analysis_record_status(task['id'])['status'] == 'error'
    assert get_analysis_log_by_trace('health_trace')['status'] == 'error'


def test_orphaned_legacy_records_expire_but_completed_records_remain(tmp_db):
    from db.portfolio import create_portfolio_analysis_record, get_analysis_record_status
    from db._conn import _get_conn
    orphan = create_portfolio_analysis_record('deep_dive', '检查', '{}', status='running')
    done = create_portfolio_analysis_record('deep_dive', '已完成', '{}', status='done')
    with _get_conn() as conn:
        conn.execute("UPDATE portfolio_analysis_records SET created_at='2026-01-01 00:00:00'")
    assert get_analysis_record_status(orphan)['status'] == 'error'
    assert get_analysis_record_status(done)['status'] == 'done'


def test_whatif_rejects_unknown_scenario_and_requires_custom_text():
    from models.portfolio import WhatIfRequest
    with pytest.raises(ValueError):
        WhatIfRequest(scenario='利率上升2%')
    with pytest.raises(ValueError):
        WhatIfRequest(scenario='custom')
    assert WhatIfRequest(scenario='rate_hike', parameter=200).parameter == 200


def test_index_request_has_no_database_specific_default_id():
    from models.analysis import AnalysisRunRequest
    assert AnalysisRunRequest(index_code='000300').agent_id is None


def test_index_route_resolves_default_by_name_and_dedups_only_same_index(tmp_db, monkeypatch):
    import asyncio
    from routers.analysis import index_analysis as route
    from models.analysis import AnalysisRunRequest
    from db import get_analysis_agent_by_name
    async def waiting(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(route, '_run_index_analysis_async', waiting)
    async def run():
        a = await route.run_analysis(AnalysisRunRequest(index_code='000300'))
        same = await route.run_analysis(AnalysisRunRequest(index_code='000300'))
        b = await route.run_analysis(AnalysisRunRequest(index_code='000905'))
        assert same['reused'] and same['id'] == a['id']
        assert b['task_id'] != a['task_id']
        from db import get_analysis_history_item
        assert get_analysis_history_item(a['id'])['agent_id'] == get_analysis_agent_by_name('指数深度分析师')['id']
    asyncio.run(run())


def test_failed_task_immediately_closes_linked_record_and_log(tmp_db):
    from db.async_tasks import start_analysis_task, update_async_task
    from db.portfolio import get_portfolio_analysis_record
    from db.agent_analysis_log import create_analysis_log, get_analysis_log_by_trace
    task = start_analysis_task('panorama', 'test', {}, 'portfolio_analysis_records', {'analysis_type': 'panorama'})
    create_analysis_log('failed_task', 'panorama', 'portfolio_analysis_records', source_id=task['id'])
    update_async_task(task['task_id'], status='error', error_msg='前置数据读取失败')
    assert get_portfolio_analysis_record(task['id'])['status'] == 'error'
    assert get_analysis_log_by_trace('failed_task')['status'] == 'error'


def test_expired_task_cannot_be_resurrected_by_late_worker(tmp_db):
    from db.async_tasks import get_async_task, update_async_task
    from db.analysis import update_analysis_history, get_analysis_history_item
    from db.agent_analysis_log import create_analysis_log, complete_analysis_log, get_analysis_log_by_trace
    from db._conn import _get_conn
    task = _start()
    create_analysis_log('late_worker', 'index', 'analysis_history', source_id=task['id'])
    with _get_conn() as conn:
        conn.execute("UPDATE async_tasks SET updated_at=datetime('now','localtime','-45 minutes')")
    assert get_async_task(task['task_id'])['status'] == 'error'
    assert not update_analysis_history(task['id'], status='done', result='迟到结果')
    assert not update_async_task(task['task_id'], status='done')
    assert not complete_analysis_log('late_worker', status='done')
    assert get_analysis_history_item(task['id'])['status'] == 'error'
    assert get_analysis_log_by_trace('late_worker')['status'] == 'error'


def test_completed_analysis_cannot_be_changed_to_error_by_late_worker(tmp_db):
    from db.async_tasks import update_async_task, get_async_task
    from db.analysis import update_analysis_history, get_analysis_history_item
    from db.portfolio import create_portfolio_analysis_record, update_analysis_record, get_portfolio_analysis_record
    from db.agent_analysis_log import create_analysis_log, complete_analysis_log, get_analysis_log_by_trace
    task = _start()
    create_analysis_log('completed', 'index', 'analysis_history', source_id=task['id'])
    update_analysis_history(task['id'], status='done', result='有效分析')
    update_async_task(task['task_id'], status='done')
    complete_analysis_log('completed', status='done')
    assert not update_analysis_history(task['id'], status='error', error_msg='迟到异常')
    assert not update_async_task(task['task_id'], status='error', error_msg='迟到异常')
    assert not complete_analysis_log('completed', status='error')
    assert get_async_task(task['task_id'])['status'] == 'done'
    assert get_analysis_history_item(task['id'])['result'] == '有效分析'
    assert get_analysis_log_by_trace('completed')['status'] == 'done'
    record = create_portfolio_analysis_record('panorama', '完成', '{}', status='done')
    assert not update_analysis_record(record, status='error')
    assert get_portfolio_analysis_record(record)['status'] == 'done'


def test_source_creation_rolls_back_when_task_insert_fails(tmp_db):
    from db._conn import _get_conn
    with _get_conn() as conn:
        conn.execute("CREATE TRIGGER reject_task BEFORE INSERT ON async_tasks BEGIN SELECT RAISE(ABORT,'test reject'); END")
    with pytest.raises(Exception, match='test reject'):
        _start()
    with _get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM analysis_history').fetchone()[0] == 0


def test_old_record_with_fresh_linked_task_keeps_running(tmp_db):
    from db._conn import _get_conn
    from db.async_tasks import get_async_task
    from db.analysis import get_analysis_history_status
    task = _start()
    with _get_conn() as conn:
        conn.execute("UPDATE analysis_history SET created_at='2026-01-01 00:00:00'")
    assert get_async_task(task['task_id'])['status'] == 'running'
    assert get_analysis_history_status(task['id'])['status'] == 'running'
