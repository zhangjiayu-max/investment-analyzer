"""Token 用量查询 + 性能监控。"""

from db._conn import _get_conn


# ── Token 用量查询 ──────────────────────────────────────


def list_token_usage(days: int = 7, limit: int = 50, offset: int = 0) -> list[dict]:
    """最近 LLM 调用记录明细（支持分页）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, model, caller, prompt_tokens, completion_tokens, total_tokens, created_at
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
        ORDER BY id DESC LIMIT ? OFFSET ?
    """, (f"-{days} days", limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_token_usage(days: int = 7) -> int:
    """统计记录总数（用于分页）。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    conn.close()
    return row[0] if row else 0


def get_today_token_total() -> int:
    """返回今日 token 总用量，用于预算检查。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(total_tokens), 0) as total
        FROM token_usage
        WHERE date(created_at) = date('now', 'localtime')
    """).fetchone()
    conn.close()
    return row[0] if row else 0


def get_token_usage_summary(days: int = 30) -> dict:
    """汇总统计。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_calls,
            COALESCE(SUM(prompt_tokens), 0) as total_prompt,
            COALESCE(SUM(completion_tokens), 0) as total_completion,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            CASE WHEN COUNT(*) > 0 THEN COALESCE(SUM(total_tokens), 0) / COUNT(*) ELSE 0 END as avg_per_call
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    conn.close()
    base = dict(row)

    conn = _get_conn()
    today = conn.execute("""
        SELECT
            COUNT(*) as calls,
            COALESCE(SUM(prompt_tokens), 0) as prompt,
            COALESCE(SUM(completion_tokens), 0) as completion,
            COALESCE(SUM(total_tokens), 0) as total
        FROM token_usage
        WHERE date(created_at) = date('now', 'localtime')
    """).fetchone()
    conn.close()

    return {
        "total_calls": base["total_calls"],
        "total_prompt": base["total_prompt"],
        "total_completion": base["total_completion"],
        "total_tokens": base["total_tokens"],
        "avg_per_call": base["avg_per_call"],
        "today": dict(today),
    }


def get_token_budget_info() -> dict:
    """获取今日 token 预算使用情况。"""
    from config import DAILY_TOKEN_LIMIT, TOKEN_WARN_THRESHOLD, TOKEN_BUDGET_BYPASS
    used = get_today_token_total()
    limit = DAILY_TOKEN_LIMIT
    pct = (used / limit * 100) if limit > 0 else 0
    if TOKEN_BUDGET_BYPASS:
        mode = "normal"
    elif pct >= 100:
        mode = "exceeded"
    elif pct >= TOKEN_WARN_THRESHOLD * 100:
        mode = "warning"
    else:
        mode = "normal"
    return {"ok": True, "used": used, "limit": limit, "pct": round(pct, 1), "mode": mode}


def get_token_usage_by_caller(days: int = 7) -> list[dict]:
    """按 caller 分组统计。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            COALESCE(NULLIF(caller, ''), 'unknown') as caller,
            COUNT(*) as calls,
            COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) as completion_tokens,
            COALESCE(SUM(total_tokens), 0) as total_tokens
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
        GROUP BY caller
        ORDER BY total_tokens DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_token_usage_daily(days: int = 30) -> list[dict]:
    """按天统计 token 用量趋势。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            date(created_at) as day,
            COUNT(*) as calls,
            COALESCE(SUM(total_tokens), 0) as tokens
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
        GROUP BY date(created_at)
        ORDER BY day ASC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 性能监控查询 ──────────────────────────────────────


def get_performance_stats(days: int = 7) -> dict:
    """获取 Agent 调用性能统计（平均耗时、最慢调用、成功率等）。"""
    conn = _get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_runs,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(MAX(duration_ms), 0) as max_duration_ms,
            COUNT(CASE WHEN duration_ms > 30000 THEN 1 END) as slow_calls,
            COUNT(DISTINCT agent_key) as unique_agents,
            SUM(CASE WHEN COALESCE(status, 'success') = 'success' THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN COALESCE(status, 'success') != 'success' THEN 1 ELSE 0 END) as error_count
        FROM agent_runs
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    result = dict(stats)
    total = result.get("total_runs", 0)
    result["success_rate"] = round(result["success_count"] / total * 100, 1) if total > 0 else 0.0
    conn.close()
    return result


def get_performance_by_agent(days: int = 7) -> list[dict]:
    """按 Agent 分组统计性能（含成功率）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            COALESCE(NULLIF(agent_key, ''), 'unknown') as agent_key,
            COALESCE(NULLIF(agent_name, ''), '未知') as agent_name,
            COUNT(*) as runs,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(MAX(duration_ms), 0) as max_duration_ms,
            COALESCE(SUM(CASE WHEN duration_ms > 30000 THEN 1 ELSE 0 END), 0) as slow_calls,
            SUM(CASE WHEN COALESCE(status, 'success') = 'success' THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN COALESCE(status, 'success') != 'success' THEN 1 ELSE 0 END) as error_count
        FROM agent_runs
        WHERE created_at >= datetime('now', ?)
        GROUP BY agent_key
        ORDER BY runs DESC
    """, (f"-{days} days",)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        total = d.get("runs", 0)
        d["success_rate"] = round(d["success_count"] / total * 100, 1) if total > 0 else 0.0
        result.append(d)
    conn.close()
    return result
