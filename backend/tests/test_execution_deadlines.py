"""执行等待时间和取消不依赖真实模型。"""
import threading
import time
import pytest
from types import SimpleNamespace
from importlib import import_module


@pytest.fixture(autouse=True)
def _finish_fake_workers_before_restoring_patches(monkeypatch):
    """超时断言后先回收假工作线程，再还原依赖，避免影响下一条用例。"""
    from concurrent.futures import wait
    from infra.execution_budget import DATA_EXECUTOR, SPECIALIST_EXECUTOR
    submitted = []
    for executor in (DATA_EXECUTOR, SPECIALIST_EXECUTOR):
        submit = executor.submit
        def tracked_submit(*args, _submit=submit, **kwargs):
            future = _submit(*args, **kwargs)
            submitted.append(future)
            return future
        monkeypatch.setattr(executor, 'submit', tracked_submit)
    yield
    if submitted:
        _, pending = wait(submitted, timeout=1)
        assert not pending, '假线程必须在恢复测试依赖之前结束'


def _llm_client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.mark.parametrize('streaming', [False, True])
def test_llm_fallback_rechecks_deadline_before_sending(monkeypatch, streaming):
    from infra.execution_budget import execution_budget
    m = import_module('services.llm.llm_service')
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    calls = []
    def primary(**kwargs):
        time.sleep(.03)
        raise RuntimeError('primary failed')
    monkeypatch.setattr(m, 'client', _llm_client(primary))
    monkeypatch.setattr(m, '_fallback_client', _llm_client(lambda **kw: calls.append(kw)))
    monkeypatch.setattr(m, '_fallback_model', m.MODEL)
    with execution_budget(.01), pytest.raises(TimeoutError):
        if streaming:
            list(m._call_llm_stream(model=m.MODEL, messages=[]))
        else:
            m._call_llm(model=m.MODEL, messages=[])
    assert calls == []


def test_llm_retry_keeps_primary_model_and_reduces_fallback_timeout(monkeypatch):
    import httpx
    from openai import APIConnectionError
    from tenacity import retry, stop_after_attempt, wait_none, retry_if_exception
    m = import_module('services.llm.llm_service')
    from infra.execution_budget import execution_budget
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, '_llm_retry', retry(stop=stop_after_attempt(2), wait=wait_none(),
                                           retry=retry_if_exception(m._is_retryable), reraise=True))
    primary_calls, fallback_calls = [], []
    response = SimpleNamespace(model=m.MODEL, usage=None)
    def primary(**kwargs):
        primary_calls.append(kwargs)
        if len(primary_calls) == 1:
            time.sleep(.02)
            raise RuntimeError('temporary primary failure')
        return response
    def fallback(**kwargs):
        fallback_calls.append(kwargs)
        raise APIConnectionError(request=httpx.Request('POST', 'https://example.invalid'))
    monkeypatch.setattr(m, 'client', _llm_client(primary))
    monkeypatch.setattr(m, '_fallback_client', _llm_client(fallback))
    monkeypatch.setattr(m, '_fallback_model', 'mimo-compatible-fallback')
    with execution_budget(.2):
        assert m._call_llm(model=m.MODEL, messages=[]) is response
    assert [kw['model'] for kw in primary_calls] == [m.MODEL, m.MODEL]
    assert fallback_calls[0]['timeout'] < primary_calls[0]['timeout'] - .01


def test_llm_cancel_does_not_start_fallback(monkeypatch):
    from infra.execution_budget import ExecutionCancelled
    m = import_module('services.llm.llm_service')
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    def cancelled(**kwargs):
        raise ExecutionCancelled('cancelled')
    calls = []
    monkeypatch.setattr(m, 'client', _llm_client(cancelled))
    monkeypatch.setattr(m, '_fallback_client', _llm_client(lambda **kw: calls.append(kw)))
    monkeypatch.setattr(m, '_fallback_model', m.MODEL)
    with pytest.raises(ExecutionCancelled):
        m._call_llm(model=m.MODEL, messages=[])
    assert calls == []


