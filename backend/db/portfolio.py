"""持仓管理全领域 CRUD（持仓、交易、零钱、调仓、净值、基金信息、预警、标签、分析记录、缓存）。"""

import json
import logging
import sqlite3
from datetime import datetime

from db._conn import _get_conn, _row_to_dict

logger = logging.getLogger(__name__)


# ── 审计日志 ──────────────────────────────────────

def _log_tx_audit(action: str, tx_id: int = None, holding_id: int = None,
                  fund_code: str = None, fund_name: str = None,
                  operator: str = "user",
                  before: dict = None, after: dict = None,
                  input_shares: float = None, input_amount: float = None,
                  input_price: float = None, detail: str = None):
    """记录交易操作审计日志。"""
    try:
        conn = _get_conn()
        conn.execute("""
            INSERT INTO portfolio_tx_audit_log
                (tx_id, holding_id, fund_code, fund_name, action, operator,
                 before_status, before_shares, before_amount, before_price,
                 after_status, after_shares, after_amount, after_price,
                 input_shares, input_amount, input_price, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tx_id, holding_id, fund_code, fund_name, action, operator,
            (before or {}).get("status"), (before or {}).get("shares"),
            (before or {}).get("amount"), (before or {}).get("price"),
            (after or {}).get("status"), (after or {}).get("shares"),
            (after or {}).get("amount"), (after or {}).get("price"),
            input_shares, input_amount, input_price, detail,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")


# ── 持仓管理 CRUD ──────────────────────────────────────


def create_holding(fund_code: str, fund_name: str, shares: float = 0,
                   cost_price: float = None, current_price: float = None,
                   index_code: str = None,
                   index_name: str = None, buy_date: str = None,
                   notes: str = None, user_id: str = "default",
                   account: str = "花无缺", fund_category: str = None) -> int:
    """新增持仓，返回 holding_id。自动分类基金类型（equity/bond/hybrid/money_market/index 等）。"""
    if fund_category is None:
        fund_category = classify_fund_category(fund_name)
    if cost_price is None:
        cost_price = current_price or 0
    total_cost = shares * cost_price
    current_value = shares * current_price if (current_price and current_price > 0) else None
    profit_loss = (current_value - total_cost) if current_value is not None else None
    profit_rate = (profit_loss / total_cost) if (profit_loss is not None and total_cost > 0) else None
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO portfolio_holdings
                (user_id, fund_code, fund_name, index_code, index_name,
                 shares, cost_price, total_cost, buy_date, notes,
                 current_price, current_value, profit_loss, profit_rate, account,
                 fund_category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, fund_code, fund_name, index_code, index_name,
              shares, cost_price, total_cost, buy_date, notes,
              current_price, current_value, profit_loss, profit_rate, account,
              fund_category))
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"基金 {fund_code} 在账户「{account}」中已存在")
    holding_id = cur.lastrowid

    # 同步创建一笔系统买入交易（is_system=1），确保 _recalculate_holding 能正确计算
    tx_date = buy_date or datetime.now().strftime("%Y-%m-%d")
    conn.execute("""
        INSERT INTO portfolio_transactions
            (holding_id, user_id, fund_code, transaction_type, amount, shares, price,
             transaction_date, status, notes, is_system)
        VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, 'confirmed', '初始建仓', 1)
    """, (holding_id, user_id, fund_code, total_cost, shares, cost_price, tx_date))

    conn.commit()
    conn.close()
    return holding_id


def get_holding(holding_id: int) -> dict | None:
    """获取单个持仓。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_holding_by_fund(fund_code: str, user_id: str = "default") -> dict | None:
    """根据基金代码获取持仓。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM portfolio_holdings WHERE fund_code = ? AND user_id = ?",
        (fund_code, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_holdings(user_id: str = "default", account: str = None) -> list[dict]:
    """获取用户所有持仓，可选按账号筛选。"""
    conn = _get_conn()
    if account:
        rows = conn.execute("""
            SELECT * FROM portfolio_holdings
            WHERE user_id = ? AND account = ?
            ORDER BY updated_at DESC
        """, (user_id, account)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM portfolio_holdings
            WHERE user_id = ?
            ORDER BY updated_at DESC
        """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 持仓表允许更新的字段白名单
_HOLDING_ALLOWED_FIELDS = {
    'fund_code', 'fund_name', 'account', 'index_code', 'index_name',
    'shares', 'cost_price', 'total_cost', 'current_price', 'current_value',
    'profit_loss', 'profit_rate', 'buy_date', 'last_update', 'notes',
    'price_updated_at', 'today_change_pct', 'today_profit', 'fund_category',
    'has_base_position', 'last_buy_price', 'last_buy_date', 'updated_at',
}

