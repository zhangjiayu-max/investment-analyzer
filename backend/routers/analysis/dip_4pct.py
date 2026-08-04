"""4%定投法 API 路由。

来源：雷牛牛4%定投法（https://mp.weixin.qq.com/s/8Dhofw5taUPL_teHoYJ8-w）
功能：建仓配置 + 触发检查 + 进度查询
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db.dip_investment_plans import (
    create_dip_plan, get_dip_plan, list_dip_plans, update_dip_plan, delete_dip_plan,
    list_dip_triggers, mark_trigger_executed,
)
from services.dip_4pct_strategy import get_dip_4pct_strategy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dip-plan", tags=["4%定投法"])


class DipPlanCreate(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    fund_name: str = Field("", description="基金名称")
    max_amount: float = Field(..., gt=0, description="投资上限（元）")
    total_shares: int = Field(10, ge=1, le=50, description="总份数（默认10）")
    dip_pct: float = Field(4.0, ge=1, le=20, description="单次触发跌幅%（默认4）")
    valuation_threshold: float = Field(20, ge=0, le=80, description="估值百分位门槛（默认20%）")
    metric_type: str = Field(None, description="估值指标（市盈率/市净率/市销率）")


class DipPlanUpdate(BaseModel):
    max_amount: float | None = None
    total_shares: int | None = None
    dip_pct: float | None = None
    valuation_threshold: float | None = None
    metric_type: str | None = None
    enabled: int | None = None
    status: str | None = None
    fund_name: str | None = None


@router.post("")
async def create_plan(plan: DipPlanCreate):
    """创建4%定投配置。"""
    try:
        plan_id = create_dip_plan(
            fund_code=plan.fund_code,
            fund_name=plan.fund_name,
            max_amount=plan.max_amount,
            total_shares=plan.total_shares,
            dip_pct=plan.dip_pct,
            valuation_threshold=plan.valuation_threshold,
            metric_type=plan.metric_type,
        )
        return {"ok": True, "id": plan_id, "message": "4%定投配置创建成功"}
    except Exception as e:
        logger.error(f"创建4%定投配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{fund_code}")
async def get_plan(fund_code: str):
    """查询单个配置。"""
    plan = get_dip_plan(fund_code)
    if not plan:
        raise HTTPException(status_code=404, detail="未找到4%定投配置")
    return {"plan": plan}


@router.get("/list/all")
async def list_plans(active_only: bool = True):
    """列出所有配置。"""
    plans = list_dip_plans(active_only=active_only)
    return {"plans": plans, "total": len(plans)}


@router.put("/{plan_id}")
async def update_plan(plan_id: int, update: DipPlanUpdate):
    """更新配置。"""
    fields = {k: v for k, v in update.dict().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="无更新字段")
    success = update_dip_plan(plan_id, **fields)
    if not success:
        raise HTTPException(status_code=404, detail="配置不存在或无有效字段")
    return {"ok": True, "message": "更新成功"}


@router.delete("/{plan_id}")
async def remove_plan(plan_id: int):
    """删除配置。"""
    success = delete_dip_plan(plan_id)
    if not success:
        raise HTTPException(status_code=404, detail="配置不存在")
    return {"ok": True, "message": "删除成功"}


@router.get("/{fund_code}/check")
async def check_trigger(fund_code: str):
    """手动检查是否触发4%定投。"""
    strategy = get_dip_4pct_strategy()
    if not strategy.is_enabled():
        raise HTTPException(status_code=403, detail="4%定投法开关未开启（dip_4pct.enabled）")
    result = strategy.check_trigger(fund_code)
    return result


@router.get("/{fund_code}/status")
async def get_status(fund_code: str):
    """查询4%定投进度（用于前端展示）。"""
    strategy = get_dip_4pct_strategy()
    return strategy.get_status(fund_code)


@router.get("/{fund_code}/triggers")
async def list_trigger_history(fund_code: str):
    """查询触发历史。"""
    plan = get_dip_plan(fund_code)
    if not plan:
        return {"triggers": [], "total": 0}
    triggers = list_dip_triggers(plan_id=plan["id"])
    return {"triggers": triggers, "total": len(triggers)}


@router.post("/triggers/{trigger_id}/execute")
async def execute_trigger(trigger_id: int):
    """标记触发为已执行（用户确认买入后调用）。"""
    success = mark_trigger_executed(trigger_id)
    if not success:
        raise HTTPException(status_code=404, detail="触发记录不存在")
    return {"ok": True, "message": "已标记为执行"}
