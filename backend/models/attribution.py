"""收益归因分析的 Pydantic 请求模型。

供 /api/analysis/attribution/* 接口的请求校验与文档使用。
"""

from pydantic import BaseModel, Field


class AttributionReportRequest(BaseModel):
    """归因报告请求。"""

    start_date: str = Field(..., description="起始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")


class CategoryAttributionRequest(BaseModel):
    """按品类归因请求。"""

    period: str = Field(
        ..., description="期间，如 2026-H1 / 2026-Q1 / 2026 / 2026-03"
    )


class ContributorsRequest(BaseModel):
    """Top 贡献/拖累请求。"""

    limit: int = Field(10, ge=1, le=50, description="返回条数")
    order: str = Field(
        "desc", pattern=r"^(desc|asc)$", description="desc=Top贡献, asc=Top拖累"
    )
