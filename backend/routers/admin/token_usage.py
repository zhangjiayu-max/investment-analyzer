"""Token 用量 + 运行状态 + 性能监控路由 — /api/token-usage/*, /api/running-agents, /api/performance/*"""

import time

from fastapi import APIRouter

from db import (
    list_token_usage, count_token_usage, get_token_usage_summary,
    get_token_budget_info, get_token_usage_by_caller, get_token_usage_daily,
    get_performance_stats, get_performance_by_agent,
)
from db._conn import _get_conn
from infra.state import running_agents

router = APIRouter(tags=["token-usage"])


@router.get("/api/token-usage")
async def get_token_usage_api(days: int = 7):
    """获取 Token 用量统计。"""
    conn = _get_conn()
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
    ).fetchone()
    if not table_exists:
        conn.close()
        return {"total": 0, "daily": [], "by_model": []}

    # 排除 embedding_index（本地 embedding 模型，非 LLM 调用），仅统计 LLM token
    row = conn.execute("""
        SELECT COUNT(*) as calls, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion, SUM(total_tokens) as total
        FROM token_usage WHERE created_at >= datetime('now', ?) AND caller != 'embedding_index'
    """, (f"-{days} days",)).fetchone()

    daily = conn.execute("""
        SELECT date(created_at) as day, COUNT(*) as calls, SUM(total_tokens) as tokens
        FROM token_usage WHERE created_at >= datetime('now', ?) AND caller != 'embedding_index'
        GROUP BY date(created_at) ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()

    by_model = conn.execute("""
        SELECT model, COUNT(*) as calls, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion, SUM(total_tokens) as total
        FROM token_usage WHERE created_at >= datetime('now', ?) AND caller != 'embedding_index'
        GROUP BY model ORDER BY total DESC
    """, (f"-{days} days",)).fetchall()

    conn.close()
    return {
        "total": {
            "calls": row[0] or 0,
            "prompt_tokens": row[1] or 0,
            "completion_tokens": row[2] or 0,
            "total_tokens": row[3] or 0,
        },
        "daily": [dict(r) for r in daily],
        "by_model": [dict(r) for r in by_model],
    }


@router.get("/api/token-usage/recent")
async def get_token_usage_recent(page: int = 1, page_size: int = 20, days: int = 7, caller: str = None, model: str = None):
    """获取最近 LLM 调用记录（分页 + 筛选）。"""
    offset = (page - 1) * page_size
    records = list_token_usage(days=days, limit=page_size, offset=offset, caller=caller, model=model)
    total = count_token_usage(days=days, caller=caller, model=model)
    return {"records": records, "total": total, "page": page, "page_size": page_size}


@router.get("/api/token-usage/summary")
async def get_token_usage_summary_api(days: int = 30):
    """获取 Token 用量汇总。"""
    return get_token_usage_summary(days=days)


@router.get("/api/token-usage/budget")
async def get_token_budget_api():
    """获取今日 Token 预算使用情况。"""
    return get_token_budget_info()


@router.get("/api/token-usage/by-caller")
async def get_token_usage_by_caller_api(days: int = 7):
    """按 caller 分组统计。"""
    return {"items": get_token_usage_by_caller(days=days)}


@router.get("/api/token-usage/daily")
async def get_token_usage_daily_api(days: int = 30):
    """按天获取 Token 用量趋势。"""
    return {"items": get_token_usage_daily(days=days)}


@router.get("/api/running-agents")
async def get_running_agents():
    """获取当前正在运行的 Agent 列表。"""
    now = time.time()
    agents = []
    for uid, info in running_agents.items():
        agents.append({
            "id": uid,
            "agent": info.get("agent", ""),
            "task": info.get("task", ""),
            "started_at": info.get("started_at", 0),
            "elapsed_s": round(now - info.get("started_at", now), 1),
        })
    return {"agents": agents}


@router.get("/api/performance/stats")
async def get_performance_stats_api(days: int = 7):
    """获取 Agent 调用性能统计。"""
    return get_performance_stats(days=days)


@router.get("/api/performance/by-agent")
async def get_performance_by_agent_api(days: int = 7):
    """按 Agent 分组统计性能。"""
    return {"items": get_performance_by_agent(days=days)}


@router.post("/api/token-usage/clear")
async def clear_token_usage():
    """清空所有 token 用量数据。"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM token_usage")
        conn.commit()
        return {"ok": True, "message": "Token 用量数据已清空"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/api/token-usage/cost")
async def get_token_cost_api(days: int = 7):
    from db import get_cost_estimate
    return get_cost_estimate(days=days)


@router.get("/api/token-usage/hourly")
async def get_token_usage_hourly_api(date: str = None):
    from db import get_token_usage_hourly
    return {"items": get_token_usage_hourly(date)}


@router.get("/api/token-usage/by-model")
async def get_token_usage_by_model_api(days: int = 7):
    from db import get_token_usage_by_model
    return {"items": get_token_usage_by_model(days=days)}


@router.get("/api/token-usage/trace/{trace_id}")
async def get_trace_token_api(trace_id: str):
    from db import get_trace_tokens
    return {"items": get_trace_tokens(trace_id)}
