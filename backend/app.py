"""投资分析助手 — FastAPI 后端"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)
import os
import queue
import re
import time
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
import os

# 日志持久化：按天轮转，保留7天
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_fmt = "%(asctime)s [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=log_fmt)
_file_handler = TimedRotatingFileHandler(
    str(LOG_DIR / "backend.log"), when="midnight", interval=1, backupCount=7, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter(log_fmt))
logging.getLogger().addHandler(_file_handler)

from fastapi import FastAPI, HTTPException, Request, UploadFile, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import STATIC_DIR, IMAGES_DIR, OUTPUT_DIR, UPLOADS_DIR, DD_IMAGES_DIR, VALUATION_IMAGES_DIR, ROOT

from db import (
    init_db,
    save_valuation, get_valuation_history, get_latest_valuation, list_valuation_indexes,
    list_index_freshness,
    list_all_analysis_records, list_linked_articles,
    list_holdings, get_portfolio_summary,
    lookup_fund_info, get_fund_holdings,
    get_portfolio_diversification,
    get_cash_balance,
    get_analysis_agent,
    create_analysis_history, list_analysis_history,
    get_config_int, get_config_float, get_config,
)
from services.market_data import get_index_valuation
from services.llm_service import chat_about_investment, _call_llm, MODEL
from services.rag import init_fts, init_chroma, index_article, index_valuation, build_rag_context_with_details, index_author_article, index_skill_document, index_skill_extraction, index_to_chroma, _get_chroma, _get_embed_model

app = FastAPI(title="投资分析助手", version="0.4.0")

# 全局状态

# P2-1：quote-bar 缓存（避免高频刷新重复调用 akshare）
# TTL 30s，hot_keywords 为 None 表示未缓存
_quote_bar_cache = {"hot_keywords": None, "expires_at": None}


app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求追踪中间件
from infra.request_tracing import RequestTracingMiddleware
app.add_middleware(RequestTracingMiddleware)

# API 标准化：响应包装中间件 + 全局异常处理器
from api.middleware import register_exception_handlers, ResponseWrapperMiddleware
app.add_middleware(ResponseWrapperMiddleware)
register_exception_handlers(app)

# ── 路由注册 ─────────────────────────────────────

from routers.market.valuation import router as valuation_router, index_info_router  # /api/valuation/* + /api/index-info/*
from routers.admin.agents import router as agents_router
from routers.conversation.conversations import router as conversations_router
from routers.task.tasks import router as tasks_router
from routers.knowledge.articles import router as articles_router
from routers.portfolio.portfolio import router as portfolio_router

# 其他路由
from routers.market.bond import router as bond_router
from routers.admin.token_usage import router as token_usage_router
from routers.task.images import router as images_router
from routers.admin.eval import router as eval_router
from routers.analysis import (
    panorama_router as analysis_panorama_router,
    deep_dive_router as analysis_deep_dive_router,
    trade_review_router as analysis_trade_review_router,
    what_if_router as analysis_what_if_router,
    diversification_router as analysis_diversification_router,
    portfolio_ai_router as analysis_portfolio_ai_router,
    rebalancing_router as analysis_rebalancing_router,
    hotspots_router as analysis_hotspots_router,
    daily_report_router as analysis_daily_report_router,
    market_intel_router as analysis_market_intel_router,
    bond_recommend_router as analysis_bond_recommend_router,
    index_analysis_router as analysis_index_analysis_router,
    fee_router as analysis_fee_router,
    correlation_router as analysis_correlation_router,
    eval_system_router as analysis_eval_system_router,
    health_score_router as analysis_health_score_router,
    rolling_return_router as analysis_rolling_return_router,
    four_pots_router as analysis_four_pots_router,
    fund_analysis_router as analysis_fund_analysis_router,
    compare_diff_router as analysis_compare_diff_router,
    trade_pattern_router as analysis_trade_pattern_router,
    decision_canvas_router as analysis_decision_canvas_router,
    accuracy_router as analysis_accuracy_router,
    institutional_flow_router as analysis_institutional_flow_router,
    smart_add_router as analysis_smart_add_router,
    fund_quality_router as analysis_fund_quality_router,
    portfolio_intelligence_router as analysis_portfolio_intelligence_router,
    master_backtest_router as analysis_master_backtest_router,
    analysis_log_router as analysis_log_router,
    health_v2_router as analysis_health_v2_router,
)
# 理财决策升级 6 项分析路由（accuracy 已在上方 import 块中注册）
from routers.analysis.attribution import router as attribution_router
from routers.analysis.behavior import router as behavior_router
from routers.analysis.strategy_backtest import router as strategy_bt_router
from routers.analysis.optimizer import router as optimizer_router
from routers.analysis.forecast import router as forecast_router
from routers.dashboard.dashboard import router as dashboard_router
from routers.admin.config import router as config_router
from routers.knowledge.rag import router as rag_router
# market_intelligence routes moved to routers/analysis/market_intel.py
from routers.knowledge.knowledge import router as knowledge_router
from routers.portfolio.watchlist import router as watchlist_router
from routers.profile import router as profile_router                    # /api/profile/*
from routers.task.async_tasks import router as async_tasks_router           # /api/async-tasks/*
from routers.knowledge.search import router as search_router                     # /api/search/*
from routers.decision.decisions import router as decisions_router               # /api/decisions/*
from routers.dashboard.finance_dashboard import router as finance_dashboard_router  # /api/finance-dashboard
from routers.strategy_sandbox import router as strategy_sandbox_router  # /api/strategy-sandbox
from routers.fund_manager import router as fund_manager_router  # /api/fund-manager
from routers.admin.data_health import router as data_health_router          # /api/data-health
from routers.portfolio.portfolio_import import router as portfolio_import_router  # /api/portfolio/import-csv
from routers.decision.opportunities import router as opportunities_router      # /api/opportunities/*
from routers.dashboard.daily_advice import router as daily_advice_router        # /api/daily-advice/*
from routers.admin.cost_governance import router as cost_governance_router  # /api/cost-governance/*
from routers.conversation.thread_review import router as thread_review_router
from routers.conversation.chat_images import router as chat_images_router, CHAT_IMAGES_DIR  # /api/thread-review/*
from routers.decision.suggestion_accuracy import router as suggestion_accuracy_router  # /api/suggestion-accuracy/*
from routers.admin.data_quality import router as data_quality_router  # /api/data-quality/*
from routers.admin.capabilities import router as capabilities_router  # /api/capabilities/*
from routers.admin.theme_rules import router as theme_rules_router  # /api/admin/theme-rules/*
from routers.market.event_radar import router as event_radar_router  # /api/alerts/event-radar/*
from routers.portfolio.trade_plans import router as trade_plans_router  # /api/trade-plans/*
from routers.portfolio.strategies import router as strategies_router  # /api/strategies/*
from routers.portfolio.bucket_routes import router as bucket_routes_router  # /api/buckets/*
from routers.conversation.memory_routes import router as memory_routes_router  # /api/memory/*
from routers.dashboard.finance_routes import router as finance_routes_router  # /api/finance/*
from routers.conversation.notifications import router as notifications_router  # /api/notifications/*
from services.data_lineage import track_sources, get_lineage  # data lineage tracking

# 注册路由
app.include_router(valuation_router)
app.include_router(index_info_router)
app.include_router(agents_router)
app.include_router(conversations_router)
app.include_router(tasks_router)
app.include_router(articles_router)
app.include_router(portfolio_router)

# 注册其他路由
app.include_router(bond_router)
app.include_router(token_usage_router)
app.include_router(images_router)
app.include_router(eval_router)
# 注册分析子路由（从 portfolio/dashboard/bond/analysis/market_intelligence 拆出）
app.include_router(analysis_panorama_router)
app.include_router(analysis_deep_dive_router)
app.include_router(analysis_trade_review_router)
app.include_router(analysis_what_if_router)
app.include_router(analysis_diversification_router)
app.include_router(analysis_portfolio_ai_router)
app.include_router(analysis_rebalancing_router)
app.include_router(analysis_hotspots_router)
app.include_router(analysis_daily_report_router)
app.include_router(analysis_market_intel_router)
app.include_router(analysis_bond_recommend_router)
app.include_router(analysis_index_analysis_router)
app.include_router(analysis_fee_router)
app.include_router(analysis_correlation_router)
app.include_router(analysis_eval_system_router)
app.include_router(analysis_health_score_router)
app.include_router(analysis_rolling_return_router)
app.include_router(analysis_four_pots_router)
app.include_router(analysis_fund_analysis_router)
app.include_router(analysis_compare_diff_router)
app.include_router(analysis_trade_pattern_router)
app.include_router(analysis_decision_canvas_router)
app.include_router(analysis_accuracy_router)
app.include_router(analysis_institutional_flow_router)
app.include_router(analysis_smart_add_router)
app.include_router(analysis_fund_quality_router)
app.include_router(analysis_portfolio_intelligence_router)
app.include_router(analysis_master_backtest_router)
app.include_router(analysis_log_router)
app.include_router(analysis_health_v2_router)
# 理财决策升级 5 项分析路由（accuracy 已注册）
app.include_router(attribution_router)
app.include_router(behavior_router)
app.include_router(strategy_bt_router)
app.include_router(optimizer_router)
app.include_router(forecast_router)
app.include_router(dashboard_router)
app.include_router(config_router)
app.include_router(rag_router)
app.include_router(knowledge_router)
# market_intelligence_router removed (routes in analysis/market_intel.py)
app.include_router(watchlist_router)
app.include_router(profile_router)
app.include_router(async_tasks_router)
app.include_router(search_router)
app.include_router(decisions_router)
app.include_router(finance_dashboard_router)
app.include_router(strategy_sandbox_router)
app.include_router(fund_manager_router)
app.include_router(data_health_router)
app.include_router(portfolio_import_router)
app.include_router(opportunities_router)
app.include_router(daily_advice_router)
app.include_router(cost_governance_router)
app.include_router(thread_review_router)
app.include_router(chat_images_router)
app.include_router(suggestion_accuracy_router)
app.include_router(data_quality_router)
app.include_router(capabilities_router)
app.include_router(theme_rules_router)
app.include_router(event_radar_router)
app.include_router(trade_plans_router)
app.include_router(strategies_router)
app.include_router(bucket_routes_router)
app.include_router(memory_routes_router)
app.include_router(finance_routes_router)
app.include_router(notifications_router)

# ── 启动初始化 ──
@app.on_event("startup")
async def _init_daily_advice():
    from db.daily_advice import init_daily_advice_tables
    init_daily_advice_tables()
    from db.thread_summaries import init_thread_summaries_table
    init_thread_summaries_table()

# 静态文件目录
for _d in (STATIC_DIR, IMAGES_DIR, OUTPUT_DIR, UPLOADS_DIR, DD_IMAGES_DIR, VALUATION_IMAGES_DIR, CHAT_IMAGES_DIR):
    _d.mkdir(parents=True, exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(IMAGES_DIR)), name="article_images")
app.mount("/static/tasks", StaticFiles(directory=str(OUTPUT_DIR)), name="task_images")
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="frontend_assets")
app.mount("/static/dd_images", StaticFiles(directory=str(DD_IMAGES_DIR)), name="dd_images")
app.mount("/static/valuation_images", StaticFiles(directory=str(VALUATION_IMAGES_DIR)), name="valuation_images")
app.mount("/static/chat_images", StaticFiles(directory=str(CHAT_IMAGES_DIR)), name="chat_images")


def _index_skill_doc_by_type(doc_type: str, title: str):
    """按 doc_type 从 skill_documents 查出并索引到 FTS + ChromaDB。"""
    try:
        from db import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, content FROM skill_documents WHERE doc_type = ?", (doc_type,)
        ).fetchone()
        conn.close()
        if row:
            index_skill_document(row["id"], title, row["content"])
            index_to_chroma("skill", str(row["id"]), title, row["content"][:8000])
            logging.info(f"已索引 skill_document: {doc_type} (id={row['id']})")
    except Exception as e:
        logging.warning(f"索引 skill_document {doc_type} 失败: {e}")


async def _async_fetch_index_history(index_codes: list):
    """后台异步回填指数历史数据（L4/L5 算法依赖）。"""
    from services.index_history_fetcher import fetch_index_history
    for code in index_codes:
        try:
            result = fetch_index_history(code, years=10)
            if result.get("saved", 0) > 0:
                logging.info(f"指数 {code} 回填 {result['saved']} 条")
        except Exception as e:
            logging.debug(f"指数 {code} 回填失败: {e}")


@app.on_event("startup")
async def startup():
    """应用启动时的初始化。"""
    logging.info("=== 启动初始化开始 ===")

    # 1. 同步初始化（必须在启动前完成）
    logging.info("初始化数据库...")
    init_db()
    logging.info("数据库初始化完成")

    # 恢复因重启中断的对话（专家已完成但综合阶段未执行）
    try:
        from services.conv_recovery import recover_interrupted_conversations, auto_retry_process_restart_interrupted
        # 1. 先自动重试 process restart 中断（专家未执行的），后台线程执行不阻塞启动
        #    必须在 recover_interrupted_conversations 之前执行，否则会被标记为 failed
        retry_stats = auto_retry_process_restart_interrupted()
        if retry_stats["triggered"]:
            logging.info(f"自动重试 process restart 中断: {retry_stats}")
        # 2. 再恢复其他中断（有专家结果的合并写回，无专家的标记中断）
        stats = recover_interrupted_conversations()
        if stats["recovered"] or stats["marked_interrupted"]:
            logging.info(f"中断对话恢复: {stats}")
    except Exception as e:
        logging.warning(f"中断对话恢复失败（不影响启动）: {e}")

    # P3 优化：回填存量 recommendations 的 target_fund_code（启动时执行一次）
    try:
        from services.index_fund_mapper import backfill_recommendations_target_fund
        count = backfill_recommendations_target_fund()
        if count > 0:
            logging.info(f"回填 {count} 条 recommendations 的 target_fund_code")
    except Exception as e:
        logging.warning(f"回填 recommendations 失败（不影响启动）: {e}")

    # 异步回填持仓指数历史（L4/L5 算法依赖）
    try:
        from services.index_history_fetcher import fetch_holdings_index_history
        from db.portfolio import list_holdings
        holdings = list_holdings()
        index_codes = set(h.get("index_code") for h in holdings if h.get("index_code"))
        if index_codes:
            logging.info(f"开始异步回填 {len(index_codes)} 个指数历史数据...")
            # 后台执行，不阻塞启动
            asyncio.create_task(_async_fetch_index_history(list(index_codes)))
    except Exception as e:
        logging.warning(f"回填指数历史启动失败（不影响启动）: {e}")

    # 升级1: 初始化全局工具注册中心
    try:
        from tools.tool_registry import ToolRegistry
        from db._conn import DB_PATH
        ToolRegistry.initialize(db_path=DB_PATH)
        logging.info("ToolRegistry 初始化完成")
    except Exception as e:
        logging.warning(f"ToolRegistry 初始化失败: {e}")

    # 创建 token_usage 索引
    try:
        from db.token_usage import ensure_indexes
        ensure_indexes()
        logging.info("token_usage 索引已创建")
    except Exception as e:
        logging.warning(f"创建 token_usage 索引失败: {e}")

    # Shadow Mode 表初始化
    try:
        from infra.shadow_mode import init_shadow_db
        init_shadow_db()
    except Exception as e:
        logging.warning(f"Shadow Mode 初始化失败: {e}")

    logging.info("初始化 FTS5...")
    init_fts()
    logging.info("FTS5 初始化完成")

    # 2. 异步初始化（后台执行，不阻塞启动）
    async def _async_init():
        """后台异步初始化 ChromaDB、Embedding 模型和种子数据。"""
        try:
            logging.info("后台初始化 ChromaDB...")
            init_chroma()
            logging.info("ChromaDB 初始化完成")
        except Exception as e:
            logging.warning(f"ChromaDB 初始化失败: {e}")

        # 预加载 Embedding 模型（消除首次查询的 2-5s 延迟）
        try:
            logging.info("预加载 Embedding 模型...")
            from services.rag import _ensure_embed_model
            _ensure_embed_model()
            logging.info("Embedding 模型预加载完成")
        except Exception as e:
            logging.warning(f"Embedding 模型预加载失败: {e}")

        try:
            from db import seed_bond_knowledge, seed_investment_strategy_knowledge
            seed_bond_knowledge()
            seed_investment_strategy_knowledge()
        except Exception as e:
            logging.warning(f"种子数据初始化失败: {e}")

        # 记忆维护（压缩过时记忆 + 蒸馏行业知识）
        try:
            logging.info("启动记忆维护...")
            from agent.memory_governance import run_governance_maintenance
            run_governance_maintenance()
            logging.info("记忆维护完成")
        except Exception as e:
            logging.warning(f"记忆维护失败: {e}")

    # 后台执行异步初始化
    asyncio.create_task(_async_init())
    logging.info("后台初始化任务已启动")

    # 后台每日报告（LLM 调用，默认关闭）
    if get_config("llm_cost.auto_daily_report", "false") == "true":
        asyncio.create_task(_auto_daily_report())
    else:
        logging.info("自动市场日报已关闭（llm_cost.auto_daily_report=false）")

    # 后台每日净值自动刷新（15:30 收盘后）
    asyncio.create_task(_auto_refresh_nav())

    # 后台每日备份（启动时 + 每天凌晨 2 点）
    asyncio.create_task(_auto_daily_backup())

    # 后台每日清理（启动回填 + 每天凌晨 3 点：决策过期 + 结论清理，P0-4.1/3.3）
    asyncio.create_task(_auto_daily_cleanup())

    # 后台每周预警准确性回测（每周一 02:00，纯本地计算，P2-4.3）
    if get_config("alert.auto_weekly_backtest", "true") == "true":
        asyncio.create_task(_auto_weekly_alert_backtest())
        logging.info("每周预警准确性回测任务已启动（alert.auto_weekly_backtest=true）")
    else:
        logging.info("每周预警准确性回测已关闭（alert.auto_weekly_backtest=false）")

    # 后台每日评测 Pipeline（LLM 调用，默认关闭）
    if get_config("llm_cost.auto_daily_eval", "false") == "true":
        asyncio.create_task(_auto_daily_eval())
    else:
        logging.info("每日评测 Pipeline 已关闭（llm_cost.auto_daily_eval=false）")

    # P0 主动提醒扫描（估值阈值/持仓风险/建议验证，默认开启）
    if get_config("alerts.proactive_scan_enabled", "true") == "true":
        asyncio.create_task(_auto_periodic_scan())
        logging.info("主动提醒扫描任务已启动（alerts.proactive_scan_enabled=true）")
    else:
        logging.info("主动提醒扫描已关闭（alerts.proactive_scan_enabled=false）")

    # 前瞻性事件雷达（每晚 20:00，默认关闭，LLM 相关开关硬约束）
    if get_config("alerts.event_radar_enabled", "false") == "true":
        asyncio.create_task(_auto_event_radar_scan())
        logging.info("前瞻事件雷达任务已启动（alerts.event_radar_enabled=true）")
    else:
        logging.info("前瞻事件雷达已关闭（alerts.event_radar_enabled=false）")

    # P0-C 修复（2026-07-20）：机会雷达回测机制修复
    # 1. 启动时补建历史机会卡的 backtest 记录（alerts.opportunity_backfill_enabled 默认 true）
    # 2. 每日 09:30 自动回测已到期记录（alerts.opportunity_backtest_enabled 默认 true）
    try:
        if get_config("alerts.opportunity_backfill_enabled", "true") == "true":
            from services.advisor.opportunity_engine import backfill_opportunity_backtests
            backfill_stats = backfill_opportunity_backtests()
            if backfill_stats.get("created", 0) > 0:
                logging.info(f"机会雷达 backfill: {backfill_stats}")
    except Exception as e:
        logging.warning(f"机会雷达 backfill 失败（不影响启动）: {e}")

    if get_config("alerts.opportunity_backtest_enabled", "true") == "true":
        asyncio.create_task(_auto_opportunity_backtest())
        logging.info("机会雷达每日回测任务已启动（alerts.opportunity_backtest_enabled=true）")
    else:
        logging.info("机会雷达每日回测已关闭（alerts.opportunity_backtest_enabled=false）")

    # O-1（2026-07-22）：主题机会引擎每日自动扫描（16:00 盘后）
    # 开关：opportunity.auto_daily_scan_enabled（默认 true）
    # 复用事件雷达 _collect_news() 三源新闻，避免重复采集
    if get_config("opportunity.auto_daily_scan_enabled", "true") == "true":
        asyncio.create_task(_auto_daily_opportunity_scan())
        logging.info("主题机会每日扫描任务已启动（opportunity.auto_daily_scan_enabled=true，16:00 触发）")
    else:
        logging.info("主题机会每日扫描已关闭（opportunity.auto_daily_scan_enabled=false）")

    # H-2（2026-07-22）：全账户诊断每日自动调度（18:00 盘后）
    # 开关：health_v2.auto_daily_diagnosis_enabled（默认 true）
    # 确保历史快照连续，score_change_7d 计算准确
    if get_config("health_v2.auto_daily_diagnosis_enabled", "true") == "true":
        asyncio.create_task(_auto_daily_health_diagnosis())
        logging.info("全账户诊断每日自动调度已启动（health_v2.auto_daily_diagnosis_enabled=true，18:00 触发）")
    else:
        logging.info("全账户诊断每日自动调度已关闭（health_v2.auto_daily_diagnosis_enabled=false）")

    # P0-3（2026-07-21）：关注列表信号回测定时任务
    if get_config("alerts.watchlist_backtest_enabled", "true") == "true":
        asyncio.create_task(_auto_watchlist_backtest())
        logging.info("关注列表信号回测任务已启动（alerts.watchlist_backtest_enabled=true）")
    else:
        logging.info("关注列表信号回测已关闭（alerts.watchlist_backtest_enabled=false）")

    # O-8（2026-07-21）：启动时一键 backfill 机会雷达与事件雷达历史数据
    # 开关：alerts.auto_backfill_on_startup_enabled（默认 true）
    # 触发：sources/impact/direction/confidence/opportunity/watchlist 全量回补
    try:
        if get_config("alerts.auto_backfill_on_startup_enabled", "true") == "true":
            from services.market.event_radar import backfill_all_once
            backfill_stats = backfill_all_once(max_events=100)
            logging.info(f"[启动 backfill] 事件雷达历史数据回补完成: {backfill_stats}")

            # O-3: 机会雷达核心字段 backfill（entry_price/entry_amount/valuation_percentile）
            try:
                from services.advisor.opportunity_engine import backfill_opportunity_fields
                opp_stats = backfill_opportunity_fields(max_items=100)
                logging.info(f"[启动 backfill] 机会雷达核心字段回补完成: {opp_stats}")
            except Exception as _e:
                logging.warning(f"[启动 backfill] 机会雷达字段回补失败（不影响启动）: {_e}")

            # O-4: watchlist current_percentile fallback 刷新
            try:
                from db.watchlist import refresh_watchlist_percentile
                wl_stats = refresh_watchlist_percentile()
                logging.info(f"[启动 backfill] watchlist 分位刷新完成: {wl_stats}")
            except Exception as _e:
                logging.warning(f"[启动 backfill] watchlist 分位刷新失败（不影响启动）: {_e}")
        else:
            logging.info("启动 backfill 已关闭（alerts.auto_backfill_on_startup_enabled=false）")
    except Exception as e:
        logging.warning(f"[启动 backfill] 失败（不影响启动）: {e}")

    # 清理上次异常退出遗留的僵尸 agent_runs
    try:
        from db.agents import _get_conn
        _conn = _get_conn()
        _cur = _conn.execute("UPDATE agent_runs SET status='failed', result='server restart cleanup' WHERE status IN ('pending','running')")
        _cleaned = _cur.rowcount
        _conn.commit()
        _conn.close()
        if _cleaned > 0:
            logging.info(f"清理 {_cleaned} 个僵尸 agent_runs")
    except Exception as e:
        logging.warning(f"清理僵尸任务失败: {e}")

    # 自动过期清理：过期的 proposed 决策和超时的建议候选
    try:
        from db.decisions import auto_expire_cleanup
        auto_expire_cleanup()
    except Exception as e:
        logging.warning(f"决策过期清理失败: {e}")

    # Reranker 模型预加载（避免首次对话 RAG 检索时加载 6 秒）
    try:
        _rerank_topn = get_config("rag.auto_rerank_topn", "false") == "true"
        if _rerank_topn:
            import threading
            def _preload_reranker():
                try:
                    from services.rag import _get_reranker
                    _get_reranker()
                    logging.info("Reranker 模型预加载完成")
                except Exception as e:
                    logging.warning(f"Reranker 预加载失败: {e}")
            threading.Thread(target=_preload_reranker, daemon=True).start()
    except Exception:
        pass

    # R-9（2026-07-23）：启动时自动运行 RAG 评估（开关控制，默认关）
    try:
        from db.config import get_config_bool
        if get_config_bool("rag.auto_run_eval_on_startup", False):
            from scripts.rag_eval_suite import run_eval_suite_by_category, save_results, EVAL_SUITE_CASES
            async def _run_startup_eval():
                # 只跑盲区类别，避免启动过慢
                blind_spot_cases = {"blind_spot_coverage": EVAL_SUITE_CASES.get("blind_spot_coverage", [])}
                result = await run_eval_suite_by_category(cases=blind_spot_cases, verbose=False)
                save_results(result)
                logger.info(f"[R-9] 启动评估完成，结果已写入 data/rag_eval_results.json")
            asyncio.create_task(_run_startup_eval())
    except Exception as e:
        logger.warning(f"[R-9] 启动评估失败（不阻塞）: {e}")

    logging.info("=== 启动初始化完成 ===")


@app.on_event("shutdown")
async def shutdown():
    """优雅关闭：等待活跃请求完成，清理资源。"""
    logging.info("=== 开始优雅关闭 ===")

    # 1. 停止接受新请求（FastAPI 自动处理）
    # 2. 等待活跃的 SSE 连接完成（最多 10 秒）
    try:
        from services.llm_service import _active_sse_count
        import time
        deadline = time.time() + 10
        while _active_sse_count() > 0 and time.time() < deadline:
            logging.info(f"等待 {_active_sse_count()} 个 SSE 连接完成...")
            await asyncio.sleep(1)
    except Exception:
        pass

    # 3. 关闭数据库连接池
    try:
        from db import _get_conn
        conn = _get_conn()
        conn.close()
        logging.info("数据库连接已关闭")
    except Exception:
        pass

    # 4. 取消后台任务
    try:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        logging.info(f"已取消 {len(tasks)} 个后台任务")
    except Exception:
        pass

    logging.info("=== 优雅关闭完成 ===")


async def _auto_daily_backup():
    """每日凌晨 2 点自动备份数据库。"""
    import time
    try:
        from db.backup import backup_database
        # 启动时先备份一次
        backup_database()
        while True:
            now = time.localtime()
            # 计算到下一个凌晨 2 点的秒数
            target_hour = 2
            if now.tm_hour >= target_hour:
                wait_seconds = (24 - now.tm_hour + target_hour) * 3600 - now.tm_min * 60 - now.tm_sec
            else:
                wait_seconds = (target_hour - now.tm_hour) * 3600 - now.tm_min * 60 - now.tm_sec
            await asyncio.sleep(max(wait_seconds, 60))
            backup_database()
    except Exception as e:
        logging.warning(f"自动备份任务异常: {e}")


async def _auto_daily_cleanup():
    """每日凌晨 3 点执行数据清理任务。

    设计稿 P0-4.1 + P0-3.3：
    - 决策过期：accepted 状态超 7 天自动转 deferred
    - 结论清理：analysis_conclusions 表过期记录清理
    - 启动时执行一次回填（holding_id / source_decision_id）
    """
    import time
    try:
        await asyncio.sleep(30)  # 等启动完成

        # 启动时回填一次（幂等）
        try:
            from db.portfolio import backfill_alert_holding_id
            from db.decisions import backfill_kb_decision_id
            alert_n = backfill_alert_holding_id()
            kb_n = backfill_kb_decision_id()
            if alert_n or kb_n:
                logging.info(f"[auto-cleanup] 启动回填: alerts={alert_n}, kb_lessons={kb_n}")
        except Exception as e:
            logging.warning(f"[auto-cleanup] 启动回填异常: {e}")

        while True:
            now = time.localtime()
            target_hour = 3
            if now.tm_hour >= target_hour:
                wait_seconds = (24 - now.tm_hour + target_hour) * 3600 - now.tm_min * 60 - now.tm_sec
            else:
                wait_seconds = (target_hour - now.tm_hour) * 3600 - now.tm_min * 60 - now.tm_sec
            await asyncio.sleep(max(wait_seconds, 60))

            try:
                # P0-4.1：决策过期
                from db.decisions import expire_stale_decisions
                expired = expire_stale_decisions(days=7)
                if expired > 0:
                    logging.info(f"[auto-cleanup] 决策过期: {expired} 条 accepted 转 deferred")

                # P0-3.3：过期结论清理
                from db.analysis_conclusions import cleanup_expired_conclusions
                cleaned = cleanup_expired_conclusions()
                if cleaned > 0:
                    logging.info(f"[auto-cleanup] 清理过期分析结论: {cleaned} 条")
            except Exception as e:
                logging.warning(f"[auto-cleanup] 清理任务异常: {e}")
    except Exception as e:
        logging.warning(f"自动清理任务异常: {e}")


async def _auto_weekly_alert_backtest():
    """P2-4.3: 每周一 02:00 回测上周预警准确性。

    纯本地 SQLite 计算（0 LLM、0 MCP、0 akshare），为预警阈值调整提供数据支撑。
    配置开关：alert.auto_weekly_backtest（默认 true）。
    """
    import time
    try:
        await asyncio.sleep(60)  # 等启动完成

        while True:
            now = time.localtime()
            # 计算到下一个周一 02:00 的等待秒数
            days_until_mon = (7 - now.tm_wday) % 7  # tm_wday: 0=周一
            if days_until_mon == 0 and now.tm_hour >= 2:
                days_until_mon = 7
            target_ts = time.mktime((
                now.tm_year, now.tm_mon, now.tm_mday + days_until_mon,
                2, 0, 0, 0, 0, -1,
            ))
            wait_seconds = target_ts - time.time()
            await asyncio.sleep(max(wait_seconds, 60))

            try:
                from services.alert_accuracy_backtest import backtest_alert_accuracy
                # 回测上周（自动取上周一）
                result = backtest_alert_accuracy()
                logging.info(
                    f"[auto-backtest] 预警准确性回测完成: "
                    f"week={result.get('week_start')} alerts={result.get('alert_count')} "
                    f"groups={len(result.get('stat_groups', []))}"
                )
            except Exception as e:
                logging.warning(f"[auto-backtest] 预警准确性回测异常: {e}")
    except Exception as e:
        logging.warning(f"每周预警回测任务异常: {e}")


async def _auto_daily_eval():
    """每日凌晨 4:00 自动执行评测 Pipeline。"""
    import time
    try:
        await asyncio.sleep(15)  # 等启动完成

        # 启动时如果今天还没跑过，立刻跑一次
        today = datetime.now().strftime("%Y-%m-%d") if 'datetime' in dir() else time.strftime("%Y-%m-%d")
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        try:
            from db.eval import get_eval_daily_report
            existing = get_eval_daily_report(today)
            if not existing:
                logging.info("今日评测未执行，立即运行...")
                from routers.analysis.eval_system import run_daily_eval
                await run_daily_eval()
        except Exception as e:
            logging.warning(f"启动时评测检查失败: {e}")

        while True:
            now = _dt.now()
            target = now.replace(hour=4, minute=0, second=0, microsecond=0)
            if now >= target:
                from datetime import timedelta
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            logging.info(f"[auto-eval] 下次每日评测: {wait_seconds/3600:.1f} 小时后")
            await asyncio.sleep(wait_seconds)

            try:
                logging.info("=== 每日自动评测开始 ===")

                # Step 1: 跑每日评测
                from routers.analysis.eval_system import run_daily_eval
                eval_result = await run_daily_eval()
                logging.info(f"每日评测完成: avg={eval_result.get('avg_score')}")

                # Step 2: 检查并晋升 Shadow
                from infra.shadow_mode import auto_promote_shadows
                promoted = await auto_promote_shadows()
                logging.info(f"Shadow 晋升: {len(promoted)} 个")

                # Step 3: 自动生成 Eval 用例
                from infra.auto_eval_generator import auto_generate_eval_cases
                gen_result = auto_generate_eval_cases()
                logging.info(f"Eval 用例生成: {gen_result}")

                # Step 4: 决策回顾自动生成（每周一）
                try:
                    from datetime import datetime as _dt
                    if _dt.now().weekday() == 0:  # 周一
                        from db.decisions import generate_weekly_decision_review
                        review_result = generate_weekly_decision_review()
                        logging.info(f"决策周回顾: {review_result}")
                except Exception as de:
                    logging.warning(f"决策回顾生成失败: {de}")

                logging.info("=== 每日自动评测完成 ===")
            except Exception as e:
                logging.error(f"每日评测失败: {e}")
    except Exception as e:
        logging.warning(f"自动评测任务异常: {e}")


async def _auto_periodic_scan():
    """P0 主动提醒扫描 — 每隔 N 分钟扫描一次（默认 30 分钟）。

    包含 3 个扫描函数：
    - 建议验证：到达 verify_after_date 的 pending 建议自动验证，生成结果 alert
    - 估值阈值：持仓相关指数 PE/PB 分位突破阈值时生成 alert
    - 持仓风险：单标的集中度/亏损超阈值时生成 alert

    开关：alerts.proactive_scan_enabled（默认 true）
    间隔：alerts.scan_interval_minutes（默认 30）
    """
    import time
    try:
        await asyncio.sleep(60)  # 等启动完成

        # 启动时立即跑一次（让用户登录就能看到最新提醒）
        # 放到线程中执行，避免同步 DB 查询阻塞事件循环导致启动后 HTTP 请求超时
        try:
            if get_config("alerts.proactive_scan_enabled", "true") == "true":
                from services.alert_scanner import run_periodic_scan
                result = await asyncio.to_thread(run_periodic_scan)
                logging.info(
                    f"[auto-scan] 启动扫描完成: "
                    f"verified={result.get('verification', {}).get('verified', 0)}, "
                    f"valuation_alerts={result.get('valuation', {}).get('alerts_created', 0)}, "
                    f"portfolio_alerts={result.get('portfolio', {}).get('alerts_created', 0)}"
                )
        except Exception as e:
            logging.warning(f"[auto-scan] 启动扫描异常: {e}")

        while True:
            # P1-1: 交易时段缩短扫描间隔，捕捉盘内异动
            # 交易日 09:30-15:00 用 alerts.trading_hours_scan_interval_minutes（默认 5）
            # 非交易时段用 alerts.scan_interval_minutes（默认 30）
            from datetime import datetime as _dt
            now = _dt.now()
            is_weekday = now.weekday() < 5  # 0-4 = 周一到周五
            is_trading_hours = is_weekday and 9 <= now.hour < 15
            if is_trading_hours:
                try:
                    interval_min = int(get_config("alerts.trading_hours_scan_interval_minutes", "5"))
                except (TypeError, ValueError):
                    interval_min = 5
            else:
                try:
                    interval_min = int(get_config("alerts.scan_interval_minutes", "30"))
                except (TypeError, ValueError):
                    interval_min = 30
            await asyncio.sleep(max(interval_min * 60, 60))  # 至少 1 分钟

            try:
                if get_config("alerts.proactive_scan_enabled", "true") != "true":
                    continue
                from services.alert_scanner import run_periodic_scan
                result = await asyncio.to_thread(run_periodic_scan)
                logging.info(
                    f"[auto-scan] 定时扫描完成 ({'盘中' if is_trading_hours else '盘后'} {interval_min}min): "
                    f"verified={result.get('verification', {}).get('verified', 0)}, "
                    f"valuation_alerts={result.get('valuation', {}).get('alerts_created', 0)}, "
                    f"portfolio_alerts={result.get('portfolio', {}).get('alerts_created', 0)}, "
                    f"market_index_drop={result.get('market_index_drop', {}).get('alerts_created', 0)}, "
                    f"capital_flow={result.get('capital_flow', {}).get('alerts_created', 0)}"
                )
            except Exception as e:
                logging.warning(f"[auto-scan] 定时扫描异常: {e}")
    except Exception as e:
        logging.warning(f"主动提醒扫描任务异常: {e}")


async def _auto_event_radar_scan():
    """前瞻性事件雷达 — 每日 3 次扫描（11:30 / 14:30 / 20:00）。

    从新闻中提取未来 1-2 周的市场事件，匹配持仓/候选基金，生成 3 级 alert。

    开关：alerts.event_radar_enabled（默认 false，LLM 相关开关硬约束）
    调度：
    - 11:30 / 14:30（盘中）：捕捉当日正在发生的异动（P2 新增）
    - 20:00（盘后）：完整扫描未来 1-2 周事件（原有逻辑）
    周末/节假日仍会触发但不影响（新闻采集会返回空）
    """
    from datetime import datetime, timedelta
    # 3 个扫描时间点（小时, 分钟）
    _SCAN_TIMES = [(11, 30), (14, 30), (20, 0)]
    try:
        await asyncio.sleep(120)  # 等启动完成（比 _auto_periodic_scan 晚 1 分钟避免抢资源）

        while True:
            # 计算距下次扫描时间点的等待秒数
            now = datetime.now()
            next_target = None
            for (h, m) in _SCAN_TIMES:
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target > now:
                    next_target = target
                    break
            if next_target is None:
                # 今天所有时间点已过，等到明天第一个
                first_h, first_m = _SCAN_TIMES[0]
                next_target = (now + timedelta(days=1)).replace(
                    hour=first_h, minute=first_m, second=0, microsecond=0)
            wait_seconds = (next_target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if get_config("alerts.event_radar_enabled", "false") != "true":
                continue

            scan_hour = next_target.hour
            try:
                from services.event_radar import scan_forward_events
                result = scan_forward_events()
                logging.info(
                    f"[event-radar] 扫描完成 ({scan_hour:02d}:00): "
                    f"extracted={result.get('extracted', 0)}, "
                    f"new={result.get('new', 0)}, "
                    f"alerts={result.get('alerts_created', 0)}"
                )

                # 扫描完成后顺便执行落地验证（检查已到期的事件）
                if get_config("alerts.event_radar_verify_enabled", "true") == "true":
                    from services.event_radar import verify_materialized_events
                    vresult = verify_materialized_events()
                    if vresult.get("verified", 0) > 0:
                        logging.info(
                            f"[event-radar] 落地验证: "
                            f"verified={vresult.get('verified', 0)}, "
                            f"correct={vresult.get('correct', 0)}, "
                            f"wrong={vresult.get('wrong', 0)}"
                        )
            except Exception as e:
                logging.warning(f"[event-radar] 扫描异常 ({scan_hour:02d}:00): {e}")
    except Exception as e:
        logging.warning(f"前瞻事件雷达任务异常: {e}")


async def _auto_opportunity_backtest():
    """机会雷达回测 — 每日 09:30 自动回测已到期的机会卡。

    P0-C 修复（2026-07-20）：
    - 原问题：theme_opportunity_backtests 表 0 条，命中率永远 None
    - 修复：每日 09:30 自动跑 review_opportunity_backtests()，
            回测所有 review_date <= today 的记录

    开关：alerts.opportunity_backtest_enabled（默认 true）
    """
    from datetime import datetime, timedelta
    # 每日 09:30 自动回测
    _SCAN_TIME = (9, 30)
    try:
        await asyncio.sleep(180)  # 等启动完成（避免与 backfill 抢资源）

        while True:
            now = datetime.now()
            target = now.replace(hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            if target <= now:
                target = (now + timedelta(days=1)).replace(
                    hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if get_config("alerts.opportunity_backtest_enabled", "true") != "true":
                continue

            try:
                from services.advisor.opportunity_engine import review_opportunity_backtests
                result = review_opportunity_backtests()
                logging.info(
                    f"[opportunity-backtest] 回测完成: "
                    f"reviewed={result.get('reviewed', 0)}, "
                    f"hit={result.get('hit', 0)}, "
                    f"miss={result.get('miss', 0)}"
                )
            except Exception as e:
                logging.warning(f"[opportunity-backtest] 回测异常: {e}")
    except Exception as e:
        logging.warning(f"机会雷达回测任务异常: {e}")


async def _auto_daily_opportunity_scan():
    """O-1（2026-07-22）：主题机会引擎每日自动扫描 — 每日 16:00 盘后自动生成主题机会卡。

    原问题：scan_daily_opportunities 仅由 /api/opportunities/daily-scan 手动触发，
            用户不点"扫描"按钮就不会生成新机会卡，回测表只能 backfill 历史数据，
            无法形成"每日生成→15日回测→命中率反馈"闭环。

    修复：每日 16:00 自动调用 scan_daily_opportunities，
          新闻源优先复用事件雷达 _collect_news() 三源采集结果（避免重复采集），
          失败时降级为 Dashboard get_hot_topics。

    开关：opportunity.auto_daily_scan_enabled（默认 true）
    调度：每日 16:00（盘后，避免与 event_radar 20:00 抢资源）
    """
    from datetime import datetime, timedelta
    _SCAN_TIME = (16, 0)
    try:
        await asyncio.sleep(240)  # 等启动完成（比 backfill 晚 4 分钟）

        while True:
            now = datetime.now()
            target = now.replace(hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            if target <= now:
                target = (now + timedelta(days=1)).replace(
                    hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if get_config("opportunity.auto_daily_scan_enabled", "true") != "true":
                continue

            try:
                # 新闻源优先复用事件雷达三源采集
                news_items = []
                try:
                    from services.market.event_radar import _collect_news
                    news_items = _collect_news()
                    logging.info(f"[opportunity-scan] 复用 event_radar 新闻 {len(news_items)} 条")
                except Exception as e:
                    logging.warning(f"[opportunity-scan] 复用 _collect_news 失败，降级 Dashboard: {e}")
                    try:
                        from routers.dashboard.dashboard import get_hot_topics
                        import asyncio as _aio
                        hot = await get_hot_topics()
                        news_items = hot.get("news", []) if hot else []
                    except Exception as e2:
                        logging.warning(f"[opportunity-scan] Dashboard get_hot_topics 也失败: {e2}")

                from services.advisor.opportunity_engine import scan_daily_opportunities
                result = scan_daily_opportunities(
                    news_items=news_items,
                    force_refresh=True,
                )
                items_count = len(result.get("items", []))
                logging.info(
                    f"[opportunity-scan] 每日扫描完成: "
                    f"items={items_count}, "
                    f"source={result.get('source', 'unknown')}"
                )
            except Exception as e:
                logging.warning(f"[opportunity-scan] 每日扫描异常: {e}")
    except Exception as e:
        logging.warning(f"主题机会每日扫描任务异常: {e}")


async def _auto_daily_health_diagnosis():
    """H-2（2026-07-22）：全账户诊断每日自动调度 — 每日 18:00 盘后自动调用 get_health_v2_dashboard。

    原问题：get_health_v2_dashboard 仅由 /api/health-v2/dashboard 手动触发，
            用户不打开页面就不会写入快照，导致 score_change_7d 计算失真，
            recommendation_candidates 表跨日重复创建候选堆积。

    修复：每日 18:00 自动调用 get_health_v2_dashboard(force_refresh=True)，
          确保历史快照连续，action_id 已稳定化（H-2去掉日期后缀）避免重复沉淀。

    开关：health_v2.auto_daily_diagnosis_enabled（默认 true）
    调度：每日 18:00（盘后，晚于机会雷达16:00，避免抢资源）
    """
    from datetime import datetime, timedelta
    _SCAN_TIME = (18, 0)
    try:
        await asyncio.sleep(300)  # 等启动完成（晚于机会雷达5分钟）

        while True:
            now = datetime.now()
            target = now.replace(hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            if target <= now:
                target = (now + timedelta(days=1)).replace(
                    hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if get_config("health_v2.auto_daily_diagnosis_enabled", "true") != "true":
                continue

            try:
                from services.health.health_v2_service import get_health_v2_dashboard
                result = get_health_v2_dashboard(force_refresh=True)
                total_score = result.get("health_score", {}).get("total_score", 0)
                actions_count = len(result.get("actions", []))
                logging.info(
                    f"[health-v2-diagnosis] 每日自动诊断完成: "
                    f"total_score={total_score}, "
                    f"actions={actions_count}"
                )
            except Exception as e:
                logging.warning(f"[health-v2-diagnosis] 每日自动诊断异常: {e}")
    except Exception as e:
        logging.warning(f"全账户诊断每日自动调度任务异常: {e}")


async def _auto_watchlist_backtest():
    """关注列表信号回测 — 每日 09:35 自动回测已到期的上车信号。

    P0-3（2026-07-21）新增：
    - 原 watchlist 信号灯"可上车"是否准确无量化验证
    - 每次信号由非 green 变 green 时插入回测记录
    - 15 交易日后自动回测涨幅（命中定义 >= 3%）

    开关：alerts.watchlist_backtest_enabled（默认 true）
    """
    from datetime import datetime, timedelta
    _SCAN_TIME = (9, 35)
    try:
        await asyncio.sleep(240)  # 等启动完成（比机会雷达回测晚 4 分钟避免抢资源）

        while True:
            now = datetime.now()
            target = now.replace(hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            if target <= now:
                target = (now + timedelta(days=1)).replace(
                    hour=_SCAN_TIME[0], minute=_SCAN_TIME[1], second=0, microsecond=0)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if get_config("alerts.watchlist_backtest_enabled", "true") != "true":
                continue

            try:
                from services.advisor.watchlist_backtest import review_watchlist_signal_backtests
                result = review_watchlist_signal_backtests()
                logging.info(
                    f"[watchlist-backtest] 回测完成: "
                    f"reviewed={result.get('reviewed', 0)}, "
                    f"hit={result.get('hit', 0)}, "
                    f"miss={result.get('miss', 0)}, "
                    f"skipped={result.get('skipped', 0)}"
                )
            except Exception as e:
                logging.warning(f"[watchlist-backtest] 回测异常: {e}")
    except Exception as e:
        logging.warning(f"[watchlist-backtest] 任务异常: {e}")


async def _auto_daily_report():
    """启动时自动检查并生成今日市场分析报告。"""
    import time
    try:
        # 等待服务完全启动
        await asyncio.sleep(5)
        # 检查今日是否已有报告
        today = time.strftime("%Y-%m-%d")
        from db import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT id FROM analysis_history WHERE agent_id = 1 AND date(created_at) = ? LIMIT 1",
            (today,)
        ).fetchone()
        conn.close()

        if row:
            logging.info(f"今日市场报告已存在 (id={row['id']})，跳过自动生成")
            return

        logging.info("今日市场报告不存在，后台自动生成中...")
        agent = get_analysis_agent(1)
        if not agent:
            logging.warning("市场日报分析师未配置，跳过自动生成")
            return

        # 创建异步任务记录，前端可轮询感知"正在生成中"
        from db import create_async_task, update_async_task
        auto_task_id = create_async_task("daily_report", caller="daily_report_auto")

        # ── 收集丰富数据上下文 ──
        # 1. 新闻
        news_context = ""
        try:
            from routers.dashboard.dashboard import get_hot_topics
            news_data = await get_hot_topics()
            news_list = news_data.get("news", [])[:8]
            news_context = "\n".join(
                f"- {n.get('title','')}（{n.get('source','')}）"
                for n in news_list if n.get('title')
            ) if news_list else "暂无新闻"
        except Exception as e:
            logging.warning(f"自动报告新闻检索失败: {e}")
            news_context = "暂无新闻"

        # 1.5 盈米 MCP 数据（市场温度 + 行情解读）
        yingmi_context = ""
        try:
            from mcp.yingmi_client import get_yingmi_client
            ym = get_yingmi_client()
            # 市场温度计 + 行情解读
            quotations = ym.call_tool_text("GetLatestQuotations")
            if quotations:
                yingmi_context = f"【盈米市场温度计及行情解读】\n{quotations[:2000]}"
        except Exception as e:
            logging.warning(f"盈米 MCP 数据获取失败: {e}")

        # 2. 市场全景（指数行情 + 板块涨跌 + 涨跌家数）
        market_context = "暂无行情数据"
        try:
            from services.market_data import get_market_overview
            overview = get_market_overview()
            market_lines = []
            # 主要指数
            if overview.get("indices"):
                market_lines.append("【主要指数】")
                for idx in overview["indices"]:
                    sign = "+" if idx["change_pct"] >= 0 else ""
                    market_lines.append(f"- {idx['name']}: {idx['price']}（{sign}{idx['change_pct']}%）成交{idx.get('volume_yi',0):.0f}亿")
            # 涨跌家数
            b = overview.get("breadth", {})
            if b.get("up"):
                market_lines.append(f"\n【涨跌统计】上涨{b['up']} / 下跌{b['down']} / 涨停{b.get('limit_up',0)} / 跌停{b.get('limit_down',0)} / 成交{b.get('total_volume_yi',0):.0f}亿")
            # 领涨板块
            if overview.get("sectors_top"):
                market_lines.append("\n【领涨板块】")
                for s in overview["sectors_top"]:
                    market_lines.append(f"- {s['name']}: +{s['change_pct']}%  领涨:{s['lead_stock']}{s['lead_change']}%")
            # 领跌板块
            if overview.get("sectors_bottom"):
                market_lines.append("\n【领跌板块】")
                for s in overview["sectors_bottom"]:
                    market_lines.append(f"- {s['name']}: {s['change_pct']}%  领涨:{s['lead_stock']}{s['lead_change']}%")
            market_context = "\n".join(market_lines) if market_lines else "暂无行情数据"
        except Exception as e:
            logging.warning(f"行情数据获取失败: {e}")

        # 3. 指数估值（分高估/低估两组展示）
        val_context = "暂无估值数据"
        try:
            from db import list_valuation_indexes
            indexes = list_valuation_indexes()
            seen = {}
            for i in indexes:
                code = i.get("index_code", "")
                if code and code not in seen:
                    seen[code] = i
            all_indexes = list(seen.values())
            if all_indexes:
                val_lines = []
                for i in all_indexes:
                    pct = i.get("percentile", None)
                    pct_str = f"{pct:.0f}%" if pct is not None else "N/A"
                    val_lines.append(
                        f"- {i['index_name']}（{i['index_code']}）: "
                        f"{i.get('metric_type','PE')}={i.get('current_value','?')}, 百分位={pct_str}"
                    )
                val_context = "\n".join(val_lines)
        except Exception:
            pass

        # 4. 持仓（按涨跌幅排序）
        holding_text = "暂无持仓"
        portfolio_text = "暂无"
        try:
            from db import list_holdings, get_portfolio_diversification, get_total_cash_balance
            holdings = list_holdings()
            div = get_portfolio_diversification()
            cash = {"balance": get_total_cash_balance()}
            if holdings:
                # 按收益率排序，方便 LLM 找出涨跌幅前3
                sorted_holdings = sorted(holdings, key=lambda x: x.get("profit_rate") or 0, reverse=True)
                holding_lines = []
                for h in sorted_holdings[:15]:
                    pct = h.get("profit_rate")
                    pct_str = f"{pct:+.1f}%" if pct is not None else "N/A"
                    val = h.get("current_value", 0) or 0
                    profit = h.get("profit", 0) or 0
                    holding_lines.append(
                        f"- {h['fund_name']}（{h.get('fund_code','')}）: "
                        f"市值{val:.0f}元, 收益率{pct_str}, 盈亏{profit:+.0f}元"
                    )
                holding_text = "\n".join(holding_lines)
            portfolio_text = (
                f"持仓{div.get('holding_count',0)}只基金，"
                f"总市值{div.get('total_value',0):.0f}元，"
                f"累计盈亏{div.get('total_profit',0):+.0f}元，"
                f"可用零钱{cash:.0f}元"
            )
        except Exception:
            pass

        # 5. 债市
        bond_text = "暂无"
        try:
            from tools import _get_bond_temperature
            bond_raw = json.loads(_get_bond_temperature())
            bond_text = f"债券温度{bond_raw.get('temperature','?')}°，收益率{bond_raw.get('rate','?')}%"
        except Exception:
            pass

        # ── 组装 prompt ──
        full_prompt = agent["system_prompt"] + f"""

