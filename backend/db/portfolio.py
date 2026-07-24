"""持仓管理全领域 CRUD（持仓、交易、零钱、调仓、净值、基金信息、预警、标签、分析记录、缓存）。"""

import json
import logging
import re
import sqlite3
from datetime import datetime, date

from db._conn import _get_conn, _row_to_dict
from db.config import get_config

# G-akshare-stats（2026-07-24）：统一 akshare 调用入口，记录统计 + 超时保护
from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

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
                   notes: str = None, user_id: str = None,
                   account: str = None, fund_category: str = None) -> int:
    """新增持仓，返回 holding_id。自动分类基金类型（equity/bond/hybrid/money_market/index 等）。"""
    if user_id is None:
        user_id = get_config('portfolio.default_user_id', 'default')
    if account is None:
        account = get_config('portfolio.default_account', '花无缺')
    if fund_category is None:
        fund_category = classify_fund_category(fund_name, fund_code=fund_code)
    if cost_price is None:
        cost_price = current_price or 0
    total_cost = shares * cost_price
    current_value = shares * current_price if (current_price and current_price > 0) else None
    profit_loss = (current_value - total_cost) if current_value is not None else None
    profit_rate = (profit_loss / total_cost) if (profit_loss is not None and total_cost > 0) else None
    conn = _get_conn()
    try:
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
    finally:
        conn.close()
    return holding_id


