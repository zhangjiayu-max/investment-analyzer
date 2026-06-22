"""持仓路由聚合 — /api/portfolio/*

从原 routers/portfolio.py（2631行）拆分为 5 个子模块：
  - holdings.py      — 持仓CRUD、现金、净值、CSV导入导出、快照
  - transactions.py  — 交易记录、交易标签、审计日志
  - analysis.py      — 持仓分析（分散度/AI/全景/深度/复盘/what-if）
  - alerts.py        — 风险预警、加减仓预警
  - rebalance.py     — 调仓管理、配置CRUD、压力测试

本文件聚合所有子路由，对外暴露统一的 router。
"""

from fastapi import APIRouter

from .holdings import router as holdings_router
from .transactions import router as transactions_router
from .analysis import router as analysis_router
from .alerts import router as alerts_router
from .rebalance import router as rebalance_router

router = APIRouter(tags=["portfolio"])

# 所有子路由统一挂载，路径前缀不变
router.include_router(holdings_router)
router.include_router(transactions_router)
router.include_router(analysis_router)
router.include_router(alerts_router)
router.include_router(rebalance_router)
