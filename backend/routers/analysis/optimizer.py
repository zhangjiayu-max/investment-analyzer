"""P1-2 组合优化引擎 API 路由。

  POST /api/analysis/optimizer/frontier         效率前沿（马科维茨）
  POST /api/analysis/optimizer/risk-parity      风险平价分配
  POST /api/analysis/optimizer/black-litterman  黑利特曼模型
  GET  /api/analysis/optimizer/suggestion       综合优化建议
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.portfolio_optimizer import (
    efficient_frontier,
    risk_parity_allocation,
    black_litterman,
    get_optimization_suggestion,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis/optimizer", tags=["analysis-optimizer"])


# ── 请求模型 ──────────────────────────────────────

class FrontierRequest(BaseModel):
    assets: list[str]
    start_date: str | None = None
    end_date: str | None = None
    num_points: int = 50


class RiskParityRequest(BaseModel):
    assets: list[str]
    start_date: str | None = None
    end_date: str | None = None


class BlackLittermanRequest(BaseModel):
    market_weights: dict[str, float]
    views: list[dict] = []
    confidence: float = 0.5


# ── 路由 ──────────────────────────────────────

@router.post("/frontier")
async def frontier_api(req: FrontierRequest):
    """效率前沿：蒙特卡洛生成 num_points 个组合，返回有效前沿、最大夏普、最小方差、当前组合。"""
    if not req.assets or len(req.assets) < 2:
        raise HTTPException(status_code=400, detail="至少需要 2 个资产")
    try:
        result = efficient_frontier(req.assets, req.start_date, req.end_date, req.num_points)
        return {"status": "ok", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[optimizer] 效率前沿计算失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"计算失败: {e}")


@router.post("/risk-parity")
async def risk_parity_api(req: RiskParityRequest):
    """风险平价分配：按波动率倒数分配权重，风险贡献均衡。"""
    if not req.assets:
        raise HTTPException(status_code=400, detail="资产列表不能为空")
    try:
        result = risk_parity_allocation(req.assets, req.start_date, req.end_date)
        return {"status": "ok", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[optimizer] 风险平价计算失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"计算失败: {e}")


@router.post("/black-litterman")
async def black_litterman_api(req: BlackLittermanRequest):
    """黑利特曼模型：融合市场均衡收益与主观观点，返回后验收益与权重。"""
    if not req.market_weights:
        raise HTTPException(status_code=400, detail="市场权重不能为空")
    try:
        result = black_litterman(req.market_weights, req.views, req.confidence)
        return {"status": "ok", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[optimizer] 黑利特曼计算失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"计算失败: {e}")


@router.get("/suggestion")
async def suggestion_api(user_id: str = "default"):
    """综合优化建议：对比当前持仓与最大夏普组合，给出配置缺口与建议文本。"""
    try:
        result = get_optimization_suggestion(user_id)
        return {"status": "ok", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[optimizer] 综合建议生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成失败: {e}")
