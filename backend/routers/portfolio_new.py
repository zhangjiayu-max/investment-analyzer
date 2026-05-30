"""持仓路由 — /api/portfolio/* (规范化版本)

路径规范：
  - /api/portfolio/holdings          - 持仓列表
  - /api/portfolio/summary           - 持仓摘要
  - /api/portfolio/holding/{id}      - 单个持仓操作
  - /api/portfolio/transactions      - 交易管理
  - /api/portfolio/cash              - 现金管理
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import (
    create_holding, get_holding, list_holdings, update_holding, delete_holding,
    get_portfolio_summary, get_cash_balance, add_cash,
    create_transaction, list_transactions, delete_transaction,
    clear_all_portfolio_data,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class CreateHoldingRequest(BaseModel):
    fund_code: str
    fund_name: str
    account: str = "default"
    shares: float = 0
    cost_price: float = 0


class UpdateHoldingRequest(BaseModel):
    fund_name: Optional[str] = None
    shares: Optional[float] = None
    cost_price: Optional[float] = None
    account: Optional[str] = None


class CreateTransactionRequest(BaseModel):
    holding_id: int
    transaction_type: str  # buy/sell
    shares: float
    price: float
    transaction_date: Optional[str] = None
    notes: Optional[str] = None


class AdjustCashRequest(BaseModel):
    amount: float
    mode: str  # add/subtract
    user_id: str = "小鱼儿"


# ── 持仓 CRUD ──────────────────────────────────────

@router.get("/holdings")
async def list_holdings_api(account: str = None):
    """持仓列表。"""
    params = {}
    if account:
        params["account"] = account
    return {"holdings": list_holdings(**params)}


@router.get("/summary")
async def get_summary(account: str = None):
    """持仓摘要。"""
    params = {}
    if account:
        params["account"] = account
    return get_portfolio_summary(**params)


@router.post("/create")
async def create_holding_api(req: CreateHoldingRequest):
    """创建持仓。"""
    holding_id = create_holding(**req.dict())
    return {"ok": True, "id": holding_id}


@router.get("/holding/{holding_id}")
async def get_holding_api(holding_id: int):
    """获取单个持仓。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    return holding


@router.put("/holding/{holding_id}")
async def update_holding_api(holding_id: int, req: UpdateHoldingRequest):
    """更新持仓。"""
    update_holding(holding_id, **req.dict(exclude_unset=True))
    return {"ok": True}


@router.delete("/holding/{holding_id}")
async def delete_holding_api(holding_id: int):
    """删除持仓。"""
    delete_holding(holding_id)
    return {"ok": True}


# ── 交易管理 ──────────────────────────────────────

@router.get("/holding/{holding_id}/transactions")
async def list_transactions_api(holding_id: int, limit: int = 50):
    """获取持仓的交易记录。"""
    return {"transactions": list_transactions(holding_id, limit)}


@router.post("/transactions")
async def create_transaction_api(req: CreateTransactionRequest):
    """创建交易。"""
    tx_id = create_transaction(**req.dict())
    return {"ok": True, "id": tx_id}


@router.delete("/transactions/{tx_id}")
async def delete_transaction_api(tx_id: int):
    """删除交易。"""
    delete_transaction(tx_id)
    return {"ok": True}


# ── 现金管理 ──────────────────────────────────────

@router.get("/cash")
async def get_cash_api(user_id: str = "小鱼儿"):
    """获取现金余额。"""
    return get_cash_balance(user_id)


@router.post("/cash")
async def adjust_cash_api(req: AdjustCashRequest):
    """调整现金余额。"""
    result = add_cash(req.amount, req.mode, req.user_id)
    return result


# ── 数据管理 ──────────────────────────────────────

@router.post("/clear")
async def clear_data_api():
    """清空所有持仓数据。"""
    clear_all_portfolio_data()
    return {"ok": True}
