"""政策新闻聚合服务（M4 新增）。

聚合来源：
- 盈米新闻 API（yingmi_search_news，加政策关键词过滤）
- 东方财富妙想（eastmoney_search，按政策关键词查询）
- akshare 央视新闻（news_cctv，按日期过滤政策标签）

按重要性分级：高（央行/国务院/证监会发文）/中（部委规章）/低（媒体解读）。

缓存：5 分钟 TTL。
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# 政策关键词分类（按级别）
_HIGH_LEVEL_KEYWORDS = [
    "央行", "国务院", "证监会", "财政部", "银保监会", "国务院常务会议",
    "降准", "降息", "MLF", "LPR", "再贷款", "再贴现",
    "印花税", "资本利得税", "证券法",
]

_MEDIUM_LEVEL_KEYWORDS = [
    "发改委", "商务部", "工信部", "住建部", "农业农村部", "国家能源局",
    "专项债", "政策性银行", "再融资", "产业政策", "补贴", "减税",
    "消费券", "以旧换新", "去库存", "稳增长",
]

_LOW_LEVEL_KEYWORDS = [
    "解读", "点评", "分析", "影响", "如何看",
    "利好", "利空", "概念股",
]

# 行业映射
_INDUSTRY_KEYWORDS = {
    "白酒": ["白酒", "食品饮料", "消费"],
    "银行": ["银行", "金融", "券商"],
    "新能源": ["新能源", "光伏", "锂电", "储能"],
    "半导体": ["半导体", "芯片", "集成电路"],
    "医药": ["医药", "医疗", "生物"],
    "房地产": ["房地产", "地产", "楼市"],
    "军工": ["军工", "国防", "航天"],
    "港股": ["港股", "恒生", "南向", "港股通"],
}


def _get_cached(key: str):
    try:
        from services.market_data import _get_cached as _gc
        return _gc(key)
    except Exception:
        return None


def _set_cached(key: str, value, ttl: int = 300):
    try:
        from services.market_data import _set_cached as _sc
        _sc(key, value)
    except Exception:
        pass


def _classify_importance(title: str, content: str = "") -> str:
    """根据标题/内容关键词分类政策重要性。"""
    text = (title + " " + content).lower()
    for kw in _HIGH_LEVEL_KEYWORDS:
        if kw in text or kw.lower() in text:
            return "high"
    for kw in _MEDIUM_LEVEL_KEYWORDS:
        if kw in text or kw.lower() in text:
            return "medium"
    for kw in _LOW_LEVEL_KEYWORDS:
        if kw in text or kw.lower() in text:
            return "low"
    return "low"


def _detect_industries(text: str) -> list:
    """从文本中识别相关行业。"""
    matched = []
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                matched.append(industry)
                break
    return matched


def _fetch_from_yingmi(query: str, limit: int = 10) -> list:
    """从盈米新闻 API 拉取政策相关新闻。"""
    results = []
    try:
        from mcp.yingmi_client import get_yingmi_client
        client = get_yingmi_client()
        # 用政策关键词扩展查询
        policy_query = query if any(kw in query for kw in ["政策", "央行", "国务院"]) else f"{query} 政策"
        raw = client._invoke("search_news", {"query": policy_query, "limit": limit})
        if isinstance(raw, dict) and raw.get("success"):
            items = raw.get("data", {}).get("items", []) or raw.get("data", {}).get("news", [])
            for item in items:
                title = item.get("title", "")
                content = item.get("content", "") or item.get("summary", "")
                results.append({
                    "title": title,
                    "snippet": content[:200] if content else "",
                    "url": item.get("url", ""),
                    "time": item.get("publish_time", "") or item.get("time", ""),
                    "source": "盈米新闻",
                    "importance": _classify_importance(title, content),
                    "industries": _detect_industries(title + " " + content),
                })
    except Exception as e:
        logger.debug(f"[policy_news] 盈米新闻拉取失败: {e}")
    return results


def _fetch_from_eastmoney(query: str, limit: int = 10) -> list:
    """从东方财富妙想拉取政策新闻。"""
    results = []
    try:
        from mcp.eastmoney_client import get_eastmoney_client
        client = get_eastmoney_client()
        # 用政策关键词查询
        policy_query = f"{query} 政策 利好"
        text = client.financial_assistant(policy_query)
        if text and len(text) > 50:
            # 拆分为多条新闻（按段落或换行）
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            for p in paragraphs[:limit]:
                title = p.split("。")[0][:80] if "。" in p else p[:80]
                results.append({
                    "title": title,
                    "snippet": p[:300],
                    "url": "",
                    "time": datetime.now().strftime("%Y-%m-%d"),
                    "source": "东方财富",
                    "importance": _classify_importance(title, p),
                    "industries": _detect_industries(p),
                })
    except Exception as e:
        logger.debug(f"[policy_news] 东方财富拉取失败: {e}")
    return results


def _fetch_from_cctv(limit: int = 10) -> list:
    """从央视新闻（akshare）拉取政策新闻。"""
    results = []
    try:
        import akshare as ak
        today = datetime.now().strftime("%Y%m%d")
        df = ak.news_cctv(date=today)
        if df is None or len(df) == 0:
            return results

        # 过滤政策相关新闻
        for _, row in df.head(limit * 3).iterrows():
            title = str(row.get("title", ""))
            content = str(row.get("content", ""))
            # 至少包含一个政策关键词才收录
            if not any(kw in title + content for kw in _HIGH_LEVEL_KEYWORDS + _MEDIUM_LEVEL_KEYWORDS):
                continue
            results.append({
                "title": title,
                "snippet": content[:200] if content else "",
                "url": "",
                "time": str(row.get("date", "")),
                "source": "央视新闻",
                "importance": _classify_importance(title, content),
                "industries": _detect_industries(title + " " + content),
            })
            if len(results) >= limit:
                break
    except Exception as e:
        logger.debug(f"[policy_news] 央视新闻拉取失败: {e}")
    return results


def _fetch_from_web_search(query: str, limit: int = 10) -> list:
    """conv#130 修复：三源全失败时的 web_search 降级。

    复用 akshare 的 stock_news_em（东方财富A股新闻）源，按 query 关键词过滤。
    标注 source="web_search降级"，importance 默认 medium（无法确定政策级别）。
    """
    results = []
    if not query:
        return results
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol="A股")
        if df is None or len(df) == 0:
            return results

        # 提取 query 关键词用于过滤
        filter_kws = [kw for kw in [query, query[:4], query[:6]] if len(kw) >= 2]

        for _, row in df.head(limit * 5).iterrows():
            title = str(row.get("新闻标题", ""))
            snippet = str(row.get("新闻内容", ""))[:200] if row.get("新闻内容") else ""
            # 按 query 关键词过滤
            text = (title + " " + snippet).lower()
            if not any(kw.lower() in text for kw in filter_kws):
                continue
            results.append({
                "title": title,
                "snippet": snippet,
                "url": str(row.get("新闻链接", "")),
                "time": str(row.get("发布时间", "")),
                "source": "web_search降级",
                "importance": "medium",  # 降级源无法确定政策级别，默认 medium
                "industries": _detect_industries(title + " " + snippet),
            })
            if len(results) >= limit:
                break
    except Exception as e:
        logger.debug(f"[policy_news] web_search 降级失败: {e}")
    return results


def get_policy_news(query: str = "", limit: int = 10) -> dict:
    """聚合政策新闻（多源）。

    Args:
        query: 查询关键词（如"恒生科技"、"房地产"），为空则拉今日政策面新闻
        limit: 每个源的最大数量

    Returns:
        {
            "query": str,
            "count": int,
            "items": [
                {"title", "snippet", "url", "time", "source", "importance", "industries"}
            ],
            "high_count": int,    # 高重要性新闻数量
            "medium_count": int,
            "low_count": int,
            "summary": str,        # 政策面综合提示
        }
    """
    cache_key = f"policy_news:{query}:{limit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    items = []
    # 并行拉取多源（实际是顺序，因 MCP 客户端单实例）
    if query:
        # 有具体查询 → 用盈米 + 东方财富
        items.extend(_fetch_from_yingmi(query, limit))
        items.extend(_fetch_from_eastmoney(query, limit))
    # 永远补充今日央视新闻
    items.extend(_fetch_from_cctv(limit))

    # conv#130 修复：三源全失败时降级到 web_search
    # 原实现：三源全失败返回空 items，导致 conv#130 中 query_policy_news 返回错误
    # 新实现：三源全失败时调用 web_search 工具查政策新闻，标注 data_source
    if not items:
        fallback_items = _fetch_from_web_search(query, limit)
        if fallback_items:
            items.extend(fallback_items)
            logger.info(f"[policy_news] 三源全失败，web_search 降级返回 {len(fallback_items)} 条")

    # 去重（按标题前 30 字）
    seen = set()
    deduped = []
    for item in items:
        key = item["title"][:30]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    # 按重要性排序（high > medium > low），同级别按时间倒序
    importance_order = {"high": 0, "medium": 1, "low": 2}
    deduped.sort(key=lambda x: (importance_order.get(x["importance"], 3), x.get("time", "")), reverse=False)
    deduped.sort(key=lambda x: importance_order.get(x["importance"], 3))

    # 截断
    deduped = deduped[:limit * 2]

    # 统计
    high_count = sum(1 for x in deduped if x["importance"] == "high")
    medium_count = sum(1 for x in deduped if x["importance"] == "medium")
    low_count = sum(1 for x in deduped if x["importance"] == "low")

    # 综合提示
    if high_count > 0:
        summary = f"今日有 {high_count} 条高级别政策新闻（央行/国务院/证监会等），需重点关注"
    elif medium_count > 0:
        summary = f"今日有 {medium_count} 条部委政策新闻，关注行业影响"
    elif low_count > 0:
        summary = f"今日有 {low_count} 条政策解读/媒体观点，参考性较弱"
    else:
        summary = "今日暂无明显政策面新闻"

    result = {
        "query": query,
        "count": len(deduped),
        "items": deduped,
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        "summary": summary,
        "note": "importance 分级：high=央行/国务院/证监会发文；medium=部委规章；low=媒体解读。industries 标注涉及行业。",
        "data_source": "web_search_fallback" if (not items and deduped) else "multi_source",
    }
    _set_cached(cache_key, result)
    return result


def get_policy_news_summary(query: str = "") -> dict:
    """政策新闻汇总（无 items，仅统计）。"""
    full = get_policy_news(query=query, limit=5)
    return {
        "query": full["query"],
        "high_count": full["high_count"],
        "medium_count": full["medium_count"],
        "low_count": full["low_count"],
        "summary": full["summary"],
        "top_titles": [item["title"] for item in full["items"][:3]],
    }
