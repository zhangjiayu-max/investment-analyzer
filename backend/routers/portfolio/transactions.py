"""交易记录、交易标签、审计日志"""
import logging
from datetime import datetime as dt

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    create_transaction, list_transactions, confirm_transaction,
    settle_transaction, delete_transaction,
    add_transaction_tag, remove_transaction_tag, get_transaction_tags,
    list_holdings, get_holding,
    create_async_task, update_async_task,
)
from db.portfolio import get_holding_by_fund
from mcp.trading_calendar import expected_confirm_date
from models.portfolio import (
    CreateTransactionRequest, ConfirmTransactionRequest, TagRequest,
)
from state import track_agent as _track_agent, untrack_agent as _untrack_agent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio-transactions"])


# ── 交易标签 API ──────────────────────────────────────────

@router.post("/api/portfolio/transactions/{tx_id}/tags")
async def add_transaction_tag_api(tx_id: int, req: TagRequest):
    """给交易记录添加标签。"""
    tag_id = add_transaction_tag(tx_id, req.tag)
    return {"ok": True, "tag_id": tag_id}


@router.delete("/api/portfolio/transactions/{tx_id}/tags/{tag}")
async def remove_transaction_tag_api(tx_id: int, tag: str):
    """移除交易记录的标签。"""
    if not remove_transaction_tag(tx_id, tag):
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


@router.get("/api/portfolio/transactions/{tx_id}/tags")
async def get_transaction_tags_api(tx_id: int):
    """获取交易记录的所有标签。"""
    return {"tags": get_transaction_tags(tx_id)}



@router.get("/api/portfolio/pending-transactions")
async def list_pending_transactions_api():
    """获取所有待确认交易（包括没有 holding_id 的新建买入）。"""
    txs = list_transactions(status="pending", limit=200, include_system=False)
    # 为交易补充基金名称
    for tx in txs:
        if not tx.get("fund_name"):
            # 从持仓表查基金名称
            fund_code = tx.get("fund_code", "")
            if fund_code:
                from db.portfolio import get_holding_by_fund
                h = get_holding_by_fund(fund_code)
                if h:
                    tx["fund_name"] = h.get("fund_name", fund_code)
                else:
                    tx["fund_name"] = fund_code
    return {"transactions": txs}


@router.get("/api/portfolio/audit-log")
async def get_audit_log(fund_code: str = None, tx_id: int = None, limit: int = 50):
    """获取交易操作审计日志。"""
    from db._conn import _get_conn
    conn = _get_conn()
    conn.row_factory = __import__('sqlite3').Row
    conditions = []
    params = []
    if fund_code:
        conditions.append("fund_code = ?")
        params.append(fund_code)
    if tx_id:
        conditions.append("tx_id = ?")
        params.append(tx_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM portfolio_tx_audit_log {where} ORDER BY id DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    conn.close()

# ── 交易记录 CRUD ──────────────────────────────────────────

@router.get("/api/portfolio/{holding_id}/transactions")
async def list_transactions_api(holding_id: int, limit: int = 100):
    """获取持仓的交易记录。"""
    return {"transactions": list_transactions(holding_id=holding_id, limit=limit)}


@router.post("/api/portfolio/transactions")
async def create_transaction_api(req: CreateTransactionRequest):
    """新增交易记录。"""
    # 自动计算 T+1 确认日
    expected_confirm = None
    if req.status == "pending" and req.transaction_date:
        try:
            from datetime import datetime as dt
            d = dt.strptime(req.transaction_date, "%Y-%m-%d").date()
            expected_confirm = str(expected_confirm_date(d, req.transaction_time))
        except (ValueError, TypeError):
            pass

    tx_id = create_transaction(
        fund_code=req.fund_code, transaction_type=req.transaction_type,
        amount=req.amount, transaction_date=req.transaction_date,
        shares=req.shares, price=req.price,
        holding_id=req.holding_id, notes=req.notes,
        status=req.status, submitted_shares=req.submitted_shares,
        submitted_amount=req.submitted_amount,
        transaction_time=req.transaction_time,
        expected_confirm_date=expected_confirm,
    )


@router.post("/api/portfolio/transactions/{tx_id}/confirm")
async def confirm_transaction_api(tx_id: int, req: ConfirmTransactionRequest):
    """确认交易：填入 T+1 实际净值，计算实际份额/金额。"""
    ok = confirm_transaction(tx_id, req.confirmed_price,
                             confirmed_shares=req.confirmed_shares,
                             confirmed_amount=req.confirmed_amount,
                             target_fund_code=req.target_fund_code,
                             target_fund_name=req.target_fund_name,
                             fee=req.fee)
    if not ok:
        raise HTTPException(404, "交易记录不存在")
    return {"ok": True}


@router.post("/api/portfolio/transactions/{tx_id}/settle")
async def settle_transaction_api(tx_id: int):
    """标记卖出交易已到账。"""
    ok = settle_transaction(tx_id)
    if not ok:
        raise HTTPException(400, "只能标记已确认的卖出交易为已到账")
    return {"ok": True}


@router.delete("/api/portfolio/transactions/{tx_id}")
async def delete_transaction_api(tx_id: int):
    """撤销 pending 状态的交易记录。"""
    ok = delete_transaction(tx_id)
    if not ok:
        raise HTTPException(400, "只能撤销待确认（pending）状态的交易")
    return {"ok": True}


