"""建议跟踪：下一可知净值、有限现金顺序扣款，不与真实组合叠加。"""
import json
import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)


def _get_fund_price_at_date(fund_code: str, target_date: str) -> float | None:
    """仅返回指定净值日，禁止用更早的旧价格伪造该日成交。"""
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        row = conn.execute('SELECT nav FROM fund_nav_history WHERE fund_code=? AND nav_date=? AND nav_date<=?', (fund_code, target_date, date.today().isoformat())).fetchone()
        return float(row['nav']) if row and row['nav'] else None
    finally:
        conn.close()


def _get_next_nav_after(fund_code, recommendation_date):
    # 情景成交仅在建议后最多三个工作日内；不能等几个月再用后来的价格假装执行。
    try:
        end = date.fromisoformat(recommendation_date[:10])
    except (ValueError, TypeError):
        return None, None
    for _ in range(3):
        end += timedelta(days=1)
        while end.weekday() >= 5:
            end += timedelta(days=1)
    end = min(end, date.today())
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        row = conn.execute('SELECT nav, nav_date FROM fund_nav_history WHERE fund_code=? AND nav_date>? AND nav_date<=? AND nav>0 ORDER BY nav_date LIMIT 1', (fund_code, recommendation_date[:10], end.isoformat())).fetchone()
        return (float(row['nav']), row['nav_date']) if row else (None, None)
    finally:
        conn.close()


def _get_latest_fund_price(fund_code: str) -> tuple[float | None, str | None]:
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        row = conn.execute('SELECT nav, nav_date FROM fund_nav_history WHERE fund_code=? AND nav_date<=? AND nav>0 ORDER BY nav_date DESC LIMIT 1', (fund_code, date.today().isoformat())).fetchone()
        return (float(row['nav']), row['nav_date']) if row else (None, None)
    finally:
        conn.close()


def _calc_holding_days(buy_date, end_date):
    try:
        return max(0, (datetime.fromisoformat(end_date[:10]) - datetime.fromisoformat(buy_date[:10])).days)
    except (ValueError, TypeError):
        return 0


def _metadata(tx):
    try:
        return json.loads(tx.get('notes') or '{}')
    except (ValueError, TypeError):
        return {}


def verify_hypothetical_tx(tx: dict) -> dict:
    """从建议日之后的首个已存在净值评估，缺未来数据则待评估。"""
    fund, day = tx.get('fund_code', ''), tx.get('transaction_date', '')
    amount = float(tx.get('amount') or 0)
    result = dict(tx_id=tx['id'], fund_code=fund, fund_name=tx.get('fund_name', ''), buy_date=day,
                  buy_amount=amount, status='pending_evaluation', message='等待建议日之后首个可知净值；未真实成交',
                  mode='suggestion_tracking')
    buy_price, execution_day = _get_next_nav_after(fund, day)
    price, nav_day = _get_latest_fund_price(fund)
    if not buy_price or not price or not nav_day or nav_day < execution_day:
        return result
    fee_rate = float(_metadata(tx).get('fee_rate', 0) or 0)
    shares = amount / (1 + fee_rate) / buy_price
    value = round(shares * price, 2)
    pnl = round(value - amount, 2)
    result.update(status='verified', message='有限现金账本内的建议情景；非真实成交', execution_date=execution_day,
        buy_price=round(buy_price, 6), buy_shares=round(shares, 4), current_price=price, current_nav_date=nav_day,
        current_value=value, profit_loss=pnl, profit_rate=pnl / amount if amount else 0,
        profit_rate_pct=pnl / amount * 100 if amount else 0, holding_days=_calc_holding_days(execution_day, nav_day),
        is_breakeven=pnl >= 0, matched_suggestion=abs((tx.get('snapshot_suggested_amount') or amount) - amount) < .01,
        snapshot_suggested_amount=tx.get('snapshot_suggested_amount'), snapshot_profit_rate=tx.get('profit_rate_at_snapshot'),
        fees=round(amount * fee_rate / (1 + fee_rate), 2), avg_cost_analysis=None,
        verified_at=datetime.now().isoformat(timespec='seconds'))
    return result


def verify_all_hypothetical(user_id: str = 'default') -> dict:
    """同一用户固定期初虚拟现金；顺序扣除、事件去重，不重复叠加真实仓位。"""
    from db.smart_add_snapshots import list_hypothetical_txs
    txs = sorted(list_hypothetical_txs(user_id=user_id), key=lambda tx: (tx.get('transaction_date', ''), tx['id']))
    initial_cash, cash, verified, seen, waiting = None, None, [], set(), False
    for tx in txs:
        metadata = _metadata(tx)
        if initial_cash is None and metadata.get('shadow_cash_budget') is not None:
            initial_cash = max(0., float(metadata['shadow_cash_budget']))
            cash = initial_cash
        result = verify_hypothetical_tx(tx)
        event = metadata.get('event_key') or f"legacy:{tx['id']}"
        if metadata.get('mode') != 'suggestion_tracking' or metadata.get('shadow_cash_budget') is None:
            result.update(status='missing_budget_evidence', message='旧记录缺少期初资金证据，仅保留建议，未计入账本')
        elif event in seen:
            result.update(status='duplicate_event', message='同一策略事件已登记，不重复执行')
        elif waiting:
            result.update(status='pending_evaluation', message='较早建议尚无成交净值，等待完整顺序时间线')
        elif result['status'] != 'verified':
            waiting = True
        elif result['buy_amount'] > cash:
            result.update(status='insufficient_shadow_cash', message='固定期初虚拟现金不足，不追加资金')
        else:
            cash = round(cash - result['buy_amount'], 2)
            seen.add(event)
        verified.append(result)
    valid = [r for r in verified if r['status'] == 'verified']
    invested = round(sum(r['buy_amount'] for r in valid), 2)
    value = round(sum(r['current_value'] for r in valid), 2)
    wealth = round(value + (cash or 0), 2) if initial_cash is not None else None
    pnl = round(wealth - initial_cash, 2) if wealth is not None else 0
    summary = dict(total_hypothetical_invested=invested, total_hypothetical_value=value,
        initial_shadow_cash=initial_cash, remaining_shadow_cash=cash, total_shadow_wealth=wealth,
        total_profit_loss=pnl, total_profit_rate=pnl / initial_cash if initial_cash else 0,
        total_profit_rate_pct=pnl / initial_cash * 100 if initial_cash else 0,
        total_count=len(valid), pending_count=sum(r['status'] == 'pending_evaluation' for r in verified),
        breakeven_count=sum(r.get('is_breakeven', False) for r in valid), suggestion_match_rate=1 if valid else 0,
        fees=round(sum(r.get('fees', 0) for r in valid), 2), valuation_dates=sorted(set(r['current_nav_date'] for r in valid)),
        note='建议跟踪使用固定期初虚拟现金，未计分红/现金利息；不代表真实组合收益，也不与真实持仓相加')
    summary['status'] = 'evaluated' if valid else 'pending_evaluation'
    if len(summary['valuation_dates']) > 1:
        summary.update(status='waiting_common_valuation_date', total_shadow_wealth=None,
                       total_hypothetical_value=None, total_profit_loss=None,
                       total_profit_rate=None, total_profit_rate_pct=None)
        summary['note'] += '；各基金净值日期不同，组合财富与收益等待同日数据后评价'
    return dict(hypothetical_txs=list(reversed(verified)), summary=summary,
                comparison=dict(real_portfolio_profit_rate=None, improvement=None, reason='建议跟踪与真实成交分开；无共同财富及同日起点不计算改善率'))