【今日日期】
{time.strftime("%Y-%m-%d")}（{["周一","周二","周三","周四","周五","周六","周日"][time.localtime().tm_wday]}）

【今日新闻】
{news_context}

{yingmi_context}

【市场行情】
{market_context}

【指数估值】
{val_context}

【持仓明细】（已按收益率从高到低排序）
{holding_text}

【持仓概况】
{portfolio_text}

【债券市场】
{bond_text}

请按照报告结构要求，基于以上真实数据撰写今日市场简报。"""

        response = await asyncio.to_thread(lambda: _call_llm(
            caller="daily_report",
            model=MODEL,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": "请生成今日市场分析报告。"},
            ],
            temperature=get_config_float('llm.temperature_analysis', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8192),
        ))
        result_text = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0

        report_id = create_analysis_history(
            index_code="", index_name="",
            agent_id=1, agent_name=agent["name"],
            prompt_used=full_prompt[:500], news_context=news_context[:500],
            valuation_context=val_context[:500], result=result_text,
            token_usage=token_usage,
        )
        # 标记异步任务完成
        update_async_task(auto_task_id, status="done", result={"report_id": report_id, "token_usage": token_usage})
        logging.info(f"今日市场报告后台自动生成完成，token用量: {token_usage}")

        # 后台自动质量评估（LLM-as-Judge，默认关闭）
        if get_config("llm_cost.llm_judge_eval", "false") == "true":
            async def _auto_eval_report():
                try:
                    from agent.eval_scorer import evaluate_llm_output
                    await evaluate_llm_output(
                        query="生成今日市场简报",
                        output=result_text,
                        context=f"新闻: {news_context[:300]}\n估值: {val_context[:300]}",
                        target_type="daily_report",
                        target_id=report_id,
                    )
                except Exception as e:
                    logging.warning(f"简报自动质量评估失败: {e}")
            asyncio.create_task(_auto_eval_report())
        else:
            logging.info("简报自动质量评估已关闭（llm_cost.llm_judge_eval=false）")
    except Exception as e:
        logging.warning(f"自动生成市场报告失败: {e}")
        try:
            update_async_task(auto_task_id, status="error", error_msg=str(e))
        except Exception:
            pass


async def _auto_refresh_nav():
    """每天 20:00 自动确认到期交易并刷新所有持仓基金净值。"""
    import time
    while True:
        try:
            now = time.localtime()
            # 计算距离下一个 15:30 的秒数
            target = time.struct_time((
                now.tm_year, now.tm_mon, now.tm_mday,
                20, 0, 0,  # 20:00:00
                now.tm_wday, now.tm_yday, now.tm_isdst,
            ))
            target_ts = time.mktime(target)
            now_ts = time.mktime(now)
            wait = target_ts - now_ts
            if wait <= 0:
                # 已过今天 15:30，等到明天
                wait += 86400
            logging.info(f"[auto-nav] 下次净值自动刷新: {wait/3600:.1f} 小时后")
            await asyncio.sleep(wait)

            # 执行刷新
            logging.info("[auto-nav] 开始自动确认到期交易并刷新持仓净值...")
            from db.portfolio import auto_confirm_due_transactions, refresh_all_fund_prices
            try:
                auto_result = auto_confirm_due_transactions()
                logging.info(f"[auto-nav] 到期交易自动确认完成: {auto_result}")
            except Exception as ae:
                logging.warning(f"[auto-nav] 自动确认交易异常: {ae}")
            result = refresh_all_fund_prices()
            logging.info(f"[auto-nav] 净值刷新完成: {result}")

            # 净值刷新后自动保存持仓快照
            try:
                from db.portfolio import save_portfolio_snapshot
                save_portfolio_snapshot()
                logging.info("[auto-nav] 持仓快照已保存")
            except Exception as se:
                logging.warning(f"[auto-nav] 持仓快照保存异常: {se}")

            # 净值刷新后自动触发预警扫描
            try:
                from routers.portfolio.portfolio import scan_portfolio_alerts
                alert_result = await scan_portfolio_alerts()
                logging.info(f"[auto-nav] 预警扫描完成: {alert_result}")
            except Exception as ae:
                logging.warning(f"[auto-nav] 预警扫描异常: {ae}")

            # 预警扫描后自动运行每日持仓提示引擎
            try:
                from services.daily_position_advisor import run_daily_position_advice
                advice_result = run_daily_position_advice(
                    user_id="default",
                    trigger_type="auto_nav_refresh",
                    force=False,
                )
                logging.info(f"[auto-nav] 每日持仓提示完成: run_id={advice_result.get('run_id')}, summary={advice_result.get('summary', '')}")
            except Exception as de:
                logging.warning(f"[auto-nav] 每日持仓提示异常: {de}")
        except Exception as e:
            logging.warning(f"[auto-nav] 自动刷新净值异常: {e}")
            await asyncio.sleep(300)  # 出错后 5 分钟重试


# ── 请求模型 ──────────────────────────────────────────


class ChatRequest(BaseModel):
    question: str
    context: str = ""


def _build_valuation_context(question: str) -> str:
    """从用户问题中提取关键词，检索匹配的指数估值数据，格式化为上下文文本。"""
    # 获取所有已入库的指数名称，用反向匹配（指数名→问题）解决中文分词难题
    all_indexes = list_valuation_indexes()
    # 去重得到唯一指数列表
    unique_indexes = {}
    for idx in all_indexes:
        code = idx["index_code"]
        if code not in unique_indexes:
            unique_indexes[code] = idx["index_name"]

    # 用指数名称（或其核心部分，如"白酒"匹配"中证白酒"）在问题中做子串匹配
    _prefixes = ("中证", "国证", "沪", "深", "恒生")
    _middles = ("全指", "综指", "50", "100", "200", "300", "500", "800", "1000")
    seen_codes = set()
    matched_indexes = []
    for code, name in unique_indexes.items():
        # 优先匹配完整名称
        if name in question and code not in seen_codes:
            seen_codes.add(code)
            matched_indexes.append({"index_code": code, "index_name": name})
            continue
        # 去掉常见前缀后匹配
        for prefix in _prefixes:
            core = name.replace(prefix, "", 1)
            if len(core) >= 2 and core in question and code not in seen_codes:
                seen_codes.add(code)
                matched_indexes.append({"index_code": code, "index_name": name})
                break
        else:
            # 去掉前缀+中间词后匹配（如"中证全指半导体"→"半导体"）
            for prefix in _prefixes:
                core = name.replace(prefix, "", 1)
                for mid in _middles:
                    core2 = core.replace(mid, "", 1)
                    if len(core2) >= 2 and core2 in question and code not in seen_codes:
                        seen_codes.add(code)
                        matched_indexes.append({"index_code": code, "index_name": name})
                        break
                if code in seen_codes:
                    break

    if not matched_indexes:
        return ""

    # 全局数据新鲜度摘要
    from datetime import date as dt_date
    freshness = list_index_freshness()
    stale_summary = []
    for f in freshness:
        sd = f.get("stale_days")
        if sd is not None and sd >= 5:
            stale_summary.append(f"{f['index_name']}({sd:.0f}天)")
    if stale_summary:
        parts = [f"⚠️ 数据新鲜度警告: 以下指数估值数据超过5天未更新: {'; '.join(stale_summary[:6])}，分析时请注意数据可能滞后。"]
    else:
        parts = []

    # 查询每个匹配指数的最新估值和近期趋势
    for idx in matched_indexes:
        code = idx["index_code"]
        name = idx["index_name"]

        index_metrics = [i for i in all_indexes if i["index_code"] == code]
        if not index_metrics:
            continue

        lines = [f"【{name}（{code}）】"]
        for metric in index_metrics:
            mt = metric["metric_type"]
            latest = get_latest_valuation(code, mt)
            if not latest:
                continue

            val = latest.get("current_value")
            pct = latest.get("percentile")
            danger = latest.get("danger_value")
            opp = latest.get("opportunity_value")
            zscore = latest.get("zscore")
            date = latest.get("snapshot_date", "")

            # 估值水平描述
            level = ""
            if pct is not None:
                if pct < 30:
                    level = "低估"
                elif pct < 70:
                    level = "合理"
                else:
                    level = "高估"

            line = f"  {mt}: 当前值={val}, 分位点={pct}%({level}), 危险值={danger}, 机会值={opp}"
            if zscore is not None:
                line += f", z-score={zscore}"
            if date:
                line += f" [{date}]"
                # 数据新鲜度提示，超过10天标记为过期
                from datetime import date as dt_date
                try:
                    d = dt_date.fromisoformat(str(date))
                    stale_days = (dt_date.today() - d).days
                    if stale_days >= 10:
                        line += f" [数据已过期{stale_days}天]"
                except Exception:
                    pass
            lines.append(line)

            # 近5日趋势
            history = get_valuation_history(code, 5, mt)
            if len(history) >= 2:
                trend_vals = [str(h["current_value"]) for h in reversed(history)]
                lines.append(f"    近{len(history)}日趋势: {'→'.join(trend_vals)}")

        if len(lines) > 1:
            parts.append("\n".join(lines))

    return "\n\n".join(parts)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """自由问答，自动关联估值数据。"""
    valuation_context = _build_valuation_context(req.question)
    answer = chat_about_investment(req.question, req.context, valuation_context)
    return {"answer": answer}


@app.post("/api/rag/reindex")
async def reindex_rag():
    """重建 RAG 全文索引 + 向量索引。"""
    # 索引所有文章
    tasks = list_tasks(limit=500)
    article_count = 0
    for t in tasks:
        if t.get("content_text") or t.get("llm_analysis"):
            body = t.get("content_text", "") + "\n" + (t.get("llm_analysis", "") or "")
            index_article(t["id"], t.get("title", ""), body)
            index_to_chroma("article", str(t["id"]), t.get("title", ""), body[:5000])
            article_count += 1

    # 索引所有估值数据
    val_count = 0
    all_indexes = list_valuation_indexes()
    for idx in all_indexes:
        code = idx["index_code"]
        name = idx.get("index_name", code)
        latest = get_latest_valuation(code, idx.get("metric_type"))
        if latest:
            index_valuation(code, name, latest)
            val_count += 1

    # 索引作者文章
    author_count = 0
    author_articles = list_author_articles(status="done", limit=500)
    for a in author_articles:
        if a.get("content_text"):
            index_author_article(a["id"], a.get("title", ""), a["content_text"], a.get("publish_time", ""))
            index_to_chroma("author_article", str(a["id"]), a.get("title", ""), a["content_text"][:5000])
            author_count += 1

    # 索引 Skill 文档
    skill_doc_count = 0
    try:
        from db import _get_conn
        conn = _get_conn()
        skill_docs = conn.execute("SELECT * FROM skill_documents ORDER BY id DESC LIMIT 10").fetchall()
        for doc in skill_docs:
            index_skill_document(doc["id"], f"Skill文档-{doc['doc_type']}", doc["content"])
            index_to_chroma("skill", str(doc["id"]), f"Skill文档-{doc['doc_type']}", doc["content"][:8000])
            skill_doc_count += 1
        conn.close()
    except Exception:
        pass

    # 索引技能提取结果
    skill_count = 0
    try:
        from db import _get_conn
        conn = _get_conn()
        skills = conn.execute("""
            SELECT s.*, a.title FROM author_skills s
            JOIN author_articles a ON s.article_id = a.id
        """).fetchall()
        for s in skills:
            skill_data = {
                "cognitive_framework": json.loads(s["cognitive_framework"] or "[]"),
                "behavior_patterns": json.loads(s["behavior_patterns"] or "[]"),
                "knowledge_strengths": json.loads(s["knowledge_strengths"] or "[]"),
                "classic_quotes": json.loads(s["classic_quotes"] or "[]"),
            }
            index_skill_extraction(s["article_id"], s["title"], skill_data)
            skill_count += 1
        conn.close()
    except Exception:
        pass

    # 索引个人文档
    linked_count = 0
    try:
        linked_docs = list_linked_articles(limit=500)
        for doc in linked_docs:
            if not doc.get("file_path"):
                continue
            file_path = UPLOADS_DIR / doc["file_path"]
            if not file_path.exists():
                continue
            file_type = doc.get("file_type", "")
            content = ""
            try:
                if file_type in ("txt", "md"):
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                elif file_type == "pdf":
                    from PyPDF2 import PdfReader
                    reader = PdfReader(str(file_path))
                    pages = [p.extract_text() for p in reader.pages if p.extract_text()]
                    content = "\n\n".join(pages)
                elif file_type == "docx":
                    from docx import Document
                    d = Document(str(file_path))
                    content = "\n\n".join(p.text for p in d.paragraphs if p.text.strip())
            except Exception:
                continue
            if content:
                index_to_chroma("linked_doc", str(doc["id"]), doc.get("title", ""), content)
                linked_count += 1
    except Exception:
        pass

    return {
        "ok": True,
        "articles_indexed": article_count,
        "valuations_indexed": val_count,
        "author_articles_indexed": author_count,
        "skill_docs_indexed": skill_doc_count,
        "skills_indexed": skill_count,
        "linked_docs_indexed": linked_count,
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/proxy-image")
async def proxy_image(url: str):
    """代理图片请求，绕过微信防盗链。"""
    import httpx
    headers = {
        "Referer": "https://mp.weixin.qq.com/",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/537.36",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "图片获取失败")
        ct = resp.headers.get("content-type", "image/jpeg")
        return StreamingResponse(iter([resp.content]), media_type=ct)



@app.get("/api/gallery")
async def list_gallery_records(search: str = None, limit: int = 200):
    """图片浏览：列出所有分析记录，支持模糊搜索。"""
    return {"records": list_all_analysis_records(search, limit)}


# ── 基金信息查询 API ──────────────────────────────────────────


@app.get("/api/fund/lookup")
async def fund_lookup_api(code: str):
    """根据基金代码查询基本信息（名称、类型、跟踪标的等）。"""
    if not code.strip():
        raise HTTPException(400, "基金代码不能为空")
    info = lookup_fund_info(code.strip())
    if not info:
        raise HTTPException(404, f"未找到基金 {code} 的信息")
    return info


@app.get("/api/fund/holdings")
async def fund_holdings_api(code: str, year: str = None):
    """获取基金持仓详情（重仓股、债券持仓、资产配置、行业配置）。"""
    if not code.strip():
        raise HTTPException(400, "基金代码不能为空")
    result = get_fund_holdings(code.strip(), year)
    return result


@app.post("/api/valuations/fetch-recent")
async def fetch_recent_valuations():
    """自动抓取近期估值数据（从乐咕乐股）。"""
    from services.market_data import get_index_valuation
    from db import save_valuation
    from datetime import datetime

    # 常用指数列表（代码 -> 名称）
    indexes_to_fetch = [
        ("000300", "沪深300"),
        ("000905", "中证500"),
        ("000852", "中证1000"),
        ("399006", "创业板指"),
        ("000016", "上证50"),
        ("399303", "国证2000"),
    ]

    fetched = 0
    errors = []
    today = datetime.now().strftime("%Y-%m-%d")

    for code, name in indexes_to_fetch:
        try:
            val = get_index_valuation(code)
            if val and val.get("pe_percentile") is not None:
                # 确定指数代码后缀
                full_code = code
                if code.startswith("399"):
                    full_code = f"{code}.SZ"
                elif code.startswith("000"):
                    full_code = f"{code}.SH"

                save_valuation({
                    "index_code": full_code,
                    "index_name": val.get("index_name", name),
                    "metric_type": "市盈率",
                    "current_value": val.get("pe"),
                    "percentile": val.get("pe_percentile"),
                    "snapshot_date": today,
                })
                fetched += 1
        except Exception as e:
            errors.append(f"{name}: {str(e)}")

    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return {"ok": True, "fetched": fetched, "errors": errors, "checked_at": checked_at}


# ── Shadow Mode API ──────────────────────────────────


@app.get("/api/shadow/configs")
async def list_shadow_configs_api(active_only: bool = True):
    """列出 Shadow 配置。"""
    from infra.shadow_mode import list_shadow_configs
    return {"configs": list_shadow_configs(active_only=active_only)}


@app.post("/api/shadow/configs")
async def create_shadow_config_api(body: dict):
    """创建 Shadow 配置。"""
    from infra.shadow_mode import create_shadow_config
    config_id = create_shadow_config(
        name=body.get("name", "未命名"),
        agent_type=body.get("agent_type", "ai"),
        current_prompt=body.get("current_prompt", ""),
        candidate_prompt=body.get("candidate_prompt", ""),
        traffic_pct=body.get("traffic_pct", 0.1),
    )
    return {"ok": True, "id": config_id}


@app.post("/api/shadow/configs/{config_id}/toggle")
async def toggle_shadow_config_api(config_id: int, body: dict):
    """启用/禁用 Shadow 配置。"""
    from infra.shadow_mode import toggle_shadow_config
    toggle_shadow_config(config_id, body.get("is_active", True))
    return {"ok": True}


@app.delete("/api/shadow/configs/{config_id}")
async def delete_shadow_config_api(config_id: int):
    """删除 Shadow 配置。"""
    from infra.shadow_mode import delete_shadow_config
    delete_shadow_config(config_id)
    return {"ok": True}


@app.get("/api/shadow/runs")
async def list_shadow_runs_api(config_id: int = None, limit: int = 100):
    """列出 Shadow 执行记录。"""
    from infra.shadow_mode import list_shadow_runs
    return {"runs": list_shadow_runs(config_id=config_id, limit=limit)}


@app.get("/api/shadow/stats")
async def get_shadow_stats_api(config_id: int = None):
    """获取 Shadow Mode 统计信息。"""
    from infra.shadow_mode import get_shadow_stats
    return get_shadow_stats(config_id=config_id)


# ── 前端页面 ──────────────────────────────────────────


@app.get("/")
async def root():
    """根路径重定向到应用页面。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/app")