def update_holding(holding_id: int, **fields):
    """更新持仓字段。自动重算 total_cost / current_value / profit_loss / profit_rate。"""
    if not fields:
        return
    # 字段名白名单校验，防止 SQL 注入
    invalid = set(fields.keys()) - _HOLDING_ALLOWED_FIELDS
    if invalid:
        raise ValueError(f"非法字段名: {invalid}")
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 如果更新了 fund_name 且未指定 fund_category，自动分类
    if "fund_name" in fields and "fund_category" not in fields:
        fields["fund_category"] = classify_fund_category(fields["fund_name"])

    # 如果更新了 shares 或 cost_price，重算 total_cost
    conn = _get_conn()
    current = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not current:
        conn.close()
        return
    current = dict(current)

    shares = fields.get("shares", current.get("shares", 0))
    cost_price = fields.get("cost_price", current.get("cost_price", 0))
    current_price = fields.get("current_price", current.get("current_price"))

    fields["total_cost"] = shares * cost_price
    if current_price is not None and current_price > 0:
        fields["current_value"] = shares * current_price
        fields["profit_loss"] = fields["current_value"] - fields["total_cost"]
        fields["profit_rate"] = fields["profit_loss"] / fields["total_cost"] if fields["total_cost"] > 0 else 0

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [holding_id]
    conn.execute(f"UPDATE portfolio_holdings SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_holding(holding_id: int) -> bool:
    """删除持仓及其交易记录。"""
    # 先查持仓信息用于审计
    conn = _get_conn()
    h = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    h = dict(h) if h else {}

    conn.execute("DELETE FROM portfolio_transactions WHERE holding_id = ?", (holding_id,))
    cur = conn.execute("DELETE FROM portfolio_holdings WHERE id = ?", (holding_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()

    # 审计日志
    if deleted:
        _log_tx_audit(
            action="delete_holding", holding_id=holding_id,
            fund_code=h.get("fund_code"), fund_name=h.get("fund_name"),
            detail=json.dumps({"shares": h.get("shares"), "total_cost": h.get("total_cost")},
                              ensure_ascii=False),
        )

    return deleted


def get_portfolio_summary(user_id: str = "default", account: str = None) -> dict:
    """获取持仓汇总：总市值、总成本、总盈亏、收益率、现金余额、总资产。排除已清仓记录。可选按账号筛选。"""
    holdings = list_holdings(user_id, account=account)
    active = [h for h in holdings if (h.get("shares") or 0) > 0]
    total_cost = sum(h.get("total_cost", 0) or 0 for h in active)
    total_value = sum(h.get("current_value", 0) or 0 for h in active)
    total_profit = total_value - total_cost
    profit_rate = total_profit / total_cost if total_cost > 0 else 0

    # 现金余额
    cash_info = get_cash_balance(user_id)
    cash_balance = cash_info.get("balance", 0) if cash_info else 0
    total_assets = total_value + cash_balance

    # 按基金类型分类统计
    fund_type_breakdown = {}
    for h in active:
        cat = h.get("fund_category") or "equity"
        if cat not in fund_type_breakdown:
            fund_type_breakdown[cat] = {"count": 0, "value": 0, "cost": 0}
        fund_type_breakdown[cat]["count"] += 1
        fund_type_breakdown[cat]["value"] += (h.get("current_value") or 0)
        fund_type_breakdown[cat]["cost"] += (h.get("total_cost") or 0)

    return {
        "holding_count": len(active),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "total_profit": round(total_profit, 2),
        "profit_rate": round(profit_rate, 4),
        "cash_balance": round(cash_balance, 2),
        "total_assets": round(total_assets, 2),
        "fund_type_breakdown": fund_type_breakdown,
        "holdings": holdings,
    }


# ── 零钱账户 ──────────────────────────────────────


def get_cash_balance(user_id: str = "default") -> dict:
    """获取零钱余额（自动触发每日收益结算）。"""
    # 先触发每日收益
    interest_info = accrue_cash_interest(user_id)
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        conn.execute("INSERT INTO portfolio_cash (user_id, balance) VALUES (?, 0)", (user_id,))
        conn.commit()
        result = {"user_id": user_id, "balance": 0, "updated_at": None, "today_interest": 0, "last_interest_date": None}
    else:
        result = dict(row)
    conn.close()
    result["accrued"] = interest_info
    return result


def get_total_cash_balance() -> float:
    """汇总所有账户的零钱余额。"""
    conn = _get_conn()
    row = conn.execute("SELECT COALESCE(SUM(balance), 0) as total FROM portfolio_cash").fetchone()
    conn.close()
    return row["total"] if row else 0


def add_cash(user_id: str, amount: float) -> float:
    """增加（或减少）零钱余额。amount 可为负数（支出）。返回新余额。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO portfolio_cash (user_id, balance, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            balance = balance + ?,
            updated_at = datetime('now','localtime')
    """, (user_id, amount, amount))
    conn.commit()
    row = conn.execute("SELECT balance FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["balance"] if row else 0


def set_cash_balance(user_id: str, balance: float) -> float:
    """直接设置零钱余额（覆盖写入）。返回新余额。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO portfolio_cash (user_id, balance, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            balance = ?,
            updated_at = datetime('now','localtime')
    """, (user_id, balance, balance))
    conn.commit()
    row = conn.execute("SELECT balance FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["balance"] if row else 0


# ── 零钱每日收益 ──────────────────────────────────────


def accrue_cash_interest(user_id: str = "default") -> dict:
    """计算并发放零钱每日收益。每天只会执行一次。返回今日收益信息。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        conn.execute("INSERT INTO portfolio_cash (user_id, balance) VALUES (?, 0)", (user_id,))
        conn.commit()
        conn.close()
        return {"interest": 0, "balance": 0, "date": None}

    cash = dict(row)
    today = datetime.now().strftime("%Y-%m-%d")
    last_date = cash.get("last_interest_date")

    if last_date == today:
        conn.close()
        return {
            "interest": cash.get("today_interest", 0) or 0,
            "balance": cash["balance"],
            "date": today,
            "already_accrued": True,
        }

    balance = cash["balance"]
    if balance <= 0:
        # 余额为0，只更新日期标记，不产生收益
        conn.execute(
            "UPDATE portfolio_cash SET last_interest_date = ?, today_interest = 0 WHERE user_id = ?",
            (today, user_id),
        )
        conn.commit()
        conn.close()
        return {"interest": 0, "balance": 0, "date": today}

    # 每日收益 = 余额 × 年化 / 365
    from config import CASH_ANNUAL_YIELD_7D
    daily_rate = CASH_ANNUAL_YIELD_7D / 365
    interest = round(balance * daily_rate, 2)
    new_balance = round(balance + interest, 2)

    conn.execute("""
        UPDATE portfolio_cash SET
            balance = ?,
            today_interest = ?,
            last_interest_date = ?,
            updated_at = datetime('now','localtime')
        WHERE user_id = ?
    """, (new_balance, interest, today, user_id))
    conn.commit()
    conn.close()
    return {"interest": interest, "balance": new_balance, "date": today, "already_accrued": False}


# ── 调仓策略配置 CRUD ──────────────────────────────────────


def save_rebalance_config(strategy: str, config_json: str, user_id: str = "default", note: str = None) -> int:
    """保存调仓配置（新建版本），返回 id。旧版本自动标记为非活跃。"""
    conn = _get_conn()
    # 将当前活跃配置标记为非活跃
    conn.execute(
        "UPDATE rebalance_config SET is_active = 0 WHERE user_id = ? AND is_active = 1",
        (user_id,),
    )
    cursor = conn.execute(
        "INSERT INTO rebalance_config (user_id, strategy, config_json, is_active, note) VALUES (?, ?, ?, 1, ?)",
        (user_id, strategy, config_json, note),
    )
    config_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return config_id


def get_active_rebalance_config(user_id: str = "default") -> dict | None:
    """获取当前活跃的调仓配置。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM rebalance_config WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    r["config"] = json.loads(r["config_json"])
    return r


def list_rebalance_configs(user_id: str = "default", limit: int = 20) -> list[dict]:
    """列出调仓配置变更历史。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, strategy, is_active, note, created_at FROM rebalance_config WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rebalance_config_by_id(config_id: int) -> dict | None:
    """按 id 获取配置详情。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM rebalance_config WHERE id = ?", (config_id,)).fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    r["config"] = json.loads(r["config_json"])
    return r


def rollback_rebalance_config(config_id: int, user_id: str = "default") -> bool:
    """回滚到指定配置版本（复制为新活跃版本）。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM rebalance_config WHERE id = ? AND user_id = ?",
        (config_id, user_id),
    ).fetchone()
    if not row:
        conn.close()
        return False
    r = dict(row)
    # 标记旧活跃为非活跃
    conn.execute(
        "UPDATE rebalance_config SET is_active = 0 WHERE user_id = ? AND is_active = 1",
        (user_id,),
    )
    # 创建新版本（复制旧配置）
    conn.execute(
        "INSERT INTO rebalance_config (user_id, strategy, config_json, is_active, note) VALUES (?, ?, ?, 1, ?)",
        (user_id, r["strategy"], r["config_json"], f"回滚到版本 #{config_id}"),
    )
    conn.commit()
    conn.close()
    return True


# ── 交易记录 CRUD ──────────────────────────────────────


def create_transaction(fund_code: str, transaction_type: str, amount: float,
                       transaction_date: str, shares: float = None,
                       price: float = None, holding_id: int = None,
                       notes: str = None, user_id: str = "default",
                       status: str = None, submitted_shares: float = None,
                       submitted_amount: float = None,
                       transaction_time: str = None,
                       expected_confirm_date: str = None,
                       fund_name: str = None, account: str = None) -> int:
    """新增交易记录，返回 transaction_id。自动更新持仓数据。

    status: 'pending' | 'confirmed' | None(默认confirmed)
      - pending: 买入时 amount 存入 submitted_amount，卖出时 shares 存入 submitted_shares
      - confirmed: 直接确认，amount/shares/price 存入实际值
    """
    # 确定状态
    if status is None:
        status = 'confirmed'

    if status == 'pending':
        # pending 交易：amount=0, shares=NULL，实际值存 submitted_* 字段
        actual_amount = 0
        actual_shares = None
        actual_price = None
        if transaction_type == 'buy':
            submitted_amount = submitted_amount or amount
        elif transaction_type in ('sell', 'convert'):
            submitted_shares = submitted_shares or shares
    else:
        actual_amount = amount
        actual_shares = shares
        actual_price = price

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_transactions
            (holding_id, user_id, fund_code, fund_name, transaction_type, amount, shares, price,
             transaction_date, notes, status, submitted_shares, submitted_amount,
             transaction_time, expected_confirm_date, account)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (holding_id, user_id, fund_code, fund_name, transaction_type, actual_amount, actual_shares,
          actual_price, transaction_date, notes, status, submitted_shares, submitted_amount,
          transaction_time, expected_confirm_date, account))
    tx_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 只有 confirmed 状态才更新持仓数据
    if holding_id and status in ('confirmed', 'settled'):
        _recalculate_holding(holding_id)

    # 审计日志
    _log_tx_audit(
        action="create", tx_id=tx_id, holding_id=holding_id,
        fund_code=fund_code, fund_name=fund_name,
        after={"status": status, "shares": actual_shares, "amount": actual_amount, "price": actual_price},
        input_shares=submitted_shares or shares,
        input_amount=submitted_amount or amount,
        input_price=price,
        detail=json.dumps({"transaction_type": transaction_type, "transaction_date": transaction_date,
                           "expected_confirm_date": expected_confirm_date, "notes": notes},
                          ensure_ascii=False),
    )

    return tx_id


def list_transactions(fund_code: str = None, holding_id: int = None,
                      user_id: str = "default", limit: int = 100,
                      include_system: bool = False, status: str = None) -> list[dict]:
    """获取交易记录列表。默认不包含系统自动生成的（is_system=1）交易。"""
    conn = _get_conn()
    conditions = ["user_id = ?"]
    params = [user_id]
    if fund_code:
        conditions.append("fund_code = ?")
        params.append(fund_code)
    if holding_id:
        conditions.append("holding_id = ?")
        params.append(holding_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if not include_system:
        conditions.append("(is_system IS NULL OR is_system = 0)")

    where = " AND ".join(conditions)
    params.append(limit)
    rows = conn.execute(f"""
        SELECT * FROM portfolio_transactions
        WHERE {where}
        ORDER BY transaction_date DESC, id DESC
        LIMIT ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _recalculate_holding(holding_id: int):
    """根据交易记录重新计算持仓数据。先处理买入再处理卖出，避免顺序问题。

    对于没有交易记录的持仓（直接导入/手动创建），保留原有数据不覆盖。
    """
    conn = _get_conn()
    holding = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not holding:
        conn.close()
        return
    holding = dict(holding)

    txs = conn.execute("""
        SELECT * FROM portfolio_transactions
        WHERE holding_id = ? AND (status IN ('confirmed', 'settled') OR status IS NULL)
        ORDER BY id ASC
    """, (holding_id,)).fetchall()

    # 如果没有任何已确认的交易，说明持仓是直接创建的，不重新计算
    if not txs:
        conn.close()
        return

    total_shares = 0.0
    total_cost = 0.0

    # 如果持仓有基准数据（直接导入/手动创建的初始持仓），先加入基准
    has_base = holding.get("has_base_position")
    base_shares = holding.get("base_shares") or 0
    print(f"[DEBUG _recalculate_holding] holding_id={holding_id}, has_base_position={has_base}, base_shares={base_shares}, shares={holding.get('shares')}, total_cost={holding.get('total_cost')}, tx_count={len(txs)}")
    if has_base:
        # 优先使用 base_shares（原始基准），避免被重算覆盖
        total_shares = base_shares if base_shares > 0 else (holding.get("shares") or 0)
        total_cost = holding.get("total_cost") or 0

    current_price = holding.get("current_price") or 0

    # 先处理所有买入
    last_buy_price = None
    last_buy_date = None
    for tx in txs:
        tx = dict(tx)
        shares = tx.get("shares", 0) or 0
        amount = tx.get("amount", 0) or 0
        tx_price = tx.get("price") or 0
        if tx["transaction_type"] == "buy" and (shares > 0 or amount > 0):
            # 只有金额没有份额时，用净值自动估算份额
            if shares <= 0 and amount > 0:
                price = tx_price or current_price
                if price > 0:
                    shares = amount / price
            total_shares += shares
            total_cost += amount
            # 跟踪最近一次买入价格和日期
            if tx_price > 0:
                last_buy_price = tx_price
                last_buy_date = tx.get("transaction_date") or ""

    # 再处理所有卖出和转换（按平均成本扣减）
    for tx in txs:
        tx = dict(tx)
        shares = tx.get("shares", 0) or 0
        if tx["transaction_type"] in ("sell", "convert") and shares > 0:
            if total_shares > 0:
                avg_cost = total_cost / total_shares
                total_cost -= avg_cost * shares
            total_shares -= shares
        elif tx["transaction_type"] == "dividend":
            amount = tx.get("amount", 0) or 0
            if amount > 0:
                total_cost -= amount

    if total_shares < 0:
        total_shares = 0

    print(f"[DEBUG _recalculate_holding] RESULT: total_shares={total_shares}, total_cost={total_cost}")
    cost_price = total_cost / total_shares if total_shares > 0 else 0
    current_value = total_shares * current_price if current_price > 0 else None
    profit_loss = (current_value - total_cost) if current_value is not None else None
    profit_rate = (profit_loss / total_cost) if (profit_loss is not None and total_cost > 0) else None

    conn.execute("""
        UPDATE portfolio_holdings SET
            shares = ?, cost_price = ?, total_cost = ?,
            current_value = ?, profit_loss = ?, profit_rate = ?,
            last_buy_price = ?, last_buy_date = ?,
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (total_shares, round(cost_price, 4), round(total_cost, 2),
          round(current_value, 2) if current_value is not None else None,
          round(profit_loss, 2) if profit_loss is not None else None,
          round(profit_rate, 4) if profit_rate is not None else None,
          last_buy_price, last_buy_date,
          holding_id))
    conn.commit()
    conn.close()


def _capture_valuation_snapshot(holding_id: int, transaction_date: str) -> str | None:
    """根据持仓的 index_code 查询交易日期附近的估值数据，返回 JSON 快照。"""
    if not holding_id or not transaction_date:
        return None
    conn = _get_conn()
    holding = conn.execute(
        "SELECT index_code, index_name FROM portfolio_holdings WHERE id = ?",
        (holding_id,)
    ).fetchone()
    if not holding or not holding["index_code"]:
        conn.close()
        return None

    index_code = holding["index_code"]
    index_name = holding["index_name"] or ""

    # 查找交易日期附近 7 天内的 PE 估值
    row = conn.execute("""
        SELECT percentile, snapshot_date FROM index_valuations
        WHERE index_code = ? AND metric_type = '市盈率'
        AND snapshot_date BETWEEN date(?, '-7 days') AND date(?, '+7 days')
        ORDER BY ABS(julianday(snapshot_date) - julianday(?))
        LIMIT 1
    """, (index_code, transaction_date, transaction_date, transaction_date)).fetchone()

    pe_percentile = row["percentile"] if row else None
    pe_date = row["snapshot_date"] if row else None

    # 查找交易日期附近 7 天内的 PB 估值
    row_pb = conn.execute("""
        SELECT percentile FROM index_valuations
        WHERE index_code = ? AND metric_type = '市净率'
        AND snapshot_date BETWEEN date(?, '-7 days') AND date(?, '+7 days')
        ORDER BY ABS(julianday(snapshot_date) - julianday(?))
        LIMIT 1
    """, (index_code, transaction_date, transaction_date, transaction_date)).fetchone()

    pb_percentile = row_pb["percentile"] if row_pb else None
    conn.close()

    if pe_percentile is None and pb_percentile is None:
        return None

    import json
    return json.dumps({
        "index_code": index_code,
        "index_name": index_name,
        "pe_percentile": pe_percentile,
        "pb_percentile": pb_percentile,
        "snapshot_date": pe_date or transaction_date
    }, ensure_ascii=False)


def backfill_valuation_snapshots() -> int:
    """为历史交易回填估值快照。返回更新的记录数。"""
    conn = _get_conn()
    txs = conn.execute("""
        SELECT t.id, t.holding_id, t.transaction_date
        FROM portfolio_transactions t
        JOIN portfolio_holdings h ON t.holding_id = h.id
        WHERE t.status IN ('confirmed', 'settled')
        AND t.valuation_snapshot IS NULL
        AND h.index_code IS NOT NULL AND h.index_code != ''
    """).fetchall()

    updated = 0
    for tx in txs:
        snapshot = _capture_valuation_snapshot(tx["holding_id"], tx["transaction_date"])
        if snapshot:
            conn.execute("UPDATE portfolio_transactions SET valuation_snapshot = ? WHERE id = ?",
                        (snapshot, tx["id"]))
            updated += 1

    conn.commit()
    conn.close()
    return updated


def confirm_transaction(tx_id: int, confirmed_price: float,
                        confirmed_shares: float = None,
                        confirmed_amount: float = None,
                        target_fund_code: str = None,
                        target_fund_name: str = None,
                        fee: float = 0) -> bool:
    """确认交易：填入实际净值，计算实际份额/金额。

    买入：confirmed_shares = (submitted_amount - fee) / confirmed_price
    卖出：confirmed_amount = submitted_shares * confirmed_price - fee
    转换：卖出源基金份额 → 买入目标基金（target_fund_code 必填）
    """
    conn = _get_conn()
    tx = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        conn.close()
        return False
    tx = dict(tx)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tx_type = tx["transaction_type"]
    user_id = tx.get("user_id", "default")
    holding_id = tx.get("holding_id")

    if tx_type == "buy":
        # 买入确认：(金额 - 手续费) / 净值 = 份额
        sub_amount = confirmed_amount or tx.get("submitted_amount") or tx.get("amount") or 0
        if confirmed_price > 0:
            net_amount = sub_amount - fee  # 扣除手续费后的净金额
            actual_shares = round(net_amount / confirmed_price, 2)
        else:
            actual_shares = confirmed_shares or 0
        actual_amount = sub_amount  # 记录总金额（含手续费）
        actual_price = confirmed_price
    elif tx_type == "sell":
        # 卖出确认：份额 × 净值 - 手续费 = 实际到账
        sub_shares = confirmed_shares or tx.get("submitted_shares") or tx.get("shares") or 0
        gross_amount = round(sub_shares * confirmed_price, 2)
        actual_amount = round(gross_amount - fee, 2)  # 扣除赎回费
        actual_shares = sub_shares
        actual_price = confirmed_price
    elif tx_type == "convert":
        # 转换确认：按份额卖出源基金，同时买入目标基金
        sub_shares = confirmed_shares or tx.get("submitted_shares") or tx.get("shares") or 0
        gross_amount = round(sub_shares * confirmed_price, 2)
        actual_amount = round(gross_amount - fee, 2)  # 扣除转换费
        actual_shares = sub_shares
        actual_price = confirmed_price
    else:
        # 分红等其他类型
        actual_amount = confirmed_amount or tx.get("amount") or 0
        actual_shares = confirmed_shares
        actual_price = confirmed_price

    conn.execute("""
        UPDATE portfolio_transactions SET
            status = 'confirmed', amount = ?, shares = ?, price = ?,
            fee = ?, confirmed_at = ?
        WHERE id = ?
    """, (actual_amount, actual_shares, actual_price, fee, now, tx_id))
    conn.commit()
    conn.close()

    # ── 新基金买入：holding_id 为空时自动创建持仓 ──
    if tx_type == "buy" and not holding_id and actual_shares and actual_shares > 0:
        fund_code = tx["fund_code"]
        fund_name = tx.get("fund_name", fund_code)
        existing = get_holding_by_fund(fund_code, user_id)
        if existing:
            holding_id = existing["id"]
            # 更新交易记录关联
            conn2 = _get_conn()
            conn2.execute("UPDATE portfolio_transactions SET holding_id = ? WHERE id = ?", (holding_id, tx_id))
            conn2.commit()
            conn2.close()
        else:
            holding_id = create_holding(
                fund_code=fund_code, fund_name=fund_name,
                shares=actual_shares, cost_price=actual_price,
                current_price=actual_price, user_id=user_id,
                account=tx.get("account", "花无缺"),
            )
            # 更新交易记录关联
            conn2 = _get_conn()
            conn2.execute("UPDATE portfolio_transactions SET holding_id = ? WHERE id = ?", (holding_id, tx_id))
            conn2.commit()
            conn2.close()

    if holding_id:
        _recalculate_holding(holding_id)

    # 捕获交易时点估值快照
    if holding_id:
        snapshot = _capture_valuation_snapshot(holding_id, tx.get("transaction_date", ""))
        if snapshot:
            conn3 = _get_conn()
            conn3.execute("UPDATE portfolio_transactions SET valuation_snapshot = ? WHERE id = ?",
                         (snapshot, tx_id))
            conn3.commit()
            conn3.close()

    # ── 补仓后跌幅即时预警 ──
    if tx_type == "buy" and holding_id and actual_price > 0:
        try:
            from db.config import get_config_int as _get_cfg_int
            buy_drop_pct = _get_cfg_int('alert.buy_drop_pct', 4)
            h = get_holding(holding_id)
            if h:
                cp = h.get("current_price") or 0
                if cp > 0:
                    drop = (actual_price - cp) / actual_price * 100
                    if drop >= buy_drop_pct:
                        fund_code = h.get("fund_code", "")
                        fund_name = h.get("fund_name", fund_code)
                        create_alert(
                            alert_type="buy_drop_alert",
                            title=f"{fund_name} 补仓价已低于净值 {drop:.1f}%",
                            content=f"{fund_name}（{fund_code}）本次买入价 {actual_price:.4f}，当前净值 {cp:.4f}，已低于买入价 {drop:.1f}%（阈值 {buy_drop_pct}%）。",
                            severity="danger" if drop >= buy_drop_pct * 1.5 else "warning",
                            related_fund_code=fund_code,
                            related_fund_name=fund_name,
                            source="transaction_confirm",
                        )
        except Exception as e:
            logging.warning(f"[confirm_tx] 补仓后跌幅即时预警异常: {e}")

    # 卖出确认后，自动将金额计入零钱
    if tx_type == "sell" and actual_amount > 0:
        add_cash(user_id, actual_amount)

    # ── 基金转换：减少源基金份额，创建/增加目标基金 ──
    if tx_type == "convert" and target_fund_code and actual_shares and actual_shares > 0:
        # 1. 源基金减少份额（通过 _recalculate_holding 已处理）
        # 2. 创建目标基金的买入交易
        target_name = target_fund_name or target_fund_code
        target_holding = get_holding_by_fund(target_fund_code, user_id)
        if not target_holding:
            # 自动创建目标基金持仓
            target_holding_id = create_holding(
                fund_code=target_fund_code, fund_name=target_name,
                shares=0, cost_price=confirmed_price,
                current_price=confirmed_price, user_id=user_id,
            )
        else:
            target_holding_id = target_holding["id"]
        # 为目标基金创建一笔确认的买入交易
        conn3 = _get_conn()
        conn3.execute("""
            INSERT INTO portfolio_transactions
                (holding_id, user_id, fund_code, transaction_type, amount, shares, price,
                 transaction_date, status, confirmed_at, notes)
            VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, 'confirmed', ?, ?)
        """, (target_holding_id, user_id, target_fund_code,
              actual_amount, actual_shares, confirmed_price,
              tx.get("transaction_date", now[:10]), now,
              f"从 {tx.get('fund_code', '')} 转换"))
        conn3.commit()
        conn3.close()
        _recalculate_holding(target_holding_id)

    # 审计日志
    _log_tx_audit(
        action="confirm", tx_id=tx_id, holding_id=holding_id,
        fund_code=tx.get("fund_code"), fund_name=tx.get("fund_name"),
        before={"status": tx.get("status"), "shares": tx.get("shares"),
                "amount": tx.get("amount"), "price": tx.get("price")},
        after={"status": "confirmed", "shares": actual_shares,
               "amount": actual_amount, "price": actual_price},
        input_shares=confirmed_shares,
        input_amount=confirmed_amount,
        input_price=confirmed_price,
        detail=json.dumps({"fee": fee, "target_fund_code": target_fund_code,
                           "submitted_shares": tx.get("submitted_shares"),
                           "submitted_amount": tx.get("submitted_amount")},
                          ensure_ascii=False),
    )

    return True


def settle_transaction(tx_id: int) -> bool:
    """标记卖出交易已到账。"""
    conn = _get_conn()
    tx = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        conn.close()
        return False
    tx = dict(tx)
    if tx.get("status") != "confirmed":
        conn.close()
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        UPDATE portfolio_transactions SET status = 'settled', settled_at = ? WHERE id = ?
    """, (now, tx_id))
    conn.commit()
    conn.close()

    # 审计日志
    _log_tx_audit(
        action="settle", tx_id=tx_id, holding_id=tx.get("holding_id"),
        fund_code=tx.get("fund_code"), fund_name=tx.get("fund_name"),
        before={"status": "confirmed", "shares": tx.get("shares"),
                "amount": tx.get("amount"), "price": tx.get("price")},
        after={"status": "settled", "shares": tx.get("shares"),
               "amount": tx.get("amount"), "price": tx.get("price")},
    )

    return True


def delete_transaction(tx_id: int) -> bool:
    """删除交易记录（仅允许 pending 状态）。"""
    conn = _get_conn()
    tx = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        conn.close()
        return False
    tx = dict(tx)
    if tx.get("status") not in (None, "pending"):
        conn.close()
        return False
    conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (tx_id,))
    conn.execute("DELETE FROM portfolio_transactions WHERE id = ?", (tx_id,))
    conn.commit()
    conn.close()

    # 审计日志
    _log_tx_audit(
        action="cancel", tx_id=tx_id, holding_id=tx.get("holding_id"),
        fund_code=tx.get("fund_code"), fund_name=tx.get("fund_name"),
        before={"status": tx.get("status"), "shares": tx.get("shares") or tx.get("submitted_shares"),
                "amount": tx.get("amount"), "price": tx.get("price")},
        detail=json.dumps({"transaction_type": tx.get("transaction_type"),
                           "submitted_shares": tx.get("submitted_shares"),
                           "submitted_amount": tx.get("submitted_amount")},
                          ensure_ascii=False),
    )

    return True


# ── 基金净值更新 ──────────────────────────────────────


def fetch_fund_nav(fund_code: str) -> dict | None:
    """
    获取基金最新净值。优先盈米 MCP（数据更新更快），失败则尝试 akshare。

    返回: {"nav": 0.57, "date": "2026-05-22", "change_pct": -2.1} 或 None
    """
    from datetime import datetime, timedelta

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # ── 优先盈米 MCP（数据更新更快）──
    try:
        from mcp.yingmi_client import get_yingmi_client
        client = get_yingmi_client()
        logging.info(f"[db] MCP 获取 {fund_code} 净值...")
        result = client.call_tool("BatchGetFundsDetail", {"fundCodes": [fund_code]})
        # 解析返回的文本内容
        text = ""
        for item in result.get("content", []):
            if item.get("type") == "text":
                text = item["text"]
                break
        if not text:
            logging.warning(f"[db] MCP {fund_code} 返回空内容")
            return None
        import json
        data = json.loads(text) if text.strip().startswith(("{", "[")) else {}
        # BatchGetFundsDetail 返回数组，取第一个
        funds = data if isinstance(data, list) else data.get("data", data.get("result", []))
        if not funds:
            logging.warning(f"[db] MCP {fund_code} 无基金数据")
            return None
        fund_data = funds[0] if isinstance(funds, list) else funds
        # MCP 返回结构: {fundCode, data: {summary: {nav, navDate, dailyReturn}}}
        summary = fund_data.get("data", {}).get("summary", {})
        nav = summary.get("nav") or summary.get("unitNav") or summary.get("latest_nav")
        nav_date = summary.get("navDate") or summary.get("nav_date") or summary.get("updateDate")
        change_pct = summary.get("dailyReturn") or summary.get("change_pct") or summary.get("dayGrowthRate")
        logging.info(f"[db] MCP {fund_code} 返回: nav={nav}, date={nav_date}, change={change_pct}")
        if nav is not None:
            # 转换日期格式（"2026年06月03日" -> "2026-06-03"）
            if nav_date and "年" in str(nav_date):
                try:
                    from datetime import datetime
                    nav_date = datetime.strptime(str(nav_date), "%Y年%m月%d日").strftime("%Y-%m-%d")
                except:
                    pass
            # 处理百分比格式（"-0.89%" -> -0.89）
            if change_pct is not None and isinstance(change_pct, str):
                change_pct = change_pct.replace("%", "").strip()
            try:
                change_pct = float(change_pct) if change_pct else None
            except (ValueError, TypeError):
                change_pct = None
            return {
                "nav": float(nav),
                "date": str(nav_date) if nav_date else "",
                "change_pct": change_pct,
            }
    except Exception as e:
        logging.warning(f"[db] 盈米 MCP 获取 {fund_code} 净值失败: {e}")

    # ── MCP 失败，尝试 akshare ──
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势')
        if df is not None and len(df) > 0:
            last = df.iloc[-1]
            nav_date = str(last["净值日期"])
            logging.info(f"[db] akshare 返回 {fund_code} 日期 {nav_date}（MCP 失败后兜底）")
            return {
                "nav": float(last["单位净值"]),
                "date": nav_date,
                "change_pct": float(last["日增长率"]) if last.get("日增长率") else None,
            }
    except Exception as e:
        logging.warning(f"[db] akshare 获取 {fund_code} 净值失败: {e}")

    return None


def get_fund_nav_history(fund_code: str, user_id: str = "default", days: int = 365) -> dict | None:
    """获取基金净值历史 + 交易点标记（用于交易行为图表）。"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势')
        if df is None or len(df) == 0:
            return None

        nav_history = []
        for _, row in df.iterrows():
            nav_history.append({
                "date": str(row["净值日期"]),
                "nav": float(row["单位净值"]),
            })
        if days > 0 and len(nav_history) > days:
            nav_history = nav_history[-days:]

        conn = _get_conn()
        txs = conn.execute("""
            SELECT t.transaction_type, t.shares, t.price, t.amount, t.transaction_date
            FROM portfolio_transactions t
            WHERE t.fund_code = ? AND t.user_id = ?
                AND (t.is_system IS NULL OR t.is_system = 0)
                AND t.status IN ('confirmed', 'settled')
            ORDER BY t.transaction_date ASC
        """, (fund_code, user_id)).fetchall()
        conn.close()

        return {
            "nav_history": nav_history,
            "transactions": [dict(t) for t in txs],
        }
    except Exception as e:
        print(f"[db] 获取基金 {fund_code} 净值历史失败: {e}")
        return None


def refresh_holding_price(holding_id: int) -> dict | None:
    """
    刷新单个持仓的最新净值并更新数据库。

    返回: {"nav": 0.57, "date": "2026-05-22", "change_pct": -2.1,
           "today_profit": -12.34, "today_change_pct": -2.1} 或 None
    """
    conn = _get_conn()
    holding = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not holding:
        conn.close()
        return None
    holding = dict(holding)
    fund_code = holding["fund_code"]

    nav_data = fetch_fund_nav(fund_code)
    if not nav_data:
        conn.close()
        return None

    nav = nav_data["nav"]
    nav_date = nav_data["date"]
    change_pct = nav_data.get("change_pct")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    shares = holding.get("shares", 0) or 0
    total_cost = holding.get("total_cost", 0) or 0
    current_value = shares * nav
    profit_loss = current_value - total_cost
    profit_rate = profit_loss / total_cost if total_cost > 0 else 0

    # 今日盈亏 = 份额 × (当前净值 - 昨日净值)，通过涨跌幅反算昨日净值
    if change_pct is not None and (100 + change_pct) != 0:
        today_profit = round(current_value * change_pct / (100 + change_pct), 2)
    else:
        today_profit = 0

    conn.execute("""
        UPDATE portfolio_holdings SET
            current_price = ?,
            current_value = ?,
            profit_loss = ?,
            profit_rate = ?,
            today_change_pct = ?,
            today_profit = ?,
            price_updated_at = ?,
            updated_at = ?
        WHERE id = ?
    """, (round(nav, 4), round(current_value, 2), round(profit_loss, 2),
          round(profit_rate, 4), change_pct, today_profit, nav_date, now, holding_id))
    conn.commit()
    conn.close()

    nav_data["today_profit"] = today_profit
    nav_data["today_change_pct"] = change_pct
    return nav_data


def refresh_all_fund_prices(user_id: str = "default") -> list[dict]:
    """
    批量刷新用户所有持仓的最新净值。

    返回: [{"fund_code": "161725", "fund_name": "...", "nav": 0.57, "date": "2026-05-22"}, ...]
    """
    holdings = list_holdings(user_id)
    results = []
    for h in holdings:
        # 跳过已清仓持仓
        if (h.get("shares") or 0) <= 0:
            results.append({
                "fund_code": h["fund_code"],
                "fund_name": h["fund_name"],
                "skipped": True,
                "reason": "已清仓",
            })
            continue
        nav_data = fetch_fund_nav(h["fund_code"])
        if not nav_data:
            results.append({
                "fund_code": h["fund_code"],
                "fund_name": h["fund_name"],
                "error": "净值获取失败",
            })
            continue

        nav = nav_data["nav"]
        nav_date = nav_data["date"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        shares = h.get("shares", 0) or 0
        total_cost = h.get("total_cost", 0) or 0
        current_value = shares * nav
        profit_loss = current_value - total_cost
        profit_rate = profit_loss / total_cost if total_cost > 0 else 0

        conn = _get_conn()
        change_pct = nav_data.get("change_pct")
        if change_pct is not None and (100 + change_pct) != 0:
            today_profit = round(current_value * change_pct / (100 + change_pct), 2)
        else:
            today_profit = 0

        conn.execute("""
            UPDATE portfolio_holdings SET
                current_price = ?,
                current_value = ?,
                profit_loss = ?,
                profit_rate = ?,
                today_change_pct = ?,
                today_profit = ?,
                price_updated_at = ?,
                updated_at = ?
            WHERE id = ?
        """, (round(nav, 4), round(current_value, 2), round(profit_loss, 2),
              round(profit_rate, 4), change_pct, today_profit, nav_date, now, h["id"]))
        conn.commit()
        conn.close()

        results.append({
            "fund_code": h["fund_code"],
            "fund_name": h["fund_name"],
            "nav": nav,
            "date": nav_date,
            "change_pct": change_pct,
            "today_profit": today_profit,
        })

    return results


# ── 基金信息查询 ──────────────────────────────────────


def lookup_fund_info(fund_code: str) -> dict | None:
    """通过 akshare 查询基金基本信息，自动填充名称、类型、跟踪标的。"""
    try:
        import akshare as ak
        df = ak.fund_overview_em(symbol=fund_code)
        if df is None or len(df) == 0:
            return None
        row = df.iloc[0]
        fund_name = str(row.get("基金简称", ""))
        fund_type_str = str(row.get("基金类型", ""))
        return {
            "fund_code": str(row.get("基金代码", fund_code)),
            "fund_name": fund_name,
            "fund_full_name": str(row.get("基金全称", "")),
            "fund_type": fund_type_str,
            "fund_category": classify_fund_category(fund_name, fund_type_str),
            "tracking_index": str(row.get("跟踪标的", "")),
            "fund_manager": str(row.get("基金经理人", "")),
            "scale": str(row.get("净资产规模", "")),
            "established": str(row.get("成立日期/规模", "")),
            "benchmark": str(row.get("业绩比较基准", "")),
        }
    except Exception as e:
        print(f"[db] 查询基金信息失败 {fund_code}: {e}")
        return None


def classify_bond_type(bond_name: str) -> str:
    """根据债券名称推断类型：利率债/信用债/可转债。"""
    name = bond_name.strip()
    # 可转债
    if "转债" in name:
        return "可转债"
    # 利率债：国债、政金债（国开/进出/农发）、地方政府债
    rate_keywords = ("国债", "国开", "进出", "农发", "政金", "地方债", "政府债", "央行")
    for kw in rate_keywords:
        if kw in name:
            return "利率债"
    # 其余归为信用债
    return "信用债"


def classify_fund_category(fund_name: str, fund_type: str = "") -> str:
    """根据基金名称和类型分类：equity / bond / hybrid / money_market / index / other。"""
    name = fund_name.strip()

    # 货币基金
    if any(kw in name for kw in ("货币", "货基", "现金", "流动性", "添利", "增利宝")):
        return "money_market"
    if "同业存单" in name:
        return "money_market"

    # 债券基金 — 纯债
    if any(kw in name for kw in ("纯债", "短债", "长债", "中短债", "中长债", "利率债", "信用债")):
        return "bond"
    if any(kw in name for kw in ("债券", "债基", "国债", "政金")):
        return "bond"
    if "中债" in name and "指数" in name:
        return "bond_index"

    # 可转债基金
    if "可转债" in name or "转债" in name:
        return "convertible_bond"

    # 指数基金
    if any(kw in name for kw in ("指数", "ETF", "ETF联接", "联接")):
        # 排除债券指数已在上面判断
        if any(kw in name for kw in ("债", "国债", "政金")):
            return "bond_index"
        return "index"

    # 混合型 — 名字含"混合"但未被债券规则捕获的
    if "混合" in name or "平衡" in name or "灵活" in name:
        if any(kw in fund_type for kw in ("债券", "债")):
            return "bond"
        return "hybrid"

    # 根据 fund_type 补充判断
    if "债券型" in fund_type:
        return "bond"
    if "货币型" in fund_type:
        return "money_market"
    if "混合型" in fund_type:
        return "hybrid"
    if "股票型" in fund_type:
        return "equity"

    # 默认归为 equity
    return "equity"


def get_fund_holdings(fund_code: str, year: str = None) -> dict:
    """获取基金持仓详情：股票重仓 + 债券持仓 + 资产配置。"""
    if not year:
        from datetime import datetime
        year = str(datetime.now().year)

    result = {
        "fund_code": fund_code,
        "top_stocks": [],
        "bond_holdings": [],
        "asset_allocation": [],
        "industry_allocation": [],
        "bond_type_summary": {},
    }

    # 1. 股票持仓 Top 10
    try:
        import akshare as ak
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        if df is not None and len(df) > 0:
            # 取最新一期的前 10
            quarters = df["季度"].unique()
            if len(quarters) > 0:
                latest_q = quarters[-1]
                latest = df[df["季度"] == latest_q].head(10)
                for _, r in latest.iterrows():
                    result["top_stocks"].append({
                        "stock_code": str(r.get("股票代码", "")),
                        "stock_name": str(r.get("股票名称", "")),
                        "pct_nav": float(r.get("占净值比例", 0)),
                        "shares": float(r.get("持股数", 0)),
                        "market_value": float(r.get("持仓市值", 0)),
                    })
    except Exception as e:
        print(f"[db] 获取股票持仓失败 {fund_code}: {e}")

    # 2. 债券持仓
    bond_type_counter = {}
    try:
        import akshare as ak
        df = ak.fund_portfolio_bond_hold_em(symbol=fund_code, date=year)
        if df is not None and len(df) > 0:
            quarters = df["季度"].unique()
            if len(quarters) > 0:
                latest_q = quarters[-1]
                latest = df[df["季度"] == latest_q].head(10)
                for _, r in latest.iterrows():
                    bond_name = str(r.get("债券名称", ""))
                    btype = classify_bond_type(bond_name)
                    bond_type_counter[btype] = bond_type_counter.get(btype, 0) + float(r.get("占净值比例", 0))
                    result["bond_holdings"].append({
                        "bond_code": str(r.get("债券代码", "")),
                        "bond_name": bond_name,
                        "pct_nav": float(r.get("占净值比例", 0)),
                        "market_value": float(r.get("持仓市值", 0)),
                        "bond_type": btype,
                    })
    except Exception as e:
        print(f"[db] 获取债券持仓失败 {fund_code}: {e}")

    result["bond_type_summary"] = {k: round(v, 2) for k, v in bond_type_counter.items()}

    # 3. 资产配置（股票/债券/现金/其他）
    try:
        import akshare as ak
        df = ak.fund_individual_detail_hold_xq(symbol=fund_code)
        if df is not None and len(df) > 0:
            for _, r in df.iterrows():
                result["asset_allocation"].append({
                    "type": str(r.get("资产类型", "")),
                    "pct": str(r.get("仓位占比", "")),
                })
    except Exception as e:
        print(f"[db] 获取资产配置失败 {fund_code}: {e}")

    # 4. 行业配置
    try:
        import akshare as ak
        df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code, date=year)
        if df is not None and len(df) > 0:
            for _, r in df.head(10).iterrows():
                result["industry_allocation"].append({
                    "industry": str(r.get("行业类别", "")),
                    "pct_nav": float(r.get("占净值比例", 0)),
                })
    except Exception as e:
        print(f"[db] 获取行业配置失败 {fund_code}: {e}")

    return result


# ── 风险预警 CRUD ──────────────────────────────────────


def create_alert(alert_type: str, title: str, content: str = None,
                 severity: str = "info", related_fund_code: str = None,
                 related_fund_name: str = None, source: str = None,
                 user_id: str = "default") -> int:
    """新增风险预警，返回 alert_id。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_alerts
            (user_id, alert_type, severity, title, content,
             related_fund_code, related_fund_name, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, alert_type, severity, title, content,
          related_fund_code, related_fund_name, source))
    alert_id = cur.lastrowid
    conn.commit()
    conn.close()
    return alert_id