class _TestStream:
    def __init__(self, delay=0):
        self.closed = False
        self.delay = delay
    def __iter__(self):
        for _ in range(5):
            time.sleep(self.delay)
            yield SimpleNamespace(model='mimo', usage=SimpleNamespace(), choices=[
                SimpleNamespace(delta=SimpleNamespace(content='x', reasoning_content=None)),
            ])
    def close(self):
        self.closed = True


def test_llm_stream_has_own_total_budget_and_closes_on_timeout(monkeypatch):
    m = import_module('services.llm.llm_service')
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, '_LLM_TOTAL_TIMEOUT', .03, raising=False)
    monkeypatch.setattr(m, '_record_token_usage', lambda *a, **kw: None)
    stream = _TestStream(.02)
    monkeypatch.setattr(m, 'client', _llm_client(lambda **kw: stream))
    with pytest.raises(TimeoutError):
        list(m._call_llm_stream(model=m.MODEL, messages=[]))
    assert stream.closed


@pytest.mark.parametrize('consume_all', [False, True])
def test_llm_stream_closes_on_completion_and_consumer_close(monkeypatch, consume_all):
    m = import_module('services.llm.llm_service')
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, '_record_token_usage', lambda *a, **kw: None)
    stream = _TestStream()
    monkeypatch.setattr(m, 'client', _llm_client(lambda **kw: stream))
    generator = m._call_llm_stream(model=m.MODEL, messages=[])
    if consume_all:
        list(generator)
    else:
        next(generator)
        generator.close()
    assert stream.closed


def test_pipeline_info_gather_returns_at_deadline_without_worker_shutdown_wait(monkeypatch):
    m = import_module('agent.core.pipeline')
    from agent.state.pipeline_state import PipelineState
    from infra.execution_budget import DATA_EXECUTOR
    release = threading.Event()
    submit = DATA_EXECUTOR.submit
    # 在执行边界模拟阻塞的数据源，避开 services 兼容导入的旧模块副本。
    monkeypatch.setattr(DATA_EXECUTOR, 'submit', lambda *a, **kw: submit(lambda: release.wait(.3)))
    original_config = m.get_config_int
    monkeypatch.setattr(m, 'get_config_int', lambda key, default: .02 if key == 'pipeline.info_gather_timeout' else original_config(key, default))
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            m._phase_info_gather(PipelineState(refined_query='测试'), {}, [], 't', prepared_rag_context='prepared')
        assert time.monotonic() - started < .15
    finally:
        release.set()


def test_pipeline_info_gather_cancel_does_not_submit_tasks(monkeypatch):
    m = import_module('agent.core.pipeline')
    from agent.state.pipeline_state import PipelineState
    from infra.execution_budget import DATA_EXECUTOR
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(DATA_EXECUTOR, 'submit', lambda *a: pytest.fail('cancelled task submitted'))
    with pytest.raises(m._PipelineCancelled):
        m._phase_info_gather(PipelineState(refined_query='测试'), {}, [], 't', prepared_rag_context='', cancel_event=cancel)


@pytest.mark.parametrize('cancelled', [False, True])
def test_orchestrator_stream_cross_review_wait_is_bounded(monkeypatch, cancelled):
    m = import_module('agent.core.orchestrator')
    release = threading.Event()
    cancel = threading.Event()
    monkeypatch.setattr(m, '_detect_conflicts_cached', lambda *a: {'detected': False})
    monkeypatch.setattr(m, 'should_run_cross_review', lambda **kw: True)
    monkeypatch.setattr(m, '_is_cost_routing_enabled', lambda: False)
    monkeypatch.setattr(m, 'get_config_int', lambda key, default: .02)
    def review(*a, **kw):
        if cancelled:
            cancel.set()
        release.wait(.3)
        return {'analysis': 'late', 'duration_ms': 1}
    monkeypatch.setattr(m, 'run_cross_review_opinion', review)
    started = time.monotonic()
    try:
        with pytest.raises(m.CancelledError if cancelled else TimeoutError):
            list(m._stream_handle_no_tool_calls(
                SimpleNamespace(content='answer'), [{'agent_key': 'a', 'agent': 'A', 'icon': '', 'analysis': 'x'}],
                [], 'q', 'q', '', 'complex', {}, {}, {}, False, False, '', cancel, time.time(), None, None, 't',
            ))
        assert time.monotonic() - started < .15
    finally:
        release.set()


