"""组合智能路由 — /api/analysis/portfolio-intelligence/*

三个端点：
- GET /risk-metrics      组合风险度量（波动率/VaR/CVaR/最大回撤/夏普/Sortino）
- GET /health-report     组合7维体检报告（聚合版）
- GET /master-matrix     大师矩阵组合版
"""
import logging

from fastapi import APIRouter, HTTPException

from services.portfolio_intelligence import (
    calculate_portfolio_risk_metrics,
    calculate_portfolio_health_report,
    build_portfolio_master_matrix,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/portfolio-intelligence", tags=["analysis-portfolio-intelligence"])


@router.get("/risk-metrics")
async def get_portfolio_risk_metrics(days: int = 365):
    """组合风险度量。

    参数：
    - days: 回看天数（默认365天）
    """
    try:
        result = calculate_portfolio_risk_metrics(days=days)
        return result
    except Exception as e:
        logger.error(f"[portfolio-intel] 风险度量失败: {e}", exc_info=True)
        raise HTTPException(500, f"风险度量失败: {str(e)}")


@router.get("/health-report")
async def get_portfolio_health_report(force_refresh: bool = False):
    """组合7维体检报告（聚合版）。"""
    try:
        result = calculate_portfolio_health_report(force_refresh=force_refresh)

        # 同时构建大师矩阵组合版
        if result.get("data_status") == "ok":
            master_matrix = build_portfolio_master_matrix(
                result.get("portfolio_report", {}),
                result.get("risk_metrics", {}),
                result.get("holding_reports", []),
            )
            result["master_perspectives"] = master_matrix

        return result
    except Exception as e:
        logger.error(f"[portfolio-intel] 组合体检失败: {e}", exc_info=True)
        raise HTTPException(500, f"组合体检失败: {str(e)}")


@router.get("/master-matrix")
async def get_portfolio_master_matrix():
    """大师矩阵组合版。"""
    try:
        health = calculate_portfolio_health_report()
        if health.get("data_status") != "ok":
            return {"masters": [], "consensus": {}, "degraded_reason": health.get("degraded_reason", "")}
        master_matrix = build_portfolio_master_matrix(
            health.get("portfolio_report", {}),
            health.get("risk_metrics", {}),
            health.get("holding_reports", []),
        )
        return master_matrix
    except Exception as e:
        logger.error(f"[portfolio-intel] 大师组合矩阵失败: {e}", exc_info=True)
        raise HTTPException(500, f"大师组合矩阵失败: {str(e)}")
