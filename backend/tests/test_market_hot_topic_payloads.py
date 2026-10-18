"""市场情报热门话题的真实上游格式兼容回归。"""
import asyncio
import json
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('payload', [
    {'success': True, 'data': [{'title': '算力投资', 'summary': '需求增长', 'heat': 90}]},
    {'success': True, 'data': {'items': [{'title': '算力投资', 'summary': '需求增长', 'heat': 90}]}},
    [{'title': '算力投资', 'summary': '需求增长', 'heat': 90}],
])
def test_hot_topics_preserve_supported_payloads(monkeypatch, payload):
    import mcp.yingmi_client as yingmi
    from routers.analysis.market_intel import _fetch_hot_topics
    monkeypatch.setattr(yingmi, 'get_yingmi_client', lambda: SimpleNamespace(
        call_tool=lambda *a: {'content': [{'type': 'text', 'text': json.dumps(payload)}]}))
    assert asyncio.run(_fetch_hot_topics()) == [
        {'title': '算力投资', 'summary': '需求增长', 'heat': 90}]


def test_hot_topics_skip_bad_blocks_and_rows(monkeypatch):
    import mcp.yingmi_client as yingmi
    from routers.analysis.market_intel import _fetch_hot_topics
    blocks = [
        {'type': 'text', 'text': 'invalid json'},
        {'type': 'text', 'text': json.dumps({'success': True, 'data': [None, 'bad', {'title': '有效话题'}]})},
    ]
    monkeypatch.setattr(yingmi, 'get_yingmi_client', lambda: SimpleNamespace(
        call_tool=lambda *a: {'content': blocks}))
    assert asyncio.run(_fetch_hot_topics()) == [{'title': '有效话题', 'summary': '', 'heat': ''}]
