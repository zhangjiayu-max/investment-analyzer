"""分析模块共享辅助函数。

从 portfolio.py 提取，供 panorama / deep_dive / trade_review / what_if / portfolio_ai 等复用。
"""
import logging
from db import (
    list_valuation_indexes, get_latest_valuation, list_index_freshness,
    lookup_fund_info, search_indexes_by_keyword,
)

logger = logging.getLogger(__name__)


def _get_valuation_context() -> str:
    """获取当前所有持仓的估值数据摘要。"""
    try:
        indexes = list_valuation_indexes()
        lines = []

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
                    except Exception:
                        pass
                lines.append(f"- {idx.get('index_name','')}({idx.get('index_code','')}): PE分位 {pe_percentile}, PB分位 {pb_percentile} [{date_str}]{stale_mark}")
        if lines:
            return "## 估值参考（跟踪指数）\n" + "\n".join(lines)
        return "## 估值参考\n暂无估值数据"
    except Exception as e:
        return f"## 估值参考\n获取失败: {e}"


def _get_valuation_context_for_fund(fund_code: str, fund_name: str) -> str:
    """获取指定基金的估值数据。"""
    try:
        fund_info = lookup_fund_info(fund_code)
        if not fund_info:
            return f"\n## 目标基金估值\n基金 {fund_name}({fund_code}) 暂无跟踪指数信息"

        index_code = fund_info.get("index_code", "")
        index_name = fund_info.get("index_name", "")

        if not index_code or not index_name:
            return f"\n## 目标基金估值\n基金 {fund_name}({fund_code}) 未设置跟踪指数"

        val = get_latest_valuation(index_code)
        if val:
            pe_percentile = val.get("pe_percentile", "N/A")
            pb_percentile = val.get("pb_percentile", "N/A")
            date_str = val.get("snapshot_date", "")
            level = ""
            if pe_percentile != "N/A":
                try:
                    pct = float(pe_percentile)
                    level = "🔥高估" if pct > 80 else ("⚠️偏高" if pct > 50 else ("✅低估" if pct < 20 else "⚡适中"))
                except Exception:
                    pass
            return f"\n## 目标基金估值\n- {index_name}({index_code}): PE分位 {pe_percentile}, PB分位 {pb_percentile} [{date_str}] {level}"
        else:
            return f"\n## 目标基金估值\n{index_name}({index_code}): 暂无估值数据"
    except Exception as e:
        return f"\n## 目标基金估值\n获取失败: {e}"


def _fetch_valuation_fallback(index_name: str = "", fund_code: str = "") -> str | None:
    """估值数据兜底：本地 DB 没有时，依次尝试盈米 MCP → 天天基金 API。"""
    if fund_code:
        try:
            from mcp.yingmi_client import get_yingmi_client
            ym = get_yingmi_client()
            diag = ym.get_fund_diagnosis(fund_code)
            if diag and isinstance(diag, str) and len(diag) > 30:
                try:
                    import json as _json
                    d = _json.loads(diag)
                    summary = d.get("diagnoseSummary", {}).get("data", {})
                    parts = []
                    risk_opp = summary.get("riskOpp", {})
                    val = risk_opp.get("valuation", "")
                    if val:
                        parts.append(f"估值: {val}")
                    win = summary.get("winProb", {}).get("winRate", "")
                    if win:
                        parts.append(f"胜率: {win}")
                    thermo = summary.get("thermometer", {})
                    temp = thermo.get("currentTemperature", "")
                    if temp:
                        parts.append(f"市场温度: {temp}°C")
                    risk_info = summary.get("riskLevelInfo", {})
                    risk = risk_info.get("fundRiskLevel", "")
                    if risk:
                        parts.append(f"风险等级: {risk}")
                    if parts:
                        return f"[盈米] " + ", ".join(parts)
                except _json.JSONDecodeError:
                    pass
                return f"[盈米诊断] {diag[:600]}"
        except Exception as e:
            logger.warning(f"盈米 MCP 兜底失败 {fund_code}: {e}")

    if index_name:
        try:
            from mcp.ttfund_client import get_ttfund_client
            client = get_ttfund_client()
            raw = client._invoke("fund_index", {"index_id": index_name, "query_scope": "valuation"})
            if isinstance(raw, dict) and raw.get("success"):
                data = raw.get("data", {})
                v = data.get("valuation", {})
                profile = data.get("index_profile", {})
                if v:
                    parts = []
                    pe = v.get("pe_ttm")
                    pe_pct = v.get("pe_percentile_10y")
                    if pe is not None:
                        parts.append(f"PE={pe:.2f} (10年{pe_pct:.0f}%分位)" if pe_pct else f"PE={pe:.2f}")
                    pb = v.get("pb")
                    pb_pct = v.get("pb_percentile_10y")
                    if pb is not None:
                        parts.append(f"PB={pb:.2f} (10年{pb_pct:.0f}%分位)" if pb_pct else f"PB={pb:.2f}")
                    roe = v.get("roe")
                    if roe:
                        parts.append(f"ROE={roe:.1f}%")
                    idx_name = profile.get("index_name", index_name)
                    if parts:
                        return f"[天天基金] {idx_name}: " + ", ".join(parts)
        except Exception as e:
            logger.warning(f"天天基金兜底失败 {index_name}: {e}")

    return None


