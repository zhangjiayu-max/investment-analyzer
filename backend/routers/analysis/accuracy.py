"""决策准确率追踪 API — /api/analysis/accuracy/*

  - GET  /api/analysis/accuracy/stats        准确率统计 + 专家胜率
  - POST /api/analysis/accuracy/auto-verify  触发自动验证（推荐 + 决策回测）
  - GET  /api/analysis/accuracy/trend        按周准确率趋势
"""

import logging

from fastapi import APIRouter, Query

from services.decision_accuracy import (
    auto_verify_all,
    get_accuracy_stats,
    get_accuracy_trend,
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
