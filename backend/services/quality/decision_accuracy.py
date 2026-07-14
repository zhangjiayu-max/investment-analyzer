"""决策准确率追踪模块 — 统计 AI 推荐/决策的准确率与专家胜率。

纯聚合统计，零 LLM 调用。

数据源：
  - recommendations 表 (db/dashboard.py)：指数方向预测 (up/down/watch)
  - decisions 表 (db/decisions.py)：理财决策回测
  - agent_runs 表 (db/agents.py)：专家 Agent 执行记录
  - execution_traces 表：质量指标

核心函数：
  - get_accuracy_stats(period_days, group_by)：准确率统计 + 专家胜率
  - auto_verify_all()：自动验证到期推荐 + 回测决策
  - get_accuracy_trend(weeks)：按周准确率趋势
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from db._conn import _get_conn
from db.dashboard import auto_verify_pending_recommendations, list_recommendations

logger = logging.getLogger(__name__)

# ── 状态常量 ──────────────────────────────────────
# recommendations 表 status 取值：pending / correct / wrong / flat
VERIFIED_STATUSES = ("correct", "wrong", "flat")
CORRECT_STATUS = "correct"

# direction → 展示用 action 名称（看多→买入方向，看空→卖出方向，观察→持有）
DIRECTION_ACTION = {
    "up": "buy",
    "down": "sell",
    "watch": "hold",
}


def _today_str() -> str:
    """返回当日日期字符串 YYYY-MM-DD。"""
    return datetime.now().strftime("%Y-%m-%d")


def _safe_div(numerator: float, denominator: float, ndigits: int = 3) -> float:
    """安全除法，分母为 0 时返回 0.0。"""
    if not denominator:
        return 0.0
    return round(numerator / denominator, ndigits)


def _resolve_agent_key(analysis_id: str | None, fallback: str = "unknown") -> str:
    """从 analysis_id 推断 agent_key。

    analysis_id 形如 "hotspots_20260701_123456"，取首个下划线前的前缀作为 agent 标识。
    """
    if not analysis_id:
        return fallback
    prefix = analysis_id.split("_", 1)[0].strip()
    return prefix or fallback


def _fetch_recommendations_with_agent(period_days: int) -> list[dict]:
    """查询 period_days 内的推荐记录，并关联 agent_runs 获取 agent_key。

    关联策略：通过子查询从 agent_runs 取 trace_id = analysis_id 的首条 agent_key；
    未命中时从 analysis_id 前缀推断；仍无则标记 "unknown"。
    """
    cutoff = (datetime.now() - timedelta(days=period_days)).strftime("%Y-%m-%d 00:00:00")
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT r.id, r.analysis_id, r.index_name, r.index_code, r.direction,
                   r.confidence, r.status, r.change_pct, r.created_at, r.verified_at,
                   (SELECT ar.agent_key FROM agent_runs ar
                    WHERE ar.trace_id = r.analysis_id LIMIT 1) AS agent_key,
                   (SELECT ar.agent_name FROM agent_runs ar
                    WHERE ar.trace_id = r.analysis_id LIMIT 1) AS agent_name
            FROM recommendations r
            WHERE r.created_at >= ?
            ORDER BY r.created_at DESC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        item = dict(row)
        # agent_key 回填：优先用 agent_runs 命中的，其次从 analysis_id 前缀推断
        if not item.get("agent_key"):
            item["agent_key"] = _resolve_agent_key(item.get("analysis_id"))
        results.append(item)
    return results


def _group_by(items: list[dict], key_fn, key_field: str) -> list[dict]:
    """按 key_fn 分组统计准确率，返回按总数倒序的列表。"""
    buckets: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "verified": 0, "correct": 0, "wrong": 0, "flat": 0}
    )
    for item in items:
        key = key_fn(item) or "unknown"
        bucket = buckets[key]
        bucket["total"] += 1
        status = item.get("status") or ""
        if status in VERIFIED_STATUSES:
            bucket["verified"] += 1
            if status == CORRECT_STATUS:
                bucket["correct"] += 1
            elif status == "wrong":
                bucket["wrong"] += 1
            elif status == "flat":
                bucket["flat"] += 1

    result = []
    for key, bucket in buckets.items():
        result.append({
            key_field: key,
            "total": bucket["total"],
            "verified": bucket["verified"],
            "correct": bucket["correct"],
            "wrong": bucket["wrong"],
            "flat": bucket["flat"],
            "accuracy": _safe_div(bucket["correct"], bucket["verified"]),
        })
    result.sort(key=lambda x: x["total"], reverse=True)
    return result


def get_accuracy_stats(period_days: int = 90, group_by: str = "agent") -> dict:
    """获取决策准确率统计 + 专家胜率。

    Args:
        period_days: 统计周期（天），默认 90
        group_by: 主分组维度 agent / scenario / action_type

    Returns:
        {
            "overall": {"total", "verified", "correct", "wrong", "flat", "accuracy"},
            "by_agent": [{"agent_key", "total", "verified", "correct", "accuracy"}],
            "by_action": [{"action", "total", "verified", "correct", "accuracy"}],
            "by_scenario": [...]  # 仅 group_by="scenario" 时返回
            "trend": [{"week", "total", "verified", "correct", "accuracy"}],
        }
    """
    items = _fetch_recommendations_with_agent(period_days)

    # ── 总体统计 ──
    total = len(items)
    verified_items = [it for it in items if it.get("status") in VERIFIED_STATUSES]
    verified = len(verified_items)
    correct = sum(1 for it in verified_items if it.get("status") == CORRECT_STATUS)
    wrong = sum(1 for it in verified_items if it.get("status") == "wrong")
    flat = sum(1 for it in verified_items if it.get("status") == "flat")

    overall = {
        "total": total,
        "verified": verified,
        "correct": correct,
        "wrong": wrong,
        "flat": flat,
        "accuracy": _safe_div(correct, verified),
    }

    # ── 按 agent 分组（专家胜率）──
    by_agent = _group_by(
        items,
        key_fn=lambda it: it.get("agent_key") or "unknown",
        key_field="agent_key",
    )

    # ── 按 action 分组（direction → action）──
    by_action = _group_by(
        items,
        key_fn=lambda it: DIRECTION_ACTION.get(it.get("direction") or "", it.get("direction") or "unknown"),
        key_field="action",
    )

    result = {
        "overall": overall,
        "by_agent": by_agent,
        "by_action": by_action,
        "trend": get_accuracy_trend(weeks=12),
    }

    # group_by=scenario 时附加按场景（index_name）分组
    if group_by == "scenario":
        result["by_scenario"] = _group_by(
            items,
            key_fn=lambda it: it.get("index_name") or "unknown",
            key_field="scenario",
        )

    return result


def get_accuracy_trend(weeks: int = 12) -> list[dict]:
    """获取按 ISO 周分组的准确率趋势。

    Args:
        weeks: 回溯周数，默认 12

    Returns:
        [{"week": "2026-W27", "total", "verified", "correct", "accuracy"}]
        按周升序排列，无数据时返回空数组。
    """
    cutoff = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d 00:00:00")
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT status, created_at
            FROM recommendations
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    buckets: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "verified": 0, "correct": 0}
    )
    for row in rows:
        created = row["created_at"] or ""
        try:
            dt = datetime.strptime(created[:10], "%Y-%m-%d")
        except Exception:
            continue
        iso = dt.isocalendar()  # (year, week, weekday)
        week_label = f"{iso[0]}-W{iso[1]:02d}"
        bucket = buckets[week_label]
        bucket["total"] += 1
        status = row["status"] or ""
        if status in VERIFIED_STATUSES:
            bucket["verified"] += 1
            if status == CORRECT_STATUS:
                bucket["correct"] += 1

    result = []
    for week_label in sorted(buckets.keys()):
        bucket = buckets[week_label]
        result.append({
            "week": week_label,
            "total": bucket["total"],
            "verified": bucket["verified"],
            "correct": bucket["correct"],
            "accuracy": _safe_div(bucket["correct"], bucket["verified"]),
        })
    return result


def auto_verify_all() -> dict:
    """自动验证到期推荐 + 回测到期决策。

    流程：
      1. 拉取 pending 推荐，按 index_code 取当前指数点位构造 price_map，批量验证
      2. 触发 decisions 表的 T+7 / T+30 自动回测

    Returns:
        {"verified_count": int, "decision_backtested": int}
    """
    # 1. 自动验证到期推荐
    recs = list_recommendations(status="pending")
    price_map: dict[str, float] = {}
    try:
        from services.market_data import get_index_current_price
        for rec in recs:
            code = rec.get("index_code") or ""
            if not code or code in price_map:
                continue
            try:
                price_info = get_index_current_price(code) or {}
                price = price_info.get("price")
                if price is not None:
                    price_map[code] = float(price)
            except Exception as e:
                logger.warning(f"获取指数 {code} 当前点位失败: {e}")
    except Exception as e:
        logger.warning(f"导入 market_data 失败: {e}")

    verified: list = []
    try:
        verified = auto_verify_pending_recommendations(price_map, _today_str())
    except Exception as e:
        logger.error(f"自动验证推荐失败: {e}", exc_info=True)

    # 2. 自动回测决策
    backtested: list = []
    try:
        from db.decisions import auto_backtest_decisions
        backtested = auto_backtest_decisions()
    except Exception as e:
        logger.error(f"自动回测决策失败: {e}", exc_info=True)

    return {"verified_count": len(verified), "decision_backtested": len(backtested)}


def get_verified_recent(limit: int = 20) -> list[dict]:
    """P0-A 决策闭环：最近已验证的建议列表（按验证时间倒序）。

    用于 DecisionAccuracy 页面展示「最近验证结果列表」区块。
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, analysis_id, index_name, index_code, direction, confidence,
                   reason, baseline_value, baseline_date, current_value, current_date,
                   change_pct, status, adopted, adopted_at, verified_at, created_at
            FROM recommendations
            WHERE status IN ('correct', 'wrong', 'flat')
            ORDER BY verified_at DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _directional_return(direction: str, change_pct: float) -> float:
    """计算建议方向收益：up → +change, down → -change, hold/other → 0。

    语义：减仓建议（down）在标的下跌时收益为正（避免了损失）。
    """
    if direction == "up":
        return float(change_pct or 0)
    if direction == "down":
        return -float(change_pct or 0)
    return 0.0