def _get_holdings_valuation_context(holdings: list) -> str:
    """按持仓匹配估值数据。"""
    from db.valuations import search_indexes_by_keyword, get_latest_valuation
    lines = []
    matched = 0
    for h in holdings:
        idx_name = (h.get("index_name") or "").strip()
        fund_name = h.get("fund_name", "")
        if not idx_name or idx_name == "该基金无跟踪标的":
            lines.append(f"- {fund_name}: 无跟踪指数，估值数据不可用")
            continue
        found = False
        search_term = idx_name.replace("指数", "").replace("中证", "").replace("全指", "")
        try:
            matches = search_indexes_by_keyword(search_term)
            for m in matches:
                val = get_latest_valuation(m["index_code"])
                if val and val.get("percentile") is not None:
                    pct = val["percentile"]
                    current_val = val.get("current_value")
                    metric = val.get("metric_type", "PE")
                    level = "🔥高估" if pct > 80 else ("⚠️偏高" if pct > 50 else ("✅低估" if pct < 20 else "⚡适中"))
                    profit_info = ""
                    pr = h.get("profit_rate")
                    if pr is not None:
                        profit_info = f", 盈亏{pr:+.1%}"
                    lines.append(f"- {fund_name} → {m['index_name']}: {metric}={current_val}, 百分位={pct:.0f}% {level}{profit_info}")
                    matched += 1
                    found = True
                    break
        except Exception:
            pass
        if not found:
            fallback = _fetch_valuation_fallback(index_name=idx_name, fund_code=h.get("fund_code", ""))
            if fallback:
                lines.append(f"- {fund_name}（{idx_name}）: {fallback}")
                matched += 1
            else:
                lines.append(f"- {fund_name}（{idx_name}）: 估值数据未匹配到")

    header = f"## 持仓估值匹配（已匹配 {matched}/{len([h for h in holdings if (h.get('shares') or 0) > 0])} 只）"
    if not lines:
        return header + "\n暂无估值数据"
    return header + "\n" + "\n".join(lines)


def _format_news_section(mcp_context: dict) -> str:
    """从 mcp_context 中提取新闻数据并格式化为 Markdown 段落。"""
    parts = []

    quotations = mcp_context.get("market_quotations", "")
    if quotations and not quotations.startswith("获取失败") and not quotations.startswith("调用失败"):
        parts.append(f"### 市场行情\n{quotations[:1500]}")

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

    akshare = mcp_context.get("akshare_news", [])
    if isinstance(akshare, list) and akshare:
        news_lines = []
        for item in akshare:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            time_val = item.get("time", "")
            source = item.get("source", "")
            if title:
                news_lines.append(f"- **{title}**  {snippet[:120]}")
        if news_lines:
            parts.append("### 实时财经新闻\n" + "\n".join(news_lines[:8]))

    hot_topics = mcp_context.get("hot_topics", "")
    if hot_topics and not hot_topics.startswith("获取失败") and not hot_topics.startswith("调用失败"):
        if not news_map:
            parts.append(f"### 市场热点\n{hot_topics[:1500]}")

    if not parts:
        return "## 新闻热点\n暂无最新新闻数据。"

    return "## 新闻热点\n" + "\n\n".join(parts)


