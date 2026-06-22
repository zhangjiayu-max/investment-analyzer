"""主题机会引擎路由 — /api/opportunities/*"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    add_to_watchlist,
    create_decision_from_opportunity,
    get_opportunity,
    list_opportunities,
    mark_opportunity_bought,
    update_opportunity_status,
)
from opportunity_engine import scan_daily_opportunities

router = APIRouter(tags=["opportunities"])


class DailyScanRequest(BaseModel):
    mode: str = "short_term"
    max_items: int = 8
    force_refresh: bool = False
    news_items: list[dict] | None = None


class MarkBoughtRequest(BaseModel):
    fund_code: str
    amount: float = 0
    transaction_id: int | None = None


@router.get("/api/opportunities/today")
async def today_opportunities_api(limit: int = 8):
    """获取今日主题机会；若没有缓存则基于当前热点扫描。"""
    today = datetime.now().strftime("%Y-%m-%d")
    items = list_opportunities(trade_date=today, limit=limit)
    if items:
        return {"date": today, "items": items, "source": "cache"}
    return await daily_scan_api(DailyScanRequest(max_items=limit, force_refresh=True))


@router.post("/api/opportunities/daily-scan")
async def daily_scan_api(req: DailyScanRequest):
    """生成今日主题机会卡。"""
    news_items = req.news_items
    if news_items is None:
        try:
            from routers.dashboard import get_hot_topics
            hot = await get_hot_topics()
            news_items = hot.get("news", [])
        except Exception:
            news_items = []
    return scan_daily_opportunities(
        news_items=news_items,
        max_items=req.max_items,
        force_refresh=req.force_refresh,
    )


@router.post("/api/opportunities/{opportunity_id}/create-decision")
async def opportunity_create_decision_api(opportunity_id: int):
    """把机会卡保存为决策草案。"""
    try:
        decision_id = create_decision_from_opportunity(opportunity_id)
    except ValueError:
        raise HTTPException(404, "机会不存在")
    return {"ok": True, "decision_id": decision_id}


@router.post("/api/opportunities/{opportunity_id}/watch")
async def opportunity_watch_api(opportunity_id: int):
    """把机会的首个匹配基金加入关注列表。"""
    item = get_opportunity(opportunity_id)
    if not item:
        raise HTTPException(404, "机会不存在")
    fund = (item.get("matched_funds") or [{}])[0]
    if not fund.get("fund_code"):
        raise HTTPException(400, "机会没有可关注基金")
    try:
        watch_id = add_to_watchlist(
            fund_code=fund["fund_code"],
            fund_name=fund.get("fund_name") or fund["fund_code"],
            index_name=fund.get("index_name"),
            notes=f"来自主题机会：{item.get('theme')}；{item.get('summary')}",
            priority=1,
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            update_opportunity_status(opportunity_id, "watching")
            return {"ok": True, "dedup": True}
        raise HTTPException(500, str(e))
    update_opportunity_status(opportunity_id, "watching")
    return {"ok": True, "watch_id": watch_id}


@router.post("/api/opportunities/{opportunity_id}/mark-bought")
async def opportunity_mark_bought_api(opportunity_id: int, req: MarkBoughtRequest):
    """标记机会已买入并开始跟踪。"""
    try:
        track_id = mark_opportunity_bought(
            opportunity_id=opportunity_id,
            fund_code=req.fund_code,
            amount=req.amount,
            transaction_id=req.transaction_id,
        )
    except ValueError:
        raise HTTPException(404, "机会不存在")
    return {"ok": True, "track_id": track_id}
