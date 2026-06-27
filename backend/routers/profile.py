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
from db import (
    create_goal_bucket,
    delete_goal_bucket,
    get_goal_bucket,
    get_goal_bucket_summary,
    get_user_profile,
    list_goal_buckets,
    update_goal_bucket,
    update_user_profile,
    sync_bucket_from_portfolio,
)

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
    # 个人财务画像 2.0
    monthly_income: float | None = None
    monthly_expense: float | None = None
    monthly_surplus: float | None = None
    emergency_fund_months: float | None = None
    target_equity_ratio: float | None = None
    max_single_position_pct: float | None = None
    primary_goal: str | None = None
    fund_usage: str | None = None
    liquidity_needs: str | None = None
    liabilities_summary: str | None = None
    behavior_biases: list | None = None


class GoalBucketRequest(BaseModel):
    name: str | None = None
    bucket_type: str | None = None
    target_amount: float | None = None
    current_amount: float | None = None
    target_ratio: float | None = None
    risk_level: str | None = None
    liquidity_days: int | None = None
    priority: int | None = None
    notes: str | None = None
    status: str | None = None


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
    # JSON 字段解析为列表便于前端使用
    for key in ("focus_assets", "behavior_biases", "positive_patterns", "negative_patterns"):
        value = profile.get(key, "")
        if not value:
            continue
        try:
            profile[key] = json.loads(value) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            profile[key] = []
    return profile


@router.put("/api/profile")
def api_update_profile(req: ProfileUpdateRequest):
    """手动修改画像字段。"""
    fields = {k: v for k, v in _dump(req).items() if v is not None}
    if not fields:
        return {"ok": False, "msg": "无更新字段"}
    # 列表字段转 JSON
    if "focus_assets" in fields and isinstance(fields["focus_assets"], list):
        fields["focus_assets"] = json.dumps(fields["focus_assets"], ensure_ascii=False)
    if "behavior_biases" in fields and isinstance(fields["behavior_biases"], list):
        fields["behavior_biases"] = json.dumps(fields["behavior_biases"], ensure_ascii=False)
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


@router.get("/api/profile/buckets")
def api_list_goal_buckets():
    """列出目标账户 / 资金桶。"""
    return {
        "items": list_goal_buckets("default"),
        "summary": get_goal_bucket_summary("default"),
    }


@router.post("/api/profile/buckets")
def api_create_goal_bucket(req: GoalBucketRequest):
    """创建资金桶。"""
    fields = {k: v for k, v in _dump(req).items() if v is not None}
    if not fields.get("name") or not fields.get("bucket_type"):
        raise HTTPException(status_code=400, detail="资金桶名称和类型必填")
    try:
        bucket_id = create_goal_bucket(
            name=fields["name"],
            bucket_type=fields["bucket_type"],
            target_amount=fields.get("target_amount", 0),
            current_amount=fields.get("current_amount", 0),
            target_ratio=fields.get("target_ratio"),
            risk_level=fields.get("risk_level", ""),
            liquidity_days=fields.get("liquidity_days"),
            priority=fields.get("priority", 3),
            notes=fields.get("notes", ""),
        )
        return {"ok": True, "id": bucket_id, "item": get_goal_bucket(bucket_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建资金桶失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/profile/buckets/{bucket_id}")
def api_update_goal_bucket(bucket_id: int, req: GoalBucketRequest):
    """更新资金桶。"""
    fields = {k: v for k, v in _dump(req).items() if v is not None}
    if not fields:
        return {"ok": False, "msg": "无更新字段"}
    try:
        ok = update_goal_bucket(bucket_id, **fields)
        if not ok:
            raise HTTPException(status_code=404, detail="资金桶不存在")
        return {"ok": True, "item": get_goal_bucket(bucket_id)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"更新资金桶失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/profile/buckets/{bucket_id}")
def api_delete_goal_bucket(bucket_id: int):
    """删除资金桶。"""
    ok = delete_goal_bucket(bucket_id)
    if not ok:
        raise HTTPException(status_code=404, detail="资金桶不存在")
    return {"ok": True}


@router.post("/api/profile/buckets/sync")
def api_sync_buckets():
    """根据实际持仓自动同步资金桶 current_amount。"""
    from db.portfolio import get_portfolio_summary, get_cash_balance
    summary = get_portfolio_summary()
    total_assets = summary.get("total_assets", 0)
    cash = get_cash_balance()
    result = sync_bucket_from_portfolio("default", total_assets, cash)
    return result
