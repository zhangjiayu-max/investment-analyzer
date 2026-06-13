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
from db.portfolio import save_analysis_cache, get_analysis_cache
from db.agents import create_agent_run
from db._conn import _get_conn
from llm_service import _call_llm, MODEL
from rag import build_rag_context_with_details, log_rag_search

router = APIRouter(prefix="/api/market-intelligence", tags=["market-intelligence"])

logger = logging.getLogger(__name__)

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


@router.get("/overview")
async def get_market_intelligence_overview(force: bool = False):
    """市场热点情报概览 — 多源新闻聚合 + LLM 自由推断热点板块。

    每日首次分析后结果缓存到数据库，后续请求直接返回缓存。
    传 force=true 可强制重新分析。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"market_intelligence_{today}"

    # 非强制模式：先查数据库缓存（当日有效）
    if not force:
        cached = get_analysis_cache(cache_key)
        if cached:
            return cached

    now = time.time()

    # 1. 并行获取多源数据
    news, hot_topics, macro, cctv_news, market_hotspots = await asyncio.gather(
        _fetch_news_multi(),
        _fetch_hot_topics(),
        asyncio.to_thread(_fetch_macro_data),
        _fetch_cctv_news(),
        _fetch_market_hotspots(),
    )
    all_news = news  # 盈米 MCP 新闻质量足够，不再混入低质量 akshare 泛财经

    # 2. 构建新闻文本
    news_text = "\n".join(
        f"- {n['title']}（{n.get('source', '')}）: {n.get('summary', '')[:150]}"
        for n in all_news if n.get("title")
    ) if all_news else "暂无新闻"

    # 央视新闻单独构建（带分类）
    cctv_text = "\n".join(
        f"- 【{n.get('category', '经济')}】{n['title']}: {n.get('summary', '')[:150]}"
        for n in cctv_news if n.get("title")
    ) if cctv_news else "暂无央视新闻"

    topics_text = "\n".join(
        f"- {t['title']}: {t.get('summary', '')[:100]}"
        for t in hot_topics[:6] if t.get("title")
    ) if hot_topics else "暂无热门话题"

    # 市场热点板块/题材聚合
    hotspots_text = ""
    if market_hotspots:
        for h in market_hotspots:
            source = h.get("source", "")
            name = h.get("name", "")
            content = h.get("content", "")
            if content:
                # AI 分析类结果（有长文本内容）
                hotspots_text += f"【{source} - {name}】\n{content}\n\n"
            elif name:
                # 简单列表类结果
                hotspots_text += f"- [{source}] {name}\n"

    index_catalog = _build_index_catalog()
    holdings_text = _build_holdings_text()

    # RAG 知识库检索（搜索与市场热点相关的文章、分析）
    rag_context = ""
    try:
        rag_query = "市场热点 板块轮动 投资策略 行业分析"
        rag_result = build_rag_context_with_details(query=rag_query, limit=5)
        rag_context = rag_result.get("context", "")
        # 记录 RAG 检索日志
        log_rag_search(
            conversation_id=0,
            message_id=0,
            query=rag_query,
            keywords=rag_result.get("keywords", []),
            results=rag_result.get("results", []),
            fts_count=rag_result.get("fts_count", 0),
            chroma_count=rag_result.get("chroma_count", 0),
            freshness_filtered=rag_result.get("freshness_filtered", 0),
        )
    except Exception as e:
        logger.warning(f"RAG 检索失败: {e}")

    # 宏观摘要
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

    # 3. 加载 prompt（从 analysis_agents 表，按名称查找）
    base_prompt = ""
    agent_id = None
    try:
        conn = _get_conn()
        row = conn.execute("SELECT id, system_prompt FROM analysis_agents WHERE name = ?", ("市场情报分析师",)).fetchone()
        conn.close()
        if row:
            agent_id = row["id"]
            base_prompt = row["system_prompt"] or ""
    except Exception:
        pass
    if not base_prompt:
        # fallback: 使用 db/analysis.py 中的默认 prompt
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

【指数估值目录】
{index_catalog}

【用户持仓】
{holdings_text}

【宏观环境】
债券市场：{bond_text}
政策指标：{policy_text or '暂无'}

【知识库参考（历史分析/文章）】
{rag_context[:1500] if rag_context else '暂无相关知识库内容'}

请从以上新闻中提炼今日真正的市场热点，特别关注央视新闻的政策信号，输出严格JSON。"""

    # 4. LLM 推断 + 记录
    sectors = []
    summary = ""
    llm_start = time.time()
    llm_status = "success"
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: _call_llm(
                caller="market_intelligence",
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=get_config_float("llm.temperature_default", 0.3),
                max_tokens=get_config_int("llm.max_tokens_report", 8192),
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
    except Exception as e:
        logger.warning(f"市场情报 LLM 推断失败: {e}")
        summary = f"分析失败: {e}"
        content = ""
        llm_status = "error"

    llm_duration = int((time.time() - llm_start) * 1000)

    # 5. 板块→指数/基金智能匹配
    sectors = _fuzzy_match_sectors_to_data(sectors)

    # 6. 记录到 analysis_history + agent_runs
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

    # 7. 组装结果
    result = {
        "news": all_news[:15],  # 合并后的新闻（最多15条）
        "cctv_news": cctv_news,  # 央视新闻（单独展示）
        "cctv_signal": cctv_signal,  # 央视新闻政策信号
        "hot_topics": hot_topics,
        "sectors": sectors,
        "macro": {
            "bond": bond,
            "policy": policy,
            "bond_text": bond_text,
            "policy_text": policy_text,
        },
        "summary": summary,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 保存到数据库缓存（当日有效）
    save_analysis_cache(cache_key, result)
    return result


@router.get("/sector-detail/{sector_name}")
async def get_sector_detail(sector_name: str):
    """单个板块深度分析 — 关联指数、持仓、相关新闻。"""
    indexes = list_valuation_indexes()
    holdings = list_holdings()

    # 收集匹配关键词
    match_terms = {sector_name.lower()}
    alias_key = sector_name.lower()
    if alias_key in _SECTOR_ALIAS:
        match_terms.update(k.lower() for k in _SECTOR_ALIAS[alias_key])

    # 匹配指数
    seen = {}
    for i in indexes:
        code = i.get("index_code", "")
        if code and code not in seen:
            seen[code] = i
    related_indexes = [
        {
            "index_code": i.get("index_code"),
            "index_name": i.get("index_name"),
            "percentile": i.get("percentile"),
            "current_value": i.get("current_value"),
            "metric_type": i.get("metric_type"),
        }
        for i in seen.values()
        if any(term in (i.get("index_name") or "").lower() for term in match_terms)
    ]

    # 匹配持仓
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

    # 匹配新闻
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
