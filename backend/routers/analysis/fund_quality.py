"""基金深度分析路由 — /api/analysis/fund-quality/*

六维体检报告 API：
- GET  /api/analysis/fund-quality/{fund_code}   获取单基金体检报告
- POST /api/analysis/fund-quality/batch         批量获取
- POST /api/analysis/fund-quality/refresh       刷新评分
"""
import logging

from fastapi import APIRouter, HTTPException

from services.fund_analysis import (
    calculate_fund_health_report,
    batch_calculate_fund_health,
    refresh_fund_quality_scores,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/fund-quality", tags=["analysis-fund-quality"])


@router.get("/{fund_code}")
async def get_fund_quality_api(fund_code: str, force_refresh: bool = False):
    """获取单基金六维体检报告。

    参数：
    - force_refresh: 是否强制重新计算（默认 false，用24小时缓存）
    """
    if not fund_code or not fund_code.strip():
        raise HTTPException(400, "请提供基金代码")
    fund_code = fund_code.strip()
    try:
        result = calculate_fund_health_report(fund_code, force_refresh=force_refresh)
        return result
    except Exception as e:
        logger.error(f"[fund-quality] 获取基金体检报告失败 {fund_code}: {e}", exc_info=True)
        raise HTTPException(500, f"分析失败: {str(e)}")


@router.post("/batch")
async def batch_fund_quality_api(req: dict):
    """批量获取基金体检报告。

    Body: {"fund_codes": ["161725", "005827"], "force_refresh": false}
    """
    fund_codes = req.get("fund_codes") or []
    if not fund_codes or not isinstance(fund_codes, list):
        raise HTTPException(400, "请提供 fund_codes 列表")
    force_refresh = bool(req.get("force_refresh", False))
    try:
        results = batch_calculate_fund_health(fund_codes, force_refresh=force_refresh)
        return {"results": results}
    except Exception as e:
        logger.error(f"[fund-quality] 批量分析失败: {e}", exc_info=True)
        raise HTTPException(500, f"批量分析失败: {str(e)}")


@router.post("/refresh")
async def refresh_fund_quality_api(req: dict = None):
    """刷新基金质量评分。

    Body: {"fund_codes": ["161725"]}  # 可选,不传则刷新所有持仓+关注
    """
    req = req or {}
    fund_codes = req.get("fund_codes")
    try:
        result = refresh_fund_quality_scores(fund_codes)
        return result
    except Exception as e:
        logger.error(f"[fund-quality] 刷新评分失败: {e}", exc_info=True)
        raise HTTPException(500, f"刷新失败: {str(e)}")
