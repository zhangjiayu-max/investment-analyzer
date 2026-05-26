"""投资分析助手 — FastAPI 后端"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.parent

from db import (
    init_db, create_task, update_task, get_task, list_tasks, delete_task,
    save_valuation, get_valuation_history, get_latest_valuation, list_valuation_indexes,
    list_index_freshness,
    sync_articles, list_articles, get_article, get_article_by_seq, get_article_by_url, create_article,
    update_article, create_analysis_record, update_analysis_record,
    get_analysis_records, get_analysis_record, get_valuation_by_image,
    list_all_analysis_records,
    list_agents, get_agent, create_agent as db_create_agent, update_agent, delete_agent,
    list_conversations, get_conversation, create_conversation, update_conversation, delete_conversation,
    get_messages, create_message,
    create_author_article, update_author_article, get_author_article_by_url,
    list_author_articles, get_author_article, delete_author_article, count_author_articles,
    create_linked_article, list_linked_articles, get_linked_article, delete_linked_article,
    update_linked_article_file, update_linked_article_embed_status, save_document_chunks, get_document_chunks,
    create_holding, get_holding, list_holdings, update_holding, delete_holding, get_portfolio_summary,
    create_transaction, list_transactions, confirm_transaction, settle_transaction, delete_transaction,
    refresh_holding_price, refresh_all_fund_prices, fetch_fund_nav,
    lookup_fund_info, get_fund_holdings,
    get_portfolio_diversification, get_transaction_summary, clear_all_portfolio_data,
    get_cash_balance, add_cash,
    get_fund_nav_history,
    create_alert, list_alerts, get_unread_alert_count, mark_alert_read, delete_alert,
    add_transaction_tag, remove_transaction_tag, get_transaction_tags,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    get_portfolio_analysis_record, delete_portfolio_analysis_record,
    update_analysis_feedback, list_bad_cases,
    list_analysis_agents, get_analysis_agent, update_analysis_agent,
    create_analysis_history, list_analysis_history, get_analysis_history_item, delete_analysis_history,
    get_index_info, save_index_info, search_indexes_by_keyword,
    save_prompt_version, list_prompt_versions, get_prompt_version,
    list_token_usage, get_token_usage_summary, get_token_usage_by_caller, get_token_usage_daily,
    count_token_usage,
    get_performance_stats, get_performance_by_agent,
    create_eval_case, list_eval_cases, get_eval_case, update_eval_case, delete_eval_case,
    create_eval_run, list_eval_runs, get_eval_run_detail, get_eval_stats,
    DEFAULT_BOND_PROMPT,
)
from article_reader import fetch_article, download_images, extract_stock_codes
from market_data import get_stock_info, get_index_valuation
from valuation import analyze_stock, analyze_fund
from llm_service import (analyze_article, analyze_article_stream, chat_about_investment,
                         analyze_images_batch, chat_with_agent, chat_with_tools, ORCHESTRATOR_PROMPT,
                         _call_llm, MODEL, _record_token_usage)
from agent.orchestrator import orchestrate, orchestrate_stream, clarify_requirement
from agent.multi_agent import run_specialist
from image_parser import ImageParser
from mcp.trading_calendar import expected_confirm_date
from rag import init_fts, init_chroma, index_article, index_valuation, index_analysis_record, build_rag_context, build_rag_context_with_details, log_rag_search, index_author_article, index_skill_document, index_skill_extraction, index_to_chroma, _get_chroma, _get_embed_model

app = FastAPI(title="投资分析助手", version="0.4.0")

# 后台分析任务进度跟踪
_analyze_progress: dict[int, dict] = {}
_analyze_cancel: set[int] = set()  # 被用户请求取消的 article_id
_analyze_tasks: dict[int, asyncio.Task] = {}  # 当前正在执行的图片分析 Task
_reanalyze_tasks: dict[int, asyncio.Task] = {}  # 单张图片重新分析 Task
_vision_semaphore = asyncio.Semaphore(3)  # 限制并发 vision API 调用数

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件目录
STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR = ROOT / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(IMAGES_DIR)), name="article_images")
OUTPUT_DIR = ROOT / "output" / "tasks"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/tasks", StaticFiles(directory=str(OUTPUT_DIR)), name="task_images")
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="frontend_assets")
UPLOADS_DIR = ROOT / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
async def startup():
    init_db()
    init_fts()
    init_chroma()
    # 种子债券知识库
    try:
        from db import seed_bond_knowledge
        if seed_bond_knowledge():
            logging.info("债券知识库已写入 skill_documents")
    except Exception:
        pass
    # 启动后台每日分析任务
    asyncio.create_task(_auto_daily_report())


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

        # 获取新闻
        news_context = ""
        try:
            from tools import execute_tool
            news_result = execute_tool("web_search", {"query": "A股 今日行情 板块 热点", "max_results": 5})
            news_context = news_result if news_result else ""
        except Exception as e:
            logging.warning(f"自动报告新闻检索失败: {e}")

        full_prompt = agent["system_prompt"]
        if news_context:
            full_prompt += f"\n\n<latest_news>\n最新财经新闻：\n{news_context}\n</latest_news>"

        response = await asyncio.to_thread(lambda: _call_llm(
            caller="daily_report",
            model=MODEL,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": "请生成今日市场分析报告。"},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0

        create_analysis_history(
            index_code="", index_name="",
            agent_id=1, agent_name=agent["name"],
            prompt_used=full_prompt, news_context=news_context,
            valuation_context="", result=result_text,
            token_usage=token_usage,
        )
        logging.info(f"今日市场报告后台自动生成完成，token用量: {token_usage}")
    except Exception as e:
        logging.warning(f"自动生成市场报告失败: {e}")


# ── 请求模型 ──────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    url: str


class ChatRequest(BaseModel):
    question: str
    context: str = ""


# ── 任务 API ──────────────────────────────────────────

@app.post("/api/tasks")
async def create_task_api(req: CreateTaskRequest):
    """创建任务，后台异步执行抓取+分析。"""
    task_id = create_task(req.url)
    # 后台启动异步任务
    asyncio.create_task(_run_task(task_id, req.url))
    return {"task_id": task_id, "status": "pending"}


@app.get("/api/tasks")
async def list_tasks_api(limit: int = 50):
    """任务列表。"""
    return {"tasks": list_tasks(limit)}


@app.get("/api/tasks/{task_id}")
async def get_task_api(task_id: int):
    """任务详情。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@app.delete("/api/tasks/{task_id}")
async def delete_task_api(task_id: int):
    """删除任务。"""
    if not delete_task(task_id):
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


@app.get("/api/tasks/{task_id}/images")
async def get_task_images(task_id: int):
    """获取任务图片列表（本地路径 + URL）。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    local_images = task.get("local_images") or []
    images = []
    for path in local_images:
        filename = Path(path).name
        images.append({
            "local_path": path,
            "url": f"/static/tasks/{task_id}/images/{filename}",
        })
    return {"images": images}


# ── 任务执行逻辑 ──────────────────────────────────────

async def _run_task(task_id: int, url: str):
    """后台异步执行：抓取 → 下载图片 → 提取代码 → 分析。"""
    try:
        # 1. 抓取文章
        update_task(task_id, status="fetching")
        article = await fetch_article(url)

        update_task(task_id,
            title=article["title"],
            author=article["author"],
            publish_time=article["publish_time"],
            content_text=article["content_text"],
        )

        # 2. 下载图片
        images_dir = str(OUTPUT_DIR / str(task_id) / "images")
        local_images = await download_images(article["images"], images_dir)
        update_task(task_id, images_dir=images_dir, local_images=local_images)

        # 3. 提取代码 + 行情分析
        update_task(task_id, status="analyzing")
        codes = extract_stock_codes(article["content_text"])

        market_summary = {}
        for code in codes[:5]:
            try:
                info = get_stock_info(code)
                analysis = analyze_stock(code)
                market_summary[code] = {
                    "name": info.get("name", ""),
                    "pe": info.get("pe"),
                    "pb": info.get("pb"),
                    "recommendation": analysis.get("recommendation", ""),
                }
            except Exception as e:
                market_summary[code] = {"error": str(e)}

        update_task(task_id,
            codes_found=codes,
            market_data=market_summary,
        )

        # 4. LLM 分析
        llm_result = analyze_article(
            title=article["title"],
            content=article["content_text"],
            market_data=json.dumps(market_summary, ensure_ascii=False, indent=2) if market_summary else None,
        )

        update_task(task_id,
            llm_analysis=llm_result,
            status="done",
        )

    except Exception as e:
        update_task(task_id, status="error", error_msg=str(e))


# ── 兼容旧接口 ──────────────────────────────────────

@app.post("/api/analyze")
async def analyze_compat(req: CreateTaskRequest):
    """兼容旧接口，创建任务并等待完成返回结果。"""
    task_id = create_task(req.url)
    await _run_task(task_id, req.url)
    return get_task(task_id)


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


@app.post("/api/tasks/{task_id}/analyze-images")
async def analyze_task_images(task_id: int):
    """分析任务中的所有图片，提取结构化数据。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")

    local_images = task.get("local_images") or []
    if not local_images:
        raise HTTPException(400, "该任务没有图片")

    results = analyze_images_batch(local_images)
    return {"results": results}


@app.post("/api/analyze-image")
async def analyze_single_image(body: dict):
    """分析单张图片（传本地路径）。"""
    path = body.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(400, "图片路径无效")
    parser = ImageParser(model_type="mimo")
    result = parser.parse(path)
    return result


# ── 估值数据 API ──────────────────────────────────────


class ParseAndSaveRequest(BaseModel):
    path: str
    model_type: str = "mimo"  # "mimo" 或 "deepseek"
    source_url: str | None = None  # 来源文章链接
    snapshot_date: str | None = None  # 可选，默认今天


@app.post("/api/valuations/parse")
async def parse_and_save(req: ParseAndSaveRequest):
    """解析图片并存储估值数据。"""
    if not req.path or not Path(req.path).exists():
        raise HTTPException(400, "图片路径无效")

    parser = ImageParser(model_type=req.model_type)
    result = parser.parse(req.path)

    # 存入数据库
    valuation_id = save_valuation(result, source_image=req.path, source_url=req.source_url, snapshot_date=req.snapshot_date)
    result["id"] = valuation_id
    return result


@app.get("/api/valuations")
async def list_indexes():
    """列出所有有估值数据的指数。"""
    return {"indexes": list_valuation_indexes()}


@app.get("/api/valuations/freshness")
async def index_freshness():
    """所有估值指数的数据新鲜度。"""
    return {"indexes": list_index_freshness()}


@app.post("/api/valuations/refresh-prices")
async def refresh_index_prices():
    """用实时行情刷新指数最新价（仅更新 current_point/change_pct，估值百分位不变）。"""
    from datetime import date
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    import akshare as ak
    from db import _get_conn

    freshness = list_index_freshness()
    updated = 0
    errors = []
    today = date.today().isoformat()

    try:
        spot_df = ak.stock_zh_index_spot_sina()
        conn = _get_conn()
        for idx in freshness:
            code = idx["index_code"]
            base = code.replace(".SZ", "").replace(".SH", "").replace(".CSI", "")
            for prefix in ["sh", "sz"]:
                sina_code = f"{prefix}{base}"
                match = spot_df[spot_df["代码"] == sina_code]
                if not match.empty:
                    row = match.iloc[0]
                    current_point = float(row["最新价"])
                    change_pct = float(row["涨跌幅"])
                    conn.execute("""
                        UPDATE index_valuations
                        SET current_point = ?, change_pct = ?
                        WHERE index_code = ? AND snapshot_date = (
                            SELECT MAX(snapshot_date) FROM index_valuations WHERE index_code = ?
                        )
                    """, (current_point, change_pct, code, code))
                    updated += 1
                    break
        conn.commit()
        conn.close()
    except Exception as e:
        errors.append(f"实时行情刷新失败: {e}")

    return {"ok": True, "updated": updated, "errors": errors}


@app.get("/api/valuations/{index_code}")
async def get_history(index_code: str, days: int = 30, metric_type: str = None):
    """查询某指数的估值历史。"""
    history = get_valuation_history(index_code, days, metric_type)
    latest = get_latest_valuation(index_code, metric_type)
    return {
        "index_code": index_code,
        "latest": latest,
        "history": history,
    }


# ── 常见指数简介（静态字典，兜底用）────────────────────
INDEX_INFO_DICT = {
    "000300": "沪深300指数由沪深A股中规模大、流动性好的最具代表性的300只股票组成，于2005年4月8日正式发布，以综合反映沪深A股市场整体表现。沪深300是A股市场最核心的宽基指数，常被用作业绩基准和指数基金跟踪标的。",
    "000905": "中证500指数由A股中剔除沪深300指数成份股后，总市值排名靠前的500只股票组成，反映中小市值上市公司的整体表现。与沪深300形成互补，是衡量中小盘股走势的重要指标。",
    "000016": "上证50指数由沪市A股中规模大、流动性好的最具代表性的50只股票组成，主要覆盖金融、能源等大盘蓝筹股，反映沪市最具影响力的一批龙头企业的整体表现。",
    "399006": "创业板指由创业板中市值大、流动性好的100只股票组成，代表创业板市场核心资产。创业板以成长型创新企业为主，集中在新能源、医药、TMT等高景气赛道。",
    "399001": "深证成指从深交所上市股票中选取500只代表性的股票作为样本，覆盖主板、中小板和创业板，是衡量深市整体表现的核心指标。",
    "000688": "科创50指数由科创板中市值大、流动性好的50只股票组成，集中体现科创板龙头企业的表现，行业以半导体、生物医药、新能源等硬科技为主。",
    "000852": "中证1000指数由A股中剔除沪深300和中证500成份股后，规模偏小且流动性好的1000只股票组成，是小盘股的代表性指数。",
    "399303": "国证2000指数由深交所市值排名1001-3000的股票组成，覆盖小盘股，是比中证1000更下沉的小盘股指数。",
    "000015": "红利指数由沪市A股中现金股息率高、分红稳定、具有一定规模及流动性的50只股票组成，反映高红利股票的整体表现，适合追求稳定分红收益的投资者。",
    "931009": "中证转债指数选取沪深交易所上市的可转换公司债券作为样本，反映可转债市场的整体表现。可转债兼具债券和股票特性，是攻守兼备的投资品种。",
    "HSI": "恒生指数由港交所上市的市值最大及成交最活跃的50只股票组成，是香港股市最重要的指标，涵盖金融、地产、科技等核心行业。",
    "HSTECH": "恒生科技指数由港交所上市的30只最大型科技企业股票组成，涵盖互联网、软件、半导体、消费电子等领域，被称为'港版纳斯达克'。",
}


