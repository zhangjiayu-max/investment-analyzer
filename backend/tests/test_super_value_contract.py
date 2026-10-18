"""超性价比指标方向、独立来源、模型权限回归；自动临时库禁止真实模型调用。"""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from db._conn import _get_conn
from routers.market import valuation as route


def add_series(code='000300', metric='市盈率', pcts=(20, 15, 5), source='data/images/normal.png', end=None):
    end = end or date.today()
    conn = _get_conn()
    for i, p in enumerate(pcts):
        day = end - timedelta(days=len(pcts) - 1 - i)
        conn.execute('INSERT INTO index_valuations (index_code,index_name,metric_type,snapshot_date,percentile,current_value,zscore,source_image) VALUES (?,?,?,?,?,?,?,?)',
                     (code, code, metric, str(day), p, 10, -1.5, source))
    conn.commit()
    conn.close()


def test_dividend_direction_and_expensive_rapid_decline():
    add_series('000001', '股息率', (30, 20, 1))
    add_series('000002', '股息率', (70, 80, 99))
    add_series('000003', pcts=(99, 98, 95, 94, 93, 90, 79))
    result = route._scan_super_value_impl(min_score=0)
    assert [o['index_code'] for o in result['opportunities']] == ['000002']
    assert result['opportunities'][0]['cheapness_percentile'] == 1


def test_cheapness_score_does_not_reward_declining_trend():
    add_series('000001', pcts=(80, 60, 40, 30, 20, 10, 5))
    add_series('000002', pcts=(5, 5, 5, 5, 5, 5, 5))
    a, b = sorted(route._scan_super_value_impl()['opportunities'], key=lambda x: x['index_code'])
    assert a['score'] == b['score']
    assert a['trend_risk']['level'] != b['trend_risk']['level']
    assert '百分点' in a['score_breakdown']['drop_7d']['detail']


def test_fresh_source_wins_without_mixing_and_real_date_range():
    add_series('000300.SH', pcts=(20, 15, 5), source='dd_old.png', end=date.today()-timedelta(days=20))
    add_series('000300', pcts=(20, 15, 5), source='data/images/normal.png')
    result = route._scan_super_value_impl()
    assert len(result['opportunities']) == 1
    opp = result['opportunities'][0]
    assert opp['data_source'] == '雷牛牛'
    assert opp['data_points'] == 3
    start, end = result['data_range'].split(' ~ ')
    assert start <= end
    assert result['snapshot_id']


def test_unknown_legacy_filename_cannot_form_a_verified_series():
    add_series('000301', pcts=(20, 15, 5), source='normal.png')
    result = route._scan_super_value_impl()
    assert not any(item['index_code'] == '000301' for item in result['opportunities'])


def test_raw_dd_history_and_liuyi_reference_coverage():
    from db.valuations import save_dd_valuation, save_liuyi_valuation
    for i, p in enumerate((25, 15, 5)):
        save_dd_valuation({'ok': True, 'update_date': str(date.today()-timedelta(days=2-i)), 'data': [
            {'index_code': '000300.SH', 'index_name': '沪深300', 'pb': 1.1, 'pb_percentile': p}]}, f'dd_{i}.png')
    save_liuyi_valuation({'ok': True, 'update_date': str(date.today()), 'data': [
        {'index_code': '000300', 'index_name': '沪深300', 'pe': 15}]}, 'liuyi.png')
    result = route._scan_super_value_impl()
    assert result['opportunities'][0]['metric_type'] == '市净率'
    coverage = {c['source']: c for c in result['coverage']}
    assert coverage['螺丝钉']['usable_series'] == 1
    assert coverage['六亿']['reference_only_records'] == 1
    assert coverage['雷牛牛']['status'] == 'unavailable'


def test_disabled_deep_is_explicit(monkeypatch):
    import db.config
    monkeypatch.setattr(db.config, 'get_config_bool', lambda *a: False)
    result = asyncio.run(route.get_super_value_deep())
    assert result['enabled'] is False
    assert result['status'] == 'disabled'
    assert result['deep_analysis'] == []


def test_model_whitelist_and_server_owned_fields():
    from services.valuation.opportunity_screener import validate_interpretations
    candidate = {'index_code': '000300', 'index_name': '沪深300', 'score': 60, 'metric_type': '市净率', 'latest_date': str(date.today()), 'current_percentile': 5}
    valid = {'index_code': '000300.SH', 'rule_score': 999, 'index_name': 'fake', 'action': '立即买入', 'confidence': 'high', 'opportunity_type': '真低估', 'catalysts': []}
    rows, warnings = validate_interpretations([valid, {**valid, 'index_code': '666666'}, {**valid, 'action': 'ALL IN'}], [candidate])
    assert len(rows) == 1
    assert rows[0]['rule_score'] == 60
    assert rows[0]['index_name'] == '沪深300'
    assert rows[0]['metric_type'] == '市净率'
    assert warnings


