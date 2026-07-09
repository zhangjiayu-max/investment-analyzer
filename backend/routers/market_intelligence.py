"""市场热点情报路由 — /api/market-intelligence/*

数据来源：
1. YingMi MCP SearchFinancialNews — 多关键词搜索（A股、半导体、AI、CPO光模块、通信、新能源）
2. YingMi MCP SearchHotTopic — 热门话题
3. akshare web_search — 东方财富/CCTV 补充资讯
4. 债券温度 + 宏观政策数据
5. 指数估值数据库（PE/PB 百分位）
6. 用户持仓数据
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime

from fastapi import APIRouter

from db import list_valuation_indexes, list_holdings, get_analysis_agent, get_config_float, get_config_int
from db import create_async_task, update_async_task, get_async_task
from db.portfolio import save_analysis_cache, get_analysis_cache
from db.agents import create_agent_run
from db._conn import _get_conn
from services.llm_service import _call_llm, MODEL
from services.rag import build_rag_context_with_details, log_rag_search
from services.market_data import get_market_overview

router = APIRouter(prefix="/api/market-intelligence", tags=["market-intelligence"])

logger = logging.getLogger(__name__)

_background_tasks = set()

# 注：market_overview 端点已迁移到 routers/analysis/market_intel.py 的 /overview

# ── 缓存（使用数据库 analysis_cache 表，每日自动失效） ──────

# ── 新闻搜索关键词（多维度覆盖） ──────────────────────────

# 基础关键词（兜底用）
_BASE_NEWS_KEYWORDS = ["A股 市场", "基金 投资"]


async def _get_dynamic_keywords() -> list[str]:
    """从多个来源获取实时热点，动态生成搜索关键词。"""
    keywords = []

    # 来源1: akshare 热门概念
    try:
        import akshare as ak
        df = ak.stock_hot_keyword_em()
        if df is not None and len(df) > 0:
            hot_kw = df["概念名称"].tolist()[:5]
            keywords.extend(hot_kw)
            logger.info(f"[market-intel] akshare 热点概念: {hot_kw}")
    except Exception:
        pass

    # 来源2: 盈米 MCP SearchHotTopic
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

    # 去重并返回
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    if unique:
        logger.info(f"[market-intel] 动态热点关键词: {unique[:6]}")
    return unique[:6]


# ── 数据采集 ──────────────────────────────────────────────


async def _fetch_news_multi() -> list[dict]:
    """多关键词并行搜索财经新闻，去重后返回。关键词动态从市场热点获取。"""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()

        # 动态获取热点关键词 + 基础关键词
        dynamic_kw = await _get_dynamic_keywords()
        all_keywords = dynamic_kw + _BASE_NEWS_KEYWORDS
        logger.info(f"[market-intel] 搜索关键词: {all_keywords}")

        async def _search(keyword: str) -> list[dict]:
            try:
                raw = await asyncio.to_thread(
                    lambda kw=keyword: mcp.call_tool("SearchFinancialNews", {"keyword": kw, "pageSize": 5})
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
        # 去重（按标题）
        seen_titles = set()
        all_news = []
        for items in results:
            for n in items:
                title = n.get("title", "").strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    all_news.append(n)
        return all_news[:15]  # 最多 15 条
    except Exception as e:
        logger.warning(f"市场情报新闻获取失败: {e}")
    return []


async def _fetch_web_news() -> list[dict]:
    """akshare 补充资讯（东方财富 + CCTV）。"""
    try:
        from tools import execute_tool
        raw = await asyncio.to_thread(
            lambda: execute_tool("web_search", {"query": "A股 今日热点板块 CPO 光模块 通信 半导体", "max_results": 5})
        )
        if not raw:
            return []
        # 尝试解析 JSON 格式的新闻结果
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
            # 如果解析失败，返回原始文本作为单条新闻
            if len(raw) > 20:
                return [{"title": "市场资讯", "summary": raw[:500], "source": "web_search", "date": "", "url": ""}]
        # 如果是字典格式，直接解析
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
    """获取央视新闻（新闻联播等），返回带分类的新闻列表。

    分类：经济、金融、政策、农业、科技、贸易
    """
    try:
        import akshare as ak
        today = datetime.now().strftime("%Y%m%d")
        df = await asyncio.to_thread(ak.news_cctv, date=today)

        if df is None or len(df) == 0:
            return []

        # 央视新闻分类关键词
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

            # 分类匹配
            category = "其他"
            for cat, keywords in CCTV_CATEGORIES.items():
                if any(kw in title or kw in content for kw in keywords):
                    category = cat
                    break

            # 只保留与经济金融相关的新闻
            if category == "其他":
                continue

            # 构建央视网搜索链接
            search_url = f"https://search.cctv.com/search.php?qtext={title}&type=web"

            results.append({
                "title": title,
                "summary": content[:200],
                "source": "央视新闻",
                "category": category,
                "date": str(row.get("date", "")),
                "url": search_url,
            })

        return results[:10]  # 最多10条
    except Exception as e:
        logger.warning(f"央视新闻获取失败: {e}")
        return []


async def _fetch_hot_topics() -> list[dict]:
    """获取热门话题（YingMi MCP SearchHotTopic）。"""
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
    """获取市场热点板块和题材。

    数据源：
    1. 东方财富妙想 API（stockHotspot）— 官方 AI 热点分析
    2. 东方财富人气排名 — 直接 API
    """
    hotspots = []

    # 1. 东方财富妙想热点分析
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

    # 2. 东方财富资讯搜索（最新研报、新闻）
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

    # 3. 东方财富人气排名（直接 API，不依赖 SDK）
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
        pct = i.get("percentile")
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


# ── 板块→指数/基金智能匹配 ─────────────────────────────────

# 扩展的板块关键词映射（支持细分领域）
_SECTOR_ALIAS = {
    # 细分科技
    "cpo": ["cpo", "光模块", "光通信", "光器件", "硅光"],
    "光模块": ["光模块", "光通信", "cpo", "光器件", "硅光"],
    "通信设备": ["通信", "5g", "6g", "基站", "射频", "天线"],
    "半导体": ["芯片", "半导体", "集成电路", "晶圆", "封测", "soc", "asic"],
    "人工智能": ["ai", "人工智能", "大模型", "算力", "智谱", "gpt", "机器人", "深度学习"],
    "消费电子": ["消费电子", "手机", "vr", "ar", "可穿戴"],
    # 大类
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
    """将 LLM 返回的板块列表与指数/持仓做模糊匹配。

    每个 sector dict 可能有 name 和 keywords 字段。
    用 keywords + name 多维度匹配，比纯 name 子串匹配更准确。
    """
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
        # 收集所有匹配关键词：板块名 + 别名 + LLM 提供的 keywords
        match_terms = {name.lower()}
        # 从别名表扩展
        alias_key = name.lower()
        if alias_key in _SECTOR_ALIAS:
            match_terms.update(k.lower() for k in _SECTOR_ALIAS[alias_key])
        # LLM 可能返回 keywords 字段
        for kw in s.get("keywords", []):
            match_terms.add(kw.lower())

        # 匹配指数
        related_indexes = []
        for idx in all_indexes:
            idx_name = (idx.get("index_name") or "").lower()
            if any(term in idx_name for term in match_terms):
                related_indexes.append({
                    "index_code": idx.get("index_code"),
                    "index_name": idx.get("index_name"),
                    "percentile": idx.get("percentile"),
                    "current_value": idx.get("current_value"),
                    "metric_type": idx.get("metric_type"),
                })

        # 匹配持仓
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

