"""只读、版本化的家庭投前约束。情景损失是假设，不是收益预测。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
import math

from db.decision_risk import DEFAULT_POLICY, get_risk_policy
from services.portfolio.stress_test import category_risk_assumption, scenario_shock

TZ = ZoneInfo('Asia/Shanghai')


def _now():
    return datetime.now(TZ)


def _number(value, default=0.):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _business_age(value, today):
    d = _date(value)
    if d is None or d > today:
        return None
    return sum((d + timedelta(days=i)).weekday() < 5 for i in range(1, (today-d).days+1))


def _equity_factor(fund):
    category = category_risk_assumption(fund.get('fund_category') or '')['risk_category']
    return 0. if category in {'cash', 'money', 'money_market', 'bond', 'bond_index'} else .6 if category in {'hybrid', 'convertible_bond'} else 1.


def _read_facts(user_id):
    from db._conn import _get_conn
    from db.portfolio import get_portfolio_snapshot
    from db.expense import get_cashflow
    conn = _get_conn()
    try:
        conn.execute('BEGIN')
        snapshot = get_portfolio_snapshot(user_id, conn=conn)
        policy = get_risk_policy(user_id, conn=conn)
        cashflow = get_cashflow(user_id, conn=conn)
        buckets = [dict(r) for r in conn.execute("SELECT * FROM goal_buckets WHERE user_id=? AND status='active'", (user_id,))]
        # 只采用本地可核对的主估值指标；缺失留待补齐，不联网凑数。
        valuations = {}
        for row in conn.execute("SELECT * FROM index_valuations ORDER BY snapshot_date DESC,id DESC"):
            item = dict(row)
            if item['metric_type'] in ('市盈率', 'PE', '市净率', 'PB'):
                code = item['index_code'].split('.')[0]
                valuations.setdefault(code, item)
        # 同连接读取活动风险，避免调用另一个事务取得较新的部分事实。
        alerts = [dict(r) for r in conn.execute("SELECT * FROM portfolio_alerts WHERE user_id=? AND (risk_status IN ('active','in_progress','recurred') OR risk_status IS NULL)", (user_id,))]
        return snapshot, cashflow, policy, buckets, valuations, alerts
    finally:
        conn.close()


def build_decision_snapshot(user_id='default', *, snapshot=None, cashflow=None, policy=None,
                            buckets=None, valuation_facts=None, now=None, active_risks=None):
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    if snapshot is None:
        snapshot, cf, pol, bs, vs, risks = _read_facts(user_id)
        cashflow = cf if cashflow is None else cashflow
        policy = pol if policy is None else policy
        buckets = bs if buckets is None else buckets
        valuation_facts = vs if valuation_facts is None else valuation_facts
        active_risks = risks if active_risks is None else active_risks
    snapshot = deepcopy(snapshot)
    cashflow, buckets = cashflow or {}, buckets or []
    policy = {**DEFAULT_POLICY, **(policy or {})}
    funds = deepcopy(snapshot.get('funds') or [])
    balance = max(0., _number(snapshot.get('cash_balance')))
    frozen = max(0., _number(snapshot.get('frozen_cash')))
    receivable = max(0., _number(snapshot.get('receivable_cash')))
    protected = max(0., _number(cashflow.get('protected_cash')))
    near_term = sum(max(0., _number(b.get('current_amount'))) for b in buckets
                    if b.get('bucket_type') == 'stable' and b.get('liquidity_days') is not None
                    and 0 <= _number(b['liquidity_days']) <= policy['future_cash_days'])
    cash = dict(balance=balance, frozen=frozen, receivable=receivable, protected=protected,
                near_term_reserved=near_term, available_now=round(max(0., balance-frozen-protected-near_term),2),
                future_monthly_surplus=max(0., _number(cashflow.get('investable_surplus'))))
    issues = []
    if not policy.get('confirmed'):
        issues.append('家庭风险政策尚未确认，请先核对风险上限')
    if not cashflow.get('data_available'):
        issues.append('家庭现金流资料不足，无法确认必要支出与备用金')
    if snapshot.get('valuation_complete') is False:
        issues.append('部分持仓缺少净值，组合风险覆盖不完整')
    for fund in funds:
        records = fund.get('records') or [fund]
        for record in records:
            if record.get('current_value') is None or _number(record.get('current_price')) <= 0:
                issues.append(f"{fund['fund_code']} 缺少有效净值或市值")
            age = _business_age(record.get('price_updated_at'), now.date())
            if age is None or age > policy['nav_max_age_business_days']:
                issues.append(f"{fund['fund_code']} 净值日期缺失、在未来或已过期")
    total = sum(_number(f.get('current_value')) for f in funds) + balance + receivable
    facts = dict(user_id=user_id, funds=funds, cash=cash, policy=policy, buckets=buckets,
                 valuation_facts=valuation_facts or {}, active_risks=active_risks or [], issues=sorted(set(issues)))
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False).encode()).hexdigest()[:24]
    ctx = {**facts, 'snapshot_id': digest, 'as_of': now.isoformat(), 'expires_at': (now+timedelta(hours=1)).isoformat(),
           'policy_version': policy.get('version',0), 'total_assets': round(total,2), 'proposed_additions': {},
           'status': 'wait_for_data' if issues else 'ready',
           'unassessed_factors': ['固定情景非预测；未穿透基金个股、汇率、信用及赎回限制', '净值时效以工作日近似，未接入完整节假日日历'],
           'assumptions': ['混合及可转债权益系数按60%估算；未知类别按权益估算', '应收款计入资产但不能用于今日买入']}
    ctx['overview'] = _projection(ctx, None, 0)
    return ctx


def _projection(ctx, code, amount):
    rate = ctx['policy']['estimated_fee_rate']
    additions = dict(ctx.get('proposed_additions') or {})
    if code:
        additions[code] = _number(additions.get(code)) + amount
    spent = sum(additions.values())
    # gross是含申购费用的扣款金额，采用 gross/(1+fee_rate) 的净申购口径。
    values = {f['fund_code']: _number(f.get('current_value')) + _number(additions.get(f['fund_code']))/(1+rate) for f in ctx['funds']}
    total = ctx['total_assets'] - spent*rate/(1+rate)
    target = next((f for f in ctx['funds'] if f['fund_code'] == code), {})
    index = str(target.get('index_code') or '').split('.')[0]
    index_groups = {}
    for f in ctx['funds']:
        group = str(f.get('index_code') or '').split('.')[0]
        if group:
            index_groups[group] = index_groups.get(group, 0.) + values[f['fund_code']]
    index_value = index_groups.get(index, 0.) if code else max(index_groups.values(), default=0.)
    equity = sum(values[f['fund_code']]*_equity_factor(f) for f in ctx['funds'])
    scenarios = []
    for key, label, scale in [('market_drop_10','市场下跌10%',.5),('market_drop_20','市场下跌20%',1),('market_drop_30','市场下跌30%',1.5),('rate_up','利率上行',1),('liquidity_crunch','流动性冲击',1)]:
        source = key if key in {'rate_up','liquidity_crunch'} else 'market_drop_20'
        loss = sum(-values[f['fund_code']]*scenario_shock(f.get('fund_category') or '', source)*scale for f in ctx['funds'])
        scenarios.append({'key':key,'label':label,'loss_amount':round(loss,2),'loss_pct':round(loss/total*100,4) if total>0 else None})
    return {'total_assets':round(total,2), 'available_cash':round(ctx['cash']['available_now']-spent,2),
            'single_position_pct':values.get(code, max(values.values(),default=0))/total*100 if total>0 else 0,
            'same_index_pct':index_value/total*100 if total>0 else 0,
            'largest_single_position_pct':max(values.values(),default=0)/total*100 if total>0 else 0,
            'largest_index_pct':max(index_groups.values(),default=0)/total*100 if total>0 else 0,
            'equity_pct':equity/total*100 if total>0 else 0,
            'worst_loss_amount':max((s['loss_amount'] for s in scenarios),default=0),
            'worst_loss_pct':max((s['loss_pct'] or 0 for s in scenarios),default=0),
            'scenarios':scenarios, 'gross_purchase':round(amount,2), 'estimated_fee':round(amount*rate/(1+rate),2)}


def _violations(ctx, projection):
    policy = ctx['policy']
    result = []
    if projection['available_cash'] < -.001:
        result.append('今日可执行现金不足；未来收入与应收不能用于本次买入')
    for field, limit, label in [('largest_single_position_pct','max_single_position_pct','单基金集中度'),('largest_index_pct','max_same_index_pct','同指数合并暴露'),('equity_pct','max_equity_pct','权益暴露'),('worst_loss_pct','max_stress_loss_pct','压力损失比例')]:
        if projection[field] > policy[limit] + 1e-8:
            result.append(f'{label}超过家庭风险上限')
    if policy.get('max_stress_loss_amount') is not None and projection['worst_loss_amount'] > policy['max_stress_loss_amount'] + .001:
        result.append('压力损失金额超过家庭可承受预算')
    return result


def evaluate_add_decision(fund_code, requested_amount, user_id='default', *, context=None, now=None):
    if isinstance(requested_amount,bool) or not isinstance(requested_amount,(int,float)) or not math.isfinite(requested_amount) or requested_amount<0:
        raise ValueError('申请金额必须是非负有限数值')
    ctx = context if context is not None else build_decision_snapshot(user_id)
    fund = next((f for f in ctx['funds'] if f['fund_code']==fund_code), None)
    issues = list(ctx['issues'])
    try:
        expires = datetime.fromisoformat(ctx['expires_at'])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=TZ)
        if (now or _now()) >= expires:
            issues.append('风险检查快照已过期，请重新读取净值、现金及家庭政策')
    except (KeyError, TypeError, ValueError):
        issues.append('风险检查有效期缺失，请重新读取资料')
    if fund is None:
        issues.append('未找到可核对的持仓标的，暂不支持无持仓基金投前计算')
    if fund and _equity_factor(fund)>0:
        index = str(fund.get('index_code') or '')
        valuation = ctx['valuation_facts'].get(index) or ctx['valuation_facts'].get(index.split('.')[0])
        d = _date((valuation or {}).get('snapshot_date'))
        today = _date(ctx['as_of'])
        if not valuation or valuation.get('percentile') is None or d is None or d>today or (today-d).days>ctx['policy']['valuation_max_age_days']:
            issues.append('权益估值证据缺失或过期，先补齐估值再检查')
    reasons = sorted(set(issues))
    before = _projection(ctx, fund_code, 0)
    amount = 0.
    status = 'wait_for_data' if issues else 'blocked'
    conflict = [a for a in ctx.get('active_risks',[]) if a.get('severity')=='danger' and a.get('related_fund_code', a.get('fund_code')) in (fund_code,'',None) and a.get('alert_type') not in {'concentration_high'}]
    if conflict:
        reasons.append('存在尚未解除的高危风险，请先处理并重新扫描')
    if not issues and not conflict:
        requested = math.floor(requested_amount*100+1e-7)/100
        reasons.extend(_violations(ctx, _projection(ctx,fund_code,requested)))
        # 所有约束同时成立；分币二分且向下取整，不能放宽零余量。
        lo, hi = 0, int(requested*100)
        while lo < hi:
            mid = (lo+hi+1)//2
            if _violations(ctx,_projection(ctx,fund_code,mid/100)):
                hi = mid-1
            else:
                lo = mid
        amount = lo/100
        if _violations(ctx,_projection(ctx,fund_code,amount)):
            amount = 0.
            reasons.extend(_violations(ctx,before))
        status = 'allowed' if amount>0 else 'blocked'
        if amount==0 and not reasons:
            reasons.append('申请金额为零或当前没有可新增额度')
    after = _projection(ctx,fund_code,amount)
    return dict(user_id=user_id,fund_code=fund_code,status=status,requested_amount=requested_amount,
                executable_amount=amount,reasons=list(dict.fromkeys(reasons)),snapshot_id=ctx['snapshot_id'],
                policy_version=ctx['policy_version'],as_of=ctx['as_of'],expires_at=ctx['expires_at'],
                before=before,after=after,comparisons=[dict(key=k,amount=a,**_projection(ctx,fund_code,a)) for k,a in [('no_add',0),('small_add',math.floor(amount*50)/100),('planned_add',amount)]],
                review_conditions=['净值、现金、家庭用途、政策或活动风险变化后必须重新检查','一小时后本检查失效；不构成交易指令'],
                unassessed_factors=ctx['unassessed_factors'])
