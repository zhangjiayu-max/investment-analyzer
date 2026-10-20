"""统一风险HTTP契约：只读、重算、版本冲突。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

@pytest.fixture
def client():
    from routers.portfolio.decision_risk import router
    app=FastAPI();app.include_router(router)
    return TestClient(app)


def test_snapshot_and_preview_are_read_only_and_unknown_is_not_allowed(client):
    from db._conn import _get_conn
    r=client.get('/api/portfolio/decision-risk/snapshot')
    assert r.status_code==200
    assert r.json()['status']=='wait_for_data'
    r=client.post('/api/portfolio/decision-risk/preview',json={'fund_code':'E','amount':1000})
    assert r.status_code==200 and r.json()['executable_amount']==0
    c=_get_conn()
    for table in ('portfolio_cash','portfolio_risk_policy','portfolio_decision_reviews','portfolio_transactions'):
        assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
    c.close()


def test_policy_version_conflict_is_visible(client):
    r=client.put('/api/portfolio/decision-risk/policy',json={'policy':{'max_equity_pct':50},'expected_version':0})
    assert r.status_code==200 and r.json()['confirmed']
    r=client.put('/api/portfolio/decision-risk/policy',json={'policy':{'max_equity_pct':60},'expected_version':0})
    assert r.status_code==409


def test_cannot_save_a_changed_snapshot_or_unknown_decision(client):
    r=client.post('/api/portfolio/decision-risk/reviews',json={'fund_code':'E','amount':1000,'snapshot_id':'old'})
    assert r.status_code==409
    sid=client.get('/api/portfolio/decision-risk/snapshot').json()['snapshot_id']
    r=client.post('/api/portfolio/decision-risk/reviews',json={'fund_code':'E','amount':1000,'snapshot_id':sid})
    assert r.status_code==400


def test_missing_execution_is_a_clear_validation_error(client):
    r=client.post('/api/portfolio/decision-risk/reviews/1/execution',json={'transaction_id':1})
    assert r.status_code==400
