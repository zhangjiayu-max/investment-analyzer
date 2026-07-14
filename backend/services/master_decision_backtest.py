"""大师决策回测引擎 — T+7/T+30自动验证大师建议准确性。

复用 db/decisions.py:auto_backtest_decisions 的 baseline+end+benchmark 模式。

判定逻辑：
- strong_buy/dca → 期望涨，涨跌幅≥2%判correct，≤-2%判wrong，中间flat
- reduce → 期望跌，涨跌幅≤-2%判correct，≥2%判wrong
- wait → 中性，对比基准
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 验证阈值（涨跌幅绝对值）
VERIFY_THRESHOLD = 2.0


def _get_fund_price_at_date(fund_code: str, target_date: str) -> float | None:
    """获取基金在指定日期的净值。"""
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
        logger.debug(f"[master_backtest] 取净值失败 {fund_code}@{target_date}: {e}")
    return None


def _verify_single_decision(decision: dict, window_days: int) -> tuple[str, float]:
    """验证单条大师决策。

    Returns:
        (result, change_pct)
        result: correct/wrong/flat
    """
    fund_code = decision["fund_code"]
    action = decision["action"]
    baseline_price = decision.get("baseline_price")
    baseline_date = decision.get("baseline_date", "")

    # 如果没有baseline_price，尝试从净值表取
    if not baseline_price or baseline_price <= 0:
        baseline_price = _get_fund_price_at_date(fund_code, baseline_date)
        if not baseline_price:
            return "flat", 0.0

    # 计算验证日期（baseline_date + window_days）
    try:
        base_dt = datetime.strptime(baseline_date[:10], "%Y-%m-%d")
        end_date = (base_dt + __import__("datetime").timedelta(days=window_days)).strftime("%Y-%m-%d")
    except Exception:
        return "flat", 0.0

    end_price = _get_fund_price_at_date(fund_code, end_date)
    if not end_price or end_price <= 0:
        return "flat", 0.0

    change_pct = (end_price - baseline_price) / baseline_price * 100

    # 判定逻辑
    if action in ("strong_buy", "dca"):
        # 期望涨
        if change_pct >= VERIFY_THRESHOLD:
            return "correct", round(change_pct, 2)
        elif change_pct <= -VERIFY_THRESHOLD:
            return "wrong", round(change_pct, 2)
        else:
            return "flat", round(change_pct, 2)
    elif action == "reduce":
        # 期望跌
        if change_pct <= -VERIFY_THRESHOLD:
            return "correct", round(change_pct, 2)
        elif change_pct >= VERIFY_THRESHOLD:
            return "wrong", round(change_pct, 2)
        else:
            return "flat", round(change_pct, 2)
    elif action == "wait":
        # 中性，涨跌都算flat（wait不预测方向）
        return "flat", round(change_pct, 2)
    else:
        return "flat", round(change_pct, 2)


def auto_backtest_master_decisions() -> dict:
    """T+7和T+30自动回测大师决策。

    Returns:
        {verified_7d, verified_30d, correct_7d, wrong_7d, flat_7d, errors}
    """
    from db.master_decision_history import list_pending_verification, update_verification_result

    stats = {
        "verified_7d": 0, "correct_7d": 0, "wrong_7d": 0, "flat_7d": 0,
        "verified_30d": 0, "correct_30d": 0, "wrong_30d": 0, "flat_30d": 0,
        "errors": 0,
    }

    # T+7验证
    pending_7d = list_pending_verification(7)
    for d in pending_7d:
        try:
            result, change_pct = _verify_single_decision(d, 7)
            update_verification_result(d["id"], 7, result, change_pct)
            stats["verified_7d"] += 1
            if result == "correct":
                stats["correct_7d"] += 1
            elif result == "wrong":
                stats["wrong_7d"] += 1
            else:
                stats["flat_7d"] += 1
        except Exception as e:
            logger.warning(f"[master_backtest] T+7验证失败 id={d['id']}: {e}")
            stats["errors"] += 1

    # T+30验证
    pending_30d = list_pending_verification(30)
    for d in pending_30d:
        try:
            result, change_pct = _verify_single_decision(d, 30)
            update_verification_result(d["id"], 30, result, change_pct)
            stats["verified_30d"] += 1
            if result == "correct":
                stats["correct_30d"] += 1
            elif result == "wrong":
                stats["wrong_30d"] += 1
            else:
                stats["flat_30d"] += 1
        except Exception as e:
            logger.warning(f"[master_backtest] T+30验证失败 id={d['id']}: {e}")
            stats["errors"] += 1

    logger.info(
        f"[master_backtest] 回测完成: T+7验证{stats['verified_7d']}条"
        f"(正确{stats['correct_7d']}/错误{stats['wrong_7d']}/平{stats['flat_7d']}), "
        f"T+30验证{stats['verified_30d']}条"
    )
    return stats