@pytest.mark.parametrize('cancelled', [False, True])
def test_pipeline_cross_review_timeout_and_cancel_do_not_wait_for_threads(monkeypatch, cancelled):
    m = import_module('agent.core.pipeline')
    orchestrator = import_module('agent.core.orchestrator')
    multi_agent = import_module('agent.core.multi_agent')
    release = threading.Event()
    cancel = threading.Event()
    monkeypatch.setattr(m, '_phase_preprocess', lambda *a: {'complexity': 'complex', 'query_info': {}})
    monkeypatch.setattr(m, 'is_simple_chat', lambda *a: False)
    monkeypatch.setattr(m, 'needs_clarification', lambda *a: (False, ''))
    monkeypatch.setattr(m, '_phase_info_gather', lambda *a, **kw: {})
    monkeypatch.setattr(m, '_phase_planning', lambda *a: {})
    monkeypatch.setattr(m, 'get_config_bool', lambda key, default: key == 'cross_review_enabled')
    original_config = m.get_config_int
    monkeypatch.setattr(m, 'get_config_int', lambda key, default: .02 if key == 'agent.cross_review_timeout' else original_config(key, default))
    monkeypatch.setattr(orchestrator, 'should_run_cross_review', lambda **kw: True)
    def execution(*a):
        yield from ()
        return {'specialists': [{'agent_key': 'a', 'analysis': 'existing analysis'}]}
    monkeypatch.setattr(m, '_phase_execution', execution)
    synthesis_calls = []
    monkeypatch.setattr(m, '_phase_synthesis', lambda *a, **kw: synthesis_calls.append(1) or {'answer': 'synthesized'})
    monkeypatch.setattr(m, '_phase_memory', lambda *a, **kw: {})
    def review(*a, **kw):
        if cancelled:
            cancel.set()
        release.wait(.3)
        return {'analysis': 'late', 'duration_ms': 1}
    monkeypatch.setattr(multi_agent, 'run_cross_review_opinion', review)
    started = time.monotonic()
    try:
        events = list(m.run_pipeline('q', [], None, None, 't', cancel_event=cancel))
        assert time.monotonic() - started < .15
        if cancelled:
            assert events[-1]['type'] == 'terminated'
            assert not synthesis_calls
            assert not any(e['type'] == 'degrade' for e in events)
        else:
            assert synthesis_calls == [1]
            assert any(e['type'] == 'answer' for e in events)
    finally:
        release.set()


@pytest.mark.parametrize('timed_out', [False, True])
def test_stream_synthesis_cancel_does_not_call_nonstream_fallback(monkeypatch, timed_out):
    from infra.execution_budget import ExecutionCancelled
    m = import_module('agent.core.orchestrator')
    arbitration = import_module('agent.core.arbitration')
    llm = import_module('services.llm_service')
    monkeypatch.setattr(arbitration, 'arbitrate_results', lambda *a, **kw: {})
    monkeypatch.setattr(m, '_build_final_synthesis_prompt', lambda *a, **kw: 'prompt')
    def cancelled(**kw):
        raise (TimeoutError('deadline') if timed_out else ExecutionCancelled('cancelled'))
        yield
    monkeypatch.setattr(llm, '_call_llm_stream', cancelled)
    monkeypatch.setattr(m, '_call_llm', lambda **kw: pytest.fail('cancelled stream started fallback'))
    monkeypatch.setattr(m, '_validate_and_repair', lambda *a, **kw: pytest.fail('expired stream started validator'))
    with pytest.raises((ExecutionCancelled, m.CancelledError, TimeoutError)):
        list(m._stream_final_synthesis('q', 'q', [], [], [], [], '', 'complex', False,
                                      {}, {}, {}, threading.Event(), time.time(), None, None, 't'))


