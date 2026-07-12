"""家庭财务规划 API 路由。"""

from fastapi import APIRouter, Query, Body

from services.finance_planner import (
    forecast_cash_flow, generate_allocation_suggestion,
    stress_test, get_goals_progress,
)

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/cash-flow-forecast")
async def get_cash_flow_forecast(months: int = Query(12, ge=1, le=36)):
    forecast = forecast_cash_flow(months)
    return {"code": 0, "message": "ok", "data": forecast}


@router.get("/allocation-suggestion")
async def get_finance_allocation_suggestion():
    suggestion = generate_allocation_suggestion()
    return {"code": 0, "message": "ok", "data": suggestion}


@router.post("/stress-test")
async def run_stress_test(scenario: str = Body('moderate')):
    result = stress_test(scenario)
    return {"code": 0, "message": "ok", "data": result}


@router.get("/goals")
async def get_finance_goals():
    progress = get_goals_progress()
    return {"code": 0, "message": "ok", "data": progress}
