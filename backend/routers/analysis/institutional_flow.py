"""机构动向 API 端点（P0 新增）。

北向资金实时数据 2024.8 后已停止公布，改用融资余额作为杠杆资金动向主信号。
"""
from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/api/institutional-flow/margin")
async def get_margin_flow(
    days: int = Query(30, ge=1, le=365, description="查询天数"),
):
    """融资余额变化序列（机构杠杆资金动向主信号）。"""
    from services.institutional_flow import get_margin_balance
    return get_margin_balance(days=days)


@router.get("/api/institutional-flow/summary")
async def get_institutional_flow_summary_api():
    """机构动向摘要（TickerBar 用，轻量级）。"""
    from services.institutional_flow import get_institutional_flow_summary
    return get_institutional_flow_summary()


@router.get("/api/institutional-flow/signal")
async def get_institutional_flow_signal_api():
    """机构动向共振信号（guardrail 用）。"""
    from services.institutional_flow import get_institutional_flow_signal
    return get_institutional_flow_signal()