def get_holding(holding_id: int) -> dict | None:
    """获取单个持仓。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_holding_by_fund(fund_code: str, user_id: str = "default", account: str = None) -> dict | None:
    """根据基金代码获取持仓。account 非空时按 (user_id, account, fund_code) 精确匹配。"""
    conn = _get_conn()
    if account:
        row = conn.execute(
            "SELECT * FROM portfolio_holdings WHERE fund_code = ? AND user_id = ? AND account = ?",
            (fund_code, user_id, account)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM portfolio_holdings WHERE fund_code = ? AND user_id = ?",
            (fund_code, user_id)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_dca_suggestion(holding_id: int) -> dict:
    """根据4%定投法返回加仓建议。

    规则：基础¥500/档，每跌4%加一档，最多3档。
    - 盈利：建议观望（recommended_amount=0, advice=watch）
    - 亏损 0~4%：第1档 ¥500
    - 亏损 4~8%：第2档 ¥1000
    - 亏损 8~12%：第3档 ¥1500
    - 亏损 >12%：封顶¥1500 + 提示超过覆盖范围
    """
    holding = get_holding(holding_id)
    if not holding:
        return {"error": "持仓不存在"}

    profit_rate = holding.get("profit_rate")
    fund_code = holding["fund_code"]
    fund_name = holding.get("fund_name", fund_code)

    # 查询近30天加仓次数（用户手动买入，排除系统交易）
    conn = _get_conn()
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        recent_count = conn.execute("""
            SELECT COUNT(*) as cnt FROM portfolio_transactions
            WHERE holding_id = ? AND transaction_type = 'buy'
              AND (is_system IS NULL OR is_system = 0) AND (is_hypothetical IS NULL OR is_hypothetical = 0)
              AND transaction_date >= ?
        """, (holding_id, cutoff)).fetchone()["cnt"]
        today = datetime.now().strftime("%Y-%m-%d")
        today_count = conn.execute("""
            SELECT COUNT(*) as cnt FROM portfolio_transactions
            WHERE holding_id = ? AND transaction_type = 'buy'
              AND (is_system IS NULL OR is_system = 0) AND (is_hypothetical IS NULL OR is_hypothetical = 0)
              AND transaction_date = ?
        """, (holding_id, today)).fetchone()["cnt"]
    finally:
        conn.close()

    BASE_AMOUNT = 500
    TIER_STEP = 500
    MAX_TIERS = 3

    if profit_rate is None:
        advice = "watch"
        recommended = 0
        tier = 0
        rule = "无法计算盈亏率，建议观望"
    elif profit_rate > 0:
        advice = "watch"
        recommended = 0
        tier = 0
        rule = f"当前盈利 {profit_rate*100:.1f}%，建议观望"
    else:
        loss_pct = -profit_rate * 100
        tier = min(int(loss_pct / 4) + 1, MAX_TIERS)
        recommended = BASE_AMOUNT + (tier - 1) * TIER_STEP
        if tier >= MAX_TIERS and loss_pct > 12:
            advice = "continue"
            rule = f"基础¥500/档，每跌4%加一档，当前亏损{loss_pct:.1f}%对应第{tier}档（已超过定投法覆盖范围，建议结合基本面分析）"
        else:
            advice = "continue"
            rule = f"基础¥500/档，每跌4%加一档，当前亏损{loss_pct:.1f}%对应第{tier}档"

    if recent_count >= 3:
        advice = "pause"
        rule += f"；近30天已加仓{recent_count}次，建议放缓节奏"

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "current_profit_rate": profit_rate,
        "current_shares": holding.get("shares", 0),
        "current_value": holding.get("current_value"),
        "suggestion": {
            "recommended_amount": recommended,
            "tier": tier,
            "max_tiers": MAX_TIERS,
            "rule": rule,
            "already_added_today": today_count > 0,
            "recent_add_count_30d": recent_count,
        },
        "advice": advice,
    }


def preview_sell(holding_id: int, shares_to_sell: float) -> dict:
    """减仓预览：计算预计盈亏和约束警告（软提示，不拦截）。

    约束检测：
    - profit_warning: 亏损状态下减仓
    - single_amount: 单次减仓 >¥50,000
    - total_reduction: 本次减仓 >总资产10%
    - concentration: 减仓后该基金占比仍 >20%
    """
    holding = get_holding(holding_id)
    if not holding:
        return {"error": "持仓不存在"}

    current_shares = holding.get("shares", 0) or 0
    if shares_to_sell > current_shares:
        return {"error": f"卖出份额 {shares_to_sell} 超过持有份额 {current_shares}"}

    current_price = holding.get("current_price") or 0
    cost_price = holding.get("cost_price") or 0
    fund_code = holding["fund_code"]
    fund_name = holding.get("fund_name", fund_code)

    expected_proceeds = shares_to_sell * current_price
    cost_basis = cost_price
    expected_profit_loss = (current_price - cost_price) * shares_to_sell
    expected_profit_rate = (expected_profit_loss / (cost_price * shares_to_sell)) if (cost_price > 0 and shares_to_sell > 0) else 0
    remaining_shares = current_shares - shares_to_sell
    remaining_value = remaining_shares * current_price

    summary = get_portfolio_summary()
    total_assets = summary.get("total_assets", 0) or 0
    total_value_before = summary.get("total_value", 0) or 0
    total_value_after = total_value_before - expected_proceeds
    concentration_after = (remaining_value / total_value_after) if total_value_after > 0 else 0

    warnings = []

    if expected_profit_loss < 0:
        loss_pct = abs(expected_profit_rate) * 100
        warnings.append({
            "type": "profit_warning",
            "level": "info",
            "message": f"当前亏损 {loss_pct:.1f}%，确认是否止损",
        })

    if expected_proceeds > 50000:
        warnings.append({
            "type": "single_amount",
            "level": "warning",
            "message": f"本次减仓金额 ¥{expected_proceeds:,.0f} 较大（>¥50,000），建议分批减仓",
        })

    if total_assets > 0 and expected_proceeds / total_assets > 0.10:
        pct = expected_proceeds / total_assets * 100
        warnings.append({
            "type": "total_reduction",
            "level": "warning",
            "message": f"本次减仓占总资产 {pct:.1f}%（>10%），建议分批减仓",
        })

    if concentration_after > 0.20:
        pct = concentration_after * 100
        warnings.append({
            "type": "concentration",
            "level": "warning",
            "message": f"减仓后该基金仍占总资产 {pct:.1f}%（>20%），集中度偏高",
        })

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "shares_to_sell": shares_to_sell,
        "current_price": current_price,
        "expected_proceeds": round(expected_proceeds, 2),
        "cost_basis": cost_basis,
        "expected_profit_loss": round(expected_profit_loss, 2),
        "expected_profit_rate": round(expected_profit_rate, 4),
        "remaining_shares": remaining_shares,
        "remaining_value": round(remaining_value, 2),
        "total_assets_after": round(total_assets - expected_proceeds, 2),
        "concentration_after": round(concentration_after, 4),
        "warnings": warnings,
    }


def get_nav_by_date(fund_code: str, nav_date: str) -> float | None:
    """从 fund_nav_history 表查询指定日期的净值。无则返回None。"""
    conn = _get_conn()
    try:
        row = conn.execute("""
            SELECT nav FROM fund_nav_history
            WHERE fund_code = ? AND nav_date = ?
        """, (fund_code, nav_date)).fetchone()
        return row["nav"] if row else None
    finally:
        conn.close()


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
        # 先拿到当前 fund_code（可能被同时更新），优先用新值
        _fc_for_cat = fields.get("fund_code")
        if not _fc_for_cat:
            conn_tmp = _get_conn()
            try:
                r = conn_tmp.execute("SELECT fund_code FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
                _fc_for_cat = r["fund_code"] if r else None
            finally:
                conn_tmp.close()
        fields["fund_category"] = classify_fund_category(fields["fund_name"], fund_code=_fc_for_cat or "")

    # 如果更新了 shares 或 cost_price，重算 total_cost
    conn = _get_conn()
    try:
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
    finally:
        conn.close()


def delete_holding(holding_id: int) -> bool:
    """删除持仓及其交易记录。"""
    # 先查持仓信息用于审计
    conn = _get_conn()
    try:
        h = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
        h = dict(h) if h else {}

        conn.execute("DELETE FROM portfolio_transactions WHERE holding_id = ?", (holding_id,))
        cur = conn.execute("DELETE FROM portfolio_holdings WHERE id = ?", (holding_id,))
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
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
    try:
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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # 只有 confirmed 状态才更新持仓数据（事务外执行，可重试恢复）
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


def get_transaction(tx_id: int) -> dict | None:
    """获取单条交易记录。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_transactions(fund_code: str = None, holding_id: int = None,
                      user_id: str = "default", limit: int = 100,
                      include_system: bool = False, status: str = None,
                      start_date: str = None, end_date: str = None) -> list[dict]:
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
    if start_date:
        conditions.append("transaction_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("transaction_date <= ?")
        params.append(end_date)
    if not include_system:
        conditions.append("(is_system IS NULL OR is_system = 0) AND (is_hypothetical IS NULL OR is_hypothetical = 0)")

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
    try:
        holding = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
        if not holding:
            conn.close()
            return
        holding = dict(holding)

        txs = conn.execute("""
            SELECT * FROM portfolio_transactions
            WHERE holding_id = ? AND (status IN ('confirmed', 'settled') OR status IS NULL)
              AND (is_hypothetical IS NULL OR is_hypothetical = 0)
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
            # 有基准持仓时跳过系统自动创建的交易（避免与基准数据双重计算）
            if has_base and tx.get("is_system"):
                continue
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
            # 有基准持仓时跳过系统自动创建的交易
            if has_base and tx.get("is_system"):
                continue
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
    finally:
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


def _auto_update_decision_on_confirm(tx_id: int, user_id: str = "default"):
    """交易确认后，自动将匹配的 proposed/accepted 决策标记为 executed。"""
    from db.decisions import update_decision_status
    conn = _get_conn()
    try:
        tx = conn.execute(
            "SELECT fund_code, fund_name, transaction_type FROM portfolio_transactions WHERE id = ?",
            (tx_id,)
        ).fetchone()
        if not tx:
            conn.close()
            return
        fund_code = tx["fund_code"]
        pending = conn.execute("""
            SELECT id FROM decision_records
            WHERE user_id = ? AND target_code = ? AND status IN ('proposed', 'accepted')
            ORDER BY created_at DESC LIMIT 5
        """, (user_id, fund_code)).fetchall()
        for row in pending:
            update_decision_status(row["id"], "executed", f"交易 #{tx_id} 已确认")
    finally:
        conn.close()


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
    try:
        tx = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx:
            conn.close()
            return False
        tx = dict(tx)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tx_type = tx["transaction_type"]
        user_id = tx.get("user_id", "default")
        holding_id = tx.get("holding_id")

        # 状态守卫：只允许确认 pending 状态的交易，防止重复确认导致现金重复入账
        if tx.get("status") != "pending":
            logger.warning(f"交易 {tx_id} 状态为 {tx.get('status')}，非 pending，拒绝确认")
            conn.close()
            return False

        # 卖出/转换份额校验：不允许超过持有份额
        if tx_type in ("sell", "convert") and holding_id:
            sub_shares_check = confirmed_shares or tx.get("submitted_shares") or tx.get("shares") or 0
            holding_check = conn.execute("SELECT shares FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
            if holding_check and sub_shares_check > (holding_check["shares"] or 0):
                logger.warning(f"交易 {tx_id} 卖出份额 {sub_shares_check} 超过持有 {holding_check['shares']}")
                conn.close()
                return False

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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # ── 新基金买入：holding_id 为空时自动创建持仓 ──
    if tx_type == "buy" and not holding_id and actual_shares and actual_shares > 0:
        fund_code = tx["fund_code"]
        fund_name = tx.get("fund_name", fund_code)
        tx_account = tx.get("account") or get_config('portfolio.default_account', '花无缺')
        # 按账户精确匹配已有持仓，避免跨账户同基金被错误关联
        existing = get_holding_by_fund(fund_code, user_id, account=tx_account)
        if existing:
            holding_id = existing["id"]
            # 更新交易记录关联
            conn2 = _get_conn()
            try:
                conn2.execute("UPDATE portfolio_transactions SET holding_id = ? WHERE id = ?", (holding_id, tx_id))
                conn2.commit()
            finally:
                conn2.close()
        else:
            # 在同一事务中创建持仓 + 关联交易 + 重算，避免并发问题
            conn2 = _get_conn()
            try:
                cursor = conn2.execute("""
                    INSERT INTO portfolio_holdings
                    (fund_code, fund_name, shares, cost_price, current_price, user_id, account)
                    VALUES (?, ?, 0, ?, ?, ?, ?)
                """, (fund_code, fund_name, actual_price, actual_price, user_id, tx_account))
                holding_id = cursor.lastrowid
                conn2.execute("UPDATE portfolio_transactions SET holding_id = ? WHERE id = ?", (holding_id, tx_id))
                conn2.commit()
            except Exception:
                conn2.rollback()
                raise
            finally:
                conn2.close()

    if holding_id:
        _recalculate_holding(holding_id)

    # 捕获交易时点估值快照
    if holding_id:
        snapshot = _capture_valuation_snapshot(holding_id, tx.get("transaction_date", ""))
        if snapshot:
            conn3 = _get_conn()
            try:
                conn3.execute("UPDATE portfolio_transactions SET valuation_snapshot = ? WHERE id = ?",
                             (snapshot, tx_id))
                conn3.commit()
            finally:
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

    # ── 交易确认后，自动更新关联决策状态为 executed ──
    try:
        _auto_update_decision_on_confirm(tx_id, user_id)
    except Exception as e:
        logging.warning(f"[confirm_tx] 决策状态自动更新异常: {e}")

    # ── 基金转换：减少源基金份额，创建/增加目标基金 ──
    if tx_type == "convert" and target_fund_code and actual_shares and actual_shares > 0:
        # 1. 源基金减少份额（通过 _recalculate_holding 已处理）
        # 2. 创建目标基金的买入交易
        target_name = target_fund_name or target_fund_code
        # 目标基金进入源基金所在账户，避免跨账户错误关联
        src_account = tx.get("account")
        if holding_id and not src_account:
            src_h = get_holding(holding_id)
            src_account = src_h.get("account") if src_h else None
        target_holding = get_holding_by_fund(target_fund_code, user_id, account=src_account)
        if not target_holding:
            # 自动创建目标基金持仓（与源基金同账户）
            target_holding_id = create_holding(
                fund_code=target_fund_code, fund_name=target_name,
                shares=0, cost_price=confirmed_price,
                current_price=confirmed_price, user_id=user_id,
                account=src_account,
            )
        else:
            target_holding_id = target_holding["id"]
        # 为目标基金创建一笔确认的买入交易
        conn3 = _get_conn()
        try:
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
        finally:
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

    # ── 持仓变更后失效相关缓存 ──
    try:
        from agent.cache import invalidate_related_caches
        invalidate_related_caches("position_change")
    except Exception:
        pass

    return True


def _effective_trade_date(transaction_date: str, transaction_time: str | None = None) -> str:
    """按 A 股基金 15:00 截止规则计算实际成交 T 日。"""
    try:
        from mcp.trading_calendar import is_trading_day, next_trading_day

        d = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        if not is_trading_day(d):
            return next_trading_day(d).isoformat()
        if transaction_time:
            hour, minute = map(int, transaction_time.split(":"))
            if hour > 15 or (hour == 15 and minute > 0):
                return next_trading_day(d).isoformat()
        return d.isoformat()
    except Exception as e:
        logging.warning(f"_effective_trade_date: trading_calendar 不可用，使用原始日期 {transaction_date} (原因: {e})")
        return transaction_date


def fetch_fund_nav_on_or_before(fund_code: str, nav_date: str) -> dict | None:
    """获取指定日期或之前最近一个交易日的基金单位净值。"""
    try:
        import akshare as ak

        df = call_akshare_with_timeout(
            ak.fund_open_fund_info_em,
            symbol=fund_code, indicator='单位净值走势', timeout=20,
        )
        if df is None or len(df) == 0:
            return None
        target = str(nav_date)
        df = df.copy()
        df["净值日期"] = df["净值日期"].astype(str)
        matched = df[df["净值日期"] <= target]
        if matched.empty:
            return None
        row = matched.iloc[-1]
        return {
            "nav": float(row["单位净值"]),
            "date": str(row["净值日期"]),
        }
    except Exception as e:
        logging.warning(f"[auto-confirm] 获取 {fund_code} {nav_date} 净值失败: {e}")
        return None


def auto_confirm_due_transactions(as_of_date: str | None = None, user_id: str = "default") -> dict:
    """自动确认已到确认日的 pending 交易。

    买入/卖出/转换均按提交时间计算实际 T 日，并用 T 日单位净值确认份额或金额。
    如果某只基金的 T 日净值尚未披露，保留 pending，等待下一次自动任务。

    2026-07-15 修复：跳过今天创建的交易，保证新建买入至少在待确认列表停留一天，
    避免 loadData() 触发自动确认导致新仓瞬间入持仓列表（用户来不及检查/撤销）。
    手动确认按钮不受影响。
    """
    today = as_of_date or date.today().isoformat()
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM portfolio_transactions
        WHERE user_id = ?
          AND status = 'pending'
          AND expected_confirm_date IS NOT NULL
          AND expected_confirm_date <= ?
          AND date(created_at) < date(?)
        ORDER BY expected_confirm_date ASC, id ASC
    """, (user_id, today, today)).fetchall()
    conn.close()

    confirmed = 0
    skipped = []
    errors = []
    for row in rows:
        tx = dict(row)
        trade_date = _effective_trade_date(tx.get("transaction_date") or today, tx.get("transaction_time"))
        nav_data = fetch_fund_nav_on_or_before(tx["fund_code"], trade_date)
        if not nav_data or not nav_data.get("nav"):
            skipped.append({
                "id": tx["id"],
                "fund_code": tx["fund_code"],
                "reason": f"{trade_date} 净值未披露",
            })
            continue
        if nav_data.get("date") and nav_data["date"] != trade_date:
            skipped.append({
                "id": tx["id"],
                "fund_code": tx["fund_code"],
                "reason": f"{trade_date} 净值未披露，最近净值为 {nav_data['date']}",
            })
            continue
        try:
            # 按费率自动计算手续费（开关 fee.auto_calc_enabled，默认 true）
            fee = 0.0
            try:
                from services.fee_calculator import calc_fee_for_tx
                holding = get_holding(tx["holding_id"]) if tx.get("holding_id") else None
                fee, _ = calc_fee_for_tx(tx, float(nav_data["nav"]), holding)
            except Exception as e:
                logger.warning(f"交易 {tx['id']} 手续费计算失败，按0处理: {e}")
            ok = confirm_transaction(tx["id"], confirmed_price=float(nav_data["nav"]), fee=fee)
            if ok:
                confirmed += 1
            else:
                errors.append({"id": tx["id"], "fund_code": tx["fund_code"], "error": "confirm failed"})
        except Exception as e:
            errors.append({"id": tx["id"], "fund_code": tx["fund_code"], "error": str(e)})

    return {
        "checked": len(rows),
        "confirmed": confirmed,
        "skipped": skipped,
        "errors": errors,
        "as_of_date": today,
    }


def settle_transaction(tx_id: int) -> bool:
    """标记卖出交易已到账。"""
    conn = _get_conn()
    try:
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
    finally:
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
    try:
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
    finally:
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
                except Exception:
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
        df = call_akshare_with_timeout(
            ak.fund_open_fund_info_em,
            symbol=fund_code, indicator='单位净值走势', timeout=20,
        )
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
    """获取基金净值历史 + 交易点标记（用于交易行为图表）。优先使用本地缓存。"""
    try:
        from services.fund_data_service import get_or_refresh_fund_nav_history
        records = get_or_refresh_fund_nav_history(fund_code, days=days)
        if not records:
            return None

        nav_history = [
            {"date": r["nav_date"], "nav": r["nav"]}
            for r in records
            if r.get("nav") is not None
        ]

        conn = _get_conn()
        try:
            txs = conn.execute("""
                SELECT t.transaction_type, t.shares, t.price, t.amount, t.transaction_date
                FROM portfolio_transactions t
                WHERE t.fund_code = ? AND t.user_id = ?
                    AND (t.is_system IS NULL OR t.is_system = 0)
                    AND t.status IN ('confirmed', 'settled')
                ORDER BY t.transaction_date ASC
            """, (fund_code, user_id)).fetchall()
        finally:
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
    刷新单个持仓的最新净值并更新数据库，同时缓存到 fund_nav_history。

    返回: {"nav": 0.57, "date": "2026-05-22", "change_pct": -2.1,
           "today_profit": -12.34, "today_change_pct": -2.1} 或 None
    """
    from services.fund_data_service import save_latest_nav

    conn = _get_conn()
    holding = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not holding:
        conn.close()
        return None
    holding = dict(holding)
    fund_code = holding["fund_code"]

    # 跳过已清仓持仓（份额为0），避免无意义的 API 调用
    if (holding.get("shares") or 0) <= 0:
        conn.close()
        return None

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

    # 缓存最新净值到本地
    if nav_date:
        try:
            save_latest_nav(fund_code, nav, nav_date, change_pct)
        except Exception as e:
            logger.warning(f"缓存最新净值失败 {fund_code}: {e}")

    nav_data["today_profit"] = today_profit
    nav_data["today_change_pct"] = change_pct
    return nav_data


def refresh_all_fund_prices(user_id: str = "default") -> list[dict]:
    """
    批量刷新用户所有持仓的最新净值，并缓存到 fund_nav_history。

    返回: [{"fund_code": "161725", "fund_name": "...", "nav": 0.57, "date": "2026-05-22"}, ...]
    """
    from services.fund_data_service import save_latest_nav

    holdings = list_holdings(user_id)
    results = []
    failed_codes = []
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
            failed_codes.append(h["fund_code"])
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

        # 缓存最新净值到本地
        if nav_date:
            try:
                save_latest_nav(h["fund_code"], nav, nav_date, change_pct)
            except Exception as e:
                logger.warning(f"缓存最新净值失败 {h['fund_code']}: {e}")

        results.append({
            "fund_code": h["fund_code"],
            "fund_name": h["fund_name"],
            "nav": nav,
            "date": nav_date,
            "change_pct": change_pct,
            "today_profit": today_profit,
        })

    if failed_codes:
        logger.warning(f"批量刷新净值失败基金: {failed_codes}")

    # 保存刷新状态，便于 /api/portfolio/refresh-status 查询
    try:
        save_analysis_cache("portfolio_last_refresh_status", {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(results),
            "success": sum(1 for r in results if "nav" in r),
            "failed": len(failed_codes),
            "failed_codes": failed_codes,
            "skipped": sum(1 for r in results if r.get("skipped")),
            "details": results,
        })
    except Exception as e:
        logger.warning(f"保存刷新状态失败: {e}")

    return results


# ── 基金信息查询 ──────────────────────────────────────


def lookup_fund_info(fund_code: str) -> dict | None:
    """通过 akshare 查询基金基本信息，自动填充名称、类型、跟踪标的。"""
    try:
        import akshare as ak
        df = call_akshare_with_timeout(
            ak.fund_overview_em, symbol=fund_code, timeout=15,
        )
        if df is None or len(df) == 0:
            return None
        row = df.iloc[0]
        fund_name = str(row.get("基金简称", ""))
        fund_type_str = str(row.get("基金类型", ""))
        fund_code_clean = re.sub(r"（.*?）", "", str(row.get("基金代码", fund_code))).strip()
        return {
            "fund_code": fund_code_clean,
            "fund_name": fund_name,
            "fund_full_name": str(row.get("基金全称", "")),
            "fund_type": fund_type_str,
            "fund_category": classify_fund_category(fund_name, fund_type_str, fund_code_clean),
            "tracking_index": str(row.get("跟踪标的", "")),
            "fund_manager": str(row.get("基金经理人", "")),
            "scale": str(row.get("净资产规模", "")),
            "established": str(row.get("成立日期/规模", "")),
            "benchmark": str(row.get("业绩比较基准", "")),
        }
    except Exception as e:
        print(f"[db] 查询基金信息失败 {fund_code}: {e}")
        return None


def compare_funds(fund_a: str, fund_b: str) -> dict:
    """两只基金六维对比：收益/回撤/波动/费率/规模/经理。

    返回结构化对比结果，含 overall_winner 和 verdict。
    """
    import time as _time

    def _safe_float(val, default=None):
        try:
            if val is None or val == "":
                return default
            v = float(val)
            return v if v == v else default  # NaN check
        except (ValueError, TypeError):
            return default

    def _parse_scale(scale_str: str) -> float | None:
        """解析规模字符串，返回亿元数值。'50亿' -> 50.0"""
        if not scale_str:
            return None
        s = str(scale_str).strip()
        # 匹配 "50亿" / "50.3亿" / "50亿元"
        import re as _re
        m = _re.search(r'([\d.]+)\s*亿', s)
        if m:
            return float(m.group(1))
        # 匹配纯数字（假设单位为亿）
        try:
            return float(s)
        except ValueError:
            return None

    def _get_fund_metrics(fund_code: str) -> dict:
        """获取单只基金的六维指标。"""
        info = lookup_fund_info(fund_code) or {}
        metrics = {
            "code": fund_code,
            "name": info.get("fund_name", fund_code),
            "type": info.get("fund_type", ""),
            "fund_manager": info.get("fund_manager", ""),
            "fee_rate": None,
            "scale": info.get("scale", ""),
            "scale_value": _parse_scale(info.get("scale", "")),
            "return_1y": None,
            "return_3y": None,
            "max_drawdown": None,
            "volatility": None,
        }

        # 尝试从费率字段解析
        # lookup_fund_info 没有直接的费率字段，尝试 akshare 获取
        try:
            import akshare as ak
            # 获取基金费率信息
            fee_df = call_akshare_with_timeout(
                ak.fund_fee_fund_ratio_em, symbol=fund_code, timeout=15,
            )
            if fee_df is not None and len(fee_df) > 0:
                # 取管理费率或申购费率
                for _, row in fee_df.iterrows():
                    fee_type = str(row.get("费用类型", ""))
                    if "管理" in fee_type:
                        metrics["fee_rate"] = _safe_float(row.get("费率"))
                        break
                if metrics["fee_rate"] is None:
                    # 取第一行作为近似
                    metrics["fee_rate"] = _safe_float(fee_df.iloc[0].get("费率"))
        except Exception:
            pass

        # 尝试用 akshare 获取历史净值计算收益/回撤/波动
        nav_series = []  # [(date_str, nav_float), ...]
        try:
            import akshare as ak
            df = call_akshare_with_timeout(
                ak.fund_open_fund_info_em,
                symbol=fund_code, indicator="累计净值走势", timeout=20,
            )
            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    d = str(row.get("净值日期", ""))
                    v = _safe_float(row.get("累计净值"))
                    if d and v is not None:
                        nav_series.append((d, v))
        except Exception:
            pass

        # akshare 失败，降级用本地净值历史
        if not nav_series:
            try:
                local_data = get_fund_nav_history(fund_code, days=1095)
                if local_data and local_data.get("nav_history"):
                    for item in local_data["nav_history"]:
                        d = item.get("date", "")
                        v = _safe_float(item.get("nav"))
                        if d and v is not None:
                            nav_series.append((d, v))
            except Exception:
                pass

        # 也尝试用 fetch_fund_nav_on_or_before 获取最新净值
        if not nav_series:
            try:
                from datetime import date as _date
                nav_data = fetch_fund_nav_on_or_before(fund_code, _date.today().isoformat())
                if nav_data and nav_data.get("nav"):
                    nav_series.append((nav_data.get("date", ""), float(nav_data["nav"])))
            except Exception:
                pass

        if len(nav_series) >= 2:
            nav_series.sort(key=lambda x: x[0])
            latest_nav = nav_series[-1][1]
            latest_date = nav_series[-1][0]

            from datetime import datetime as _dt, timedelta as _td
            try:
                latest_dt = _dt.strptime(latest_date, "%Y-%m-%d")
            except ValueError:
                latest_dt = _dt.now()

            # 1年收益率
            one_year_ago = (latest_dt - _td(days=365)).strftime("%Y-%m-%d")
            nav_1y = None
            for d, v in nav_series:
                if d <= one_year_ago:
                    nav_1y = v
                else:
                    break
            if nav_1y and nav_1y > 0:
                metrics["return_1y"] = round((latest_nav / nav_1y - 1) * 100, 2)

            # 3年收益率
            three_years_ago = (latest_dt - _td(days=1095)).strftime("%Y-%m-%d")
            nav_3y = None
            for d, v in nav_series:
                if d <= three_years_ago:
                    nav_3y = v
                else:
                    break
            if nav_3y and nav_3y > 0:
                metrics["return_3y"] = round((latest_nav / nav_3y - 1) * 100, 2)

            # 最大回撤（近3年）
            max_nav = nav_series[0][1]
            max_dd = 0.0
            for _, v in nav_series:
                if v > max_nav:
                    max_nav = v
                dd = (v - max_nav) / max_nav if max_nav > 0 else 0
                if dd < max_dd:
                    max_dd = dd
            metrics["max_drawdown"] = round(max_dd * 100, 2)

            # 波动率（近1年日收益率标准差年化）
            if len(nav_series) >= 30:
                # 取近1年数据
                one_year_data = [(d, v) for d, v in nav_series if d >= one_year_ago]
                if len(one_year_data) >= 10:
                    returns = []
                    for i in range(1, len(one_year_data)):
                        prev = one_year_data[i - 1][1]
                        curr = one_year_data[i][1]
                        if prev > 0:
                            returns.append((curr / prev - 1))
                    if returns:
                        import statistics
                        vol = statistics.pstdev(returns) * (252 ** 0.5) * 100
                        metrics["volatility"] = round(vol, 2)

        return metrics

    # 获取两只基金指标（各自最多10秒）
    # akshare 调用本身有超时，这里不做额外限制，依赖 akshare 内部超时
    metrics_a = _get_fund_metrics(fund_a)
    metrics_b = _get_fund_metrics(fund_b)

    # 构建对比维度
    comparison = {}
    a_wins = 0
    b_wins = 0

    # 1. 近1年收益率（高的赢）
    ra, rb = metrics_a["return_1y"], metrics_b["return_1y"]
    if ra is not None and rb is not None:
        winner = "a" if ra > rb else ("b" if rb > ra else None)
        if winner == "a": a_wins += 1
        elif winner == "b": b_wins += 1
        comparison["return_1y"] = {"a": ra, "b": rb, "winner": winner}
    elif ra is not None:
        comparison["return_1y"] = {"a": ra, "b": None, "winner": "a"}
        a_wins += 1
    elif rb is not None:
        comparison["return_1y"] = {"a": None, "b": rb, "winner": "b"}
        b_wins += 1
    else:
        comparison["return_1y"] = {"a": None, "b": None, "winner": None}

    # 2. 近3年收益率（高的赢）
    ra, rb = metrics_a["return_3y"], metrics_b["return_3y"]
    if ra is not None and rb is not None:
        winner = "a" if ra > rb else ("b" if rb > ra else None)
        if winner == "a": a_wins += 1
        elif winner == "b": b_wins += 1
        comparison["return_3y"] = {"a": ra, "b": rb, "winner": winner}
    elif ra is not None:
        comparison["return_3y"] = {"a": ra, "b": None, "winner": "a"}
        a_wins += 1
    elif rb is not None:
        comparison["return_3y"] = {"a": None, "b": rb, "winner": "b"}
        b_wins += 1
    else:
        comparison["return_3y"] = {"a": None, "b": None, "winner": None}

    # 3. 最大回撤（小的赢）
    da, db = metrics_a["max_drawdown"], metrics_b["max_drawdown"]
    if da is not None and db is not None:
        winner = "a" if da > db else ("b" if db > da else None)  # 回撤值更大=更小回撤
        if winner == "a": a_wins += 1
        elif winner == "b": b_wins += 1
        comparison["max_drawdown"] = {"a": da, "b": db, "winner": winner}
    elif da is not None:
        comparison["max_drawdown"] = {"a": da, "b": None, "winner": "a"}
        a_wins += 1
    elif db is not None:
        comparison["max_drawdown"] = {"a": None, "b": db, "winner": "b"}
        b_wins += 1
    else:
        comparison["max_drawdown"] = {"a": None, "b": None, "winner": None}

    # 4. 波动率（小的赢）
    va, vb = metrics_a["volatility"], metrics_b["volatility"]
    if va is not None and vb is not None:
        winner = "a" if va < vb else ("b" if vb < va else None)
        if winner == "a": a_wins += 1
        elif winner == "b": b_wins += 1
        comparison["volatility"] = {"a": va, "b": vb, "winner": winner}
    elif va is not None:
        comparison["volatility"] = {"a": va, "b": None, "winner": "a"}
        a_wins += 1
    elif vb is not None:
        comparison["volatility"] = {"a": None, "b": vb, "winner": "b"}
        b_wins += 1
    else:
        comparison["volatility"] = {"a": None, "b": None, "winner": None}

    # 5. 费率（低的赢）
    fa, fb = metrics_a["fee_rate"], metrics_b["fee_rate"]
    if fa is not None and fb is not None:
        winner = "a" if fa < fb else ("b" if fb < fa else None)
        if winner == "a": a_wins += 1
        elif winner == "b": b_wins += 1
        comparison["fee_rate"] = {"a": fa, "b": fb, "winner": winner}
    elif fa is not None:
        comparison["fee_rate"] = {"a": fa, "b": None, "winner": "a"}
        a_wins += 1
    elif fb is not None:
        comparison["fee_rate"] = {"a": None, "b": fb, "winner": "b"}
        b_wins += 1
    else:
        comparison["fee_rate"] = {"a": None, "b": None, "winner": None}

    # 6. 规模（大的赢）
    sa, sb = metrics_a["scale_value"], metrics_b["scale_value"]
    if sa is not None and sb is not None:
        winner = "a" if sa > sb else ("b" if sb > sa else None)
        if winner == "a": a_wins += 1
        elif winner == "b": b_wins += 1
        comparison["scale"] = {"a": metrics_a["scale"], "b": metrics_b["scale"], "winner": winner}
    elif sa is not None:
        comparison["scale"] = {"a": metrics_a["scale"], "b": None, "winner": "a"}
        a_wins += 1
    elif sb is not None:
        comparison["scale"] = {"a": None, "b": metrics_b["scale"], "winner": "b"}
        b_wins += 1
    else:
        comparison["scale"] = {"a": metrics_a["scale"], "b": metrics_b["scale"], "winner": None}

    # 综合胜出
    if a_wins > b_wins:
        overall_winner = "a"
    elif b_wins > a_wins:
        overall_winner = "b"
    else:
        overall_winner = "tie"

    # 生成评语
    name_a = metrics_a["name"]
    name_b = metrics_b["name"]
    if overall_winner == "a":
        # 分析A的优势维度
        a_advantages = []
        b_advantages = []
        for dim, label in [("return_1y", "近1年收益"), ("return_3y", "近3年收益"),
                           ("max_drawdown", "回撤控制"), ("volatility", "波动率"),
                           ("fee_rate", "费率"), ("scale", "规模")]:
            w = comparison.get(dim, {}).get("winner")
            if w == "a":
                a_advantages.append(label)
            elif w == "b":
                b_advantages.append(label)
        a_str = "、".join(a_advantages) if a_advantages else "综合指标"
        b_str = "、".join(b_advantages) if b_advantages else "部分维度"
        verdict = f"综合来看，{name_a}更适合追求收益与性价比的投资者。"
        if b_advantages:
            verdict += f"虽然{name_b}在{b_str}上占优，但{name_a}在{a_str}方面表现更突出。"
        else:
            verdict += f"{name_a}在{a_str}方面全面领先。"
    elif overall_winner == "b":
        a_advantages = []
        b_advantages = []
        for dim, label in [("return_1y", "近1年收益"), ("return_3y", "近3年收益"),
                           ("max_drawdown", "回撤控制"), ("volatility", "波动率"),
                           ("fee_rate", "费率"), ("scale", "规模")]:
            w = comparison.get(dim, {}).get("winner")
            if w == "a":
                a_advantages.append(label)
            elif w == "b":
                b_advantages.append(label)
        a_str = "、".join(a_advantages) if a_advantages else "部分维度"
        b_str = "、".join(b_advantages) if b_advantages else "综合指标"
        verdict = f"综合来看，{name_b}更适合追求稳健与性价比的投资者。"
        if a_advantages:
            verdict += f"虽然{name_a}在{a_str}上占优，但{name_b}在{b_str}方面表现更突出。"
        else:
            verdict += f"{name_b}在{b_str}方面全面领先。"
    else:
        verdict = f"两只基金各项指标势均力敌。{name_a}和{name_b}在不同维度各有优势，建议根据个人风险偏好和投资目标选择。"

    return {
        "fund_a": {"code": fund_a, "name": name_a, "type": metrics_a["type"],
                    "fund_manager": metrics_a["fund_manager"]},
        "fund_b": {"code": fund_b, "name": name_b, "type": metrics_b["type"],
                    "fund_manager": metrics_b["fund_manager"]},
        "comparison": comparison,
        "overall_winner": overall_winner,
        "verdict": verdict,
    }


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


def _refine_bond_subcategory(default_category: str, fund_type: str) -> str:
    """P1-1: 把粗分类 'bond' 细化为子类，识别混合二级/可转债/纯债/中短债。

    Args:
        default_category: 默认分类（'bond' / 'bond_pure' 等）
        fund_type: fund_metadata.fund_type 字段，如 "债券型-混合二级"

    Returns:
        细化后的子类：
        - bond_hybrid: 混合二级债基（可持有最多 20% 股票，必须穿透分析）
        - bond_convertible: 可转债基金
        - bond_pure: 纯债基金
        - bond_short: 中短债基金
        - bond: 默认债基（无法判断子类时保留）
    """
    ft = (fund_type or "").strip()
    if not ft:
        return default_category

    # 可转债优先判断
    if "可转" in ft or "转债" in ft:
        return "bond_convertible"
    # 混合二级 / 混合一级
    if "混合" in ft or "二级" in ft or "一级" in ft:
        return "bond_hybrid"
    # 中短债 / 短债
    if "中短债" in ft or "短债" in ft:
        return "bond_short"
    # 纯债
    if "纯债" in ft:
        return "bond_pure"
    # 默认返回原分类
    return default_category


def classify_fund_category(fund_name: str, fund_type: str = "", fund_code: str = "") -> str:
    """根据基金名称、类型和本地缓存分类：equity / bond / hybrid / money_market / index 等。

    优先读取 fund_metadata 缓存的 fund_category，其次用 fund_type，最后退回到名称启发式。

    P1-1: bond 子类细化（bond_pure / bond_hybrid / bond_convertible / bond_short）
    - 修复 conv 129 根因之五：bond_category='bond' 误导 AI 把"混合二级债基"当纯债处理
    - 子类值兼容性：上层使用方对未知值可回退为 'bond'（如 _map_category_to_risk_level）
    """
    name = fund_name.strip()
    ft = fund_type.strip()

    # 1) 优先读取本地 fund_metadata 缓存
    if fund_code:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT fund_category, fund_type FROM fund_metadata WHERE fund_code = ?",
                (fund_code,),
            ).fetchone()
            conn.close()
            if row:
                cached_category = (row["fund_category"] or "").strip()
                cached_type = (row["fund_type"] or "").strip()
                if cached_category:
                    return cached_category
                # 如果只有 fund_type，用它补充
                if cached_type and not ft:
                    ft = cached_type
        except Exception as e:
            logger.warning(f"读取 fund_metadata 分类失败 {fund_code}: {e}")

    # 2) 货币基金
    if any(kw in name for kw in ("货币", "货基", "现金", "流动性", "添利", "增利宝")):
        return "money_market"
    if "同业存单" in name:
        return "money_market"

    # 3) 可转债基金要优先于普通债券判断，避免"可转债"被"债券"关键词提前捕获
    if "可转债" in name or "转债" in name:
        return "convertible_bond"

    # 4) 债券基金 — 纯债
    if any(kw in name for kw in ("纯债", "短债", "长债", "中短债", "中长债", "利率债", "信用债")):
        return _refine_bond_subcategory("bond_pure", ft)
    if any(kw in name for kw in ("债券", "债基", "国债", "政金")):
        return _refine_bond_subcategory("bond", ft)
    if "中债" in name and "指数" in name:
        return "bond_index"

    # 5) 指数基金
    if any(kw in name for kw in ("指数", "ETF", "ETF联接", "联接")):
        # 排除债券指数已在上面判断
        if any(kw in name for kw in ("债", "国债", "政金")):
            return "bond_index"
        return "index"

    # 6) 混合型
    if "混合" in name or "平衡" in name or "灵活" in name:
        if any(kw in ft for kw in ("债券", "债")):
            return _refine_bond_subcategory("bond_hybrid", ft)
        return "hybrid"

    # 7) 根据 fund_type 判断
    if "债券型" in ft:
        return _refine_bond_subcategory("bond", ft)
    if "货币型" in ft:
        return "money_market"
    if "混合型" in ft:
        return "hybrid"
    if "股票型" in ft:
        return "equity"
    if "指数型" in ft:
        return "index"
    if "QDII" in ft:
        return "qdii"
    if "FOF" in ft:
        return "fof"

    # 8) 默认归为 equity
    return "equity"


def get_fund_holdings(fund_code: str, year: str = None) -> dict:
    """获取基金持仓详情：股票重仓 + 债券持仓 + 资产配置（三级兜底：akshare → ttfund → 本地快照）。

    P0-6 系统性修复：conv 129 根因之三修复
    - 问题：akshare `fund_portfolio_hold_em` / `fund_portfolio_bond_hold_em` 对所有基金报
      `JSONDecodeError: Can not decode value starting with character ';'`，导致 top_stocks 永远为空
    - 修复：akshare 失效时依次尝试 ttfund MCP → 本地综合快照表，确保穿透数据非空
    """
    if not year:
        from datetime import datetime
        year = str(datetime.now().year)

    # 第 1 级：akshare 主路径
    result = _akshare_fund_holdings(fund_code, year)
    if result.get("top_stocks"):
        # akshare 成功 → 写入综合快照 + 细粒度快照
        _save_full_snapshot_silent(fund_code, result, "akshare")
        return result

    # 第 2 级：ttfund MCP 兜底（需先登录）
    ttfund_data = _ttfund_fund_holding(fund_code)
    if ttfund_data and ttfund_data.get("top_stocks"):
        # 合并 akshare 的部分有效数据（asset_allocation / industry_allocation 通常仍可用）
        merged = _merge_holdings(result, ttfund_data)
        merged["_data_source"] = "ttfund_fallback"
        _save_full_snapshot_silent(fund_code, merged, "ttfund")
        return merged

    # 第 3 级：本地综合快照表兜底（akshare + ttfund 都失败时）
    try:
        from db.fund_holdings_snapshot import get_latest_full_fund_holdings_snapshot
        snapshot = get_latest_full_fund_holdings_snapshot(fund_code)
        if snapshot and (snapshot.get("top_stocks") or snapshot.get("asset_allocation")):
            snapshot["_data_source"] = "local_snapshot_stale"
            logger.warning(
                f"[fund_holdings] {fund_code} akshare+ttfund 均失败，使用本地快照（updated_at={snapshot.get('_snapshot_updated_at')})"
            )
            return snapshot
    except Exception as e:
        logger.warning(f"[fund_holdings] 本地快照读取失败 {fund_code}: {e}")

    # 全部失败：返回 akshare 的部分结果（可能含 asset_allocation / industry_allocation）
    if not result.get("_data_source"):
        result["_data_source"] = "akshare_partial_failure"
    return result


def _akshare_fund_holdings(fund_code: str, year: str) -> dict:
    """第 1 级：通过 akshare 获取基金持仓（原 get_fund_holdings 主体逻辑）。"""
    result = {
        "fund_code": fund_code,
        "top_stocks": [],
        "bond_holdings": [],
        "asset_allocation": [],
        "industry_allocation": [],
        "bond_type_summary": {},
        "report_date": None,
    }

    # 1. 股票持仓 Top 10
    try:
        import akshare as ak
        df = call_akshare_with_timeout(
            ak.fund_portfolio_hold_em, symbol=fund_code, date=year, timeout=15,
        )
        if df is not None and len(df) > 0:
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
                result["report_date"] = str(latest_q)
    except Exception as e:
        logger.warning(f"[fund_holdings] akshare 股票持仓失败 {fund_code}: {e}")

    # 2. 债券持仓
    bond_type_counter = {}
    try:
        import akshare as ak
        df = call_akshare_with_timeout(
            ak.fund_portfolio_bond_hold_em, symbol=fund_code, date=year, timeout=15,
        )
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
        logger.warning(f"[fund_holdings] akshare 债券持仓失败 {fund_code}: {e}")

    result["bond_type_summary"] = {k: round(v, 2) for k, v in bond_type_counter.items()}

    # 3. 资产配置（股票/债券/现金/其他）
    try:
        import akshare as ak
        df = call_akshare_with_timeout(
            ak.fund_individual_detail_hold_xq, symbol=fund_code, timeout=15,
        )
        if df is not None and len(df) > 0:
            for _, r in df.iterrows():
                result["asset_allocation"].append({
                    "type": str(r.get("资产类型", "")),
                    "pct": str(r.get("仓位占比", "")),
                })
    except Exception as e:
        logger.warning(f"[fund_holdings] akshare 资产配置失败 {fund_code}: {e}")

    # 4. 行业配置
    try:
        import akshare as ak
        df = call_akshare_with_timeout(
            ak.fund_portfolio_industry_allocation_em,
            symbol=fund_code, date=year, timeout=15,
        )
        if df is not None and len(df) > 0:
            for _, r in df.head(10).iterrows():
                result["industry_allocation"].append({
                    "industry": str(r.get("行业类别", "")),
                    "pct_nav": float(r.get("占净值比例", 0)),
                })
    except Exception as e:
        logger.warning(f"[fund_holdings] akshare 行业配置失败 {fund_code}: {e}")

    # 细粒度快照写入（仅当有股票持仓且有季报日期时；保持原行为）
    if result["top_stocks"] and result.get("report_date"):
        try:
            from db.fund_holdings_snapshot import save_fund_holdings_snapshot
            save_fund_holdings_snapshot(fund_code, result["report_date"], result["top_stocks"])
        except Exception as e:
            logger.warning(f"[fund_holdings] 细粒度快照写入失败 {fund_code}: {e}")

    return result


def _ttfund_fund_holding(fund_code: str) -> dict | None:
    """第 2 级：通过 ttfund MCP 获取基金持仓（akshare 失效时兜底）。

    ttfund MCP 需先登录才能调用，未登录时返回 None 并打印警告。
    """
    try:
        from mcp.ttfund_client import TtfundClient
        client = TtfundClient()
        raw = client.fund_holding(fund_code)
        if not raw:
            return None
        # 转换 ttfund 返回结构为标准 schema
        return _normalize_ttfund_holding(fund_code, raw)
    except RuntimeError as e:
        # ttfund 未登录
        logger.warning(f"[fund_holdings] ttfund MCP 未登录或不可用 {fund_code}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[fund_holdings] ttfund 兜底失败 {fund_code}: {e}")
        return None


def _normalize_ttfund_holding(fund_code: str, raw: dict) -> dict:
    """把 ttfund MCP 的 fund_holding 返回值标准化为 get_fund_holdings 同结构。"""
    result = {
        "fund_code": fund_code,
        "top_stocks": [],
        "bond_holdings": [],
        "asset_allocation": [],
        "industry_allocation": [],
        "bond_type_summary": {},
        "report_date": None,
    }
    # ttfund 字段名: top_holdings / asset_allocation / industry_allocation
    for s in (raw.get("top_holdings") or [])[:10]:
        result["top_stocks"].append({
            "stock_code": str(s.get("stock_code", "")),
            "stock_name": str(s.get("stock_name", "")),
            "pct_nav": float(s.get("pct_nav", 0) or 0),
            "shares": float(s.get("shares", 0) or 0),
            "market_value": float(s.get("market_value", 0) or 0),
        })
    for b in (raw.get("bond_holdings") or [])[:10]:
        bond_name = str(b.get("bond_name", ""))
        btype = classify_bond_type(bond_name)
        result["bond_holdings"].append({
            "bond_code": str(b.get("bond_code", "")),
            "bond_name": bond_name,
            "pct_nav": float(b.get("pct_nav", 0) or 0),
            "market_value": float(b.get("market_value", 0) or 0),
            "bond_type": btype,
        })
        result["bond_type_summary"][btype] = result["bond_type_summary"].get(btype, 0) + float(b.get("pct_nav", 0) or 0)
    for a in (raw.get("asset_allocation") or []):
        result["asset_allocation"].append({
            "type": str(a.get("type", "")),
            "pct": str(a.get("pct", "")),
        })
    for ind in (raw.get("industry_allocation") or [])[:10]:
        result["industry_allocation"].append({
            "industry": str(ind.get("industry", "")),
            "pct_nav": float(ind.get("pct_nav", 0) or 0),
        })
    result["bond_type_summary"] = {k: round(v, 2) for k, v in result["bond_type_summary"].items()}
    result["report_date"] = raw.get("report_date")
    return result


def _merge_holdings(ak_data: dict, ttfund_data: dict) -> dict:
    """合并 akshare 部分有效数据（asset_allocation / industry_allocation）+ ttfund 数据。"""
    merged = dict(ttfund_data)
    # 优先用 akshare 的 asset_allocation / industry_allocation（通常仍可用）
    if ak_data.get("asset_allocation") and not merged.get("asset_allocation"):
        merged["asset_allocation"] = ak_data["asset_allocation"]
    if ak_data.get("industry_allocation") and not merged.get("industry_allocation"):
        merged["industry_allocation"] = ak_data["industry_allocation"]
    if ak_data.get("bond_type_summary") and not merged.get("bond_type_summary"):
        merged["bond_type_summary"] = ak_data["bond_type_summary"]
    return merged


def _save_full_snapshot_silent(fund_code: str, data: dict, data_source: str) -> None:
    """静默写入综合快照（失败不阻塞主流程）。"""
    try:
        from db.fund_holdings_snapshot import save_full_fund_holdings_snapshot
        save_full_fund_holdings_snapshot(fund_code, data, data_source)
    except Exception as e:
        logger.warning(f"[fund_holdings] 综合快照写入失败 {fund_code}: {e}")


# ── 风险预警 CRUD ──────────────────────────────────────


def create_alert(alert_type: str, title: str, content: str = None,
                 severity: str = "info", related_fund_code: str = None,
                 related_fund_name: str = None, source: str = None,
                 user_id: str = "default", holding_id: int = None) -> int:
    """新增风险预警，返回 alert_id。24小时内同标题+severity不重复生成。

    Args:
        holding_id: 关联持仓ID（P0-3.1 FK 强关联，可选）
    """
    conn = _get_conn()
    try:
        # P1-3.2：daily_advice_signal 同日同基金去重（更新而非新建）
        # 原有 24h title+severity 去重对 daily_advice_signal 无效（title 是动态摘要文本）
        if alert_type == "daily_advice_signal" and related_fund_code:
            today = datetime.now().strftime("%Y-%m-%d")
            existing_da = conn.execute("""
                SELECT id FROM portfolio_alerts
                WHERE user_id = ? AND alert_type = 'daily_advice_signal'
                  AND related_fund_code = ?
                  AND date(created_at) = ?
                ORDER BY id DESC LIMIT 1
            """, (user_id, related_fund_code, today)).fetchone()
            if existing_da:
                conn.execute("""
                    UPDATE portfolio_alerts
                    SET title = ?, content = ?, severity = ?, source = ?
                    WHERE id = ?
                """, (title, content, severity, source, existing_da['id']))
                conn.commit()
                return existing_da['id']

        # 去重：24小时内同 title + severity 不重复
        existing = conn.execute("""
            SELECT id FROM portfolio_alerts
            WHERE user_id = ? AND title = ? AND severity = ?
              AND created_at > datetime('now', '-1 day')
            LIMIT 1
        """, (user_id, title, severity)).fetchone()
        if existing:
            conn.close()
            return existing['id']

        # 自动推断 holding_id：若未传入但有 related_fund_code，尝试匹配持仓
        if holding_id is None and related_fund_code:
            try:
                h = conn.execute(
                    "SELECT id FROM portfolio_holdings WHERE fund_code = ? AND user_id = ? LIMIT 1",
                    (related_fund_code, user_id),
                ).fetchone()
                if h:
                    holding_id = h["id"]
            except Exception:
                pass

        cur = conn.execute("""
            INSERT INTO portfolio_alerts
                (user_id, alert_type, severity, title, content,
                 related_fund_code, related_fund_name, source, holding_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, alert_type, severity, title, content,
              related_fund_code, related_fund_name, source, holding_id))
        alert_id = cur.lastrowid
        conn.commit()
        return alert_id
    finally:
        conn.close()


