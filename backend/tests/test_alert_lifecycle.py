"""风险事实与通知独立；全部数据库操作限于 tmp_db，禁止网络。"""
import socket
from datetime import datetime, timedelta
import pytest

@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def denied(*a, **kw):
        raise AssertionError('external TCP forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)

@pytest.fixture
def lifecycle(tmp_db):
    from db import alert_lifecycle as m
    from db._conn import _get_conn
    conn = _get_conn(); m.init_alert_lifecycle(conn); conn.commit(); conn.close()
    return m


def risk(m, severity='warning', **kw):
    return m.create_alert('loss_warning', '亏损', severity=severity,
                          related_fund_code='001', source='test_scan', **kw)


def test_read_does_not_resolve_or_remove_active_risk(lifecycle):
    m = lifecycle; key = risk(m)
    assert m.mark_alert_read(key)
    row = m.list_alerts(active_only=True)[0]
    assert row['risk_status'] == 'active'
    assert row['is_read'] == 1
    assert m.cleanup_old_alerts(days=-1, include_unread=True)['deleted'] == 0


def test_persistence_worsening_recovery_and_recurrence(lifecycle):
    m = lifecycle; key = risk(m, evidence={'risk_value': 16}, fact_date='2026-10-19')
    m.mark_alert_read(key)
    assert risk(m, evidence={'risk_value': 16}) == key
    assert m.list_alerts()[0]['is_read'] == 1
    assert risk(m, 'danger', evidence={'risk_value': 25}) == key
    row = m.list_alerts()[0]
    assert row['is_read'] == 0 and row['revision'] == 2
    assert m.resolve_completed_scan(['loss_warning'], [], source='test_scan', success=False) == 0
    assert m.resolve_completed_scan(['loss_warning'], [], source='other', success=True) == 0
    assert m.resolve_completed_scan(['loss_warning'], [], source='test_scan', success=True) == 1
    assert m.list_alerts(active_only=True) == []
    assert risk(m) == key
    row = m.list_alerts()[0]
    assert row['risk_status'] == 'recurred' and row['episode'] == 2 and row['is_read'] == 0
    assert {e['event_type'] for e in m.get_lifecycle_history(key)} >= {'detected','worsened','resolved','recurred'}


def test_missing_coverage_never_resolves_and_action_is_not_recovery(lifecycle):
    m = lifecycle; key = risk(m)
    assert m.resolve_completed_scan([], [], source='test_scan', success=True) == 0
    assert m.update_alert_action(key, 'acknowledged')
    assert m.list_alerts(active_only=True)[0]['risk_status'] == 'in_progress'
    with pytest.raises(ValueError, match='原因'):
        m.update_alert_action(key, 'ignored')
    assert m.update_alert_action(key, 'ignored', reason='等待核对', review_date='2026-10-25')
    assert len(m.list_alerts(active_only=True)) == 1


def test_server_prioritizes_danger_before_pagination(lifecycle):
    m = lifecycle
    for i in range(55):
        m.create_alert('notice', f'信息{i}', related_fund_code=str(i), source='test_scan')
    risk(m, 'danger')
    page = m.list_active_alerts_page(limit=5)
    assert page['total'] == 56 and page['alerts'][0]['severity'] == 'danger'
    assert len(page['alerts']) == 5
    assert page['active_count'] == 56


def test_legacy_groups_keep_read_contract(lifecycle):
    m = lifecycle
    from db._conn import _get_conn
    conn = _get_conn()
    for read in [0, 1]:
        conn.execute("INSERT INTO portfolio_alerts(user_id,alert_type,severity,title,related_fund_code,is_read) VALUES('default','legacy','warning','旧风险','001',?)", (read,))
    conn.commit(); conn.close()
    row = m.list_alerts()[0]
    assert row['cnt'] == 2 and row['unread_count'] == 1
    assert m.mark_alert_read(row['id'])
    assert m.list_alerts()[0]['is_read'] == 1


def test_reliability_never_calls_three_samples_high(lifecycle):
    value = lifecycle.calculate_reliability({'avg_win_rate':100, 'total_samples':3, 'avg_change':2})
    assert value['level'] == 'unknown'
    assert value['confidence_interval'] and value['samples'] == 3


def test_resolved_notifications_do_not_inflate_unread_risks(lifecycle):
    m=lifecycle; key=risk(m)
    assert m.get_unread_alert_count()==1
    m.resolve_completed_scan(['loss_warning'],[],source='test_scan',success=True)
    assert m.get_unread_alert_count()==0


def test_ignore_review_due_reopens_notification_without_resolving(lifecycle,monkeypatch):
    from datetime import datetime as real_datetime
    m=lifecycle; key=risk(m)
    future=(real_datetime.now()+timedelta(days=2)).strftime('%Y-%m-%d')
    m.update_alert_action(key,'ignored',reason='等待补齐账单',review_date=future)
    m.mark_alert_read(key)
    class FutureClock:
        @classmethod
        def now(cls): return real_datetime.now()+timedelta(days=3)
        strptime=real_datetime.strptime
    monkeypatch.setattr(m,'datetime',FutureClock)
    risk(m)
    row=m.list_alerts(active_only=True)[0]
    assert row['is_read']==0 and row['acknowledged_status'] is None
    assert row['risk_status']=='active'
    assert any(e['event_type']=='review_due' for e in m.get_lifecycle_history(key))


def test_ignored_requires_nonempty_reason_and_nonpast_review(lifecycle):
    m=lifecycle; key=risk(m)
    with pytest.raises(ValueError):
        m.update_alert_action(key,'ignored',reason='   ',review_date='2099-01-01')
    with pytest.raises(ValueError):
        m.update_alert_action(key,'ignored',reason='核对',review_date='2000-01-01')


def test_unread_list_excludes_resolved_as_unread_count_does(lifecycle):
    m=lifecycle; risk(m)
    m.resolve_completed_scan(['loss_warning'],[],source='test_scan',success=True)
    assert m.list_alerts(unread_only=True)==[]


def test_cannot_cleanup_resolved_representative_if_group_contains_active(lifecycle):
    m=lifecycle
    from db._conn import _get_conn
    conn=_get_conn()
    active=conn.execute("INSERT INTO portfolio_alerts(user_id,alert_type,severity,title,related_fund_code,risk_status) VALUES('default','legacy','warning','活动','001','active')").lastrowid
    resolved=conn.execute("INSERT INTO portfolio_alerts(user_id,alert_type,severity,title,related_fund_code,risk_status) VALUES('default','legacy','warning','已恢复','001','resolved')").lastrowid
    conn.commit();conn.close()
    with pytest.raises(ValueError,match='活动'):
        m.delete_resolved_alert(resolved)
    assert len(m.list_alerts(active_only=True))==1
