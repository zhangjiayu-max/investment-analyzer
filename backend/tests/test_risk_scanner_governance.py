"""扫描契约及风险检测回归；只使用临时数据库和内存行情。"""
import socket
from datetime import date, timedelta
import pytest

@pytest.fixture(autouse=True)
def no_tcp(monkeypatch):
    def denied(*a, **kw): raise AssertionError('external TCP forbidden')
    monkeypatch.setattr(socket.socket,'connect',denied)

@pytest.fixture
def scanner(monkeypatch, tmp_db):
    from services.advisor import alert_scanner as s
    from db.alert_lifecycle import init_alert_lifecycle
    from db._conn import _get_conn
    conn=_get_conn(); init_alert_lifecycle(conn); conn.commit(); conn.close()
    monkeypatch.setattr(s,'_is_enabled',lambda key, default=True: default if key=='alerts.auto_candidate_enabled' else True)
    monkeypatch.setattr(s,'_get_int',lambda key, default:default)
    monkeypatch.setattr(s,'_build_risk_content',lambda *a: '测试风险事实')
    return s


def holdings():
    return [{'fund_code':str(i),'fund_name':f'基金{i}','shares':100,'current_price':1 if i else 4,
             'cost_price':8,'today_change_pct':-7,'price_updated_at':date.today().isoformat()} for i in range(5)]


def test_real_holding_fields_detect_three_independent_risks(scanner, monkeypatch):
    s=scanner; captured=[]
    monkeypatch.setattr(s,'list_holdings',holdings)
    monkeypatch.setattr(s,'create_alert',lambda **kw:captured.append(kw) or len(captured))
    result=s.scan_portfolio_risk()
    keys={(a['alert_type'],a['related_fund_code']) for a in captured}
    assert {('concentration_high','0'),('loss_warning','0'),('daily_drop','0')} <= keys
    assert result['coverage']['daily_drop']['checked']==5


def test_missing_price_prevents_false_loss_and_concentration_recovery(scanner,monkeypatch):
    s=scanner
    monkeypatch.setattr(s,'list_holdings',lambda:[dict(holdings()[0],current_price=None)])
    captured=[]; monkeypatch.setattr(s,'create_alert',lambda **kw:captured.append(kw))
    result=s.scan_portfolio_risk()
    assert not captured
    assert result['failed']>0
    assert result['coverage']['concentration_high']['status']=='incomplete'


def test_monday_accepts_friday_fact_date_but_stale_daily_change_is_skipped(scanner,monkeypatch):
    assert scanner._price_fact_is_current('2026-10-16', as_of=date(2026,10,19))
    assert not scanner._price_fact_is_current('2026-10-12', as_of=date(2026,10,19))


def test_periodic_contract_reports_partial_failure_and_updates(scanner,monkeypatch):
    funcs=['scan_and_verify_recommendations','scan_valuation_thresholds','scan_portfolio_risk',
           'scan_loss_plus_undervalued','scan_watchlist_signals','scan_health_score',
           'scan_valuation_failures','scan_market_index_drop','scan_capital_flow_anomaly','scan_watchlist_exit_signals','scan_detailed_risks']
    for name in funcs: monkeypatch.setattr(scanner,name,lambda:{'alerts_created':0,'skipped':'disabled'})
    def raises(): raise RuntimeError('行情失败')
    monkeypatch.setattr(scanner,'scan_portfolio_risk',raises)
    result=scanner.run_periodic_scan()
    assert result['failed']==1 and result['status']=='incomplete'
    assert all(k in result for k in ('generated','updated','resolved','skipped','coverage','last_scan'))
    assert result['coverage']['portfolio']['status']=='failed'


def test_stale_valuation_never_opens_add_window(scanner,monkeypatch):
    monkeypatch.setattr(scanner,'list_holdings',lambda:[dict(holdings()[0],index_code='INDEX')])
    monkeypatch.setattr(scanner,'_get_preferred_metric_type',lambda code:'市盈率')
    monkeypatch.setattr(scanner,'get_best_valuation',lambda *a,**kw:{'percentile':1,'is_expired':True})
    captured=[]; monkeypatch.setattr(scanner,'create_alert',lambda **kw:captured.append(kw))
    result=scanner.scan_loss_plus_undervalued()
    assert not captured
    assert result['failed']>0


def test_missing_optional_zscore_keeps_risk_facts(scanner,monkeypatch):
    monkeypatch.setattr(scanner,'list_holdings',lambda:[dict(holdings()[0],index_code='INDEX')])
    monkeypatch.setattr(scanner,'_get_preferred_metric_type',lambda code:'市盈率')
    monkeypatch.setattr(scanner,'get_best_valuation',lambda *a,**kw:{'percentile':1,'is_expired':False,'snapshot_date':date.today().isoformat(),'zscore':None})
    captured=[]; monkeypatch.setattr(scanner,'create_alert',lambda **kw:captured.append(kw))
    result=scanner.scan_loss_plus_undervalued()
    assert len(captured)==1
    assert 'None' not in captured[0]['content']
    assert '补仓窗口开启' not in captured[0]['title']


