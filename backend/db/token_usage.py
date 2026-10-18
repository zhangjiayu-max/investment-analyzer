"""Token 用量查询 + 性能监控。"""

from datetime import date as date_type

from db._conn import _get_conn


def _window_modifier(days: int) -> str:
    """近 N 个本地日历日，N=1 从今日零点起，与本地记账时间一致。"""
    return f"-{max(1, int(days)) - 1} days"


# ── Token 用量查询 ──────────────────────────────────────


def list_token_usage(days: int = 7, limit: int = 50, offset: int = 0, caller: str = None, model: str = None) -> list[dict]:
    """最近 LLM 调用记录明细（支持分页 + 筛选）。"""
    conn = _get_conn()
    query = """
        SELECT id, model, caller, prompt_tokens, completion_tokens, total_tokens, created_at, trace_id
        FROM token_usage
        WHERE created_at >= datetime('now', 'localtime', 'start of day', ?) AND COALESCE(caller, '') != 'embedding_index'
    """
    params = [_window_modifier(days)]
    if caller:
        query += " AND caller = ?"
        params.append(caller)
    if model:
        query += " AND model = ?"
        params.append(model)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_token_usage(days: int = 7, caller: str = None, model: str = None) -> int:
    """统计记录总数（用于分页 + 筛选）。"""
    conn = _get_conn()
    query = """
        SELECT COUNT(*) as cnt
        FROM token_usage
        WHERE created_at >= datetime('now', 'localtime', 'start of day', ?) AND COALESCE(caller, '') != 'embedding_index'
    """
    params = [_window_modifier(days)]
    if caller:
        query += " AND caller = ?"
        params.append(caller)
    if model:
        query += " AND model = ?"
        params.append(model)
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row[0] if row else 0


def get_today_token_total() -> int:
    """返回今日 token 总用量，用于预算检查。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(total_tokens), 0) as total
        FROM token_usage
        WHERE date(created_at) = date('now', 'localtime') AND COALESCE(caller, '') != 'embedding_index'
    """).fetchone()
    conn.close()
    return row[0] if row else 0


def get_token_usage_summary(days: int = 30) -> dict:
    """汇总统计。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_calls,
            COALESCE(SUM(prompt_tokens), 0) as total_prompt,
            COALESCE(SUM(completion_tokens), 0) as total_completion,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            CASE WHEN COUNT(*) > 0 THEN 1.0 * COALESCE(SUM(total_tokens), 0) / COUNT(*) ELSE 0 END as avg_per_call
        FROM token_usage
        WHERE created_at >= datetime('now', 'localtime', 'start of day', ?) AND COALESCE(caller, '') != 'embedding_index'
    """, (_window_modifier(days),)).fetchone()
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
        WHERE date(created_at) = date('now', 'localtime') AND COALESCE(caller, '') != 'embedding_index'
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
        WHERE created_at >= datetime('now', 'localtime', 'start of day', ?) AND COALESCE(caller, '') != 'embedding_index'
        GROUP BY caller
        ORDER BY total_tokens DESC
    """, (_window_modifier(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_token_usage_daily(days: int = 30) -> list[dict]:
    """按天统计 token 用量趋势。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            date(created_at) as day,
            COUNT(*) as calls,
            COALESCE(SUM(total_tokens), 0) as tokens,
            COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) as completion_tokens
        FROM token_usage
        WHERE created_at >= datetime('now', 'localtime', 'start of day', ?) AND COALESCE(caller, '') != 'embedding_index'
        GROUP BY date(created_at)
        ORDER BY day ASC
    """, (_window_modifier(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 性能监控查询 ──────────────────────────────────────


# 历史执行入口存在三种成功终态；运行中、排队中不能进入成功率分母。
_SUCCESS = "COALESCE(status,'success') IN ('success','completed','done')"
_FAILURE = "status IN ('error','failed','timeout','cancelled','canceled','aborted')"
_ACTIVE = "status IN ('running','pending','queued','streaming','started')"
_PERFORMANCE_COLUMNS = f"""
    COALESCE(AVG(CASE WHEN {_SUCCESS} OR {_FAILURE} THEN duration_ms END),0) as avg_duration_ms,
    COALESCE(MAX(CASE WHEN {_SUCCESS} OR {_FAILURE} THEN duration_ms END),0) as max_duration_ms,
    COUNT(CASE WHEN ({_SUCCESS} OR {_FAILURE}) AND duration_ms > 30000 THEN 1 END) as slow_calls,
    COUNT(CASE WHEN {_SUCCESS} THEN 1 END) as success_count,
    COUNT(CASE WHEN {_FAILURE} THEN 1 END) as error_count,
    COUNT(CASE WHEN {_ACTIVE} THEN 1 END) as active_count
