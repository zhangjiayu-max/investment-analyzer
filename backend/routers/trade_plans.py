"""交易计划 API 路由。"""

from fastapi import APIRouter, HTTPException, Body

from db import (
    create_trade_plan, get_trade_plan, list_trade_plans,
    update_trade_plan, delete_trade_plan, list_pending_trade_plans,
)
from services.trade_plan_engine import generate_trade_plan, get_pending_trade_plans_summary

router = APIRouter(prefix="/api/trade-plans", tags=["trade-plans"])


@router.post("/generate")
async def generate_plan(recommendation_id: int = Body(..., embed=True)):
    plan_id = generate_trade_plan(recommendation_id)
    if not plan_id:
        raise HTTPException(status_code=400, detail="生成交易计划失败")
    return {"ok": True, "plan_id": plan_id}


@router.post("/")
async def create_plan(
    recommendation_id: int = None,
    fund_code: str = Body(..., embed=True),
    fund_name: str = Body(None, embed=True),
    action: str = Body("BUY", embed=True),
    amount: float = Body(0, embed=True),
    shares: float = Body(0, embed=True),
    target_price: float = Body(None, embed=True),
    batch_count: int = Body(1, embed=True),
    batch_interval_days: int = Body(7, embed=True),
    stop_loss_pct: float = Body(None, embed=True),
    take_profit_pct: float = Body(None, embed=True),
    execution_notes: str = Body(None, embed=True),
):
    plan_id = create_trade_plan(
        recommendation_id=recommendation_id,
        fund_code=fund_code,
        fund_name=fund_name,
        action=action,
        amount=amount,
        shares=shares,
        target_price=target_price,
        batch_count=batch_count,
        batch_interval_days=batch_interval_days,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        execution_notes=execution_notes,
    )
    return {"ok": True, "plan_id": plan_id}


@router.get("/")
async def list_plans(status: str = None, fund_code: str = None):
    plans = list_trade_plans(status=status, fund_code=fund_code)
    return {"ok": True, "data": plans}


@router.get("/{plan_id}")
async def get_plan(plan_id: int):
    plan = get_trade_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="交易计划不存在")
    return {"ok": True, "data": plan}


@router.put("/{plan_id}")
async def update_plan(plan_id: int, data: dict = Body(...)):
    update_trade_plan(plan_id, **data)
    return {"ok": True}


@router.delete("/{plan_id}")
async def delete_plan(plan_id: int):
    success = delete_trade_plan(plan_id)
    if not success:
        raise HTTPException(status_code=404, detail="交易计划不存在")
    return {"ok": True}


@router.get("/pending/summary")
async def pending_summary():
    summary = get_pending_trade_plans_summary()
    return {"ok": True, "data": summary}
