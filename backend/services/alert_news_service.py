"""P1-3: 财经新闻检索服务 — 为预警附加相关新闻。

成本管控：
  - MCP 调用受 alert.news_integration 开关控制（默认 false）
  - 30 分钟缓存（alert.news_cache_ttl）
  - 仅 danger/warning 级预警触发检索
  - 单基金最多 3 条新闻（alert.news_per_fund）

MCP 响应结构（参考 routers/market_intelligence.py:100-129）：
  result = {"content": [{"type": "text", "text": "<json>"}]}
  parsed = {"success": true, "data": {"items": [{title, summary, sources, publishDate, url}]}}
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from db._conn import _get_conn
from db.config import get_config as _get_config

logger = logging.getLogger(__name__)

# 基金名称常见后缀，提取关键词时去除
_FUND_NAME_SUFFIXES = (
    "ETF", "LOF", "联接", "指数", "增强", "股票型", "混合型", "债券型", "基金",
    "A", "C", "B", "E", "份额", "类",
)


def get_alert_news(fund_code: str, fund_name: str = "",
                   force_refresh: bool = False) -> list[dict]:
    """获取基金相关财经新闻（带缓存）。

    返回：[{news_title, news_summary, news_source, news_url, published_at}, ...]
    开关关闭或检索失败时返回空列表，不阻塞预警流程。
    """
    if _get_config("alert.news_integration", "false") != "true":
        return []

    ttl_min = int(_get_config("alert.news_cache_ttl", "30"))
    per_fund = int(_get_config("alert.news_per_fund", "3"))

    # 检查缓存
    if not force_refresh:
        cached = _load_cached_news(fund_code, ttl_min)
        if cached:
            return cached[:per_fund]

    # 检索关键词：优先基金简称（去常见后缀），fallback 用基金代码
    keyword = _extract_keyword(fund_name) or fund_code
    news_list = _fetch_news_from_mcp(keyword, limit=per_fund)
    if not news_list:
        return []

    # 写缓存
    _save_news_cache(fund_code, keyword, news_list)
    return news_list[:per_fund]


def _load_cached_news(fund_code: str, ttl_min: int) -> list[dict]:
    """从 alert_news_cache 表读取未过期缓存。"""
    cutoff = (datetime.now() - timedelta(minutes=ttl_min)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT news_title, news_summary, news_source, news_url, published_at
            FROM alert_news_cache
            WHERE fund_code = ? AND fetched_at >= ?
            ORDER BY published_at DESC
        """, (fund_code, cutoff)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _fetch_news_from_mcp(keyword: str, limit: int = 3) -> list[dict]:
    """通过 MCP SearchFinancialNews 检索新闻。

    失败时返回空列表，不阻塞预警流程。
    响应解析参考 routers/market_intelligence.py:100-129。
    """
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        today = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        raw = mcp.call_tool("SearchFinancialNews", {
            "keyword": keyword,
            "startDate": start_date,
            "endDate": today,
            "page": 1,
            "pageSize": limit,
        })
        items = []
        if isinstance(raw, dict):
            for c in raw.get("content", []):
                if c.get("type") == "text":
                    try:
                        parsed = json.loads(c["text"])
                    except (json.JSONDecodeError, KeyError):
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    data = parsed.get("data", {})
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get("items", [])
        result = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = it.get("title") or it.get("newsTitle") or ""
            if not title:
                continue
            result.append({
                "news_title": title,
                "news_summary": it.get("summary") or it.get("abstract") or "",
                "news_source": it.get("sources") or it.get("source") or it.get("media") or "",
                "news_url": it.get("url") or it.get("link") or "",
                "published_at": it.get("publishDate") or it.get("publishTime") or it.get("publishedAt") or "",
            })
        return result
    except Exception as e:
        logger.warning(f"[alert_news] MCP 检索失败 keyword={keyword}: {e}")
        return []


def _save_news_cache(fund_code: str, keyword: str, news_list: list[dict]):
    """写入新闻缓存（先清除该基金旧缓存）。"""
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM alert_news_cache WHERE fund_code = ?", (fund_code,)
        )
        for n in news_list:
            conn.execute("""
                INSERT INTO alert_news_cache
                (fund_code, keyword, news_title, news_summary, news_source, news_url, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                fund_code, keyword,
                n["news_title"], n.get("news_summary", ""),
                n.get("news_source", ""), n.get("news_url", ""),
                n.get("published_at", ""),
            ))
        conn.commit()
    finally:
        conn.close()


def _extract_keyword(fund_name: str) -> str:
    """从基金名称提取检索关键词（去掉常见后缀）。

    例如 "宏利消费红利指数A" → "宏利消费红利"
    """
    if not fund_name:
        return ""
    kw = fund_name.strip()
    # 反复去除后缀（如 "中证500ETF" → "中证500" → 不再匹配）
    changed = True
    while changed:
        changed = False
        for s in _FUND_NAME_SUFFIXES:
            if kw.endswith(s) and len(kw) > len(s) + 1:
                kw = kw[:-len(s)]
                changed = True
    return kw.strip() or fund_name


def enrich_alerts_with_news(alerts: list[dict]) -> list[dict]:
    """批量给预警列表附加 related_news 字段。

    仅 danger/warning 级触发，避免 info 级浪费 MCP 配额。
    同基金多次出现只检索一次（按 fund_code 去重）。
    开关关闭时所有预警 related_news 返回空列表。
    """
    if _get_config("alert.news_integration", "false") != "true":
        for a in alerts:
            a["related_news"] = []
        return alerts

    seen_codes: set = set()
    news_cache: dict = {}
    for a in alerts:
        if a.get("severity") not in ("danger", "warning"):
            a["related_news"] = []
            continue
        code = a.get("related_fund_code") or ""
        if not code:
            a["related_news"] = []
            continue
        if code not in seen_codes:
            seen_codes.add(code)
            news_cache[code] = get_alert_news(code, a.get("related_fund_name") or "")
        a["related_news"] = news_cache.get(code, [])
    return alerts