def test_pipeline_deadline_is_terminal_instead_of_react_restart(monkeypatch):
    p = import_module('agent.core.pipeline')
    m = import_module('agent.core.orchestrator')
    monkeypatch.setattr(p, '_phase_preprocess', lambda *a: {'complexity': 'complex', 'query_info': {}})
    monkeypatch.setattr(p, 'is_simple_chat', lambda *a: False)
    monkeypatch.setattr(p, 'needs_clarification', lambda *a: (False, ''))
    def timeout(*a, **kw):
        raise TimeoutError('information gathering still running')
    monkeypatch.setattr(p, '_phase_info_gather', timeout)
    monkeypatch.setattr(p, 'is_pipeline_enabled', lambda: True, raising=False)
    monkeypatch.setattr(m, '_stream_precheck', lambda *a, **kw: pytest.fail('timed-out pipeline restarted ReAct'))
    events = list(m.orchestrate_stream('q', [], rag_context='prepared'))
    assert events[-1]['type'] == 'error'
    assert '超时' in events[-1]['message']


@pytest.mark.parametrize('cancelled', [False, True, 'after_timeout'])
def test_parallel_timeout_keeps_finished_specialists_for_degraded_synthesis(monkeypatch, cancelled):
    p = import_module('agent.core.pipeline')
    multi_agent = import_module('agent.core.multi_agent')
    builder = import_module('agent.core.context_builder')
    release = threading.Event()
    cancel = threading.Event()
    specs = [{'agent_key': key, 'name': key, 'system_prompt': 'configured'} for key in ('fast', 'slow')]
    plan = {'specialists': specs, 'plan': {'steps': [{'agent_key': s['agent_key']} for s in specs]}}
    monkeypatch.setattr(p, '_phase_preprocess', lambda *a: {'complexity': 'medium', 'query_info': {}})
    monkeypatch.setattr(p, 'is_simple_chat', lambda *a: False)
    monkeypatch.setattr(p, 'needs_clarification', lambda *a: (False, ''))
    monkeypatch.setattr(p, '_phase_info_gather', lambda *a, **kw: {})
    monkeypatch.setattr(p, '_phase_planning', lambda *a: plan)
    monkeypatch.setattr(p, 'get_config_bool', lambda key, default: key == 'pipeline.parallel_execution')
    original_config = p.get_config_int
    monkeypatch.setattr(p, 'get_config_int', lambda key, default: .06 if key == 'agent.specialist_timeout' else original_config(key, default))
    monkeypatch.setattr(builder, 'build_specialist_context', lambda **kw: 'prepared')
    monkeypatch.setattr(p, '_route_model', lambda *a: 'mimo')
    monkeypatch.setattr(p, '_phase_memory', lambda *a, **kw: {})
    monkeypatch.setattr(p, '_synthesize_multiple_specialists', lambda *a, **kw: ('根据本轮证据核对风险，缺失专家意见需进一步复核。' * 12, {}, 'completed'))
    analysis = '已核对可用持仓证据，风险敞口应依据总资产口径评估，缺失信息需要保留并补查。' * 10
    def specialist(**kw):
        if kw['agent_key'] == 'slow':
            release.wait(.3)
        return {'agent_key': kw['agent_key'], 'agent': kw['agent_key'], 'analysis': analysis,
                'status': 'success', 'tool_calls': [{'name': 'query_portfolio', 'result': '{}'}]}
    monkeypatch.setattr(multi_agent, 'run_specialist', specialist)
    events = []
    try:
        for event in p.run_pipeline('q', [], None, None, '204-timeout', cancel_event=cancel):
            events.append(event)
            if event['type'] == 'specialist_done' and (cancelled is True or
                    (cancelled == 'after_timeout' and event.get('status') == 'timeout')):
                cancel.set()
        if cancelled:
            assert events[-1]['type'] == 'terminated'
            assert not any(e['type'] == 'answer' for e in events)
        else:
            answer = next(e for e in events if e['type'] == 'answer')
            assert answer['completion_status'] == 'degraded'
            assert answer['quality_status'] == 'degraded'
            assert answer['confidence'] <= .45
            assert answer['tool_calls'][0]['name'] == 'query_portfolio'
            fast, slow = answer['specialist_results']
            assert fast['analysis'] == analysis
            assert slow['status'] == 'timeout'
            assert slow['asset_actions'] == []
            assert not any(e['type'] in {'terminated', 'degrade', 'error'} for e in events)
    finally:
        release.set()