@app.get("/app", response_class=HTMLResponse)
async def app_page():
    """Web 管理页面。"""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(
            index_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return HTMLResponse("<h1>前端文件未找到</h1><p>请创建 static/index.html</p>")


@app.get("/favicon.svg", include_in_schema=False)
async def _serve_favicon():
    return FileResponse(str(STATIC_DIR / "favicon.svg"))


@app.get("/icons.svg", include_in_schema=False)
async def _serve_icons():
    return FileResponse(str(STATIC_DIR / "icons.svg"))


@app.get("/sw.js", include_in_schema=False)
async def _serve_sw():
    return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript")


@app.get("/manifest.json", include_in_schema=False)
async def _serve_manifest():
    return FileResponse(str(STATIC_DIR / "manifest.json"), media_type="application/manifest+json")


@app.get("/api/finance/quote-bar")
async def finance_quote_bar():
    """每日理财箴言 + 市场热点。

    P2-1 修复：akshare 调用用 asyncio.to_thread 包装 + 30s 缓存 + 3s 超时降级。
    根因：原实现把同步的 ak.stock_hot_keyword_em() 直接放在 async def 中，
    阻塞 FastAPI 事件循环 1-5 秒，导致刷新页面时所有请求排队卡顿。
    """
    import asyncio
    import random
    from datetime import date, datetime, timedelta

    quotes = [
        "别人贪婪时恐惧，别人恐惧时贪婪。—— 巴菲特",
        "投资中最重要的是：不要亏损。—— 巴菲特",
        "复利是世界第八大奇迹。—— 爱因斯坦",
        "退潮时才知道谁在裸泳。—— 巴菲特",
        "投资不是比谁聪明，而是比谁更有耐心。",
        "不要把鸡蛋放在同一个篮子里。",
        "最好的买入时机是昨天，其次是今天。",
        "资产配置决定了投资回报的 90% 以上。",
        "种一棵树最好的时间是十年前，其次是现在。",
        "市场下跌时买入，需要勇气；上涨时持有，需要定力。",
        "风险永远和收益成正比，警惕高收益诱惑。",
        "投资不是为了暴富，而是为了让生活更安心。",
        "定投的魅力在于：摊低成本，积少成多。",
        "不要试图预测市场，要适应市场。",
        "投资最大的成本不是手续费，是无知。",
        "市场永远在波动，情绪稳定才是最大的优势。",
        "买你了解的东西，不懂不投。",
        "收益是时间的函数，不是操作频率的函数。",
        "优质资产拿得住，远比频繁交易更重要。",
        "今晚的下跌，是为了明天的上涨留空间。",
        "在别人悲观时买入，在别人乐观时卖出。",
        "耐心是投资者最好的朋友。—— 格雷厄姆",
        "价格是你支付的，价值是你得到的。—— 巴菲特",
        "市场短期是投票机，长期是称重机。—— 格雷厄姆",
        "永远不要投资你输不起的钱。",
    ]
    # 随机选择一条（每次刷新不同）
    daily_quote = random.choice(quotes)
    today = date.today().isoformat()

    # P2-1：市场热点（akshare）— 异步化 + 缓存 + 超时降级
    # 用模块级缓存避免高频刷新时重复调用 akshare
    global _quote_bar_cache
    now = datetime.now()
    if _quote_bar_cache["hot_keywords"] is not None and _quote_bar_cache["expires_at"] > now:
        return {"date": today, "quote": daily_quote, "hot_keywords": _quote_bar_cache["hot_keywords"]}

    hot_keywords = []
    try:
        import akshare as ak

        def _fetch_hot_keywords():
            """同步函数：调用 akshare 获取热点关键词。"""
            df = ak.stock_hot_keyword_em()
            seen = set()
            result = []
            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    kw = row.get("概念名称", "")
                    if kw and kw not in seen and len(seen) < 8:
                        seen.add(kw)
                        result.append(kw)
            return result

        # 用 asyncio.to_thread 包装同步调用 + 3s 超时
        # 这是不阻塞事件循环的关键
        hot_keywords = await asyncio.wait_for(
            asyncio.to_thread(_fetch_hot_keywords),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        logger.warning("[quote-bar] akshare 超时 3s，降级返回空 hot_keywords")
    except Exception as e:
        logger.warning(f"[quote-bar] akshare 失败: {e}")

    # 写缓存（30s TTL，避免高频刷新重复调用 akshare）
    _quote_bar_cache["hot_keywords"] = hot_keywords
    _quote_bar_cache["expires_at"] = now + timedelta(seconds=30)

    return {"date": today, "quote": daily_quote, "hot_keywords": hot_keywords}


@app.post("/api/admin/backup")
async def trigger_backup():
    """手动触发数据库备份。"""
    from db.backup import backup_database, list_backups
    path = backup_database()
    if path:
        return {"ok": True, "path": path, "backups": list_backups()}
    return {"ok": False, "error": "备份失败"}


@app.get("/api/admin/backups")
async def list_backups_api():
    """查看备份列表。"""
    from db.backup import list_backups
    return {"backups": list_backups()}


# 升级1: 工具管理 API
@app.get("/api/admin/tools")
async def list_tools_api():
    """列出所有可用工具。"""
    try:
        from tools.tool_registry import ToolRegistry
        registry = ToolRegistry.get_instance()
        tools = registry.list_tool_names()
        return {"tools": tools, "total": len(tools)}
    except Exception as e:
        return {"tools": [], "total": 0, "error": str(e)}


@app.get("/api/admin/agents/{agent_key}/tools")
async def get_agent_tools_api(agent_key: str):
    """获取指定 agent 的工具配置。"""
    from db.agents import get_agent_tools
    try:
        from tools.tool_registry import ToolRegistry
        registry = ToolRegistry.get_instance()
        all_tools = registry.list_tool_names()
    except Exception:
        all_tools = []
    tools = get_agent_tools(agent_key)
    return {"agent_key": agent_key, "tools": tools, "available_tools": all_tools}


@app.put("/api/admin/agents/{agent_key}/tools")
async def update_agent_tools_api(agent_key: str, tools: list[str] = Body(...)):
    """更新 agent 的工具配置。"""
    from db.agents import update_agent_tools
    try:
        from tools.tool_registry import ToolRegistry
        registry = ToolRegistry.get_instance()
        valid_tools = set(registry.list_tool_names())
        invalid = [t for t in tools if t not in valid_tools]
        if invalid:
            raise HTTPException(400, f"无效工具名: {invalid}")
    except RuntimeError:
        pass  # ToolRegistry 未初始化，跳过校验
    update_agent_tools(agent_key, tools)
    return {"ok": True, "agent_key": agent_key, "tools": tools}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
