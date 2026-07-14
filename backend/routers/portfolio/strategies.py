"""策略监控 API 路由。"""

from fastapi import APIRouter, HTTPException, Body
from typing import Optional

from db import (
    get_strategy_monitoring, list_strategy_monitoring,
    update_strategy_monitoring, delete_strategy_monitoring,
    list_strategy_trades,
)
from services.strategy_monitor import create_monitor, run_strategy_check, get_monitor_stats

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.post("/monitor")
async def create_strategy_monitor(
    strategy_name: str = Body(...),
    strategy_type: str = Body(...),
    target_code: str = Body(...),
    target_type: str = Body('index'),
    parameters: dict = Body({}),
):
    monitor_id = create_monitor(strategy_name, strategy_type, target_code, parameters)
    return {"ok": True, "monitor_id": monitor_id}


@router.get("/monitor")
async def list_monitors(status: Optional[str] = None, target_code: Optional[str] = None):
    monitors = list_strategy_monitoring(status=status, target_code=target_code)
    return {"code": 0, "message": "ok", "data": monitors}


@router.get("/monitor/{monitor_id}")
async def get_monitor(monitor_id: int):
    monitor = get_strategy_monitoring(monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail="策略监控不存在")
    return {"code": 0, "message": "ok", "data": monitor}


@router.put("/monitor/{monitor_id}")
async def update_monitor(
    monitor_id: int,
    current_state: Optional[str] = Body(None),
    parameters: Optional[dict] = Body(None),
):
    update_fields = {}
    if current_state:
        update_fields['current_state'] = current_state
    if parameters:
        import json
        update_fields['parameters'] = json.dumps(parameters, ensure_ascii=False)
    if not update_fields:
        raise HTTPException(status_code=400, detail="请提供更新字段")
    update_strategy_monitoring(monitor_id, **update_fields)
    return {"ok": True}


@router.delete("/monitor/{monitor_id}")
async def delete_monitor(monitor_id: int):
    success = delete_strategy_monitoring(monitor_id)
    if not success:
        raise HTTPException(status_code=404, detail="策略监控不存在")
    return {"ok": True}


@router.post("/monitor/{monitor_id}/trigger")
async def trigger_strategy(monitor_id: int):
    result = run_strategy_check(monitor_id)
    if result is None:
        raise HTTPException(status_code=404, detail="策略监控不存在")
    return {"ok": True, "data": result}


@router.get("/monitor/{monitor_id}/stats")
async def get_monitor_statistics(monitor_id: int):
    stats = get_monitor_stats(monitor_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="策略监控不存在")
    return {"code": 0, "message": "ok", "data": stats}


@router.get("/monitor/{monitor_id}/trades")
async def get_monitor_trades(monitor_id: int):
    trades = list_strategy_trades(monitoring_id=monitor_id)
    return {"code": 0, "message": "ok", "data": trades}
