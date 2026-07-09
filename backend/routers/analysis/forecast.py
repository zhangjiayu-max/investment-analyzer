"""估值预测信号路由 — /api/analysis/forecast/*

  - GET /mean-reversion  均值回归分析
  - GET /extreme         极值预警
  - GET /signals         全市场信号扫描
"""
import logging

from fastapi import APIRouter, HTTPException

from services.valuation_forecast import (
    mean_reversion_analysis,
    extreme_warning,
    forecast_signals,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/forecast", tags=["analysis-forecast"])


@router.get("/mean-reversion")
async def mean_reversion_api(index_code: str, metric_type: str = "市盈率"):
    """均值回归分析：半衰期 + 回归概率 + 预期收益 + 信号判定。"""
    if not index_code:
        raise HTTPException(400, "index_code 不能为空")
    try:
        result = mean_reversion_analysis(index_code, metric_type)
    except Exception as e:
        logger.error(f"[forecast] mean-reversion 接口异常 {index_code}: {e}", exc_info=True)
        raise HTTPException(500, f"分析失败: {e}")
    return {"ok": True, "result": result}


@router.get("/extreme")
async def extreme_api(index_code: str, metric_type: str = "市盈率"):
    """极值预警：分位 <5% 或 >95% 触发，附历史相似时点后续收益。"""
    if not index_code:
        raise HTTPException(400, "index_code 不能为空")
    try:
        result = extreme_warning(index_code, metric_type)
    except Exception as e:
        logger.error(f"[forecast] extreme 接口异常 {index_code}: {e}", exc_info=True)
        raise HTTPException(500, f"分析失败: {e}")
    return {"ok": True, "result": result}


@router.get("/signals")
async def signals_api():
    """全市场信号扫描：返回 signal_strength > 0.5 的强信号列表。"""
    try:
        signals = forecast_signals()
    except Exception as e:
        logger.error(f"[forecast] signals 接口异常: {e}", exc_info=True)
        raise HTTPException(500, f"扫描失败: {e}")
    return {"ok": True, "signals": signals, "count": len(signals)}
