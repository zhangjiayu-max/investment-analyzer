"""治理评测回归：临时库、替代执行器，禁止真实模型调用。"""
import asyncio
import concurrent.futures
import json

import pytest


def test_prompt_activation_checks_ownership_and_updates_real_agent(tmp_db):
    from db.eval import create_prompt_version, activate_prompt_version, get_active_prompt
    from db.analysis import get_analysis_agent_by_name
    vid = create_prompt_version('panorama', 'audit', '真实候选提示词' * 3)
    assert activate_prompt_version(vid, 'panorama')
    assert get_analysis_agent_by_name('全景诊断分析师')['system_prompt'] == '真实候选提示词' * 3
    assert not activate_prompt_version(999999, 'panorama')
    assert not activate_prompt_version(vid, 'deep_dive')
    assert get_active_prompt('panorama')['id'] == vid


def test_source_conversion_is_atomic_and_idempotent(tmp_db):
    from db.eval import create_eval_case_from_source
    def create(_):
        return create_eval_case_from_source('bad_case:chat', 20, name='案例', analysis_type='ai', input_params='{}')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert len({r['id'] for r in results}) == 1
    assert sum(r['created'] for r in results) == 1


def test_run_preserves_input_and_normalizes_raw_rubric(tmp_db):
    from db.eval import create_eval_case, create_eval_run, update_eval_case, get_eval_run_detail
    cid = create_eval_case('旧名称', 'panorama', '{"key":1}', expected_quality='旧标准')
    rid = create_eval_run(cid, 'panorama', '结果', score=20, score_max=25, rubric_version='judge-v1')
    update_eval_case(cid, name='新名称', input_params='{}', expected_quality='新标准')
    run = get_eval_run_detail(rid)
    assert run['score'] == 8
    assert run['raw_score'] == 20 and run['score_max'] == 25
    assert run['input_params'] == '{"key":1}' and run['expected_quality'] == '旧标准'
    assert run['case_name'] == '旧名称'


def test_legacy_migration_uses_rubric_evidence_and_is_repeatable(tmp_db):
    from db._conn import _get_conn
    from db.eval import init_eval_tables
    with _get_conn() as conn:
        conn.execute("INSERT INTO eval_runs(case_id,analysis_type,score,result_data) VALUES(1,'panorama',20,?)",
                     (json.dumps({'scores': {'score_total':20}, 'run_mode':'daily'}),))
        conn.execute("INSERT INTO eval_runs(case_id,analysis_type,score,result_data) VALUES(1,'ai',8,'{}')")
        init_eval_tables(conn)
        init_eval_tables(conn)
        rows = conn.execute('SELECT score,score_normalized,score_max FROM eval_runs ORDER BY id').fetchall()
    assert [r['score'] for r in rows] == [8, 8]
    assert [r['score_normalized'] for r in rows] == [80, 80]
    assert [r['score_max'] for r in rows] == [25, 10]


def test_real_executor_waits_for_pending_analysis(tmp_db, monkeypatch):
    from services.quality import eval_execution as execution
    import routers.analysis.panorama as route
    from db.async_tasks import create_async_task, update_async_task
    tid = create_async_task('panorama_analysis')
    async def submit(req):
        async def complete():
            await asyncio.sleep(0)
            update_async_task(tid, status='done', result={'result':'真实报告', 'token_usage':123})
        asyncio.create_task(complete())
        return {'task_id':tid, 'status':'running'}
    monkeypatch.setattr(route, 'panorama_analysis_api', submit)
    result = asyncio.run(execution.execute_case({'analysis_type':'panorama','input_params':'{}'}))
    assert result['result'] == '真实报告' and result['token_usage'] == 123


def test_ab_executes_target_prompt_and_keeps_versions_isolated(tmp_db, monkeypatch):
    import routers.analysis.eval_system as route
    from db.eval import create_eval_case, create_prompt_version, activate_prompt_version
    cid = create_eval_case('A/B', 'panorama', '{}')
    a = create_prompt_version('panorama', 'A', '旧版真实提示词' * 3)
    b = create_prompt_version('panorama', 'B', '新版真实提示词' * 3)
    activate_prompt_version(a, 'panorama')
    seen = []
    async def execute(case, prompt_override=None):
        seen.append(prompt_override)
        return {'result':prompt_override, 'token_usage':0}
    async def judge(**kwargs):
        return {'score_total':20, 'judge_comments':'评分', 'status':'scored'}
    monkeypatch.setattr(route, 'execute_case', execute)
    monkeypatch.setattr(route, 'run_llm_judge', judge)
    async def run():
        await route._execute_single_eval(cid, 'A')
        await route._execute_single_eval(cid, 'B')
    asyncio.run(run())
    assert seen == ['旧版真实提示词' * 3, '新版真实提示词' * 3]


