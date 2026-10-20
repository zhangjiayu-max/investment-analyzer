"""长篇分析必须续写截断内容，保留推理上下文并限制追加调用。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def response(content, reason, tokens):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason,
        message=SimpleNamespace(content=content, reasoning_content='reasoning state'))],
        usage=SimpleNamespace(total_tokens=tokens))


def completion():
    from routers.analysis import _agent_contract as module
    func = getattr(module, 'complete_text_analysis', None)
    assert callable(func), '缺少有界文本分析续写，长报告会被当作成功保存'
    return func


def test_truncated_text_continues_with_reasoning_and_actual_tokens():
    call = Mock(side_effect=[response('第一部分，', 'length', 100), response('完整结论。', 'stop', 200)])
    messages = [{'role': 'system', 'content': 'configured prompt'}, {'role': 'user', 'content': '组合上下文'}]
    text, tokens = asyncio.run(completion()(call, messages=messages, caller='panorama', model='configured model'))
    assert text == '第一部分，完整结论。'
    assert tokens == 300
    assert len(messages) == 2
    sent = call.call_args.kwargs['messages']
    assert sent[2]['content'] == '第一部分，'
    assert sent[2]['reasoning_content'] == 'reasoning state'
    assert sent[3]['role'] == 'user'


def test_repeated_truncation_has_bounded_calls_and_never_succeeds():
    call = Mock(return_value=response('仍未完', 'length', 100))
    with pytest.raises(RuntimeError, match='截断'):
        asyncio.run(completion()(call, messages=[{'role': 'user', 'content': '上下文'}]))
    assert call.call_count == 3


def test_empty_response_never_becomes_valid_analysis():
    call = Mock(return_value=response('   ', 'stop', 100))
    with pytest.raises(ValueError, match='有效分析'):
        asyncio.run(completion()(call, messages=[]))


def test_reasoning_only_length_limit_continues_until_visible_report():
    call = Mock(side_effect=[response('', 'length', 100), response('完整报告。', 'stop', 200)])
    text, tokens = asyncio.run(completion()(call, messages=[{'role': 'user', 'content': '上下文'}]))
    assert text == '完整报告。'
    assert tokens == 300
    assert call.call_args.kwargs['messages'][1]['reasoning_content'] == 'reasoning state'

def test_sync_completion_continues_after_length_and_keeps_reasoning(monkeypatch):
    from services.llm.text_completion import complete_text_analysis_sync
    call = Mock(side_effect=[response('第一段', 'length', 10), response('第二段', 'stop', 20)])
    text, tokens, status = complete_text_analysis_sync(call, messages=[{'role': 'user', 'content': '问题'}], caller='pipeline')
    assert (text, tokens, status) == ('第一段第二段', 30, 'completed')
    sent = call.call_args.kwargs['messages']
    assert sent[-2]['reasoning_content'] == 'reasoning state'


def test_sync_completion_reports_truncated_failure_after_bound(monkeypatch):
    from services.llm.text_completion import complete_text_analysis_sync
    call = Mock(return_value=response('半截', 'length', 10))
    text, tokens, status = complete_text_analysis_sync(call, messages=[], max_continuations=1, caller='pipeline')
    assert text == '半截半截'
    assert tokens == 20
    assert status == 'truncated'