@app.get("/api/index-info/{index_code}")
async def get_index_info_api(index_code: str, index_name: str = ""):
    """获取指数简介信息。优先查缓存（5天有效），其次查静态字典，最后用 LLM 生成。"""
    # 1. 查缓存（5天过期）
    cached = get_index_info(index_code)
    if cached:
        from datetime import datetime, timedelta
        try:
            created = datetime.strptime(cached["created_at"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() - created < timedelta(days=5):
                return {"index_code": index_code, "info": cached["info"], "source": "cache"}
        except (ValueError, KeyError):
            pass  # 解析失败则视为过期，继续重新获取

    # 2. 静态字典（去掉 .SH/.SZ 等后缀再匹配）
    clean_code = index_code.split('.')[0] if '.' in index_code else index_code
    lookup_code = clean_code if clean_code in INDEX_INFO_DICT else index_code
    if lookup_code in INDEX_INFO_DICT:
        info = INDEX_INFO_DICT[lookup_code]
        save_index_info(index_code, index_name, info)
        return {"index_code": index_code, "info": info, "source": "dict"}

    # 3. LLM 生成
    try:
        prompt = f"请用2-3句话简洁介绍「{index_name or index_code}」这个股票指数，包括它由哪些股票组成、覆盖什么行业、适合什么样的投资者。不要使用markdown格式，直接输出纯文本。"
        resp = _call_llm(messages=[{"role": "user", "content": prompt}], model=MODEL, max_tokens=800)
        info = resp.choices[0].message.content if resp and resp.choices else ""
        if info:
            save_index_info(index_code, index_name, info.strip())
            return {"index_code": index_code, "info": info.strip(), "source": "llm"}
    except Exception as e:
        logging.warning(f"LLM生成指数信息失败: {e}")

    return {"index_code": index_code, "info": "", "source": "none"}


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
            index_author_article(a["id"], a.get("title", ""), a["content_text"])
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


# ══════════════════════════════════════════════════════
# Agent 对话系统 API
# ══════════════════════════════════════════════════════

class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    system_prompt: str
    knowledge_scope: str = ""
    icon: str = "robot"


class CreateConversationRequest(BaseModel):
    title: str = "新对话"
    agent_id: int
    context_data: str = None


class SendMessageRequest(BaseModel):
    content: str


@app.get("/api/agents")
async def list_agents_api():
    """列出所有 Agent。"""
    return {"agents": list_agents()}


@app.post("/api/agents")
async def create_agent_api(req: CreateAgentRequest):
    """创建自定义 Agent。"""
    agent_id = db_create_agent(
        name=req.name, system_prompt=req.system_prompt,
        description=req.description, knowledge_scope=req.knowledge_scope, icon=req.icon,
    )
    return {"ok": True, "agent_id": agent_id}


@app.get("/api/agents/{agent_id}")
async def get_agent_api(agent_id: int):
    """获取单个 Agent 详情。"""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    return agent


class UpdateAgentRequest(BaseModel):
    name: str = None
    description: str = None
    system_prompt: str = None
    knowledge_scope: str = None
    icon: str = None


@app.put("/api/agents/{agent_id}")
async def update_agent_api(agent_id: int, req: UpdateAgentRequest):
    """更新 Agent 信息。修改提示词时自动保存版本历史。"""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    # 提示词变更前，保存当前版本
    if 'system_prompt' in fields and fields['system_prompt'] != agent.get('system_prompt'):
        save_prompt_version(agent_id, 'conversation', agent['system_prompt'])
    if fields:
        update_agent(agent_id, **fields)
    return {"ok": True}


@app.delete("/api/agents/{agent_id}")
async def delete_agent_api(agent_id: int):
    """删除自定义 Agent（预设 Agent 不可删除）。"""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    if agent.get("is_preset"):
        raise HTTPException(400, "预设 Agent 不可删除")
    delete_agent(agent_id)
    return {"ok": True}


class GeneratePromptRequest(BaseModel):
    name: str = ""
    description: str = ""
    current_prompt: str = ""
    mode: str = "optimize"  # "optimize" 或 "generate"

# 可用工具列表（供 AI 生成提示词时参考）
AVAILABLE_TOOLS_DESC = """
- query_valuation: 查询指定指数的估值数据（PE、PB、百分位、z-score）
- search_knowledge: 从知识库检索相关文章、分析记录
- get_bond_temperature: 获取当前债市温度
- get_valuation_list: 获取所有指数估值概览，可筛选低估/高估
- calculate_metrics: 计算投资指标（定投收益率、年化收益、最大回撤、风险等级）
- web_search: 搜索最新财经新闻和市场资讯
- query_portfolio: 查询用户持仓信息
- query_fund_info: 查询基金详细信息
"""

PROMPT_GENERATOR_META = """你是一位 AI 提示词工程专家，专门为投资分析领域的 Agent 编写系统提示词。

## 编写规范
一个高质量的 Agent 提示词必须包含以下部分：

1. **人设**：清晰的角色定义（经验年限、专注领域、专业背景）
2. **分析框架**：具体的方法论，带数值阈值（如"百分位 <20% 为深度低估"）
3. **输出规范**：明确的格式要求（结论先行、数据支撑、风险提示等）
4. **思维链**：分步推理流程（理解诉求→检索数据→分析→结论→标注置信度）
5. **知识边界**：能力范围声明（擅长什么、不擅长什么、超出范围怎么处理）
6. **Few-shot 示例**：一个好的回答样例（让模型知道期望的输出质量）
7. **负面约束**：明确列出不要做的事（如"不要给出具体买卖时点"、"不要编造数据"）

## 注意事项
- 使用 Markdown 标题层级（## / ###），结构清晰
- 数值判断标准必须具体（如百分位区间、z-score 阈值）
- 语气专业但不晦涩，面向普通投资者
- 篇幅控制在 300-600 字，不要太长
"""


@app.post("/api/agents/generate-prompt")
async def generate_prompt_api(req: GeneratePromptRequest):
    """AI 辅助生成或优化 Agent 系统提示词。"""
    if req.mode == "optimize" and not req.current_prompt:
        raise HTTPException(400, "优化模式需要提供 current_prompt")

    if req.mode == "optimize":
        user_content = f"""请优化以下 Agent 系统提示词。保持原有角色定位不变，但按规范补充缺失的部分（如 Few-shot 示例、负面约束等），让提示词更专业、更完整。

## 当前 Agent 信息
- 名称：{req.name}
- 描述：{req.description}

## 当前提示词
{req.current_prompt}

## 可用工具
{AVAILABLE_TOOLS_DESC}

请直接输出优化后的完整提示词，不要加任何解释说明。"""
    else:
        user_content = f"""请根据以下信息从零生成一个 Agent 系统提示词。

## Agent 信息
- 名称：{req.name}
- 描述：{req.description}

## 可用工具
{AVAILABLE_TOOLS_DESC}

请直接输出完整的提示词，不要加任何解释说明。"""

    try:
        resp = _call_llm(
            messages=[
                {"role": "system", "content": PROMPT_GENERATOR_META},
                {"role": "user", "content": user_content},
            ],
            model=MODEL,
            max_tokens=2000,
        )
        result = resp.choices[0].message.content if resp and resp.choices else ""
        if not result:
            raise HTTPException(500, "AI 生成失败，返回为空")
        return {"ok": True, "prompt": result.strip()}
    except HTTPException:
        raise
    except Exception as e:
        logging.warning(f"AI 生成提示词失败: {e}")
        raise HTTPException(500, f"AI 生成失败: {str(e)}")


@app.get("/api/agents/{agent_id}/versions")
async def list_agent_versions_api(agent_id: int):
    """列出某 Agent 的提示词版本历史。"""
    versions = list_prompt_versions(agent_id, 'conversation')
    return {"versions": versions}


@app.post("/api/agents/{agent_id}/rollback/{version_id}")
async def rollback_agent_prompt_api(agent_id: int, version_id: int):
    """回滚到指定版本的提示词。"""
    version = get_prompt_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    # 保存当前提示词为新版本
    save_prompt_version(agent_id, 'conversation', agent['system_prompt'])
    # 回滚
    update_agent(agent_id, system_prompt=version['system_prompt'])
    return {"ok": True, "system_prompt": version['system_prompt']}


@app.get("/api/analysis-agents/{agent_id}/versions")
async def list_analysis_agent_versions_api(agent_id: int):
    """列出某分析 Agent 的提示词版本历史。"""
    versions = list_prompt_versions(agent_id, 'analysis')
    return {"versions": versions}


@app.post("/api/analysis-agents/{agent_id}/rollback/{version_id}")
async def rollback_analysis_agent_prompt_api(agent_id: int, version_id: int):
    """回滚分析 Agent 到指定版本的提示词。"""
    version = get_prompt_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    current = get_analysis_agent(agent_id)
    if not current:
        raise HTTPException(404, "Agent 不存在")
    save_prompt_version(agent_id, 'analysis', current['system_prompt'])
    update_analysis_agent(agent_id, system_prompt=version['system_prompt'])
    return {"ok": True, "system_prompt": version['system_prompt']}


@app.get("/api/conversations")
async def list_conversations_api():
    """对话列表。"""
    return {"conversations": list_conversations()}


@app.post("/api/conversations")
async def create_conversation_api(req: CreateConversationRequest):
    """创建对话。"""
    conv_id = create_conversation(title=req.title, agent_id=req.agent_id, context_data=req.context_data)
    return {"ok": True, "conversation_id": conv_id}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation_api(conv_id: int):
    """删除对话。"""
    delete_conversation(conv_id)
    return {"ok": True}


@app.get("/api/conversations/{conv_id}/messages")
async def get_messages_api(conv_id: int, limit: int = 50):
    """获取对话消息历史。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    msgs = get_messages(conv_id, limit)
    return {"conversation": conv, "messages": msgs}


@app.post("/api/conversations/{conv_id}/messages")
async def send_message_api(conv_id: int, req: SendMessageRequest):
    """发送消息并获取 AI 回复（多 Agent 协作模式）。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 1. 存储用户消息
    create_message(conv_id, "user", req.content)

    # 2. RAG 检索
    agent = get_agent(conv["agent_id"]) if conv.get("agent_id") else None
    rag_types = []
    if agent and agent.get("knowledge_scope"):
        try:
            scope = json.loads(agent["knowledge_scope"]) if isinstance(agent["knowledge_scope"], str) else agent["knowledge_scope"]
            rag_types = scope.get("rag_types", [])
        except (json.JSONDecodeError, TypeError):
            pass

    rag_result = build_rag_context_with_details(req.content, content_types=rag_types if rag_types else None)
    rag_context = rag_result["context"]

    # 3. 获取对话历史
    history = get_messages(conv_id, limit=20)
    msg_list = [{"role": m["role"], "content": m["content"]} for m in history]

    # 4. 调用 Orchestrator（多 Agent 协作）
    try:
        llm_result = orchestrate(req.content, msg_list, rag_context)
        answer = llm_result["answer"]
    except Exception as e:
        answer = f"AI 回复失败: {str(e)}"
        llm_result = {"answer": answer, "specialist_results": [], "tool_calls": [], "turns": 0}

    # 5. 存储 AI 回复
    specialist_results = llm_result.get("specialist_results", [])
    metadata_dict = {
        "specialist_results": [
            {"agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
            for s in specialist_results
        ],
        "tool_calls": llm_result.get("tool_calls", []),
    }
    metadata = json.dumps(metadata_dict, ensure_ascii=False) if specialist_results else None
    msg_id = create_message(conv_id, "assistant", answer, metadata=metadata)

    # 6. 记录 RAG 日志
    log_rag_search(
        conversation_id=conv_id,
        message_id=msg_id,
        query=req.content,
        keywords=rag_result.get("keywords", []),
        results=rag_result.get("results", []),
        content_types=rag_types if rag_types else None,
    )

    # 7. 自动更新对话标题
    if len(history) <= 1 and conv.get("title") == "新对话":
        short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
        update_conversation(conv_id, title=short_title)

    return {
        "answer": answer,
        "specialist_results": [
            {"agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")}
            for s in specialist_results
        ],
        "rag": {
            "keywords": rag_result.get("keywords", []),
            "sources": [{"type": r.get("label", r.get("content_type")), "title": r.get("title")} for r in rag_result.get("results", [])[:3]],
            "results_count": len(rag_result.get("results", [])),
        },
        "tool_calls": llm_result.get("tool_calls", []),
        "turns": llm_result.get("turns", 1),
    }


@app.post("/api/conversations/{conv_id}/messages/stream")
async def send_message_stream(conv_id: int, req: SendMessageRequest):
    """SSE 流式对话，支持多 Agent 专家分析实时展示。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 解析 knowledge_scope
    rag_types = []
    agent = get_agent(conv["agent_id"]) if conv.get("agent_id") else None
    if agent and agent.get("knowledge_scope"):
        try:
            scope = json.loads(agent["knowledge_scope"]) if isinstance(agent["knowledge_scope"], str) else agent["knowledge_scope"]
            rag_types = scope.get("rag_types", [])
        except (json.JSONDecodeError, TypeError):
            pass

    async def event_stream():
        import asyncio

        # 1. 存储用户消息
        create_message(conv_id, "user", req.content)
        yield _sse_event("user_message", {"content": req.content})

        # 2. 需求澄清（使用 LLM 分析问题）
        yield _sse_event("status", {"message": "正在理解您的问题..."})
        clarification = clarify_requirement(req.content)
        complexity = clarification["complexity"]
        yield _sse_event("status", {"message": f"问题类型: {complexity} ({clarification.get('reason', '')})"})

        # 3. 简单任务：直接路由到专家，跳过 Orchestrator
        if complexity == "simple" and len(clarification.get("specialists", [])) == 1:
            # 只需要1个专家，直接调用
            agent_key = clarification["specialists"][0]
            if agent_key:
                yield _sse_event("status", {"message": f"正在咨询{_get_specialist_name(agent_key)}..."})

                # 直接运行专家
                def _run_expert():
                    try:
                        return run_specialist(agent_key, req.content)
                    except Exception as e:
                        return {"error": str(e)}

                result = await asyncio.to_thread(_run_expert)

                if "error" not in result:
                    # 发送专家完成事件
                    yield _sse_event("specialist_done", {
                        "agent_key": result.get("agent_key", agent_key),
                        "agent": result.get("agent", ""),
                        "icon": result.get("icon", ""),
                        "analysis": result.get("analysis", ""),
                        "duration_ms": result.get("duration_ms", 0),
                    })

                    # 发送最终回答
                    answer = result.get("analysis", "")
                    specialist_results = [{
                        "agent_key": result.get("agent_key", agent_key),
                        "agent": result.get("agent", ""),
                        "icon": result.get("icon", ""),
                        "analysis": answer,
                        "duration_ms": result.get("duration_ms", 0),
                    }]

                    yield _sse_event("answer", {
                        "content": answer,
                        "specialist_results": specialist_results,
                    })

                    # 存储回复
                    metadata_dict = {
                        "specialist_results": [
                            {"agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                            for s in specialist_results
                        ],
                        "complexity": complexity,
                    }
                    metadata = json.dumps(metadata_dict, ensure_ascii=False)
                    msg_id = create_message(conv_id, "assistant", answer, metadata=metadata)
                    yield _sse_event("done", {"message_id": msg_id})
                    return
                else:
                    # 专家执行失败，回退到 Orchestrator
                    yield _sse_event("status", {"message": "专家执行失败，切换到完整分析模式..."})

        # 4. RAG 检索（中等和复杂任务）
        yield _sse_event("status", {"message": "正在检索知识库..."})
        rag_result = build_rag_context_with_details(req.content, content_types=rag_types if rag_types else None)
        rag_context = rag_result["context"]

        if rag_result.get("results"):
            sources = [{"type": r.get("label", r.get("content_type")), "title": r.get("title")} for r in rag_result["results"][:3]]
            yield _sse_event("rag_sources", {"sources": sources})

        # 5. 获取对话历史
        history = get_messages(conv_id, limit=20)
        msg_list = [{"role": m["role"], "content": m["content"]} for m in history]

        # 6. 调用 Orchestrator（多 Agent 协作）
        yield _sse_event("status", {"message": "正在分析问题，决定需要咨询哪些专家..."})

        def _run_orchestrator_stream():
            """在线程中运行 orchestrator 流式生成器，通过队列传递事件。"""
            import queue
            q = queue.Queue()

            def _producer():
                try:
                    for event in orchestrate_stream(req.content, msg_list, rag_context):
                        q.put(event)
                except Exception as e:
                    q.put({"type": "error", "message": str(e)})
                finally:
                    q.put(None)  # 结束信号

            import threading
            t = threading.Thread(target=_producer, daemon=True)
            t.start()
            return q

        q = await asyncio.to_thread(_run_orchestrator_stream)

        specialist_results = []
        all_tool_calls = []
        final_answer = ""

        while True:
            event = await asyncio.to_thread(q.get)
            if event is None:
                break

            event_type = event.get("type")

            if event_type == "status":
                yield _sse_event("status", event)

            elif event_type == "specialist_start":
                yield _sse_event("specialist_start", {
                    "agent_key": event.get("agent_key"),
                    "agent": event.get("agent"),
                    "icon": event.get("icon"),
                })

            elif event_type == "specialist_done":
                specialist_results.append({
                    "agent_key": event.get("agent_key"),
                    "agent": event.get("agent"),
                    "icon": event.get("icon"),
                    "analysis": event.get("analysis"),
                    "duration_ms": event.get("duration_ms"),
                })
                yield _sse_event("specialist_done", {
                    "agent_key": event.get("agent_key"),
                    "agent": event.get("agent"),
                    "icon": event.get("icon"),
                    "analysis": event.get("analysis"),
                    "duration_ms": event.get("duration_ms"),
                })

            elif event_type == "answer":
                final_answer = event.get("content", "")
                all_tool_calls = event.get("tool_calls", [])
                if not specialist_results:
                    specialist_results = event.get("specialist_results", [])

            elif event_type == "error":
                yield _sse_event("error", {"message": event.get("message", "未知错误")})
                return

        # 5. 发送最终回答
        answer = final_answer
        yield _sse_event("answer", {
            "content": answer,
            "specialist_results": specialist_results,
        })

        # 5.1 主动分析是否产生预警（后台异步执行）
        asyncio.create_task(_proactive_alert_check(req.content, answer, specialist_results))

        # 6. 存储 AI 回复
        metadata_dict = {
            "specialist_results": [
                {"agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                for s in specialist_results
            ],
            "tool_calls": all_tool_calls,
        }
        metadata = json.dumps(metadata_dict, ensure_ascii=False) if specialist_results or all_tool_calls else None
        msg_id = create_message(conv_id, "assistant", answer, metadata=metadata)

        # 7. 记录 RAG 日志
        log_rag_search(
            conversation_id=conv_id,
            message_id=msg_id,
            query=req.content,
            keywords=rag_result.get("keywords", []),
            results=rag_result.get("results", []),
            content_types=rag_types if rag_types else None,
        )

        # 8. 自动更新对话标题
        if len(history) <= 1 and conv.get("title") == "新对话":
            short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
            update_conversation(conv_id, title=short_title)

        yield _sse_event("done", {"message_id": msg_id})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse_event(event_type: str, data: dict) -> str:
    """格式化 SSE 事件。"""
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"


async def _proactive_alert_check(query: str, answer: str, specialist_results: list):
    """对话结束后，主动检测是否需要对持仓生成预警。"""
    try:
        # 检测是否涉及政策/新闻/估值变化等可能影响持仓的内容
        alert_keywords = ["政策", "新闻", "利好", "利空", "上涨", "下跌", "大涨", "大跌",
                          "风险", "泡沫", "危机", "加息", "降息", "降准", "监管",
                          "高估", "低估", "加仓", "减仓", "卖出", "买入", "注意"]
        if not any(kw in query for kw in alert_keywords) and not any(kw in answer for kw in alert_keywords):
            return

        # 获取持仓数据
        holdings = list_holdings()
        if not holdings:
            return

        # 检查各专家分析中是否有关联持仓的内容
        fund_names = {h.get("fund_name", "") for h in holdings if h.get("fund_name")}
        index_names = {h.get("index_name", "") for h in holdings if h.get("index_name")}

        # 构建预警内容
        alert_holdings = []
        combined_text = query + " " + answer

        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            if not analysis:
                continue
            combined_text += " " + analysis[:2000]

        for h in holdings:
            fname = h.get("fund_name", "")
            iname = h.get("index_name", "")
            if (fname and fname in combined_text) or (iname and iname in combined_text):
                alert_holdings.append(h)

        if not alert_holdings and any(kw in combined_text for kw in ["政策", "新闻", "利好", "利空", "市场"]):
            # 虽然没直接提到某只基金，但涉及政策/新闻，对全部持仓生成轻度预警
            for h in holdings[:3]:  # 最多3只
                create_alert(
                    alert_type="news_impact",
                    title=f"市场动态可能影响 {h.get('fund_name', '')}",
                    content=f"当前对话涉及市场变化，可能影响您的持仓 {h.get('fund_name', '')}（{h.get('fund_code', '')}）。建议关注后续走势。",
                    severity="info",
                    related_fund_code=h.get("fund_code"),
                    related_fund_name=h.get("fund_name"),
                    source="ai_analysis",
                )
        elif alert_holdings:
            for h in alert_holdings[:5]:
                create_alert(
                    alert_type="news_impact",
                    title=f"对话涉及 {h.get('fund_name', '')}，建议关注",
                    content=f"当前对话内容涉及您的持仓 {h.get('fund_name', '')}（{h.get('fund_code', '')}），可能影响该持仓。",
                    severity="info",
                    related_fund_code=h.get("fund_code"),
                    related_fund_name=h.get("fund_name"),
                    source="ai_analysis",
                )
    except Exception as e:
        logger.warning(f"[proactive_alert] 生成预警异常: {e}")


def _route_to_specialist(query: str) -> str | None:
    """根据问题关键词路由到最合适的专家。返回 agent_key 或 None。"""
    query = query.strip()

    # 估值相关关键词 → 估值专家
    valuation_keywords = ["估值", "PE", "PB", "百分位", "z-score", "高估", "低估", "贵不贵", "便宜"]
    if any(kw in query for kw in valuation_keywords):
        return "valuation_expert"

    # 新闻/市场动态关键词 → 择时分析师
    news_keywords = ["新闻", "最新", "动态", "政策", "消息", "市场", "今天", "昨天"]
    if any(kw in query for kw in news_keywords):
        return "market_analyst"

    # 风险相关关键词 → 风险评估师
    risk_keywords = ["风险", "回撤", "波动", "最大回撤"]
    if any(kw in query for kw in risk_keywords):
        return "risk_assessor"

    # 配置相关关键词 → 资产配置师
    allocation_keywords = ["配置", "配比", "定投", "股债", "组合"]
    if any(kw in query for kw in allocation_keywords):
        return "allocation_advisor"

    # 默认返回估值专家（最常见的查询）
    return "valuation_expert"


def _get_specialist_name(agent_key: str) -> str:
    """获取专家名称。"""
    names = {
        "valuation_expert": "估值专家",
        "market_analyst": "择时分析师",
        "risk_assessor": "风险评估师",
        "allocation_advisor": "资产配置师",
    }
    return names.get(agent_key, "专家")


@app.get("/api/conversations/{conv_id}/rag-logs")
async def get_rag_logs_api(conv_id: int, limit: int = 50):
    """获取对话的 RAG 检索日志。"""
    from db import _get_conn
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM rag_logs
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conv_id, limit)).fetchall()
    conn.close()
    logs = []
    for row in rows:
        log = dict(row)
        log["keywords"] = json.loads(log.get("keywords") or "[]")
        log["results"] = json.loads(log.get("results") or "[]")
        log["content_types"] = json.loads(log.get("content_types") or "[]")
        logs.append(log)
    return {"logs": logs}


@app.get("/api/rag-logs")
async def get_all_rag_logs_api(limit: int = 100):
    """获取所有 RAG 检索日志。"""
    from db import _get_conn
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM rag_logs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    logs = []
    for row in rows:
        log = dict(row)
        log["keywords"] = json.loads(log.get("keywords") or "[]")
        log["results"] = json.loads(log.get("results") or "[]")
        log["content_types"] = json.loads(log.get("content_types") or "[]")
        logs.append(log)
    return {"logs": logs}


@app.get("/api/rag-stats")
async def get_rag_stats_api(days: int = 7):
    """获取 RAG 检索统计。"""
    from db import _get_conn
    conn = _get_conn()

    # 总检索次数
    total = conn.execute("SELECT COUNT(*) FROM rag_logs").fetchone()[0]

    # 按天统计
    daily = conn.execute("""
        SELECT date(created_at) as day, COUNT(*) as count
        FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY date(created_at)
        ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()

    # 热门关键词
    keywords_raw = conn.execute("""
        SELECT keywords FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        ORDER BY id DESC LIMIT 100
    """, (f"-{days} days",)).fetchall()

    from collections import Counter
    keyword_counter = Counter()
    for row in keywords_raw:
        try:
            kws = json.loads(row[0] or "[]")
            for kw in kws:
                keyword_counter[kw] += 1
        except:
            pass
    top_keywords = [{"keyword": k, "count": c} for k, c in keyword_counter.most_common(20)]

    # 知识类型命中统计
    type_stats = conn.execute("""
        SELECT content_types, COUNT(*) as count
        FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY content_types
    """, (f"-{days} days",)).fetchall()

    type_counter = Counter()
    for row in type_stats:
        try:
            types = json.loads(row[0] or "[]")
            for t in types:
                type_counter[t] += 1
        except:
            pass
    type_distribution = [{"type": t, "count": c} for t, c in type_counter.most_common()]

    # 平均命中结果数
    avg_results = conn.execute("""
        SELECT AVG(results_count) FROM rag_logs
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()[0] or 0

    conn.close()

    return {
        "total": total,
        "daily": [dict(r) for r in daily],
        "top_keywords": top_keywords,
        "type_distribution": type_distribution,
        "avg_results": round(avg_results, 1),
    }


@app.get("/api/token-usage")
async def get_token_usage_api(days: int = 7):
    """获取 Token 用量统计。"""
    from db import _get_conn
    conn = _get_conn()

    # 检查表是否存在
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
    ).fetchone()
    if not table_exists:
        conn.close()
        return {"total": 0, "daily": [], "by_model": []}

    # 总量
    row = conn.execute("""
        SELECT COUNT(*) as calls, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion, SUM(total_tokens) as total
        FROM token_usage WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()

    # 按天统计
    daily = conn.execute("""
        SELECT date(created_at) as day, COUNT(*) as calls, SUM(total_tokens) as tokens
        FROM token_usage WHERE created_at >= datetime('now', ?)
        GROUP BY date(created_at) ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()

    # 按模型统计
    by_model = conn.execute("""
        SELECT model, COUNT(*) as calls, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion, SUM(total_tokens) as total
        FROM token_usage WHERE created_at >= datetime('now', ?)
        GROUP BY model ORDER BY total DESC
    """, (f"-{days} days",)).fetchall()

    conn.close()

    return {
        "total": {
            "calls": row[0] or 0,
            "prompt_tokens": row[1] or 0,
            "completion_tokens": row[2] or 0,
            "total_tokens": row[3] or 0,
        },
        "daily": [dict(r) for r in daily],
        "by_model": [dict(r) for r in by_model],
    }


@app.get("/api/token-usage/recent")
async def get_token_usage_recent(page: int = 1, page_size: int = 20, days: int = 7):
    """获取最近 LLM 调用记录（分页）。"""
    offset = (page - 1) * page_size
    records = list_token_usage(days=days, limit=page_size, offset=offset)
    total = count_token_usage(days=days)
    return {"records": records, "total": total, "page": page, "page_size": page_size}


@app.get("/api/token-usage/summary")
async def get_token_usage_summary_api(days: int = 30):
    """获取 Token 用量汇总。"""
    return get_token_usage_summary(days=days)


@app.get("/api/token-usage/by-caller")
async def get_token_usage_by_caller_api(days: int = 7):
    """按 caller 分组统计。"""
    return {"items": get_token_usage_by_caller(days=days)}


@app.get("/api/token-usage/daily")
async def get_token_usage_daily_api(days: int = 30):
    """按天获取 Token 用量趋势。"""
    return {"items": get_token_usage_daily(days=days)}


# ── 性能监控 API ──────────────────────────────────────


@app.get("/api/performance/stats")
async def get_performance_stats_api(days: int = 7):
    """获取 Agent 调用性能统计。"""
    return get_performance_stats(days=days)


@app.get("/api/performance/by-agent")
async def get_performance_by_agent_api(days: int = 7):
    """按 Agent 分组统计性能。"""
    return {"items": get_performance_by_agent(days=days)}


# ── 评测集 API (Eval Suite) ─────────────────────────────


class CreateEvalCaseRequest(BaseModel):
    name: str
    analysis_type: str
    input_params: str = "{}"
    description: str = ""
    expected_quality: str = ""


class UpdateEvalCaseRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    input_params: str | None = None
    expected_quality: str | None = None
    is_active: int | None = None


@app.get("/api/eval/cases")
async def list_eval_cases_api(analysis_type: str = "", active_only: bool = True):
    """列出评测用例。"""
    return {"cases": list_eval_cases(analysis_type=analysis_type or None, active_only=active_only)}


@app.post("/api/eval/cases")
async def create_eval_case_api(req: CreateEvalCaseRequest):
    """创建评测用例。"""
    case_id = create_eval_case(
        name=req.name, analysis_type=req.analysis_type,
        input_params=req.input_params, description=req.description,
        expected_quality=req.expected_quality,
    )
    return {"ok": True, "id": case_id}


@app.delete("/api/eval/cases/{case_id}")
async def delete_eval_case_api(case_id: int):
    """删除评测用例。"""
    ok = delete_eval_case(case_id)
    if not ok:
        raise HTTPException(404, "评测用例不存在")
    return {"ok": True}


@app.post("/api/eval/cases/{case_id}/run")
async def run_eval_case_api(case_id: int):
    """运行单个评测用例：调用对应的分析模式，记录结果。"""
    case = get_eval_case(case_id)
    if not case:
        raise HTTPException(404, "评测用例不存在")

    import json

    input_params = json.loads(case["input_params"] or "{}")
    analysis_type = case["analysis_type"]
    start = time.time()

    try:
        if analysis_type == "panorama":
            result = await panorama_analysis_api(PanoramaAnalysisRequest())
            # 如果返回的是 StreamingResponse 或 dict
            if isinstance(result, StreamingResponse):
                result_summary = "流式输出（已在后台执行）"
                result_data = "{}"
            else:
                result_summary = json.dumps(result, ensure_ascii=False)[:500]
                result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "deep_dive":
            holding_id = input_params.get("holding_id")
            if not holding_id:
                raise HTTPException(400, "deep_dive 需要 holding_id 参数")
            result = await fund_deep_dive_api(holding_id, DeepDiveRequest())
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "trade_review":
            result = await trade_review_api(TradeReviewRequest(
                start_date=input_params.get("start_date", ""),
                end_date=input_params.get("end_date", ""),
            ))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "what_if":
            result = await what_if_analysis_api(WhatIfRequest(
                scenario=input_params.get("scenario", ""),
                parameter=input_params.get("parameter", ""),
            ))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "diversification_ai":
            result = await portfolio_diversification_ai_summary()
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "ai":
            question = input_params.get("question", "")
            result = await portfolio_ai_analysis_api(PortfolioAiAnalysisRequest(question=question))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        else:
            raise HTTPException(400, f"不支持的分析类型: {analysis_type}")

        duration_ms = int((time.time() - start) * 1000)
        run_id = create_eval_run(
            case_id=case_id, analysis_type=analysis_type,
            result_summary=result_summary,
            result_data=result_data,
            duration_ms=duration_ms,
        )
        return {"ok": True, "run_id": run_id, "duration_ms": duration_ms}
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        run_id = create_eval_run(
            case_id=case_id, analysis_type=analysis_type,
            result_summary=f"错误: {str(e)[:200]}",
            error_msg=str(e)[:1000],
            duration_ms=duration_ms,
        )
        return {"ok": False, "run_id": run_id, "error": str(e)}


@app.get("/api/eval/runs")
async def list_eval_runs_api(case_id: int = 0, limit: int = 50):
    """列出评测运行记录。"""
    return {"runs": list_eval_runs(case_id=case_id or None, limit=limit)}


@app.get("/api/eval/runs/{run_id}")
async def get_eval_run_detail_api(run_id: int):
    """获取单条运行记录详情。"""
    run = get_eval_run_detail(run_id)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    return run


@app.get("/api/eval/stats")
async def get_eval_stats_api():
    """获取评测统计概览。"""
    return get_eval_stats()


# ── AI 市场分析 API ──────────────────────────────────────


class AnalysisRunRequest(BaseModel):
    index_code: str = ""
    index_name: str = ""
    agent_id: int = 1


class AnalysisAgentUpdateRequest(BaseModel):
    name: str = None
    description: str = None
    system_prompt: str = None
    is_active: int = None


@app.post("/api/analysis/run")
async def run_analysis(req: AnalysisRunRequest):
    """触发 AI 市场分析。"""
    # 1. 获取 agent 配置
    agent = get_analysis_agent(req.agent_id)
    if not agent:
        raise HTTPException(404, "分析 Agent 不存在")

    # 2. 获取当前指数估值数据
    valuation_context = ""
    if req.index_code:
        try:
            latest = get_latest_valuation(req.index_code)
            if latest:
                valuation_context = json.dumps(latest, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 3. 检索最新新闻
    news_context = ""
    try:
        from tools import execute_tool
        news_result = execute_tool("web_search", {"query": "A股 今日行情 板块 热点", "max_results": 5})
        news_context = news_result if news_result else ""
    except Exception as e:
        logger.warning(f"新闻检索失败: {e}")

    # 4. 拼装 prompt
    full_prompt = agent["system_prompt"]
    if valuation_context:
        full_prompt += f"\n\n<current_valuation>\n当前指数估值数据（{req.index_name or req.index_code}）：\n{valuation_context}\n</current_valuation>"
    if news_context:
        full_prompt += f"\n\n<latest_news>\n最新财经新闻：\n{news_context}\n</latest_news>"

    # 5. 调用 LLM
    try:
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="market_analysis",
            model=MODEL,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": "请生成今日市场分析报告。"},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"AI 分析失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    # 6. 保存历史
    history_id = create_analysis_history(
        index_code=req.index_code,
        index_name=req.index_name,
        agent_id=agent["id"],
        agent_name=agent["name"],
        prompt_used=full_prompt,
        news_context=news_context,
        valuation_context=valuation_context,
        result=result_text,
        token_usage=token_usage,
    )

    return {"id": history_id, "result": result_text, "token_usage": token_usage}


@app.get("/api/analysis/history")
async def get_analysis_history_api(index_code: str = "", limit: int = 50):
    """获取分析历史列表。"""
    return {"history": list_analysis_history(index_code or None, limit)}


@app.get("/api/analysis/history/{history_id}")
async def get_analysis_history_detail_api(history_id: int):
    """获取单条分析历史详情。"""
    item = get_analysis_history_item(history_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    return item


@app.delete("/api/analysis/history/{history_id}")
async def delete_analysis_history_api(history_id: int):
    """删除分析历史。"""
    delete_analysis_history(history_id)
    return {"ok": True}


@app.get("/api/analysis-agents")
async def list_analysis_agents_api():
    """获取分析 Agent 列表。"""
    return {"agents": list_analysis_agents()}


@app.put("/api/analysis-agents/{agent_id}")
async def update_analysis_agent_api(agent_id: int, req: AnalysisAgentUpdateRequest):
    """更新分析 Agent 配置。修改提示词时自动保存版本历史。"""
    kwargs = {k: v for k, v in req.dict().items() if v is not None}
    if not kwargs:
        raise HTTPException(400, "无更新内容")
    # 提示词变更前，保存当前版本
    if 'system_prompt' in kwargs:
        current = get_analysis_agent(agent_id)
        if current and kwargs['system_prompt'] != current.get('system_prompt'):
            save_prompt_version(agent_id, 'analysis', current['system_prompt'])
    update_analysis_agent(agent_id, **kwargs)
    return {"ok": True}


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


# ── 文章管理 API ──────────────────────────────────────


@app.post("/api/articles/sync")
async def sync_articles_api():
    """从 articles.json 同步文章列表到 DB。"""
    articles_file = ROOT / "doc" / "articles.json"
    if not articles_file.exists():
        raise HTTPException(400, "articles.json 不存在")
    with open(articles_file) as f:
        articles = json.load(f)
    sync_articles(articles)
    count = len(list_articles())
    return {"ok": True, "total": count}


@app.get("/api/articles")
async def list_articles_api(status: str = None):
    """文章列表，可选按状态筛选。"""
    return {"articles": list_articles(status)}


@app.get("/api/gallery")
async def list_gallery_records(search: str = None, limit: int = 200):
    """图片浏览：列出所有分析记录，支持模糊搜索。"""
    return {"records": list_all_analysis_records(search, limit)}


# ══════════════════════════════════════════════════════
# 作者文章 API
# ══════════════════════════════════════════════════════

_crawl_semaphore = asyncio.Semaphore(3)  # 限制并发爬取数


@app.post("/api/author-articles/import")
async def import_author_articles():
    """从 Excel 导入作者文章（幂等，跳过已存在）。"""
    from import_articles import import_from_excel
    result = import_from_excel()
    return {"ok": True, **result}


@app.get("/api/author-articles")
async def list_author_articles_api(status: str = None, search: str = None, limit: int = 200):
    """作者文章列表。"""
    articles = list_author_articles(status=status, search=search, limit=limit)
    stats = count_author_articles()
    return {"articles": articles, "stats": stats}


@app.get("/api/author-articles/{article_id}")
async def get_author_article_api(article_id: int):
    """作者文章详情。"""
    article = get_author_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    return article


@app.delete("/api/author-articles/{article_id}")
async def delete_author_article_api(article_id: int):
    """删除作者文章。"""
    if not delete_author_article(article_id):
        raise HTTPException(404, "文章不存在")
    return {"ok": True}


class ExtractUrlRequest(BaseModel):
    url: str


@app.post("/api/author-articles/extract")
async def extract_article_from_url(body: ExtractUrlRequest):
    """从 URL 提取文章信息（通用网页，不依赖 Playwright）。"""
    import requests as req
    from bs4 import BeautifulSoup

    try:
        resp = req.get(body.url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"请求失败: {e}")

    soup = BeautifulSoup(resp.text, "lxml")

    # Extract title
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)

    # Extract author
    author = ""
    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"]
    og_site = soup.find("meta", property="og:site_name")
    if not author and og_site and og_site.get("content"):
        author = og_site["content"]

    # Extract publish time
    publish_time = ""
    for attr in [("property", "article:published_time"), ("name", "pubdate"),
                 ("name", "publish_time"), ("property", "og:article:published_time")]:
        tag = soup.find("meta", attrs=dict([attr]))
        if tag and tag.get("content"):
            publish_time = tag["content"][:19]
            break
    if not publish_time:
        time_tag = soup.find("time")
        if time_tag:
            publish_time = time_tag.get("datetime", "")[:19] or time_tag.get_text(strip=True)[:19]

    # Extract summary
    summary = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        summary = meta_desc["content"]
    elif soup.find("meta", property="og:description"):
        summary = soup.find("meta", property="og:description").get("content", "")

    # Extract content text (main body)
    content_text = ""
    for selector in ["article", '[role="main"]', "main", ".article-content",
                     ".post-content", ".entry-content", "#content", ".content"]:
        container = soup.select_one(selector)
        if container and len(container.get_text(strip=True)) > 100:
            content_text = container.get_text("\n", strip=True)
            break
    if not content_text:
        paragraphs = soup.find_all("p")
        content_text = "\n\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)

    if len(content_text) > 5000:
        content_text = content_text[:5000] + "..."

    return {
        "url": body.url,
        "title": title,
        "author": author,
        "publish_time": publish_time,
        "summary": summary[:500] if summary else "",
        "content_text": content_text,
    }


@app.post("/api/author-articles")
async def create_author_article_api(body: dict):
    """直接创建作者文章记录。"""
    article_id = create_author_article(
        url=body.get("url", ""),
        title=body.get("title", ""),
        publish_time=body.get("publish_time", ""),
        summary=body.get("summary", ""),
        article_type=body.get("article_type", ""),
    )
    if body.get("content_text"):
        update_author_article(article_id, content_text=body["content_text"])
    return {"id": article_id}


@app.post("/api/author-articles/crawl")
async def crawl_all_author_articles():
    """批量爬取所有 pending 状态的作者文章全文。"""
    pending = list_author_articles(status="pending", limit=500)
    if not pending:
        return {"ok": True, "message": "没有待爬取的文章", "total": 0}

    asyncio.create_task(_batch_crawl_author_articles(pending))
    return {"ok": True, "message": f"开始爬取 {len(pending)} 篇文章", "total": len(pending)}


@app.post("/api/author-articles/{article_id}/crawl")
async def crawl_single_author_article(article_id: int):
    """爬取单篇作者文章全文。"""
    article = get_author_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")

    update_author_article(article_id, status="crawling", error_msg="")
    asyncio.create_task(_crawl_one_author_article(article_id, article["url"]))
    return {"ok": True, "message": "开始爬取"}


def _clean_article_html(html: str) -> str:
    """清理微信文章 HTML，保留图文混排结构。"""
    import re
    # data-src → src（微信图片用 data-src 做懒加载）
    html = html.replace('data-src="', 'src="')
    # 移除 script/style 标签
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # 移除 noscript
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # 移除微信特有的 data- 属性
    html = re.sub(r'\s+data-[a-z-]+="[^"]*"', '', html, flags=re.IGNORECASE)
    # 移除 onclick 等事件
    html = re.sub(r'\s+on[a-z]+="[^"]*"', '', html, flags=re.IGNORECASE)
    # 图片 src 走代理（微信图片）
    html = re.sub(
        r'src="(https?://mmbiz\.qpic\.cn/[^"]*)"',
        lambda m: f'src="/api/proxy-image?url={m.group(1)}"',
        html
    )
    # 移除空的 section/div（减少嵌套）
    html = re.sub(r'<(section|div)[^>]*>\s*</\1>', '', html, flags=re.IGNORECASE)
    return html.strip()


async def _batch_crawl_author_articles(articles: list):
    """后台批量爬取作者文章。"""
    for a in articles:
        update_author_article(a["id"], status="crawling")

    async def _crawl_with_limit(article_id, url):
        async with _crawl_semaphore:
            await _crawl_one_author_article(article_id, url)

    tasks = [_crawl_with_limit(a["id"], a["url"]) for a in articles]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _crawl_one_author_article(article_id: int, url: str):
    """爬取单篇作者文章并更新数据库。"""
    try:
        result = await fetch_article(url)
        content = result.get("content_text", "")
        raw_html = result.get("content_html", "")
        title = result.get("title", "")
        publish_time = result.get("publish_time", "")
        images = result.get("images", [])

        # 清理 HTML：data-src → src，过滤脚本/样式/空白标签
        clean_html = _clean_article_html(raw_html) if raw_html else ""

        update_author_article(article_id,
            content_text=content,
            content_html=clean_html,
            title=title or None,
            publish_time=publish_time or None,
            images=json.dumps(images, ensure_ascii=False) if images else None,
            status="done",
            error_msg="",
        )

        # 索引到 RAG
        if content:
            index_author_article(article_id, title, content)

    except Exception as e:
        update_author_article(article_id, status="error", error_msg=str(e)[:500])


@app.post("/api/articles/add")
async def add_article_api(req: CreateTaskRequest):
    """粘贴公众号链接，自动解析+下载+分析（全流程）。"""
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "链接不能为空")

    # 去重检查
    existing = get_article_by_url(url)
    if existing:
        return {"ok": False, "message": "文章已存在", "article_id": existing["id"], "status": existing["status"]}

    # 先插入占位记录
    article_id = create_article(url, title="解析中...")

    # 后台全流程：解析 → 下载 → 分析
    asyncio.create_task(_background_add_article(article_id, url))
    return {"ok": True, "message": "已提交，正在解析", "article_id": article_id}


@app.get("/api/articles/{article_id}")
async def get_article_api(article_id: int):
    """文章详情，含图片列表和分析记录。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    records = get_analysis_records(article_id)
    article["analysis_records"] = records
    return article


async def _background_add_article(article_id: int, url: str):
    """全流程后台任务：解析文章 → 下载图片 → 分析估值。"""
    try:
        # 1. 解析文章
        update_article(article_id, status="downloading")
        result = await fetch_article(url)
        title = result.get("title", "未知标题")
        publish_time = result.get("publish_time", "")
        images = result.get("images", [])

        if not images:
            update_article(article_id, status="error", error_msg="文章无图片", title=title, publish_time=publish_time)
            return

        # 更新文章信息
        update_article(article_id, title=title, publish_time=publish_time)

        # 2. 下载图片
        from datetime import datetime
        article_date = publish_time[:10] if publish_time else datetime.now().strftime("%Y-%m-%d")
        safe_title = "".join(c for c in title[:20] if c.isalnum() or c in " _-").strip() or "article"
        save_dir = IMAGES_DIR / article_date / safe_title
        save_dir.mkdir(parents=True, exist_ok=True)

        local_paths = await download_images(images, str(save_dir))

        manifest = {
            "url": url, "title": title, "article_date": article_date,
            "images": [{"index": i, "url": u, "local_path": str(lp)} for i, (u, lp) in enumerate(zip(images, local_paths))],
        }
        manifest_path = save_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        for i, (u, lp) in enumerate(zip(images, local_paths)):
            create_analysis_record(article_id, i, str(lp), u)

        images_dir_str = str(save_dir)
        try:
            images_dir_str = str(save_dir.relative_to(ROOT))
        except ValueError:
            pass

        update_article(article_id,
            status="downloaded", images_dir=images_dir_str,
            manifest_path=str(manifest_path), image_count=len(local_paths),
        )

        # 3. 自动分析
        update_article(article_id, status="analyzing")
        records = get_analysis_records(article_id)
        pending = [r for r in records if r["status"] in ("pending", "error", "cancelled", "timeout")]
        parser = ImageParser(model_type="mimo")
        success = 0
        failed = 0

        for record in pending:
            rid = record["id"]
            img_path = record["image_path"]
            if not os.path.isabs(img_path):
                img_path = str(ROOT / img_path)
            if not os.path.exists(img_path):
                update_analysis_record(rid, status="error", error_msg="文件不存在")
                failed += 1
                continue

            try:
                async with _vision_semaphore:
                    parse_result = await asyncio.wait_for(
                        asyncio.to_thread(parser.parse, img_path), timeout=600,
                    )
                has_value = parse_result and parse_result.get("index_code") and parse_result.get("current_value") is not None
                if has_value:
                    save_valuation(parse_result, source_image=img_path, source_url=url, snapshot_date=article_date)
                    update_analysis_record(rid, status="success",
                        index_code=parse_result.get("index_code"), index_name=parse_result.get("index_name"),
                        metric_type=parse_result.get("metric_type"),
                        raw_response=json.dumps(parse_result, ensure_ascii=False))
                    success += 1
                else:
                    update_analysis_record(rid, status="error",
                        index_code=parse_result.get("index_code") if parse_result else None,
                        error_msg="AI 未能提取到数据",
                        raw_response=json.dumps(parse_result, ensure_ascii=False) if parse_result else "")
                    failed += 1
            except asyncio.TimeoutError:
                update_analysis_record(rid, status="timeout", error_msg="分析超时")
                failed += 1
            except Exception as e:
                update_analysis_record(rid, status="error", error_msg=str(e))
                failed += 1

        err_msg = f"{failed} 张失败" if failed > 0 else None
        update_article(article_id, status="analyzed", error_msg=err_msg)

    except Exception as e:
        update_article(article_id, status="error", error_msg=str(e))


@app.post("/api/articles/{article_id}/download")
async def download_article_images(article_id: int):
    """异步下载文章中的图片（后台执行）。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    if article["status"] == "downloading":
        return {"ok": False, "message": "正在下载中，请稍候"}

    update_article(article_id, status="downloading")
    asyncio.create_task(_background_download(article_id, article))
    return {"ok": True, "message": "下载已开始"}


async def _background_download(article_id: int, article: dict):
    """后台下载任务。"""
    try:
        result = await fetch_article(article["url"])
        images = result["images"]
        if not images:
            update_article(article_id, status="error", error_msg="无图片")
            return

        from datetime import datetime
        article_date = article.get("publish_time", "")[:10] or datetime.now().strftime("%Y-%m-%d")
        safe_title = "".join(c for c in (article["title"] or "")[:20] if c.isalnum() or c in " _-").strip() or "article"
        save_dir = IMAGES_DIR / article_date / safe_title
        save_dir.mkdir(parents=True, exist_ok=True)

        local_paths = await download_images(images, str(save_dir))

        manifest = {
            "url": article["url"],
            "title": article["title"],
            "article_date": article_date,
            "images": [
                {"index": i, "url": url, "local_path": str(lp)}
                for i, (url, lp) in enumerate(zip(images, local_paths))
            ],
        }
        manifest_path = save_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        for i, (url, lp) in enumerate(zip(images, local_paths)):
            create_analysis_record(article_id, i, str(lp), url)

        # 归一化 images_dir 为相对路径（相对于项目根）
        images_dir_str = str(save_dir)
        try:
            images_dir_str = str(save_dir.relative_to(ROOT))
        except ValueError:
            pass  # 不在 ROOT 下时保留原路径

        update_article(article_id,
            status="downloaded",
            images_dir=images_dir_str,
            manifest_path=str(manifest_path),
            image_count=len(local_paths),
        )
    except Exception as e:
        update_article(article_id, status="error", error_msg=str(e))


@app.post("/api/articles/{article_id}/analyze")
async def analyze_article_images(article_id: int):
    """异步分析文章中的所有图片（后台执行）。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")

    records = get_analysis_records(article_id)
    pending = [r for r in records if r["status"] in ("pending", "error", "cancelled", "timeout")]
    if not pending:
        return {"ok": True, "message": "没有待分析的图片", "total": 0}
    if article["status"] == "analyzing":
        return {"ok": False, "message": "正在分析中，请稍候"}

    update_article(article_id, status="analyzing")
    _analyze_progress[article_id] = {"total": len(pending), "done": 0, "success": 0, "failed": 0, "current_record_id": None}
    asyncio.create_task(_background_analyze(article_id))
    return {"ok": True, "message": "分析已开始"}


@app.get("/api/articles/{article_id}/analyze-status")
async def get_analyze_status(article_id: int):
    """查询后台分析任务进度。"""
    article = get_article(article_id)
    progress = _analyze_progress.get(article_id, {})
    return {
        "status": article["status"] if article else "unknown",
        "progress": progress,
    }


@app.post("/api/articles/{article_id}/cancel-analyze")
async def cancel_analyze(article_id: int):
    """取消正在进行的分析任务。直接操作数据库，不依赖后台任务。"""
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    if article["status"] != "analyzing":
        return {"ok": False, "message": "当前没有在分析中"}

    # 1. 设置取消标志 + 取消 asyncio Task
    _analyze_cancel.add(article_id)
    task = _analyze_tasks.get(article_id)
    if task and not task.done():
        task.cancel()

    # 2. 直接标记所有 pending/error 记录为 cancelled
    records = get_analysis_records(article_id)
    for r in records:
        if r["status"] in ("pending", "error"):
            update_analysis_record(r["id"], status="cancelled", error_msg="用户取消分析")

    # 3. 更新文章状态为 error
    update_article(article_id, status="error", error_msg="用户取消分析")

    # 4. 清理进度跟踪
    _analyze_progress.pop(article_id, None)

    return {"ok": True, "message": "已取消"}


async def _background_analyze(article_id: int):
    """后台分析任务。单张图片超时 10 分钟，取消时立即中断当前图片。"""
    IMAGE_TIMEOUT = 600  # 10 分钟
    current_task = None  # 当前图片的 asyncio Task，用于取消中断

    def _cancel_remaining(records_list, from_index):
        """标记从 from_index 开始的所有待处理记录为已取消。"""
        for r in records_list[from_index:]:
            if r["status"] in ("pending", "error"):
                update_analysis_record(r["id"], status="cancelled", error_msg="用户取消分析")

    try:
        article = get_article(article_id)
        records = get_analysis_records(article_id)
        pending = [r for r in records if r["status"] in ("pending", "error", "cancelled", "timeout")]

        parser = ImageParser(model_type="mimo")
        success = 0
        failed = 0

        for idx, record in enumerate(pending):
            # 循环开头检查取消
            if article_id in _analyze_cancel:
                _analyze_cancel.discard(article_id)
                _cancel_remaining(pending, idx)
                update_article(article_id, status="error", error_msg="用户取消分析")
                break

            rid = record["id"]
            img_path = record["image_path"]
            # 相对路径转绝对路径（相对于项目根目录）
            if not os.path.isabs(img_path):
                img_path = str(ROOT / img_path)

            # 记录当前正在分析的图片
            _analyze_progress[article_id]["current_record_id"] = rid

            if not os.path.exists(img_path):
                update_analysis_record(rid, status="error", error_msg="文件不存在")
                failed += 1
                _analyze_progress[article_id]["done"] += 1
                _analyze_progress[article_id]["failed"] = failed
                continue

            try:
                # 用 create_task 包装，这样可以被 task.cancel() 立即中断（信号量限制并发）
                async def _parse_with_semaphore():
                    async with _vision_semaphore:
                        return await asyncio.to_thread(parser.parse, img_path)

                current_task = asyncio.create_task(_parse_with_semaphore())
                _analyze_tasks[article_id] = current_task
                result = await asyncio.wait_for(current_task, timeout=IMAGE_TIMEOUT)

                has_value = (
                    result
                    and result.get("index_code")
                    and result.get("current_value") is not None
                )
                if has_value:
                    vid = save_valuation(
                        result,
                        source_image=img_path,
                        source_url=article["url"],
                        snapshot_date=article.get("publish_time", "")[:10] or None,
                    )
                    update_analysis_record(rid,
                        status="success",
                        index_code=result.get("index_code"),
                        index_name=result.get("index_name"),
                        metric_type=result.get("metric_type"),
                        raw_response=json.dumps(result, ensure_ascii=False),
                    )
                    success += 1
                else:
                    st = "success" if result.get("index_code") else "error"
                    update_analysis_record(rid,
                        status=st,
                        index_code=result.get("index_code"),
                        index_name=result.get("index_name"),
                        metric_type=result.get("metric_type"),
                        error_msg="AI 返回空值（无当前值）" if result.get("index_code") else "AI 未能提取到数据",
                        raw_response=json.dumps(result, ensure_ascii=False) if result else "",
                    )
                    failed += 1
            except asyncio.TimeoutError:
                update_analysis_record(rid, status="timeout", error_msg=f"分析超时（{IMAGE_TIMEOUT // 60}分钟）")
                failed += 1
            except asyncio.CancelledError:
                # 被用户取消
                update_analysis_record(rid, status="cancelled", error_msg="用户取消分析")
                _analyze_cancel.discard(article_id)
                _cancel_remaining(pending, idx + 1)
                update_article(article_id, status="error", error_msg="用户取消分析")
                break
            except Exception as e:
                update_analysis_record(rid, status="error", error_msg=str(e))
                failed += 1
            finally:
                current_task = None
                _analyze_tasks.pop(article_id, None)

            _analyze_progress[article_id]["done"] += 1
            _analyze_progress[article_id]["success"] = success
            _analyze_progress[article_id]["failed"] = failed
            _analyze_progress[article_id]["current_record_id"] = None

            # 每处理完一张后检查取消
            if article_id in _analyze_cancel:
                _analyze_cancel.discard(article_id)
                _cancel_remaining(pending, idx + 1)
                update_article(article_id, status="error", error_msg="用户取消分析")
                break

            await asyncio.sleep(0.3)

        # 仅当未被取消时才根据结果设置文章状态
        if article_id not in _analyze_cancel:
            all_records = get_analysis_records(article_id)
            total_success = len([r for r in all_records if r["status"] == "success"])
            if total_success > 0:
                update_article(article_id, status="analyzed")
            elif failed > 0:
                update_article(article_id, status="error", error_msg=f"{failed} 张图片分析失败")
    except Exception as e:
        update_article(article_id, status="error", error_msg=str(e))
    finally:
        _analyze_progress.pop(article_id, None)
        _analyze_tasks.pop(article_id, None)


@app.post("/api/records/{record_id}/reanalyze")
async def reanalyze_image(record_id: int):
    """重新分析单张图片（异步，后台执行，前端轮询查状态）。"""
    record = get_analysis_record(record_id)
    if not record:
        raise HTTPException(404, "记录不存在")

    # 已有分析正在进行则直接返回
    task = _reanalyze_tasks.get(record_id)
    if task and not task.done():
        return {"ok": True, "message": "分析已在进行中"}

    # 重置记录状态为 analyzing
    update_analysis_record(record_id, status="analyzing", error_msg="")

    # 后台启动
    bt = asyncio.create_task(_background_reanalyze(record_id))
    _reanalyze_tasks[record_id] = bt
    return {"ok": True, "message": "分析已开始", "record_id": record_id}


@app.get("/api/records/{record_id}/reanalyze-status")
async def get_reanalyze_status(record_id: int):
    """查询单张图片重新分析的状态。"""
    record = get_analysis_record(record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    task = _reanalyze_tasks.get(record_id)
    return {
        "record_id": record_id,
        "status": record["status"],
        "error_msg": record.get("error_msg"),
        "running": bool(task and not task.done()),
    }


async def _background_reanalyze(record_id: int):
    """后台重新分析单张图片（10分钟超时，不阻塞 event loop）。"""
    IMAGE_TIMEOUT = 600
    try:
        record = get_analysis_record(record_id)
        if not record:
            update_analysis_record(record_id, status="error", error_msg="记录不存在")
            return

        img_path = record["image_path"]
        if not os.path.isabs(img_path):
            img_path = str(ROOT / img_path)
        if not os.path.exists(img_path):
            update_analysis_record(record_id, status="error", error_msg="图片文件不存在")
            return

        article = get_article(record["article_id"])
        parser = ImageParser(model_type="mimo")

        # 异步执行 parser.parse，不阻塞 event loop（信号量限制并发）
        async with _vision_semaphore:
            result = await asyncio.wait_for(
                asyncio.to_thread(parser.parse, img_path),
                timeout=IMAGE_TIMEOUT,
            )

        has_value = (
            result
            and result.get("index_code")
            and result.get("current_value") is not None
        )
        if not has_value:
            err = "AI 返回空值（无当前值）" if result and result.get("index_code") else "AI 未能提取到数据"
            update_analysis_record(record_id,
                status="error",
                error_msg=err,
                raw_response=json.dumps(result, ensure_ascii=False) if result else "",
            )
            return

        vid = save_valuation(
            result,
            source_image=img_path,
            source_url=article["url"] if article else None,
            snapshot_date=article.get("publish_time", "")[:10] if article else None,
        )
        update_analysis_record(record_id,
            status="success",
            index_code=result.get("index_code"),
            index_name=result.get("index_name"),
            metric_type=result.get("metric_type"),
            raw_response=json.dumps(result, ensure_ascii=False),
        )
    except asyncio.TimeoutError:
        update_analysis_record(record_id, status="timeout", error_msg="分析超时（10分钟）")
    except Exception as e:
        update_analysis_record(record_id, status="error", error_msg=str(e))
    finally:
        _reanalyze_tasks.pop(record_id, None)


# ── 个人文档 API ──────────────────────────────────────────

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".doc"}

@app.get("/api/linked-articles")
async def list_linked_articles_api(limit: int = 200):
    return list_linked_articles(limit=limit)

@app.post("/api/linked-articles")
async def upload_document(file: UploadFile):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}，仅支持 .txt / .md / .pdf / .docx")

    content = await file.read()
    file_size = len(content)
    title = Path(file.filename).stem

    article_id = create_linked_article(
        title=title, file_path="", file_size=file_size, file_type=ext.lstrip("."),
    )

    safe_name = f"{article_id}_{file.filename}"
    save_path = UPLOADS_DIR / safe_name
    save_path.write_bytes(content)

    update_linked_article_file(article_id, safe_name)

    # 异步 embedding（不阻塞响应）
    asyncio.create_task(_embed_linked_doc(article_id, ext.lstrip("."), content, title))

    return {"id": article_id}


async def _embed_linked_doc(article_id: int, file_type: str, raw_content: bytes, title: str):
    """后台任务：提取文档文本并 embedding。"""
    update_linked_article_embed_status(article_id, "embedding")
    try:
        text = ""
        if file_type in ("txt", "md"):
            text = raw_content.decode("utf-8", errors="replace")
        elif file_type == "pdf":
            from PyPDF2 import PdfReader
            import io
            reader = PdfReader(io.BytesIO(raw_content))
            pages = [p.extract_text() for p in reader.pages if p.extract_text()]
            text = "\n\n".join(pages)
        elif file_type == "docx":
            from docx import Document
            import io
            doc = Document(io.BytesIO(raw_content))
            text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if text.strip():
            chunks_count = index_to_chroma("linked_doc", str(article_id), title, text)
            from rag import _chunk_text
            chunks = _chunk_text(text)
            save_document_chunks(article_id, chunks)
            update_linked_article_embed_status(article_id, "done", chunks_count)
        else:
            update_linked_article_embed_status(article_id, "failed")
    except Exception:
        update_linked_article_embed_status(article_id, "failed")

@app.get("/api/linked-articles/{article_id}/download")
async def download_document(article_id: int):
    article = get_linked_article(article_id)
    if not article or not article.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在")
    file_path = UPLOADS_DIR / article["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")
    return FileResponse(
        str(file_path),
        filename=article["title"] + "." + article.get("file_type", ""),
        media_type="application/octet-stream",
    )

@app.get("/api/linked-articles/{article_id}/content")
async def get_document_content(article_id: int):
    article = get_linked_article(article_id)
    if not article or not article.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在")
    file_path = UPLOADS_DIR / article["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")

    file_type = article.get("file_type", "")
    content = ""

    try:
        if file_type in ("txt", "md"):
            content = file_path.read_text(encoding="utf-8", errors="replace")
        elif file_type == "pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(str(file_path))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            content = "\n\n".join(pages)
        elif file_type == "docx":
            from docx import Document
            doc = Document(str(file_path))
            content = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif file_type == "doc":
            content = "（.doc 格式暂不支持在线预览，请下载后查看）"
        else:
            content = "（不支持的文件格式）"
    except Exception as e:
        content = f"内容提取失败: {e}"

    return {"content": content, "file_type": file_type}


@app.delete("/api/linked-articles/{article_id}")
async def delete_linked_article_api(article_id: int):
    article = get_linked_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文档不存在")
    # Delete physical file
    if article.get("file_path"):
        file_path = UPLOADS_DIR / article["file_path"]
        if file_path.exists():
            file_path.unlink()
    delete_linked_article(article_id)
    return {"ok": True}


@app.post("/api/linked-articles/{article_id}/embed")
async def embed_document(article_id: int):
    """对单篇文档做 embedding 并存入向量库。"""
    article = get_linked_article(article_id)
    if not article or not article.get("file_path"):
        raise HTTPException(status_code=404, detail="文档不存在")

    file_path = UPLOADS_DIR / article["file_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")

    # 标记为 embedding 中
    update_linked_article_embed_status(article_id, "embedding")

    file_type = article.get("file_type", "")
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
            doc = Document(str(file_path))
            content = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif file_type == "doc":
            update_linked_article_embed_status(article_id, "failed")
            raise HTTPException(status_code=400, detail=".doc 格式暂不支持 embedding，请先转换为 .docx")
    except HTTPException:
        raise
    except Exception as e:
        update_linked_article_embed_status(article_id, "failed")
        raise HTTPException(status_code=500, detail=f"内容提取失败: {e}")

    if not content.strip():
        update_linked_article_embed_status(article_id, "failed")
        raise HTTPException(status_code=400, detail="文档内容为空，无法索引")

    try:
        # 分块并存入 ChromaDB
        chunks_count = index_to_chroma("linked_doc", str(article_id), article.get("title", ""), content)

        # 保存分块到 SQLite（用于展示）
        from rag import _chunk_text
        chunks = _chunk_text(content)
        save_document_chunks(article_id, chunks)

        # 更新状态
        update_linked_article_embed_status(article_id, "done", chunks_count)

        return {"ok": True, "chunks_indexed": chunks_count}
    except Exception as e:
        update_linked_article_embed_status(article_id, "failed")
        raise HTTPException(status_code=500, detail=f"Embedding 失败: {e}")


@app.get("/api/linked-articles/{article_id}/chunks")
async def get_document_chunks_api(article_id: int):
    """获取文档的分块详情。"""
    article = get_linked_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = get_document_chunks(article_id)
    return {"chunks": chunks, "total": len(chunks)}


@app.post("/api/rag/test-search")
async def rag_test_search(body: dict):
    """命中测试：输入查询词，返回 FTS5 + 向量搜索结果。"""
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    limit = body.get("limit", 5)
    content_types = body.get("content_types")

    result = build_rag_context_with_details(query, content_types=content_types, limit=limit)

    # 诊断信息
    chroma_ok = _get_chroma() is not None and _get_embed_model() is not None
    result["debug"] = {
        "chroma_available": chroma_ok,
        "fts_count": sum(1 for r in result["results"] if r.get("content_type") != "linked_doc"),
        "vector_count": sum(1 for r in result["results"] if r.get("content_type") == "linked_doc"),
        "total_in_chroma": _get_chroma().count() if _get_chroma() else 0,
    }
    return result


# ══════════════════════════════════════════════════════
# 持仓管理 API
# ══════════════════════════════════════════════════════


class CreateHoldingRequest(BaseModel):
    fund_code: str
    fund_name: str
    shares: float = 0
    cost_price: float = None
    current_price: float = None
    index_code: str = None
    index_name: str = None
    buy_date: str = None
    notes: str = None
    account: str = "花无缺"


class UpdateHoldingRequest(BaseModel):
    fund_name: str = None
    shares: float = None
    cost_price: float = None
    current_price: float = None
    index_code: str = None
    index_name: str = None
    buy_date: str = None
    notes: str = None
    account: str = None


class CreateTransactionRequest(BaseModel):
    fund_code: str
    transaction_type: str  # 'buy' | 'sell' | 'dividend'
    amount: float = 0      # 买入金额 / 卖出时可为0（pending）
    transaction_date: str
    shares: float | None = None
    price: float | None = None
    holding_id: int | None = None
    notes: str | None = None
    status: str | None = None     # 'pending' | 'confirmed' | None(默认confirmed)
    submitted_shares: float | None = None  # 卖出时提交的份额
    submitted_amount: float | None = None  # 买入时提交的金额
    transaction_time: str | None = None    # HH:MM 格式，如 14:30


class ConfirmTransactionRequest(BaseModel):
    confirmed_price: float
    confirmed_shares: float | None = None
    confirmed_amount: float | None = None


class CreateAlertRequest(BaseModel):
    alert_type: str  # risk_warning | add_position | reduce_position | news_impact | valuation_alert
    title: str
    content: str = None
    severity: str = "info"  # info | warning | danger
    related_fund_code: str = None
    related_fund_name: str = None
    source: str = None


class TagRequest(BaseModel):
    tag: str


class AdjustCashRequest(BaseModel):
    amount: float
    mode: str = "add"  # "add" 存入/支出, "set" 直接设置


@app.get("/api/portfolio")
async def list_portfolio_api(account: str = None):
    """获取所有持仓。可选 ?account=花无缺 筛选。"""
    return {"holdings": list_holdings(account=account) if account else list_holdings()}


@app.get("/api/portfolio/summary")
async def portfolio_summary_api(account: str = None):
    """获取持仓汇总。可选 ?account=花无缺 筛选。"""
    if account:
        return get_portfolio_summary(account=account)
    return get_portfolio_summary()


@app.post("/api/portfolio/clear")
async def clear_portfolio_api():
    """清空所有持仓数据。"""
    clear_all_portfolio_data()
    return {"ok": True, "message": "所有持仓数据已清空"}


@app.get("/api/portfolio/cash")
async def get_cash_api():
    """获取零钱余额。"""
    return get_cash_balance()


@app.post("/api/portfolio/cash")
async def adjust_cash_api(req: AdjustCashRequest):
    """调整零钱余额。mode='add' 时 amount 正数存入/负数支出，mode='set' 时直接设置余额。"""
    from db import add_cash, set_cash_balance
    if req.mode == "set":
        new_balance = set_cash_balance("default", req.amount)
    else:
        new_balance = add_cash("default", req.amount)
    return {"ok": True, "balance": new_balance}


@app.get("/api/portfolio/fund-nav-history/{fund_code}")
async def fund_nav_history_api(fund_code: str, days: int = 365):
    """获取基金净值历史 + 买卖点标记，用于交易行为图表。"""
    result = get_fund_nav_history(fund_code, days=days)
    if not result:
        raise HTTPException(404, f"获取 {fund_code} 净值数据失败")
    return result


@app.post("/api/portfolio")
async def create_holding_api(req: CreateHoldingRequest):
    """新增持仓。"""
    try:
        holding_id = create_holding(
        fund_code=req.fund_code, fund_name=req.fund_name,
        shares=req.shares, cost_price=req.cost_price,
        current_price=req.current_price,
        index_code=req.index_code, index_name=req.index_name,
        buy_date=req.buy_date, notes=req.notes,
        account=req.account,
        )
        return {"ok": True, "holding_id": holding_id}
    except ValueError as e:
        raise HTTPException(400, str(e))



# ── 持仓分析 API ──────────────────────────────────────────


@app.get("/api/portfolio/analysis/diversification")
async def portfolio_diversification_api():
    """分析持仓分散度：基金数量、指数分布、类型分布、仓位集中度。"""
    result = get_portfolio_diversification()

    # 补充 MCP 分析数据（每个 MCP 调用独立 try/except，返回状态信息）
    mcp_data = {}
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        holdings = list_holdings()
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code") and h["fund_code"].strip()]

        if not fund_codes:
            mcp_data["error"] = "持仓中没有有效的基金代码"
        else:
            # 构建持仓映射 {fund_code: current_value} 用于 MCP 参数
            holding_map = {h["fund_code"]: h.get("current_value", 0) or 0 for h in holdings if h.get("fund_code")}

            # 资产大类穿透分析（需 holdingList: [{fundCode, amount}]）
            try:
                raw = mcp.call_tool_text("GetFundAssetClassAnalysis", {
                    "holdingList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes],
                })
                mcp_data["asset_class"] = {"status": "ok", "data": raw}
            except Exception as e:
                mcp_data["asset_class"] = {"status": "error", "message": str(e)}

            # 基金相关性分析（需 fundList: [{fundCode}]）
            try:
                raw = mcp.call_tool_text("GetFundsCorrelation", {
                    "fundList": [{"fundCode": c} for c in fund_codes],
                })
                mcp_data["correlation"] = {"status": "ok", "data": raw}
            except Exception as e:
                mcp_data["correlation"] = {"status": "error", "message": str(e)}

            # 持仓最大基金的行业配置
            try:
                top = max(holdings, key=lambda h: (h.get("current_value", 0) or 0))
                raw = mcp.call_tool_text("getFundIndustryAllocation", {
                    "fundCode": top["fund_code"],
                })
                mcp_data["top_holding_industry"] = {
                    "status": "ok",
                    "fund_name": top.get("fund_name", ""),
                    "fund_code": top["fund_code"],
                    "data": raw,
                }
            except Exception as e:
                mcp_data["top_holding_industry"] = {"status": "error", "message": str(e)}

            # 市场行情
            try:
                raw = mcp.get_latest_quotations()
                mcp_data["market"] = {"status": "ok", "data": raw}
            except Exception as e:
                mcp_data["market"] = {"status": "error", "message": str(e)}
    except Exception as e:
        mcp_data["error"] = f"MCP 客户端异常: {e}"

    result["mcp"] = mcp_data
    return result


# ── MCP 解析辅助函数（分散度分析使用） ──

def _parse_mcp_pct_pairs(text: str) -> list[tuple[str, float]]:
    """提取文本中的(标签, 百分比)对，如 ('股票', 85.0)。"""
    try:
        return [(m.group(1), float(m.group(2)))
                for m in re.finditer(r'([一-龥]{2,})\s*[:：]?\s*(\d+\.?\d*)%', text)]
    except Exception:
        return []


def _parse_mcp_correlation(text: str) -> list[dict]:
    """提取基金对相关系数，返回 [{"fund_a": str, "fund_b": str, "coefficient": float}]。"""
    pairs = []
    try:
        # 匹配 "A 和 B 的相关系数为 0.88" / "A vs B: 0.88" / "A, B, 0.88"
        for m in re.finditer(
            r'([一-龥a-zA-Z]{2,})[\s和vsVS、,]+([一-龥a-zA-Z]{2,})[\s\S]{0,40}?(\d+\.\d{2})',
            text,
        ):
            c = float(m.group(3))
            if 0 < c <= 1:
                pairs.append({"fund_a": m.group(1).strip(), "fund_b": m.group(2).strip(), "coefficient": c})
    except Exception:
        pass
    return pairs


@app.post("/api/portfolio/analysis/diversification/ai-summary")
async def portfolio_diversification_ai_summary(agent_id: int = 2):
    """基于 MCP + 持仓数据，生成 AI 分散度分析解读。"""
    # 1. 获取 agent 配置
    agent = get_analysis_agent(agent_id)
    if not agent:
        raise HTTPException(404, "分析 Agent 不存在")
    system_prompt = agent["system_prompt"]

    # 2. 获取持仓 + 分散度数据
    result = get_portfolio_diversification()
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

    total_value = result.get('total_value', 1) or 1

    # 3. 预计算：基金集中度检验（本地数据，高置信度）
    concentration_items = []
    for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
        pct = (h.get('current_value', 0) or 0) / total_value * 100
        if pct > 25:
            level = "⚠️ 高度集中"
        elif pct > 15:
            level = "⚡ 适度集中"
        else:
            level = "✅ 合理"
        concentration_items.append(f"- {level} {h.get('fund_name','')}({h.get('fund_code','')}): {pct:.1f}%（阈值: >25%高度集中, >15%适度集中）")

    # 4. 获取 MCP 数据 + 结构化提取
    mcp_raw_sections = []
    mcp_parsed = {"correlation": [], "asset_class_pcts": [], "industry_pcts": {}}

    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code") and h["fund_code"].strip()]

        if fund_codes:
            holding_map = {h["fund_code"]: h.get("current_value", 0) or 0 for h in holdings if h.get("fund_code")}

            # 4a. 资产大类穿透分析
            try:
                raw = mcp.call_tool_text("GetFundAssetClassAnalysis", {
                    "holdingList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
                })
                mcp_raw_sections.append(f"【资产大类穿透分析】\n{raw}")
                mcp_parsed["asset_class_pcts"] = _parse_mcp_pct_pairs(raw)
            except Exception as e:
                mcp_raw_sections.append(f"【资产大类穿透分析】调用失败: {e}")

            # 4b. 基金相关性分析
            try:
                raw = mcp.call_tool_text("GetFundsCorrelation", {
                    "fundList": [{"fundCode": c} for c in fund_codes]
                })
                mcp_raw_sections.append(f"【基金相关性分析】\n{raw}")
                mcp_parsed["correlation"] = _parse_mcp_correlation(raw)
            except Exception as e:
                mcp_raw_sections.append(f"【基金相关性分析】调用失败: {e}")

            # 4c. 行业配置（top 5 基金，原来是 top 1）
            sorted_holdings = sorted(holdings, key=lambda h: (h.get("current_value", 0) or 0), reverse=True)
            for h in sorted_holdings[:5]:
                fc = h.get("fund_code", "")
                if fc and fc.strip():
                    try:
                        raw = mcp.call_tool_text("getFundIndustryAllocation", {"fundCode": fc})
                        label = f"{h.get('fund_name','')}行业配置"
                        mcp_raw_sections.append(f"【{label}】\n{raw}")
                        mcp_parsed["industry_pcts"][fc] = {"name": h.get("fund_name", ""), "pcts": _parse_mcp_pct_pairs(raw)}
                    except Exception:
                        pass

            # 4d. 组合诊断（新增：MCP 第三方综合诊断作为参考）
            try:
                raw = mcp.call_tool_text("DiagnoseFundPortfolio", {
                    "fundList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
                })
                mcp_raw_sections.append(f"【组合诊断】\n{raw}")
            except Exception:
                pass

            # 4e. 市场行情
            try:
                raw = mcp.get_latest_quotations()
                mcp_raw_sections.append(f"【市场行情】\n{raw}")
            except Exception:
                pass

            # 4f. 政策热点新闻
            try:
                hot_raw = mcp.call_tool_text("SearchHotTopic", {})
                mcp_raw_sections.append(f"【市场热点】\n{hot_raw}")
            except Exception:
                pass
            # 对主要持仓行业搜索相关新闻
            industry_keywords = set()
            for h in sorted_holdings[:3]:
                idx = (h.get("index_name") or "").strip()
                if idx and idx != "该基金无跟踪标的":
                    kw = idx.replace("指数", "").replace("中证", "").replace("全指", "").strip()
                    if kw:
                        industry_keywords.add(kw)
            for kw in industry_keywords:
                try:
                    news_raw = mcp.call_tool_text("SearchFinancialNews", {"keyword": kw, "pageSize": 3})
                    mcp_raw_sections.append(f"【{kw}相关新闻】\n{news_raw}")
                except Exception:
                    pass
    except Exception as e:
        mcp_raw_sections.append(f"MCP 数据获取异常: {e}")

    # 5. 预计算：相关性阈值检验
    correlation_lines = []
    if mcp_parsed["correlation"]:
        for cp in mcp_parsed["correlation"]:
            c = cp["coefficient"]
            if c >= 0.85:
                correlation_lines.append(f"- ⚠️ {cp['fund_a']} vs {cp['fund_b']}: {c:.2f}（强同向波动 ≥0.85）")
            elif c >= 0.7:
                correlation_lines.append(f"- ⚡ {cp['fund_a']} vs {cp['fund_b']}: {c:.2f}（中等相关 0.7-0.85）")
            else:
                correlation_lines.append(f"- ✅ {cp['fund_a']} vs {cp['fund_b']}: {c:.2f}（分散有效 <0.7）")
    if not correlation_lines:
        correlation_lines.append("（相关性数据暂缺，无法检验）")

    correlation_block = "\n".join(correlation_lines)

    # 6. 预计算：行业集中度汇总（从解析的 MCP 行业数据中提取）
    industry_summary_lines = []
    total_industry_pcts = {}
    for fc, indata in mcp_parsed["industry_pcts"].items():
        for label, pct in indata["pcts"]:
            total_industry_pcts[label] = total_industry_pcts.get(label, 0) + pct
    if total_industry_pcts:
        sorted_industries = sorted(total_industry_pcts.items(), key=lambda x: -x[1])
        industry_summary_lines.append(f"累计行业暴露（top {len(mcp_parsed['industry_pcts'])} 只基金）:")
        for label, pct in sorted_industries[:5]:
            flag = "⚠️" if pct > 35 else ("⚡" if pct > 20 else "✅")
            industry_summary_lines.append(f"- {flag} {label}: {pct:.0f}%（阈值: >35%过高, >20%偏高）")
    industry_block = "\n".join(industry_summary_lines) if industry_summary_lines else "（行业数据暂缺）"

    # 7. 资产大类汇总
    asset_class_block = ""
    if mcp_parsed["asset_class_pcts"]:
        asset_lines = [f"- {label}: {pct:.0f}%" for label, pct in mcp_parsed["asset_class_pcts"]]
        asset_class_block = "底层资产穿透:\n" + "\n".join(asset_lines)

    # 7.5 估值参考（查询持仓基金跟踪指数的估值数据）
    valuation_ref_lines = []
    seen_indexes = set()
    for h in holdings:
        idx_name = (h.get("index_name") or "").strip()
        if not idx_name or idx_name == "该基金无跟踪标的" or idx_name in seen_indexes:
            continue
        seen_indexes.add(idx_name)
        try:
            matches = search_indexes_by_keyword(idx_name.replace("指数", ""))
            for m in matches:
                val = get_latest_valuation(m["index_code"])
                if val and val.get("percentile") is not None:
                    pct = val["percentile"]
                    z = val.get("zscore")
                    metric = val.get("metric_type", "")
                    current_val = val.get("current_value")
                    level = "🔥 高估" if pct > 80 else ("⚠️ 偏高" if pct > 50 else "✅ 低估" if pct < 20 else "⚡ 适中")
                    z_note = f"z-score={z:+.2f}" if z is not None else ""
                    valuation_ref_lines.append(
                        f"- {m['index_name']}({metric}={current_val}, "
                        f"历史分位={pct:.1f}% {level} {z_note})"
                    )
                    break
        except Exception:
            pass
    valuation_block = ""
    if valuation_ref_lines:
        valuation_block = "跟踪指数估值参考:\n" + "\n".join(valuation_ref_lines)
        valuation_block += "\n\n💡 估值分位<20%为低估区域，可适度容忍集中；>80%为高估区域，宜警惕集中风险。"

    # 8. 拼装 LLM prompt（预计算分析 + 原始 MCP 数据）
    holdings_text = "\n".join(
        f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
        f"持仓占比 {(h.get('current_value',0) or 0) / total_value * 100:.1f}%, "
        f"盈亏 {h.get('profit_loss',0):+.2f}元"
        for h in holdings
    )

    mcp_raw_block = "\n\n".join(mcp_raw_sections) if mcp_raw_sections else "（无 MCP 数据）"

    user_content = f"""## 持仓概览
持有基金 {result.get('holding_count',0)} 只 | 总投资 {result.get('total_cost',0):.0f}元 | 总市值 {result.get('total_value',0):.0f}元

## 持仓明细
{holdings_text}

## 类型分布
{json.dumps(result.get('type_distribution',{}), ensure_ascii=False)}

## 📊 预计算分析（系统已自动计算以下指标供你参考）

### 基金集中度检验
{chr(10).join(concentration_items)}

### 相关性检验
{correlation_block}

### 行业集中度（基于 MCP 行业配置数据）
{industry_block}

{f"### 资产大类穿透\n{asset_class_block}" if asset_class_block else ""}

{f"### 估值参考（跟踪指数）\n{valuation_block}" if valuation_block else ""}

## 📄 MCP 原始数据（供验证和深度参考）
{mcp_raw_block}

请对以上持仓分散度进行专业解读。"""

    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="diversification_analysis",
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        analysis = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        raise HTTPException(500, f"AI 分析失败: {e}")

    # 保存记录
    record_id = create_portfolio_analysis_record(
        analysis_type="diversification_ai",
        summary=f"分散度解读 · {result.get('holding_count',0)}只基金",
        input_data=json.dumps({"holdings": result}, ensure_ascii=False),
        result_data=analysis,
        token_usage=tokens,
        agent_id=agent_id,
    )

    return {"id": record_id, "result": analysis, "token_usage": tokens}


@app.get("/api/portfolio/analysis/ai-summary/today-status")
async def portfolio_ai_summary_today_status():
    """查询今天是否已有 AI 分散度分析结果。"""
    records = list_portfolio_analysis_records(analysis_type="diversification_ai", limit=5)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    for r in records:
        if r.get("created_at", "").startswith(today):
            return {"analyzed_today": True, "record_id": r["id"]}
    return {"analyzed_today": False, "record_id": None}


@app.get("/api/portfolio/analysis/{holding_id}/performance")
async def holding_performance_api(holding_id: int):
    """分析单只持仓基金的投资表现。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    fund_code = holding["fund_code"]
    txs = list_transactions(fund_code=fund_code, limit=100)
    buy_txs = [t for t in txs if t["transaction_type"] == "buy"]
    sell_txs = [t for t in txs if t["transaction_type"] == "sell"]
    buy_total = sum(t.get("amount", 0) or 0 for t in buy_txs)
    sell_total = sum(t.get("amount", 0) or 0 for t in sell_txs)
    return {
        "fund_code": fund_code,
        "fund_name": holding.get("fund_name", ""),
        "shares": holding.get("shares", 0),
        "cost_price": holding.get("cost_price", 0),
        "current_price": holding.get("current_price", 0),
        "total_cost": holding.get("total_cost", 0),
        "current_value": holding.get("current_value", 0),
        "profit_loss": round(holding.get("profit_loss", 0) or 0, 2),
        "profit_rate": round((holding.get("profit_rate", 0) or 0) * 100, 2),
        "buy_count": len(buy_txs),
        "sell_count": len(sell_txs),
        "buy_total": round(buy_total, 2),
        "sell_total": round(sell_total, 2),
    }


@app.get("/api/portfolio/analysis/transactions-summary")
async def transactions_summary_api():
    """交易行为汇总分析。"""
    return get_transaction_summary()


class PortfolioAiAnalysisRequest(BaseModel):
    question: str = ""


@app.post("/api/portfolio/analysis/ai")
async def portfolio_ai_analysis_api(req: PortfolioAiAnalysisRequest):
    """AI 持仓分析：调用 MCP 工具获取专业数据 + LLM 生成分析报告。"""
    # 1. 获取持仓数据
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

    # 2. 调用 MCP 工具
    mcp_context = {}
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()

        # 并行调用多个 MCP 工具
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code")]

        # 组合诊断（所有基金）
        if fund_codes:
            try:
                mcp_context["portfolio_diagnosis"] = mcp.diagnose_portfolio(fund_codes)
            except Exception as e:
                mcp_context["portfolio_diagnosis"] = f"诊断失败: {e}"

        # 市场行情
        try:
            mcp_context["market_quotations"] = mcp.get_latest_quotations()
        except Exception as e:
            mcp_context["market_quotations"] = f"行情获取失败: {e}"

        # 热点话题
        try:
            mcp_context["hot_topics"] = mcp.get_hot_topics()
        except Exception as e:
            mcp_context["hot_topics"] = f"热点获取失败: {e}"

        # 各基金诊断（最多3只，避免 token 过多）
        fund_diagnoses = {}
        for code in fund_codes[:3]:
            try:
                fund_diagnoses[code] = mcp.get_fund_diagnosis(code)
            except Exception as e:
                fund_diagnoses[code] = f"诊断失败: {e}"
        mcp_context["fund_diagnoses"] = fund_diagnoses

    except ImportError:
        mcp_context["error"] = "MCP 客户端未配置"
    except Exception as e:
        mcp_context["error"] = f"MCP 调用异常: {e}"

    # 3. 拼装 LLM 上下文
    holdings_summary = []
    for h in holdings:
        holdings_summary.append(
            f"- {h.get('fund_name', '')}({h.get('fund_code', '')}): "
            f"持有 {h.get('shares', 0)} 份, "
            f"成本价 {h.get('cost_price', 'N/A')}, "
            f"当前净值 {h.get('current_price', 'N/A')}, "
            f"盈亏 {h.get('profit_loss', 0):.2f}元"
        )

    user_question = req.question or "请全面分析我的持仓情况，包括资产配置合理性、风险分散度、各基金表现，以及改进建议。"

    system_prompt = """你是一位专业的投资组合分析师。请根据以下持仓数据和专业分析工具的输出，给出全面的投资组合分析报告。

分析要求：
1. **资产配置分析** — 持仓的基金类型分布、行业分布是否合理
2. **风险评价** — 组合的集中度风险、相关性风险、回撤风险
3. **各基金表现** — 逐个评价每只基金的表现（收益、波动、性价比）
4. **改进建议** — 具体的调仓建议、定投策略、风险控制措施
5. **市场环境** — 结合当前市场行情和热点，给出背景判断

输出格式：使用 Markdown 标题层级，结论先行，数据支撑，内容专业易懂。"""

    user_content = f"""## 当前持仓
{chr(10).join(holdings_summary)}

## 专业分析数据
{json.dumps(mcp_context, ensure_ascii=False, indent=2)}

## 用户问题
{user_question}

请给出全面的分析报告。"""

    # 4. 调用 LLM
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_analysis",
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"AI 分析失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    # 5. 保存记录
    record_id = create_portfolio_analysis_record(
        analysis_type="ai",
        summary=f"AI持仓分析 · {len(holdings)}只基金",
        input_data=json.dumps({
            "holdings": [{k: h.get(k) for k in ("fund_code", "fund_name", "shares", "cost_price", "current_price", "profit_loss")} for h in holdings],
            "question": user_question,
        }, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
    )

    return {
        "id": record_id,
        "result": result_text,
        "token_usage": tokens,
        "holdings_count": len(holdings),
        "mcp_used": list(mcp_context.keys()),
    }


@app.get("/api/portfolio/analysis/ai-records")
async def list_ai_analysis_records_api(limit: int = 20):
    """列出 AI 持仓分析记录。"""
    records = list_portfolio_analysis_records(analysis_type="ai", limit=limit)
    return {"records": records}


@app.get("/api/portfolio/analysis/ai-records/{record_id}")
async def get_ai_analysis_record_api(record_id: int):
    """获取单条 AI 持仓分析记录详情。"""
    record = get_portfolio_analysis_record(record_id)
    if not record:
        raise HTTPException(404, "分析记录不存在")
    return record


@app.delete("/api/portfolio/analysis/ai-records/{record_id}")
async def delete_ai_analysis_record_api(record_id: int):
    """删除 AI 持仓分析记录。"""
    if not delete_portfolio_analysis_record(record_id):
        raise HTTPException(404, "分析记录不存在")
    return {"ok": True}


class FeedbackRequest(BaseModel):
    feedback: str
    note: str = ""


@app.post("/api/portfolio/analysis/feedback/{record_id}")
async def submit_analysis_feedback_api(record_id: int, req: FeedbackRequest):
    """提交对分析结果的反馈。"""
    if req.feedback not in ("helpful", "unhelpful"):
        raise HTTPException(400, "feedback 必须为 helpful 或 unhelpful")
    if not update_analysis_feedback(record_id, req.feedback, req.note):
        raise HTTPException(404, "分析记录不存在")
    return {"ok": True}


@app.get("/api/portfolio/analysis/bad-cases")
async def list_bad_cases_api(analysis_type: str = None, limit: int = 50):
    """列出被标记为 unhelpful 的分析记录（Bad Cases）。"""
    cases = list_bad_cases(analysis_type=analysis_type, limit=limit)
    return {"cases": cases, "count": len(cases)}


# ── AI 持仓分析 4 模式 ─────────────────────────────────────


class PanoramaAnalysisRequest(BaseModel):
    """全景诊断请求。"""
    pass  # 无参数，基于当前持仓分析


class DeepDiveRequest(BaseModel):
    """单基金深度分析请求。"""
    pass  # holding_id 通过路径参数传入


class TradeReviewRequest(BaseModel):
    """交易复盘请求。"""
    start_date: str | None = None
    end_date: str | None = None


class WhatIfRequest(BaseModel):
    """情景推演请求。"""
    scenario: str  # 'market_drop' | 'repair_to_median' | 'repair_to_opportunity'
    parameter: float | None = None  # 市场下跌场景的跌幅百分比


def _get_valuation_context() -> str:
    """获取当前所有持仓的估值数据摘要。"""
    try:
        indexes = list_valuation_indexes()
        lines = []

        # 新鲜度总览
        freshness = list_index_freshness()
        stale = [f for f in freshness if f.get("stale_days", 0) >= 10]
        if stale:
            lines.append("[数据新鲜度] 以下指数数据超过10天未更新: " +
                         ", ".join(f"{f['index_name']}({int(f['stale_days'])}天)" for f in stale[:5]))

        for idx in indexes[:20]:
            val = get_latest_valuation(idx.get("index_code", ""))
            if val:
                pe_percentile = val.get("pe_percentile", "N/A")
                pb_percentile = val.get("pb_percentile", "N/A")
                date_str = val.get("snapshot_date", "")
                stale_mark = ""
                if date_str:
                    from datetime import date as dt_date
                    try:
                        d = dt_date.fromisoformat(str(date_str))
                        sd = (dt_date.today() - d).days
                        if sd >= 10:
                            stale_mark = f" [数据过期{sd}天]"
                    except:
                        pass
                lines.append(f"- {idx.get('index_name','')}({idx.get('index_code','')}): PE分位 {pe_percentile}, PB分位 {pb_percentile} [{date_str}]{stale_mark}")
        if lines:
            return "## 估值参考（跟踪指数）\n" + "\n".join(lines)
        return "## 估值参考\n暂无估值数据"
    except Exception as e:
        return f"## 估值参考\n获取失败: {e}"


def _format_news_section(mcp_context: dict) -> str:
    """从 mcp_context 中提取新闻数据并格式化为 Markdown 段落。"""
    parts = []

    # 市场行情
    quotations = mcp_context.get("market_quotations", "")
    if quotations and not quotations.startswith("获取失败") and not quotations.startswith("调用失败"):
        parts.append(f"### 市场行情\n{quotations[:1500]}")

    # MCP 热点新闻（带关键词搜索的）
    news_map = mcp_context.get("news", {})
    if isinstance(news_map, dict) and news_map:
        hot_news = []
        fund_news = []
        for key, val in news_map.items():
            if isinstance(val, str) and not val.startswith("获取失败"):
                if key in ("market_hot", "market_trend"):
                    hot_news.append(val[:1000])
                else:
                    fund_news.append(val[:800])
        if hot_news:
            parts.append("### 近期市场热点\n" + "\n---\n".join(hot_news))
        if fund_news:
            parts.append("### 持仓相关新闻\n" + "\n---\n".join(fund_news))

    # akshare 实时新闻
    akshare = mcp_context.get("akshare_news", [])
    if isinstance(akshare, list) and akshare:
        news_lines = []
        for item in akshare:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            time = item.get("time", "")
            source = item.get("source", "")
            if title:
                news_lines.append(f"- **{title}**  {snippet[:120]}")
        if news_lines:
            parts.append("### 实时财经新闻\n" + "\n".join(news_lines[:8]))

    # hot_topics（兜底）
    hot_topics = mcp_context.get("hot_topics", "")
    if hot_topics and not hot_topics.startswith("获取失败") and not hot_topics.startswith("调用失败"):
        if not news_map:  # 只有 news 搜索失败时才展示 hot_topics
            parts.append(f"### 市场热点\n{hot_topics[:1500]}")

    if not parts:
        return "## 新闻热点\n暂无最新新闻数据。"

    return "## 新闻热点\n" + "\n\n".join(parts)


def _add_akshare_news(mcp_context: dict, max_results: int = 8):
    """用 akshare 补充实时财经新闻。"""
    try:
        import akshare as ak
        news_items = []
        # 东方财富 A 股新闻
        try:
            df = ak.stock_news_em(symbol="A股")
            if df is not None and len(df) > 0:
                for _, row in df.head(max_results).iterrows():
                    news_items.append({
                        "title": str(row.get("新闻标题", "")),
                        "snippet": str(row.get("新闻内容", ""))[:200],
                        "time": str(row.get("发布时间", "")),
                        "source": "东方财富",
                    })
        except Exception:
            pass
        # 央视新闻补充
        if len(news_items) < 3:
            try:
                from datetime import datetime
                df2 = ak.news_cctv(date=datetime.now().strftime("%Y%m%d"))
                if df2 is not None and len(df2) > 0:
                    for _, row in df2.head(max_results - len(news_items)).iterrows():
                        title = str(row.get("title", ""))
                        if any(kw in title for kw in ["股", "基金", "央行", "利率", "经济", "金融", "市场", "投资", "GDP", "通胀", "行情"]):
                            news_items.append({
                                "title": title,
                                "snippet": str(row.get("content", ""))[:200],
                                "time": str(row.get("date", "")),
                                "source": "央视新闻",
                            })
            except Exception:
                pass
        if news_items:
            mcp_context["akshare_news"] = news_items
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"akshare 新闻获取异常: {e}")


def _get_mcp_context(holdings: list[dict]) -> dict:
    """获取 MCP 相关数据，包含实时市场新闻和热点搜索。"""
    mcp_context = {}
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code") and h["fund_code"].strip()]

        if not fund_codes:
            return {}

        holding_map = {h["fund_code"]: h.get("current_value", 0) or 0 for h in holdings if h.get("fund_code")}

        # 资产大类穿透
        try:
            raw = mcp.call_tool_text("GetFundAssetClassAnalysis", {
                "holdingList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
            })
            mcp_context["asset_class_analysis"] = raw
        except Exception as e:
            mcp_context["asset_class_analysis"] = f"调用失败: {e}"

        # 基金相关性
        try:
            raw = mcp.call_tool_text("GetFundsCorrelation", {
                "fundList": [{"fundCode": c} for c in fund_codes]
            })
            mcp_context["correlation"] = raw
        except Exception as e:
            mcp_context["correlation"] = f"调用失败: {e}"

        # 组合诊断
        try:
            raw = mcp.call_tool_text("DiagnoseFundPortfolio", {
                "fundList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
            })
            mcp_context["portfolio_diagnosis"] = raw
        except Exception as e:
            mcp_context["portfolio_diagnosis"] = f"调用失败: {e}"

        # 市场行情
        try:
            mcp_context["market_quotations"] = mcp.get_latest_quotations()
        except Exception as e:
            mcp_context["market_quotations"] = f"获取失败: {e}"

        # ── 实时热点新闻 — 精简版，只查市场热点 + 前3持仓 ──
        try:
            news_map = {}

            # 市场整体热点（2个关键词）
            for kw, label in [("A股 热门 板块 基金", "market_hot"), ("市场热点 行情", "market_trend")]:
                try:
                    news_map[label] = mcp.search_news(kw, 5)
                except Exception:
                    pass

            # 前3大持仓相关新闻
            top3 = sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True)[:3]
            for h in top3:
                name = h.get("fund_name", "") or ""
                kw = name.replace("ETF", "").replace("联接", "").replace("基金", "").replace("LOF", "").strip()[:10]
                if len(kw) >= 2:
                    try:
                        news_map[f"fund_{h.get('fund_code','')}"] = mcp.search_news(kw, 3)
                    except Exception:
                        pass

            if news_map:
                mcp_context["news"] = news_map

            # 热点话题
            try:
                mcp_context["hot_topics"] = mcp.call_tool_text("SearchHotTopic", {"keyword": "市场热点 热门基金"})
            except Exception:
                try:
                    mcp_context["hot_topics"] = mcp.call_tool_text("SearchHotTopic", {})
                except Exception as e:
                    mcp_context["hot_topics"] = f"获取失败: {e}"

        except Exception as e:
            mcp_context["news"] = f"获取失败: {e}"

        # 用 akshare 补充实时新闻
        _add_akshare_news(mcp_context)

        # 各基金诊断（最多3只）
        for code in fund_codes[:3]:
            try:
                mcp_context[f"fund_diagnosis_{code}"] = mcp.call_tool_text("GetFundDiagnosis", {"fundCode": code})
            except Exception:
                pass

    except ImportError:
        mcp_context["error"] = "MCP 客户端未配置"
    except Exception as e:
        mcp_context["error"] = f"MCP 调用异常: {e}"
    return mcp_context


@app.post("/api/portfolio/analysis/panorama")
async def panorama_analysis_api(req: PanoramaAnalysisRequest):
    """模式 1：全景诊断 — 从全局视角诊断投资组合健康状况。"""
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

    agent = get_analysis_agent(3)
    if not agent:
        raise HTTPException(404, "全景诊断分析师未配置")
    system_prompt = agent["system_prompt"]

    # 收集数据
    diversification = get_portfolio_diversification()
    total_value = diversification.get('total_value', 1) or 1

    # 持仓明细
    holdings_lines = []
    for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
        pct = (h.get('current_value', 0) or 0) / total_value * 100
        holdings_lines.append(
            f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
            f"账户 {h.get('account','花无缺')}, "
            f"市值 {h.get('current_value',0):.2f}, "
            f"盈亏 {h.get('profit_loss',0):.2f} ({h.get('profit_rate',0)*100:.1f}%), "
            f"占比 {pct:.1f}%"
        )

    # 类型分布
    type_dist = diversification.get('type_distribution', {})
    type_lines = [f"  - {k}: {v:.1f}%" for k, v in type_dist.items()]

    # MCP 数据
    mcp_context = _get_mcp_context(holdings)

    # 估值数据
    valuation_context = _get_valuation_context()

    # 从 mcp_context 中提取新闻数据，单独格式化
    news_section = _format_news_section(mcp_context)

    user_content = (
        f"## 持仓明细\n" + "\n".join(holdings_lines) +
        f"\n\n## 类型分布\n" + "\n".join(type_lines) +
        f"\n\n## 集中度\n- 前3大持仓占比: {diversification.get('top3_concentration', 0):.1f}%"
        f"\n- 前5大持仓占比: {diversification.get('top5_concentration', 0):.1f}%\n"
        f"\n## MCP 专业数据\n{json.dumps(mcp_context, ensure_ascii=False, indent=2)}\n"
        f"\n{valuation_context}"
        f"\n\n{news_section}"
    )

    # 调用 LLM（带 90 秒超时）
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.wait_for(asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_panorama",
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        )), timeout=90)
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except asyncio.TimeoutError:
        logger.error(f"全景诊断 LLM 调用超时")
        raise HTTPException(504, "AI 分析超时，请重试")
    except Exception as e:
        logger.error(f"全景诊断失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    # 保存记录
    record_id = create_portfolio_analysis_record(
        analysis_type="panorama",
        summary=f"全景诊断 · {len(holdings)}只基金",
        input_data=json.dumps({"holdings_count": len(holdings), "total_value": diversification.get('total_value')}, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=3,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


def _get_fund_mcp_diagnosis(fund_code: str) -> str:
    """获取单只基金的 MCP 诊断数据。"""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        # 基金诊断
        diagnosis = mcp.call_tool_text("GetFundDiagnosis", {"fundCode": fund_code})
        # 相关性（如果 MCP 支持单只查询）
        return diagnosis or "MCP 诊断不可用"
    except ImportError:
        return "MCP 客户端未配置"
    except Exception as e:
        return f"MCP 诊断获取失败: {e}"


@app.post("/api/portfolio/analysis/deep-dive/{holding_id}")
async def fund_deep_dive_api(holding_id: int, req: DeepDiveRequest):
    """模式 2：单基金深度分析 — 分析买入质量、持有收益、操作记录。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")

    agent = get_analysis_agent(4)
    if not agent:
        raise HTTPException(404, "基金深度分析师未配置")

    fund_code = holding["fund_code"]
    fund_name = holding.get("fund_name", "")

    # 1) 交易记录
    txs = list_transactions(fund_code=fund_code, limit=100)
    tx_lines = []
    for t in sorted(txs, key=lambda x: x.get("transaction_date", "")):
        tx_lines.append(
            f"- {t['transaction_date']} {'买入' if t['transaction_type']=='buy' else '卖出'}: "
            f"金额 {t.get('amount',0):.2f}, 份额 {t.get('shares',0):.4f}, "
            f"价格 {t.get('price',0):.4f}, 状态 {t.get('status','confirmed')}"
        )

    # 2) 估值历史 — 带趋势
    valuation_section = ""
    try:
        index_code = holding.get("index_code") or ""
        if index_code:
            hist = get_valuation_history(index_code, days=365)
            if hist and len(hist) > 0:
                latest_day = hist[-1]
                # 当前值
                parts = []
                for mt in ["pe", "pb"]:
                    lv = get_latest_valuation(index_code, mt)
                    if lv:
                        pct = lv.get("percentile", "N/A")
                        val = lv.get("current_value", "N/A")
                        parts.append(f"{mt.upper()}: {val} (分位 {pct}%)")
                if parts:
                    valuation_section += "当前估值: " + ", ".join(parts) + "\n"
                # 趋势（近30天分位变化）
                recent = hist[-30:]
                if len(recent) >= 5:
                    pe_pcts = [get_latest_valuation(index_code, "pe").get("percentile") for _ in recent[:5]]
                    # 简化：月初 vs 月末
                    first = recent[0]
                    last = recent[-1]
                    valuation_section += (
                        f"估值趋势: {first.get('date','?')}→{last.get('date','?')}, "
                        f"PE {first.get('current_value','?')}→{last.get('current_value','?')}\n"
                    )
                # 买入时估值
                if txs:
                    first_buy = next((t for t in txs if t["transaction_type"] == "buy"), None)
                    if first_buy:
                        buy_date = first_buy.get("transaction_date", "")
                        if buy_date:
                            buy_val = get_latest_valuation(index_code, "pe")
                            if buy_val:
                                valuation_section += f"首次买入时估值: PE {buy_val.get('current_value','?')} (分位 {buy_val.get('percentile','?')}%)\n"
            else:
                valuation_section = "暂无估值历史数据\n"
        else:
            val = get_latest_valuation(fund_code)
            if val:
                valuation_section = f"当前PE分位: {val.get('percentile','N/A')}%\n"
            else:
                valuation_section = "该基金无跟踪指数数据\n"
    except Exception as e:
        valuation_section = f"估值获取失败: {e}\n"

    # 3) 基金基本面（重仓股、行业配置、资产配置）
    fundamentals_section = ""
    try:
        info = lookup_fund_info(fund_code)
        if info:
            cnt_lines = []
            for k in ("fund_name", "fund_type", "index_name", "management_rate", "custody_rate", "fund_scale", "establish_date"):
                v = info.get(k)
                if v is not None:
                    cnt_lines.append(f"- {k}: {v}")
            if cnt_lines:
                fundamentals_section += "### 基金基本信息\n" + "\n".join(cnt_lines) + "\n"

        holdings_data = get_fund_holdings(fund_code)
        if holdings_data:
            # 重仓股
            stocks = holdings_data.get("top_stocks", [])
            if stocks:
                stock_lines = [f"  - {s['stock_name']}({s.get('stock_code','')}): {s.get('pct_nav','?')}%" for s in stocks[:5]]
                fundamentals_section += "### 重仓股\n" + "\n".join(stock_lines) + "\n"
            # 资产配置
            alloc = holdings_data.get("asset_allocation", [])
            if alloc:
                alloc_lines = [f"  - {a['type']}: {a['pct']}%" for a in alloc]
                fundamentals_section += "### 资产配置\n" + "\n".join(alloc_lines) + "\n"
            # 债券类型
            bt = holdings_data.get("bond_type_summary", {})
            if bt:
                bt_lines = [f"  - {k}: {v}%" for k, v in bt.items() if v]
                if bt_lines:
                    fundamentals_section += "### 债券类型\n" + "\n".join(bt_lines) + "\n"
    except Exception as e:
        fundamentals_section = f"基本面获取失败: {e}\n"

    # 4) 组合上下文 — 该基金在整体组合中的位置
    portfolio_context = ""
    try:
        all_holdings = list_holdings()
        total_value = sum((h.get("current_value", 0) or 0) for h in all_holdings)
        this_value = holding.get("current_value", 0) or 0
        this_pct = this_value / total_value * 100 if total_value > 0 else 0
        # 相关性数据（从 MCP 获取）
        portfolio_context += f"该基金在组合中占比: {this_pct:.1f}%\n"
        # 与其他基金的对比
        if len(all_holdings) > 1:
            others = [(h.get("fund_name",""), h.get("current_value",0) or 0) for h in all_holdings if h["id"] != holding_id]
            others.sort(key=lambda x: x[1], reverse=True)
            portfolio_context += "组合中其他主要持仓: " + ", ".join(f"{n}(市值{v:.0f})" for n, v in others[:3]) + "\n"
    except Exception as e:
        portfolio_context = f"组合上下文获取失败: {e}\n"

    # 5) 新闻/市场上下文（简版，基于指数名或基金名搜索）
    news_context = ""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        # 用基金名或指数名搜相关新闻
        kw = (fund_name or index_code or "")[:10]
        if kw and len(kw) >= 2:
            news_text = mcp.search_news(kw, 3)
            if news_text and not news_text.startswith("获取失败"):
                news_context = f"### 相关新闻\n{news_text[:1500]}"
        # 补充大盘新闻
        try:
            import akshare as ak
            df = ak.stock_news_em(symbol="A股")
            if df is not None and len(df) > 0:
                ak_lines = []
                for _, row in df.head(3).iterrows():
                    title = str(row.get("新闻标题", ""))
                    if title:
                        ak_lines.append(f"- {title}")
                if ak_lines:
                    news_context += "\n### 实时财经新闻\n" + "\n".join(ak_lines)
        except ImportError:
            pass
    except Exception:
        pass

    user_content = (
        f"## 基金持仓信息\n"
        f"- 基金: {fund_name}({fund_code})\n"
        f"- 账户: {holding.get('account','花无缺')}\n"
        f"- 持有份额: {holding.get('shares',0):.4f}\n"
        f"- 成本净值: {holding.get('cost_price',0):.4f}\n"
        f"- 当前净值: {holding.get('current_price',0):.4f}\n"
        f"- 市值: {holding.get('current_value',0):.2f}\n"
        f"- 盈亏: {holding.get('profit_loss',0):.2f} ({holding.get('profit_rate',0)*100:.1f}%)\n"
        f"- 持有时间: 自首次交易起\n"
        f"\n## 交易记录\n" + ("\n".join(tx_lines) if tx_lines else "无交易记录") +
        f"\n\n## 估值数据\n{valuation_section}"
        f"\n\n{fundamentals_section}"
        f"\n\n## 组合角色上下文\n{portfolio_context}"
        f"\n\n## MCP 诊断\n{_get_fund_mcp_diagnosis(fund_code)}"
        f"\n\n{news_context}"
    )

    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_deep_dive",
            model=MODEL,
            messages=[
                {"role": "system", "content": agent["system_prompt"]},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"深度分析失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    record_id = create_portfolio_analysis_record(
        analysis_type="deep_dive",
        summary=f"深度分析 · {fund_name}",
        input_data=json.dumps({"holding_id": holding_id, "fund_code": fund_code}, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=4,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


@app.post("/api/portfolio/analysis/trade-review")
async def trade_review_api(req: TradeReviewRequest):
    """模式 3：交易复盘 — 分析交易行为模式和操作质量。"""
    txs = list_transactions(limit=500)
    if not txs:
        raise HTTPException(400, "暂无交易记录")

    agent = get_analysis_agent(5)
    if not agent:
        raise HTTPException(404, "交易复盘分析师未配置")

    # 过滤日期范围
    if req.start_date:
        txs = [t for t in txs if t.get("transaction_date", "") >= req.start_date]
    if req.end_date:
        txs = [t for t in txs if t.get("transaction_date", "") <= req.end_date]
    if not txs:
        raise HTTPException(400, "所选日期范围内无交易记录")

    # 交易记录 + 标签
    tx_lines = []
    for t in sorted(txs, key=lambda x: x.get("transaction_date", "")):
        tags = get_transaction_tags(t["id"])
        tag_str = f" [{','.join(tags)}]" if tags else ""
        tx_lines.append(
            f"- {t['transaction_date']} {t.get('transaction_time','')} "
            f"{'买入' if t['transaction_type']=='buy' else '卖出'}"
            f"{tag_str}: "
            f"{t.get('fund_name','')}({t.get('fund_code','')}), "
            f"金额 {t.get('amount',0):.2f}, 价格 {t.get('price',0):.4f}, "
            f"状态 {t.get('status','confirmed')}"
        )

    # 汇总统计
    buy_count = len([t for t in txs if t["transaction_type"] == "buy"])
    sell_count = len([t for t in txs if t["transaction_type"] == "sell"])
    buy_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "buy")
    sell_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "sell")

    # 估值数据
    valuation_context = _get_valuation_context()

    user_content = (
        f"## 操作总览\n- 买入 {buy_count} 笔, 共 {buy_total:.2f} 元\n"
        f"- 卖出 {sell_count} 笔, 共 {sell_total:.2f} 元\n"
        f"- 净投入: {buy_total - sell_total:.2f} 元\n"
        f"\n## 交易明细\n" + "\n".join(tx_lines) +
        f"\n\n{valuation_context}"
    )

    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_trade_review",
            model=MODEL,
            messages=[
                {"role": "system", "content": agent["system_prompt"]},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"交易复盘失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    record_id = create_portfolio_analysis_record(
        analysis_type="trade_review",
        summary=f"交易复盘 · {buy_count}买{sell_count}卖",
        input_data=json.dumps({"start_date": req.start_date, "end_date": req.end_date, "tx_count": len(txs)},
                              ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=5,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


@app.post("/api/portfolio/analysis/what-if")
async def what_if_analysis_api(req: WhatIfRequest):
    """模式 4：情景推演 — 模拟不同市场情景下的组合变化。"""
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

    agent = get_analysis_agent(6)
    if not agent:
        raise HTTPException(404, "情景推演分析师未配置")

    # 持仓数据
    total_value = sum(h.get('current_value', 0) or 0 for h in holdings)
    holdings_lines = []
    for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
        pct = (h.get('current_value', 0) or 0) / total_value * 100 if total_value else 0
        holdings_lines.append(
            f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
            f"市值 {h.get('current_value',0):.2f}, 占比 {pct:.1f}%, "
            f"成本 {h.get('total_cost',0):.2f}"
        )

    # 估值数据
    valuation_context = _get_valuation_context()

    scenario_desc = {
        "market_drop": f"市场整体下跌 {req.parameter or 10}%",
        "repair_to_median": "估值修复到历史中位数",
        "repair_to_opportunity": "估值修复到机会值（20%分位）",
    }.get(req.scenario, req.scenario)

    user_content = (
        f"## 用户选择的情景\n{scenario_desc}"
        f"{'(跌幅: ' + str(req.parameter) + '%)' if req.scenario == 'market_drop' and req.parameter else ''}"
        f"\n\n## 当前持仓\n" + "\n".join(holdings_lines) +
        f"\n总市值: {total_value:.2f}\n"
        f"\n{valuation_context}"
    )

    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_whatif",
            model=MODEL,
            messages=[
                {"role": "system", "content": agent["system_prompt"]},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"情景推演失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    record_id = create_portfolio_analysis_record(
        analysis_type="what_if",
        summary=f"情景推演 · {scenario_desc}",
        input_data=json.dumps({"scenario": req.scenario, "parameter": req.parameter}, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=6,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


@app.get("/api/portfolio/analysis/panorama/records")
async def list_panorama_records_api(limit: int = 10):
    """列出全景诊断历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="panorama", limit=limit)
    return {"records": records}


@app.get("/api/portfolio/analysis/deep-dive/records")
async def list_deep_dive_records_api(limit: int = 10):
    """列出深度分析历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="deep_dive", limit=limit)
    return {"records": records}


@app.get("/api/portfolio/analysis/trade-review/records")
async def list_trade_review_records_api(limit: int = 10):
    """列出交易复盘历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="trade_review", limit=limit)
    return {"records": records}


@app.get("/api/portfolio/analysis/what-if/records")
async def list_whatif_records_api(limit: int = 10):
    """列出情景推演历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="what_if", limit=limit)
    return {"records": records}


# ── 风险预警 API ──────────────────────────────────────────


@app.get("/api/portfolio/alerts")
async def list_alerts_api(unread_only: bool = False, limit: int = 50):
    """获取预警列表。"""
    return {"alerts": list_alerts(limit=limit, unread_only=unread_only)}


@app.get("/api/portfolio/alerts/unread-count")
async def unread_alert_count_api():
    """获取未读预警数量。"""
    return {"count": get_unread_alert_count()}


@app.put("/api/portfolio/alerts/{alert_id}/read")
async def mark_alert_read_api(alert_id: int):
    """标记预警为已读。"""
    if not mark_alert_read(alert_id):
        raise HTTPException(404, "预警不存在")
    return {"ok": True}


@app.delete("/api/portfolio/alerts/{alert_id}")
async def delete_alert_api(alert_id: int):
    """删除预警。"""
    if not delete_alert(alert_id):
        raise HTTPException(404, "预警不存在")
    return {"ok": True}


@app.post("/api/portfolio/alerts/generate")
async def generate_alert_api(req: CreateAlertRequest):
    """AI 主动生成预警。"""
    alert_id = create_alert(
        alert_type=req.alert_type,
        title=req.title,
        content=req.content,
        severity=req.severity,
        related_fund_code=req.related_fund_code,
        related_fund_name=req.related_fund_name,
        source=req.source or "system",
    )
    return {"ok": True, "alert_id": alert_id}


# ── 交易标签 API ──────────────────────────────────────────


@app.post("/api/portfolio/transactions/{tx_id}/tags")
async def add_transaction_tag_api(tx_id: int, req: TagRequest):
    """给交易记录添加标签。"""
    tag_id = add_transaction_tag(tx_id, req.tag)
    return {"ok": True, "tag_id": tag_id}


@app.delete("/api/portfolio/transactions/{tx_id}/tags/{tag}")
async def remove_transaction_tag_api(tx_id: int, tag: str):
    """移除交易记录的标签。"""
    if not remove_transaction_tag(tx_id, tag):
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


@app.get("/api/portfolio/transactions/{tx_id}/tags")
async def get_transaction_tags_api(tx_id: int):
    """获取交易记录的所有标签。"""
    return {"tags": get_transaction_tags(tx_id)}

@app.get("/api/portfolio/{holding_id}")
async def get_holding_api(holding_id: int):
    """获取单个持仓详情。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    return holding


@app.put("/api/portfolio/{holding_id}")
async def update_holding_api(holding_id: int, req: UpdateHoldingRequest):
    """更新持仓。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if fields:
        update_holding(holding_id, **fields)
    return {"ok": True}


@app.delete("/api/portfolio/{holding_id}")
async def delete_holding_api(holding_id: int):
    """删除持仓。"""
    if not delete_holding(holding_id):
        raise HTTPException(404, "持仓不存在")
    return {"ok": True}


@app.get("/api/portfolio/{holding_id}/transactions")
async def list_transactions_api(holding_id: int, limit: int = 100):
    """获取持仓的交易记录。"""
    return {"transactions": list_transactions(holding_id=holding_id, limit=limit)}


@app.post("/api/portfolio/transactions")
async def create_transaction_api(req: CreateTransactionRequest):
    """新增交易记录。"""
    # 自动计算 T+1 确认日
    expected_confirm = None
    if req.status == "pending" and req.transaction_date:
        try:
            from datetime import datetime as dt
            d = dt.strptime(req.transaction_date, "%Y-%m-%d").date()
            expected_confirm = str(expected_confirm_date(d, req.transaction_time))
        except (ValueError, TypeError):
            pass

    tx_id = create_transaction(
        fund_code=req.fund_code, transaction_type=req.transaction_type,
        amount=req.amount, transaction_date=req.transaction_date,
        shares=req.shares, price=req.price,
        holding_id=req.holding_id, notes=req.notes,
        status=req.status, submitted_shares=req.submitted_shares,
        submitted_amount=req.submitted_amount,
        transaction_time=req.transaction_time,
        expected_confirm_date=expected_confirm,
    )
    return {"ok": True, "transaction_id": tx_id, "expected_confirm_date": expected_confirm}


@app.post("/api/portfolio/transactions/{tx_id}/confirm")
async def confirm_transaction_api(tx_id: int, req: ConfirmTransactionRequest):
    """确认交易：填入 T+1 实际净值，计算实际份额/金额。"""
    ok = confirm_transaction(tx_id, req.confirmed_price,
                             confirmed_shares=req.confirmed_shares,
                             confirmed_amount=req.confirmed_amount)
    if not ok:
        raise HTTPException(404, "交易记录不存在")
    return {"ok": True}


@app.post("/api/portfolio/transactions/{tx_id}/settle")
async def settle_transaction_api(tx_id: int):
    """标记卖出交易已到账。"""
    ok = settle_transaction(tx_id)
    if not ok:
        raise HTTPException(400, "只能标记已确认的卖出交易为已到账")
    return {"ok": True}


@app.delete("/api/portfolio/transactions/{tx_id}")
async def delete_transaction_api(tx_id: int):
    """撤销 pending 状态的交易记录。"""
    ok = delete_transaction(tx_id)
    if not ok:
        raise HTTPException(400, "只能撤销待确认（pending）状态的交易")
    return {"ok": True}


@app.post("/api/portfolio/refresh")
async def refresh_all_prices_api():
    """批量刷新所有持仓的最新净值。"""
    results = refresh_all_fund_prices()
    return {"ok": True, "results": results, "total": len(results)}


@app.post("/api/portfolio/{holding_id}/refresh")
async def refresh_single_price_api(holding_id: int):
    """刷新单个持仓的最新净值。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    nav_data = refresh_holding_price(holding_id)
    if not nav_data:
        raise HTTPException(502, "净值获取失败，请稍后重试")
    return {"ok": True, "fund_code": holding["fund_code"], "nav": nav_data}


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


# ── Dashboard 每日投资决策看板 ────────────────────────────


def _assess_valuation(percentile: float) -> dict:
    """根据百分位给出估值评估。"""
    if percentile <= 10:
        return {"label": "极度低估", "level": "extreme"}
    elif percentile <= 25:
        return {"label": "低估", "level": "undervalued"}
    elif percentile <= 40:
        return {"label": "偏低", "level": "slightly_low"}
    elif percentile <= 60:
        return {"label": "合理", "level": "fair"}
    elif percentile <= 80:
        return {"label": "偏高", "level": "slightly_high"}
    elif percentile <= 90:
        return {"label": "高估", "level": "overvalued"}
    else:
        return {"label": "极度高估", "level": "extreme_high"}


def _get_cash_advice(temperature, balance: float) -> dict:
    """根据债市温度给出零钱配置建议。temperature 为 None 时给出保守建议。"""
    if not balance or balance <= 0:
        return {"summary": "暂无可用零钱", "allocation": []}

    if temperature is None:
        return {
            "summary": "债市数据暂缺，建议暂时放在货币基金中",
            "allocation": [
                {"name": "货币基金", "ratio": 100, "desc": "流动性好，风险低"},
            ],
        }
    elif temperature <= 20:
        return {
            "summary": f"债市温度 {temperature}°，处于历史低位。债券收益率高，是配置中长期债券基金的好时机",
            "allocation": [
                {"name": "中长期债券基金", "ratio": 60, "desc": "收益率高位锁定收益"},
                {"name": "短债基金", "ratio": 25, "desc": "兼顾收益与流动性"},
                {"name": "货币基金", "ratio": 15, "desc": "日常备用"},
            ],
        }
    elif temperature <= 35:
        return {
            "summary": f"债市温度 {temperature}°，仍处于偏低区域，适合增加债券配置",
            "allocation": [
                {"name": "中长期债券基金", "ratio": 40, "desc": "获取较高收益"},
                {"name": "短债基金", "ratio": 40, "desc": "灵活调整"},
                {"name": "货币基金", "ratio": 20, "desc": "日常备用"},
            ],
        }
    elif temperature <= 50:
        return {
            "summary": f"债市温度 {temperature}°，处于适中区域，建议短债为主均衡配置",
            "allocation": [
                {"name": "短债基金", "ratio": 50, "desc": "收益率尚可，风险可控"},
                {"name": "中长期债券基金", "ratio": 25, "desc": "少量参与"},
                {"name": "货币基金", "ratio": 25, "desc": "保留流动性"},
            ],
        }
    elif temperature <= 70:
        return {
            "summary": f"债市温度 {temperature}°，偏高区域，债券价格已在高位，注意利率风险",
            "allocation": [
                {"name": "货币基金", "ratio": 50, "desc": "规避回调风险"},
                {"name": "短债基金", "ratio": 50, "desc": "短久期低波动"},
            ],
        }
    else:
        return {
            "summary": f"债市温度 {temperature}°，高温预警！债券价格处于历史高位，建议减配债券等待回调",
            "allocation": [
                {"name": "货币基金", "ratio": 70, "desc": "等待债市回调"},
                {"name": "短债基金", "ratio": 30, "desc": "极小仓位保持参与"},
            ],
        }


@app.get("/api/dashboard")
async def get_dashboard():
    """每日投资决策看板 — 聚合四块核心数据。每个模块独立容错。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    # ── Section 1: 低估指数 ──
    undervalued = []
    try:
        indexes = list_valuation_indexes()
        # 按 index_code 去重，保留百分位最低的指标
        best_per_code = {}
        for idx in indexes:
            code = idx["index_code"]
            p = idx.get("percentile")
            if p is None:
                continue
            if code not in best_per_code or p < best_per_code[code]["percentile"]:
                assess = _assess_valuation(p)
                best_per_code[code] = {
                    "index_code": code,
                    "index_name": idx.get("index_name", ""),
                    "metric_type": idx.get("metric_type", ""),
                    "current_value": idx.get("current_value"),
                    "percentile": p,
                    "latest_date": idx.get("latest_date", ""),
                    "assessment": assess["label"],
                    "assessment_level": assess["level"],
                }
        # 过滤：百分位 <= 30% 且数据新鲜（30天内）
        from datetime import datetime, timedelta
        freshness_cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        undervalued = [
            v for v in best_per_code.values()
            if v["percentile"] <= 30 and v.get("latest_date", "") >= freshness_cutoff
        ]
        undervalued.sort(key=lambda x: x["percentile"])
    except Exception as e:
        logging.warning(f"Dashboard 低估指数获取失败: {e}")

    # ── Section 2: 持仓健康度 ──
    portfolio_health = None
    try:
        holdings = list_holdings()
        active = [h for h in holdings if (h.get("shares") or 0) > 0]
        if active:
            summary = get_portfolio_summary()
            divers = get_portfolio_diversification()
            total_val = summary.get("total_value", 0) or 0
            sorted_h = sorted(active, key=lambda h: (h.get("current_value", 0) or 0), reverse=True)
            top3_pct = round(
                sum(h.get("current_value", 0) or 0 for h in sorted_h[:3]) / total_val * 100, 1
            ) if total_val > 0 else 0

            # 集中度评估
            if top3_pct > 60:
                conc_level, conc_assess = "high", "前3持仓占比 %.1f%%，集中度很高，建议分散" % top3_pct
            elif top3_pct > 40:
                conc_level, conc_assess = "moderate", "前3持仓占比 %.1f%%，集中度偏高，可适当调整" % top3_pct
            else:
                conc_level, conc_assess = "low", "前3持仓占比 %.1f%%，分散度良好" % top3_pct

            portfolio_health = {
                "holding_count": summary.get("holding_count", 0),
                "total_value": round(total_val, 2),
                "total_profit": round(summary.get("total_profit", 0), 2),
                "profit_rate": summary.get("profit_rate", 0),
                "max_holding_pct": divers.get("max_holding_pct", 0),
                "top3_concentration": top3_pct,
                "type_distribution": divers.get("type_distribution", {}),
                "concentration_level": conc_level,
                "concentration_assessment": conc_assess,
            }
    except Exception as e:
        logging.warning(f"Dashboard 持仓数据获取失败: {e}")

    # ── Section 3: 零钱 + 债券 ──
    cash_balance = 0
    try:
        cash_balance = get_cash_balance().get("balance", 0)
    except Exception:
        pass

    bond_info = None
    try:
        raw_bond = _fetch_bond_data()
        if raw_bond and len(raw_bond) > 1:
            last = raw_bond[-1]
            # 计算趋势：找 7天前、30天前、90天前的数据点
            ref_dates = {}
            last_date_str = last.get("date", "")
            for d in raw_bond:
                ref_dates[d["date"]] = {"temp": d.get("degree"), "yield": d.get("yield")}

            def _lookup_bond(days_ago):
                """找距离指定天数最近的交易日数据。"""
                from datetime import datetime, timedelta
                target = datetime.strptime(last_date_str, "%Y-%m-%d") - timedelta(days=days_ago)
                for i in range(7):
                    look = target.strftime("%Y-%m-%d")
                    if look in ref_dates:
                        return ref_dates[look]
                    target -= timedelta(days=1)
                return None

            ref_7d = _lookup_bond(7)
            ref_30d = _lookup_bond(30)

            bond_info = {
                "temperature": last.get("degree"),
                "yield_val": float(last["yield"]) if last.get("yield") else None,
                "date": last.get("date", ""),
                "trend": {
                    "week_ago_temp": ref_7d["temp"] if ref_7d else None,
                    "week_ago_yield": float(ref_7d["yield"]) if ref_7d and ref_7d.get("yield") else None,
                    "month_ago_temp": ref_30d["temp"] if ref_30d else None,
                    "month_ago_yield": float(ref_30d["yield"]) if ref_30d and ref_30d.get("yield") else None,
                },
            }
    except Exception as e:
        logging.warning(f"Dashboard 债市数据获取失败: {e}")

    cash_advice = _get_cash_advice(
        bond_info["temperature"] if bond_info else None, cash_balance
    )

    # ── 数据新鲜度 ──
    freshness_info = {"stale_count": 0, "stale_indexes": []}
    try:
        all_freshness = list_index_freshness()
        stale = [f for f in all_freshness if f.get("stale_days", 0) >= 10]
        freshness_info = {
            "stale_count": len(stale),
            "stale_indexes": [
                {"name": f["index_name"], "code": f["index_code"],
                 "latest_date": f["latest_date"], "stale_days": int(f["stale_days"])}
                for f in stale[:8]
            ],
        }
    except Exception as e:
        logging.warning(f"Dashboard 新鲜度获取失败: {e}")

    return {
        "date": today,
        "undervalued_indexes": undervalued,
        "portfolio_health": portfolio_health,
        "cash_management": {
            "balance": cash_balance,
            "bond_market": bond_info,
            "suggestion": cash_advice,
        },
        "data_freshness": freshness_info,
    }


# ── 市场热点 API（带缓存，解析JSON结构化输出）────────────

_hot_topics_cache = {"data": None, "ts": 0}


@app.get("/api/dashboard/daily-report")
async def get_daily_report():
    """获取今日自动生成的日报。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    from db import _get_conn
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM analysis_history WHERE agent_id = 1 AND date(created_at) = ? ORDER BY created_at DESC LIMIT 1",
        (today,)
    ).fetchone()
    conn.close()
    if row:
        r = dict(row)
        return {"has_report": True, "report": r}
    return {"has_report": False, "report": None}


@app.get("/api/dashboard/hot-topics")
async def get_hot_topics():
    """获取今日市场热点（YingMi MCP SearchFinancialNews，120秒缓存）。"""
    import time
    now = time.time()
    if _hot_topics_cache["data"] and now - _hot_topics_cache["ts"] < 120:
        return _hot_topics_cache["data"]

    news_items = []
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        raw = mcp.call_tool("SearchFinancialNews", {"keyword": "A股", "pageSize": 6})
        if isinstance(raw, dict):
            for c in raw.get("content", []):
                if c.get("type") == "text":
                    parsed = json.loads(c["text"])
                    if parsed.get("success") and parsed.get("data", {}).get("items"):
                        for item in parsed["data"]["items"]:
                            news_items.append({
                                "title": item.get("title", ""),
                                "summary": item.get("summary", ""),
                                "source": item.get("sources", ""),
                                "date": item.get("publishDate", ""),
                                "url": item.get("url", ""),
                            })
    except Exception as e:
        logging.warning(f"热点新闻获取失败: {e}")

    # fallback
    if not news_items:
        try:
            from tools import execute_tool
            web_raw = execute_tool("web_search", {"query": "A股 今日热点 板块 基金", "max_results": 5})
            if web_raw:
                news_items.append({"title": "网络资讯", "summary": web_raw[:500], "source": "web_search", "date": "", "url": ""})
        except Exception as e:
            logging.warning(f"热点 web_search 失败: {e}")

    result = {"news": news_items, "source": "yingmi" if news_items else "none"}
    _hot_topics_cache["data"] = result
    _hot_topics_cache["ts"] = now
    return result


# ── 热点 AI 分析（结构化推荐） ────────────────────────────────
# prompt 通过 analysis_agents 配置管理，见 db.py 中"热点分析专家"系统提示词


@app.get("/api/dashboard/hotspots-analysis")
async def get_hotspots_analysis():
    """结构化热点分析 — LLM 输出 JSON 推荐。"""
    # 1. 收集今日数据
    news_data = await get_hot_topics()
    news_list = news_data.get("news", [])[:5]
    news_text = "\n".join(
        f"- {n.get('title','')}（{n.get('source','')}）"
        for n in news_list if n.get('title')
    ) if news_list else "暂无新闻"

    # 估值数据 + 可参考指数代码
    try:
        from db import list_valuation_indexes
        indexes = list_valuation_indexes()
        # 去重，按 index_code 分组，优先展示最新数据
        seen = {}
        for i in indexes:
            code = i.get("index_code", "")
            if code and code not in seen:
                seen[code] = i
        all_indexes = list(seen.values())
        # 可参考指数代码表
        code_ref_text = "\n".join(
            f"- {i['index_name']}（{i['index_code']}）: {i.get('metric_type','PE')}={i.get('current_value','?')}, "
            f"百分位={i.get('percentile',100):.0f}%"
            for i in all_indexes
        ) if all_indexes else "暂无指数数据"

        # 低估指数（百分位<30）
        low_val = [i for i in all_indexes if i.get("percentile", 100) < 30]
        val_text = "\n".join(
            f"- {i['index_name']}（{i['index_code']}）: 百分位={i.get('percentile',100):.0f}%"
            for i in low_val[:10]
        ) if low_val else "暂无低估指数"
    except Exception as e:
        code_ref_text = "暂无"
        val_text = "暂无"

    # 持仓明细 + 概况
    try:
        from db import list_holdings, get_portfolio_diversification, get_cash_balance
        holdings = list_holdings()
        div = get_portfolio_diversification()
        cash = get_cash_balance()

        # 持仓明细文本
        if holdings:
            holding_lines = []
            for h in holdings[:15]:
                pct = h.get("profit_rate")
                pct_str = f"{pct:+.1f}%" if pct is not None else "N/A"
                val = h.get("current_value", 0) or 0
                holding_lines.append(
                    f"- {h['fund_name']}（{h.get('fund_code','')}）: "
                    f"市值{val:.0f}元, 收益率{pct_str}"
                )
            holding_text = "\n".join(holding_lines)
        else:
            holding_text = "暂无持仓"

        portfolio_text = (
            f"持仓{div.get('holding_count',0)}只基金，"
            f"总市值{div.get('total_value',0):.0f}元，"
            f"盈亏{div.get('total_profit',0):.0f}元，"
            f"可用零钱{cash:.0f}元"
        )
    except Exception:
        holding_text = "暂无"
        portfolio_text = "暂无"

    # 债券
    try:
        from tools import _get_bond_temperature
        bond_raw = json.loads(_get_bond_temperature())
        bond_text = f"债券温度{bond_raw.get('temperature','?')}°，收益率{bond_raw.get('rate','?')}%"
    except Exception:
        bond_text = "暂无"

    # 从 analysis_agents 加载热点分析 prompt，支持通过管理页面动态修改
    try:
        from db import get_analysis_agent
        agent = get_analysis_agent(7)
        base_prompt = agent["system_prompt"] if agent else ""
    except Exception:
        base_prompt = ""
    if not base_prompt:
        base_prompt = "你是一位专业的A股市场分析专家。请基于以下市场数据分析今日投资机会，输出结构化JSON。\n\n## 输出格式\n返回严格JSON：{\"summary\":\"...\", \"recommendations\":[{\"direction\":\"up|down|watch\",\"index_name\":\"...\",\"index_code\":\"...\",\"reason\":\"...\",\"confidence\":\"high|medium|low\"}]}\n\n## 今日数据："

    prompt = base_prompt + f"""
【今日新闻】
{news_text}

【可参考指数代码】
{code_ref_text}

【低估指数】
{val_text}

【持仓明细】
{holding_text}

【持仓概况】
{portfolio_text}

【债券市场】
{bond_text}

请严格按照JSON格式输出分析结果。"""

    try:
        response = await asyncio.wait_for(asyncio.to_thread(lambda: _call_llm(
            caller="hotspots_analysis",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4096,
        )), timeout=60)
        content = response.choices[0].message.content or "{}"
        # 尝试提取 JSON
        import re as _re
        json_match = _re.search(r'\{.*\}', content, _re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(content)
        # 确保字段完整
        recs = parsed.get("recommendations", [])
        result = {
            "summary": parsed.get("summary", ""),
            "recommendations": recs,
            "analysis_text": content,
        }
        # 保存到推荐验证库 + 缓存 + 分析历史
        if recs:
            try:
                from datetime import datetime
                from db import save_recommendations, save_analysis_cache
                from db import _get_conn as _get_db_conn
                analysis_id = datetime.now().strftime("hotspots_%Y%m%d_%H%M%S")
                save_recommendations(recs, analysis_id)
                save_analysis_cache("hotspots_latest", result)
                # 记录分析历史（含使用的 prompt 版本）
                _conn = _get_db_conn()
                _conn.execute(
                    "INSERT INTO analysis_history (agent_id, agent_name, prompt_used, news_context, result, token_usage) VALUES (?, ?, ?, ?, ?, ?)",
                    (7, "热点分析专家", base_prompt[:500] if base_prompt else "", news_text[:500], content, 0)
                )
                _conn.commit()
                _conn.close()
            except Exception as e:
                logging.warning(f"保存推荐记录失败: {e}")
        return result
    except asyncio.TimeoutError:
        return {"summary": "分析超时，请重试", "recommendations": [], "analysis_text": ""}
    except Exception as e:
        logging.warning(f"热点结构化分析失败: {e}")
        return {"summary": f"分析失败: {str(e)}", "recommendations": [], "analysis_text": ""}


@app.get("/api/dashboard/hotspots-analysis/latest")
async def get_latest_hotspots_analysis():
    """返回最近一次缓存的热点分析结果（刷新页面后还原用）。"""
    from db import get_analysis_cache
    cached = get_analysis_cache("hotspots_latest")
    if cached:
        # 补充 recommendations 中的 id 字段，供反馈使用
        try:
            from db import _get_conn
            conn = _get_conn()
            rows = conn.execute(
                "SELECT id, index_name FROM recommendations WHERE analysis_id LIKE 'hotspots_%' ORDER BY id DESC LIMIT 10"
            ).fetchall()
            conn.close()
            id_map = {r["index_name"]: r["id"] for r in rows}
            for rec in cached.get("recommendations", []):
                if rec.get("index_name") in id_map:
                    rec["id"] = id_map[rec["index_name"]]
        except Exception:
            pass
        return cached
    # 没有缓存，尝试从历史推荐记录重建
    try:
        from db import list_recommendations
        recs = list_recommendations(limit=10)
        if recs:
            return {
                "summary": f"上次分析结果（共{len(recs)}条推荐）",
                "recommendations": recs,
                "analysis_text": "",
            }
    except Exception:
        pass
    return {"summary": "", "recommendations": [], "analysis_text": ""}


@app.get("/api/dashboard/recommendations")
async def list_recommendations_api(limit: int = 50, status: str = ""):
    """列出历史推荐记录。"""
    from db import list_recommendations
    recs = list_recommendations(limit, status or None)
    return {"recommendations": recs}


@app.get("/api/dashboard/recommendations/stats")
async def recommendations_stats_api():
    """推荐验证统计。"""
    from db import _get_conn
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    correct = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'correct'").fetchone()[0]
    wrong = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'wrong'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'pending'").fetchone()[0]
    conn.close()
    total_verified = correct + wrong
    accuracy = round(correct / total_verified * 100, 1) if total_verified > 0 else None
    return {
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "pending": pending,
        "verified": total_verified,
        "accuracy": accuracy,
    }


# ── 推荐反馈 / 进化系统 API ──────────────────────────────


@app.post("/api/dashboard/recommendations/{rec_id}/feedback")
async def create_recommendation_feedback(rec_id: int, body: dict):
    """提交推荐反馈（点赞/点踩/评论）。"""
    from db import save_recommendation_feedback
    fid = save_recommendation_feedback(
        recommendation_id=rec_id,
        rating=body.get("rating", "neutral"),
        tags=body.get("tags", ""),
        comment=body.get("comment", ""),
    )
    return {"ok": True, "id": fid}


@app.get("/api/dashboard/recommendations/feedback")
async def list_feedback_api():
    """列出所有推荐反馈。"""
    from db import list_recommendation_feedback
    return {"feedback": list_recommendation_feedback()}


@app.get("/api/dashboard/recommendations/feedback-stats")
async def feedback_stats_api():
    """推荐反馈统计（点赞率等）。"""
    from db import get_recommendation_feedback_stats
    return get_recommendation_feedback_stats()


@app.post("/api/llm-feedback")
async def create_llm_feedback(body: dict):
    """提交 LLM 输出反馈（进化系统）。"""
    from db import save_llm_feedback
    fid = save_llm_feedback(
        caller=body.get("caller", ""),
        input_summary=body.get("input_summary", ""),
        output_summary=body.get("output_summary", ""),
        rating=body.get("rating", "neutral"),
        tags=body.get("tags", ""),
        comment=body.get("comment", ""),
    )
    return {"ok": True, "id": fid}


@app.get("/api/llm-feedback")
async def list_llm_feedback_api(caller: str = "", rating: str = ""):
    """列出 LLM 反馈。"""
    from db import list_llm_feedback
    return {"feedback": list_llm_feedback(
        caller=caller or None,
        rating=rating or None,
    )}


# ── 债市数据 API ──────────────────────────────────────────


def _fetch_bond_data():
    """抓取有知有行债市温度数据，返回原始数据列表。"""
    import re
    import html as html_mod
    import requests as req

    resp = req.get(
        "https://youzhiyouxing.cn/data/macro",
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        timeout=15,
    )
    resp.raise_for_status()

    match = re.search(r'data-cbond-history="([^"]+)"', resp.text)
    if not match:
        return []

    raw = html_mod.unescape(match.group(1))
    bracket_count = 0
    end_idx = 0
    for i, c in enumerate(raw):
        if c == "[":
            bracket_count += 1
        elif c == "]":
            bracket_count -= 1
            if bracket_count == 0:
                end_idx = i + 1
                break

    return json.loads(raw[:end_idx])


@app.get("/api/bond/market-temperature")
async def get_bond_market_temperature():
    """抓取有知有行债市温度数据。"""
    try:
        data = _fetch_bond_data()
        last = data[-1] if data else {}
        return {
            "history": data,
            "current": {
                "date": last.get("date"),
                "temperature": last.get("degree"),
                "rate": float(last["yield"]) if last.get("yield") else None,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"数据源请求失败: {e}")


@app.get("/api/bond/yield-curve")
async def bond_yield_curve_api(country: str = "china"):
    """获取国债收益率曲线数据。"""
    from tools import _get_bond_yield_curve
    result = json.loads(_get_bond_yield_curve({"country": country}))
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.get("/api/bond/market-overview")
async def bond_market_overview_api():
    """获取债市综合概况。"""
    from tools import _get_bond_market_overview
    result = json.loads(_get_bond_market_overview())
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.post("/api/bond/ai-recommend")
async def bond_ai_recommend():
    """AI 债券配置推荐：结合债市温度历史趋势、收益率曲线、持仓穿透、基金排行榜给出具体购买建议。"""
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    import akshare as ak
    import json as json_mod
    from tools import _get_bond_yield_curve

    # 1. 债市温度（完整历史，用于趋势分析）
    bond_history = []
    try:
        raw = _fetch_bond_data()
        if raw:
            # 取最近90天的数据
            bond_history = raw[-90:]
    except Exception as e:
        pass

    # 2. 收益率曲线
    yield_curve = {}
    try:
        yc = json.loads(_get_bond_yield_curve({"country": "china"}))
        if "error" not in yc:
            yield_curve = yc
    except Exception:
        pass

    # 3. 现有持仓穿透分析（用 akshare 查每只债券基金的底层持仓）
    holdings_with_penetration = []
    total_bond_value = 0
    total_portfolio_value = 0
    try:
        all_h = list_holdings()
        for h in all_h:
            if (h.get("shares") or 0) > 0:
                v = h.get("current_value") or 0
                total_portfolio_value += v
                if h.get("fund_category") == "bond":
                    total_bond_value += v
                    code = h.get("fund_code", "")
                    # 查股票持仓（判断是否为二级债基）
                    has_stock = False
                    stock_ratio = 0
                    try:
                        stock_df = ak.fund_portfolio_hold_em(symbol=code, date="2025")
                        if stock_df is not None and not stock_df.empty:
                            has_stock = True
                            stock_ratio = float(stock_df["占净值比例"].sum())
                    except:
                        pass
                    # 查债券持仓
                    bond_types = []
                    try:
                        bond_df = ak.fund_portfolio_bond_hold_em(symbol=code, date="2025")
                        if bond_df is not None and not bond_df.empty:
                            for _, row in bond_df.iterrows():
                                bname = str(row.get("债券名称", ""))
                                if "国债" in bname or "国开" in bname or "政金" in bname or "农发" in bname or "进出" in bname:
                                    bond_types.append("利率债")
                                elif "可转债" in bname or "转债" in bname:
                                    bond_types.append("可转债")
                                else:
                                    bond_types.append("信用债")
                    except:
                        pass

                    holdings_with_penetration.append({
                        "code": code,
                        "name": h.get("fund_name", ""),
                        "value": v,
                        "pct_of_portfolio": round(v / total_portfolio_value * 100, 1) if total_portfolio_value > 0 else 0,
                        "profit": h.get("profit_loss", 0),
                        "has_stock": has_stock,
                        "stock_ratio_pct": round(stock_ratio, 2),
                        "bond_type_tags": list(set(bond_types)) if bond_types else ["待确认"],
                    })
    except Exception as e:
        pass

    # 4. 零钱余额
    cash_balance = 0
    try:
        cash_balance = get_cash_balance().get("balance", 0)
    except Exception:
        pass

    # 5. 全市场纯债基金排行榜
    all_bond_funds = []
    try:
        df = ak.fund_open_fund_rank_em(symbol="债券型")
        # 排除含"可转债"的基金
        pure_mask = ~df["基金简称"].str.contains("可转债", na=False)
        for _, row in df[pure_mask].head(30).iterrows():
            all_bond_funds.append({
                "code": row["基金代码"],
                "name": row["基金简称"],
                "year_return": row.get("近1年"),
                "fee": row.get("手续费"),
            })
    except Exception as e:
        pass

    # 6. 货币基金排行榜
    money_funds = []
    try:
        mf = ak.fund_money_rank_em()
        for _, row in mf.head(5).iterrows():
            money_funds.append({
                "code": row.get("基金代码", ""),
                "name": row.get("基金简称", ""),
                "year_return": row.get("近1年"),
            })
    except Exception as e:
        pass

    # 7. 构建 LLM 上下文
    agent = get_analysis_agent(8)
    system_prompt = agent["system_prompt"] if agent else DEFAULT_BOND_PROMPT

    context_lines = [
        f"## 债市温度历史（近90天）\n{json_mod.dumps(bond_history, ensure_ascii=False, indent=2)}",
        f"## 收益率曲线\n{json_mod.dumps(yield_curve, ensure_ascii=False, indent=2)}",
        f"## 现有债券持仓（含穿透数据）\n持有 {len(holdings_with_penetration)} 只，总值 {total_bond_value:.2f}，占总资产 {round(total_bond_value/total_portfolio_value*100,1) if total_portfolio_value > 0 else 0}%\n" + json_mod.dumps(holdings_with_penetration, ensure_ascii=False, indent=2),
        f"## 零钱余额\n{cash_balance} 元（占总资产 {round(cash_balance/total_portfolio_value*100,1) if total_portfolio_value > 0 else 0}%）",
        f"## 全市场纯债基金排行榜（Top 30）\n" + json_mod.dumps(all_bond_funds, ensure_ascii=False, indent=2),
        f"## 货币基金排行榜（备选）\n" + json_mod.dumps(money_funds, ensure_ascii=False, indent=2),
    ]

    combined_input = "请基于以下数据给出债券配置建议：\n\n" + "\n\n".join(context_lines)

    # 8. 调用 LLM
    from llm_service import chat_with_agent
    result = chat_with_agent(system_prompt, [{"role": "user", "content": combined_input}])

    # 尝试从结果中提取 JSON
    try:
        parsed = json_mod.loads(result)
        return {"ok": True, "result": parsed}
    except:
        import re as re_mod
        match = re_mod.search(r'```(?:json)?\s*(\{.*?\})\s*```', result, re_mod.DOTALL)
        if match:
            try:
                parsed = json_mod.loads(match.group(1))
                return {"ok": True, "result": parsed}
            except:
                pass
        return {"ok": True, "result": {"summary": "AI分析完成", "raw": result}}


# ── 前端页面 ──────────────────────────────────────────


@app.get("/app", response_class=HTMLResponse)
async def app_page():
    """Web 管理页面。"""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>前端文件未找到</h1><p>请创建 static/index.html</p>")


@app.get("/favicon.svg", include_in_schema=False)
async def _serve_favicon():
    return FileResponse(str(STATIC_DIR / "favicon.svg"))


@app.get("/icons.svg", include_in_schema=False)
async def _serve_icons():
    return FileResponse(str(STATIC_DIR / "icons.svg"))


@app.get("/api/finance/quote-bar")
async def finance_quote_bar():
    """每日理财箴言 + 市场热点。"""
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
    import random
    daily_quote = random.choice(quotes)

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
