"""决策准确率追踪 API — /api/analysis/accuracy/*

  - GET  /api/analysis/accuracy/stats             准确率统计 + 专家胜率
  - POST /api/analysis/accuracy/auto-verify       触发自动验证（推荐 + 决策回测）
  - GET  /api/analysis/accuracy/trend             按周准确率趋势
  - GET  /api/analysis/accuracy/recent-verified   最近已验证建议列表
  - GET  /api/analysis/accuracy/adoption-stats    采纳率 + 采纳 vs 未采纳收益对比
"""

import logging

from fastapi import APIRouter, Query

from services.decision_accuracy import (
    auto_verify_all,
    get_accuracy_stats,
    get_accuracy_trend,
    get_adoption_stats,
    get_verified_recent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/accuracy", tags=["analysis-accuracy"])


@router.get("/stats")
def get_stats(
    period_days: int = Query(90, ge=1, le=365, description="统计周期（天）"),
    group_by: str = Query("agent", description="主分组维度：agent / scenario / action_type"),
):
    """获取决策准确率统计与专家胜率。"""
    try:
        return get_accuracy_stats(period_days=period_days, group_by=group_by)
    except Exception as e:
        logger.error(f"获取准确率统计失败: {e}", exc_info=True)
        return {
            "overall": {
                "total": 0, "verified": 0, "correct": 0,
                "wrong": 0, "flat": 0, "accuracy": 0.0,
            },
            "by_agent": [],
            "by_action": [],
            "trend": [],
            "error": str(e),
        }


@router.post("/auto-verify")
def trigger_auto_verify():
    """触发自动验证到期推荐 + 决策回测。"""
    try:
        return auto_verify_all()
    except Exception as e:
        logger.error(f"自动验证失败: {e}", exc_info=True)
        return {"verified_count": 0, "decision_backtested": 0, "error": str(e)}


@router.get("/trend")
def get_trend(
    weeks: int = Query(12, ge=1, le=52, description="回溯周数"),
):
    """获取按周准确率趋势。"""
    try:
        return get_accuracy_trend(weeks=weeks)
    except Exception as e:
        logger.error(f"获取准确率趋势失败: {e}", exc_info=True)
        return []


@router.get("/recent-verified")
def get_recent_verified(
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
):
    """P0-A 决策闭环：最近已验证的建议列表（按验证时间倒序）。"""
    try:
        return {"items": get_verified_recent(limit=limit)}
    except Exception as e:
        logger.error(f"获取最近验证建议失败: {e}", exc_info=True)
        return {"items": []}


@router.get("/adoption-stats")
def get_adoption(
    period_days: int = Query(180, ge=1, le=730, description="统计周期（天）"),
):
    """P0-A 决策闭环：采纳率统计 + 采纳 vs 未采纳收益对比。"""
    try:
        return get_adoption_stats(period_days=period_days)
    except Exception as e:
        logger.error(f"获取采纳率统计失败: {e}", exc_info=True)
        return {
            "total_marked": 0, "adopted": 0, "rejected": 0,
            "adoption_rate": 0.0, "adopted_avg_return": 0.0,
            "rejected_avg_return": 0.0, "adopted_correct_rate": 0.0,
            "rejected_correct_rate": 0.0, "verified_count": 0,
            "error": str(e),
        }
