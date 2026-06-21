"""策略沙盒路由 — /api/strategy-sandbox/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from strategy_sandbox import PRESETS, run_backtest

router = APIRouter(tags=["strategy-sandbox"])


class BacktestRequest(BaseModel):
    target_code: str
    target_type: str = "index"  # "index" | "fund"
    strategy: str = "dca"
    initial_cash: float = 10000
    monthly_amount: float = 1000
    days: int = 1095  # 3 年
    # 费率参数
    buy_fee_rate: float = 0.0015   # 申购费率 0.15%
    sell_fee_rate: float = 0.005   # 赎回费率 0.5%
    mgmt_fee_annual: float = 0.015 # 年管理费率 1.5%
    # 估值加权定投参数
    low_pct: float = 30
    high_pct: float = 70
    min_multiplier: float = 0.5
    max_multiplier: float = 2.0
    # 估值分位买卖参数
    buy_threshold: float = 30
    sell_threshold: float = 70
    buy_amount: float = 2000
    sell_ratio: float = 0.3
    # 再平衡参数
    frequency_months: int = 3
    equity_target: float = 0.6
    drift_threshold: float = 0.05


@router.get("/api/strategy-sandbox/presets")
async def get_presets():
    """获取预设策略模板。"""
    return {"presets": PRESETS}


@router.post("/api/strategy-sandbox/backtest")
async def run_backtest_api(req: BacktestRequest):
    """运行策略回测。"""
    try:
        result = run_backtest(req.model_dump())
        return result
    except Exception as e:
        raise HTTPException(500, f"回测失败: {str(e)}")
