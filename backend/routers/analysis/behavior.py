"""行为金融诊断 — GET /api/analysis/behavior/report、GET /api/analysis/behavior/score

纯算法量化 4 类投资者行为偏差（处置/锚定/羊群/过度交易），不调 LLM。
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from services.behavior_diagnosis import diagnose_behavior, get_behavior_score

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-behavior"])


@router.get("/api/analysis/behavior/report")
async def get_behavior_report_api(
    user_id: str = Query("default"),
    period_days: int = Query(90, ge=7, le=730, description="回溯天数，默认 90 天"),
):
    """获取行为金融诊断报告：4 类偏差量化分 + 综合分 + 针对性建议。"""
    try:
        report = diagnose_behavior(user_id=user_id, period_days=period_days)
        return {"ok": True, "data": report}
    except Exception as e:
        logger.error(f"行为诊断报告生成失败 user_id={user_id}: {e}", exc_info=True)
        raise HTTPException(500, f"行为诊断报告生成失败: {e}")


@router.get("/api/analysis/behavior/score")
async def get_behavior_score_api(user_id: str = Query("default")):
    """获取综合行为偏差分（用于 Dashboard），0-1 之间，越高越严重。"""
    try:
        score = get_behavior_score(user_id=user_id)
        return {"ok": True, "score": round(score, 3)}
    except Exception as e:
        logger.error(f"行为偏差分获取失败 user_id={user_id}: {e}", exc_info=True)
        raise HTTPException(500, f"行为偏差分获取失败: {e}")