def test_worker_timeout_does_not_discard_another_finished_specialist(monkeypatch):
    p = import_module('agent.core.pipeline')
    from concurrent.futures import Future
    from agent.infra.blackboard import Blackboard
    from agent.infra.convergence import ConvergenceDetector
    from agent.infra.termination import TerminationChecker
    from agent.state.pipeline_state import PipelineState
    builder = import_module('agent.core.context_builder')
    monkeypatch.setattr(builder, 'build_specialist_context', lambda **kw: 'prepared')
    failed, finished = Future(), Future()
    failed.set_exception(TimeoutError('worker exhausted its own budget'))
    analysis = '已核对持仓证据，风险敞口应根据总资产计算，保留数据缺口等待复核。' * 10
    finished.set_result({'agent_key': 'fast', 'analysis': analysis, 'status': 'success',
                         'tool_calls': [{'name': 'query_portfolio', 'result': '{}'}]})
    # 模拟同一批 done 中，超时 future 恰好先被消费的竞争。
    monkeypatch.setattr(p, '_phase_futures', lambda *a: iter([(0, failed), (1, finished)]))
    steps = [{'agent_key': key} for key in ('slow', 'fast')]
    specs = [{'agent_key': key, 'name': key} for key in ('slow', 'fast')]
    state = PipelineState(refined_query='q')
    gen = p._execute_steps_parallel(state, steps, {'specialists': specs}, {}, {},
        ConvergenceDetector(), Blackboard(), TerminationChecker(), None, 'worker-timeout')
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            result = stop.value
            break
    by_key = {s['agent_key']: s for s in result['specialists']}
    assert by_key['fast']['analysis'] == analysis
    assert by_key['slow']['status'] == 'timeout'
    assert result['tool_calls'][0]['name'] == 'query_portfolio'


def test_cancel_is_checked_before_followup_budget_shortcut():
    from agent.core.pipeline import _run_followup_phase, _PipelineCancelled
    from agent.state.pipeline_state import PipelineState
    cancel = threading.Event()
    cancel.set()
    state = PipelineState(tokens_used=1000, token_budget={'max_total': 1000})
    with pytest.raises(_PipelineCancelled):
        _run_followup_phase(lambda *a: pytest.fail('cancelled review'), (state,), cancel, 'reflection')


@pytest.mark.parametrize('phase', ['reflection', 'debate'])
def test_optional_review_timeout_still_returns_degraded_report(monkeypatch, phase):
    p = import_module('agent.core.pipeline')
    from agent.infra.blackboard import Blackboard
    monkeypatch.setattr(p, '_phase_preprocess', lambda *a: {'complexity': 'medium', 'query_info': {}})
    monkeypatch.setattr(p, 'is_simple_chat', lambda *a: False)
    monkeypatch.setattr(p, 'needs_clarification', lambda *a: (False, ''))
    monkeypatch.setattr(p, '_phase_info_gather', lambda *a, **kw: {})
    monkeypatch.setattr(p, '_phase_planning', lambda *a: {})
    monkeypatch.setattr(p, 'get_config_bool', lambda key, default: key == f'pipeline.{phase}_enabled')
    monkeypatch.setattr(Blackboard, 'find_conflicts', lambda *a: [{'severity': 'medium'}])
    def execution(*a):
        yield from ()
        return {'specialists': [{'agent_key': 'risk', 'analysis': '依据现有持仓证据评估风险，保留数据缺口并等待核对。' * 10}]}
    monkeypatch.setattr(p, '_phase_execution', execution)
    def timeout(*a, **kw):
        raise TimeoutError('optional review deadline')
    monkeypatch.setattr(p, '_run_followup_phase', timeout)
    monkeypatch.setattr(p, '_phase_memory', lambda *a, **kw: {})
    events = list(p.run_pipeline('q', [], None, None, '204-review-timeout'))
    answer = next(e for e in events if e['type'] == 'answer')
    assert answer['completion_status'] == 'degraded'
    assert answer['quality_status'] == 'degraded'
    assert answer['confidence'] <= .45
    assert not any(e['type'] in {'terminated', 'error'} for e in events)


