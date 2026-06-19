"""用户画像路由 — /api/profile/*

KYC 理财画像的读写入口。
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.kyc import (
    get_kyc_questionnaire, get_kyc_profile, submit_kyc_answers,
)
from db import get_user_profile, update_user_profile

logger = logging.getLogger(__name__)

router = APIRouter(tags=["profile"])


class KycSubmitRequest(BaseModel):
    answers: dict = {}       # {dimension: value}
    source: str = "questionnaire"


class ProfileUpdateRequest(BaseModel):
    risk_tolerance: str | None = None
    investment_horizon: str | None = None
    capital_scale: str | None = None
    investment_experience: str | None = None
    loss_tolerance: str | None = None
    focus_assets: list | None = None
    # 通用偏好维度（兼容旧字段）
    preferences_json: str | None = None
    feedback_summary: str | None = None


def _dump(model) -> dict:
    """pydantic v1/v2 兼容取值。"""
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


@router.get("/api/profile/kyc")
def api_get_kyc_questionnaire():
    """返回问卷题库 + 当前 KYC 画像。"""
    return {
        "questionnaire": get_kyc_questionnaire(),
        "profile": get_kyc_profile(),
    }


@router.post("/api/profile/kyc/submit")
def api_submit_kyc(req: KycSubmitRequest):
    """提交 KYC 问卷答案。"""
    try:
        profile = submit_kyc_answers("default", req.answers, req.source)
        return {"ok": True, "profile": profile}
    except Exception as e:
        logger.error(f"提交 KYC 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/profile")
def api_get_profile():
    """获取完整用户画像（含偏好 + KYC 维度）。"""
    profile = get_user_profile("default") or {}
    # focus_assets 解析为列表便于前端使用
    fa = profile.get("focus_assets", "")
    if fa:
        try:
            profile["focus_assets"] = json.loads(fa) if isinstance(fa, str) else fa
        except (json.JSONDecodeError, TypeError):
            profile["focus_assets"] = []
    return profile


@router.put("/api/profile")
def api_update_profile(req: ProfileUpdateRequest):
    """手动修改画像字段。"""
    fields = {k: v for k, v in _dump(req).items() if v is not None}
    if not fields:
        return {"ok": False, "msg": "无更新字段"}
    # focus_assets 列表转 JSON
    if "focus_assets" in fields and isinstance(fields["focus_assets"], list):
        fields["focus_assets"] = json.dumps(fields["focus_assets"], ensure_ascii=False)
    try:
        update_user_profile("default", **fields)
        return {"ok": True, "profile": get_user_profile("default")}
    except Exception as e:
        logger.error(f"更新画像失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/profile/alerts")
def api_get_alerts():
    """获取专属理财顾问的主动关怀预警（持仓回撤超承受度 / 估值极端区间）。"""
    from agent.wealth_advisor import generate_proactive_alerts
    try:
        alerts = generate_proactive_alerts("default")
        return {"alerts": alerts, "count": len(alerts)}
    except Exception as e:
        logger.error(f"获取预警失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