"""


def _performance_result(row) -> dict:
    result = dict(row)
    terminal = result['success_count'] + result['error_count']
    result['terminal_count'] = terminal
    result['success_rate'] = round(result['success_count'] / terminal * 100, 1) if terminal else 0.0
    return result


def get_performance_stats(days: int = 7) -> dict:
    """全部运行量与终态性能，成功率只基于已终结任务。"""
    conn = _get_conn()
    try:
        row = conn.execute(f"""
            SELECT COUNT(*) as total_runs, COUNT(DISTINCT agent_key) as unique_agents,
                   {_PERFORMANCE_COLUMNS}
            FROM agent_runs
            WHERE created_at >= datetime('now','localtime','start of day',?)
        """, (_window_modifier(days),)).fetchone()
        return _performance_result(row)
    finally:
        conn.close()


def get_performance_by_agent(days: int = 7) -> list[dict]:
    """与概览使用同一终态定义和本地日历窗口。"""
    conn = _get_conn()
    try:
        rows = conn.execute(f"""
            SELECT COALESCE(NULLIF(agent_key,''),'unknown') as agent_key,
                   COALESCE(NULLIF(agent_name,''),'未知') as agent_name,
                   COUNT(*) as runs, {_PERFORMANCE_COLUMNS}
            FROM agent_runs
            WHERE created_at >= datetime('now','localtime','start of day',?)
            GROUP BY agent_key ORDER BY runs DESC
        """, (_window_modifier(days),)).fetchall()
        return [_performance_result(row) for row in rows]
    finally:
        conn.close()


# ── 数据库索引优化 ──────────────────────────────────────


def ensure_indexes():
    """创建必要的数据库索引（幂等操作）。"""
    conn = _get_conn()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_caller ON token_usage(caller)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_trace ON token_usage(trace_id)")
    conn.commit()
    conn.close()


# ── 费用估算 + 高级查询 ──────────────────────────────────────

def _model_cost(model, prompt, completion) -> float:
    from infra.cost_tracker import _get_price
    price = _get_price(model)
    return ((prompt or 0) * price['input'] + (completion or 0) * price['output']) / 1_000_000


def get_cost_estimate(days: int = 7) -> dict:
    """所选窗口与今日/近7日/本月分别聚合，不依赖明细分页。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT model, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion
            FROM token_usage
            WHERE created_at >= datetime('now','localtime','start of day',?)
              AND COALESCE(caller,'') != 'embedding_index'
            GROUP BY model
        """, (_window_modifier(days),)).fetchall()
        periods = conn.execute("""
            SELECT model,
                SUM(CASE WHEN created_at >= date('now','localtime') THEN prompt_tokens ELSE 0 END) as today_prompt,
                SUM(CASE WHEN created_at >= date('now','localtime') THEN completion_tokens ELSE 0 END) as today_completion,
                SUM(CASE WHEN created_at >= date('now','localtime','-6 days') THEN prompt_tokens ELSE 0 END) as week_prompt,
                SUM(CASE WHEN created_at >= date('now','localtime','-6 days') THEN completion_tokens ELSE 0 END) as week_completion,
                SUM(CASE WHEN created_at >= date('now','localtime','start of month') THEN prompt_tokens ELSE 0 END) as month_prompt,
                SUM(CASE WHEN created_at >= date('now','localtime','start of month') THEN completion_tokens ELSE 0 END) as month_completion
            FROM token_usage
            WHERE created_at >= MIN(date('now','localtime','-6 days'), date('now','localtime','start of month'))
              AND COALESCE(caller,'') != 'embedding_index'
            GROUP BY model
        """).fetchall()
        by_model = [{"model": row['model'], "prompt_tokens": row['prompt'] or 0,
                     "completion_tokens": row['completion'] or 0,
                     "cost": round(_model_cost(row['model'], row['prompt'], row['completion']), 6)}
                    for row in rows]
        result = {'total_cost': round(sum(_model_cost(r['model'], r['prompt'], r['completion']) for r in rows), 6),
                  'by_model': by_model, 'days': days, 'currency': 'CNY'}
        for period in ('today', 'week', 'month'):
            result[period + '_cost'] = round(sum(_model_cost(r['model'], r[period + '_prompt'], r[period + '_completion'])
                                                for r in periods), 6)
        return result
    finally:
        conn.close()


def get_token_usage_hourly(date: str = None) -> list[dict]:
    """日期校验及参数绑定，禁止将用户日期拼入 SQL。"""
    target = date_type.fromisoformat(str(date)).isoformat() if date is not None else None
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT strftime('%H', created_at) as hour, COUNT(*) as calls, SUM(total_tokens) as tokens
            FROM token_usage
            WHERE date(created_at) = COALESCE(?, date('now','localtime'))
              AND COALESCE(caller,'') != 'embedding_index'
            GROUP BY hour ORDER BY hour ASC
        """, (target,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_trace_tokens(trace_id: str) -> dict:
    """按精确 trace 聚合审计链；尚未产生过记录的可选表返回空数组。"""
    fields = {
        'token_usage': ['id','model','caller','prompt_tokens','completion_tokens','total_tokens','created_at'],
        'tool_audit_logs': ['id','tool_name','arguments','result_preview','success','error_category','duration_ms','created_at'],
        'agent_runs': ['id','conversation_id','message_id','agent_key','agent_name','query','result','tool_calls','duration_ms','status','created_at'],
        'rag_logs': ['id','conversation_id','message_id','query','keywords','content_types','results_count','fts_count','chroma_count','freshness_filtered','result_sources','result_times','created_at'],
    }
    result = {'trace_id': trace_id, **{table: [] for table in fields}}
    if not trace_id:
        return result
    conn = _get_conn()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, wanted in fields.items():
            if table not in tables:
                continue
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            if 'trace_id' not in columns:
                continue
            projection = ','.join(name if name in columns else f'NULL AS {name}' for name in wanted)
            rows = conn.execute(f'SELECT {projection} FROM {table} WHERE trace_id=? ORDER BY id ASC', (trace_id,)).fetchall()
            result[table] = [dict(row) for row in rows]
        return result
    finally:
        conn.close()


def get_token_usage_by_model(days: int = 7) -> list[dict]:
    """按模型分组统计。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT model, COUNT(*) as calls,
               SUM(prompt_tokens) as prompt_tokens,
               SUM(completion_tokens) as completion_tokens,
               SUM(total_tokens) as total_tokens
        FROM token_usage
        WHERE created_at >= datetime('now', 'localtime', 'start of day', ?) AND COALESCE(caller, '') != 'embedding_index'
        GROUP BY model ORDER BY total_tokens DESC
    """, (_window_modifier(days),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