def backfill_alert_holding_id() -> int:
    """回填 portfolio_alerts.holding_id（用 related_fund_code 匹配持仓）。

    设计稿 P0-3.1：建立持仓↔预警 FK 强关联。
    历史预警只有 related_fund_code 字符串，本函数回填 holding_id。

    Returns:
        回填条数
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, related_fund_code FROM portfolio_alerts "
            "WHERE holding_id IS NULL AND related_fund_code IS NOT NULL "
            "AND related_fund_code != ''"
        ).fetchall()
        updated = 0
        for r in rows:
            h = conn.execute(
                "SELECT id FROM portfolio_holdings WHERE fund_code = ? LIMIT 1",
                (r["related_fund_code"],),
            ).fetchone()
            if h:
                conn.execute(
                    "UPDATE portfolio_alerts SET holding_id = ? WHERE id = ?",
                    (h["id"], r["id"]),
                )
                updated += 1
        conn.commit()
        return updated
    finally:
        conn.close()


def list_alerts(user_id: str = "default", limit: int = 50,
                unread_only: bool = False) -> list[dict]:
    """获取预警列表（按 alert_type+fund_code+severity 跨日合并），含可靠性标注。

    P0-1: 跨日合并 — 同基金同类型同 severity 的预警合并为一条，cnt 标注次数。
          title/content/source/related_fund_name 取最新一条记录。
    P0-2: 可靠性标注 — 基于 alert_accuracy_stats 回测数据附加 reliability 字段。
    """
    conn = _get_conn()
    try:
        if unread_only:
            where = "WHERE user_id = ? AND is_read = 0"
            params: list = [user_id]
        else:
            where = "WHERE user_id = ?"
            params = [user_id]

        rows = conn.execute(f"""
            SELECT alert_type, related_fund_code, severity,
                   COUNT(*) as cnt,
                   MAX(created_at) as latest_at,
                   MAX(id) as latest_id,
                   MIN(created_at) as first_at
            FROM portfolio_alerts
            {where}
            GROUP BY alert_type, COALESCE(related_fund_code, ''), severity
            ORDER BY latest_at DESC
            LIMIT ?
        """, (*params, limit)).fetchall()

        if not rows:
            return []

        # 批量查最新一条的 title/content/source/related_fund_name/acknowledged_status
        latest_ids = [r["latest_id"] for r in rows]
        placeholders = ",".join(["?"] * len(latest_ids))
        detail_rows = conn.execute(
            f"SELECT id, title, content, source, related_fund_name, acknowledged_status "
            f"FROM portfolio_alerts WHERE id IN ({placeholders})",
            latest_ids,
        ).fetchall()
        detail_map = {r["id"]: dict(r) for r in detail_rows}

        result = []
        for r in rows:
            r = dict(r)
            d = detail_map.get(r["latest_id"], {})
            r["title"] = d.get("title", "")
            r["content"] = d.get("content", "")
            r["source"] = d.get("source", "")
            r["related_fund_name"] = d.get("related_fund_name")
            r["acknowledged_status"] = d.get("acknowledged_status")
            result.append(r)
    finally:
        conn.close()

    # P0-2: 附加可靠性（数据量小，Python 层 join）
    _enrich_reliability(result)
    return result


def _enrich_reliability(alerts: list[dict]) -> None:
    """P0-2: 给预警列表附加 reliability 字段（基于 alert_accuracy_stats 回测数据）。

    原地修改 alerts，无返回值。取最近 4 周回测的聚合数据。
    """
    if not alerts:
        return
    conn = _get_conn()
    try:
        stats_rows = conn.execute("""
            SELECT alert_type, severity,
                   AVG(win_rate) as avg_win_rate,
                   SUM(sample_count) as total_samples,
                   AVG(avg_followup_change) as avg_change
            FROM alert_accuracy_stats
            WHERE week_start >= date('now','localtime','-28 days')
            GROUP BY alert_type, severity
        """).fetchall()
    finally:
        conn.close()

    stats_map = {(r["alert_type"], r["severity"]): dict(r) for r in stats_rows}
    for a in alerts:
        key = (a.get("alert_type"), a.get("severity"))
        a["reliability"] = _calc_reliability(
            a.get("alert_type", ""), a.get("severity", ""), stats_map.get(key)
        )


def _calc_reliability(alert_type: str, severity: str, stats: dict | None) -> dict:
    """根据回测数据计算可靠性等级。

    规则：
      - 高：胜率 ≥ 80% 且样本 ≥ 3
      - 中：胜率 ≥ 60% 或样本 < 3（数据不足）
      - 低：胜率 < 60%
      - 无数据：unknown（前端显示灰色"未回测"）
    特殊：valuation_opportunity 一律标"低"（低估值不等于买点，需结合趋势）
    """
    if alert_type == "valuation_opportunity":
        return {
            "level": "low", "label": "低",
            "reason": "低估值不等于买点，可能继续下跌",
            "win_rate": None, "samples": None, "avg_change": None,
        }
    if not stats or not stats.get("total_samples"):
        return {
            "level": "unknown", "label": "未回测",
            "reason": "暂无历史回测数据",
            "win_rate": None, "samples": 0, "avg_change": None,
        }
    win_rate = stats["avg_win_rate"] or 0
    samples = stats["total_samples"] or 0
    avg_change = stats["avg_change"]
    if win_rate >= 80 and samples >= 3:
        level, label = "high", "高"
    elif win_rate >= 60 or samples < 3:
        level, label = "medium", "中"
    else:
        level, label = "low", "低"
    if avg_change is not None:
        reason = f"近4周回测：胜率{win_rate:.0f}% / 样本{samples} / 平均{avg_change:+.2f}%"
    else:
        reason = f"近4周回测：胜率{win_rate:.0f}% / 样本{samples}"
    return {
        "level": level, "label": label, "reason": reason,
        "win_rate": round(win_rate, 1),
        "samples": samples,
        "avg_change": round(avg_change, 2) if avg_change is not None else None,
    }


def get_unread_alert_count(user_id: str = "default") -> int:
    """获取未读预警数量。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM portfolio_alerts WHERE user_id = ? AND is_read = 0",
        (user_id,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def cleanup_old_alerts(user_id: str = "default", days: int = 30) -> int:
    """清理已读预警中超过 N 天的记录，返回删除条数。"""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            DELETE FROM portfolio_alerts
            WHERE user_id = ? AND is_read = 1 AND created_at < datetime('now', ? || ' days')
        """, (user_id, f"-{days}"))
        deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def get_alert_history(alert_id: int, days: int = 30) -> list[dict]:
    """P1-3.1：查询同持仓同类型预警的历史记录。

    用于预警卡片"历史对比"展示，判断是否反复发生。
    """
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM portfolio_alerts WHERE id = ?", (alert_id,)
        ).fetchone()
        if not cur:
            return []
        cur = dict(cur)
        if not cur.get("related_fund_code"):
            return []
        rows = conn.execute("""
            SELECT id, alert_type, severity, title, content, created_at, is_read, source
            FROM portfolio_alerts
            WHERE alert_type = ? AND related_fund_code = ?
              AND created_at >= datetime('now','localtime', ?)
            ORDER BY created_at DESC LIMIT 20
        """, (cur["alert_type"], cur["related_fund_code"], f"-{days} days")).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_alert_read(alert_id: int) -> bool:
    """标记预警为已读（同时标记同标题+severity的所有未读预警）。"""
    conn = _get_conn()
    # 先获取该 alert 的 title+severity
    row = conn.execute(
        "SELECT title, severity, user_id FROM portfolio_alerts WHERE id = ?",
        (alert_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    cur = conn.execute(
        "UPDATE portfolio_alerts SET is_read = 1 WHERE title = ? AND severity = ? AND user_id = ? AND is_read = 0",
        (row["title"], row["severity"], row["user_id"])
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def delete_alert(alert_id: int) -> bool:
    """删除预警（同时删除同标题+severity的所有预警）。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT title, severity, user_id FROM portfolio_alerts WHERE id = ?",
        (alert_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    cur = conn.execute(
        "DELETE FROM portfolio_alerts WHERE title = ? AND severity = ? AND user_id = ?",
        (row["title"], row["severity"], row["user_id"])
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def update_alert_acknowledgment(alert_id: int, status: str) -> bool:
    """更新预警业务确认状态（同时更新同分组的所有预警，与 list_alerts 跨日合并一致）。

    用于后续回测用户决策质量。

    Args:
        alert_id: 预警 ID（合并视图中的 latest_id）
        status: 'acknowledged'（已采纳）或 'ignored'（已忽略）
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT alert_type, related_fund_code, severity, user_id "
            "FROM portfolio_alerts WHERE id = ?",
            (alert_id,)
        ).fetchone()
        if not row:
            return False
        # 按 list_alerts 的跨日合并分组键（alert_type + fund_code + severity + user_id）批量更新
        cur = conn.execute(
            "UPDATE portfolio_alerts SET acknowledged_status = ? "
            "WHERE alert_type = ? AND COALESCE(related_fund_code, '') = COALESCE(?, '') "
            "AND severity = ? AND user_id = ?",
            (status, row["alert_type"], row["related_fund_code"],
             row["severity"], row["user_id"])
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


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
    for h in active:
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
        max_value = max((h.get("current_value", 0) or 0) for h in active)
        max_holding_pct = round(max_value / total_value * 100, 2)

    return {
        "holding_count": len(active),
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
            AND (is_system IS NULL OR is_system = 0) AND (is_hypothetical IS NULL OR is_hypothetical = 0)
    """, (user_id,)).fetchall()
    buy_count = buy_rows[0]["tx_count"] if buy_rows else 0
    buy_total = buy_rows[0]["total_amount"] or 0 if buy_rows else 0

    # 卖出统计（排除系统交易）
    sell_rows = conn.execute("""
        SELECT COUNT(*) as tx_count, SUM(amount) as total_amount
        FROM portfolio_transactions
        WHERE user_id = ? AND transaction_type = 'sell' AND (status IN ('confirmed', 'settled') OR status IS NULL)
            AND (is_system IS NULL OR is_system = 0) AND (is_hypothetical IS NULL OR is_hypothetical = 0)
    """, (user_id,)).fetchall()
    sell_count = sell_rows[0]["tx_count"] if sell_rows else 0
    sell_total = sell_rows[0]["total_amount"] or 0 if sell_rows else 0

    # 最近交易明细（含基金名称 + pending 交易的提交金额/份额）
    recent = conn.execute("""
        SELECT t.id, t.fund_code, t.transaction_type, t.shares, t.price, t.amount,
               t.transaction_date, t.status, t.is_system, t.fee,
               t.submitted_amount, t.submitted_shares, t.valuation_snapshot,
               COALESCE(t.fund_name, h.fund_name, '') as fund_name,
               COALESCE(h.index_name, '') as index_name
        FROM portfolio_transactions t
        LEFT JOIN portfolio_holdings h ON t.holding_id = h.id
        WHERE t.user_id = ? AND (t.is_system IS NULL OR t.is_system = 0)
            AND (t.is_hypothetical IS NULL OR t.is_hypothetical = 0)
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


def analyze_trade_patterns(user_id: str = "default") -> dict:
    """分析用户真实交易行为模式（排除系统交易）。

    返回追涨倾向、杀跌倾向、持有耐心、频繁交易度、胜率、最大单笔亏损、交易风格判定。
    """
    conn = _get_conn()

    # 获取所有非系统交易（含 fund_name 从 holdings 关联）
    rows = conn.execute("""
        SELECT t.id, t.holding_id, t.fund_code, t.fund_name, t.transaction_type,
               t.shares, t.price, t.amount, t.transaction_date, t.status, t.is_system,
               h.index_code, h.fund_name as holding_fund_name
        FROM portfolio_transactions t
        LEFT JOIN portfolio_holdings h ON t.holding_id = h.id
        WHERE t.user_id = ?
          AND (t.is_system IS NULL OR t.is_system = 0)
          AND (t.is_hypothetical IS NULL OR t.is_hypothetical = 0)
          AND (t.status IN ('confirmed', 'settled') OR t.status IS NULL)
        ORDER BY t.transaction_date ASC, t.id ASC
    """, (user_id,)).fetchall()

    if not rows or len(rows) < 3:
        conn.close()
        return {"error": "交易记录不足", "count": len(rows) if rows else 0}

    txs = [dict(r) for r in rows]

    # ── 按基金代码分组，计算买入均价（份额加权）──
    fund_buys = {}  # {fund_code: [{shares, price, amount, date}, ...]}
    fund_sells = []  # [{fund_code, shares, price, amount, date, buy_avg_price}, ...]

    for tx in txs:
        fc = tx["fund_code"]
        if tx["transaction_type"] == "buy":
            fund_buys.setdefault(fc, []).append({
                "shares": tx.get("shares") or 0,
                "price": tx.get("price") or 0,
                "amount": tx.get("amount") or 0,
                "date": tx.get("transaction_date", ""),
            })
        elif tx["transaction_type"] == "sell":
            fund_sells.append({
                "fund_code": fc,
                "shares": tx.get("shares") or 0,
                "price": tx.get("price") or 0,
                "amount": tx.get("amount") or 0,
                "date": tx.get("transaction_date", ""),
            })

    # 计算每只基金的加权平均买入价（卖出时扣减份额）
    def _calc_buy_avg(buy_list, sell_shares_so_far=0):
        """计算当前买入均价（考虑已卖出份额扣减）。"""
        total_cost = 0.0
        total_shares = 0.0
        remaining_sells = sell_shares_so_far
        for b in buy_list:
            shares = b["shares"]
            if remaining_sells > 0:
                if remaining_sells >= shares:
                    remaining_sells -= shares
                    continue
                else:
                    shares -= remaining_sells
                    remaining_sells = 0
            total_cost += b["price"] * shares
            total_shares += shares
        return total_cost / total_shares if total_shares > 0 else 0

    # ── 追涨倾向：买入时的PE分位中位数 ──
    buy_pe_percentiles = []
    for tx in txs:
        if tx["transaction_type"] != "buy":
            continue
        index_code = tx.get("index_code")
        if not index_code:
            continue
        tx_date = tx.get("transaction_date", "")
        val_row = conn.execute("""
            SELECT percentile FROM index_valuations
            WHERE index_code = ? AND metric_type = '市盈率'
              AND snapshot_date BETWEEN date(?, '-7 days') AND date(?, '+7 days')
            ORDER BY ABS(julianday(snapshot_date) - julianday(?))
            LIMIT 1
        """, (index_code, tx_date, tx_date, tx_date)).fetchone()
        if val_row and val_row["percentile"] is not None:
            buy_pe_percentiles.append(float(val_row["percentile"]))

    # ── 杀跌倾向 + 胜率 + 最大单笔亏损 ──
    sell_loss_pcts = []  # 亏损幅度列表（负数=亏损）
    win_count = 0
    max_loss = 0.0  # 最大单笔亏损金额（正数）
    holding_days_list = []  # 持有天数

    # 按基金代码，模拟先进先出计算卖出时的盈亏
    fund_buy_queue = {}  # {fund_code: [{shares, price, date}, ...]}
    for tx in txs:
        fc = tx["fund_code"]
        if tx["transaction_type"] == "buy":
            fund_buy_queue.setdefault(fc, []).append({
                "shares": tx.get("shares") or 0,
                "price": tx.get("price") or 0,
                "date": tx.get("transaction_date", ""),
            })
        elif tx["transaction_type"] == "sell":
            queue = fund_buy_queue.get(fc, [])
            sell_shares = tx.get("shares") or 0
            sell_price = tx.get("price") or 0
            sell_date = tx.get("transaction_date", "")

            remaining = sell_shares
            total_cost = 0.0
            buy_dates_for_this_sell = []
            while remaining > 0 and queue:
                lot = queue[0]
                if lot["shares"] <= remaining:
                    total_cost += lot["shares"] * lot["price"]
                    buy_dates_for_this_sell.append(lot["date"])
                    remaining -= lot["shares"]
                    queue.pop(0)
                else:
                    total_cost += remaining * lot["price"]
                    buy_dates_for_this_sell.append(lot["date"])
                    lot["shares"] -= remaining
                    remaining = 0

            actual_sell_amount = sell_shares * sell_price
            profit = actual_sell_amount - total_cost
            loss_pct = (profit / total_cost * 100) if total_cost > 0 else 0
            sell_loss_pcts.append(loss_pct)

            if profit > 0:
                win_count += 1
            if profit < 0 and abs(profit) > max_loss:
                max_loss = abs(profit)

            # 持有天数：用最早买入日期到卖出日期
            if buy_dates_for_this_sell:
                try:
                    from datetime import datetime as _dt
                    earliest_buy = min(buy_dates_for_this_sell)
                    d_buy = _dt.strptime(earliest_buy[:10], "%Y-%m-%d")
                    d_sell = _dt.strptime(sell_date[:10], "%Y-%m-%d")
                    holding_days_list.append((d_sell - d_buy).days)
                except Exception:
                    pass

    # ── 频繁交易度：月均交易次数 ──
    try:
        from datetime import datetime as _dt2
        dates = [t["transaction_date"] for t in txs if t.get("transaction_date")]
        if dates:
            first_date = _dt2.strptime(min(dates)[:10], "%Y-%m-%d")
            last_date = _dt2.strptime(max(dates)[:10], "%Y-%m-%d")
            months_span = max((last_date - first_date).days / 30.0, 0.5)
            monthly_avg = len(txs) / months_span
        else:
            months_span = 0
            monthly_avg = 0
    except Exception:
        months_span = 0
        monthly_avg = 0

    conn.close()

    # ── 计算统计值 ──
    import statistics

    # 追涨倾向：买入PE分位中位数
    chase_pe_median = round(statistics.median(buy_pe_percentiles), 2) if buy_pe_percentiles else None

    # 杀跌倾向：亏损卖出的亏损幅度中位数
    loss_sells = [p for p in sell_loss_pcts if p < 0]
    panic_sell_median = round(statistics.median(loss_sells), 2) if loss_sells else None

    # 持有耐心：平均持仓天数
    avg_holding_days = round(statistics.mean(holding_days_list), 1) if holding_days_list else None

    # 胜率
    total_sells = len(sell_loss_pcts)
    win_rate = round(win_count / total_sells * 100, 2) if total_sells > 0 else None

    # 最大单笔亏损
    max_single_loss = round(max_loss, 2) if max_loss > 0 else 0.0

    # ── 交易风格判定 ──
    style_tags = []
    if chase_pe_median is not None and chase_pe_median > 60:
        style_tags.append("追涨杀跌型")
    if monthly_avg > 6:
        style_tags.append("频繁交易型")
    if avg_holding_days is not None and avg_holding_days > 180:
        style_tags.append("长期持有型")
    if monthly_avg > 0 and monthly_avg <= 3 and chase_pe_median is not None and chase_pe_median < 50:
        style_tags.append("纪律定投型")
    if not style_tags:
        style_tags.append("均衡型")
    trade_style = "/".join(style_tags)

    return {
        "total_transactions": len(txs),
        "chase_pe_median": chase_pe_median,          # 追涨倾向：买入PE分位中位数
        "panic_sell_median": panic_sell_median,       # 杀跌倾向：亏损卖出幅度中位数(%)
        "avg_holding_days": avg_holding_days,          # 持有耐心：平均持仓天数
        "monthly_avg_trades": round(monthly_avg, 2),    # 频繁交易度：月均交易次数
        "win_rate": win_rate,                          # 胜率(%)
        "max_single_loss": max_single_loss,             # 最大单笔亏损金额
        "total_sells": total_sells,
        "loss_sells_count": len(loss_sells),
        "trade_style": trade_style,                     # 交易风格判定
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
        conn.close()
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
                   token_usage, agent_id, root_cause, root_cause_detail, created_at
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
                'root_cause': d.get('root_cause', ''),
                'root_cause_detail': d.get('root_cause_detail', ''),
                'metadata': {'token_usage': d.get('token_usage'), 'agent_id': d.get('agent_id')},
                'created_at': d.get('created_at', ''),
            })

    if source != 'analysis':
        # 来源 B: llm_feedback (chat / specialist 等)
        rows = conn.execute("""
            SELECT id, caller, input_summary, output_summary, rating, tags, comment,
                   root_cause, root_cause_detail, created_at
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
                'root_cause': d.get('root_cause', ''),
                'root_cause_detail': d.get('root_cause_detail', ''),
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


# ── 持仓快照（每日记录市值） ──────────────────────────────

def save_portfolio_snapshot(user_id: str = "default") -> int:
    """保存当前持仓市值快照，返回 snapshot id。"""
    conn = _get_conn()
    try:
        holdings = list_holdings(user_id)
        total_value = sum(h.get("current_value", 0) or 0 for h in holdings)
        total_cost = sum((h.get("shares", 0) or 0) * (h.get("cost_price", 0) or 0) for h in holdings)
        cash = get_cash_balance(user_id).get("balance", 0)
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        cur = conn.execute(
            "INSERT OR REPLACE INTO portfolio_snapshots (user_id, snapshot_date, total_value, total_cost, cash, holding_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, today, round(total_value, 2), round(total_cost, 2), round(cash, 2), len(holdings))
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_portfolio_snapshots(user_id: str = "default", limit: int = 365) -> list[dict]:
    """查询持仓快照历史。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE user_id = ? ORDER BY snapshot_date DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ── 持仓快照（P1 优化：含盈亏率/总资产/持仓明细 JSON） ──────────────────────────────


def create_snapshot(snapshot_date: str = None, user_id: str = "default") -> dict | None:
    """记录当日持仓快照（幂等：同一天覆盖）。返回快照 dict。

    与 save_portfolio_snapshot 的区别：本函数额外记录 total_profit_loss /
    total_profit_rate / cash_balance / total_assets / holdings_json，
    供 P1 分析 API（盈亏趋势、配置分布回溯）使用。
    """
    if snapshot_date is None:
        snapshot_date = datetime.now().strftime("%Y-%m-%d")

    summary = get_portfolio_summary(user_id)
    holdings = list_holdings(user_id)
    holdings_data = [
        {
            "fund_code": h.get("fund_code"),
            "fund_name": h.get("fund_name"),
            "shares": h.get("shares", 0),
            "current_price": h.get("current_price"),
            "current_value": h.get("current_value"),
            "profit_loss": h.get("profit_loss"),
            "profit_rate": h.get("profit_rate"),
            "account": h.get("account"),
            "fund_category": h.get("fund_category"),
        }
        for h in holdings
        if (h.get("shares") or 0) > 0
    ]

    total_cost = summary.get("total_cost", 0) or 0
    total_value = summary.get("total_value", 0) or 0
    total_profit_loss = summary.get("total_profit_loss", 0) or summary.get("total_profit", 0) or 0
    total_profit_rate = (total_profit_loss / total_cost) if total_cost > 0 else 0
    cash_balance = summary.get("cash_balance", 0) or 0
    total_assets = summary.get("total_assets", 0) or 0
    holdings_json = json.dumps(holdings_data, ensure_ascii=False)

    conn = _get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO portfolio_snapshots
                (user_id, snapshot_date, total_cost, total_value, total_profit_loss,
                 total_profit_rate, cash_balance, total_assets, holdings_json,
                 cash, holding_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, snapshot_date, total_cost, total_value, total_profit_loss,
              total_profit_rate, cash_balance, total_assets, holdings_json,
              cash_balance, len(holdings_data)))
        conn.commit()
    finally:
        conn.close()

    return {
        "snapshot_date": snapshot_date,
        "total_cost": total_cost,
        "total_value": total_value,
        "total_profit_loss": total_profit_loss,
        "total_profit_rate": total_profit_rate,
        "cash_balance": cash_balance,
        "total_assets": total_assets,
        "holdings_json": holdings_json,
    }


def list_snapshots(start_date: str = None, end_date: str = None,
                   limit: int = 90, user_id: str = "default") -> list[dict]:
    """查询快照列表（默认最近 90 天）。按日期倒序。"""
    conn = _get_conn()
    try:
        conditions = ["user_id = ?"]
        params: list = [user_id]
        if start_date:
            conditions.append("snapshot_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("snapshot_date <= ?")
            params.append(end_date)
        where = " WHERE " + " AND ".join(conditions)
        params.append(limit)
        rows = conn.execute(f"""
            SELECT * FROM portfolio_snapshots{where}
            ORDER BY snapshot_date DESC LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_snapshot(user_id: str = "default") -> dict | None:
    """获取最新快照。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE user_id = ? ORDER BY snapshot_date DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── 持仓分析（P1 优化：配置分布 / 分基金盈亏 / 集中度） ──────────────────────────────


def get_distribution_analysis(user_id: str = "default") -> dict:
    """配置分布分析：按账户、按基金类别聚合市值占比。

    返回:
        {
            "by_account": [{"name": "花无缺", "value": 1200.0, "pct": 0.6}, ...],
            "by_category": [{"name": "equity", "value": 2000.0, "pct": 1.0}, ...],
            "total_value": 2000.0,
        }
    """
    holdings = list_holdings(user_id)
    active = [h for h in holdings if (h.get("shares") or 0) > 0]
    total_value = sum((h.get("current_value") or 0) for h in active)

    by_account: dict[str, float] = {}
    by_category: dict[str, float] = {}
    for h in active:
        value = h.get("current_value") or 0
        account = h.get("account") or "未分类"
        category = h.get("fund_category") or "未分类"
        by_account[account] = by_account.get(account, 0) + value
        by_category[category] = by_category.get(category, 0) + value

    by_account_pct = [
        {"name": k, "value": round(v, 2), "pct": round(v / total_value, 4) if total_value > 0 else 0}
        for k, v in sorted(by_account.items(), key=lambda x: -x[1])
    ]
    by_category_pct = [
        {"name": k, "value": round(v, 2), "pct": round(v / total_value, 4) if total_value > 0 else 0}
        for k, v in sorted(by_category.items(), key=lambda x: -x[1])
    ]

    return {
        "by_account": by_account_pct,
        "by_category": by_category_pct,
        "total_value": round(total_value, 2),
    }


def get_profit_by_fund(user_id: str = "default") -> list[dict]:
    """分基金盈亏分析，按盈亏额倒序排列。排除已清仓记录。

    返回每只基金的: fund_code / fund_name / current_value / total_cost /
    profit_loss / profit_rate / account。
    """
    holdings = list_holdings(user_id)
    result = []
    for h in holdings:
        if (h.get("shares") or 0) <= 0:
            continue
        result.append({
            "fund_code": h.get("fund_code"),
            "fund_name": h.get("fund_name"),
            "current_value": h.get("current_value"),
            "total_cost": h.get("total_cost"),
            "profit_loss": h.get("profit_loss"),
            "profit_rate": h.get("profit_rate"),
            "account": h.get("account"),
        })
    result.sort(key=lambda x: x.get("profit_loss") or 0, reverse=True)
    return result


def get_concentration_analysis(user_id: str = "default") -> dict:
    """集中度分析：单基金占比、Top3 占比、超限预警（>20%）。

    返回:
        {
            "holdings": [{"fund_code","fund_name","value","pct"}, ...],  # 按占比倒序
            "max_concentration": 0.35,        # 最大单基金占比
            "max_fund": {...},                 # 最大占比基金（无持仓时 None）
            "top3_concentration": 0.75,        # Top3 合计占比
            "warning": True,                   # max_concentration > 0.20
        }
    """
    holdings = list_holdings(user_id)
    active = [h for h in holdings if (h.get("shares") or 0) > 0]
    total_value = sum((h.get("current_value") or 0) for h in active)

    items = []
    for h in active:
        value = h.get("current_value") or 0
        items.append({
            "fund_code": h.get("fund_code"),
            "fund_name": h.get("fund_name"),
            "value": round(value, 2),
            "pct": round(value / total_value, 4) if total_value > 0 else 0,
        })
    items.sort(key=lambda x: x["pct"], reverse=True)

    max_conc = items[0]["pct"] if items else 0
    top3_conc = sum(i["pct"] for i in items[:3])

    return {
        "holdings": items,
        "max_concentration": max_conc,
        "max_fund": items[0] if items else None,
        "top3_concentration": round(top3_conc, 4),
        "warning": max_conc > 0.20,
    }
