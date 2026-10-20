"""家庭统一风险快照、投前比较及真实执行复查。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from db.decision_risk import (save_risk_policy, save_decision_review, list_decision_reviews,
                              link_decision_execution, review_decision_outcome)
from services.portfolio.decision_guardrails import build_decision_snapshot, evaluate_add_decision

router=APIRouter(prefix='/api/portfolio/decision-risk',tags=['portfolio-risk'])


class PolicyRequest(BaseModel):
    policy: dict
    expected_version: int = Field(ge=0)


class PreviewRequest(BaseModel):
    fund_code: str = Field(min_length=1,max_length=32)
    amount: float = Field(ge=0,allow_inf_nan=False)


class ReviewRequest(PreviewRequest):
    snapshot_id: str
    horizon_days: int = Field(default=30,ge=1,le=365)


class ExecutionRequest(BaseModel):
    transaction_id: int = Field(gt=0)


@router.get('/snapshot')
def risk_snapshot():
    return build_decision_snapshot()


@router.put('/policy')
def update_policy(req: PolicyRequest):
    try:
        return save_risk_policy(req.policy,expected_version=req.expected_version)
    except ValueError as exc:
        raise HTTPException(409 if '已经更新' in str(exc) else 400,str(exc)) from exc


@router.post('/preview')
def preview(req: PreviewRequest):
    return evaluate_add_decision(req.fund_code,req.amount)


@router.get('/reviews')
def reviews():
    return {'reviews':list_decision_reviews()}


@router.post('/reviews')
def save_review(req: ReviewRequest):
    # 不信任浏览器金额和状态；重新读取事实，过期/变化必须重新预览。
    ctx=build_decision_snapshot()
    if ctx['snapshot_id']!=req.snapshot_id:
        raise HTTPException(409,'持仓、现金或风险政策已经变化，请重新检查')
    result=evaluate_add_decision(req.fund_code,req.amount,context=ctx)
    try:
        return {'ok':True,'id':save_decision_review(result,horizon_days=req.horizon_days),'decision':result}
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@router.post('/reviews/{review_id}/execution')
def link_execution(review_id:int,req:ExecutionRequest):
    try:
        return {'ok':True,'execution':link_decision_execution(review_id,req.transaction_id)}
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@router.post('/reviews/{review_id}/outcome')
def review_outcome(review_id:int):
    try:
        return review_decision_outcome(review_id)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
