"""理财决策中枢路由 — /api/decisions/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    get_decision,
    list_due_decision_reviews,
    list_decisions,
    list_today_decisions,
    record_decision_review,
    update_decision_action_status,
    update_decision_status,
)

router = APIRouter(tags=["decisions"])


class DecisionStatusRequest(BaseModel):
    status: str
    user_note: str = ""


class DecisionReviewRequest(BaseModel):
    outcome: str
    result_note: str = ""
    profit_change: float | None = None
    lesson: str = ""


@router.get("/api/decisions")
async def list_decisions_api(status: str = "", limit: int = 50):
    """列出理财决策档案。"""
    return {"items": list_decisions(status=status or None, limit=limit)}


@router.get("/api/decisions/reviews/due")
async def list_due_decision_reviews_api(limit: int = 20):
    """列出到期需要复盘的决策。"""
    return {"items": list_due_decision_reviews(limit=limit)}


@router.get("/api/decisions/today")
async def list_today_decisions_api(limit: int = 20):
    """列出今日行动。"""
    return {"items": list_today_decisions(limit=limit)}


@router.get("/api/decisions/{decision_id}")
async def get_decision_api(decision_id: int):
    """获取单条决策档案。"""
    item = get_decision(decision_id)
    if not item:
        raise HTTPException(404, "决策不存在")
    return item


@router.put("/api/decisions/{decision_id}/status")
async def update_decision_status_api(decision_id: int, req: DecisionStatusRequest):
    """更新决策状态。"""
    ok = update_decision_status(decision_id, req.status, req.user_note)
    if not ok:
        raise HTTPException(400, "无效状态或决策不存在")
    item = get_decision(decision_id)
    return {"ok": True, "item": item}


@router.post("/api/decisions/{decision_id}/review")
async def record_decision_review_api(decision_id: int, req: DecisionReviewRequest):
    """提交决策复盘。"""
    review_id = record_decision_review(
        decision_id,
        outcome=req.outcome,
        result_note=req.result_note,
        profit_change=req.profit_change,
        lesson=req.lesson,
    )
    if not review_id:
        raise HTTPException(400, "复盘结果无效或决策不存在")
    return {"ok": True, "id": review_id, "item": get_decision(decision_id)}


@router.post("/api/decisions/{decision_id}/actions/{action_id}/complete")
async def complete_decision_action_api(decision_id: int, action_id: int):
    """完成决策行动项。"""
    item = get_decision(decision_id)
    if not item:
        raise HTTPException(404, "决策不存在")
    action_ids = {a["id"] for a in item.get("actions", [])}
    if action_id not in action_ids:
        raise HTTPException(404, "行动项不存在")
    ok = update_decision_action_status(action_id, "done")
    if not ok:
        raise HTTPException(400, "行动项状态更新失败")
    return {"ok": True, "item": get_decision(decision_id)}
