"""每月支出记录路由 — /api/expense/*

设计稿：doc/plans/2026-10-14-financial-decision-enhancement.md
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    copy_fixed_expenses,
    create_expense,
    delete_expense,
    get_cashflow,
    get_expense,
    get_expense_categories,
    get_expense_summary,
    get_expense_trend,
    list_expenses,
    update_expense,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["expense"])


class ExpenseCreateRequest(BaseModel):
    month: str                    # YYYY-MM
    category: str
    amount: float
    sub_category: str = ""
    is_fixed: bool = False
    note: str = ""


class ExpenseUpdateRequest(BaseModel):
    month: str | None = None
    category: str | None = None
    amount: float | None = None
    sub_category: str | None = None
    is_fixed: bool | None = None
    note: str | None = None


class CopyFixedRequest(BaseModel):
    from_month: str
    to_month: str


@router.get("/api/expense/categories")
def api_categories():
    """支出分类常量（固定/浮动两组）。"""
    return get_expense_categories()


@router.post("/api/expense")
def api_create(req: ExpenseCreateRequest):
    try:
        new_id = create_expense(
            month=req.month,
            category=req.category,
            amount=req.amount,
            sub_category=req.sub_category,
            is_fixed=req.is_fixed,
            note=req.note,
        )
        return {"ok": True, "id": new_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/expense")
def api_list(month: str | None = None):
    """按月查询明细；month 为空返回全部。"""
    try:
        return {"items": list_expenses(month=month)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/expense/{expense_id}")
def api_update(expense_id: int, req: ExpenseUpdateRequest):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        ok = update_expense(expense_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在或无字段可更新")
    return {"ok": True}


@router.delete("/api/expense/{expense_id}")
def api_delete(expense_id: int):
    if not delete_expense(expense_id):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True}


@router.get("/api/expense/summary")
def api_summary(month: str):
    try:
        return get_expense_summary(month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/expense/trend")
def api_trend(months: int = 6):
    return {"items": get_expense_trend(months=months)}


@router.post("/api/expense/copy-fixed")
def api_copy_fixed(req: CopyFixedRequest):
    """把 from_month 的固定支出复制到 to_month（幂等）。"""
    try:
        copied = copy_fixed_expenses(req.from_month, req.to_month)
        return {"ok": True, "copied": copied}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/expense/cashflow")
def api_cashflow():
    """收支视图：收入 - 支出 = 可投资结余。"""
    return get_cashflow()
