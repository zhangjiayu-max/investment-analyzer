"""服务协议/追踪/边界在隔离应用中验收，不启动生产生命周期。"""
import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.middleware import ResponseWrapperMiddleware
from infra.request_tracing import RequestTracingMiddleware, get_request_id


def test_openapi_preserves_standard_schema():
    app = FastAPI()
    app.add_middleware(ResponseWrapperMiddleware)
    @app.get('/api/example')
    def example():
        return {'value':42}
    client = TestClient(app)
    assert client.get('/openapi.json').json()['openapi'].startswith('3.')
    assert client.get('/api/example').json()['data'] == {'value':42}


def test_concurrent_request_context_and_thread_propagation():
    async def run():
        arrived = asyncio.Event()
        seen = []
        async def app(scope, receive, send):
            before = get_request_id()
            if scope['path'] == '/a':
                await arrived.wait()
            else:
                arrived.set()
                await asyncio.sleep(.01)
            thread_id = await asyncio.to_thread(get_request_id)
            seen.append((before, get_request_id(), thread_id))
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b''})
        middleware = RequestTracingMiddleware(app)
        async def receive():
            return {'type':'http.request','body':b''}
        async def send(message):
            pass
        await asyncio.gather(*(middleware({'type':'http','path':path},receive,send) for path in ['/a','/b']))
        assert len({row[0] for row in seen}) == 2
        assert all(len(set(row))==1 for row in seen)
        assert get_request_id() == 'unknown'
    asyncio.run(run())


def test_image_proxy_rejects_untrusted_redirect_targets():
    from infra.image_proxy import validate_image_url
    assert validate_image_url('https://mmbiz.qpic.cn/mmbiz_png/a/0?wx_fmt=png')
    for url in ['http://127.0.0.1/x','https://mmbiz.qpic.cn.evil.test/x',
                'file:///etc/passwd','https://user@mmbiz.qpic.cn/x','https://mmbiz.qpic.cn:444/x']:
        with pytest.raises(ValueError):
            validate_image_url(url)


def test_optional_api_auth_rejects_missing_credentials(monkeypatch):
    from infra.api_auth import ApiAuthMiddleware
    app = FastAPI()
    app.add_middleware(ApiAuthMiddleware, token='test-secret')
    @app.get('/api/private')
    def private():
        return {'ok':True}
    client = TestClient(app)
    assert client.get('/api/private').status_code == 401
    assert client.get('/api/private', headers={'Authorization':'Bearer test-secret'}).status_code == 200


def test_blocking_jobs_keep_loop_responsive_and_propagate_context():
    import time
    from infra.blocking import run_blocking
    from infra.request_tracing import set_request_id
    async def run():
        set_request_id('worker-test')
        task = asyncio.create_task(run_blocking(lambda: (time.sleep(.08),get_request_id())[1]))
        await asyncio.sleep(.01)
        assert not task.done()
        assert await task == 'worker-test'
    asyncio.run(run())


def test_readiness_core_and_optional_dependency():
    from infra.readiness import check_readiness
    from db._conn import _get_conn
    report = check_readiness()
    assert report['ready'] is True
    assert report['dependencies']['vector_search']['required'] is False
    conn = _get_conn()
    try:
        conn.execute('DROP TABLE conversations')
        conn.commit()
    finally:
        conn.close()
    assert check_readiness()['ready'] is False


def test_sse_passes_frames_and_cookies_unchanged():
    from fastapi.responses import StreamingResponse
    app = FastAPI()
    app.add_middleware(ResponseWrapperMiddleware)
    @app.get('/api/events')
    def events():
        async def stream():
            yield b'data: first\n\n'
            yield b'data: done\n\n'
        response = StreamingResponse(stream(), media_type='text/event-stream')
        response.set_cookie('a','1')
        response.set_cookie('b','2')
        return response
    response = TestClient(app).get('/api/events')
    assert response.content == b'data: first\n\ndata: done\n\n'
    assert len(response.headers.get_list('set-cookie')) == 2


def test_obsidian_resolves_real_script_and_checks_exit(monkeypatch):
    from routers.knowledge import knowledge
    from pathlib import Path
    calls = []
    async def fake_run(*args, **kwargs):
        calls.append(args)
        return b'', b'parse failed', 1
    monkeypatch.setattr(knowledge, '_run_obsidian', fake_run, raising=False)
    async def run():
        with pytest.raises(Exception) as error:
            await knowledge.obsidian_status()
        assert getattr(error.value, 'status_code', None) == 502
    asyncio.run(run())
    assert knowledge.OBSIDIAN_SCRIPT == Path(__file__).resolve().parents[1] / 'scripts' / 'sync_obsidian.py'


def test_production_middleware_envelopes_compressed_json_without_losing_cookies():
    from app import app as production_app
    app=FastAPI()
    app.user_middleware=list(production_app.user_middleware)
    @app.get('/api/large')
    def large():
        from fastapi.responses import JSONResponse
        response=JSONResponse({'value':'x'*800})
        response.set_cookie('first','1')
        response.set_cookie('second','2')
        return response
    client=TestClient(app)
    plain=client.get('/api/large',headers={'Accept-Encoding':'identity'})
    compressed=client.get('/api/large',headers={'Accept-Encoding':'gzip'})
    assert compressed.json()['data']==plain.json()['data']=={'value':'x'*800}
    assert compressed.headers['content-encoding']=='gzip'
    assert len(compressed.headers.get_list('set-cookie'))==2


@pytest.mark.parametrize('operation',['preview','embedding'])
def test_document_processing_does_not_block_event_loop(monkeypatch,tmp_path,operation):
    import time
    from pathlib import Path
    from routers.knowledge import articles
    document=tmp_path/'note.txt'
    document.write_text('测试内容')
    monkeypatch.setattr(articles,'UPLOADS_DIR',tmp_path)
    monkeypatch.setattr(articles,'get_linked_article',lambda _: {'file_path':'note.txt','file_type':'txt','title':'note'})
    monkeypatch.setattr(articles,'update_linked_article_embed_status',lambda *a:None)
    monkeypatch.setattr(articles,'save_document_chunks',lambda *a:None)
    original=Path.read_text
    def slow_read(path,*args,**kwargs):
        time.sleep(.08)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',slow_read)
    monkeypatch.setattr(articles,'index_to_chroma',lambda *a,**kw:1)
    async def run():
        job=articles.get_document_content(1) if operation=='preview' else articles.embed_document(1)
        task=asyncio.create_task(job)
        await asyncio.sleep(.01)
        assert not task.done(), 'document parsing blocked the shared event loop'
        await task
    asyncio.run(run())
