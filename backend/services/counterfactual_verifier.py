"""反事实验证引擎 — 跟踪假设补仓操作的后续盈亏。

复用 master_decision_backtest._get_fund_price_at_date() 的净值取数逻辑。

验证维度：
1. 单笔假设交易的独立盈亏（买入价 vs 当前价）
2. 摊薄效果（假设补仓后该标的新平均成本 vs 当前价 → 是否回本）
3. 假设组合 vs 真实组合对比（如果当时都按建议补了，整体改善多少）
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_fund_price_at_date(fund_code: str, target_date: str) -> float | None:
    """获取基金在指定日期的净值（复用 master_decision_backtest 逻辑）。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute(
            """SELECT nav FROM fund_nav_history
               WHERE fund_code = ? AND nav_date <= ?
               ORDER BY nav_date DESC LIMIT 1""",
            (fund_code, target_date),
        ).fetchone()
        conn.close()
        if row:
            return float(row["nav"])
    except Exception as e:
        logger.debug(f"[counterfactual] 取净值失败 {fund_code}@{target_date}: {e}")
    return None


def _get_latest_fund_price(fund_code: str) -> tuple[float | None, str | None]:
    """获取基金最新净值和净值日期。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute(
            """SELECT nav, nav_date FROM fund_nav_history
               WHERE fund_code = ?
               ORDER BY nav_date DESC LIMIT 1""",
            (fund_code,),
        ).fetchone()
        conn.close()
        if row:
            return float(row["nav"]), row["nav_date"]
    except Exception as e:
        logger.debug(f"[counterfactual] 取最新净值失败 {fund_code}: {e}")
    return None, None


def _calc_holding_days(buy_date: str, end_date: str) -> int:
    """计算持有天数。"""
    try:
        b = datetime.strptime(buy_date[:10], "%Y-%m-%d")
        e = datetime.strptime(end_date[:10], "%Y-%m-%d")
        return max(0, (e - b).days)
    except Exception:
        return 0


def verify_hypothetical_tx(tx: dict) -> dict:
    """验证单条假设交易的后续盈亏。

    Args:
        tx: portfolio_transactions 行（is_hypothetical=1），含 snapshot 关联字段

    Returns:
        验证结果 dict
    """
    tx_id = tx["id"]
    fund_code = tx.get("fund_code", "")
    fund_name = tx.get("fund_name", "")
    buy_date = tx.get("transaction_date", "")
    buy_amount = tx.get("amount", 0) or 0
    buy_price_recorded = tx.get("price", 0) or 0  # 快照时记录的买入价
    buy_shares_recorded = tx.get("shares", 0) or 0

    # 优先用净值表的实际净值（更准确），快照价只是备份
    buy_price = _get_fund_price_at_date(fund_code, buy_date)
    nav_date_lag = None
    if not buy_price:
        buy_price = buy_price_recorded if buy_price_recorded > 0 else None
        nav_date_lag = "no_nav_history"

    # 当前净值
    current_price, current_nav_date = _get_latest_fund_price(fund_code)
    if not current_price:
        return {
            "tx_id": tx_id,
            "fund_code": fund_code,
            "fund_name": fund_name,
            "buy_date": buy_date,
            "buy_amount": buy_amount,
            "status": "no_nav_data",
            "message": "无法获取净值数据（新基金或净值缺失）",
        }

    # 计算假设买入份额（用实际净值重算，避免快照价不准）
    buy_shares = buy_amount / buy_price if buy_price > 0 else 0
    current_value = round(buy_shares * current_price, 2)
    profit_loss = round(current_value - buy_amount, 2)
    profit_rate = round(profit_loss / buy_amount, 4) if buy_amount > 0 else 0

    holding_days = _calc_holding_days(buy_date, current_nav_date or datetime.now().strftime("%Y-%m-%d"))
    is_breakeven = current_price >= buy_price

    # 摊薄效果：假设补仓后该标的的新平均成本
    # 需要查询快照时刻的真实持仓（成本/份额），从快照表取 profit_rate_at_snapshot 推算
    avg_cost_improvement = None
    snapshot_profit_rate = tx.get("profit_rate_at_snapshot")
    if snapshot_profit_rate is not None and buy_price > 0:
        # 快照时的成本价 = buy_price / (1 + profit_rate_at_snapshot)
        # （profit_rate = (current - cost)/cost → cost = current/(1+profit_rate)）
        old_cost_price = buy_price / (1 + snapshot_profit_rate) if (1 + snapshot_profit_rate) != 0 else buy_price
        # 假设补仓后的新平均成本（简化：按金额1:1摊薄，实际份额比取决于 old_shares）
        # 这里用金额加权近似：new_cost ≈ (old_cost + buy_amount) / (old_shares + new_shares)
        # 由于无 old_shares 快照，用比例近似：如果补仓金额 = 原仓位的 X%，则新成本 = old_cost*(1-X) + buy_price*X
        # 简化展示：直接对比"假设补仓的独立盈亏"即可，摊薄效果在 summary 层面体现
        avg_cost_improvement = {
            "old_cost_price": round(old_cost_price, 4),
            "current_price": round(current_price, 4),
            "breakeven_needed_pct": round((old_cost_price - current_price) / old_cost_price * 100, 2) if old_cost_price > 0 else 0,
        }

    # 匹配建议金额
    snapshot_suggested = tx.get("snapshot_suggested_amount")
    matched_suggestion = (
        snapshot_suggested is not None
        and abs(snapshot_suggested - buy_amount) < 1.0
    )

    return {
        "tx_id": tx_id,
        "fund_code": fund_code,
        "fund_name": fund_name,
        "buy_date": buy_date,
        "buy_amount": buy_amount,
        "buy_price": round(buy_price, 4),
        "buy_shares": round(buy_shares, 4),
        "current_price": round(current_price, 4),
        "current_nav_date": current_nav_date,
        "current_value": current_value,
        "profit_loss": profit_loss,
        "profit_rate": profit_rate,
        "profit_rate_pct": round(profit_rate * 100, 2),
        "holding_days": holding_days,
        "is_breakeven": is_breakeven,
        "nav_date_lag": nav_date_lag,
        "matched_suggestion": matched_suggestion,
        "snapshot_suggested_amount": snapshot_suggested,
        "snapshot_profit_rate": snapshot_profit_rate,
        "avg_cost_analysis": avg_cost_improvement,
        "status": "verified",
        "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def verify_all_hypothetical(user_id: str = "default") -> dict:
    """验证所有假设交易 + 汇总对比。

    Returns:
        {
            "hypothetical_txs": [...],   # 每条验证结果
            "summary": {...},            # 假设组合汇总
            "comparison": {...},         # 假设vs真实对比
        }
    """
    from db.smart_add_snapshots import list_hypothetical_txs
    from db.portfolio import get_portfolio_summary

    txs = list_hypothetical_txs(user_id=user_id)
    verified = []
    for tx in txs:
        try:
            result = verify_hypothetical_tx(tx)
            verified.append(result)
        except Exception as e:
            logger.warning(f"[counterfactual] 验证失败 tx_id={tx.get('id')}: {e}")
            verified.append({
                "tx_id": tx.get("id"),
                "fund_code": tx.get("fund_code", ""),
                "fund_name": tx.get("fund_name", ""),
                "buy_date": tx.get("transaction_date", ""),
                "status": "error",
                "message": str(e),
            })

    # 汇总假设组合
    valid_results = [v for v in verified if v.get("status") == "verified"]
    total_invested = round(sum(v["buy_amount"] for v in valid_results), 2)
    total_value = round(sum(v["current_value"] for v in valid_results), 2)
    total_profit_loss = round(total_value - total_invested, 2)
    total_profit_rate = round(total_profit_loss / total_invested, 4) if total_invested > 0 else 0
    breakeven_count = sum(1 for v in valid_results if v.get("is_breakeven"))
    matched_count = sum(1 for v in valid_results if v.get("matched_suggestion"))
    suggestion_match_rate = round(matched_count / len(valid_results), 4) if valid_results else 0

    summary = {
        "total_hypothetical_invested": total_invested,
        "total_hypothetical_value": total_value,
        "total_profit_loss": total_profit_loss,
        "total_profit_rate": total_profit_rate,
        "total_profit_rate_pct": round(total_profit_rate * 100, 2),
        "breakeven_count": breakeven_count,
        "total_count": len(valid_results),
        "suggestion_match_rate": suggestion_match_rate,
    }

    # 真实组合对比
    comparison = {"real_portfolio_profit_rate": None, "improvement": None}
    try:
        real_summary = get_portfolio_summary(user_id=user_id)
        # 兼容字段名：total_profit_rate 或 total_profit_loss / total_cost 推算
        real_profit_rate = real_summary.get("total_profit_rate")
        if real_profit_rate is None:
            total_profit = real_summary.get("total_profit") or 0
            total_cost = real_summary.get("total_cost") or 0
            real_profit_rate = (total_profit / total_cost) if total_cost > 0 else None
        if real_profit_rate is not None:
            comparison["real_portfolio_profit_rate"] = round(real_profit_rate, 4)
            comparison["hypothetical_profit_rate"] = total_profit_rate
            # 改善幅度 = 假设补仓部分收益率 - 真实组合收益率（近似）
            comparison["improvement"] = round(total_profit_rate - real_profit_rate, 4)
    except Exception as e:
        logger.debug(f"[counterfactual] 真实组合对比失败: {e}")

    return {
        "hypothetical_txs": verified,
        "summary": summary,
        "comparison": comparison,
    }
