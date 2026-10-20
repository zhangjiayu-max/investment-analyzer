"""调试报告下载必须使用未截断的原始产出。"""
import json

import pytest


@pytest.mark.parametrize('structured', [False, True])
def test_debug_keeps_full_report_for_export(monkeypatch, structured):
    import tools
    from routers.admin.capabilities import api_capabilities_debug

    report = '## 分析报告\n' + '完整正文' * 2000 + '\n报告末尾'
    raw = json.dumps({'analysis': report}, ensure_ascii=False) if structured else report
    monkeypatch.setattr(tools, 'execute_tool', lambda *args, **kwargs: raw)
    result = api_capabilities_debug({'tool_name': 'query_valuation', 'arguments': {'index_name': '沪深300'}})

    assert len(result['result']) < len(raw)
    assert result['full_result'] == raw
    assert '报告末尾' in result['full_result']