def test_online_metric_not_pe_fallback_and_valid_forecast_fallback():
    from services.valuation.opportunity_screener import enrich_candidate
    calls = []
    opp = {'index_code': '000300', 'metric_type': '股息率', 'stale_days': 6, 'score': 60, 'latest_date': str(date.today()-timedelta(days=6)), 'current_percentile': 95}
    def online(code, metric):
        calls.append(('online', metric))
        return {'metric_type': '市盈率', 'current_value': 15, 'snapshot_date': str(date.today()), 'percentile': 95}
    def mr(code, metric):
        calls.append(('mr', metric))
        return {'signal': 'insufficient_data'}
    def ew(code, metric):
        calls.append(('ew', metric))
        return {'signal': 'extreme_high', 'metric_type': metric}
    result = asyncio.run(enrich_candidate(opp, online, mr, ew, online_threshold_days=3))
    assert ('online', '股息率') in calls
    assert ('ew', '股息率') in calls
    assert result['online_pe'] is None
    assert result['current_verification']['status'] == 'metric_mismatch'
    assert result['forecast_signal'] == 'extreme_high'
    assert result['score'] == result['historical_score'] == 60


def test_online_new_percentile_is_separate_verification():
    from services.valuation.opportunity_screener import enrich_candidate
    opp = {'index_code': '000300', 'metric_type': '市净率', 'stale_days': 6, 'score': 60, 'latest_date': str(date.today()-timedelta(days=6)), 'current_percentile': 5}
    result = asyncio.run(enrich_candidate(opp, lambda *a: {'metric_type': '市净率', 'current_value': 3, 'snapshot_date': str(date.today()), 'percentile': 95}, lambda *a: {}, lambda *a: {}))
    assert result['current_percentile'] == 5
    assert result['current_verification']['percentile'] == 95
    assert result['current_verification']['comparable'] is False
    assert result['current_verification']['status'] == 'available'
    assert result['online_pe'] is None


def _stub_enabled_deep(monkeypatch, response=None, error=None):
    import db
    import db.config
    import db.valuations
    import services.llm_service
    import services.valuation_forecast
    monkeypatch.setattr(db.config, 'get_config_bool', lambda *a: True)
    monkeypatch.setattr(db.config, 'get_config_int', lambda k, default: 1 if k == 'agent.valuation_screener_top_n' else default)
    monkeypatch.setattr(db, 'get_analysis_agent_by_name', lambda *a: {'id': 1, 'system_prompt': 'Configured valuation analyst', 'is_active': 1})
    monkeypatch.setattr(db.valuations, 'fetch_online_valuation', lambda *a: None)
    monkeypatch.setattr(services.valuation_forecast, 'mean_reversion_analysis', lambda *a: {'signal': 'insufficient_data'})
    monkeypatch.setattr(services.valuation_forecast, 'extreme_warning', lambda *a: {'signal': 'insufficient_data'})
    def llm(**kwargs):
        if error:
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response))], usage=None)
    monkeypatch.setattr(services.llm_service, '_call_llm', llm)


def test_deep_failure_logs_error_and_keeps_full_snapshot(monkeypatch):
    add_series('000001')
    add_series('000002')
    _stub_enabled_deep(monkeypatch, error=RuntimeError('offline model'))
    import db.agent_analysis_log as logs
    statuses = []
    monkeypatch.setattr(logs, 'complete_analysis_log', lambda **kw: statuses.append(kw['status']))
    result = asyncio.run(route.get_super_value_deep())
    assert len(result['opportunities']) == 2
    assert result['status'] == 'error'
    assert result['enabled'] is True
    assert statuses == ['error']


def test_deep_validates_output_and_uses_configured_prompt(monkeypatch):
    import json
    add_series('000001')
    _stub_enabled_deep(monkeypatch, response=json.dumps({'deep_analysis': [
        {'index_code': '000001', 'index_name': 'invented', 'rule_score': 999, 'action': '观望', 'confidence': 'low', 'opportunity_type': '价值陷阱', 'catalysts': []},
        {'index_code': '999999', 'action': '观望', 'confidence': 'low', 'opportunity_type': '价值陷阱'}], 'overall_summary': '谨慎核验'}))
    import services.llm_service
    original = services.llm_service._call_llm
    def verify_prompt(**kw):
        assert kw['messages'][0] == {'role': 'system', 'content': 'Configured valuation analyst'}
        return original(**kw)
    monkeypatch.setattr(services.llm_service, '_call_llm', verify_prompt)
    result = asyncio.run(route.get_super_value_deep())
    assert result['status'] == 'partial'
    assert len(result['deep_analysis']) == 1
    assert result['deep_analysis'][0]['rule_score'] == result['opportunities'][0]['score']


