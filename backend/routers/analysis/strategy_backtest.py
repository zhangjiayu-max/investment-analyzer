"""策略库回测路由 — /api/analysis/strategy/*

接口：
  - GET  /list       → 策略模板列表
  - POST /backtest   → 运行回测
  - POST /sweep      → 参数扫描
  - GET  /results    → 历史回测结果
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.strategy_library import (
    STRATEGY_TEMPLATES,
    run_backtest,
    parameter_sweep,
)
from db.backtest_results import list_backtests

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/strategy", tags=["strategy-backtest"])


# ── 请求模型 ──────────────────────────────────────────────

class BacktestRequest(BaseModel):
    """运行回测请求。"""
    strategy: str = Field(..., description="策略名称：dca/grid/two_eight/core_satellite")
    target_code: str = Field(..., description="目标指数代码")
    params: dict = Field(default_factory=dict, description="策略参数")
    start_date: str | None = Field(None, description="起始日期 YYYY-MM-DD")
    end_date: str | None = Field(None, description="结束日期 YYYY-MM-DD")
    initial_cash: float = Field(100000, description="初始资金")


class SweepRequest(BaseModel):
    """参数扫描请求。"""
    strategy: str = Field(..., description="策略名称")
    target_code: str = Field(..., description="目标指数代码")
    param_ranges: dict = Field(
        default_factory=dict,
        description="参数范围 {param_name: [val1, val2, ...]}",
    )
    start_date: str | None = None
    end_date: str | None = None
    initial_cash: float = 100000


# ── API 端点 ──────────────────────────────────────────────

@router.get("/list")
async def list_strategies():
    """获取策略模板列表。"""
    return {"strategies": STRATEGY_TEMPLATES}


@router.post("/backtest")
async def backtest(req: BacktestRequest):
    """运行回测。"""
    try:
        result = await asyncio.to_thread(
            run_backtest,
            req.strategy,
            req.target_code,
            req.params,
            req.start_date,
            req.end_date,
            req.initial_cash,
        )
    except Exception as e:
        logger.exception(f"回测执行异常: {e}")
        raise HTTPException(500, f"回测失败: {e}")

    if result.get("status") == "error":
        raise HTTPException(400, result.get("error", "回测失败"))
    return result


@router.post("/sweep")
async def sweep(req: SweepRequest):
    """参数扫描。"""
    try:
        results = await asyncio.to_thread(
            parameter_sweep,
            req.strategy,
            req.target_code,
            req.param_ranges,
            req.start_date,
            req.end_date,
            req.initial_cash,
        )
    except Exception as e:
        logger.exception(f"参数扫描异常: {e}")
        raise HTTPException(500, f"参数扫描失败: {e}")

    return {"results": results, "count": len(results)}


@router.get("/results")
async def get_results(limit: int = 20):
    """获取历史回测结果。"""
    items = list_backtests(limit=limit)
    return {"items": items, "count": len(items)}
