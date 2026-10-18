"""策略库回测路由 — /api/analysis/strategy/*

接口：
  - GET  /list       → 策略模板列表
  - POST /backtest   → 运行回测
  - POST /sweep      → 参数扫描
  - GET  /results    → 历史回测结果
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Literal

from services.strategy_library import (
    STRATEGY_TEMPLATES,
    run_backtest,
    parameter_sweep,
    validate_backtest_request,
)
from db.backtest_results import list_backtests

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/strategy", tags=["strategy-backtest"])


# ── 请求模型 ──────────────────────────────────────────────

class BacktestRequest(BaseModel):
    """运行回测请求。"""
    model_config = ConfigDict(allow_inf_nan=False)
    strategy: Literal["dca", "grid", "two_eight", "core_satellite"] = Field(..., description="策略名称")
    target_code: str = Field(..., description="目标代码（指数或基金）")
    params: dict = Field(default_factory=dict, description="策略参数")
    start_date: str | None = Field(None, description="起始日期 YYYY-MM-DD")
    end_date: str | None = Field(None, description="结束日期 YYYY-MM-DD")
    initial_cash: float = Field(100000, gt=0, le=1e12, description="初始资金")
    target_type: Literal["index", "fund"] = Field("index", description="目标类型：index 或 fund（P3 新增）")

    @model_validator(mode="after")
    def validate_strategy_parameters(self):
        validate_backtest_request(self.strategy, self.target_code, self.params,
                                  self.start_date, self.end_date, self.initial_cash, self.target_type)
        return self


class SweepRequest(BaseModel):
    """参数扫描请求。"""
    model_config = ConfigDict(allow_inf_nan=False)
    strategy: Literal["dca", "grid", "two_eight", "core_satellite"] = Field(..., description="策略名称")
    target_code: str = Field(..., description="目标代码（指数或基金）")
    param_ranges: dict = Field(
        default_factory=dict,
        description="参数范围 {param_name: [val1, val2, ...]}",
    )
    start_date: str | None = None
    end_date: str | None = None
    initial_cash: float = Field(100000, gt=0, le=1e12)
    target_type: Literal["index", "fund"] = Field("index", description="目标类型：index 或 fund（P3 新增）")


# ── API 端点 ──────────────────────────────────────────────

@router.get("/list")
async def list_strategies():
    """获取策略模板列表。"""
    from db.config import get_config
    return {"strategies": STRATEGY_TEMPLATES, "enabled": str(get_config("strategy_backtest.enabled", "true")).lower() == "true"}


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
            req.target_type,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
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
            req.target_type,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.exception(f"参数扫描异常: {e}")
        raise HTTPException(500, f"参数扫描失败: {e}")

    return {"results": results, "count": len(results)}


@router.get("/results")
async def get_results(limit: int = Query(20, ge=1, le=100)):
    """获取历史回测结果。"""
    items = list_backtests(limit=limit)
    return {"items": items, "count": len(items)}