def test_dict_history_drawdown_uses_sorted_facts(scanner):
    payload={'nav_history':[{'date':f'2026-10-{d:02d}','nav':1 if d<15 else .7} for d in reversed(range(1,20))],'transactions':[]}
    facts=scanner._drawdown_facts(payload)
    assert facts['drawdown_pct']==pytest.approx(30)
    assert facts['fact_date']=='2026-10-19'


def test_detailed_dict_drawdown_and_buy_drop_do_not_generate_add_signal(scanner,monkeypatch):
    from db.alert_lifecycle import list_alerts
    from db.daily_advice import init_daily_advice_tables
    init_daily_advice_tables()
    rows=[dict(holdings()[0],last_buy_price=8,last_buy_date='2026-10-01')]
    payload={'nav_history':[{'date':(date.today()-timedelta(days=d)).isoformat(),'nav':8 if d else 4} for d in range(11)],'transactions':[]}
    result=scanner.scan_detailed_risks(holdings=rows,nav_history_loader=lambda *a,**kw:payload,
           valuation_loader=lambda *a:None,cash_loader=lambda:100)
    alerts=list_alerts()
    assert any(a['alert_type']=='drawdown_alert' for a in alerts)
    assert any(a['alert_type']=='buy_drop_alert' for a in alerts)
    assert all('100%' not in (a['content'] or '') for a in alerts)
    assert result['failed']>0  # valuation missing is visible, not safe
    from db._conn import _get_conn
    conn=_get_conn()
    assert conn.execute("SELECT COUNT(*) FROM daily_position_signals").fetchone()[0]==0
    conn.close()


def test_failed_detailed_scan_preserves_previous_drawdown(scanner):
    from db.alert_lifecycle import create_alert,list_alerts
    create_alert('drawdown_alert','旧回撤',source='system_scan',related_fund_code='0')
    def failed(*a,**kw): raise RuntimeError('历史取数失败')
    result=scanner.scan_detailed_risks(holdings=holdings(),nav_history_loader=failed,
                                     valuation_loader=lambda *a:None,cash_loader=lambda:0)
    assert result['coverage']['drawdown_alert']['status']=='incomplete'
    assert any(a['alert_type']=='drawdown_alert' for a in list_alerts(active_only=True))


def test_add_candidate_builds_formal_decision_context(scanner,monkeypatch):
    from services.portfolio import decision_guardrails as guard
    from db import decisions
    formal={'funds':[{'fund_code':'001'}],'snapshot_id':'formal'}
    seen=[]; candidates=[]
    monkeypatch.setattr(scanner,'_is_enabled',lambda *a:True)
    monkeypatch.setattr(guard,'build_decision_snapshot',lambda **kw:formal)
    def evaluate(code,amount,**kw):
        seen.append(kw['context'])
        if kw['context'] is not formal:
            raise ValueError('source evidence is not a decision context')
        return {'status':'allowed','executable_amount':500,'snapshot_id':'formal'}
    monkeypatch.setattr(guard,'evaluate_add_decision',evaluate)
    monkeypatch.setattr(decisions,'create_candidate_from_structured_recommendation',lambda data:candidates.append(data) or 1)
    scanner._auto_candidate('001','测试','valuation_low','低估参考','风险说明',source_snapshot={'requested_amount':500,'percentile':10})
    assert seen==[formal]
    assert candidates[0]['action_type']=='add'
    assert candidates[0]['suggested_amount']==500


def test_missing_last_buy_evidence_cannot_resolve_buy_drop(scanner):
    from db.alert_lifecycle import create_alert,list_alerts
    create_alert('buy_drop_alert','买后下跌',source='system_scan',related_fund_code='0')
    result=scanner.scan_detailed_risks(holdings=holdings(),nav_history_loader=lambda *a,**k:None,
                                     valuation_loader=lambda *a:None,cash_loader=lambda:0)
    assert result['coverage']['buy_drop_alert']['status']=='incomplete'
    assert any(a['alert_type']=='buy_drop_alert' for a in list_alerts(active_only=True))


def test_default_periodic_scan_covers_detailed_risk_categories(scanner,monkeypatch):
    funcs=['scan_and_verify_recommendations','scan_valuation_thresholds','scan_portfolio_risk',
           'scan_loss_plus_undervalued','scan_watchlist_signals','scan_health_score',
           'scan_valuation_failures','scan_market_index_drop','scan_capital_flow_anomaly','scan_watchlist_exit_signals','scan_detailed_risks']
    for name in funcs: monkeypatch.setattr(scanner,name,lambda:{'alerts_created':0,'skipped':'disabled'})
    called=[]
    def detailed():
        called.append(True)
        return {'generated':0,'updated':0,'resolved':1,'failed':0,'skipped':0,
                'status':'complete','coverage':{'drawdown_alert':{'expected':1,'checked':1,'status':'complete'}}}
    monkeypatch.setattr(scanner,'scan_detailed_risks',detailed)
    result=scanner.run_periodic_scan()
    assert called==[True]
    assert result['resolved']==1
    assert result['coverage']['detailed']['status']=='complete'