def test_invalid_values_and_future_dates_are_not_fresh_candidates():
    add_series('000001', pcts=(float('inf'), float('inf'), float('inf')))
    add_series('000002', pcts=(5, 5, 5), end=date.today()+timedelta(days=20))
    result = route._scan_super_value_impl()
    assert result['opportunities'] == []


def test_raw_names_resolve_only_unique_mapping_or_standard_name():
    from db.valuations import save_dd_valuation, save_index_code_mapping
    save_index_code_mapping('000300', '沪深300', aliases=['沪深三百'])
    for i, p in enumerate((25, 15, 5)):
        save_dd_valuation({'ok': True, 'update_date': str(date.today()-timedelta(days=2-i)), 'data': [
            {'index_name': '沪深三百', 'pb': 1, 'pb_percentile': p},
            {'index_name': '未知指数', 'pe': 15, 'pe_percentile': 5}]}, f'raw_names_{i}.png')
    result = route._scan_super_value_impl()
    assert result['opportunities'][0]['index_code'] == '000300'
    dd = next(c for c in result['coverage'] if c['source'] == '螺丝钉')
    assert dd['missing_code_records'] == 3
    assert dd['resolved_name_records'] == 3


def test_deep_real_log_reaches_completed_status(monkeypatch):
    import json
    add_series('000001')
    _stub_enabled_deep(monkeypatch, response=json.dumps({'deep_analysis': [
        {'index_code': '000001', 'action': '观望', 'confidence': 'low', 'opportunity_type': '价值陷阱'}], 'overall_summary': '谨慎核验'}))
    result = asyncio.run(route.get_super_value_deep())
    assert result['status'] == 'success'
    conn = _get_conn()
    try:
        log = conn.execute("SELECT status FROM agent_analysis_log WHERE analysis_type='super_value_deep'").fetchone()
        assert log['status'] == 'done'
    finally:
        conn.close()


def test_model_invalid_enum_structure_is_dropped():
    from services.valuation.opportunity_screener import validate_interpretations
    rows, warnings = validate_interpretations([{'index_code': '000300', 'action': ['买入'], 'opportunity_type': '真低估', 'confidence': 'high'}], [{'index_code': '000300', 'score': 50}])
    assert rows == []
    assert warnings


def test_source_histories_cannot_be_combined_to_reach_three_observations():
    add_series('000300', pcts=(10, 5), source='dd_a.png')
    add_series('000300.SH', pcts=(10, 5), source='normal.png')
    result = route._scan_super_value_impl()
    assert result['opportunities'] == []
    assert result['empty_reason'] == 'insufficient_history'


def test_ambiguous_alias_is_not_guessed():
    from db.valuations import save_dd_valuation, save_index_code_mapping
    save_index_code_mapping('000001', '指数一', aliases=['同名指数'])
    save_index_code_mapping('000002', '指数二', aliases=['同名指数'])
    for i in range(3):
        save_dd_valuation({'ok': True, 'update_date': str(date.today()-timedelta(days=i)), 'data': [
            {'index_name': '同名指数', 'pe': 10, 'pe_percentile': 5}]}, f'unknown_{i}.png')
    result = route._scan_super_value_impl()
    assert result['opportunities'] == []
    assert next(c for c in result['coverage'] if c['source'] == '螺丝钉')['missing_code_records'] == 3


def test_enhanced_uses_identical_snapshot_metric_and_cutoff(monkeypatch):
    import json
    import services.valuation as service
    import mcp.yingmi_client as yingmi
    import db.knowledge
    import db.config
    add_series('000300', metric='市净率')
    expected = route._scan_super_value_impl()
    _stub_enabled_deep(monkeypatch, response=json.dumps({'strategies': [
        {'index_code': '000300', 'action': '观望', 'confidence': 'low', 'opportunity_type': '价值陷阱'}], 'overall_summary': '核验基本面'}))
    monkeypatch.setattr(service, 'check_pe_pb_divergence', lambda *a: None)
    monkeypatch.setattr(service, 'get_index_history_years', lambda *a: 10)
    monkeypatch.setattr(service, 'get_history_years_warning', lambda *a: None)
    monkeypatch.setattr(yingmi, 'get_yingmi_client', lambda: SimpleNamespace(call_tool=lambda *a: {}))
    monkeypatch.setattr(db.knowledge, 'search_knowledge', lambda *a, **kw: [])
    enhanced = asyncio.run(route.get_enhanced_strategy())
    assert enhanced['snapshot_id'] == expected['snapshot_id']
    assert enhanced['cutoff_date'] == expected['cutoff_date']
    assert enhanced['opportunities'] == expected['opportunities']
    assert enhanced['strategies'][0]['metric_type'] == '市净率'
    monkeypatch.setattr(db.config, 'get_config_bool', lambda *a: False)
    deep = asyncio.run(route.get_super_value_deep())
    assert deep['snapshot_id'] == enhanced['snapshot_id']