def list_alerts(user_id: str = "default", limit: int = 50,
                unread_only: bool = False) -> list[dict]:
    """获取预警列表，按时间倒序。"""
    conn = _get_conn()
    if unread_only:
        rows = conn.execute("""
            SELECT * FROM portfolio_alerts
            WHERE user_id = ? AND is_read = 0
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM portfolio_alerts
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_unread_alert_count(user_id: str = "default") -> int:
    """获取未读预警数量。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM portfolio_alerts WHERE user_id = ? AND is_read = 0",
        (user_id,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def mark_alert_read(alert_id: int) -> bool:
    """标记预警为已读。"""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE portfolio_alerts SET is_read = 1 WHERE id = ?", (alert_id,)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def delete_alert(alert_id: int) -> bool:
    """删除预警。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM portfolio_alerts WHERE id = ?", (alert_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# ── 交易标签 CRUD ──────────────────────────────────────


def add_transaction_tag(transaction_id: int, tag: str) -> int:
    """给交易记录添加标签，返回 tag_id。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO transaction_tags (transaction_id, tag) VALUES (?, ?)",
        (transaction_id, tag)
    )
    conn.commit()
    tag_id = cur.lastrowid
    conn.close()
    return tag_id


def remove_transaction_tag(transaction_id: int, tag: str) -> bool:
    """移除交易记录的指定标签。"""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM transaction_tags WHERE transaction_id = ? AND tag = ?",
        (transaction_id, tag)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_transaction_tags(transaction_id: int) -> list[str]:
    """获取交易记录的所有标签。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT tag FROM transaction_tags WHERE transaction_id = ?",
        (transaction_id,)
    ).fetchall()
    conn.close()
    return [r["tag"] for r in rows]


# ── 持仓分析辅助函数 ──────────────────────────────────


def get_portfolio_diversification(user_id: str = "default") -> dict:
    """分析持仓分散度：基金数量、指数分布、类型分布。排除已清仓记录。"""
    holdings = list_holdings(user_id)
    active = [h for h in holdings if (h.get("shares") or 0) > 0]

    total_value = sum(h.get("current_value", 0) or 0 for h in active)
    total_cost = sum(h.get("total_cost", 0) or 0 for h in active)

    # 指数分布
    index_dist = {}
    for h in active:
        idx = h.get("index_name") or "未知"
        val = h.get("current_value", 0) or 0
        index_dist[idx] = index_dist.get(idx, 0) + val

    # 基金类型分布（通过 fund_code 前几位判断）
    # 股票型/混合型/债券型/指数型/货币型
    type_dist = {"股票型": 0, "混合型": 0, "债券型": 0, "指数型": 0, "货币型": 0, "其他": 0}
    for h in holdings:
        code = h.get("fund_code", "")
        val = h.get("current_value", 0) or 0
        # 简单分类：以 fund_name 或 index_name 判断
        name = (h.get("fund_name", "") or "") + (h.get("index_name", "") or "")
        if "指数" in name or "ETF" in name or "ETF联接" in name:
            type_dist["指数型"] = type_dist.get("指数型", 0) + val
        elif "债" in name or "纯债" in name or "信用债" in name:
            type_dist["债券型"] = type_dist.get("债券型", 0) + val
        elif "货" in name or "货币" in name:
            type_dist["货币型"] = type_dist.get("货币型", 0) + val
        elif "混合" in name or "灵活" in name:
            type_dist["混合型"] = type_dist.get("混合型", 0) + val
        elif "股" in name or "股票" in name:
            type_dist["股票型"] = type_dist.get("股票型", 0) + val
        else:
            type_dist["其他"] = type_dist.get("其他", 0) + val

    # 仓位集中度：最大持仓占比
    max_holding_pct = 0
    if total_value > 0:
        max_value = max((h.get("current_value", 0) or 0) for h in holdings)
        max_holding_pct = round(max_value / total_value * 100, 2)

    return {
        "holding_count": len(holdings),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "max_holding_pct": max_holding_pct,
        "index_distribution": {k: round(v, 2) for k, v in sorted(index_dist.items(), key=lambda x: -x[1])},
        "type_distribution": {k: round(v, 2) for k, v in type_dist.items() if v > 0},
    }


def get_transaction_summary(user_id: str = "default") -> dict:
    """分析交易行为汇总，含最近 50 笔交易明细。不包含系统交易的统计数据。"""
    conn = _get_conn()

    # 买入统计（排除系统交易）
    buy_rows = conn.execute("""
        SELECT COUNT(*) as tx_count, SUM(amount) as total_amount
        FROM portfolio_transactions
        WHERE user_id = ? AND transaction_type = 'buy' AND (status IN ('confirmed', 'settled') OR status IS NULL)
            AND (is_system IS NULL OR is_system = 0)
    """, (user_id,)).fetchall()
    buy_count = buy_rows[0]["tx_count"] if buy_rows else 0
    buy_total = buy_rows[0]["total_amount"] or 0 if buy_rows else 0

    # 卖出统计（排除系统交易）
    sell_rows = conn.execute("""
        SELECT COUNT(*) as tx_count, SUM(amount) as total_amount
        FROM portfolio_transactions
        WHERE user_id = ? AND transaction_type = 'sell' AND (status IN ('confirmed', 'settled') OR status IS NULL)
            AND (is_system IS NULL OR is_system = 0)
    """, (user_id,)).fetchall()
    sell_count = sell_rows[0]["tx_count"] if sell_rows else 0
    sell_total = sell_rows[0]["total_amount"] or 0 if sell_rows else 0

    # 最近交易明细（含基金名称）
    recent = conn.execute("""
        SELECT t.id, t.fund_code, t.transaction_type, t.shares, t.price, t.amount,
               t.transaction_date, t.status, t.is_system,
               COALESCE(h.fund_name, '') as fund_name,
               COALESCE(h.index_name, '') as index_name
        FROM portfolio_transactions t
        LEFT JOIN portfolio_holdings h ON t.holding_id = h.id
        WHERE t.user_id = ? AND (t.is_system IS NULL OR t.is_system = 0)
        ORDER BY t.id DESC
        LIMIT 50
    """, (user_id,)).fetchall()

    conn.close()

    return {
        "buy_count": buy_count,
        "buy_total": round(buy_total, 2),
        "sell_count": sell_count,
        "sell_total": round(sell_total, 2),
        "total_tx_count": buy_count + sell_count,
        "net_investment": round(buy_total - sell_total, 2),
        "recent_transactions": [dict(r) for r in recent],
    }


def clear_all_portfolio_data(user_id: str = "default"):
    """删除用户的所有持仓、交易记录、预警和标签。"""
    # 审计日志（在删除前写入）
    _log_tx_audit(
        action="clear_all", operator="user",
        detail=json.dumps({"user_id": user_id, "scope": "持仓/交易/预警/标签/分析记录/现金"},
                          ensure_ascii=False),
    )

    conn = _get_conn()
    conn.execute("DELETE FROM portfolio_alerts WHERE user_id = ?", (user_id,))
    conn.execute("""
        DELETE FROM transaction_tags WHERE transaction_id IN
        (SELECT id FROM portfolio_transactions WHERE user_id = ?)
    """, (user_id,))
    conn.execute("DELETE FROM portfolio_transactions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM portfolio_holdings WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM portfolio_analysis_records WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM portfolio_cash WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def create_portfolio_analysis_record(analysis_type: str, summary: str,
                                     input_data: str, result_data: str = "",
                                     token_usage: int = 0,
                                     user_id: str = "default",
                                     agent_id: int = None,
                                     status: str = "done") -> int:
    """保存持仓分析记录。status: running / done / error"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_analysis_records
            (user_id, analysis_type, summary, input_data, result_data, token_usage, agent_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, analysis_type, summary, input_data, result_data, token_usage, agent_id, status))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def update_analysis_record(record_id: int, **fields) -> bool:
    """更新分析记录字段（result_data, status, token_usage 等）。"""
    if not fields:
        return False
    conn = _get_conn()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [record_id]
    conn.execute(f"UPDATE portfolio_analysis_records SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return True


def get_analysis_record_status(record_id: int) -> dict | None:
    """查询分析记录状态。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, status, result_data, token_usage, error_msg FROM portfolio_analysis_records WHERE id = ?",
        (record_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def list_portfolio_analysis_records(analysis_type: str = None,
                                    limit: int = 20,
                                    user_id: str = "default") -> list[dict]:
    """列出持仓分析记录。"""
    conn = _get_conn()
    if analysis_type:
        rows = conn.execute("""
            SELECT id, user_id, analysis_type, summary, result_data, token_usage, created_at
            FROM portfolio_analysis_records
            WHERE user_id = ? AND analysis_type = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, analysis_type, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, user_id, analysis_type, summary, result_data, token_usage, created_at
            FROM portfolio_analysis_records
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portfolio_analysis_record(record_id: int) -> dict | None:
    """获取单条持仓分析记录详情。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM portfolio_analysis_records WHERE id = ?",
        (record_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_analysis_feedback(record_id: int, feedback: str, note: str = "") -> bool:
    """提交用户对分析结果的反馈（helpful/unhelpful）。"""
    conn = _get_conn()
    cursor = conn.execute(
        "UPDATE portfolio_analysis_records SET feedback = ?, feedback_note = ? WHERE id = ?",
        (feedback, note, record_id)
    )
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0


def list_bad_cases(analysis_type: str = None, limit: int = 50) -> list[dict]:
    """列出被标记为 unhelpful 的分析记录（Bad Cases）。"""
    conn = _get_conn()
    if analysis_type:
        rows = conn.execute("""
            SELECT id, analysis_type, summary, input_data, result_data, feedback_note,
                   token_usage, agent_id, created_at
            FROM portfolio_analysis_records
            WHERE feedback = 'unhelpful' AND analysis_type = ?
            ORDER BY created_at DESC LIMIT ?
        """, (analysis_type, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, analysis_type, summary, input_data, result_data, feedback_note,
                   token_usage, agent_id, created_at
            FROM portfolio_analysis_records
            WHERE feedback = 'unhelpful'
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all_bad_cases(source: str = None, limit: int = 100) -> list[dict]:
    """统一查询所有 Bad Case（分析记录 + LLM 反馈）。

    参数:
        source: 'analysis' 只查分析记录, 'chat' 只查 LLM 反馈, None 查全部
        limit: 每个来源的最大条数
    返回:
        统一结构的 bad case 列表，每条包含 source, id, type, summary, input, output, note, metadata, created_at
    """
    conn = _get_conn()
    results = []

    if source != 'chat':
        # 来源 A: portfolio_analysis_records
        rows = conn.execute("""
            SELECT id, analysis_type, summary, input_data, result_data, feedback_note,
                   token_usage, agent_id, created_at
            FROM portfolio_analysis_records
            WHERE feedback = 'unhelpful'
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        for r in rows:
            d = dict(r)
            results.append({
                'source': 'analysis',
                'id': d['id'],
                'type': d.get('analysis_type', ''),
                'summary': d.get('summary', ''),
                'input': d.get('input_data', ''),
                'output': d.get('result_data', ''),
                'note': d.get('feedback_note', ''),
                'metadata': {'token_usage': d.get('token_usage'), 'agent_id': d.get('agent_id')},
                'created_at': d.get('created_at', ''),
            })

    if source != 'analysis':
        # 来源 B: llm_feedback (chat / specialist 等)
        rows = conn.execute("""
            SELECT id, caller, input_summary, output_summary, rating, tags, comment, created_at
            FROM llm_feedback
            WHERE rating = 'unhelpful'
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        for r in rows:
            d = dict(r)
            results.append({
                'source': 'chat',
                'id': d['id'],
                'type': d.get('caller', ''),
                'summary': d.get('output_summary', ''),
                'input': d.get('input_summary', ''),
                'output': d.get('output_summary', ''),
                'note': d.get('comment', ''),
                'metadata': {'tags': d.get('tags', ''), 'caller': d.get('caller', '')},
                'created_at': d.get('created_at', ''),
            })

    conn.close()
    # 按时间倒序排列
    results.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return results[:limit]


def delete_portfolio_analysis_record(record_id: int) -> bool:
    """删除持仓分析记录。"""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM portfolio_analysis_records WHERE id = ?", (record_id,)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# ── 分析结果缓存 ──────────────────────────────────────


def save_analysis_cache(cache_key: str, data: dict) -> bool:
    """保存分析结果缓存（幂等 upsert）。"""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO analysis_cache (cache_key, data, created_at) VALUES (?, ?, datetime('now','localtime'))",
        (cache_key, json.dumps(data, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return True


def get_analysis_cache(cache_key: str) -> dict | None:
    """读取分析结果缓存。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT data FROM analysis_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row["data"])
        except Exception:
            return None
    return None


def get_cached_fund_holdings(fund_code: str) -> dict:
    """获取基金持仓（24h 缓存）。"""
    cache_key = f"fund_holdings_{fund_code}"
    cached = get_analysis_cache(cache_key)
    if cached:
        return cached
    data = get_fund_holdings(fund_code)
    save_analysis_cache(cache_key, data)
    return data


def get_portfolio_penetration(user_id: str = "default") -> dict:
    """跨基金加权聚合底层股票持仓，计算持仓穿透。"""
    cache_key = f"portfolio_penetration_{user_id}"
    cached = get_analysis_cache(cache_key)
    if cached:
        return cached

    holdings = list_holdings(user_id)
    holdings = [h for h in holdings if (h.get("shares") or 0) > 0 and (h.get("current_value") or 0) > 0]
    if not holdings:
        return {"top_stocks": [], "overlap_matrix": {"fund_names": [], "matrix": []},
                "total_portfolio_value": 0, "fund_count": 0, "cached_at": None}

    total_value = sum(h["current_value"] for h in holdings)

    fund_stock_map = {}
    for h in holdings:
        fund_code = h["fund_code"]
        fund_name = h["fund_name"]
        fund_value = h["current_value"]
        try:
            fh = get_cached_fund_holdings(fund_code)
            stocks = fh.get("top_stocks", [])
            fund_stock_map[fund_code] = {"name": fund_name, "value": fund_value, "stocks": stocks}
        except Exception as e:
            print(f"[db] 获取基金持仓失败 {fund_code}: {e}")
            fund_stock_map[fund_code] = {"name": fund_name, "value": fund_value, "stocks": []}

    stock_agg = {}
    for fund_code, info in fund_stock_map.items():
        fund_weight = info["value"] / total_value * 100
        for s in info["stocks"]:
            sc = s["stock_code"]
            sn = s["stock_name"]
            contribution = (s["pct_nav"] / 100) * fund_weight
            if sc not in stock_agg:
                stock_agg[sc] = {"stock_code": sc, "stock_name": sn, "total_weight_pct": 0, "held_in_funds": []}
            stock_agg[sc]["total_weight_pct"] += contribution
            stock_agg[sc]["held_in_funds"].append({
                "fund_name": info["name"],
                "contribution_pct": round(contribution, 2),
            })

    top_stocks = sorted(stock_agg.values(), key=lambda x: x["total_weight_pct"], reverse=True)[:15]
    for ts in top_stocks:
        ts["total_weight_pct"] = round(ts["total_weight_pct"], 2)
        ts["held_in_funds"].sort(key=lambda x: x["contribution_pct"], reverse=True)

    fund_codes = list(fund_stock_map.keys())
    fund_names = [fund_stock_map[fc]["name"] for fc in fund_codes]
    n = len(fund_codes)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        stocks_i = set(s["stock_code"] for s in fund_stock_map[fund_codes[i]]["stocks"])
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
            else:
                stocks_j = set(s["stock_code"] for s in fund_stock_map[fund_codes[j]]["stocks"])
                if stocks_i and stocks_j:
                    overlap = len(stocks_i & stocks_j)
                    matrix[i][j] = round(overlap / min(len(stocks_i), len(stocks_j)), 2) if min(len(stocks_i), len(stocks_j)) > 0 else 0

    from datetime import datetime
    result = {
        "top_stocks": top_stocks,
        "overlap_matrix": {"fund_names": fund_names, "matrix": matrix},
        "total_portfolio_value": round(total_value, 2),
        "fund_count": len(holdings),
        "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_analysis_cache(cache_key, result)
    return result
