"""检索排序、索引来源、记忆与评估归属的确定性回归。"""
import json
import pytest
from db._conn import _get_conn
from db.conversations import create_conversation, create_message, save_conversation_summary, get_conversation_summary


def test_vector_distance_rank_prefers_near(monkeypatch):
    from services.rag import rag
    class Encoding:
        def tolist(self): return [[1.0]]
    class Model:
        def encode(self,*a,**kw): return Encoding()
    class Collection:
        def query(self,**kw):
            return {'ids':[['near','far']], 'metadatas':[[{'reference_id':'1'},{'reference_id':'2'}]],
                    'documents':[['near','far']], 'distances':[[.1,.7]]}
    monkeypatch.setattr(rag,'_get_chroma',lambda:Collection())
    monkeypatch.setattr(rag,'_get_embed_model',lambda:Model())
    result,_ = rag.search_chroma('相似')
    assert sorted(result,key=lambda r:r['rank'])[0]['body'] == 'near'


def test_cleanup_preserves_non_knowledge_sources():
    from services.rag import rag
    from db.knowledge import cleanup_orphan_fts_records
    rag.init_fts()
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO author_articles (id,title,url) VALUES (100,'valid','https://example.test')")
        conn.execute("INSERT INTO knowledge_fts(content_type,title,body,reference_id) VALUES ('author_article','valid','内容','100')")
        conn.execute("INSERT INTO knowledge_fts(content_type,title,body,reference_id) VALUES ('book','orphan','内容','999')")
        conn.commit()
    finally:
        conn.close()
    result=cleanup_orphan_fts_records()
    assert result['cleaned']==1
    conn=_get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_fts WHERE content_type='author_article'").fetchone()[0]==1
    finally:
        conn.close()


def test_summary_cursor_is_real_last_message_id(monkeypatch):
    from agent.memory import memory
    conv = create_conversation('a')
    other = create_conversation('b')
    ids=[]
    for i in range(4):
        ids.append(create_message(conv,'user',str(i)))
        create_message(other,'user','gap')
    monkeypatch.setattr(memory,'_build_structured_summary',lambda *a:'summary')
    assert memory.update_conversation_summary(conv)
    assert get_conversation_summary(conv)['up_to_message_id'] == ids[-1]


def test_legacy_summary_invalidated():
    conv=create_conversation('a')
    create_message(conv,'user','one')
    conn=_get_conn()
    try:
        conn.execute('INSERT INTO conversation_summaries (conversation_id,up_to_message_id,summary) VALUES (?,?,?)',(conv,1,'legacy'))
        conn.commit()
    finally:
        conn.close()
    assert get_conversation_summary(conv) is None


def test_evaluator_uses_requested_message_and_only_its_runs(monkeypatch):
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    conv=create_conversation('a')
    first=create_message(conv,'assistant','旧答案',json.dumps({'duration_ms':2000}))
    create_message(conv,'assistant','新答案',json.dumps({'duration_ms':99999}))
    from db.agents import create_agent_run
    create_agent_run(conv,first,'a','a','q','answer',duration_ms=1000)
    create_agent_run(conv,first+1,'b','b','q','answer',duration_ms=1000)
    evaluator=ConversationQualityEvaluator()
    captured=[]
    real=evaluator._evaluate_execution
    def execution(metadata,runs):
        captured.append((metadata,runs))
        return real(metadata,runs)
    monkeypatch.setattr(evaluator,'_evaluate_execution',execution)
    result=evaluator.evaluate(conv,first,trigger_evolution=False)
    assert result.metadata['duration_ms']==2000
    assert len(captured[0][1])==1
    assert captured[0][1][0]['message_id']==first


def test_parallelism_uses_real_intervals():
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    runs=[{'agent_key':'a','started_at':'2026-01-01 00:00:00','completed_at':'2026-01-01 00:00:02'},
          {'agent_key':'b','started_at':'2026-01-01 00:00:00','completed_at':'2026-01-01 00:00:03'}]
    result=ConversationQualityEvaluator()._evaluate_execution({'duration_ms':3000},runs)
    assert result.metrics['parallelism'] == pytest.approx(.4)


def test_cancelled_message_cannot_be_overwritten_by_completion():
    from db.conversations import update_message_content_and_metadata
    conv=create_conversation('a')
    msg=create_message(conv,'assistant','已取消',json.dumps({'execution_status':'cancelled'}))
    assert update_message_content_and_metadata(msg,'迟到的结果',{'execution_status':'completed'}) is False
    conn=_get_conn()
    try:
        row=conn.execute('SELECT content,metadata FROM messages WHERE id=?',(msg,)).fetchone()
        assert row['content']=='已取消'
        assert json.loads(row['metadata'])['execution_status']=='cancelled'
    finally:
        conn.close()


