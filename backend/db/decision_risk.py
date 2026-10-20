"""家庭风险政策与决策复查档案；读取不创建默认记录。"""
from __future__ import annotations

import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from db._conn import _get_conn

DEFAULT_POLICY = {
    "max_single_position_pct": 25.0,
    "max_equity_pct": 60.0,
    "max_same_index_pct": 40.0,
    "max_stress_loss_amount": None,
    "max_stress_loss_pct": 15.0,
    "nav_max_age_business_days": 3,
    "valuation_max_age_days": 14,
    "future_cash_days": 90,
    "estimated_fee_rate": .0015,
}


def init_decision_risk_tables(conn):
    """只创建结构，不自动确认家庭政策或改写历史决策。"""
    conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_risk_policy (
        user_id TEXT PRIMARY KEY, version INTEGER NOT NULL, policy_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_decision_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'default',
        fund_code TEXT NOT NULL, snapshot_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
        approved_amount REAL NOT NULL, plan_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned',
        transaction_id INTEGER UNIQUE, execution_json TEXT, outcome_json TEXT,
        horizon_days INTEGER NOT NULL DEFAULT 30, created_at TEXT NOT NULL, reviewed_at TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_reviews_user ON portfolio_decision_reviews(user_id,id DESC)")


def get_risk_policy(user_id="default", *, conn=None) -> dict:
    owned = conn is None
    c = conn if conn is not None else _get_conn()
    try:
        row = c.execute("SELECT * FROM portfolio_risk_policy WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {**DEFAULT_POLICY, "version": 0, "confirmed": False, "source": "unconfirmed_defaults", "updated_at": None}
        return {**DEFAULT_POLICY, **json.loads(row["policy_json"]), "version": row["version"],
                "confirmed": True, "source": "user_confirmed", "updated_at": row["updated_at"]}
    finally:
        if owned:
            c.close()


def _validate_policy(policy):
    for key in DEFAULT_POLICY:
        value = policy.get(key)
        if key == "max_stress_loss_amount" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} 必须为有限数值")
        if key.endswith("_pct") and not 0 < value <= 100:
            raise ValueError(f"{key} 必须大于0且不超过100%")
        if key == "max_stress_loss_amount" and value <= 0:
            raise ValueError("可承受压力损失金额必须大于0")
        if key.endswith("_days") and (not 0 < value <= 365 or int(value) != value):
            raise ValueError(f"{key} 必须为1至365的整数")
        if key == "estimated_fee_rate" and not 0 <= value <= .05:
            raise ValueError("预估费率必须是0至0.05之间的比例")


def save_risk_policy(fields: dict, user_id="default", *, expected_version: int) -> dict:
    if set(fields) - set(DEFAULT_POLICY):
        raise ValueError("包含不支持的风险政策字段")
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = get_risk_policy(user_id, conn=conn)
        if current["version"] != expected_version:
            raise ValueError("风险政策已经更新，请刷新后重新核对")
        policy = {key: fields.get(key, current[key]) for key in DEFAULT_POLICY}
        _validate_policy(policy)
        version = current["version"] + 1
        now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        conn.execute("INSERT INTO portfolio_risk_policy(user_id,version,policy_json,updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,policy_json=excluded.policy_json,updated_at=excluded.updated_at",
                     (user_id, version, json.dumps(policy, ensure_ascii=False), now))
        conn.commit()
        return get_risk_policy(user_id, conn=conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_decision_review(plan: dict, user_id="default", horizon_days=30) -> int:
    """显式保存已审批计划；预览不进入样本，保存也不等于成交。"""
    if plan.get("status") != "allowed" or not plan.get("executable_amount"):
        raise ValueError("只有通过风险检查的正金额计划可以保存")
    if not isinstance(horizon_days, int) or not 1 <= horizon_days <= 365:
        raise ValueError("复查期限须为1至365天")
    conn = _get_conn()
    try:
        cur = conn.execute("INSERT INTO portfolio_decision_reviews "
                           "(user_id,fund_code,snapshot_id,policy_version,approved_amount,plan_json,horizon_days,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?)", (
                               user_id, plan["fund_code"], plan["snapshot_id"], plan["policy_version"],
                               plan["executable_amount"], json.dumps(plan, ensure_ascii=False, allow_nan=False),
                               horizon_days, datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_decision_reviews(user_id="default", limit=50) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM portfolio_decision_reviews WHERE user_id=? ORDER BY id DESC LIMIT ?",
                            (user_id, max(1, min(int(limit), 200)))).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("plan_json", "execution_json", "outcome_json"):
                raw = item.pop(key)
                item[key.removesuffix("_json")] = json.loads(raw) if raw else None
            result.append(item)
        return result
    finally:
        conn.close()


def link_decision_execution(review_id, transaction_id, user_id='default'):
    """关联既有真实成交；保留超额/过期偏差，不修改成交账本。"""
    import sqlite3
    conn = _get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM portfolio_decision_reviews WHERE id=? AND user_id=?', (review_id,user_id)).fetchone()
        tx = conn.execute('SELECT * FROM portfolio_transactions WHERE id=? AND user_id=?', (transaction_id,user_id)).fetchone()
        if not row or not tx:
            raise ValueError('计划或成交记录不存在')
        plan, tx = dict(row), dict(tx)
        if plan['transaction_id'] is not None:
            raise ValueError('计划已经关联实际成交，不能覆盖')
        if tx['fund_code'] != plan['fund_code'] or tx['transaction_type']!='buy' or tx['status']!='confirmed' or tx.get('is_hypothetical') or tx.get('is_system'):
            raise ValueError('只允许关联同用户、同基金的真实已确认买入')
        if tx['transaction_date'][:10] < plan['created_at'][:10] or not tx.get('shares') or tx['shares']<=0 or not tx.get('price') or tx['price']<=0:
            raise ValueError('成交早于计划或缺少有效成交份额和价格')
        facts = json.loads(plan['plan_json'])
        if tx.get('transaction_time') and f"{tx['transaction_date'][:10]}T{tx['transaction_time']}" < plan['created_at'][:16]:
            raise ValueError('成交下单时刻早于计划，不能作为计划执行')
        deviations=[]
        if tx['amount'] > plan['approved_amount']+.01:
            deviations.append('实际投入超过批准金额')
        # 交易没有可靠时刻时不能声称在一小时有效期内。
        if not tx.get('transaction_time'):
            deviations.append('缺少准确下单时刻，无法确认是否在计划有效期内')
        elif facts.get('expires_at') and f"{tx['transaction_date'][:10]}T{tx['transaction_time']}" > facts['expires_at'][:16]:
            deviations.append('实际下单超出计划有效期，应重新检查')
        execution = {k:tx.get(k) for k in ('id','fund_code','amount','shares','price','fee','transaction_date','confirmed_at','account','holding_id')}
        execution['deviations']=deviations
        conn.execute("UPDATE portfolio_decision_reviews SET transaction_id=?,execution_json=?,status='executed' WHERE id=?", (transaction_id,json.dumps(execution,ensure_ascii=False,allow_nan=False),review_id))
        conn.commit()
        return execution
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError('该成交已被其他计划关联，不允许重复计入') from exc
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def review_decision_outcome(review_id, user_id='default', *, today=None):
    """固定成交日起期限复查。后续混合交易须对账，不能伪装成实际持有收益。"""
    from datetime import date, timedelta
    today = date.fromisoformat(today) if today else datetime.now(ZoneInfo('Asia/Shanghai')).date()
    conn=_get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM portfolio_decision_reviews WHERE id=? AND user_id=?',(review_id,user_id)).fetchone()
        if not row:
            raise ValueError('计划不存在')
        record=dict(row)
        def publish_pending(result):
            conn.execute('UPDATE portfolio_decision_reviews SET status=?,outcome_json=?,reviewed_at=NULL WHERE id=?',
                         (result['status'],json.dumps(result,ensure_ascii=False,allow_nan=False),review_id))
            conn.commit()
            return result
        if not record['transaction_id']:
            return {'status':'not_executed','message':'尚未关联真实成交'}
        tx=conn.execute('SELECT * FROM portfolio_transactions WHERE id=? AND user_id=?',(record['transaction_id'],user_id)).fetchone()
        if not tx or tx['status']!='confirmed' or tx['is_hypothetical']:
            return publish_pending({'status':'needs_reconciliation','message':'关联成交已删除或状态发生变化，需先对账'})
        frozen = json.loads(record['execution_json'])
        if any(tx[key] != frozen.get(key) for key in ('fund_code','amount','shares','price','fee','transaction_date','holding_id')):
            result = {'status':'needs_reconciliation','message':'关联成交与保存的执行事实不一致，请先对账'}
            return publish_pending(result)
        start=date.fromisoformat(tx['transaction_date'][:10]); due=start+timedelta(days=record['horizon_days'])
        result={'due_date':due.isoformat(),'basis':'linked_execution_without_subsequent_trades','fees':tx['fee'] or 0}
        if today<due:
            return publish_pending({**result,'status':'pending_maturity','message':'固定复查期限尚未成熟'})
        intervening=conn.execute("SELECT id FROM portfolio_transactions WHERE user_id=? AND fund_code=? AND id<>? AND (status IN ('confirmed','settled') OR status IS NULL) AND COALESCE(is_hypothetical,0)=0 AND transaction_date>=? AND transaction_date<=? LIMIT 1",(user_id,tx['fund_code'],tx['id'],start.isoformat(),due.isoformat())).fetchone()
        if intervening:
            return publish_pending({**result,'status':'needs_reconciliation','message':'期间存在其他买卖、转换或分红，须对账后评价真实结果'})
        navs=conn.execute('SELECT nav_date,nav FROM fund_nav_history WHERE fund_code=? AND nav_date>=? AND nav_date<=? AND nav>0 ORDER BY nav_date',(tx['fund_code'],start.isoformat(),due.isoformat())).fetchall()
        # 周末允许前一工作日；超过三个工作日缺口不采用陈旧净值。
        if not navs or sum((date.fromisoformat(navs[-1]['nav_date'])+timedelta(days=i)).weekday()<5 for i in range(1,(due-date.fromisoformat(navs[-1]['nav_date'])).days+1))>3:
            return publish_pending({**result,'status':'pending_data','message':'固定期限净值缺失或过旧，不使用未来净值替代'})
        cost=float(tx['amount']); units=float(tx['shares'])
        terminal=round(units*navs[-1]['nav'],2)
        wealth=[cost]+[units*n['nav'] for n in navs]
        peak=cost;max_dd=0.;water=0;longest=0
        for value in wealth:
            peak=max(peak,value)
            max_dd=max(max_dd,1-value/peak if peak else 0)
            water=water+1 if value<peak else 0;longest=max(longest,water)
        result.update(status='reviewed',nav_date=navs[-1]['nav_date'],terminal_wealth=terminal,
                      profit_loss=round(terminal-cost,2),max_loss_amount=round(max(0,cost-min(wealth)),2),
                      max_drawdown=max_dd,underwater_observations=longest,unrecovered=terminal<cost,
                      cash_shortfall=None,cash_shortfall_note='成交后家庭流水不完整，不推断真实现金缺口',
                      limitation='仅限此笔成交且期间无其他同基金交易；单位净值未复权分红，样本不用于保证未来收益')
        conn.execute('UPDATE portfolio_decision_reviews SET outcome_json=?,status=?,reviewed_at=? WHERE id=?',(json.dumps(result,ensure_ascii=False,allow_nan=False),'reviewed',datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),review_id))
        conn.commit()
        return result
    finally:
        conn.close()
