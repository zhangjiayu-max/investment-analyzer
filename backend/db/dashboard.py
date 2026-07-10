"""推荐验证系统 + LLM 反馈 + 用户画像。"""

import json
from datetime import datetime, timedelta

from db._conn import _get_conn


# ── 推荐验证系统 ──────────────────────────────────────


def save_recommendations(recommendations: list[dict], analysis_id: str = None,
                         baselines: list[dict] = None, verify_days: int = 5) -> list[int]:
    """批量保存推荐记录。baselines 可选，每项 {"price": float, "date": str}。

    verify_days: 验证窗口（交易日），默认 T+5。
    rec 中可选 target_fund_code/target_fund_name/suggested_amount 字段（P2 执行落地）。
    """
    # 简单按自然日估算交易日（1 周 ≈ 5 交易日 ≈ 7 自然日）
    verify_after = (datetime.now() + timedelta(days=int(verify_days * 1.4))).strftime("%Y-%m-%d")

    ids = []
    conn = _get_conn()
    for i, rec in enumerate(recommendations):
        bl = baselines[i] if baselines and i < len(baselines) else None
        bl_price = bl.get("price") if bl else None
        bl_date = bl.get("date") if bl else None
        cur = conn.execute(
            "INSERT INTO recommendations "
            "(analysis_id, index_name, index_code, direction, reason, confidence, "
            "baseline_value, baseline_date, verify_after_date, verify_window_days, "
            "target_fund_code, target_fund_name, suggested_amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                analysis_id,
                rec.get("index_name", ""),
                rec.get("index_code", ""),
                rec.get("direction", ""),
                rec.get("reason", ""),
                rec.get("confidence", ""),
                bl_price,
                bl_date,
                verify_after,
                verify_days,
                rec.get("target_fund_code") or None,
                rec.get("target_fund_name") or None,
                rec.get("suggested_amount"),
            )
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