def get_adoption_stats(period_days: int = 180) -> dict:
    """P0-A 决策闭环：采纳率统计 + 采纳 vs 未采纳收益对比。

    用于证明「采纳建议的收益高于未采纳」，引导用户重视 AI 建议。

    Returns:
        {
            "total_marked": int,            # 已标记采纳/不采纳的总数
            "adopted": int,                 # 采纳数
            "rejected": int,                # 不采纳数
            "adoption_rate": float,         # 采纳率（0-1）
            "adopted_avg_return": float,    # 采纳组平均方向收益（%）
            "rejected_avg_return": float,   # 未采纳组平均方向收益（%）
            "adopted_correct_rate": float,  # 采纳组预测正确率（0-1）
            "rejected_correct_rate": float, # 未采纳组预测正确率（0-1）
            "verified_count": int,          # 已验证总数（含未标记）
        }
    """
    cutoff = (datetime.now() - timedelta(days=period_days)).strftime("%Y-%m-%d 00:00:00")
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT direction, change_pct, status, adopted
            FROM recommendations
            WHERE created_at >= ?
              AND status IN ('correct', 'wrong', 'flat')
              AND change_pct IS NOT NULL
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    adopted_returns: list[float] = []
    rejected_returns: list[float] = []
    adopted_correct = 0
    rejected_correct = 0
    adopted_total = 0
    rejected_total = 0

    for row in rows:
        item = dict(row)
        direction = item.get("direction") or ""
        change = item.get("change_pct")
        status = item.get("status") or ""
        adopted = item.get("adopted") or 0

        if change is None:
            continue

        ret = _directional_return(direction, float(change))

        if adopted == 1:
            adopted_returns.append(ret)
            adopted_total += 1
            if status == CORRECT_STATUS:
                adopted_correct += 1
        elif adopted == -1:
            rejected_returns.append(ret)
            rejected_total += 1
            if status == CORRECT_STATUS:
                rejected_correct += 1

    adopted_avg = round(sum(adopted_returns) / len(adopted_returns), 3) if adopted_returns else 0.0
    rejected_avg = round(sum(rejected_returns) / len(rejected_returns), 3) if rejected_returns else 0.0

    return {
        "total_marked": adopted_total + rejected_total,
        "adopted": adopted_total,
        "rejected": rejected_total,
        "adoption_rate": _safe_div(adopted_total, adopted_total + rejected_total),
        "adopted_avg_return": adopted_avg,
        "rejected_avg_return": rejected_avg,
        "adopted_correct_rate": _safe_div(adopted_correct, adopted_total),
        "rejected_correct_rate": _safe_div(rejected_correct, rejected_total),
        "verified_count": len(rows),
    }
