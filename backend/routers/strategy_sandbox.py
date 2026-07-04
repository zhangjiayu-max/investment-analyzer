"""策略沙盒路由 — /api/strategy-sandbox/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.strategy_sandbox import PRESETS, run_backtest
from db import (
    save_backtest, list_backtests, get_backtest, delete_backtest,
    link_backtest_to_decision,
)

router = APIRouter(tags=["strategy-sandbox"])


class BacktestRequest(BaseModel):
    target_code: str
    target_type: str = "index"  # "index" | "fund"
    strategy: str = "dca"
    initial_cash: float = 10000
    monthly_amount: float = 1000
    days: int = 1095  # 3 年
    # 费率参数
    buy_fee_rate: float = 0.0015   # 申购费率 0.15%
    sell_fee_rate: float = 0.005   # 赎回费率 0.5%
    mgmt_fee_annual: float = 0.015 # 年管理费率 1.5%
    # 估值加权定投参数
    low_pct: float = 30
    high_pct: float = 70
    min_multiplier: float = 0.5
    max_multiplier: float = 2.0
    # 估值分位买卖参数
    buy_threshold: float = 30
    sell_threshold: float = 70
    buy_amount: float = 2000
    sell_ratio: float = 0.3
    # 再平衡参数
    frequency_months: int = 3
    equity_target: float = 0.6
    drift_threshold: float = 0.05


@router.get("/api/strategy-sandbox/presets")
async def get_presets():
    """获取预设策略模板。"""
    return {"presets": PRESETS}


@router.post("/api/strategy-sandbox/backtest")
async def run_backtest_api(req: BacktestRequest):
    """运行策略回测。"""
    import asyncio
    try:
        result = await asyncio.to_thread(run_backtest, req.model_dump())
        return result
    except Exception as e:
        raise HTTPException(500, f"回测失败: {str(e)}")


# ── 回测结果持久化 ──────────────────────────────────────

class SaveBacktestRequest(BaseModel):
    name: str
    target_code: str
    target_type: str = "index"
    strategy: str = "dca"
    params: dict = {}
    result: dict = {}
    benchmark: dict = {}
    months: int = 0
    notes: str = ""


@router.post("/api/strategy-sandbox/save")
async def save_backtest_api(req: SaveBacktestRequest):
    """保存回测结果。"""
    backtest_id = save_backtest(
        name=req.name,
        target_code=req.target_code,
        target_type=req.target_type,
        strategy=req.strategy,
        params=req.params,
        result=req.result,
        benchmark=req.benchmark,
        months=req.months,
        notes=req.notes,
    )
    return {"ok": True, "id": backtest_id}


@router.get("/api/strategy-sandbox/history")
async def list_backtests_api(limit: int = 20):
    """列出历史回测。"""
    return {"items": list_backtests(limit=limit)}


@router.get("/api/strategy-sandbox/{backtest_id}")
async def get_backtest_api(backtest_id: int):
    """获取单条回测详情（含净值曲线）。"""
    item = get_backtest(backtest_id)
    if not item:
        raise HTTPException(404, "回测记录不存在")
    return item


@router.delete("/api/strategy-sandbox/{backtest_id}")
async def delete_backtest_api(backtest_id: int):
    """删除回测记录。"""
    ok = delete_backtest(backtest_id)
    if not ok:
        raise HTTPException(404, "回测记录不存在")
    return {"ok": True}


class LinkDecisionRequest(BaseModel):
    decision_id: int


@router.post("/api/strategy-sandbox/{backtest_id}/link-decision")
async def link_decision_api(backtest_id: int, req: LinkDecisionRequest):
    """关联回测到决策。"""
    ok = link_backtest_to_decision(backtest_id, req.decision_id)
    if not ok:
        raise HTTPException(404, "回测记录不存在")
    return {"ok": True}