def _add_akshare_news(mcp_context: dict, max_results: int = 8):
    """用 akshare 补充实时财经新闻。"""
    try:
        import akshare as ak
        news_items = []
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

        try:
            raw = mcp.call_tool_text("GetFundAssetClassAnalysis", {
                "holdingList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
            })
            mcp_context["asset_class_analysis"] = raw
        except Exception as e:
            mcp_context["asset_class_analysis"] = f"调用失败: {e}"

        try:
            raw = mcp.call_tool_text("GetFundsCorrelation", {
                "fundList": [{"fundCode": c} for c in fund_codes]
            })
            mcp_context["correlation"] = raw
        except Exception as e:
            mcp_context["correlation"] = f"调用失败: {e}"

        try:
            raw = mcp.call_tool_text("DiagnoseFundPortfolio", {
                "fundList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
            })
            mcp_context["portfolio_diagnosis"] = raw
        except Exception as e:
            mcp_context["portfolio_diagnosis"] = f"调用失败: {e}"

        try:
            mcp_context["market_quotations"] = mcp.get_latest_quotations()
        except Exception as e:
            mcp_context["market_quotations"] = f"获取失败: {e}"

        try:
            news_map = {}
            for kw, label in [("A股 热门 板块 基金", "market_hot"), ("市场热点 行情", "market_trend")]:
                try:
                    news_map[label] = mcp.search_news(kw, 5)
                except Exception:
                    pass

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

            try:
                mcp_context["hot_topics"] = mcp.call_tool_text("SearchHotTopic", {"keyword": "市场热点 热门基金"})
            except Exception:
                try:
                    mcp_context["hot_topics"] = mcp.call_tool_text("SearchHotTopic", {})
                except Exception as e:
                    mcp_context["hot_topics"] = f"获取失败: {e}"

        except Exception as e:
            mcp_context["news"] = f"获取失败: {e}"

        _add_akshare_news(mcp_context)

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


def _get_fund_mcp_diagnosis(fund_code: str) -> str:
    """获取单只基金的 MCP 诊断数据。"""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        diagnosis = mcp.call_tool_text("GetFundDiagnosis", {"fundCode": fund_code})
        return diagnosis or "MCP 诊断不可用"
    except ImportError:
        return "MCP 客户端未配置"
    except Exception as e:
        return f"MCP 诊断获取失败: {e}"


def _parse_mcp_pct_pairs(text: str) -> list[tuple[str, float]]:
    """解析 MCP 文本中的百分比分配。"""
    import re
    pairs = []
    for m in re.finditer(r'([^\d:,]+?)\s*[:：]\s*([\d.]+)%?', text):
        pairs.append((m.group(1).strip(), float(m.group(2))))
    return pairs


def _parse_mcp_correlation(text: str) -> list[dict]:
    """解析 MCP 相关性文本。"""
    import re
    items = []
    for m in re.finditer(r'(\S+?)\s*[-—]\s*(\S+?)\s*[:：]\s*([\d.]+)', text):
        items.append({"fund_a": m.group(1), "fund_b": m.group(2), "correlation": float(m.group(3))})
    return items


# ── R-2（2026-07-23）：分析路由统一 RAG 注入入口 ──

def inject_rag_context(
    base_query: str,
    extra_keywords: str = "",
    limit: int = 5,
    max_chars: int = 1500,
    caller: str = "",
) -> str:
    """分析路由统一 RAG 注入入口。

    所有分析路由（panorama/portfolio_ai/deep_dive/index_analysis/market_intel/diversification）
    必须通过本函数注入蒸馏知识，避免手写调用导致漏写（如 diversification 死代码 bug）。

    Args:
        base_query: 基础检索词（如"持仓分散度 集中度"）
        extra_keywords: 附加关键词（如基金名、指数名）
        limit: 最大返回条数
        max_chars: 注入上下文最大字符数
        caller: 调用方标识，用于日志追踪
    Returns:
        RAG 上下文文本（含 ## 知识库参考 标题），空则返回空字符串
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("rag.analysis_rag_injection_enabled", True):
            return ""
    except Exception:
        pass

    try:
        from services.rag import build_rag_context_with_details, log_rag_search
        rag_query = f"{base_query} {extra_keywords}".strip()
        rag_result = build_rag_context_with_details(query=rag_query, limit=limit)
        context = rag_result.get("context", "")
        if context:
            try:
                log_rag_search(
                    conversation_id=0, message_id=0, query=rag_query,
                    keywords=rag_result.get("keywords", []),
                    results=rag_result.get("results", []),
                    fts_count=rag_result.get("fts_count", 0),
                    chroma_count=rag_result.get("chroma_count", 0),
                    freshness_filtered=rag_result.get("freshness_filtered", 0),
                )
            except Exception:
                pass
            return f"\n\n## 知识库参考（蒸馏书籍）\n{context[:max_chars]}"
        return ""
    except Exception as e:
        logger.warning(f"[{caller}] RAG 检索失败: {e}")
        return ""
