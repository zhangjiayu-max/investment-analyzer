"""
数据质量报告 API — 数据缺口分析、新鲜度、一致性检查。

端点:
  GET /api/data-quality/full        → 完整报告
  GET /api/data-quality/gaps        → 数据缺口分析
  GET /api/data-quality/freshness   → 数据新鲜度
  GET /api/data-quality/consistency → 数据一致性检查
"""

from fastapi import APIRouter

from services.data_quality_report import (
    data_gap_analysis,
    data_freshness_report,
    data_consistency_check,
    generate_full_report,
)

router = APIRouter()


@router.get("/api/data-quality/full")
async def get_full_report():
    """完整数据质量报告：缺口 + 新鲜度 + 一致性"""
    return generate_full_report()


@router.get("/api/data-quality/gaps")
async def get_data_gaps():
    """数据缺口分析：各表字段 NULL 率和数据缺口"""
    return {"data_gaps": data_gap_analysis()}


@router.get("/api/data-quality/freshness")
async def get_freshness():
    """数据新鲜度报告：各数据源最新更新时间和状态"""
    return data_freshness_report()


@router.get("/api/data-quality/consistency")
async def get_consistency():
    """数据一致性检查：跨表数据校验"""
    return {"consistency": data_consistency_check()}
