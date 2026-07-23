"""市场情报 — 从 market_intelligence.py 提取所有路由"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from db import list_valuation_indexes, list_holdings, get_analysis_agent_by_name, get_config_float, get_config_int
from db import create_async_task, update_async_task, get_async_task
from db.portfolio import save_analysis_cache, get_analysis_cache
from db.agents import create_agent_run
from db._conn import _get_conn
from db.agent_analysis_log import create_analysis_log, complete_analysis_log


def _safe_percentile(v, default=None):
    """percentile 防御性 float 转换（兼容 "97.25%" 文本型历史数据）。

    Bug A 防御：即便 DB 迁移后仍有未覆盖路径，这里兜底。
    """
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace('%', '').strip())
    except (ValueError, TypeError):
        return default


from services.llm_service import _call_llm, MODEL
from services.rag import build_rag_context_with_details, log_rag_search  # 保留向后兼容
from services.market_data import get_market_overview
from services.unified_evidence import build_unified_evidence
from ._shared import inject_rag_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/market-intelligence", tags=["analysis-market-intel"])

_background_tasks = set()

# ── 新闻搜索关键词 ──────────────────────────────────────────

_BASE_NEWS_KEYWORDS = ["A股 市场", "基金 投资"]


async def _get_dynamic_keywords() -> list[str]:
    """从多个来源获取实时热点，动态生成搜索关键词。"""
    keywords = []
    try:
        import akshare as ak
        # 2026-07-13 性能优化：akshare 同步调用改用 to_thread，避免阻塞事件循环
        df = await asyncio.to_thread(ak.stock_hot_keyword_em)
        if df is not None and len(df) > 0:
            hot_kw = df["概念名称"].tolist()[:5]
            keywords.extend(hot_kw)
            logger.info(f"[market-intel] akshare 热点概念: {hot_kw}")
    except Exception:
        pass

    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        raw = await asyncio.to_thread(
            lambda: mcp.call_tool("SearchHotTopic", {"keyword": "A股 热点"})
        )
        if isinstance(raw, dict):
            for c in raw.get("content", []):
                if c.get("type") == "text":
                    parsed = json.loads(c["text"])
                    data = parsed.get("data", {}) if isinstance(parsed, dict) else {}
                    items = []
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get("items", [])
                    for item in items[:5]:
                        title = item.get("title", "") if isinstance(item, dict) else str(item)
                        if title:
                            clean = title.replace("！", "").replace("？", "").replace("…", "").replace("：", " ")
                            if len(clean) > 2:
                                keywords.append(clean[:15])
    except Exception:
        pass

    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    if unique:
        logger.info(f"[market-intel] 动态热点关键词: {unique[:6]}")
    return unique[:6]


async def _fetch_news_multi() -> list[dict]:
    """多关键词并行搜索财经新闻，去重后返回。

    2026-07-14 修复：传 startDate/endDate 限定最近 7 天，避免返回相关性排序的旧新闻。
    """
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()

        dynamic_kw = await _get_dynamic_keywords()
        all_keywords = dynamic_kw + _BASE_NEWS_KEYWORDS
        logger.info(f"[market-intel] 搜索关键词: {all_keywords}")

        # 限定最近 7 天，确保拿到的都是近期新闻
        from datetime import timedelta
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        async def _search(keyword: str) -> list[dict]:
            try:
                raw = await asyncio.to_thread(
                    lambda kw=keyword: mcp.call_tool("SearchFinancialNews", {
                        "keyword": kw,
                        "pageSize": 5,
                        "startDate": start_date,
                        "endDate": end_date,
                    })
                )
                items = []
                if isinstance(raw, dict):
                    for c in raw.get("content", []):
                        if c.get("type") == "text":
                            parsed = json.loads(c["text"])
                            if parsed.get("success") and parsed.get("data", {}).get("items"):
                                for item in parsed["data"]["items"]:
                                    items.append({
                                        "title": item.get("title", ""),
                                        "summary": item.get("summary", ""),
                                        "source": item.get("sources", ""),
                                        "date": item.get("publishDate", ""),
                                        "url": item.get("url", ""),
                                    })
                return items
            except Exception:
                return []

        results = await asyncio.gather(*[_search(kw) for kw in all_keywords])
        seen_titles = set()
        all_news = []
        for items in results:
            for n in items:
                title = n.get("title", "").strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    all_news.append(n)
        # 按 publishDate 倒序，最新的排前面
        all_news.sort(key=lambda x: x.get("date", ""), reverse=True)
        return all_news[:15]
    except Exception as e:
        logger.warning(f"市场情报新闻获取失败: {e}")
    return []


async def _fetch_web_news() -> list[dict]:
    """akshare 补充资讯。"""
    try:
        from tools import execute_tool
        raw = await asyncio.to_thread(
            lambda: execute_tool("web_search", {"query": "A股 今日热点板块 CPO 光模块 通信 半导体", "max_results": 5})
        )
        if not raw:
            return []
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("data", {}).get("items"):
                    items = []
                    for item in parsed["data"]["items"][:5]:
                        items.append({
                            "title": item.get("title", ""),
                            "summary": item.get("summary", ""),
                            "source": item.get("sources", "web_search"),
                            "date": item.get("publishDate", ""),
                            "url": item.get("url", ""),
                        })
                    return items
            except (json.JSONDecodeError, KeyError):
                pass
            if len(raw) > 20:
                return [{"title": "市场资讯", "summary": raw[:500], "source": "web_search", "date": "", "url": ""}]
        elif isinstance(raw, dict):
            if raw.get("data", {}).get("items"):
                items = []
                for item in raw["data"]["items"][:5]:
                    items.append({
                        "title": item.get("title", ""),
                        "summary": item.get("summary", ""),
                        "source": item.get("sources", "web_search"),
                        "date": item.get("publishDate", ""),
                        "url": item.get("url", ""),
                    })
                return items
    except Exception:
        pass
    return []


async def _fetch_cctv_news() -> list[dict]:
    """获取央视新闻。"""
    try:
        import akshare as ak
        today = datetime.now().strftime("%Y%m%d")
        df = await asyncio.to_thread(ak.news_cctv, date=today)

        if df is None or len(df) == 0:
            return []

        CCTV_CATEGORIES = {
            "经济": ["经济", "GDP", "增长", "发展", "改革", "开放", "市场", "企业", "产业", "消费", "投资"],
            "金融": ["金融", "银行", "证券", "保险", "基金", "股市", "债券", "利率", "汇率", "央行", "货币政策"],
            "政策": ["政策", "国务院", "发改委", "财政部", "证监会", "监管", "法规", "条例"],
            "农业": ["农业", "粮食", "农村", "乡村振兴", "农产品", "种业", "耕地", "水利"],
            "科技": ["科技", "创新", "芯片", "人工智能", "数字经济", "5G", "新能源", "半导体", "量子"],
            "贸易": ["贸易", "出口", "进口", "关税", "外贸", "一带一路", "自贸区", "RCEP"],
        }

        results = []
        for _, row in df.iterrows():
            title = str(row.get("title", ""))
            content = str(row.get("content", ""))[:500]

            category = "其他"
            for cat, keywords in CCTV_CATEGORIES.items():
                if any(kw in title or kw in content for kw in keywords):
                    category = cat
                    break

            if category == "其他":
                continue

            search_url = f"https://search.cctv.com/search.php?qtext={title}&type=web"

            results.append({
                "title": title,
                "summary": content[:200],
                "source": "央视新闻",
                "category": category,
                "date": str(row.get("date", "")),
                "url": search_url,
            })

        return results[:10]
    except Exception as e:
        logger.warning(f"央视新闻获取失败: {e}")
        return []


async def _fetch_hot_topics() -> list[dict]:
    """获取热门话题。"""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        raw = await asyncio.to_thread(
            lambda: mcp.call_tool("SearchHotTopic", {"keyword": "A股 热点"})
        )
        if isinstance(raw, dict):
            items = []
            for c in raw.get("content", []):
                if c.get("type") == "text":
                    parsed = json.loads(c["text"])
                    if isinstance(parsed, dict) and parsed.get("data", {}).get("items"):
                        for item in parsed["data"]["items"]:
                            items.append({
                                "title": item.get("title", ""),
                                "summary": item.get("summary", ""),
                                "heat": item.get("heat", ""),
                            })
                    elif isinstance(parsed, list):
                        for item in parsed:
                            items.append({
                                "title": item.get("title", ""),
                                "summary": item.get("summary", ""),
                                "heat": item.get("heat", ""),
                            })
            return items[:8]
    except Exception as e:
        logger.warning(f"热门话题获取失败: {e}")
    return []


async def _fetch_market_hotspots() -> list[dict]:
    """获取市场热点板块和题材。"""
    hotspots = []

    try:
        from mcp.eastmoney_client import get_eastmoney_client
        from config import EASTMONEY_API_KEY
        if EASTMONEY_API_KEY:
            client = get_eastmoney_client()
            hotspot_text = await asyncio.to_thread(
                client.stock_hotspot, "今日A股市场热点板块和题材概念"
            )
            if hotspot_text:
                hotspots.append({
                    "source": "东方财富妙想",
                    "name": "AI热点分析",
                    "content": hotspot_text[:1500],
                })
    except Exception as e:
        logger.warning(f"东方财富妙想热点获取失败: {e}")

    try:
        from mcp.eastmoney_client import get_eastmoney_client
        from config import EASTMONEY_API_KEY
        if EASTMONEY_API_KEY:
            client = get_eastmoney_client()
            search_text = await asyncio.to_thread(
                client.financial_search, "今日A股热门板块 热点题材 资金流向"
            )
            if search_text:
                hotspots.append({
                    "source": "东方财富资讯",
                    "name": "最新资讯",
                    "content": search_text[:1000],
                })
    except Exception as e:
        logger.warning(f"东方财富资讯搜索失败: {e}")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as http_client:
            resp = await http_client.post(
                "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                json={"appId": "appId01", "pageNo": 1, "pageSize": 10},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data = resp.json()
        for item in (data.get("data", []) or [])[:10]:
            name = item.get("name", "")
            code = item.get("sc", "")
            if name:
                hotspots.append({
                    "source": "东财人气",
                    "name": name,
                    "code": code,
                })
    except Exception as e:
        logger.warning(f"东财人气获取失败: {e}")

    return hotspots


def _fetch_macro_data() -> dict:
    """获取宏观数据快照。"""
    macro = {}
    try:
        from tools import _get_bond_temperature
        bond_raw = json.loads(_get_bond_temperature())
        macro["bond"] = {
            "temperature": bond_raw.get("temperature"),
            "rate": bond_raw.get("rate"),
        }
    except Exception:
        macro["bond"] = {}
    try:
        from tools import _get_macro_policy_data
        policy_raw = json.loads(_get_macro_policy_data())
        macro["policy"] = policy_raw
    except Exception:
        macro["policy"] = {}
    return macro


def _build_index_catalog() -> str:
    """构建指数估值目录文本。"""
    indexes = list_valuation_indexes()
    seen = {}
    for i in indexes:
        code = i.get("index_code", "")
        if code and code not in seen:
            seen[code] = i
    all_indexes = list(seen.values())
    if not all_indexes:
        return "暂无指数数据"
    lines = []
    for i in all_indexes:
        pct = _safe_percentile(i.get("percentile"))
        pct_str = f"{pct:.0f}%" if pct is not None else "N/A"
        lines.append(
            f"- {i['index_name']}（{i.get('index_code','')}）: "
            f"{i.get('metric_type','PE')}={i.get('current_value','?')}, 百分位={pct_str}"
        )
    return "\n".join(lines)


def _build_holdings_text() -> str:
    """构建持仓文本。"""
    holdings = list_holdings()
    if not holdings:
        return "暂无持仓"
    lines = []
    for h in holdings[:15]:
        pct = h.get("profit_rate")
        pct_str = f"{pct:+.1%}" if pct is not None else "N/A"
        lines.append(f"- {h['fund_name']}（{h.get('fund_code','')}）: 收益率{pct_str}")
    return "\n".join(lines)


def _build_event_radar_brief(limit: int = 5) -> dict:
    """构建事件雷达摘要，供市场情报分析复用。"""
    try:
        from db.market_events import list_active_events, list_verified_events
        from services.event_radar import get_sector_accuracy_stats

        accuracy_stats = get_sector_accuracy_stats()
        active_events = list_active_events()[:limit]
        verified_events = list_verified_events(limit=limit)
    except Exception as e:
        logger.debug(f"事件雷达摘要构建失败: {e}")
        return {
            "summary": "暂无事件雷达数据。",
            "accuracy": {"overall": {"total": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0}, "by_sector": {}},
            "active_events": [],
            "recent_verified": [],
        }

    overall = accuracy_stats.get("overall", {})
    by_sector = accuracy_stats.get("by_sector", {})
    active_items = []
    for ev in active_events:
        sectors = []
        try:
            sectors = json.loads(ev.get("affected_sectors") or "[]")
        except Exception:
            sectors = []
        active_items.append({
            "title": ev.get("title", ""),
            "status": ev.get("status", ""),
            "direction": ev.get("direction", ""),
            "confidence": float(ev.get("confidence") or 0),
            "relevance_to_user": ev.get("relevance_to_user", ""),
            "expected_date": ev.get("expected_date", ""),
            "sectors": sectors[:3],
        })

    recent_verified = []
    for ev in verified_events:
        try:
            result = json.loads(ev.get("verification_result") or "{}")
        except Exception:
            result = {}
        if not result:
            continue
        recent_verified.append({
            "title": ev.get("title", ""),
            "status": result.get("status", "flat"),
            "change_pct": result.get("change_pct"),
            "verified_date": result.get("verified_date", ""),
            "direction_predicted": result.get("direction_predicted", ""),
        })

    summary_parts = []
    if overall.get("total"):
        summary_parts.append(
            f"已验证 {overall.get('total', 0)} 个，正确 {overall.get('correct', 0)}，偏差 {overall.get('wrong', 0)}，准确率 {overall.get('accuracy', 0.0) * 100:.0f}%"
        )
    if active_items:
        top_titles = "、".join(item["title"][:18] for item in active_items[:3] if item.get("title"))
        if top_titles:
            summary_parts.append(f"当前前瞻事件：{top_titles}")
    if recent_verified:
        latest = recent_verified[0]
        summary_parts.append(
            f"最近验证：{latest.get('title', '')[:18]} -> {latest.get('status', 'flat')}"
        )

    return {
        "summary": "；".join(summary_parts) or "暂无事件雷达数据。",
        "accuracy": {
            "overall": overall,
            "by_sector": by_sector,
        },
        "active_events": active_items,
        "recent_verified": recent_verified,
    }


def _build_watchlist_signal_brief(user_id: str = "default", limit: int = 5) -> dict:
    """构建关注列表信号摘要，供市场情报分析复用。"""
    try:
        from db.watchlist import list_watchlist

        items = [
            item for item in list_watchlist(user_id=user_id, status="watching")
            if item.get("status") != "bought"
        ]
    except Exception as e:
        logger.debug(f"关注列表摘要构建失败: {e}")
        return {
            "summary": "暂无关注基金信号。",
            "counts": {"green": 0, "yellow": 0, "red": 0, "gray": 0, "total": 0},
            "top_items": [],
        }

    counts = {"green": 0, "yellow": 0, "red": 0, "gray": 0}
    priority_rank = {"green": 0, "yellow": 1, "red": 2, "gray": 3}
    for item in items:
        status = item.get("signal_status") or "gray"
        counts[status] = counts.get(status, 0) + 1

    sorted_items = sorted(
        items,
        key=lambda item: (
            priority_rank.get(item.get("signal_status") or "gray", 3),
            float(_safe_percentile(item.get("current_percentile"), default=999) or 999),
            -float(item.get("priority") or 0),
        ),
    )[:limit]

    top_items = []
    for item in sorted_items:
        top_items.append({
            "id": item.get("id"),
            "fund_code": item.get("fund_code", ""),
            "fund_name": item.get("fund_name", ""),
            "signal_status": item.get("signal_status") or "gray",
            "signal_reason": item.get("signal_reason") or item.get("notes") or "",
            "current_percentile": item.get("current_percentile"),
            "target_percentile": item.get("target_percentile"),
            "distance_to_buy": item.get("distance_to_buy"),
            "nav_updated_at": item.get("nav_updated_at"),
            "suggested_buy_price": item.get("suggested_buy_price"),
        })

    counts["total"] = len(items)
    summary = (
        f"可上车 {counts['green']}，接近上车 {counts['yellow']}，等待中 {counts['red']}，数据不足 {counts['gray']}，共 {counts['total']} 只"
        if counts["total"]
        else "暂无关注基金信号。"
    )
    return {
        "summary": summary,
        "counts": counts,
        "top_items": top_items,
    }


# 扩展的板块关键词映射
_SECTOR_ALIAS = {
    "cpo": ["cpo", "光模块", "光通信", "光器件", "硅光"],
    "光模块": ["光模块", "光通信", "cpo", "光器件", "硅光"],
    "通信设备": ["通信", "5g", "6g", "基站", "射频", "天线"],
    "半导体": ["芯片", "半导体", "集成电路", "晶圆", "封测", "soc", "asic"],
    "人工智能": ["ai", "人工智能", "大模型", "算力", "智谱", "gpt", "机器人", "深度学习"],
    "消费电子": ["消费电子", "手机", "vr", "ar", "可穿戴"],
    "新能源": ["新能源", "光伏", "风电", "储能", "锂电", "电池"],
    "消费": ["消费", "白酒", "食品", "啤酒", "餐饮", "零售", "家电"],
    "医药": ["医药", "医疗", "创新药", "疫苗", "cxo", "中药", "器械"],
    "金融": ["银行", "保险", "券商", "金融", "证券"],
    "地产": ["地产", "房地产", "楼市", "房价"],
    "军工": ["军工", "国防", "航天", "导弹", "航空"],
    "汽车": ["汽车", "新能源车", "电动车", "自动驾驶"],
    "基建": ["基建", "铁路", "公路", "水利"],
    "有色": ["有色", "铜", "铝", "黄金", "稀土", "锂矿"],
    "化工": ["化工", "石化", "化学", "材料"],
}


def _fuzzy_match_sectors_to_data(sectors: list[dict]) -> list[dict]:
    """将 LLM 返回的板块列表与指数/持仓做模糊匹配。"""
    indexes = list_valuation_indexes()
    holdings = list_holdings()
    seen_idx = {}
    for i in indexes:
        code = i.get("index_code", "")
        if code and code not in seen_idx:
            seen_idx[code] = i
    all_indexes = list(seen_idx.values())

    for s in sectors:
        name = s.get("name", "")
        match_terms = {name.lower()}
        alias_key = name.lower()
        if alias_key in _SECTOR_ALIAS:
            match_terms.update(k.lower() for k in _SECTOR_ALIAS[alias_key])
        for kw in s.get("keywords", []):
            match_terms.add(kw.lower())

        related_indexes = []
        for idx in all_indexes:
            idx_name = (idx.get("index_name") or "").lower()
            if any(term in idx_name for term in match_terms):
                related_indexes.append({
                    "index_code": idx.get("index_code"),
                    "index_name": idx.get("index_name"),
                    "percentile": _safe_percentile(idx.get("percentile")),
                    "current_value": idx.get("current_value"),
                    "metric_type": idx.get("metric_type"),
                })

        related_funds = []
        for h in holdings:
            fname = (h.get("fund_name") or "").lower()
            if any(term in fname for term in match_terms):
                related_funds.append({
                    "fund_code": h.get("fund_code"),
                    "fund_name": h.get("fund_name"),
                    "profit_rate": h.get("profit_rate"),
                })

        s["related_indexes"] = related_indexes
        s["related_funds"] = related_funds

    return sectors


# ── API 端点 ──────────────────────────────────────────────


@router.get("/market-overview")
async def market_overview_api():
    """市场行情总览：主要指数 + 领涨/领跌板块 + 涨跌家数。
    数据 5 分钟缓存（进程内），前端可高频调用。用于每日看板顶部行情展示。
    """
    try:
        data = get_market_overview()
        return {"ok": True, "data": data}
    except Exception as e:
        logging.warning(f"[market_overview] 行情数据获取失败: {e}")
        raise HTTPException(status_code=500, detail=f"行情数据获取失败: {e}")



@router.get("/overview")
async def get_market_intelligence_overview(force: bool = False):
    """市场热点情报概览 — 返回当日缓存（如已有）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"market_intelligence_{today}"

    cached = get_analysis_cache(cache_key)
    if cached:
        return cached

    return {
        "news": [],
        "cctv_news": [],
        "cctv_signal": "",
        "hot_topics": [],
        "sectors": [],
        "macro": {},
        "event_radar": {
            "summary": "",
            "accuracy": {"overall": {"total": 0, "correct": 0, "wrong": 0, "flat": 0, "accuracy": 0.0}, "by_sector": {}},
            "active_events": [],
            "recent_verified": [],
        },
        "watchlist_signals": {
            "summary": "",
            "counts": {"green": 0, "yellow": 0, "red": 0, "gray": 0, "total": 0},
            "top_items": [],
        },
        "summary": "暂无今日市场情报，请点击刷新触发分析",
        "forecast_1w": "",
        "forecast_2w": "",
        "risk_warning": "",
        "shared_signals": None,
        "fetched_at": "",
        "need_trigger": True,
    }


