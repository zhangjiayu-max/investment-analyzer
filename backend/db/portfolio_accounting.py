"""持仓现金流水、对账调整与可审计真实收益；初始化只加结构，不改历史余额。"""
import json
import math
from datetime import date

from db._conn import _get_conn


def init_portfolio_accounting_schema(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(portfolio_transactions)')}
    for name, definition in {
        'funding_source': "TEXT NOT NULL DEFAULT 'legacy'",
        'cash_account': 'TEXT',
        'cash_settlement_state': "TEXT NOT NULL DEFAULT 'legacy'",
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE portfolio_transactions ADD COLUMN {name} {definition}')
    conn.execute('''CREATE TABLE IF NOT EXISTS portfolio_cash_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
        account TEXT NOT NULL, tx_id INTEGER, event_type TEXT NOT NULL,
        amount REAL NOT NULL, balance_after REAL, event_key TEXT UNIQUE,
        detail TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_cash_ledger_scope ON portfolio_cash_ledger(user_id, account, id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS portfolio_holding_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, holding_id INTEGER NOT NULL,
        user_id TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        included_tx_ids TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '手动对账调整',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_holding_adjustments ON portfolio_holding_adjustments(holding_id, id)')


def resolve_cash_account(user_id, holding_account=None, requested=None):
    """资金账户归属由固定家庭成员/用户身份限定，持仓标签不能越权引用他人现金。"""
    if user_id == 'default':
        account = requested or holding_account or 'default'
        if account not in ('花无缺', '小鱼儿', 'default'):
            raise ValueError('请明确选择所属家庭现金账户')
        return account
    if requested and requested != user_id:
        raise ValueError('现金账户不属于当前用户')
    return user_id


def cash_event(conn, user_id, account, event_type, amount, *, tx_id=None, key=None,
               changes_balance=False, detail=None):
    """与成交共用事务；唯一事件键防止重复入账。冻结/应收仅记流水。"""
    if key and conn.execute('SELECT 1 FROM portfolio_cash_ledger WHERE event_key=?', (key,)).fetchone():
        return False
    if changes_balance:
        # 首笔新账创建家庭分账户时，保留已知旧家庭余额，不能因新账户出现而消失。
        if user_id == 'default' and account in ('花无缺','小鱼儿'):
            existing = conn.execute("SELECT 1 FROM portfolio_cash WHERE user_id IN ('花无缺','小鱼儿') LIMIT 1").fetchone()
            legacy = conn.execute("SELECT balance FROM portfolio_cash WHERE user_id='default'").fetchone()
            if not existing and legacy and legacy['balance']:
                cash_event(conn,'default','default','legacy_cash_retained',0,
                           key='cash:default:legacy_retained',detail={'reason':'新账新增分账户，旧家庭余额未迁移'})
        conn.execute('''INSERT INTO portfolio_cash(user_id,balance,updated_at)
            VALUES (?, ?, datetime('now','localtime'))
            ON CONFLICT(user_id) DO UPDATE SET balance=balance+excluded.balance,
                updated_at=excluded.updated_at''', (account, amount))
    row = conn.execute('SELECT balance FROM portfolio_cash WHERE user_id=?', (account,)).fetchone()
    conn.execute('''INSERT INTO portfolio_cash_ledger
        (user_id,account,tx_id,event_type,amount,balance_after,event_key,detail)
        VALUES (?,?,?,?,?,?,?,?)''',
        (user_id, account, tx_id, event_type, round(amount, 8), row['balance'] if row else 0, key,
         json.dumps(detail, ensure_ascii=False) if detail else None))
    return True


def frozen_for_account(conn, user_id, account, excluding_tx=None):
    row = conn.execute('''SELECT COALESCE(SUM(submitted_amount),0) AS frozen
        FROM portfolio_transactions WHERE user_id=? AND cash_account=?
        AND transaction_type='buy' AND status='pending' AND funding_source='internal'
        AND (? IS NULL OR id<>?)''', (user_id, account, excluding_tx, excluding_tx)).fetchone()
    return float(row['frozen'])


def available_for_account(conn, user_id, account, excluding_tx=None):
    row = conn.execute('SELECT balance FROM portfolio_cash WHERE user_id=?', (account,)).fetchone()
    return float(row['balance'] if row else 0) - frozen_for_account(conn, user_id, account, excluding_tx)


def list_cash_ledger(user_id='default', account=None, limit=200):
    conn = _get_conn()
    try:
        rows = conn.execute('''SELECT * FROM portfolio_cash_ledger WHERE user_id=?'''
                            + (' AND account=?' if account else '') + ' ORDER BY id DESC LIMIT ?',
                            (user_id, account, limit) if account else (user_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _xirr(flows):
    """已知完整真实现金流的资金加权收益；只接受存在唯一经济根的常规流。"""
    merged = {}
    try:
        for day, amount in flows:
            d = date.fromisoformat(str(day)[:10])
            merged[d] = merged.get(d, 0) + amount
    except (ValueError, TypeError):
        return {'value': None, 'reason': '现金流日期缺失或无效'}
    nonzero = sorted((d, amount) for d, amount in merged.items() if abs(amount) > 1e-8)
    if len(nonzero) < 2 or nonzero[0][0] == nonzero[-1][0]:
        return {'value': None, 'reason': '至少需要跨日期的投入与回收现金流'}
    if not any(a < 0 for _, a in nonzero) or not any(a > 0 for _, a in nonzero):
        return {'value': None, 'reason': '现金流缺少投入或回收'}
    signs = [a > 0 for _, a in nonzero]
    if sum(a != b for a, b in zip(signs, signs[1:])) != 1:
        return {'value': None, 'reason': '非传统现金流可能有多个收益率解'}
    start = nonzero[0][0]
    def npv(rate):
        return sum(a / ((1 + rate) ** ((d - start).days / 365)) for d, a in nonzero)
    low, high = -.9999, 1000.0
    try:
        lv, hv = npv(low), npv(high)
        if lv * hv > 0:
            return {'value': None, 'reason': '在合理搜索范围内无收益率解'}
        for _ in range(180):
            mid = (low + high) / 2
            mv = npv(mid)
            if abs(mv) < 1e-8:
                break
            if lv * mv <= 0:
                high = mid
            else:
                low, lv = mid, mv
        return {'value': round(mid, 8), 'reason': None}
    except (OverflowError, ZeroDivisionError):
        return {'value': None, 'reason': '现金流不足以稳定计算收益率'}


def get_portfolio_performance(user_id='default', account=None, *, conn=None, holdings=None):
    """真实成交收益。浮盈亏、已实现和分红分列，费用已含于净成交，避免二次扣减。"""
    owned = conn is None
    conn = conn if conn is not None else _get_conn()
    try:
        holdings = holdings if holdings is not None else [dict(r) for r in conn.execute(
            'SELECT * FROM portfolio_holdings WHERE user_id=?' + (' AND account=?' if account else ''),
            (user_id, account) if account else (user_id,)).fetchall()]
        realized = dividends = fees = unrealized = 0.0
        complete = True
        issues = []
        flows = []
        for h in holdings:
            txs = [dict(r) for r in conn.execute('''SELECT * FROM portfolio_transactions
                WHERE holding_id=? AND (status IN ('confirmed','settled') OR status IS NULL)
                AND COALESCE(is_hypothetical,0)=0
                ORDER BY transaction_date, COALESCE(transaction_time,'00:00'), id''', (h['id'],))]
            adjustments = [dict(r) for r in conn.execute('SELECT * FROM portfolio_holding_adjustments WHERE holding_id=? ORDER BY id', (h['id'],))]
            if adjustments or h.get('has_base_position') or not txs:
                complete = False
                issues.append({'holding_id': h['id'], 'reason': '存在期初导入/对账调整，完整历史现金流未知'})
            shares = float(h.get('base_shares') or 0) if h.get('has_base_position') else 0.0
            cost = float(h.get('base_total_cost') or 0) if h.get('has_base_position') else 0.0
            replay = []
            included = set()
            for adjustment in adjustments:
                captured = set(json.loads(adjustment['included_tx_ids']))
                replay.extend(tx for tx in txs if tx['id'] in captured and tx['id'] not in included)
                included.update(captured)
                replay.append({'_adjustment': json.loads(adjustment['after_json'])})
            replay.extend(tx for tx in txs if tx['id'] not in included)
            for tx in replay:
                if '_adjustment' in tx:
                    shares = float(tx['_adjustment'].get('shares') or 0)
                    cost = float(tx['_adjustment'].get('total_cost') or 0)
                    continue
                if h.get('has_base_position') and tx.get('is_system'):
                    continue
                kind, amount, qty = tx['transaction_type'], float(tx['amount'] or 0), float(tx.get('shares') or 0)
                fees += float(tx.get('fee') or 0)
                if tx.get('is_system') and (amount > 0 or qty > 0):
                    complete = False
                    issues.append({'holding_id': h['id'], 'reason': '期初建仓记录不代表完整实际入金现金流'})
                if kind == 'buy':
                    if qty <= 0 and amount > 0 and (tx.get('price') or 0) > 0:
                        qty = amount / tx['price']
                    shares += qty
                    cost += amount
                    flows.append((tx['transaction_date'], -amount))
                elif kind in ('sell', 'convert'):
                    sold_cost = cost / shares * qty if shares > 0 else 0
                    if qty > shares + 1e-6:
                        complete = False
                        issues.append({'holding_id': h['id'], 'reason': '成交份额与历史持仓无法核对'})
                    realized += amount - sold_cost
                    cost -= sold_cost
                    shares -= qty
                    flows.append((tx['transaction_date'], amount))
                elif kind == 'dividend':
                    dividends += amount
                    flows.append((tx['transaction_date'], amount))
            if float(h.get('shares') or 0) > 0:
                if h.get('current_value') is None or not h.get('current_price'):
                    complete = False
                    issues.append({'holding_id': h['id'], 'reason': '持仓缺少可靠净值'})
                else:
                    unrealized += float(h['current_value']) - float(h.get('total_cost') or 0)
                    flows.append((h.get('price_updated_at') or h.get('last_update'), float(h['current_value'])))
        valuation_complete = all(float(h.get('shares') or 0) <= 0 or (h.get('current_value') is not None and (h.get('current_price') or 0) > 0) for h in holdings)
        xirr = _xirr(flows) if complete else {'value': None, 'reason': '完整入金、成交、对账或净值历史不足'}
        return {'basis': 'actual_confirmed_transactions',
                'unrealized_profit': round(unrealized, 2) if valuation_complete else None,
                'realized_profit': round(realized, 2), 'cash_dividends': round(dividends, 2),
                'fees': round(fees, 2), 'fees_already_included': True,
                'cumulative_investment_profit': round(unrealized + realized + dividends, 2) if valuation_complete else None,
                'history_complete': complete, 'coverage_issues': issues, 'xirr': xirr}
    finally:
        if owned:
            conn.close()
