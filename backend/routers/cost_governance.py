"""成本治理 + 评测增强 API — 统计检验 + 告警 + 成本仪表盘。"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from infra.cost_tracker import cost_tracker, budget_controller
from db._conn import _get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cost-governance", tags=["cost-governance"])


# ============ 成本仪表盘 ============

@router.get("/dashboard")
async def cost_dashboard():
    """成本仪表盘：今日 + 当月 + 预算状态。"""
    daily = cost_tracker.daily_summary()
    budget = budget_controller.get_status()
    return {
        "status": "ok",
        "daily": daily,
        "budget": budget,
    }


@router.get("/daily")
async def cost_daily_api(date: str = None):
    """指定日期的成本明细。"""
    summary = cost_tracker.daily_summary(date)
    return {"status": "ok", "summary": summary}


@router.get("/monthly")
async def cost_monthly_api():
    """当月成本汇总。"""
    return {"status": "ok", "summary": cost_tracker.monthly_summary()}


class BudgetUpdateRequest(BaseModel):
    monthly_budget: float = Field(..., gt=0, le=10000)


@router.put("/budget")
async def update_budget_api(data: BudgetUpdateRequest):
    """更新月度预算上限。"""
    from db.config import update_config
    update_config("cost.monthly_budget", str(data.monthly_budget))
    # 更新内存中的 controller
    budget_controller.budget = data.monthly_budget
    return {"status": "ok", "budget": data.monthly_budget}


# ============ 统计显著性检验 ============

def _normal_cdf(x: float) -> float:
    """标准正态分布 CDF 近似计算。"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def mann_whitney_u_test(scores_a: list[float], scores_b: list[float]) -> dict:
    """Mann-Whitney U 检验（非参数检验）。

    适用场景：两个版本的评分分布对比，不要求正态分布。
    样本量 >= 3 即可运行（>= 8 时用正态近似更准确）。
    """
    if len(scores_a) < 3 or len(scores_b) < 3:
        return {"p_value": 1.0, "significant": False, "reason": "样本不足 (需要各>=3)"}

    # 合并排序
    combined = [(s, "a") for s in scores_a] + [(s, "b") for s in scores_b]
    combined.sort(key=lambda x: x[0])

    # 计算 rank 和（处理并列用平均 rank）
    rank_sum_a = 0.0
    n1, n2 = len(scores_a), len(scores_b)
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2  # 平均 rank（1-based）
        for k in range(i, j + 1):
            if combined[k][1] == "a":
                rank_sum_a += avg_rank
        i = j + 1

    # U 统计量
    u1 = rank_sum_a - (n1 * (n1 + 1)) / 2
    u2 = n1 * n2 - u1
    u = min(u1, u2)

    # 正态近似（n1+n2 >= 8 时适用）
    mu = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)

    if sigma == 0:
        return {"p_value": 1.0, "significant": False, "reason": "方差为零"}

    z = (u - mu) / sigma
    p_value = 2 * (1 - _normal_cdf(abs(z)))  # 双尾

    mean_a = sum(scores_a) / n1
    mean_b = sum(scores_b) / n2

    return {
        "mean_a": round(mean_a, 3),
        "mean_b": round(mean_b, 3),
        "diff": round(mean_b - mean_a, 3),
        "u_statistic": round(u, 3),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "sample_sizes": {"a": n1, "b": n2},
        "conclusion": (
            "B 显著优于 A" if p_value < 0.05 and mean_b > mean_a else
            "A 显著优于 B" if p_value < 0.05 else
            "差异不显著，需要更多数据"
        ),
    }


class ABTestSignificanceRequest(BaseModel):
    scores_a: list[float] = Field(..., min_items=3)
    scores_b: list[float] = Field(..., min_items=3)
    label_a: str = "A"
    label_b: str = "B"


@router.post("/ab-test/significance")
async def ab_test_significance_api(data: ABTestSignificanceRequest):
    """A/B 测试统计显著性检验。"""
    result = mann_whitney_u_test(data.scores_a, data.scores_b)
    result["label_a"] = data.label_a
    result["label_b"] = data.label_b
    return {"status": "ok", "result": result}


# ============ 告警规则引擎 ============

class AlertEngine:
    """告警规则引擎：检查预设阈值，带冷却期。"""

    def __init__(self):
        self.last_alerts: dict[str, datetime] = {}

    def run_checks(self, daily_stats: dict, prev_stats: dict = None) -> list[dict]:
        """执行所有告警检查。"""
        rules = [
            ("score_drop", 24, self._check_score_drop),
            ("hallucination_spike", 12, self._check_hallucination_rate),
            ("negative_feedback_spike", 12, self._check_negative_feedback),
            ("latency_spike", 6, self._check_latency),
            ("budget_warning", 12, self._check_budget),
        ]
        alerts = []
        now = datetime.now()

        for name, cooldown_hours, check_fn in rules:
            last = self.last_alerts.get(name)
            if last and (now - last).total_seconds() < cooldown_hours * 3600:
                continue
            detail = check_fn(daily_stats, prev_stats)
            if detail:
                self.last_alerts[name] = now
                alerts.append({"rule": name, "detail": detail, "time": now.isoformat()})

        return alerts

    def _check_score_drop(self, stats, prev):
        if not prev:
            return None
        cur = stats.get("avg_score", 0)
        prv = prev.get("avg_score", 0)
        if prv - cur > 0.5:
            return f"评分从 {prv:.2f} 降至 {cur:.2f}"
        return None

    def _check_hallucination_rate(self, stats, prev):
        rate = stats.get("hallucination_rate", 0)
        if rate > 0.15:
            return f"幻觉率 {rate:.1%} > 阈值 15%"
        return None

    def _check_negative_feedback(self, stats, prev):
        rate = stats.get("negative_feedback_rate", 0)
        if rate > 0.20:
            return f"负面反馈率 {rate:.1%} > 阈值 20%"
        return None

    def _check_latency(self, stats, prev):
        latency = stats.get("avg_latency", 0)
        if latency > 15:
            return f"平均延迟 {latency:.1f}s > 阈值 15s"
        return None

    def _check_budget(self, stats, prev):
        pct = stats.get("budget_usage_pct", 0)
        if pct > 80:
            return f"月度预算已用 {pct:.0f}%，即将超额"
        return None


_alert_engine = AlertEngine()


@router.get("/alerts")
async def get_alerts_api():
    """获取当前告警状态。"""
    # 从评测日报取最新统计
    try:
        conn = _get_conn()
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        today_row = conn.execute(
            "SELECT avg_score, alerts FROM eval_daily_reports WHERE report_date = ?",
            (today,)
        ).fetchone()
        prev_row = conn.execute(
            "SELECT avg_score FROM eval_daily_reports WHERE report_date = ?",
            (yesterday,)
        ).fetchone()
        conn.close()

        daily_stats = {
            "avg_score": today_row["avg_score"] if today_row else 0,
        }
        prev_stats = {"avg_score": prev_row["avg_score"]} if prev_row else None
    except Exception:
        daily_stats = {}
        prev_stats = None

    # 加入预算状态
    budget = budget_controller.get_status()
    daily_stats["budget_usage_pct"] = budget["usage_pct"]

    alerts = _alert_engine.run_checks(daily_stats, prev_stats)
    return {"status": "ok", "alerts": alerts, "stats": daily_stats}
