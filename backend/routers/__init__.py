# routers/__init__.py — 路由聚合包，从功能子目录统一导入所有 router
#
# 子目录划分：
#   conversation/  对话与消息
#   portfolio/     持仓与交易
#   market/        估值与行情
#   dashboard/     仪表盘与日报
#   knowledge/     知识库与文章
#   admin/         系统管理
#   decision/      理财决策
#   task/          异步任务
#   analysis/      分析（已有子目录，独立维护）
# 根目录保留：fund_manager.py / profile.py / strategy_sandbox.py

# task（无跨包依赖，优先导入；knowledge.articles 顶层依赖 task.tasks）
from .task import (
    tasks_router,
    async_tasks_router,
    images_router,
)

# conversation
from .conversation import (
    conversations_router,
    thread_review_router,
    chat_images_router,
    notifications_router,
    memory_routes_router,
)

# portfolio
from .portfolio import (
    portfolio_router,
    portfolio_import_router,
    trade_plans_router,
    watchlist_router,
    bucket_routes_router,
    strategies_router,
)

# market
from .market import (
    valuation_router,
    index_info_router,
    bond_router,
    market_intelligence_router,
    event_radar_router,
)

# dashboard
from .dashboard import (
    dashboard_router,
    daily_advice_router,
    finance_dashboard_router,
    finance_routes_router,
)

# knowledge
from .knowledge import (
    knowledge_router,
    articles_router,
    rag_router,
    search_router,
)

# admin
from .admin import (
    agents_router,
    capabilities_router,
    eval_router,
    cost_governance_router,
    token_usage_router,
    config_router,
    data_health_router,
    data_quality_router,
)

# decision
from .decision import (
    decisions_router,
    opportunities_router,
    suggestion_accuracy_router,
)

# 根目录保留的路由
from .fund_manager import router as fund_manager_router
from .profile import router as profile_router
from .strategy_sandbox import router as strategy_sandbox_router
