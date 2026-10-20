"""复查只认真实确认成交，固定期限不偷看未来价格。"""
import json
from datetime import datetime, timedelta
import pytest
from db._conn import _get_conn


def seed():
    from db.decision_risk import save_decision_review
    plan = dict(status='allowed', fund_code='E', executable_amount=1000, snapshot_id='s', policy_version=1,
                as_of='2026-01-01T09:00:00+08:00', expires_at='2026-01-01T10:00:00+08:00')
    rid = save_decision_review(plan)
    c = _get_conn()
    c.execute("UPDATE portfolio_decision_reviews SET created_at='2026-01-01T09:00:00+08:00' WHERE id=?", (rid,))
    tx = c.execute("INSERT INTO portfolio_transactions(fund_code,transaction_type,amount,shares,price,transaction_date,status,fee,confirmed_at) VALUES('E','buy',1000,990,1,'2026-01-01','confirmed',10,'2026-01-02')").lastrowid
    c.commit(); c.close()
    return rid,tx


def test_saved_plan_is_not_execution():
    from db.decision_risk import list_decision_reviews
    rid,_ = seed()
    review = list_decision_reviews()[0]
    assert review['id']==rid and review['status']=='planned'
    assert review['transaction_id'] is None


def test_real_execution_link_validated_and_not_reused():
    from db.decision_risk import link_decision_execution, save_decision_review
    rid,tx = seed()
    link_decision_execution(rid,tx)
    with pytest.raises(ValueError):
        link_decision_execution(rid,tx+100)
    rid2=save_decision_review(dict(status='allowed',fund_code='E',executable_amount=1000,snapshot_id='b',policy_version=1))
    with pytest.raises(ValueError):
        link_decision_execution(rid2,tx)


def test_hypothetical_and_pending_are_not_real_execution():
    from db.decision_risk import link_decision_execution
    rid,tx=seed(); c=_get_conn()
    c.execute("UPDATE portfolio_transactions SET status='pending' WHERE id=?",(tx,));c.commit();c.close()
    with pytest.raises(ValueError): link_decision_execution(rid,tx)
    c=_get_conn();c.execute("UPDATE portfolio_transactions SET status='confirmed',is_hypothetical=1 WHERE id=?",(tx,));c.commit();c.close()
    with pytest.raises(ValueError): link_decision_execution(rid,tx)


def test_outcome_waits_for_maturity_and_never_uses_future_nav():
    from db.decision_risk import link_decision_execution, review_decision_outcome
    rid,tx=seed();link_decision_execution(rid,tx)
    assert review_decision_outcome(rid,today='2026-01-10')['status']=='pending_maturity'
    c=_get_conn()
    c.executemany("INSERT INTO fund_nav_history(fund_code,nav_date,nav) VALUES('E',?,?)", [('2026-01-05',.9),('2026-01-31',.8),('2026-02-05',1.5)])
    c.commit();c.close()
    outcome=review_decision_outcome(rid,today='2026-02-10')
    assert outcome['nav_date']=='2026-01-31'
    assert outcome['terminal_wealth']==792
    assert outcome['profit_loss']==-208
    assert outcome['max_loss_amount']==208
    assert outcome['basis']=='linked_execution_without_subsequent_trades'


def test_missing_due_nav_does_not_substitute_old_nav():
    from db.decision_risk import link_decision_execution, review_decision_outcome
    rid,tx=seed();link_decision_execution(rid,tx)
    c=_get_conn();c.execute("INSERT INTO fund_nav_history(fund_code,nav_date,nav) VALUES('E','2026-01-05',1)");c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='pending_data'


def test_intervening_trade_requires_reconciliation_not_fake_hold_return():
    from db.decision_risk import link_decision_execution, review_decision_outcome
    rid,tx=seed();link_decision_execution(rid,tx)
    c=_get_conn();c.execute("INSERT INTO portfolio_transactions(fund_code,transaction_type,amount,transaction_date,status) VALUES('E','sell',200,'2026-01-10','confirmed')");c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='needs_reconciliation'


def test_trade_before_plan_same_day_is_not_execution():
    from db.decision_risk import link_decision_execution
    rid,tx=seed();c=_get_conn();c.execute("UPDATE portfolio_transactions SET transaction_time='08:00' WHERE id=?",(tx,));c.commit();c.close()
    with pytest.raises(ValueError):link_decision_execution(rid,tx)


def test_linked_execution_mutation_requires_reconciliation():
    from db.decision_risk import link_decision_execution,review_decision_outcome
    rid,tx=seed();link_decision_execution(rid,tx)
    c=_get_conn();c.execute("UPDATE portfolio_transactions SET shares=1500 WHERE id=?",(tx,));c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='needs_reconciliation'


def test_settled_sale_during_horizon_requires_reconciliation():
    from db.decision_risk import link_decision_execution,review_decision_outcome
    rid,tx=seed();link_decision_execution(rid,tx)
    c=_get_conn();c.execute("INSERT INTO portfolio_transactions(fund_code,transaction_type,amount,transaction_date,status) VALUES('E','sell',200,'2026-01-10','settled')");c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='needs_reconciliation'


def test_later_reconciliation_replaces_previously_published_result():
    from db.decision_risk import link_decision_execution,review_decision_outcome,list_decision_reviews
    rid,tx=seed();link_decision_execution(rid,tx)
    c=_get_conn();c.execute("INSERT INTO fund_nav_history(fund_code,nav_date,nav) VALUES('E','2026-01-31',1)");c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='reviewed'
    c=_get_conn();c.execute("INSERT INTO portfolio_transactions(fund_code,transaction_type,amount,transaction_date,status) VALUES('E','sell',200,'2026-01-10','settled')");c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='needs_reconciliation'
    row=list_decision_reviews()[0]
    assert row['status']=='needs_reconciliation'
    assert row['outcome']['status']=='needs_reconciliation'
    assert row['reviewed_at'] is None


def test_changed_execution_status_retracts_old_review():
    from db.decision_risk import link_decision_execution,review_decision_outcome,list_decision_reviews
    rid,tx=seed();link_decision_execution(rid,tx)
    c=_get_conn();c.execute("INSERT INTO fund_nav_history(fund_code,nav_date,nav) VALUES('E','2026-01-31',1)");c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='reviewed'
    c=_get_conn();c.execute("UPDATE portfolio_transactions SET status='pending' WHERE id=?",(tx,));c.commit();c.close()
    assert review_decision_outcome(rid,today='2026-02-10')['status']=='needs_reconciliation'
    assert list_decision_reviews()[0]['status']=='needs_reconciliation'