def test_enhanced_saves_complete_validated_json_and_news_evidence(monkeypatch):
    import json
    import services.valuation as service
    import services.llm_service as llm
    import mcp.yingmi_client as yingmi
    import db.knowledge
    add_series('000300')
    text = '基本面需要持续核验。' * 400
    _stub_enabled_deep(monkeypatch, response=json.dumps({'strategies': [
        {'index_code': '000300', 'action': '观望', 'confidence': 'low', 'opportunity_type': '价值陷阱', 'action_detail': text}], 'overall_summary': '核验基本面', 'untrusted_field': 'should not persist'}))
    monkeypatch.setattr(service, 'check_pe_pb_divergence', lambda *a: None)
    monkeypatch.setattr(service, 'get_index_history_years', lambda *a: 10)
    monkeypatch.setattr(service, 'get_history_years_warning', lambda *a: None)
    news = {'content': [{'type': 'text', 'text': json.dumps({'success': True, 'data': {'items': [
        {'title': '政策新闻', 'publish_time': '2026-10-17', 'url': 'https://example.com/news', 'summary': '政策变化'}]}})}]}
    monkeypatch.setattr(yingmi, 'get_yingmi_client', lambda: SimpleNamespace(call_tool=lambda *a: news))
    monkeypatch.setattr(db.knowledge, 'search_knowledge', lambda *a, **kw: [])
    original = llm._call_llm
    prompts = []
    def capture(**kw):
        prompts.append(kw['messages'][0]['content'])
        return original(**kw)
    monkeypatch.setattr(llm, '_call_llm', capture)
    result = asyncio.run(route.get_enhanced_strategy())
    assert 'https://example.com/news' in prompts[0]
    assert '2026-10-17' in prompts[0]
    conn = _get_conn()
    try:
        saved = json.loads(conn.execute('SELECT result FROM analysis_history ORDER BY id DESC LIMIT 1').fetchone()['result'])
        assert saved['strategies'][0]['action_detail'] == text
        assert saved['snapshot_id'] == result['snapshot_id']
        assert 'untrusted_field' not in saved
    finally:
        conn.close()


def test_online_quote_iso_timestamp_retains_actual_date():
    from services.valuation.opportunity_screener import enrich_candidate
    opp = {'index_code': '000300', 'metric_type': '市净率', 'stale_days': 6, 'score': 60, 'latest_date': str(date.today()-timedelta(days=6)), 'current_percentile': 5}
    timestamp = f'{date.today()}T09:30:00+08:00'
    result = asyncio.run(enrich_candidate(opp, lambda *a: {'metric_type': '市净率', 'current_value': 3, 'snapshot_date': timestamp}, lambda *a: {}, lambda *a: {}))
    assert result['current_verification']['status'] == 'available'
    assert result['current_verification']['snapshot_date'] == str(date.today())


def test_explicit_providers_override_filename_and_never_form_mixed_history():
    import json
    add_series('000300', pcts=(20, 10, 5), source='ordinary.png')
    conn = _get_conn()
    rows = conn.execute('SELECT id FROM index_valuations ORDER BY snapshot_date').fetchall()
    for row, provider in zip(rows, ('螺丝钉', '六亿', '雷牛牛')):
        conn.execute('UPDATE index_valuations SET raw_json=? WHERE id=?', (json.dumps({'provider': provider}), row['id']))
    conn.commit()
    conn.close()
    result = route._scan_super_value_impl()
    assert result['opportunities'] == []
    assert result['empty_reason'] == 'insufficient_history'
    assert {c['source'] for c in result['coverage'] if c['records']} == {'螺丝钉', '六亿', '雷牛牛'}


def test_legacy_filename_is_explicitly_unverified():
    add_series(source='normal.png')
    result = route._scan_super_value_impl()
    assert result['opportunities'] == []
    unknown = next(c for c in result['coverage'] if c['source'].startswith('未核验文件:'))
    assert unknown['status'] == 'reference_only'


def test_catalysts_are_server_labeled_hypotheses():
    from services.valuation.opportunity_screener import validate_interpretations
    rows, _ = validate_interpretations([{'index_code': '000300', 'action': '观望', 'opportunity_type': '真低估', 'confidence': 'low', 'catalysts': ['未来盈利改善'], 'catalysts_status': 'confirmed'}], [{'index_code': '000300', 'score': 50}])
    assert rows[0]['catalysts_status'] == 'hypothesis'