@pytest.mark.parametrize('fallback', [False, True])
def test_sequential_pipeline_specialist_uses_bounded_wait(monkeypatch, fallback):
    m = import_module('agent.core.pipeline')
    multi_agent = import_module('agent.core.multi_agent')
    from agent.state.pipeline_state import PipelineState
    from agent.infra.blackboard import Blackboard
    from agent.infra.convergence import ConvergenceDetector
    from agent.infra.termination import TerminationChecker
    release = threading.Event()
    monkeypatch.setattr(m, '_route_model', lambda *a: 'mimo')
    monkeypatch.setattr(m, 'build_specialist_context', lambda **kw: '')
    monkeypatch.setattr(m, 'get_config_int', lambda key, default: .02 if key == 'agent.specialist_timeout' else default)
    def run(**kw):
        release.wait(.3)
        return {'analysis': 'late'}
    monkeypatch.setattr(multi_agent, 'run_specialist', run)
    spec = {'agent_key': 'a', 'name': 'A'}
    plan = {'specialists': [spec]}
    if not fallback:
        plan['plan'] = {'steps': [{'agent_key': 'a', 'query': 'q'}]}
    state = PipelineState(refined_query='q')
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            list(m._phase_execution(state, plan, {}, {}, ConvergenceDetector(), Blackboard(),
                                    TerminationChecker(start_time=time.time()), threading.Event(), 't'))
        assert time.monotonic() - started < .15
    finally:
        release.set()


def test_stream_checks_budget_before_next_network_read(monkeypatch):
    m = import_module('services.llm.llm_service')
    monkeypatch.setattr(m, '_LLM_TOTAL_TIMEOUT', .02, raising=False)
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    stream = _TestStream()
    reads = []
    original_iter = type(stream).__iter__
    def iterator():
        for chunk in original_iter(stream):
            reads.append(1)
            yield chunk
    monkeypatch.setattr(type(stream), '__iter__', lambda self: iterator())
    monkeypatch.setattr(m, 'client', _llm_client(lambda **kw: stream))
    chunks = m._call_llm_stream(model=m.MODEL, messages=[])
    next(chunks)
    time.sleep(.03)
    with pytest.raises(TimeoutError):
        next(chunks)
    assert len(reads) == 1
    assert stream.closed


def test_llm_retry_backoff_respects_total_deadline(monkeypatch):
    import httpx
    from openai import APITimeoutError
    m = import_module('services.llm.llm_service')
    monkeypatch.setattr(m, '_LLM_TOTAL_TIMEOUT', .02)
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, '_fallback_client', None)
    calls = []
    def request(**kw):
        calls.append(1)
        raise APITimeoutError(request=httpx.Request('POST', 'https://example.invalid'))
    monkeypatch.setattr(m, 'client', _llm_client(request))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        m._call_llm(model=m.MODEL, messages=[])
    assert time.monotonic() - started < .15
    assert calls == [1]


