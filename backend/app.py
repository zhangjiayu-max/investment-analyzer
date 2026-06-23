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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import STATIC_DIR, IMAGES_DIR, OUTPUT_DIR, UPLOADS_DIR, DD_IMAGES_DIR, VALUATION_IMAGES_DIR

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
    get_config_int,
)
from market_data import get_index_valuation
from llm_service import chat_about_investment, _call_llm, MODEL
from rag import init_fts, init_chroma, index_article, index_valuation, build_rag_context_with_details, index_author_article, index_skill_document, index_skill_extraction, index_to_chroma, _get_chroma, _get_embed_model

app = FastAPI(title="投资分析助手", version="0.4.0")

# 全局状态

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理 — 返回 JSON 而非 HTML 500。"""
    import traceback
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": str(exc)})

# ── 路由注册 ─────────────────────────────────────

from routers.valuation import router as valuation_router, index_info_router  # /api/valuation/* + /api/index-info/*
from routers.agents import router as agents_router
from routers.conversations import router as conversations_router
from routers.tasks import router as tasks_router
from routers.articles import router as articles_router
from routers.portfolio import router as portfolio_router

# 其他路由
from routers.bond import router as bond_router
from routers.token_usage import router as token_usage_router
from routers.images import router as images_router
from routers.eval import router as eval_router
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
)
from routers.dashboard import router as dashboard_router
from routers.config import router as config_router
from routers.rag import router as rag_router
# market_intelligence routes moved to routers/analysis/market_intel.py
from routers.knowledge import router as knowledge_router
from routers.watchlist import router as watchlist_router
from routers.profile import router as profile_router                    # /api/profile/*
from routers.async_tasks import router as async_tasks_router           # /api/async-tasks/*
from routers.search import router as search_router                     # /api/search/*
from routers.decisions import router as decisions_router               # /api/decisions/*
from routers.finance_dashboard import router as finance_dashboard_router  # /api/finance-dashboard
from routers.strategy_sandbox import router as strategy_sandbox_router  # /api/strategy-sandbox
from routers.fund_manager import router as fund_manager_router  # /api/fund-manager
from routers.data_health import router as data_health_router          # /api/data-health
from routers.portfolio_import import router as portfolio_import_router  # /api/portfolio/import-csv
from routers.opportunities import router as opportunities_router      # /api/opportunities/*
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

# 静态文件目录
for _d in (STATIC_DIR, IMAGES_DIR, OUTPUT_DIR, UPLOADS_DIR, DD_IMAGES_DIR, VALUATION_IMAGES_DIR):
    _d.mkdir(parents=True, exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(IMAGES_DIR)), name="article_images")
app.mount("/static/tasks", StaticFiles(directory=str(OUTPUT_DIR)), name="task_images")
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="frontend_assets")
app.mount("/static/dd_images", StaticFiles(directory=str(DD_IMAGES_DIR)), name="dd_images")
app.mount("/static/valuation_images", StaticFiles(directory=str(VALUATION_IMAGES_DIR)), name="valuation_images")


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


@app.on_event("startup")
async def startup():
    """应用启动时的初始化。"""
    logging.info("=== 启动初始化开始 ===")

    # 1. 同步初始化（必须在启动前完成）
    logging.info("初始化数据库...")
    init_db()
    logging.info("数据库初始化完成")

    # Shadow Mode 表初始化
    try:
        from shadow_mode import init_shadow_db
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
            from rag import _ensure_embed_model
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

    # 后台每日报告
    asyncio.create_task(_auto_daily_report())

    # 后台每日净值自动刷新（15:30 收盘后）
    asyncio.create_task(_auto_refresh_nav())
    logging.info("=== 启动初始化完成 ===")


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
            from routers.dashboard import get_hot_topics
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
            from market_data import get_market_overview
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

        # 后台自动质量评估
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
                from routers.portfolio import scan_portfolio_alerts
                alert_result = await scan_portfolio_alerts()
                logging.info(f"[auto-nav] 预警扫描完成: {alert_result}")
            except Exception as ae:
                logging.warning(f"[auto-nav] 预警扫描异常: {ae}")
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
                except:
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
    from market_data import get_index_valuation
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
    from shadow_mode import list_shadow_configs
    return {"configs": list_shadow_configs(active_only=active_only)}


@app.post("/api/shadow/configs")
async def create_shadow_config_api(body: dict):
    """创建 Shadow 配置。"""
    from shadow_mode import create_shadow_config
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
    from shadow_mode import toggle_shadow_config
    toggle_shadow_config(config_id, body.get("is_active", True))
    return {"ok": True}


@app.delete("/api/shadow/configs/{config_id}")
async def delete_shadow_config_api(config_id: int):
    """删除 Shadow 配置。"""
    from shadow_mode import delete_shadow_config
    delete_shadow_config(config_id)
    return {"ok": True}


@app.get("/api/shadow/runs")
async def list_shadow_runs_api(config_id: int = None, limit: int = 100):
    """列出 Shadow 执行记录。"""
    from shadow_mode import list_shadow_runs
    return {"runs": list_shadow_runs(config_id=config_id, limit=limit)}


@app.get("/api/shadow/stats")
async def get_shadow_stats_api(config_id: int = None):
    """获取 Shadow Mode 统计信息。"""
    from shadow_mode import get_shadow_stats
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
    """每日理财箴言 + 市场热点。"""
    from datetime import date
    import random
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

    # 市场热点（akshare）
    hot_keywords = []
    try:
        import akshare as ak
        df = ak.stock_hot_keyword_em()
        seen = set()
        for _, row in df.iterrows():
            kw = row.get("概念名称", "")
            if kw and kw not in seen and len(seen) < 8:
                seen.add(kw)
                hot_keywords.append(kw)
    except Exception:
        pass

    return {"date": today, "quote": daily_quote, "hot_keywords": hot_keywords}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
