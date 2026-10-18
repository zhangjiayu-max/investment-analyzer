"""每月支出记录路由 — /api/expense/*

设计稿：doc/plans/2026-10-14-financial-decision-enhancement.md
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

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
    create_recurring_template,
    get_recurring_template,
    list_recurring_templates,
    update_recurring_template,
    delete_recurring_template,
    list_recurring_bills,
    pay_recurring_bill,
    get_recurring_summary,
)
from db.expense import set_expense_month_complete

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


class ExpenseMonthStatusRequest(BaseModel):
    month: str
    complete: StrictBool


class RecurringTemplateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    category: str
    amount: StrictFloat = Field(gt=0, allow_inf_nan=False)
    frequency: str
    start_month: str
    end_month: str | None = None
    due_day: StrictInt = Field(default=1, ge=1, le=31)
    active: StrictBool = True
    note: str = Field(default="", max_length=2000)


class RecurringTemplateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    amount: StrictFloat | None = Field(default=None, gt=0, allow_inf_nan=False)
    frequency: str | None = None
    start_month: str | None = None
    end_month: str | None = None
    due_day: StrictInt | None = Field(default=None, ge=1, le=31)
    active: StrictBool | None = None
    note: str | None = Field(default=None, max_length=2000)


class RecurringBillPayRequest(BaseModel):
    month: str


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


@router.put("/api/expense/month-status")
def api_month_status(req: ExpenseMonthStatusRequest):
    try:
        set_expense_month_complete(req.month, req.complete)
        return get_cashflow(month=req.month)
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
def api_cashflow(month: str | None = None):
    """统一现金流，包含待付账单、月均预留和应急储备上限。"""
    try:
        return get_cashflow(month=month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/expense/templates")
def api_recurring_templates():
    return {"items": list_recurring_templates()}


@router.post("/api/expense/templates")
def api_create_recurring_template(req: RecurringTemplateCreateRequest):
    try:
        return {"ok": True, "id": create_recurring_template(**req.model_dump())}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/expense/templates/{template_id}")
def api_update_recurring_template(template_id: int, req: RecurringTemplateUpdateRequest):
    fields = req.model_dump(exclude_unset=True)
    try:
        if not update_recurring_template(template_id, **fields):
            raise HTTPException(status_code=404, detail="模板不存在或无字段可更新")
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/expense/templates/{template_id}")
def api_delete_recurring_template(template_id: int):
    if not delete_recurring_template(template_id):
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"ok": True}


@router.get("/api/expense/bills")
def api_recurring_bills(month: str):
    try:
        return {"items": list_recurring_bills(month), "summary": get_recurring_summary(month)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/expense/templates/{template_id}/pay")
def api_pay_recurring_bill(template_id: int, req: RecurringBillPayRequest):
    if get_recurring_template(template_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    try:
        return {"ok": True, "expense_id": pay_recurring_bill(template_id, req.month)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
