"""收益归因分析 API 路由 — /api/analysis/attribution/*

提供 Brinson 三因素归因报告、按品类归因、Top 贡献/拖累查询。
"""

from fastapi import APIRouter, HTTPException, Query

from services.attribution import (
    get_attribution_report,
    get_category_attribution,
    get_contributors,
)

router = APIRouter(tags=["attribution"])


@router.get("/api/analysis/attribution/report")
def api_attribution_report(
    start_date: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
):
    """获取 Brinson 收益归因报告。"""
    try:
        return get_attribution_report("default", start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/analysis/attribution/by_category")
def api_attribution_by_category(
    period: str = Query(..., description="期间，如 2026-H1 / 2026-Q1 / 2026 / 2026-03"),
):
    """按品类获取归因。"""
    try:
        categories = get_category_attribution("default", period)
        return {"period": period, "categories": categories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/analysis/attribution/contributors")
def api_attribution_contributors(
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    order: str = Query("desc", pattern=r"^(desc|asc)$", description="desc=Top贡献, asc=Top拖累"),
):
    """获取 Top 贡献/拖累持仓。order=desc 贡献最大，asc 拖累最大。"""
    try:
        items = get_contributors("default", limit, order)
        return {"order": order, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