@router.post("/overview/trigger")
async def trigger_market_intelligence():
    """触发市场热点情报分析（异步）。"""
    task_id = create_async_task("market_intelligence", caller="market_intelligence")
    task = asyncio.create_task(_run_market_intelligence_async(task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_market_intelligence_async(task_id: int):
    """后台执行市场热点情报分析。"""
    try:
        result = await _do_market_intelligence()
        update_async_task(task_id, status="done", result=result)
    except Exception as e:
        logging.error(f"市场情报异步任务失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _do_market_intelligence():
    """市场热点情报分析业务逻辑。"""
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"market_intelligence_{today}"

    now = time.time()

    news, hot_topics, macro, cctv_news, market_hotspots, market_overview = await asyncio.gather(
        _fetch_news_multi(),
        _fetch_hot_topics(),
        asyncio.to_thread(_fetch_macro_data),
        _fetch_cctv_news(),
        _fetch_market_hotspots(),
        asyncio.to_thread(get_market_overview),
    )
    event_radar_brief = await asyncio.to_thread(_build_event_radar_brief)
    watchlist_brief = await asyncio.to_thread(_build_watchlist_signal_brief)
    all_news = news

    news_text = "\n".join(
        f"- {n['title']}（{n.get('source', '')}）: {n.get('summary', '')[:150]}"
        for n in all_news if n.get("title")
    ) if all_news else "暂无新闻"

    cctv_text = "\n".join(
        f"- 【{n.get('category', '经济')}】{n['title']}: {n.get('summary', '')[:150]}"
        for n in cctv_news if n.get("title")
    ) if cctv_news else "暂无央视新闻"

    topics_text = "\n".join(
        f"- {t['title']}: {t.get('summary', '')[:100]}"
        for t in hot_topics[:6] if t.get("title")
    ) if hot_topics else "暂无热门话题"

    hotspots_text = ""
    if market_hotspots:
        for h in market_hotspots:
            source = h.get("source", "")
            name = h.get("name", "")
            content = h.get("content", "")
            if content:
                hotspots_text += f"【{source} - {name}】\n{content}\n\n"
            elif name:
                hotspots_text += f"- [{source}] {name}\n"

    index_catalog = _build_index_catalog()
    holdings_text = _build_holdings_text()

    rag_context = ""
    try:
        rag_context = inject_rag_context(
            base_query="市场热点 板块轮动 投资策略 行业分析",
            caller="market_intel",
        )
    except Exception as e:
        logger.warning(f"RAG 检索失败: {e}")

    shared_evidence = {}
    try:
        shared_evidence = build_unified_evidence(
            user_id="default",
            query="市场热点 板块轮动 投资策略 未来1-2周",
            scenario_type="market_intelligence",
            limit=5,
        )
    except Exception as e:
        logger.warning(f"共享证据构建失败: {e}")

    bond = macro.get("bond", {})
    bond_text = f"债券温度{bond.get('temperature', '?')}°，收益率{bond.get('rate', '?')}%" if bond else "暂无"
    policy = macro.get("policy", {})
    policy_text = ""
    if policy.get("lpr"):
        lpr = policy["lpr"]
        if isinstance(lpr, dict):
            policy_text += f"LPR: {lpr.get('1y', '?')}/{lpr.get('5y', '?')} "
    if policy.get("cpi"):
        cpi = policy["cpi"]
        if isinstance(cpi, dict):
            policy_text += f"CPI: {cpi.get('latest', '?')}% "

    sector_perf_text = ""
    if market_overview:
        sectors_top = market_overview.get("sectors_top", [])
        sectors_bottom = market_overview.get("sectors_bottom", [])
        breadth = market_overview.get("breadth", {})
        
        if sectors_top:
            sector_perf_text += "领涨板块：\n"
            for s in sectors_top[:8]:
                sector_perf_text += f"- {s['name']}: +{s['change_pct']}%  领涨:{s.get('lead_stock', '')}{s.get('lead_change', '')}%\n"
        
        if sectors_bottom:
            sector_perf_text += "领跌板块：\n"
            for s in sectors_bottom[:5]:
                sector_perf_text += f"- {s['name']}: {s['change_pct']}%\n"
        
        if breadth:
            sector_perf_text += f"涨跌家数：{breadth.get('up', 0)}↑ {breadth.get('down', 0)}↓ 涨停{breadth.get('limit_up', 0)}家 跌停{breadth.get('limit_down', 0)}家\n"

    base_prompt = ""
    agent_id = None
    agent_name = "市场情报分析师"
    try:
        agent_row = get_analysis_agent_by_name("市场情报分析师")
        if agent_row:
            agent_id = agent_row.get("id")
            agent_name = agent_row.get("name", "市场情报分析师")
            base_prompt = agent_row.get("system_prompt") or ""
    except Exception:
        pass
    if not base_prompt:
        from db.analysis import DEFAULT_MARKET_INTELLIGENCE_PROMPT
        base_prompt = DEFAULT_MARKET_INTELLIGENCE_PROMPT

    prompt = f"""{base_prompt}

---

【央视新闻联播（政策风向标）】
{cctv_text}

【今日财经新闻（多源聚合）】
{news_text}

【热门话题】
{topics_text}

{f"【实时热点板块/题材】{chr(10)}{hotspots_text}" if hotspots_text else ""}

{f"【今日板块实时涨跌】{chr(10)}{sector_perf_text}" if sector_perf_text else ""}

【指数估值目录】
{index_catalog}

【用户持仓】
{holdings_text}

【宏观环境】
债券市场：{bond_text}
政策指标：{policy_text or '暂无'}
{rag_context if rag_context else ''}

【共享证据层】
{shared_evidence.get("prompt_context", "")[:1800] if shared_evidence else '暂无共享证据'}

【事件雷达验证摘要】
{event_radar_brief.get("summary", "暂无事件雷达数据。")}

【关注基金信号摘要】
{watchlist_brief.get("summary", "暂无关注基金信号。")}

请从以上新闻中提炼今日真正的市场热点，特别关注央视新闻的政策信号，输出严格JSON。

重要规则：
1. 只推荐今日板块实时涨跌中涨幅靠前或有明确政策驱动的板块
2. 如果某板块新闻利好但今日实际下跌，需说明「短期承压但中长期有逻辑」，不要标为"今日热门"
3. 结合板块涨跌和新闻，区分「已兑现」（已大涨）和「待兑现」（有逻辑但还没涨）的机会
4. 结合事件雷达验证结果，给出未来 1-2 周偏多/偏空的短期判断
5. 结合关注基金信号，提示哪些基金更接近可上车区间"""

    sectors = []
    summary = ""
    cctv_signal = ""
    forecast_1w = ""
    forecast_2w = ""
    risk_warning = ""
    watchlist_takeaway = ""
    llm_start = time.time()
    llm_status = "success"
    response = None
    llm_error_msg = ""
    # 分析记录埋点（running）
    trace_id = f"log_{uuid.uuid4().hex[:12]}"
    try:
        create_analysis_log(
            trace_id=trace_id, agent_id=agent_id, agent_name=agent_name,
            analysis_type="market_intel", source_table="analysis_history",
            source_id=None, query="市场情报分析",
            input_summary="市场情报",
        )
    except Exception as _e:
        logger.warning(f"create_analysis_log 失败: {_e}")
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: _call_llm(
                caller="market_intelligence",
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=get_config_float("llm.temperature_default", 0.3),
                max_tokens=get_config_int("llm.max_tokens_report", 8192),
                trace_id=trace_id,
            )),
            timeout=120,
        )
        content = response.choices[0].message.content or "{}"
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(content)
        sectors = parsed.get("sectors", [])
        summary = parsed.get("summary", "")
        cctv_signal = parsed.get("cctv_signal", "")
        forecast_1w = parsed.get("forecast_1w", "")
        forecast_2w = parsed.get("forecast_2w", "")
        risk_warning = parsed.get("risk_warning", "")
        watchlist_takeaway = parsed.get("watchlist_takeaway", "")
    except Exception as e:
        logger.warning(f"市场情报 LLM 推断失败: {e}")
        summary = f"分析失败: {e}"
        content = ""
        llm_status = "error"
        llm_error_msg = str(e)

    llm_duration = int((time.time() - llm_start) * 1000)
    # 分析记录埋点（done/error）
    try:
        if llm_status == "success":
            _tokens = response.usage.total_tokens if hasattr(response, 'usage') and response.usage else 0
            complete_analysis_log(
                trace_id=trace_id, status="done",
                duration_ms=llm_duration, token_usage=_tokens,
            )
        else:
            complete_analysis_log(
                trace_id=trace_id, status="error",
                duration_ms=llm_duration, error_msg=llm_error_msg,
            )
    except Exception as _e:
        logger.warning(f"complete_analysis_log 失败: {_e}")

    sectors = _fuzzy_match_sectors_to_data(sectors)

    sector_names = [s.get("name", "") for s in sectors if s.get("name")]
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO analysis_history (agent_id, agent_name, prompt_used, news_context, result, token_usage) VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id or 0, "市场情报分析师", base_prompt[:500], news_text[:500],
             json.dumps({"summary": summary, "sectors": sector_names}, ensure_ascii=False),
             (response.usage.total_tokens if llm_status == "success" and hasattr(response, 'usage') and response.usage else 0))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"记录市场情报分析历史失败: {e}")

    try:
        create_agent_run(
            conversation_id=0, message_id=0,
            agent_key="market_intelligence", agent_name="市场情报分析师",
            query=news_text[:500], result=summary[:500],
            duration_ms=llm_duration, status=llm_status,
        )
    except Exception as e:
        logger.warning(f"记录 agent_run 失败: {e}")

    result = {
        "news": all_news[:15],
        "cctv_news": cctv_news,
        "cctv_signal": cctv_signal,
        "hot_topics": hot_topics,
        "sectors": sectors,
        "macro": {
            "bond": bond,
            "policy": policy,
            "bond_text": bond_text,
            "policy_text": policy_text,
        },
        "summary": summary,
        "forecast_1w": forecast_1w,
        "forecast_2w": forecast_2w,
        "risk_warning": risk_warning,
        "watchlist_takeaway": watchlist_takeaway,
        "event_radar": event_radar_brief,
        "watchlist_signals": watchlist_brief,
        "shared_signals": shared_evidence,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    save_analysis_cache(cache_key, result)
    return result


@router.get("/sector-detail/{sector_name}")
async def get_sector_detail(sector_name: str):
    """单个板块深度分析 — 关联指数、持仓、相关新闻。"""
    indexes = list_valuation_indexes()
    holdings = list_holdings()

    match_terms = {sector_name.lower()}
    alias_key = sector_name.lower()
    if alias_key in _SECTOR_ALIAS:
        match_terms.update(k.lower() for k in _SECTOR_ALIAS[alias_key])

    seen = {}
    for i in indexes:
        code = i.get("index_code", "")
        if code and code not in seen:
            seen[code] = i
    related_indexes = [
        {
            "index_code": i.get("index_code"),
            "index_name": i.get("index_name"),
            "percentile": _safe_percentile(i.get("percentile")),
            "current_value": i.get("current_value"),
            "metric_type": i.get("metric_type"),
        }
        for i in seen.values()
        if any(term in (i.get("index_name") or "").lower() for term in match_terms)
    ]

    related_funds = [
        {
            "fund_code": h.get("fund_code"),
            "fund_name": h.get("fund_name"),
            "profit_rate": h.get("profit_rate"),
            "current_value": h.get("current_value"),
        }
        for h in holdings
        if any(term in (h.get("fund_name") or "").lower() for term in match_terms)
    ]

    news = await _fetch_news_multi()
    related_news = [
        n for n in news
        if any(term in f"{n.get('title', '')} {n.get('summary', '')}".lower() for term in match_terms)
    ]

    return {
        "sector": sector_name,
        "related_indexes": related_indexes,
        "related_funds": related_funds,
        "related_news": related_news,
    }
