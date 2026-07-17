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
        "smart_add.max_add_vs_position_mult", "smart_add.cooldown_days", "smart_add.max_buys_in_cooldown",
        "smart_add.trend_signal_enabled", "smart_add.trend_base_ratio", "smart_add.trend_position_pct",
        "smart_add.dip_signal_enabled", "smart_add.dip_base_ratio", "smart_add.dca_drop_step_pct",
        "smart_add.dca_tiers",
        # 退出信号（2026-07-17 新增）
        "smart_add.exit_signal_enabled",
        # 价值平均法 + 网格交易 + 基本面健康（2026-07-17 新增）
        "smart_add.va_enabled", "smart_add.grid_enabled", "smart_add.fund_health_enabled",
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


# ── 反事实决策验证：假设操作跟踪 ──


@router.get("/api/smart-add/snapshots")
async def list_snapshots(
    fund_code: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """查询历史智能补仓建议快照。"""
    from db.smart_add_snapshots import list_snapshots as _list
    rows = _list(fund_code=fund_code, start_date=start_date, end_date=end_date, limit=limit)
    return {"data": rows, "total": len(rows)}


@router.get("/api/smart-add/hypothetical/track")
async def track_hypothetical():
    """反事实跟踪验证：所有假设补仓操作的当前盈亏 + 假设vs真实组合对比。

    假设操作由系统自动生成（每次生成补仓建议时自动创建），用户无需手动操作。
    """
    from services.counterfactual_verifier import verify_all_hypothetical
    return verify_all_hypothetical()


@router.delete("/api/smart-add/hypothetical/{tx_id}")
async def delete_hypothetical(tx_id: int):
    """删除假设交易（清理噪声用，不影响真实持仓）。"""
    from db.smart_add_snapshots import delete_hypothetical_tx
    ok = delete_hypothetical_tx(tx_id)
    return {"ok": ok}
