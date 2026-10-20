"""本机代理故障不能影响 MIMO；模型连接失败不能触发第二轮编排。"""
import importlib
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError, OpenAI


@pytest.fixture
def model_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, body))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
            self.end_headers()
            if body.get('stream'):
                chunk = {'id': 'direct-test', 'object': 'chat.completion.chunk', 'created': 0,
                         'model': 'mimo-test', 'choices': [{'index': 0, 'finish_reason': None,
                             'delta': {'content': 'direct stream works'}}]}
                self.wfile.write(f'data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n'.encode())
                return
            self.wfile.write(json.dumps({
                'id': 'direct-test', 'object': 'chat.completion', 'created': 0,
                'model': 'mimo-test', 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': 'direct connection works'}}],
            }).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.fixture(params=['environment', 'macos_system'])
def broken_proxy(monkeypatch, request):
    # 使用仍绑定但不监听的端口，确定性制造 connection refused。
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        proxy = f'http://127.0.0.1:{sock.getsockname()[1]}'
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            monkeypatch.delenv(key, raising=False)
        if request.param == 'environment':
            monkeypatch.setenv('HTTP_PROXY', proxy)
            monkeypatch.setenv('HTTPS_PROXY', proxy)
        else:
            monkeypatch.setattr(httpx._utils, 'getproxies', lambda: {'http': proxy, 'https': proxy})
        yield


def test_text_clients_ignore_broken_proxy(monkeypatch, model_server, broken_proxy):
    import config
    from services.llm import llm_service
    url, requests = model_server
    monkeypatch.setattr(config, 'get_llm_config', lambda: ('test-key', url, 'mimo-test'))
    monkeypatch.setattr(config, 'get_llm_fallback_config', lambda: ('fallback-key', url, 'mimo-test'))
    # 独立载入，验证真实模块初始化，又不替换其他测试使用的全局客户端。
    spec = importlib.util.spec_from_file_location('_direct_llm_test', Path(llm_service.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        for client in (module.client, module._fallback_client, module._arbitration_client):
            response = client.chat.completions.create(
                model='mimo-test', messages=[{'role': 'user', 'content': 'test'}], timeout=.5)
            assert response.choices[0].message.content == 'direct connection works'
        with module.client.chat.completions.create(
                model='mimo-test', messages=[{'role': 'user', 'content': 'test'}],
                stream=True, timeout=.5) as stream:
            assert ''.join(chunk.choices[0].delta.content or '' for chunk in stream) == 'direct stream works'
        assert len(requests) == 4
        assert all(path == '/v1/chat/completions' for path, _ in requests)
    finally:
        module.client.close()
        module._fallback_client.close()


def test_vision_ignores_broken_proxy(monkeypatch, model_server, broken_proxy):
    from services.content import image_parser
    clients = []

    def client(**kwargs):
        instance = OpenAI(**dict(kwargs, max_retries=0, timeout=.5))
        clients.append(instance)
        return instance

    monkeypatch.setattr(image_parser, 'OpenAI', client)
    url, requests = model_server
    try:
        assert image_parser._openai_chat('table', 'image', 'png', 'mimo-test', url, 'test-key') == 'direct connection works'
        assert len(requests) == 1
    finally:
        for instance in clients:
            instance.close()


@pytest.mark.parametrize('mode', ['parallel', 'sequential', 'fallback'])
@pytest.mark.parametrize('resume', [False, True])
def test_all_specialists_disconnected_is_terminal(monkeypatch, mode, resume):
    p = importlib.import_module('agent.core.pipeline')
    multi_agent = importlib.import_module('agent.core.multi_agent')
    orchestrator = importlib.import_module('agent.core.orchestrator')
    specs = [{'agent_key': key, 'name': key, 'system_prompt': 'configured'} for key in ('a', 'b')]
    steps = [{'agent_key': s['agent_key']} for s in specs]
    plan = {'specialists': specs, 'plan': None if mode == 'fallback' else {'steps': steps}}
    monkeypatch.setattr(p, '_phase_preprocess', lambda *a: {'complexity': 'medium', 'query_info': {}})
    monkeypatch.setattr(p, 'is_simple_chat', lambda *a: False)
    monkeypatch.setattr(p, 'needs_clarification', lambda *a: (False, ''))
    monkeypatch.setattr(p, '_phase_info_gather', lambda *a, **kw: {})
    monkeypatch.setattr(p, '_phase_planning', lambda *a: plan)
    monkeypatch.setattr(p, 'get_config_bool', lambda key, default: key == 'pipeline.parallel_execution' and mode == 'parallel')
    monkeypatch.setattr(p, 'build_specialist_context', lambda **kw: 'prepared')
    builder = importlib.import_module('agent.core.context_builder')
    monkeypatch.setattr(builder, 'build_specialist_context', lambda **kw: 'prepared')
    monkeypatch.setattr(p, '_route_model', lambda *a: 'mimo-test')
    monkeypatch.setattr(p, '_phase_synthesis', lambda *a, **kw: pytest.fail('disconnected model reached synthesis'))
    calls = []

    def disconnected(**kw):
        calls.append(kw['agent_key'])
        raise APIConnectionError(request=httpx.Request('POST', 'https://example.test/v1/chat/completions'))

    monkeypatch.setattr(multi_agent, 'run_specialist', disconnected)
    if resume:
        rewriter = importlib.import_module('agent.query.query_rewriter')
        monkeypatch.setattr(rewriter, 'fuse_clarified_query', lambda *a: 'q')
        from agent.state.pipeline_state import PipelineState, PipelinePhase
        checkpoint = PipelineState(original_query='q', refined_query='q', phase=PipelinePhase.PREPROCESS).to_dict()
        checkpoint['query_info'] = {}
        events = list(p.run_pipeline_from_checkpoint(checkpoint, 'answer', [], '205-test'))
    else:
        monkeypatch.setattr(p, 'is_pipeline_enabled', lambda: True)
        monkeypatch.setattr(orchestrator, '_stream_precheck', lambda *a, **kw: pytest.fail('connection failure restarted ReAct'))
        events = list(orchestrator.orchestrate_stream('q', [], rag_context='prepared'))
    assert calls
    assert events[-1]['type'] == 'error'
    assert '连接' in (events[-1].get('reason') or events[-1].get('message', ''))
    assert not any(e['type'] in {'answer', 'degrade'} for e in events)


@pytest.mark.parametrize('mode', ['parallel', 'sequential', 'fallback'])
def test_connection_failure_keeps_other_successful_specialists(monkeypatch, mode):
    p = importlib.import_module('agent.core.pipeline')
    multi_agent = importlib.import_module('agent.core.multi_agent')
    from agent.state.pipeline_state import PipelineState
    from agent.infra.blackboard import Blackboard
    from agent.infra.convergence import ConvergenceDetector
    from agent.infra.termination import TerminationChecker
    specs = [{'agent_key': key, 'name': key, 'system_prompt': 'configured'} for key in ('a', 'b')]
    plan = {'specialists': specs, 'plan': None if mode == 'fallback' else {
        'steps': [{'agent_key': s['agent_key']} for s in specs]}}
    monkeypatch.setattr(p, 'get_config_bool', lambda key, default: key == 'pipeline.parallel_execution' and mode == 'parallel')
    monkeypatch.setattr(p, 'build_specialist_context', lambda **kw: 'prepared')
    builder = importlib.import_module('agent.core.context_builder')
    monkeypatch.setattr(builder, 'build_specialist_context', lambda **kw: 'prepared')
    monkeypatch.setattr(p, '_route_model', lambda *a: 'mimo-test')

    def specialist(**kw):
        if kw['agent_key'] == 'a':
            raise APIConnectionError(request=httpx.Request('POST', 'https://example.test'))
        return {'agent_key': 'b', 'agent': 'b', 'analysis': 'available report',
                'status': 'success', 'tool_calls': []}

    monkeypatch.setattr(multi_agent, 'run_specialist', specialist)
    generator = p._phase_execution(PipelineState(refined_query='q', token_budget={'max_total': 30000}), plan, {}, {},
        ConvergenceDetector(), Blackboard(), TerminationChecker(), threading.Event(), '205-partial')
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as done:
            result = done.value
            break
    assert [s['agent_key'] for s in result['specialists']] == ['b']
    assert result['specialists'][0]['analysis'] == 'available report'
    assert any(e.get('status') == 'failed' for e in events)
