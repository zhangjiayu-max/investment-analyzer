"""资金桶联动 API 路由。"""

from fastapi import APIRouter, HTTPException, Body

from db import list_goal_buckets, get_goal_bucket
from services.bucket_engine import (
    sync_holdings_to_bucket, assign_holding_to_bucket,
    generate_allocation_suggestion, transfer_between_buckets,
    get_bucket_with_holdings,
)

router = APIRouter(prefix="/buckets", tags=["buckets"])


@router.post("/{bucket_id}/sync-holdings")
async def sync_bucket_holdings(bucket_id: int):
    result = sync_holdings_to_bucket(bucket_id)
    if result is None:
        raise HTTPException(status_code=404, detail="资金桶不存在")
    return {"ok": True, "linked_count": len(result)}


@router.get("/allocation-suggestion")
async def get_allocation_suggestion():
    suggestions = generate_allocation_suggestion()
    return {"code": 0, "message": "ok", "data": suggestions}


@router.post("/{from_id}/transfer/{to_id}")
async def transfer_bucket(
    from_id: int,
    to_id: int,
    amount: float = Body(...),
):
    success = transfer_between_buckets(from_id, to_id, amount)
    if not success:
        raise HTTPException(status_code=400, detail="调拨失败，请检查资金桶是否存在或余额是否充足")
    return {"ok": True}


@router.put("/holdings/{holding_id}/assign")
async def assign_bucket_to_holding(
    holding_id: int,
    bucket_id: int = Body(...),
):
    success = assign_holding_to_bucket(holding_id, bucket_id)
    if not success:
        raise HTTPException(status_code=400, detail="分配失败")
    return {"ok": True}


@router.get("/{bucket_id}/details")
async def get_bucket_details(bucket_id: int):
    bucket = get_bucket_with_holdings(bucket_id)
    if not bucket:
        raise HTTPException(status_code=404, detail="资金桶不存在")
    return {"code": 0, "message": "ok", "data": bucket}


@router.get("/")
async def list_buckets_with_holdings():
    buckets = list_goal_buckets()
    result = []
    for bucket in buckets:
        details = get_bucket_with_holdings(bucket['id'])
        if details:
            result.append(details)
    return {"code": 0, "message": "ok", "data": result}