def test_conversation_stats_respect_window(tmp_db):
    from db._conn import _get_conn
    from db.eval import get_conversation_eval_stats
    with _get_conn() as conn:
        conn.execute("INSERT INTO conversation_evaluations(conversation_id,auto_score,created_at) VALUES(1,90,datetime('now','localtime'))")
        conn.execute("INSERT INTO conversation_evaluations(conversation_id,auto_score,created_at) VALUES(2,10,datetime('now','localtime','-15 days'))")
    assert get_conversation_eval_stats(7)['total_evals'] == 1
    assert get_conversation_eval_stats(30)['total_evals'] == 2


def test_quality_aggregates_share_effective_population(tmp_db):
    from db.eval import create_eval_case, create_eval_run, get_governance_quality
    from db.dashboard import save_llm_feedback
    cid = create_eval_case('测试','ai')
    create_eval_run(cid,'ai','有效',score=4)
    create_eval_run(cid,'ai','跳过',score=0)
    create_eval_run(cid,'ai','待评')
    save_llm_feedback('quality_evaluator', score_data_accuracy=4,score_logic=4,score_actionability=4)
    report = get_governance_quality(30)
    assert report['summary']['scored_count'] == 2
    assert report['summary']['low_quality_count'] == 2
    assert len(report['low_quality']) == 2
    assert report['agents'][0]['avg_score'] == 4
    assert report['trend'][0]['avg_score'] == 4


def test_done_task_uses_persisted_analysis_output(tmp_db, monkeypatch):
    from services.quality.eval_execution import await_analysis_result
    from db.async_tasks import create_async_task, update_async_task
    import db.portfolio as portfolio
    tid = create_async_task('panorama_analysis')
    update_async_task(tid,status='done',result={'record_id':22})
    monkeypatch.setattr(portfolio,'get_analysis_record_status',lambda rid: {'id':rid,'status':'done','result_data':'持久化完整输出','token_usage':17})
    result = asyncio.run(await_analysis_result({'task_id':tid,'status':'running'}))
    assert result['result'] == '持久化完整输出' and result['token_usage'] == 17


def test_unsupported_case_is_skipped_without_generic_llm(tmp_db):
    import routers.admin.eval as route
    from db.eval import create_eval_case,get_eval_run_detail
    cid = create_eval_case('历史来源','quality_evaluator','{}')
    result = asyncio.run(route._do_eval_case_run(cid))
    assert result['skipped'] and not result['ok']
    run = get_eval_run_detail(result['run_id'])
    assert run['score_status'] == 'skipped' and run['score'] is None


def test_suggestion_accept_is_atomic_idempotent_and_terminal(tmp_db):
    from db._conn import _get_conn
    from db.eval import accept_governance_suggestion,reject_governance_suggestion
    with _get_conn() as conn:
        sid = conn.execute("INSERT INTO eval_suggestions(conversation_id,name,analysis_type) VALUES(1,'建议','orchestrator')").lastrowid
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:accept_governance_suggestion(sid),range(4)))
    assert len({r['case_id'] for r in results}) == 1
    assert sum(not r['reused'] for r in results) == 1
    assert not reject_governance_suggestion(sid)


