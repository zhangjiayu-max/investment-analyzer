"""理财决策中枢路由 — /api/decisions/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    build_decision_precheck,
    create_chat_decision_draft,
    create_decision,
    create_decision_action,
    get_decision,
    get_messages,
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


class ChatDecisionDraftRequest(BaseModel):
    conversation_id: int
    assistant_message_id: int
    user_message_id: int | None = None
    target_type: str = "portfolio"
    target_code: str = ""
    target_name: str = ""
    review_days: int = 30


class CreateDecisionRequest(BaseModel):
    """直接创建决策（不通过对话）。"""
    decision_type: str = "watch"  # add/reduce/watch/rebalance/hold/sell
    target_type: str = "fund"
    target_code: str = ""
    target_name: str = ""
    summary: str
    rationale: str = ""
    review_days: int = 30
    source_type: str = "manual"


@router.post("/api/decisions/create")
async def create_decision_api(req: CreateDecisionRequest):
    """直接创建决策（不通过对话），用于配置偏离等场景一键创建。"""
    from datetime import datetime, timedelta
    review_at = (datetime.now() + timedelta(days=req.review_days)).strftime("%Y-%m-%d")
    decision_id = create_decision(
        source_type=req.source_type,
        decision_type=req.decision_type,
        target_type=req.target_type,
        target_code=req.target_code,
        target_name=req.target_name,
        summary=req.summary,
        rationale=req.rationale,
        review_at=review_at,
    )
    # 尝试预检查
    try:
        precheck = build_decision_precheck(decision_id)
    except Exception:
        precheck = {"exists": True, "error": "precheck_failed"}
    return {"ok": True, "id": decision_id, "item": get_decision(decision_id), "precheck": precheck}


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


@router.post("/api/decisions/from-chat")
async def create_decision_from_chat_api(req: ChatDecisionDraftRequest):
    """把 AI 对话回复保存为理财决策草案。"""
    messages = get_messages(req.conversation_id, limit=200)
    assistant_msg = next((m for m in messages if m["id"] == req.assistant_message_id), None)
    if not assistant_msg or assistant_msg.get("role") != "assistant":
        raise HTTPException(404, "助手消息不存在")

    user_msg = None
    if req.user_message_id:
        user_msg = next((m for m in messages if m["id"] == req.user_message_id), None)
    if not user_msg:
        assistant_index = next(
            (i for i, m in enumerate(messages) if m["id"] == req.assistant_message_id),
            -1,
        )
        for msg in reversed(messages[:assistant_index]):
            if msg.get("role") == "user":
                user_msg = msg
                break

    decision_id = create_chat_decision_draft(
        conversation_id=req.conversation_id,
        assistant_message_id=req.assistant_message_id,
        user_message_id=user_msg.get("id") if user_msg else req.user_message_id,
        assistant_content=assistant_msg.get("content") or "",
        user_query=user_msg.get("content") if user_msg else "",
        target_type=req.target_type,
        target_code=req.target_code,
        target_name=req.target_name,
        review_days=req.review_days,
    )
    return {"ok": True, "id": decision_id, "item": get_decision(decision_id)}


@router.get("/api/decisions/{decision_id}")
async def get_decision_api(decision_id: int):
    """获取单条决策档案。"""
    item = get_decision(decision_id)
    if not item:
        raise HTTPException(404, "决策不存在")
    return item


@router.get("/api/decisions/{decision_id}/precheck")
async def get_decision_precheck_api(decision_id: int):
    """获取决策执行前检查结果。"""
    result = build_decision_precheck(decision_id)
    if not result.get("exists"):
        raise HTTPException(404, "决策不存在")
    return result


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


# ── 多模型评审 API ──

class PeerReviewRequest(BaseModel):
    reviewer_types: list[str] = ["suitability", "evidence", "counter", "overconfidence"]


@router.post("/api/decisions/{decision_id}/peer-review")
async def trigger_peer_review_api(decision_id: int, req: PeerReviewRequest):
    """触发决策多模型评审。"""
    decision = get_decision(decision_id)
    if not decision:
        raise HTTPException(404, "决策不存在")

    from agent.orchestrator import run_peer_review
    from db import create_peer_review, count_high_risk_reviews, update_decision_status

    results = []
    for rtype in req.reviewer_types:
        review_result = run_peer_review(decision, rtype)
        if review_result:
            review_id = create_peer_review(
                decision_id=decision_id,
                reviewer_type=rtype,
                verdict=review_result["verdict"],
                model_name=review_result.get("model_name", ""),
                prompt_version=review_result.get("prompt_version", ""),
                score_json=review_result.get("score", {}),
                concerns_json=review_result.get("concerns", []),
                suggestions_json=review_result.get("suggestions", []),
            )
            results.append({
                "id": review_id,
                "reviewer_type": rtype,
                **review_result,
            })

    # 如果多个评审都给出高风险结论，自动降级为 deferred
    high_risk_count = count_high_risk_reviews(decision_id)
    if high_risk_count >= 2:
        update_decision_status(decision_id, "deferred", "多模型评审发现高风险，自动降级")

    return {
        "ok": True,
        "reviews": results,
        "high_risk_count": high_risk_count,
        "auto_deferred": high_risk_count >= 2,
    }


@router.get("/api/decisions/{decision_id}/peer-reviews")
async def list_peer_reviews_api(decision_id: int):
    """获取决策的评审列表。"""
    decision = get_decision(decision_id)
    if not decision:
        raise HTTPException(404, "决策不存在")

    from db import list_peer_reviews
    return {"items": list_peer_reviews(decision_id)}
