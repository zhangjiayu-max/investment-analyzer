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


class SimulateRequest(BaseModel):
    fund_code: str
    monthly_drop_pct: float = -5.0
    months: int = 6


@router.post("/api/smart-add/simulate")
async def simulate_strategies_api(req: SimulateRequest):
    """策略对比模拟器：对比不补仓/DCA/金字塔/VA在持续下跌场景下的效果。"""
    from services.smart_add_planner import simulate_strategies
    return simulate_strategies(req.fund_code, req.monthly_drop_pct, req.months)


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


@router.get("/api/smart-add/index-exposure")
async def get_index_exposure():
    """穿透指数集中度：基于持仓穿透后的指数暴露占比 + 软提示。

    返回每个指数的：
    - total_pct: 占总资产百分比（基于 effective_base = max(total_cost, current_value)）
    - funds: 涉及的基金代码列表
    - fund_names: 涉及的基金名称列表
    - fund_type: 基金类型（broad/industry/theme/bond/hk_overseas/unknown）
    - limit_pct: 该类型指数集中度上限
    - warning: {level, current_pct, limit_pct, exceeded, room_pct, message}

    软提示：超限不拦截，仅返回 warning，由前端展示橙色警告。
    """
    from db.portfolio import list_holdings, get_portfolio_summary
    from services.advisor.position_sizing import (
        calc_index_exposure,
        check_index_exposure_warning,
        INDEX_EXPOSURE_LIMITS,
    )
    from services.advisor.smart_add_metrics import classify_fund

    holdings = list_holdings()
    summary = get_portfolio_summary()
    total_assets = summary.get("total_assets") or 0

    exposure = calc_index_exposure(holdings, total_assets)

    # 给每个指数附加 fund_type / limit_pct / warning
    enriched = {}
    for index_code, info in exposure.items():
        # 取该指数下第一个能识别出 fund_type 的基金作为代表
        fund_type = "unknown"
        for fc in info.get("funds", []):
            h = next((x for x in holdings if x.get("fund_code") == fc), None)
            if h:
                ft_info = classify_fund(h)
                ft = ft_info.get("fund_type", "unknown")
                if ft and ft != "unknown":
                    fund_type = ft
                    break
        limit_pct = INDEX_EXPOSURE_LIMITS.get(fund_type, 30)
        warning = check_index_exposure_warning(exposure, index_code, fund_type)
        enriched[index_code] = {
            **info,
            "fund_type": fund_type,
            "limit_pct": limit_pct,
            "warning": warning,
        }

    # 按占比降序
    sorted_exposure = dict(
        sorted(enriched.items(), key=lambda kv: kv[1]["total_pct"], reverse=True)
    )

    return {
        "exposure": sorted_exposure,
        "total_assets": round(total_assets, 2),
        "fund_count": len(holdings),
    }
