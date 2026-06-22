"""分析路由包 — /api/portfolio/analysis/* 等分析类接口

按业务域拆分：
  - panorama       全景诊断
  - deep_dive      深度分析 + 指定基金分析
  - trade_review   交易复盘
  - what_if        情景推演
  - diversification 分散度分析 + AI 摘要
  - portfolio_ai   持仓 AI 分析
  - rebalancing    调仓策略
  - hotspots       热点分析 + 机会推荐
  - daily_report   每日简报
  - market_intel   市场情报
  - bond_recommend 债券 AI 推荐
  - index_analysis 指数深度分析

注：eval.py 保持原位，仅修改 import 路径。
"""
from .panorama import router as panorama_router
from .deep_dive import router as deep_dive_router
from .trade_review import router as trade_review_router
from .what_if import router as what_if_router
from .diversification import router as diversification_router
from .portfolio_ai import router as portfolio_ai_router
from .rebalancing import router as rebalancing_router
from .hotspots import router as hotspots_router
from .daily_report import router as daily_report_router
from .market_intel import router as market_intel_router
from .bond_recommend import router as bond_recommend_router
from .index_analysis import router as index_analysis_router

__all__ = [
    "panorama_router", "deep_dive_router", "trade_review_router",
    "what_if_router", "diversification_router", "portfolio_ai_router",
    "rebalancing_router", "hotspots_router", "daily_report_router",
    "market_intel_router", "bond_recommend_router", "index_analysis_router",
]
