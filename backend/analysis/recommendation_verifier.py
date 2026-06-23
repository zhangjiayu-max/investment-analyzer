"""推荐自动验证 — 定时验证历史推荐的准确性。

适配实际 recommendations 表 schema:
  baseline_value, current_value, change_pct, verified_at, status, direction
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _get_conn():
    from db._conn import _get_conn
    return _get_conn()


def get_unverified_recommendations(days_ago: int = 7) -> list[dict]:
    """获取超过N天未验证的推荐（baseline_value 有值，current_value 为空）。"""
    conn = _get_conn()
    try:
        cutoff = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute("""
            SELECT id, index_code, index_name, direction, baseline_value, created_at
            FROM recommendations
            WHERE baseline_value IS NOT NULL AND baseline_value > 0
              AND (current_value IS NULL OR current_value = 0)
              AND created_at <= ?
              AND direction IN ('up', 'down')
            ORDER BY created_at ASC
            LIMIT 50
        """, (cutoff,)).fetchall()
        return [
            {"id": r[0], "index_code": r[1], "index_name": r[2],
             "direction": r[3], "baseline_value": r[4], "created_at": r[5]}
            for r in rows
        ]
    finally:
        conn.close()


def get_index_current_price(index_code: str) -> float | None:
    """获取指数当前价格。回退链: akshare → 估值表。"""
    if not index_code:
        return None
    try:
        import akshare as ak
        code = index_code.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")
        if len(code) == 6 and code[:3] in ("399", "000"):
            df = ak.index_zh_a_hist(
                symbol=code, period="daily",
                start_date=(datetime.now() - timedelta(days=5)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                return float(df.iloc[-1]["收盘"])
    except Exception as e:
        logger.warning(f"akshare 获取指数价格失败 {index_code}: {e}")

    # 回退: 从 index_valuations 表获取
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT current_value FROM index_valuations WHERE index_code = ? ORDER BY valuation_date DESC LIMIT 1",
            (index_code,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return float(row[0])
    except Exception:
        pass
    return None


def verify_recommendation(rec: dict) -> dict | None:
    """验证单条推荐。"""
    current_price = get_index_current_price(rec["index_code"])
    if not current_price or current_price <= 0:
        return None

    baseline = rec["baseline_value"]
    if not baseline or baseline <= 0:
        return None

    return_pct = (current_price - baseline) / baseline * 100

    # 判断是否正确
    direction = rec["direction"]
    if direction == "up":
        is_correct = return_pct > 0
    elif direction == "down":
        is_correct = return_pct < 0
    else:
        is_correct = None

    return {
        "rec_id": rec["id"],
        "current_value": round(current_price, 4),
        "change_pct": round(return_pct, 2),
        "is_correct": is_correct,
        "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_verification(rec_id: int, verification: dict):
    """保存验证结果到 recommendations 表。"""
    conn = _get_conn()
    try:
        conn.execute("""
            UPDATE recommendations
            SET current_value = ?, change_pct = ?, verified_at = ?
            WHERE id = ?
        """, (
            verification["current_value"],
            verification["change_pct"],
            verification["verified_at"],
            rec_id,
        ))
        conn.commit()
    finally:
        conn.close()


def verify_all_pending(days_ago: int = 7) -> dict:
    """验证所有超过N天的未验证推荐。"""
    recs = get_unverified_recommendations(days_ago)
    verified_count = 0
    correct_count = 0
    wrong_count = 0

    for rec in recs:
        verification = verify_recommendation(rec)
        if verification:
            save_verification(rec["id"], verification)
            verified_count += 1
            if verification["is_correct"] is True:
                correct_count += 1
            elif verification["is_correct"] is False:
                wrong_count += 1

    return {
        "total_checked": len(recs),
        "verified": verified_count,
        "correct": correct_count,
        "wrong": wrong_count,
        "accuracy": round(correct_count / verified_count * 100, 1) if verified_count > 0 else 0,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_recommendation_stats(days: int = 30) -> dict:
    """获取推荐准确率统计。"""
    conn = _get_conn()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        total = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]

        verified = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE verified_at IS NOT NULL AND created_at >= ?", (cutoff,)
        ).fetchone()[0]

        correct = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE verified_at IS NOT NULL AND change_pct > 0 AND direction = 'up' AND created_at >= ?",
            (cutoff,)
        ).fetchone()[0] + conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE verified_at IS NOT NULL AND change_pct < 0 AND direction = 'down' AND created_at >= ?",
            (cutoff,)
        ).fetchone()[0]

        wrong = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE verified_at IS NOT NULL AND change_pct < 0 AND direction = 'up' AND created_at >= ?",
            (cutoff,)
        ).fetchone()[0] + conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE verified_at IS NOT NULL AND change_pct > 0 AND direction = 'down' AND created_at >= ?",
            (cutoff,)
        ).fetchone()[0]

        avg_return = conn.execute(
            "SELECT AVG(change_pct) FROM recommendations WHERE verified_at IS NOT NULL AND created_at >= ?", (cutoff,)
        ).fetchone()[0]

        best = conn.execute(
            "SELECT index_name, change_pct FROM recommendations WHERE verified_at IS NOT NULL AND created_at >= ? ORDER BY change_pct DESC LIMIT 1",
            (cutoff,)
        ).fetchone()

        worst = conn.execute(
            "SELECT index_name, change_pct FROM recommendations WHERE verified_at IS NOT NULL AND created_at >= ? ORDER BY change_pct ASC LIMIT 1",
            (cutoff,)
        ).fetchone()

        return {
            "period_days": days,
            "total_recommendations": total,
            "verified": verified,
            "accuracy": round(correct / verified * 100, 1) if verified > 0 else 0,
            "correct_count": correct,
            "wrong_count": wrong,
            "avg_return_pct": round(avg_return, 2) if avg_return else 0,
            "best_rec": {"name": best[0], "return_pct": best[1]} if best else None,
            "worst_rec": {"name": worst[0], "return_pct": worst[1]} if worst else None,
        }
    finally:
        conn.close()
