"""对话203专家输出回归：全程离线，不初始化持仓或生产数据库。"""
import copy
import importlib
import json
import socket
from types import SimpleNamespace

import pytest


VALID = '基于当前提供的持仓数据，单一标的集中度需要结合总资产口径核对。缺少久期与相关性数据，因此只能确认结构风险，不能量化回撤，也不能据此直接调整风险预算。' * 4
XML = '<tool_call_36a8><function=query_valuation><parameter=index_name>沪深300</parameter></function></tool_call_36a8>'


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    import db._conn as conn
    import db.config as config
    monkeypatch.setattr(conn, 'DB_PATH', tmp_path / 'unused.db')
    monkeypatch.setattr(config, 'get_config', lambda key, default='': default)
    for name in ('get_config_bool', 'get_config_int', 'get_config_float'):
        monkeypatch.setattr(config, name, lambda key, default: default)
    attempts = []
    def no_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('专家输出单元测试禁止网络')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', no_network)
    yield
    assert not attempts


@pytest.fixture
def core(monkeypatch):
    core = importlib.import_module('agent.core.multi_agent')
    for name in ('get_config_bool', 'get_config_int', 'get_config_float'):
        monkeypatch.setattr(core, name, lambda key, default: default)
    monkeypatch.setattr(core, 'load_specialist_agents', lambda: {'risk': {
        'name': '风险管理师', 'icon': 'R', 'tools': [], 'system_prompt': '已配置的风险分析提示'}})
    monkeypatch.setattr(core, '_parse_structured_output', lambda *args: {})
    monkeypatch.setattr(core, '_estimate_specialist_tokens', lambda *args: 100)
    reflection = importlib.import_module('agent.infra.self_reflection')
    monkeypatch.setattr(reflection, 'is_self_reflection_enabled', lambda: False)
    return core


def response(content, reason='stop', reasoning=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason,
        message=SimpleNamespace(content=content, tool_calls=None,
                                model_extra={'reasoning_content': reasoning} if reasoning else {}))],
                           usage=SimpleNamespace(total_tokens=42))


def run(core):
    return core.run_specialist('risk', '结合我的持仓分析风险', from_pipeline=True,
                               prebuilt_context='预构建的持仓与KYC数据')


def test_id_tool_call_is_detected_parsed_and_removed(core):
    assert core._has_raw_toolcall_tags(XML)
    assert core._extract_text_tool_calls(XML) == [
        {'name': 'query_valuation', 'arguments': {'index_name': '沪深300'}}]
    assert core._clear_raw_toolcall_tags('分析正文。' + XML) == '分析正文。'


@pytest.mark.parametrize('broken', [XML, XML[:-20], '<function=query_valuation>',
                                   '<invoke name="query_valuation">',
                                   '<｜｜DSML｜｜invoke name="query_valuation">'])
def test_malformed_tool_markup_is_not_prose(core, broken):
    assert core._has_raw_toolcall_tags(broken)
    assert '<' not in core._clear_raw_toolcall_tags('分析正文。' + broken)


def test_two_nonempty_or_numeric_tools_do_not_prove_convergence(core):
    tools = [{'result_preview': '持仓数据已获取'}, {'result_preview': 'PE: 20.5 低估'}]
    assert not core._check_react_convergence(tools, [])
    assert not core._check_react_convergence([{'result_preview': '{"confidence": 0.9}'}], [])
    assert core._check_react_convergence(tools, [{'role': 'assistant', 'content': VALID}])


def test_structured_tool_evidence_stays_complete_during_history_compression(core):
    evidence = json.dumps({'padding': 'x' * 1800, 'temperature': 93,
                           'date': '2026-10-16', 'level': '偏热'}, ensure_ascii=False)
    messages = [{'role': 'tool', 'tool_call_id': 'a', 'content': evidence},
                {'role': 'tool', 'tool_call_id': 'b', 'content': '{}'}]
    core._compress_prior_tool_messages(messages)
    assert json.loads(messages[0]['content']) == json.loads(evidence)


@pytest.mark.parametrize('retry, reason', [(XML, 'stop'), (VALID + '（来源：知识库', 'stop'),
                                         (VALID, 'length')])