def test_delete_linked_document_removes_chunks_and_search_index(monkeypatch):
    from db.articles import delete_linked_article
    from services.rag import rag
    rag.init_fts()
    deleted=[]
    import services.rag as public_rag
    monkeypatch.setattr(public_rag,'delete_chroma_by_filter',lambda kind,**kw:deleted.append((kind,kw)))
    conn=_get_conn()
    try:
        cur=conn.execute("INSERT INTO linked_articles(title,url) VALUES ('doc','local:test')")
        doc=cur.lastrowid
        conn.execute("INSERT INTO document_chunks(article_id,chunk_index,content,char_count) VALUES (?,0,'内容',2)",(doc,))
        conn.execute("INSERT INTO knowledge_fts(content_type,title,body,reference_id) VALUES ('linked_doc','doc','内容',?)",(str(doc),))
        conn.commit()
    finally:
        conn.close()
    assert delete_linked_article(doc)
    conn=_get_conn()
    try:
        assert conn.execute('SELECT COUNT(*) FROM document_chunks WHERE article_id=?',(doc,)).fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_fts WHERE content_type='linked_doc' AND reference_id=?",(str(doc),)).fetchone()[0]==0
    finally:
        conn.close()
    assert deleted==[('linked_doc',{'reference_id':str(doc)})]


def test_vector_orphans_excluded_even_when_external_delete_failed():
    from db.index_sources import filter_existing_sources
    assert filter_existing_sources([{'content_type':'linked_doc','reference_id':'404','body':'stale'}]) == []


def test_deleted_article_distillation_never_returned_from_fts(monkeypatch):
    from db.articles import delete_author_article
    from services.rag import rag
    import services.rag as public_rag
    monkeypatch.setattr(public_rag,'delete_chroma_by_filter',lambda *a,**kw:None)
    rag.init_fts()
    conn=_get_conn()
    try:
        conn.execute("INSERT INTO author_articles(id,title,url) VALUES (90001,'old','https://example.test/old')")
        conn.execute("INSERT INTO knowledge_fts(content_type,title,body,reference_id) VALUES ('skill','old','TOKENALPHA','90001')")
        conn.commit()
    finally:
        conn.close()
    assert delete_author_article(90001)
    results,_=rag.search_knowledge('TOKENALPHA',content_type='skill')
    assert results == []


def test_cancelled_message_rejects_late_progress_and_status():
    from db.conversations import update_message_metadata,mark_message_execution_status
    conv=create_conversation('cancel')
    msg=create_message(conv,'assistant','取消',json.dumps({'execution_status':'cancelled'}))
    update_message_metadata(msg,{'execution_status':'streaming','progress':90})
    assert mark_message_execution_status(msg,'completed') is False
    conn=_get_conn()
    try:
        metadata=json.loads(conn.execute('SELECT metadata FROM messages WHERE id=?',(msg,)).fetchone()[0])
        assert metadata['execution_status']=='cancelled'
    finally:
        conn.close()


def test_llm_evaluation_uses_question_before_target_even_outside_recent_window(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from agent.eval.conversation_evaluator import evaluate_with_llm
    from services.llm import llm_service
    conv=create_conversation('evaluate')
    create_message(conv,'user','FIRST_WRONG_QUERY')
    create_message(conv,'assistant','first')
    create_message(conv,'user','TARGET_CORRECT_QUERY')
    target=create_message(conv,'assistant','TARGET_ANSWER')
    for i in range(60):
        create_message(conv,'user',f'future {i}')
    captured=[]
    def call(**kwargs):
        captured.append(kwargs['messages'][-1]['content'])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"total_score":80}'))])
    monkeypatch.setattr(llm_service,'_call_llm',call)
    asyncio.run(evaluate_with_llm(conv,target))
    assert len(captured)==1
    assert 'TARGET_CORRECT_QUERY' in captured[0]
    assert 'FIRST_WRONG_QUERY' not in captured[0]


def test_cancelled_channels_and_messages_are_one_way_terminal():
    from db.conversations import mark_conversation_cancelled,mark_message_execution_status,get_messages
    from db.stream_channels import create_channel,cancel_channel,complete_channel,fail_channel,mark_aborted,get_channel,append_event,list_events
    conv=create_conversation('cancel active')
    msg=create_message(conv,'assistant','running',json.dumps({'execution_status':'resuming'}))
    channel=create_channel(conv,msg)
    mark_conversation_cancelled(conv)
    assert json.loads(get_messages(conv)[0]['metadata'])['execution_status']=='cancelled'
    assert get_channel(channel)['status']=='cancelled'
    cancel_channel(channel)
    complete_channel(channel)
    fail_channel(channel,'late')
    mark_aborted(channel,'late')
    append_event(channel,'answer',{'content':'late'})
    assert get_channel(channel)['status']=='cancelled'
    assert list_events(channel)==[]
    assert not mark_message_execution_status(msg,'streaming')


