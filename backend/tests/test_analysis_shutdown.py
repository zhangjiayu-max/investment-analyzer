"""服务关闭应等待指数分析落库，且不能取消 ASGI 生命周期或其他任务。"""
import asyncio
import json


def test_shutdown_waits_for_index_report_and_preserves_unowned_tasks(monkeypatch):
    from app import app as api_app
    from routers.analysis import index_analysis as route
    from models.analysis import AnalysisRunRequest
    from db.analysis import get_analysis_history_item
    from db.async_tasks import get_async_task
    from db.agent_analysis_log import get_analysis_log_by_trace
    import agent.eval_scorer as scorer

    async def noop(**kwargs):
        pass

    monkeypatch.setattr(scorer, 'evaluate_llm_output', noop)
    monkeypatch.setattr(route, '_collect_valuation_evidence', lambda req: route._empty_valuation_evidence(req.metric_type))
    monkeypatch.setattr(route, '_collect_index_news', lambda req: {'items': [], 'context': '', 'warnings': []})
    monkeypatch.setattr(route, 'inject_rag_context', lambda **kw: '')
    monkeypatch.setattr(route, 'list_holdings', lambda: [])
    monkeypatch.setattr(route, 'build_portfolio_facts_with_check', lambda: '{}')

    async def run():
        model_started, finish_model = asyncio.Event(), asyncio.Event()
        traces = []

        async def complete(*args, **kwargs):
            traces.append(kwargs['trace_id'])
            model_started.set()
            await finish_model.wait()
            return '完整的创业板分析报告', 23317

        monkeypatch.setattr(route, 'complete_text_analysis', complete)
        unowned = asyncio.create_task(asyncio.Event().wait())
        started = await route.run_analysis(AnalysisRunRequest(index_code='399006', index_name='创业板指'))
        await asyncio.wait_for(model_started.wait(), 2)
        shutting_down = asyncio.create_task(api_app.router.on_shutdown[0]())
        try:
            await asyncio.sleep(.02)
            assert not shutting_down.done(), '关闭流程必须等待模型报告保存'
            finish_model.set()
            await asyncio.wait_for(shutting_down, 2)
            assert not unowned.done(), '不能取消不属于应用后台注册表的任务'
            history = get_analysis_history_item(started['id'])
            task = get_async_task(started['task_id'])
            log = get_analysis_log_by_trace(traces[0])
            assert history['status'] == task['status'] == log['status'] == 'done'
            assert '完整的创业板分析报告' in history['result']
            assert json.loads(task['result'])['result'] == history['result']
            assert history['token_usage'] == task['token_usage'] == log['token_usage'] == 23317
        finally:
            finish_model.set()
            if not shutting_down.done():
                await shutting_down
            unowned.cancel()
            await asyncio.gather(unowned, return_exceptions=True)

    asyncio.run(run())


def test_index_shutdown_deadline_persists_restart_reason(monkeypatch):
    from routers.analysis import index_analysis as route
    from models.analysis import AnalysisRunRequest
    from db.analysis import get_analysis_history_item
    from db.async_tasks import get_async_task
    from db.agent_analysis_log import create_analysis_log, get_analysis_log_by_trace

    async def run():
        entered = asyncio.Event()
        traces = []

        async def pending(history_id, req, agent, task_id, trace_id, started):
            traces.append(trace_id)
            create_analysis_log(trace_id, 'index_analysis', 'analysis_history', source_id=history_id)
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(route, '_execute_index_analysis', pending)
        started = await route.run_analysis(AnalysisRunRequest(index_code='399006'))
        await asyncio.wait_for(entered.wait(), 2)
        await route.drain_index_analysis_tasks(timeout=.01)
        history = get_analysis_history_item(started['id'])
        task = get_async_task(started['task_id'])
        log = get_analysis_log_by_trace(traces[0])
        assert history['status'] == task['status'] == log['status'] == 'error'
        assert '服务重启' in history['error_msg']
        assert '服务重启' in log['error_details']['summary']
        assert not any(not t.done() for t in route._background_tasks)

    asyncio.run(run())


def test_application_background_tasks_are_cancelled_and_awaited():
    from infra.background_tasks import create_background_task, cancel_background_tasks

    async def run():
        cleaned = asyncio.Event()

        async def periodic():
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        owned = create_background_task(periodic())
        await asyncio.sleep(0)
        await cancel_background_tasks()
        assert owned.cancelled() and cleaned.is_set()

    asyncio.run(run())