def test_invalid_reflection_retry_cannot_replace_valid_draft(core, monkeypatch, retry, reason):
    reflection = importlib.import_module('agent.infra.self_reflection')
    monkeypatch.setattr(reflection, 'is_self_reflection_enabled', lambda: True)
    monkeypatch.setattr(reflection, 'evaluate_analysis', lambda **kwargs: {
        'need_retry': True, 'gaps': ['补充来源']})
    monkeypatch.setattr(reflection, 'get_max_retry', lambda: 1)
    calls = []
    def llm(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        return response(VALID, reasoning='初稿推理') if len(calls) == 1 else response(retry, reason)
    monkeypatch.setattr(core, '_call_llm', llm)
    result = run(core)
    assert result['analysis'] == VALID
    assert result['self_reflection']['gaps_resolved'] is False
    assert result['status'] == 'degraded'
    assert calls[1]['messages'][-2]['reasoning_content'] == '初稿推理'


def test_length_stopped_analysis_is_continued_with_mimo_reasoning(core, monkeypatch):
    calls = []
    def llm(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        return response(VALID + '（来源：', 'length', '需要补齐引用') if len(calls) == 1 else response('工具持仓数据）。')
    monkeypatch.setattr(core, '_call_llm', llm)
    result = run(core)
    assert result['analysis'] == VALID + '（来源：工具持仓数据）。'
    assert result['status'] == 'success'
    assert len(calls) == 2
    assert calls[1]['messages'][-2]['reasoning_content'] == '需要补齐引用'
    assert 'tools' not in calls[1]


def test_never_finished_analysis_is_not_success(core, monkeypatch):
    monkeypatch.setattr(core, '_call_llm', lambda **kwargs: response(VALID + '（来源：知识库', 'length'))
    result = run(core)
    assert result['status'] in {'failed', 'degraded'}
    assert result['completion_status'] == 'truncated'


def test_invalid_closure_cannot_be_marked_success(core, monkeypatch):
    calls = []
    def llm(**kwargs):
        calls.append(kwargs)
        return response('短答' if len(calls) == 1 else XML * 4)
    monkeypatch.setattr(core, '_call_llm', llm)
    result = run(core)
    assert result['status'] == 'failed'
    assert '<tool_call' not in result['analysis']


@pytest.mark.parametrize('analysis, extra', [(XML * 4, {}), (VALID + '（来源：知识库', {}),
    (VALID, {'completion_status': 'truncated'}), (VALID, {'status': 'degraded'}),
    ('{"tool_evidence": "' + '数据' * 100 + '"}', {})])
def test_shared_validation_rejects_invalid_specialist_result(analysis, extra):
    quality = importlib.import_module('agent.infra.output_quality')
    result = quality.validate_specialist_output({'status': 'success', 'analysis': analysis, **extra})
    assert result['valid'] is False
    assert result['issues']


def test_shared_validation_accepts_complete_prose_without_metadata():
    quality = importlib.import_module('agent.infra.output_quality')
    assert quality.is_valid_analysis(VALID)
    assert quality.validate_specialist_output({'status': 'success', 'analysis': VALID})['valid']


def test_full_json_reaches_specialist_and_broadcast_after_native_tool(core, monkeypatch):
    evidence = json.dumps({'padding': 'x' * 2000, 'temperature': 93, 'date': '2026-10-16'})
    tool = SimpleNamespace(id='tool-1', function=SimpleNamespace(name='get_bond_temperature', arguments='{}'))
    calls = []
    def llm(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) == 1:
            result = response(None, reasoning='先核对原始债市指标')
            result.choices[0].message.tool_calls = [tool]
            return result
        return response(VALID)
    monkeypatch.setattr(core, '_call_llm', llm)
    monkeypatch.setattr(core, 'execute_tool', lambda *args, **kwargs: evidence)
    result = run(core)
    tool_message = next(message for message in calls[1]['messages'] if message['role'] == 'tool')
    assert json.loads(tool_message['content'])['temperature'] == 93
    assert tool_message['content'] == evidence
    assert result['tool_calls'][0]['result'] == evidence
    assert calls[1]['messages'][2]['reasoning_content'] == '先核对原始债市指标'


def test_tool_data_fallback_is_failed_even_when_long_enough(core, monkeypatch):
    evidence = json.dumps({'positions': '持仓数据' * 100})
    tool = SimpleNamespace(id='tool-1', function=SimpleNamespace(name='query_portfolio', arguments='{}'))
    def llm(**kwargs):
        if kwargs['caller'].endswith('#summary'):
            return response('短答')
        result = response(None)
        result.choices[0].message.tool_calls = [tool]
        return result
    monkeypatch.setattr(core, '_call_llm', llm)
    monkeypatch.setattr(core, 'execute_tool', lambda *args, **kwargs: evidence)
    result = run(core)
    assert result['status'] == 'failed'
    assert result['output_source'] == 'tool_fallback'


def test_failed_specialist_does_not_export_executable_signals(core, monkeypatch):
    monkeypatch.setattr(core, '_call_llm', lambda **kwargs: response(VALID + '（来源：知识库', 'length'))
    monkeypatch.setattr(core, '_parse_structured_output', lambda *args: {
        'verdict': 'buy', 'confidence': 0.9, 'action_signals': [{'type': 'BUY', 'target': '014846'}]})
    result = run(core)
    assert result['verdict'] == 'unknown'
    assert result['confidence'] == 0
    assert result['action_signals'] == []
    assert result['structured']['action_signals'] == []


def test_filtered_completion_cannot_be_success(core, monkeypatch):
    monkeypatch.setattr(core, '_call_llm', lambda **kwargs: response(VALID, 'content_filter'))
    result = run(core)
    assert result['status'] != 'success'


def test_compatible_model_fallback_continues_length_stopped_output(core, monkeypatch):
    calls = []
    def llm(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if 'tools' in kwargs:
            raise RuntimeError('tool calling is unsupported')
        return response(VALID + '（来源：', 'length', '补齐来源') if len(calls) == 2 else response('工具数据）。')
    monkeypatch.setattr(core, '_call_llm', llm)
    result = run(core)
    assert result['analysis'] == VALID + '（来源：工具数据）。'
    assert result['status'] == 'success'
    assert len(calls) == 3


def test_xml_tool_followup_preserves_mimo_reasoning(core, monkeypatch):
    calls = []
    def llm(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        return response(XML, reasoning='先查询估值再核对风险') if len(calls) == 1 else response(VALID)
    monkeypatch.setattr(core, '_call_llm', llm)
    monkeypatch.setattr(core, 'execute_tool', lambda *args, **kwargs: '{"temperature":93}')
    result = run(core)
    assistant = next(message for message in calls[1]['messages'] if message['role'] == 'assistant')
    assert assistant['content'] == XML
    assert assistant['reasoning_content'] == '先查询估值再核对风险'
    assert result['analysis'] == VALID


def test_invalid_closure_preserves_valid_shorter_draft(core, monkeypatch):
    original = '基于当前持仓数据，单一标的集中度需要核对总资产口径；久期和相关性数据不足，当前只能确认结构风险，应补查后再讨论是否调整。'
    assert 50 <= len(original) < 200
    calls = []
    def llm(**kwargs):
        calls.append(kwargs)
        return response(original if len(calls) == 1 else XML * 4)
    monkeypatch.setattr(core, '_call_llm', llm)
    result = run(core)
    assert result['analysis'] == original
    assert result['status'] == 'degraded'


def test_failed_specialist_clears_asset_actions_in_all_protocol_layers(core, monkeypatch):
    monkeypatch.setattr(core, '_call_llm', lambda **kwargs: response(VALID + '（来源：知识库', 'length'))
    action = {'target': '014846', 'type': 'SELL', 'reason': '超出集中度', 'status': 'current'}
    protocol = {'verdict': 'sell', 'confidence': .9, 'portfolio_stance': 'sell',
                'asset_actions': [action], 'action_signals': [action]}
    monkeypatch.setattr(core, '_parse_structured_output', lambda *args: {
        'structured': copy.deepcopy(protocol), 'a2a': {'payload': copy.deepcopy(protocol)}})
    result = run(core)
    for layer in (result, result['structured'], result['structured']['structured'],
                  result['structured']['a2a']['payload']):
        assert layer['asset_actions'] == []
        assert layer['action_signals'] == []
        assert layer['portfolio_stance'] == 'unknown'
        assert layer['verdict'] == 'unknown'
        assert layer['confidence'] == 0
