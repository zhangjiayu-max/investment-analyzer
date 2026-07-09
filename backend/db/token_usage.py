"""Token 用量查询 + 性能监控。"""

from db._conn import _get_conn


# ── Token 用量查询 ──────────────────────────────────────


def list_token_usage(days: int = 7, limit: int = 50, offset: int = 0, caller: str = None, model: str = None) -> list[dict]:
    """最近 LLM 调用记录明细（支持分页 + 筛选）。"""
    conn = _get_conn()
    query = """
        SELECT id, model, caller, prompt_tokens, completion_tokens, total_tokens, created_at, trace_id
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
    """
    params = [f"-{days} days"]
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
        WHERE created_at >= datetime('now', ?)
    """
    params = [f"-{days} days"]
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
        WHERE date(created_at) = date('now', 'localtime') AND caller != 'embedding_index'
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
            CASE WHEN COUNT(*) > 0 THEN COALESCE(SUM(total_tokens), 0) / COUNT(*) ELSE 0 END as avg_per_call
        FROM token_usage
        WHERE created_at >= datetime('now', ?) AND caller != 'embedding_index'
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
        WHERE date(created_at) = date('now', 'localtime') AND caller != 'embedding_index'
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
    """按天统计 token 用量趋势。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            date(created_at) as day,
            COUNT(*) as calls,
            COALESCE(SUM(total_tokens), 0) as tokens
        FROM token_usage
        WHERE created_at >= datetime('now', ?) AND caller != 'embedding_index'
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

# A3 修复：统一定价表，以 infra/cost_tracker.py:MODEL_PRICES 为权威源
# 避免两套价格表不一致导致 /token-usage/cost 与 /cost-governance/dashboard 金额冲突
try:
    from infra.cost_tracker import MODEL_PRICES as _RAW_PRICES
    # 适配字段名：cost_tracker 用 input/output，本模块用 prompt/completion
    MODEL_PRICING = {
        k: {"prompt": v["input"], "completion": v["output"]}
        for k, v in _RAW_PRICES.items()
    }
except ImportError:
    # 兜底（与 infra/cost_tracker.py 保持一致）
    MODEL_PRICING = {
        "deepseek-chat": {"prompt": 0.5, "completion": 2.0},
        "deepseek-reasoner": {"prompt": 1.0, "completion": 4.0},
        "deepseek-v4-flash": {"prompt": 0.5, "completion": 2.0},
        "deepseek-v4-pro": {"prompt": 1.0, "completion": 4.0},
        "mimo": {"prompt": 0.3, "completion": 1.2},
        "mimo-v2.5-pro": {"prompt": 0.3, "completion": 1.2},
        "mimo-v2.5": {"prompt": 0.3, "completion": 1.2},
        "ollama": {"prompt": 0.0, "completion": 0.0},
        "qwen3-vl": {"prompt": 0.0, "completion": 0.0},
    }


def get_cost_estimate(days: int = 7) -> dict:
    """估算近 N 天的 API 费用。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT model,
               SUM(prompt_tokens) as prompt,
               SUM(completion_tokens) as completion
        FROM token_usage
        WHERE created_at >= datetime('now', ?) AND caller != 'embedding_index'
        GROUP BY model
    """, (f"-{days} days",)).fetchall()
    conn.close()
    total_cost = 0.0
    by_model = []
    for r in rows:
        d = dict(r)
        # A3 修复：模糊匹配模型名，与 cost_tracker 逻辑一致
        model_lower = (d["model"] or "").lower()
        pricing = None
        for key, val in MODEL_PRICING.items():
            if key in model_lower:
                pricing = val
                break
        if not pricing:
            pricing = {"prompt": 0.5, "completion": 2.0}  # 默认兜底，与 cost_tracker 一致
        cost = (d["prompt"] / 1_000_000 * pricing["prompt"]) + (d["completion"] / 1_000_000 * pricing["completion"])
        total_cost += cost
        by_model.append({
            "model": d["model"],
            "prompt_tokens": d["prompt"],
            "completion_tokens": d["completion"],
            "cost": round(cost, 4)
        })
    return {"total_cost": round(total_cost, 2), "by_model": by_model}


def get_token_usage_hourly(date: str = None) -> list[dict]:
    """按小时统计 token 用量。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    if date:
        target = f"date(created_at) = '{date}'"
    else:
        target = "date(created_at) = date('now', 'localtime')"
    rows = conn.execute(f"""
        SELECT
            strftime('%H', created_at) as hour,
            COUNT(*) as calls,
            SUM(total_tokens) as tokens
        FROM token_usage
        WHERE {target} AND caller != 'embedding_index'
        GROUP BY hour ORDER BY hour ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trace_tokens(trace_id: str) -> dict:
    """查询一次链路的完整调用明细：token_usage + tool_audit_logs + agent_runs + rag_logs。

    覆盖对话链路与分散度/相关性等页面分析两类场景。任一表缺失不阻断查询。
    """
    if not trace_id:
        return {"trace_id": "", "token_usage": [], "tool_audit_logs": [], "agent_runs": [], "rag_logs": []}
    conn = _get_conn()

    # 1. token_usage：LLM 调用消耗
    token_rows = conn.execute("""
        SELECT id, model, caller, prompt_tokens, completion_tokens, total_tokens, created_at
        FROM token_usage
        WHERE trace_id = ?
        ORDER BY id ASC
    """, (trace_id,)).fetchall()

    # 2. tool_audit_logs：工具调用审计
    tool_rows = conn.execute("""
        SELECT id, tool_name, arguments, result_preview, success, error_category, duration_ms, created_at
        FROM tool_audit_logs
        WHERE trace_id = ?
        ORDER BY id ASC
    """, (trace_id,)).fetchall()

    # 3. agent_runs：Agent 执行记录
    agent_rows = conn.execute("""
        SELECT id, conversation_id, message_id, agent_key, agent_name, query,
               result, tool_calls, duration_ms, status, created_at
        FROM agent_runs
        WHERE trace_id = ?
        ORDER BY id ASC
    """, (trace_id,)).fetchall()

    # 4. rag_logs：RAG 检索日志
    rag_rows = conn.execute("""
        SELECT id, conversation_id, message_id, query, keywords, content_types,
               results_count, fts_count, chroma_count, freshness_filtered,
               result_sources, result_times, created_at
        FROM rag_logs
        WHERE trace_id = ?
        ORDER BY id ASC
    """, (trace_id,)).fetchall()

    conn.close()
    return {
        "trace_id": trace_id,
        "token_usage": [dict(r) for r in token_rows],
        "tool_audit_logs": [dict(r) for r in tool_rows],
        "agent_runs": [dict(r) for r in agent_rows],
        "rag_logs": [dict(r) for r in rag_rows],
    }


def get_token_usage_by_model(days: int = 7) -> list[dict]:
    """按模型分组统计。排除 embedding_index（非 LLM 调用）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT model, COUNT(*) as calls,
               SUM(prompt_tokens) as prompt_tokens,
               SUM(completion_tokens) as completion_tokens,
               SUM(total_tokens) as total_tokens
        FROM token_usage
        WHERE created_at >= datetime('now', ?) AND caller != 'embedding_index'
        GROUP BY model ORDER BY total_tokens DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
