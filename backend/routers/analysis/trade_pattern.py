"""交易行为模式分析 — GET /api/analysis/trade-patterns"""
import logging

from fastapi import APIRouter, Query

from db.portfolio import analyze_trade_patterns

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-trade-patterns"])


@router.get("/api/analysis/trade-patterns")
async def get_trade_patterns_api(user_id: str = Query("default")):
    """获取用户真实交易行为模式分析。

    返回追涨倾向、杀跌倾向、持有耐心、频繁交易度、胜率、最大单笔亏损、交易风格判定。
    """
    data = analyze_trade_patterns(user_id)
    return {"ok": True, "data": data}