def test_synthesis_checks_cancel_during_stream_and_closes_it(monkeypatch):
    m = import_module('agent.core.orchestrator')
    arbitration = import_module('agent.core.arbitration')
    llm = import_module('services.llm_service')
    from infra.execution_budget import ExecutionCancelled
    cancel = threading.Event()
    closed = []
    monkeypatch.setattr(arbitration, 'arbitrate_results', lambda *a, **kw: {})
    monkeypatch.setattr(m, '_build_final_synthesis_prompt', lambda *a, **kw: 'prompt')
    def stream(**kw):
        try:
            cancel.set()
            yield {'content': 'late'}
        finally:
            closed.append(True)
    monkeypatch.setattr(llm, '_call_llm_stream', stream)
    monkeypatch.setattr(m, '_call_llm', lambda **kw: pytest.fail('cancelled stream started fallback'))
    generator = m._stream_final_synthesis('q', 'q', [], [], [], [], '', 'complex', False,
                                           {}, {}, {}, cancel, time.time(), None, None, 't')
    try:
        with pytest.raises((ExecutionCancelled, m.CancelledError)):
            next(generator)
        assert closed == [True]
    finally:
        generator.close()


def test_specialist_timeout_does_not_wait_for_worker_exit():
    from agent.core.orchestrator import _run_specialist_with_timeout
    release = threading.Event()
    def work():
        release.wait(.3)
        return {'status':'success'}
    start=time.monotonic()
    try:
        result=_run_specialist_with_timeout(work,.02,'trace','test')
        assert time.monotonic()-start < .15
        assert result['status']=='timeout'
    finally:
        release.set()


def test_bounded_executor_limits_hung_inflight_and_cancel():
    from infra.execution_budget import BoundedExecutor, wait_result, ExecutionCancelled
    pool=BoundedExecutor(2,'test-deadline')
    release=threading.Event()
    try:
        futures=[pool.submit(lambda: release.wait(.3)) for _ in range(2)]
        with pytest.raises(TimeoutError):
            pool.submit(lambda: None)
        cancel=threading.Event()
        cancel.set()
        with pytest.raises(ExecutionCancelled):
            wait_result(futures[0],timeout=.2,cancel_event=cancel)
    finally:
        release.set()
        pool.shutdown()


def test_context_deadline_stops_worker_llm_before_request(monkeypatch):
    from infra.execution_budget import execution_budget
    llm = import_module('services.llm_service')
    monkeypatch.setattr(llm,'_enforce_token_budget',lambda *a:None)
    class Chat:
        def create(self,**kwargs):
            raise AssertionError('expired deadline must not send request')
    monkeypatch.setattr(llm,'client',SimpleNamespace(chat=SimpleNamespace(completions=Chat())))
    with execution_budget(.001):
        time.sleep(.003)
        with pytest.raises(TimeoutError):
            llm._call_llm(messages=[],model=llm.MODEL)


def test_pipeline_wait_checks_cancel_without_completed_workers():
    from concurrent.futures import Future
    from agent.core.pipeline import _completed_with_deadline, _PipelineCancelled
    cancel=threading.Event()
    cancel.set()
    with pytest.raises(_PipelineCancelled):
        list(_completed_with_deadline({Future():None},cancel,.1))


def test_pipeline_reuses_outer_rag_without_search(monkeypatch):
    from agent.core.pipeline import _phase_info_gather
    from agent.state.pipeline_state import PipelineState
    import services.rag as rag
    # 与被测函数的 from services.portfolio_context import 保持相同绑定；兼容加载器可能留下旧父包属性。
    portfolio_context = __import__('services.portfolio_context', fromlist=['build_portfolio_context'])
    from agent.memory import memory
    monkeypatch.setattr(rag,'build_rag_context_with_details',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('duplicate RAG')))
    monkeypatch.setattr(portfolio_context,'build_portfolio_context',lambda *a,**kw:'portfolio')
    monkeypatch.setattr(memory,'build_user_memory_context',lambda *a,**kw:'memory')
    result = _phase_info_gather(PipelineState(refined_query='测试'),{},[],'t',prepared_rag_context='外层证据与约束')
    assert result['rag_context']=='外层证据与约束'