def test_cancelled_retry_uses_new_message_and_no_in_place_llm(monkeypatch):
    import asyncio
    from routers.conversation.conversations import retry_conversation_message_api,resume_synthesis_api
    from db.agents import create_agent_run
    from services import conv_recovery
    conv=create_conversation('retry')
    create_message(conv,'user','original question')
    msg=create_message(conv,'assistant','cancelled',json.dumps({'execution_status':'cancelled'}))
    create_agent_run(conv,msg,'a','a','q','prior result')
    monkeypatch.setattr(conv_recovery,'resume_synthesis_only',lambda *a: (_ for _ in ()).throw(AssertionError('do not synthesize cancelled attempt')))
    result=asyncio.run(retry_conversation_message_api(conv,msg))
    assert result['message_id']!=msg
    assert result['original_query']=='original question'
    with pytest.raises(Exception) as error:
        asyncio.run(resume_synthesis_api(conv,msg))
    assert getattr(error.value,'status_code',None)==409


def test_parallelism_reads_real_agent_run_completed_at():
    from db.agents import create_pending_agent_run,update_agent_run_status
    from agent.eval.conversation_evaluator import ConversationQualityEvaluator
    conv=create_conversation('intervals')
    msg=create_message(conv,'assistant','answer')
    ids=[]
    for key in ['a','b']:
        run=create_pending_agent_run(conv,msg,key,key)
        update_agent_run_status(run,'running')
        update_agent_run_status(run,'completed')
        ids.append(run)
    conn=_get_conn()
    try:
        # 给真实写入的记录设置固定时段，避免依赖等待整秒。
        for run,seconds in zip(ids,[2,3]):
            conn.execute("UPDATE agent_runs SET started_at='2026-01-01 00:00:00',completed_at=? WHERE id=?",
                         (f'2026-01-01 00:00:0{seconds}',run))
        conn.commit()
        runs=[dict(r) for r in conn.execute('SELECT * FROM agent_runs WHERE message_id=?',(msg,))]
    finally:
        conn.close()
    result=ConversationQualityEvaluator()._evaluate_execution({'duration_ms':3000},runs)
    assert result.metrics['parallelism']==pytest.approx(.4)


def test_cancelled_attempt_does_not_cancel_next_question_in_same_conversation():
    from routers.conversation.conversations import _begin_send_attempt,_release_conv_send_lock,_fast_path_cancelled
    from db.conversations import mark_conversation_cancelled,get_conversation_cancel_status,update_message_content_and_metadata
    conv=create_conversation('new attempt')
    old=create_message(conv,'assistant','old',json.dumps({'execution_status':'streaming'}))
    mark_conversation_cancelled(conv)
    event=_begin_send_attempt(conv)
    try:
        new=create_message(conv,'assistant','new',json.dumps({'execution_status':'streaming'}))
        assert not get_conversation_cancel_status(conv)
        assert not _fast_path_cancelled(conv,new,event)
        assert _fast_path_cancelled(conv,old,event)
        assert not update_message_content_and_metadata(old,'late',{'execution_status':'completed'})
        assert update_message_content_and_metadata(new,'answer B',{'execution_status':'completed'})
    finally:
        _release_conv_send_lock(conv)


def test_partial_progress_keeps_status_so_cancel_still_guards_late_answer():
    from db.conversations import update_message_metadata,mark_conversation_cancelled,update_message_content_and_metadata,get_messages
    conv=create_conversation('progress')
    msg=create_message(conv,'assistant','working',json.dumps({'execution_status':'streaming','trace_id':'t'}))
    update_message_metadata(msg,{'refined_query':'rewritten'})
    metadata=json.loads(get_messages(conv)[0]['metadata'])
    assert metadata['execution_status']=='streaming'
    assert metadata['trace_id']=='t'
    mark_conversation_cancelled(conv)
    assert not update_message_content_and_metadata(msg,'late',{'execution_status':'completed'})


def test_cancelled_agent_run_rejects_late_worker_completion():
    from db.agents import create_pending_agent_run,update_agent_run_status
    from db.conversations import mark_conversation_cancelled
    conv=create_conversation('worker cancelled')
    msg=create_message(conv,'assistant','working',json.dumps({'execution_status':'streaming'}))
    run=create_pending_agent_run(conv,msg,'a','a')
    update_agent_run_status(run,'running')
    mark_conversation_cancelled(conv)
    update_agent_run_status(run,'completed',result='late')
    conn=_get_conn()
    try:
        row=conn.execute('SELECT status,result FROM agent_runs WHERE id=?',(run,)).fetchone()
        assert row['status']=='cancelled'
        assert row['result']!='late'
    finally:
        conn.close()
