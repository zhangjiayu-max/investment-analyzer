"""交易复盘不能被历史估值快照缺失或格式异常阻断。"""
import asyncio
import json
from unittest.mock import Mock

import pytest


@pytest.fixture
def prepared_route(monkeypatch):
    from routers.analysis import trade_review as module
    import db.async_tasks as tasks
    captured = {}
    monkeypatch.setattr(module, 'get_analysis_agent_by_name', lambda name: {
        'id': 5, 'name': name, 'is_active': 1, 'system_prompt': 'configured prompt'})
    monkeypatch.setattr(module, 'get_transaction_tags', lambda ident: [])
    monkeypatch.setattr(module, 'build_portfolio_facts_with_check', lambda: '{}')
    monkeypatch.setattr(tasks, 'start_analysis_task', lambda *a: {
        'id': 31, 'task_id': 32, 'status': 'running', 'reused': False})
    async def worker(task_id, record_id, prompt, user_content, *args):
        captured['content'] = user_content
    monkeypatch.setattr(module, '_run_trade_review_async', worker)
    monkeypatch.setattr(module, '_background_tasks', set())
    return module, captured


def run_review(module):
    from models.portfolio import TradeReviewRequest
    async def run():
        result = await module.trade_review_api(TradeReviewRequest())
        await asyncio.gather(*module._background_tasks)
        return result
    return asyncio.run(run())


def transaction(ident, snapshot, kind='buy'):
    return {'id': ident, 'transaction_date': '2026-10-16', 'transaction_type': kind,
            'fund_code': '161725', 'fund_name': '测试基金', 'amount': 100,
            'valuation_snapshot': snapshot}


@pytest.mark.parametrize('snapshot', [
    json.dumps({'pe_percentile': None, 'pb_percentile': 20}),
    '{}', 'null', '[1]', 'bad json',
])
def test_missing_pe_does_not_prevent_start(prepared_route, monkeypatch, snapshot):
    module, captured = prepared_route
    monkeypatch.setattr(module, 'list_transactions', lambda **kw: [transaction(1, snapshot)])
    assert run_review(module)['task_id'] == 32
    assert '平均PE分位' not in captured['content']
    assert 'PE分位缺失或无效' in captured['content']


def test_summary_uses_only_valid_pe_and_reports_coverage(prepared_route, monkeypatch):
    module, captured = prepared_route
    snapshots = [None, 0, '20%', 'not-a-number', float('nan'), 101]
    rows = [transaction(i+1, json.dumps({'pe_percentile': pe})) for i, pe in enumerate(snapshots)]
    rows += [transaction(7, json.dumps({'pe_percentile': 80}), 'sell')]
    monkeypatch.setattr(module, 'list_transactions', lambda **kw: rows)
    run_review(module)
    text = captured['content']
    assert '平均PE分位 10.0%' in text
    assert '低估买入(PE<30%) 2/2 笔' in text
    assert '有效PE快照 2/6 笔' in text
    assert 'PE分位缺失或无效 4 笔' in text
    assert '高估卖出(PE>70%) 1/1 笔' in text
