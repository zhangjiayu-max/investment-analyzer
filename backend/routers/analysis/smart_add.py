"""智能补仓计划器 API。"""
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


@router.get("/api/smart-add/plan")
async def get_smart_add_plan():
    """生成全持仓补仓计划表。"""
    from services.smart_add_planner import generate_smart_add_plan
    return generate_smart_add_plan()


@router.get("/api/smart-add/plan/{fund_code}")
async def get_single_plan(fund_code: str):
    """单标的补仓计划。"""
    from services.smart_add_planner import generate_smart_add_plan
    result = generate_smart_add_plan()
    plans = result.get("plans", [])
    for p in plans:
        if p.get("fund_code") == fund_code:
            return p
    return {"error": f"未找到持仓 {fund_code}"}


@router.get("/api/smart-add/config")
async def get_config():
    """读取智能补仓配置。"""
    from services.smart_add_planner import get_smart_add_config
    return get_smart_add_config()


@router.post("/api/smart-add/config")
async def update_config(config: dict):
    """更新配置。"""
    from db.config import update_config
    allowed_keys = [
        "smart_add.enabled", "smart_add.base_dca_pct", "smart_add.pyramid_enabled",
        "smart_add.pool_pct", "smart_add.pyramid_tiers", "smart_add.loss_threshold",
        "smart_add.max_single_position_pct", "smart_add.valuation_pause_pct", "smart_add.stale_days",
    ]
    updated = []
    for k, v in config.items():
        if k in allowed_keys:
            update_config(k, str(v))
            updated.append(k)
    return {"ok": True, "updated": updated}


class PreviewRequest(BaseModel):
    fund_code: str
    additional_drop_pct: float
    add_amount: float


@router.post("/api/smart-add/preview")
async def preview_scenario(req: PreviewRequest):
    """模拟"如果再跌X%后补Y元"的摊薄效果。"""
    from services.smart_add_planner import preview_add_scenario
    return preview_add_scenario(req.fund_code, req.additional_drop_pct, req.add_amount)