def test_analysis_judge_failure_is_retryable_not_scored(tmp_db, monkeypatch):
    from db.agent_analysis_log import create_analysis_log,get_analysis_log
    from db.async_tasks import get_async_task
    import routers.analysis.analysis_log as route
    import agent.eval.eval_scorer as scorer
    lid = create_analysis_log('judge-failure','panorama','analysis_history',status='done')
    monkeypatch.setattr(route,'fetch_source_result',lambda *a,**k:'测试分析结果'*30)
    async def failed(**kwargs):
        return {'overall_score':0,'overall_reason':'模型不可用'}
    monkeypatch.setattr(scorer,'evaluate_llm_output',failed)
    async def run():
        first = await route.evaluate_analysis_log_api(lid)
        await asyncio.gather(*list(route._eval_tasks))
        assert get_async_task(first['task_id'])['status'] == 'error'
        second = await route.evaluate_analysis_log_api(lid)
        assert second['task_id'] != first['task_id']
        await asyncio.gather(*list(route._eval_tasks))
    asyncio.run(run())
    log = get_analysis_log(lid)
    assert log['eval_status'] == 'error' and not log['has_eval'] and log['eval_score'] is None


def test_ab_rejects_wrong_types_and_changed_input_pairs(tmp_db, monkeypatch):
    import routers.analysis.eval_system as route
    from fastapi import HTTPException
    from db.eval import create_prompt_version
    a = create_prompt_version('panorama','old','旧提示词'*4)
    b = create_prompt_version('deep_dive','new','新提示词'*4)
    with pytest.raises(HTTPException) as err:
        asyncio.run(route.run_batch_eval_api(route.EvalBatchRequest(version_a=a,version_b=b)))
    assert err.value.status_code == 400
    async def valid(*args,**kwargs):
        return {'scores':{'score_total':20}}
    monkeypatch.setattr(route,'_execute_single_eval',valid)
    evidence = iter([{'hash':'before'},{'hash':'after'}])
    monkeypatch.setattr(route,'capture_data_evidence',lambda _:next(evidence))
    with pytest.raises(RuntimeError,match='成对有效'):
        asyncio.run(route._compare_prompt_versions({'id':2,'version':'B'},{'id':1,'version':'A'},[{'id':1}]))


def test_case_update_rejects_nonobject_json():
    from models.eval import UpdateEvalCaseRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        UpdateEvalCaseRequest(input_params='[]')


def test_malformed_judge_json_does_not_fabricate_average_score(tmp_db, monkeypatch):
    from types import SimpleNamespace
    import routers.analysis.eval_system as route
    monkeypatch.setattr(route,'get_config',lambda *args:'true')
    monkeypatch.setattr(route,'_call_llm',lambda **kwargs:SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))]))
    result = asyncio.run(route.run_llm_judge('panorama','','{}','完整输出'))
    assert result['status'] == 'error' and result['score_total'] == 0


def test_root_cause_batch_keeps_event_loop_responsive(tmp_db, monkeypatch):
    import threading
    import routers.analysis.portfolio_ai as route
    import infra.root_cause_analyzer as analyzer
    from db.async_tasks import get_async_task
    entered,release = threading.Event(),threading.Event()
    def blocking(*args):
        entered.set()
        release.wait(2)
        return {'total':1,'analyzed':1,'failed':0}
    monkeypatch.setattr(analyzer,'batch_analyze',blocking)
    async def run():
        ack = await route.batch_analyze_root_cause(1)
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set() and not release.is_set()
            assert get_async_task(ack['task_id'])['status'] == 'running'
        finally:
            release.set()
            await asyncio.gather(*list(route._background_tasks))
        assert get_async_task(ack['task_id'])['status'] == 'done'
    asyncio.run(run())


def test_batch_separates_quality_threshold_skips_and_execution_errors(tmp_db, monkeypatch):
    import routers.admin.eval as route
    from db.async_tasks import create_async_task,get_async_task
    responses = iter([
        {'ok':True,'score':8,'score_status':'scored'},
        {'ok':True,'score':3,'score_status':'scored'},
        {'ok':False,'skipped':True},
        {'ok':False,'error':'执行异常'},
    ])
    async def run_one(cid):
        return next(responses)
    monkeypatch.setattr(route,'_do_eval_case_run',run_one)
    tid = create_async_task('eval_case_batch')
    asyncio.run(route._run_case_batch(tid,[1,2,3,4]))
    result = get_async_task(tid)['result']
    if isinstance(result,str):
        result = json.loads(result)
    assert (result['passed'],result['failed'],result['skipped'],result['errors']) == (1,1,1,1)


