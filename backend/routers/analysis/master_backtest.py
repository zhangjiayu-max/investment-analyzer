"""大师决策回测路由 — /api/analysis/master-backtest/*

三个端点：
- GET /history    大师决策历史列表
- GET /stats      大师胜率统计
- POST /verify    手动触发T+N验证
"""
import logging

from fastapi import APIRouter, HTTPException

from db.master_decision_history import list_master_decisions, get_master_accuracy_stats
from services.master_decision_backtest import auto_backtest_master_decisions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/master-backtest", tags=["analysis-master-backtest"])


@router.get("/history")
async def get_master_history(
    master_key: str = None,
    fund_code: str = None,
    days: int = 90,
    limit: int = 100,
    verified_only: bool = False,
):
    """大师决策历史列表。"""
    try:
        result = list_master_decisions(
            master_key=master_key,
            fund_code=fund_code,
            days=days,
            limit=limit,
            verified_only=verified_only,
        )
        return {"history": result, "count": len(result)}
    except Exception as e:
        logger.error(f"[master-backtest] 历史查询失败: {e}", exc_info=True)
        raise HTTPException(500, f"查询失败: {str(e)}")


@router.get("/stats")
async def get_master_stats(days: int = 90):
    """大师胜率统计。"""
    try:
        result = get_master_accuracy_stats(days=days)
        return result
    except Exception as e:
        logger.error(f"[master-backtest] 统计查询失败: {e}", exc_info=True)
        raise HTTPException(500, f"统计失败: {str(e)}")


@router.post("/verify")
async def trigger_verification():
    """手动触发T+N验证。"""
    try:
        result = auto_backtest_master_decisions()
        return {"ok": True, "stats": result}
    except Exception as e:
        logger.error(f"[master-backtest] 验证触发失败: {e}", exc_info=True)
        raise HTTPException(500, f"验证失败: {str(e)}")
