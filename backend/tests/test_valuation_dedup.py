"""单次分析复用同指数快照、失败源短缓存，避免重复在线取数。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import time
from db import valuations


def test_online_same_key_failure_singleflight(monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        time.sleep(.02)
        return None
    monkeypatch.setattr(valuations, '_online_fallback_uncached', fetch, raising=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: valuations._online_fallback('SINGLE_TEST','市盈率',datetime.now(),'test'), range(3)))
    assert results == [None]*3
    assert len(calls) == 1


def test_sizing_reuses_existing_portfolio_snapshot(monkeypatch):
    from services.advisor import decision_pipeline, smart_add_planner
    calls = []
    monkeypatch.setattr(smart_add_planner, 'generate_smart_add_plan', lambda *a, **kw: calls.append(1))
    layer = decision_pipeline._sizing_layer('000001','default',{},1000,
            plan_result={'plans':[{'fund_code':'000001','final_suggested_amount':42}]})
    assert layer['suggested_amount'] == 42
    assert calls == []
