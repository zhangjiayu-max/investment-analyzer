"""龙虎榜机构席位路由 — /api/market/dragon-tiger/*

P1-8：机构动向信号补充（北向资金叫停 + 融资融券滞后，龙虎榜为机构短期动向信号源）。

F-akshare（2026-07-23）：async def → def，让 FastAPI 自动放到线程池执行。
原 async def 直接同步调用 akshare，akshare 卡死时阻塞事件循环，
导致所有请求（含静态文件 /app）排队无响应。改为 def 后由 FastAPI 的
AnyIO 线程池承载，不阻塞事件循环。
"""
import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dragon-tiger"])


@router.get("/api/market/dragon-tiger")
def get_dragon_tiger(
    days: int = Query(5, ge=1, le=60, description="回看天数（1-60）"),
):
    """获取近N天龙虎榜数据（含机构/游资席位区分）。"""
    from services.market.dragon_tiger import fetch_dragon_tiger, _is_enabled
    if not _is_enabled():
        return {"items": [], "disabled": True, "reason": "market.dragon_tiger_enabled=false"}
    try:
        items = fetch_dragon_tiger(days=days)
        return {"items": items, "total": len(items)}
    except Exception as e:
        logger.error(f"获取龙虎榜数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取龙虎榜数据失败: {e}")


@router.get("/api/market/dragon-tiger/signals")
def get_dragon_tiger_signals_api(
    days: int = Query(5, ge=1, le=60, description="回看天数（1-60）"),
):
    """获取龙虎榜信号汇总（机构净买/卖 TOP10 + 活跃个股 + 机构参与率）。"""
    from services.market.dragon_tiger import get_dragon_tiger_signals, _is_enabled
    if not _is_enabled():
        return {
            "top_institutional_buys": [],
            "top_institutional_sells": [],
            "active_stocks": [],
            "institutional_active_rate": 0.0,
            "signal_date": "",
            "summary": "龙虎榜开关已关闭",
            "total_count": 0,
            "disabled": True,
        }
    try:
        return get_dragon_tiger_signals(days=days)
    except Exception as e:
        logger.error(f"获取龙虎榜信号汇总失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取龙虎榜信号汇总失败: {e}")


@router.get("/api/market/dragon-tiger/{stock_code}")
def get_stock_dragon_tiger(
    stock_code: str,
    days: int = Query(30, ge=1, le=365, description="回看天数（1-365）"),
):
    """查询单只个股的龙虎榜历史。"""
    from services.market.dragon_tiger import get_dragon_tiger_for_stock, _is_enabled
    if not _is_enabled():
        return {"disabled": True, "reason": "market.dragon_tiger_enabled=false"}
    if not stock_code:
        raise HTTPException(status_code=400, detail="stock_code 不能为空")
    try:
        result = get_dragon_tiger_for_stock(stock_code, days=days)
        if result is None:
            return {"code": stock_code, "records": [], "appear_count": 0, "total_net_buy_sum": 0.0}
        return result
    except Exception as e:
        logger.error(f"查询个股 {stock_code} 龙虎榜失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询个股龙虎榜失败: {e}")
