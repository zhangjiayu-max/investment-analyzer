"""全账户资产健康度诊断 2.0 — API 路由。

提供资产全景、健康分、四笔钱诊断、行动清单、历史趋势、用户画像等接口。
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from services.health.health_v2_service import (
    get_health_v2_dashboard,
    recalculate_health_v2,
    get_or_create_profile,
    update_profile,
    get_health_v2_history,
    update_action_status,
    DEFAULT_TARGET_POTS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health-v2", tags=["health-v2"])


class ProfileUpdate(BaseModel):
    risk_level: Optional[str] = Field(None, description="风险偏好: conservative/steady/aggressive")
    target_date: Optional[str] = Field(None, description="目标日期 YYYY-MM-DD")
    target_pots: Optional[dict] = Field(None, description="四笔钱目标配比")
    monthly_investable: Optional[float] = Field(None, ge=0, description="每月可投资金额")
    emergency_months: Optional[int] = Field(None, ge=1, le=24, description="备用金月数")


class ActionStatusUpdate(BaseModel):
    status: str = Field(..., description="accepted/rejected/executed")
    feedback: Optional[str] = Field(None, description="用户反馈")
    actual_return: Optional[float] = Field(None, description="实际收益")


@router.get("/dashboard")
async def dashboard(user_id: str = Query("default"), force_refresh: bool = Query(False)):
    """获取全账户资产健康度诊断仪表盘。"""
    return get_health_v2_dashboard(user_id=user_id, force_refresh=force_refresh)


@router.post("/recalculate")
async def recalculate(user_id: str = Query("default")):
    """强制重新计算健康度诊断（清除缓存）。"""
    return recalculate_health_v2(user_id=user_id)


@router.get("/profile")
async def profile(user_id: str = Query("default")):
    """获取或创建用户投资画像。"""
    return get_or_create_profile(user_id=user_id)


@router.put("/profile")
async def update_profile_endpoint(payload: ProfileUpdate, user_id: str = Query("default")):
    """更新用户投资画像。"""
    return update_profile(
        user_id=user_id,
        risk_level=payload.risk_level,
        target_date=payload.target_date,
        target_pots=payload.target_pots,
        monthly_investable=payload.monthly_investable,
        emergency_months=payload.emergency_months,
    )


@router.get("/profile/target-pots-defaults")
async def target_pots_defaults():
    """获取按风险等级的默认四笔钱配比。"""
    return DEFAULT_TARGET_POTS


@router.get("/history")
async def history(user_id: str = Query("default"), days: int = Query(30, ge=7, le=365)):
    """获取健康分历史趋势。"""
    return {
        "days": days,
        "scores": get_health_v2_history(user_id=user_id, days=days),
    }


@router.post("/actions/{action_id}/status")
async def action_status(action_id: str, payload: ActionStatusUpdate, user_id: str = Query("default")):
    """更新行动项状态（接受/执行/忽略/反馈）。"""
    ok = update_action_status(
        action_id=action_id,
        user_id=user_id,
        status=payload.status,
        feedback=payload.feedback,
        actual_return=payload.actual_return,
    )
    return {"ok": ok, "action_id": action_id, "status": payload.status}
