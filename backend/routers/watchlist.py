"""关注列表路由 — /api/watchlist/*

管理看好但未持有的基金，方便择机买入。
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    add_to_watchlist, get_watchlist_item, get_watchlist_by_fund,
    list_watchlist, update_watchlist_item, remove_from_watchlist,
    batch_add_to_watchlist, refresh_watchlist_navs, get_watchlist_summary,
    lookup_fund_info, fetch_fund_nav,
    get_holding_by_fund,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["watchlist"])


def save_watchlist_trigger_candidate(item: dict, user_id: str = "default") -> int:
    """把关注列表触发项保存为建议候选。"""
    from db.decisions import create_candidate_from_structured_recommendation

    fund_code = item.get("fund_code") or ""
    fund_name = item.get("fund_name") or fund_code or "关注标的"
    current_pct = item.get("current_percentile")
    target_pct = item.get("target_percentile")
    summary = item.get("reason") or f"{fund_name} 触发关注条件，可进入分批买入决策"
    return create_candidate_from_structured_recommendation({
        "source_type": "watchlist",
        "source_id": item.get("id"),
        "scenario_type": "watchlist_trigger",
        "action_type": "add",
        "target_type": "fund",
        "target_code": fund_code,
        "target_name": fund_name,
        "summary": summary,
        "reason": item.get("notes") or summary,
        "confidence": "medium",
        "evidence": {
            "current_percentile": current_pct,
            "target_percentile": target_pct,
            "source": item.get("source") or "watchlist",
        },
        "risk": {"notes": ["关注条件触发不等于必须买入，仍需确认资金和仓位"]},
        "source_snapshot": item,
        "dedupe_key": f"watchlist_trigger:{fund_code}",
        "priority": item.get("priority", 0) or 5,
    }, user_id=user_id)


class AddWatchlistRequest(BaseModel):
    fund_code: str
    fund_name: str = ""
    fund_category: str = None
    index_code: str = None
    index_name: str = None
    target_price: float | None = None
    target_percentile: float | None = None
    notes: str = None
    priority: int = 0


class UpdateWatchlistRequest(BaseModel):
    fund_name: str = None
    fund_category: str = None
    index_code: str = None
    index_name: str = None
    target_price: float | None = None
    target_percentile: float | None = None
    notes: str = None
    priority: int = None
    status: str = None


class BatchAddRequest(BaseModel):
    items: list[dict]


@router.get("/api/watchlist")
async def list_watchlist_api(status: str = None, category: str = None):
    """获取关注列表。可选 ?status=watching & ?category=index 筛选。"""
    return {"items": list_watchlist(status=status, category=category)}


@router.get("/api/watchlist/summary")
async def watchlist_summary_api():
    """获取关注列表统计。"""
    return get_watchlist_summary()


@router.get("/api/watchlist/patrol")
async def watchlist_patrol_api():
    """关注列表巡检 — 检查估值，低于目标百分位时提醒建仓。"""
    items = list_watchlist(status="watching")
    if not items:
        return {"alerts": [], "checked": 0, "message": "关注列表为空"}

    from db.valuations import search_indexes_by_keyword, get_latest_valuation

    alerts = []
    checked = 0

    for item in items:
        fund_code = item["fund_code"]
        fund_name = item.get("fund_name", fund_code)
        index_name = item.get("index_name", "")
        target_pct = item.get("target_percentile")

        # 查询当前估值
        current_pct = None
        pe = None
        pb = None
        source = ""

        # 1) 本地 DB
        if index_name:
            try:
                search_term = index_name.replace("指数", "").replace("中证", "").replace("全指", "")
                matches = search_indexes_by_keyword(search_term)
                for m in matches:
                    val = get_latest_valuation(m["index_code"])
                    if val and val.get("percentile") is not None:
                        current_pct = val["percentile"]
                        pe = val.get("current_value")
                        source = "本地"
                        break
            except Exception:
                pass

        # 2) 天天基金兜底
        if current_pct is None and index_name:
            try:
                from mcp.ttfund_client import get_ttfund_client
                client = get_ttfund_client()
                raw = client._invoke("fund_index", {"index_id": index_name, "query_scope": "valuation"})
                if isinstance(raw, dict) and raw.get("success"):
                    v = raw.get("data", {}).get("valuation", {})
                    current_pct = v.get("pe_percentile_10y")
                    pe = v.get("pe_ttm")
                    pb = v.get("pb")
                    source = "天天基金"
            except Exception:
                pass

        checked += 1

        # 判断是否触发提醒
        is_alert = False
        alert_reason = ""

        if current_pct is not None:
            # 更新 watchlist 的当前百分位
            try:
                update_watchlist_item(item["id"], current_percentile=current_pct)
            except Exception:
                pass

            if target_pct is not None and current_pct <= target_pct:
                is_alert = True
                alert_reason = f"当前百分位 {current_pct:.0f}% 已低于目标 {target_pct:.0f}%"
            elif target_pct is None and current_pct <= 20:
                is_alert = True
                alert_reason = f"当前百分位 {current_pct:.0f}% 处于低估区域（≤20%）"

        if is_alert:
            alert_item = {
                "id": item["id"],
                "fund_code": fund_code,
                "fund_name": fund_name,
                "index_name": index_name,
                "current_percentile": current_pct,
                "target_percentile": target_pct,
                "pe": pe,
                "pb": pb,
                "source": source,
                "reason": alert_reason,
                "notes": item.get("notes", ""),
            }
            try:
                alert_item["candidate_id"] = save_watchlist_trigger_candidate(alert_item)
            except Exception as e:
                logger.warning(f"[watchlist] 保存建议候选失败: {e}")
            alerts.append(alert_item)

    return {
        "checked": checked,
        "alerts": alerts,
        "message": f"巡检完成，{checked} 只基金中有 {len(alerts)} 只触发提醒",
    }


@router.get("/api/watchlist/{item_id}")
async def get_watchlist_item_api(item_id: int):
    """获取单条关注记录。"""
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注记录不存在")
    return item


@router.post("/api/watchlist")
async def add_to_watchlist_api(req: AddWatchlistRequest):
    """添加基金到关注列表。自动查询基金信息补充名称。"""
    fund_code = req.fund_code.strip()

    # 自动查询基金信息
    fund_name = req.fund_name
    index_code = req.index_code
    index_name = req.index_name
    fund_category = req.fund_category

    if not fund_name:
        try:
            info = lookup_fund_info(fund_code)
            if info:
                fund_name = info.get("fund_name", fund_code)
                if not fund_category:
                    fund_category = info.get("fund_category")
                if not index_code:
                    tracking = info.get("tracking_index", "")
                    if tracking:
                        index_name = tracking
        except Exception as e:
            logger.warning(f"查询基金信息失败 {fund_code}: {e}")
            fund_name = fund_name or fund_code

    # 查重：已在持仓中时标记为补仓监控
    existing = get_holding_by_fund(fund_code)
    is_existing_holding = existing and (existing.get("shares") or 0) > 0

    # 已持仓的基金自动标记为补仓监控
    notes = req.notes
    if is_existing_holding and not notes:
        profit = existing.get("profit_rate", 0)
        notes = f"补仓监控（当前持仓盈亏 {profit:+.1%}）"

    try:
        item_id = add_to_watchlist(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_category=fund_category,
            index_code=index_code,
            index_name=index_name,
            target_price=req.target_price,
            target_percentile=req.target_percentile,
            notes=notes,
            priority=req.priority,
        )
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            raise HTTPException(400, f"{fund_name}({fund_code}) 已在关注列表中")
        raise HTTPException(500, f"添加失败: {e}")

    return {"ok": True, "id": item_id}


@router.post("/api/watchlist/batch")
async def batch_add_watchlist_api(req: BatchAddRequest):
    """批量添加基金到关注列表。"""
    result = batch_add_to_watchlist(req.items)
    return {"ok": True, **result}


@router.put("/api/watchlist/{item_id}")
async def update_watchlist_item_api(item_id: int, req: UpdateWatchlistRequest):
    """更新关注记录。"""
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "无更新字段")

    if not update_watchlist_item(item_id, **fields):
        raise HTTPException(404, "关注记录不存在")
    return {"ok": True}


@router.delete("/api/watchlist/{item_id}")
async def remove_from_watchlist_api(item_id: int):
    """从关注列表移除。"""
    if not remove_from_watchlist(item_id):
        raise HTTPException(404, "关注记录不存在")
    return {"ok": True}


@router.post("/api/watchlist/refresh-navs")
async def refresh_watchlist_navs_api():
    """批量刷新关注列表基金净值。"""
    results = refresh_watchlist_navs()
    return {"ok": True, "results": results}


@router.post("/api/watchlist/{item_id}/mark-bought")
async def mark_as_bought_api(item_id: int):
    """标记为已买入（从关注列表移到持仓）。"""
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注记录不存在")

    update_watchlist_item(item_id, status="bought")
    return {"ok": True, "message": f"{item.get('fund_name','')} 已标记为已买入"}


@router.post("/api/watchlist/{item_id}/lookup")
async def lookup_and_fill_api(item_id: int):
    """查询基金详细信息并自动填充。"""
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注记录不存在")

    fund_code = item["fund_code"]
    try:
        info = lookup_fund_info(fund_code)
        if not info:
            raise HTTPException(404, f"未找到 {fund_code} 的基金信息")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"查询失败: {e}")

    updates = {}
    if info.get("fund_name") and not item.get("fund_name"):
        updates["fund_name"] = info["fund_name"]
    if info.get("fund_category") and not item.get("fund_category"):
        updates["fund_category"] = info["fund_category"]
    tracking = info.get("tracking_index", "")
    if tracking and not item.get("index_name"):
        updates["index_name"] = tracking

    # 获取最新净值
    try:
        nav_data = fetch_fund_nav(fund_code)
        if nav_data:
            updates["current_nav"] = nav_data.get("nav")
    except Exception:
        pass

    if updates:
        update_watchlist_item(item_id, **updates)

    return {"ok": True, "info": info, "updates": updates}

