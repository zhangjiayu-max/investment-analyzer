"""Shadow 执行、评分和抽样回归（无真实模型调用）。"""
import asyncio
import sys
from types import SimpleNamespace
import pytest
from db._conn import _get_conn
from infra import shadow_mode as shadow


def setup_config(monkeypatch):
    shadow.init_shadow_db()
    monkeypatch.setattr('db.config.get_config', lambda key, default='': 'true' if key == 'llm_cost.auto_shadow_mode' else default)
    return shadow.create_shadow_config('test', 'chat', 'old', 'candidate', 1)


def completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=SimpleNamespace(total_tokens=17))


def test_shadow_replays_messages_and_extracts_completion(monkeypatch):
    config_id = setup_config(monkeypatch)
    messages = [{'role':'system','content':'old'}, {'role':'user','content':'actual context'}]
    calls=[]
    def llm(**kwargs):
        calls.append(kwargs)
        return completion('candidate output' if len(calls) == 1 else '{"production_score":7,"shadow_score":8,"reason":"better"}')
    monkeypatch.setitem(sys.modules, 'services.llm_service', SimpleNamespace(_call_llm=llm))
    result = asyncio.run(shadow.run_shadow(config_id, {'messages': messages}, 'production', 'chat'))
    assert result['status'] == 'scored'
    assert calls[0]['messages'] == [{'role':'system','content':'candidate'}, messages[1]]
    assert messages[0]['content'] == 'old'
    assert shadow.list_shadow_runs()[0]['shadow_output'] == 'candidate output'
    assert shadow.get_shadow_stats()['valid_runs'] == 1


def test_scoring_failure_is_not_neutral_valid_sample(monkeypatch):
    config_id = setup_config(monkeypatch)
    monkeypatch.setitem(sys.modules, 'services.llm_service', SimpleNamespace(_call_llm=lambda **kw: completion('output' if kw['caller'] != 'shadow_judge' else 'invalid json')))
    result = asyncio.run(shadow.run_shadow(config_id, {'messages':[{'role':'user','content':'x'}]}, 'production', 'chat'))
    assert result['status'] == 'scoring_failed'
    stats = shadow.get_shadow_stats()
    assert stats['total_runs'] == 1
    assert stats['valid_runs'] == 0
    assert stats['avg_shadow_score'] is None
    assert stats['shadow_win_rate'] is None


def test_execution_failure_is_recorded(monkeypatch):
    config_id = setup_config(monkeypatch)
    def fail(**kw): raise RuntimeError('unavailable')
    monkeypatch.setitem(sys.modules, 'services.llm_service', SimpleNamespace(_call_llm=fail))
    result = asyncio.run(shadow.run_shadow(config_id, {}, 'production', 'chat'))
    assert result['status'] == 'execution_failed'
    assert shadow.list_shadow_runs()[0]['status'] == 'execution_failed'


def test_sampling_honors_zero_traffic_and_disabled(monkeypatch):
    config_id = setup_config(monkeypatch)
    conn = _get_conn(); conn.execute('UPDATE shadow_configs SET traffic_pct=0 WHERE id=?',(config_id,)); conn.commit(); conn.close()
    assert shadow.schedule_shadow('chat', {'messages':[]}, 'production') == 0
    assert shadow.schedule_shadow('panorama', {}, 'production') == 0


@pytest.mark.parametrize('traffic', [-1, 2, float('nan')])
def test_invalid_traffic_rejected(traffic):
    shadow.init_shadow_db()
    with pytest.raises(ValueError): shadow.create_shadow_config('test','chat','','candidate',traffic)


def test_candidate_keeps_same_rag_evidence(monkeypatch):
    config_id = setup_config(monkeypatch)
    calls=[]
    def llm(**kwargs):
        calls.append(kwargs)
        return completion('candidate answer' if len(calls) == 1 else '{"production_score":6,"shadow_score":7}')
    monkeypatch.setitem(sys.modules, 'services.llm_service', SimpleNamespace(_call_llm=llm))
    asyncio.run(shadow.run_shadow(config_id, {'base_prompt':'old', 'messages':[{'role':'system','content':'old\nRAG evidence'}, {'role':'user','content':'q'}]}, 'prod','chat'))
    assert calls[0]['messages'][0]['content'] == 'candidate\nRAG evidence'


def test_daily_cap_prevents_more_model_calls(monkeypatch):
    config_id = setup_config(monkeypatch)
    conn = _get_conn()
    conn.executemany("INSERT INTO shadow_runs (config_id,agent_type,status) VALUES (?,'chat','execution_failed')", [(config_id,)] * 20)
    conn.commit(); conn.close()
    monkeypatch.setitem(sys.modules, 'services.llm_service', SimpleNamespace(_call_llm=lambda **kw: pytest.fail('daily cap must stop calls')))
    assert asyncio.run(shadow.run_shadow(config_id, {}, 'prod','chat')) is None


def test_review_recommendation_never_activates_prompt(monkeypatch):
    config_id = setup_config(monkeypatch)
    conn = _get_conn()
    conn.executemany("INSERT INTO shadow_runs (config_id,agent_type,status,production_score,shadow_score) VALUES (?,'chat','scored',6,8)", [(config_id,)] * 30)
    conn.commit(); conn.close()
    monkeypatch.setattr('db.eval.activate_prompt_version', lambda *a, **kw: pytest.fail('manual review required'))
    recommendations = asyncio.run(shadow.auto_promote_shadows())
    assert recommendations[0]['action'] == 'review_required'
    assert shadow.list_shadow_configs()[0]['is_active'] == 1


def test_deleting_config_does_not_restore_daily_budget(monkeypatch):
    config_id = setup_config(monkeypatch)
    conn = _get_conn()
    conn.executemany("INSERT INTO shadow_runs (config_id,agent_type,status) VALUES (?,'chat','execution_failed')", [(config_id,)] * 20)
    conn.commit(); conn.close()
    shadow.delete_shadow_config(config_id)
    new_id = shadow.create_shadow_config('new','chat','','candidate',1)
    monkeypatch.setitem(sys.modules, 'services.llm_service', SimpleNamespace(_call_llm=lambda **kw: pytest.fail('deletion must not release daily budget')))
    assert asyncio.run(shadow.run_shadow(new_id,{},'prod','chat')) is None


def test_auto_evolution_keeps_unsupported_agents_as_drafts(monkeypatch):
    from agent.eval import conversation_evolution as evolution
    shadow.init_shadow_db()
    monkeypatch.setattr('db.portfolio.list_all_bad_cases', lambda **kw: [{'id': 1}])
    cases = [{'root_cause': 'data_error', 'agent_type': 'risk_assessor',
              'suggestion': 'use evidence'}] * 3
    monkeypatch.setattr('infra.root_cause_analyzer.batch_analyze', lambda **kw: {'results': cases})
    monkeypatch.setattr('db.eval.get_active_prompt', lambda kind: {'prompt_content': 'old'})
    drafts = []
    monkeypatch.setattr('db.eval.create_prompt_version', lambda **kw: drafts.append(kw) or 42)
    monkeypatch.setattr(evolution, '_generate_improved_prompt', lambda *args: 'candidate')
    result = asyncio.run(evolution._auto_root_cause_to_fix(1, {}))
    assert len(drafts) == 1
    assert result[0]['version_id'] == 42
    assert result[0]['shadow_id'] is None
    assert result[0]['status'] == 'draft_only'
    assert shadow.list_shadow_configs(active_only=False) == []