@pytest.mark.parametrize('body',['{}','[]','{"score":true,"reason":"说明"}','{"score":20,"reason":"说明"}','{"score":NaN,"reason":"说明"}','{"score":8}'])
def test_invalid_judge_cannot_score_eval_run(tmp_db,monkeypatch,body):
    from types import SimpleNamespace
    import agent.eval.eval_scorer as scorer
    import routers.admin.eval as route
    from db.eval import create_eval_case,create_eval_run,get_eval_run_detail
    monkeypatch.setattr(scorer,'_get_eval_agent',lambda:None)
    monkeypatch.setattr(scorer,'_call_llm',lambda **kw:SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))]))
    cid = create_eval_case('严格评分','ai','{}',expected_quality='必须引用数据')
    rid = create_eval_run(cid,'ai','完整输出')
    state = asyncio.run(route._score_run_async(rid,{'expected_quality':'必须引用数据'},'真实报告'*50,'ai'))
    run = get_eval_run_detail(rid)
    assert state == 'error' and run['score_status'] == 'error' and run['score'] is None


def test_numeric_prompt_label_does_not_override_explicit_id(tmp_db,monkeypatch):
    import routers.analysis.eval_system as route
    from db.eval import create_eval_case,create_prompt_version
    cid = create_eval_case('ID归属','panorama')
    create_prompt_version('panorama','2','标签2的提示词'*3)
    chosen = create_prompt_version('panorama','real','目标ID的提示词'*3)
    seen=[]
    async def execute(case,prompt_override=None):
        seen.append(prompt_override)
        return {'result':'完整输出'}
    async def judge(**kw):
        return {'score_total':20,'judge_comments':'有效','status':'scored'}
    monkeypatch.setattr(route,'execute_case',execute)
    monkeypatch.setattr(route,'run_llm_judge',judge)
    asyncio.run(route._execute_single_eval(cid,chosen))
    assert seen == ['目标ID的提示词'*3]


def test_legacy_active_does_not_override_production_prompt(tmp_db,monkeypatch):
    import routers.analysis.eval_system as route
    from db.eval import create_eval_case,create_prompt_version,init_eval_tables
    from db._conn import _get_conn
    cid = create_eval_case('生产基准','panorama')
    legacy = create_prompt_version('panorama','legacy','旧版仅评测提示词'*3)
    with _get_conn() as conn:
        conn.execute('UPDATE prompt_versions SET is_active=1 WHERE id=?',(legacy,))
        init_eval_tables(conn)
    production = route.production_prompt('panorama')
    seen=[]
    async def execute(case,prompt_override=None):
        seen.append(prompt_override)
        return {'result':'真实生产输出'}
    async def judge(**kw):
        return {'score_total':20,'judge_comments':'有效','status':'scored'}
    monkeypatch.setattr(route,'execute_case',execute)
    monkeypatch.setattr(route,'run_llm_judge',judge)
    asyncio.run(route._execute_single_eval(cid))
    assert seen == [production]
    versions=asyncio.run(route.list_prompts_api('panorama'))['versions']
    assert versions[0]['publication_status'] == 'drifted' and not versions[0]['is_current']


def test_prompt_regression_reports_error_skip_new_separately(tmp_db,monkeypatch):
    import db
    import agent.eval.regression as route
    monkeypatch.setattr(db,'list_eval_cases_by_agent',lambda *args:[{'id':1},{'id':2},{'id':3}])
    responses=iter([{'status':'error'},{'status':'skipped'},{'status':'new'}])
    async def one(*args):
        return next(responses)
    monkeypatch.setattr(route,'_run_single_case',one)
    stored=[]
    monkeypatch.setattr(route,'set_regression_result',lambda *args:stored.append(args[-1]))
    asyncio.run(route.run_regression_tests(1))
    result=stored[-1]
    assert result['status']=='partial'
    assert result['summary']=={'total':3,'scored':1,'improved':0,'degraded':0,'unchanged':0,'new':1,'skipped':1,'error':1}



def test_regression_terminal_counts_survive_process_cache_loss(tmp_db):
    from db.eval import save_regression_result,get_latest_regression_result
    save_regression_result(1,'conversation',{'status':'partial','cases':[{'status':'new'},{'status':'skipped'},{'status':'error'}]})
    result=get_latest_regression_result(1,'conversation')
    assert result['status']=='partial' and result['summary']['scored']==1
    assert result['summary']['unchanged']==0 and result['summary']['error']==1 and result['summary']['skipped']==1