def list_recommendations(limit: int = 50, status: str = None) -> list[dict]:
    """列出推荐记录。"""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM recommendations ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def verify_recommendation(rec_id: int, current_value: float, current_date: str) -> dict:
    """验证单条推荐。"""
    conn = _get_conn()
    rec = conn.execute("SELECT * FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
    if not rec:
        conn.close()
        return {"ok": False, "error": "not found"}
    rec = dict(rec)

    baseline = rec["baseline_value"]
    if not baseline:
        conn.execute(
            "UPDATE recommendations SET baseline_value = ?, baseline_date = ?, current_value = ?, current_date = ? WHERE id = ?",
            (current_value, current_date, current_value, current_date, rec_id)
        )
        conn.commit()
        conn.close()
        return {"ok": True, "status": "pending", "message": "基线已记录，等待后续验证"}

    change_pct = (current_value - baseline) / baseline * 100 if baseline else 0
    direction = rec["direction"]

    if direction == "up":
        correct = change_pct > 0
    elif direction == "down":
        correct = change_pct < 0
    else:
        correct = None

    status = "correct" if correct is True else ("wrong" if correct is False else "pending")
    conn.execute(
        "UPDATE recommendations SET current_value = ?, current_date = ?, change_pct = ?, "
        "status = ?, verified_at = datetime('now','localtime') WHERE id = ?",
        (current_value, current_date, round(change_pct, 2), status, rec_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "status": status, "change_pct": round(change_pct, 2)}


def auto_verify_pending_recommendations(price_map: dict, verify_date: str,
                                         benchmark_change_pct: float = None,
                                         min_change_threshold: float = 2.0) -> list[dict]:
    """批量验证 pending 推荐。

    - 仅验证到达 verify_after_date 的推荐
    - up/down: 涨跌幅超过阈值才算有效，否则标记为 "flat"
    - watch: 用 benchmark_change_pct 对比，跑赢基准=正确
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE status = 'pending' AND baseline_value IS NOT NULL "
        "AND (verify_after_date IS NULL OR verify_after_date <= ?)",
        (verify_date,),
    ).fetchall()
    results = []
    for row in rows:
        rec = dict(row)
        code = rec["index_code"]
        current_price = price_map.get(code)
        if current_price is None:
            continue
        baseline = rec["baseline_value"]
        change_pct = (current_price - baseline) / baseline * 100 if baseline else 0
        direction = rec["direction"]
        window_days = rec.get("verify_window_days") or 5

        if direction == "up":
            if abs(change_pct) < min_change_threshold:
                status = "flat"  # 涨跌幅太小，无意义
            else:
                status = "correct" if change_pct > 0 else "wrong"
        elif direction == "down":
            if abs(change_pct) < min_change_threshold:
                status = "flat"
            else:
                status = "correct" if change_pct < 0 else "wrong"
        elif direction == "watch":
            # watch: 对比基准，跑赢基准=正确
            if benchmark_change_pct is not None:
                outperform = change_pct - benchmark_change_pct
                if abs(outperform) < min_change_threshold:
                    status = "flat"
                else:
                    status = "correct" if outperform > 0 else "wrong"
                conn.execute(
                    "UPDATE recommendations SET current_value = ?, current_date = ?, change_pct = ?, "
                    "benchmark_change_pct = ?, status = ?, verified_at = datetime('now','localtime'), "
                    "verify_window_days = ? WHERE id = ?",
                    (current_price, verify_date, round(change_pct, 2),
                     round(benchmark_change_pct, 2), status, window_days, rec["id"])
                )
                results.append({
                    "id": rec["id"], "index_name": rec["index_name"], "status": status,
                    "change_pct": round(change_pct, 2),
                    "benchmark_change_pct": round(benchmark_change_pct, 2),
                    "outperform": round(outperform, 2),
                })
                continue
            else:
                continue  # 无基准数据，跳过
        else:
            continue

        conn.execute(
            "UPDATE recommendations SET current_value = ?, current_date = ?, change_pct = ?, "
            "status = ?, verified_at = datetime('now','localtime'), verify_window_days = ? WHERE id = ?",
            (current_price, verify_date, round(change_pct, 2), status, window_days, rec["id"])
        )
        results.append({
            "id": rec["id"], "index_name": rec["index_name"], "status": status,
            "change_pct": round(change_pct, 2), "verify_window_days": window_days,
        })
    conn.commit()
    conn.close()
    return results


# ── P0-A 决策闭环：用户采纳标记 ────────────────────────────────────


def adopt_recommendation(rec_id: int, adopted: int) -> dict:
    """标记用户是否采纳某条建议。

    Args:
        rec_id: recommendation id
        adopted: 1=已采纳, -1=未采纳, 0=取消标记（回到待定）

    Returns:
        {"ok": bool, "id": int, "adopted": int}
    """
    if adopted not in (-1, 0, 1):
        return {"ok": False, "error": "invalid adopted value"}
    conn = _get_conn()
    rec = conn.execute("SELECT id FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
    if not rec:
        conn.close()
        return {"ok": False, "error": "not found"}
    from datetime import datetime
    adopted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if adopted != 0 else None
    conn.execute(
        "UPDATE recommendations SET adopted = ?, adopted_at = ? WHERE id = ?",
        (adopted, adopted_at, rec_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": rec_id, "adopted": adopted}


def list_pending_verification_recommendations(verify_date: str) -> list[dict]:
    """列出到达验证窗口且尚未验证的建议（用于定时任务）。

    验证窗口锚点：用户采纳时间(adopted_at) + verify_window_days 天。
    仅验证已采纳(adopted=1)的建议。
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM recommendations "
        "WHERE status = 'pending' AND baseline_value IS NOT NULL "
        "AND adopted = 1 AND adopted_at IS NOT NULL "
        "AND date(adopted_at, '+' || COALESCE(verify_window_days, 5) || ' days') <= ? "
        "ORDER BY adopted_at ASC",
        (verify_date,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 推荐反馈 / 进化系统 ────────────────────────────────────


def save_recommendation_feedback(recommendation_id: int, rating: str = "neutral",
                                  tags: str = "", comment: str = "") -> int:
    """保存推荐反馈（点赞/点踩）。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO recommendation_feedback (recommendation_id, rating, tags, comment) VALUES (?, ?, ?, ?)",
        (recommendation_id, rating, tags, comment)
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_recommendation_feedback(recommendation_id: int = None, limit: int = 50) -> list[dict]:
    """列出推荐反馈。"""
    conn = _get_conn()
    if recommendation_id:
        rows = conn.execute(
            "SELECT * FROM recommendation_feedback WHERE recommendation_id = ? ORDER BY id DESC LIMIT ?",
            (recommendation_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM recommendation_feedback ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recommendation_feedback_stats() -> dict:
    """获取推荐反馈统计。"""
    conn = _get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_feedback,
            COALESCE(SUM(CASE WHEN rating='helpful' THEN 1 ELSE 0 END), 0) as helpful,
            COALESCE(SUM(CASE WHEN rating='unhelpful' THEN 1 ELSE 0 END), 0) as unhelpful,
            COALESCE(SUM(CASE WHEN rating='neutral' THEN 1 ELSE 0 END), 0) as neutral
        FROM recommendation_feedback
    """).fetchone()
    conn.close()
    return dict(stats)


def save_llm_feedback(caller: str, input_summary: str = "", output_summary: str = "",
                      rating: str = "neutral", tags: str = "", comment: str = "",
                      reason_tag: str = "", score_data_accuracy: int = None,
                      score_logic: int = None, score_actionability: int = None,
                      target_type: str = "", target_id: int = None) -> int:
    """保存 LLM 输出反馈（进化系统核心）。"""
    # 计算综合评分
    scores = [s for s in [score_data_accuracy, score_logic, score_actionability] if s is not None]
    overall_score = round(sum(scores) / len(scores), 1) if scores else None

    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO llm_feedback
           (caller, input_summary, output_summary, rating, tags, comment, reason_tag,
            score_data_accuracy, score_logic, score_actionability, overall_score, target_type, target_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (caller, input_summary, output_summary, rating, tags, comment, reason_tag,
         score_data_accuracy, score_logic, score_actionability, overall_score, target_type, target_id)
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def create_chat_feedback(message_id: int, rating: str = "neutral", comment: str = "") -> int:
    """保存对话消息反馈（点赞/点踩）。"""
    return save_llm_feedback(
        caller="chat",
        input_summary=f"message_id={message_id}",
        output_summary="",
        rating=rating,
        comment=comment,
    )


def list_llm_feedback(caller: str = None, rating: str = None, limit: int = 50) -> list[dict]:
    """列出 LLM 反馈。"""
    conn = _get_conn()
    conditions = []
    params = []
    if caller:
        conditions.append("caller = ?")
        params.append(caller)
    if rating:
        conditions.append("rating = ?")
        params.append(rating)
    where = " AND ".join(conditions) if conditions else "1=1"
    rows = conn.execute(f"""
        SELECT * FROM llm_feedback WHERE {where} ORDER BY id DESC LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_quality_summary(days: int = 30) -> dict:
    """获取质量评分概览。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_feedback,
            COUNT(CASE WHEN overall_score IS NOT NULL THEN 1 END) as scored_count,
            COALESCE(AVG(overall_score), 0) as avg_overall,
            COALESCE(AVG(score_data_accuracy), 0) as avg_data_accuracy,
            COALESCE(AVG(score_logic), 0) as avg_logic,
            COALESCE(AVG(score_actionability), 0) as avg_actionability,
            COUNT(CASE WHEN overall_score IS NOT NULL AND overall_score < 3 THEN 1 END) as low_quality_count
        FROM llm_feedback
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_quality_trend(days: int = 30) -> list[dict]:
    """获取按天的质量评分趋势。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            date(created_at) as day,
            COUNT(*) as feedback_count,
            COALESCE(AVG(overall_score), 0) as avg_score,
            COALESCE(AVG(score_data_accuracy), 0) as avg_accuracy,
            COALESCE(AVG(score_logic), 0) as avg_logic,
            COALESCE(AVG(score_actionability), 0) as avg_actionability
        FROM llm_feedback
        WHERE created_at >= datetime('now', ?) AND overall_score IS NOT NULL
        GROUP BY date(created_at)
        ORDER BY day ASC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_low_quality_items(limit: int = 20) -> list[dict]:
    """获取低分产出列表（bad cases）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM llm_feedback
        WHERE overall_score IS NOT NULL AND overall_score < 3
        ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 用户画像 ──────────────────────────────────

def get_user_profile(user_id: str = "default") -> dict | None:
    """获取用户画像。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_profile(user_id: str = "default", **fields) -> bool:
    """更新用户画像字段。"""
    if not fields:
        return False
    allowed = {
        "preferences_json", "feedback_summary", "positive_patterns", "negative_patterns",
        "risk_tolerance", "investment_horizon", "capital_scale", "investment_experience",
        "loss_tolerance", "focus_assets", "kyc_completed", "kyc_completed_at",
        "kyc_version", "kyc_source", "monthly_income", "monthly_expense",
        "monthly_surplus", "emergency_fund_months", "target_equity_ratio",
        "max_single_position_pct", "primary_goal", "fund_usage", "liquidity_needs",
        "liabilities_summary", "behavior_biases",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    if isinstance(fields.get("behavior_biases"), list):
        fields["behavior_biases"] = json.dumps(fields["behavior_biases"], ensure_ascii=False)
    conn = _get_conn()
    # 确保记录存在
    conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    conn.execute(f"UPDATE user_profiles SET {set_clause}, updated_at = datetime('now','localtime') WHERE user_id = ?", values)
    conn.commit()
    conn.close()
    return True


def increment_feedback_count(user_id: str = "default") -> int:
    """增加反馈计数，返回更新后的总数。"""
    conn = _get_conn()
    conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
    conn.execute("UPDATE user_profiles SET total_feedback_count = total_feedback_count + 1, updated_at = datetime('now','localtime') WHERE user_id = ?", (user_id,))
    row = conn.execute("SELECT total_feedback_count FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else 0


# ── P1 投资目标 CRUD ──────────────────────────────────────

def list_investment_goals(user_id: str = "default") -> list[dict]:
    """列出用户的投资目标（按优先级倒序）。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM investment_goals WHERE user_id = ? ORDER BY priority DESC, created_at ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_investment_goal(user_id: str = "default", **fields) -> int:
    """新增投资目标。必填 goal_type，可选 target_amount/target_date/monthly_contribution/priority。"""
    allowed = {"goal_type", "target_amount", "target_date", "monthly_contribution", "priority"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields.get("goal_type"):
        raise ValueError("goal_type is required")
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO investment_goals (user_id, goal_type, target_amount, target_date, "
        "monthly_contribution, priority) VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id,
            fields["goal_type"],
            fields.get("target_amount"),
            fields.get("target_date"),
            fields.get("monthly_contribution"),
            fields.get("priority", 0),
        ),
    )
    conn.commit()
    goal_id = cur.lastrowid
    conn.close()
    return goal_id


def update_investment_goal(goal_id: int, **fields) -> bool:
    """更新投资目标字段。"""
    allowed = {"goal_type", "target_amount", "target_date", "monthly_contribution",
               "current_progress", "priority"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [goal_id]
    conn.execute(
        f"UPDATE investment_goals SET {set_clause}, updated_at = datetime('now','localtime') WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()
    return True


def delete_investment_goal(goal_id: int) -> bool:
    """删除投资目标。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM investment_goals WHERE id = ?", (goal_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
