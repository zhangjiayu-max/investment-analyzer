"""工具定义与执行器 — 供 Orchestrator Agent 通过 function calling 调用"""

import json
import logging
import re
import html as html_mod
import requests as req

from db import (
    list_valuation_indexes,
    get_latest_valuation,
    get_valuation_history,
    search_indexes_by_keyword,
    list_holdings,
    get_portfolio_summary,
    list_transactions,
    refresh_holding_price,
    refresh_all_fund_prices,
    lookup_fund_info,
    get_fund_holdings,
)

logger = logging.getLogger(__name__)

# ── Tool Schema（OpenAI function calling 格式）──────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_valuation",
            "description": "查询指定指数的估值数据（PE、PB、股息率、百分位、z-score 等）。当用户问到某个指数估值高低、是否值得投资时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index_name": {
                        "type": "string",
                        "description": "指数名称或关键词，如'沪深300'、'白酒'、'半导体'",
                    },
                },
                "required": ["index_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "从知识库中检索相关文章、分析记录、个人文档。当需要引用专业观点、查找历史分析、检索文档内容时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或问题",
                    },
                    "content_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "article",
                                "author_article",
                                "valuation",
                                "analysis",
                                "skill",
                                "linked_doc",
                            ],
                        },
                        "description": "限定搜索范围，为空则搜索全部",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bond_temperature",
            "description": "获取当前债市温度（有知有行债市温度计）。债市温度低说明债市有投资价值，高说明债市偏贵。用于判断股债配置比例。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_valuation_list",
            "description": "获取所有已录入指数的估值概览。可筛选低估/高估指数，用于回答'现在有什么可以买'、'哪些指数高估了'等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "enum": ["all", "undervalued", "overvalued"],
                        "description": "筛选：all=全部, undervalued=低估(百分位<30), overvalued=高估(百分位>70)",
                        "default": "all",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_author_opinions",
            "description": "获取特定作者的投资观点文章。当用户想了解某位专家对特定话题的看法时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "author": {
                        "type": "string",
                        "description": "作者名称，如'研究员雷牛牛'",
                    },
                    "topic": {
                        "type": "string",
                        "description": "话题关键词，如'银行股'、'定投策略'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回文章数量，默认 3",
                        "default": 3,
                    },
                },
                "required": ["author"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_metrics",
            "description": "计算投资指标。当用户问到收益率、回撤、定投效果等需要计算的问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_type": {
                        "type": "string",
                        "enum": [
                            "dca_return",
                            "annualized_return",
                            "max_drawdown",
                            "risk_level",
                        ],
                        "description": "计算类型：dca_return=定投收益率, annualized_return=年化收益, max_drawdown=最大回撤, risk_level=风险等级评估",
                    },
                    "index_name": {
                        "type": "string",
                        "description": "指数名称或关键词",
                    },
                    "period": {
                        "type": "string",
                        "description": "时间范围，如'1年'、'3年'、'5年'，默认3年",
                        "default": "3年",
                    },
                },
                "required": ["metric_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "获取最新财经新闻和市场资讯（东方财富、央视新闻等）。当需要了解最新市场动态、政策变化、行业新闻时调用。数据源：akshare 财经新闻接口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如'A股 最新政策'、'央行降息 2026'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_portfolio",
            "description": "查询用户当前持仓信息，包括持仓基金、金额、盈亏等。当用户问到持仓、加减仓、盈亏相关问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["summary", "detail", "by_index", "refresh"],
                        "description": "查询类型：summary=汇总概览, detail=持仓详情, by_index=按指数查询, refresh=刷新最新净值后返回详情",
                    },
                    "index_name": {
                        "type": "string",
                        "description": "指数名称（当 query_type=by_index 时使用）",
                    },
                },
                "required": ["query_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_fund_info",
            "description": "查询基金详细信息，包括基本信息（名称、类型、跟踪标的）、持仓详情（重仓股票、债券持仓及类型）、资产配置。当用户问到某只基金的持仓、重仓股、债券类型等问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_code": {
                        "type": "string",
                        "description": "基金代码，如 '161725'",
                    },
                    "detail_type": {
                        "type": "string",
                        "enum": ["basic", "holdings", "all"],
                        "description": "查询类型：basic=基本信息, holdings=持仓详情（含重仓股、债券、资产配置）, all=全部",
                        "default": "all",
                    },
                },
                "required": ["fund_code"],
            },
        },
    },
]


# ── Tool 执行器 ──────────────────────────────────────


def execute_tool(name: str, arguments: dict) -> str:
    """执行工具调用，返回 JSON 字符串结果。"""
    try:
        if name == "query_valuation":
            return _query_valuation(arguments)
        elif name == "search_knowledge":
            return _search_knowledge(arguments)
        elif name == "get_bond_temperature":
            return _get_bond_temperature()
        elif name == "get_valuation_list":
            return _get_valuation_list(arguments)
        elif name == "get_author_opinions":
            return _get_author_opinions(arguments)
        elif name == "calculate_metrics":
            return _calculate_metrics(arguments)
        elif name == "web_search":
            return _web_search(arguments)
        elif name == "query_portfolio":
            return _query_portfolio(arguments)
        elif name == "query_fund_info":
            return _query_fund_info(arguments)
        else:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"工具执行异常 [{name}]: {e}")
        return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)


# ── 各工具实现 ──────────────────────────────────────


def _query_valuation(args: dict) -> str:
    """查询指定指数的估值数据。"""
    index_name = args.get("index_name", "")

    # 先用关键词搜索匹配的指数
    all_indexes = list_valuation_indexes()
    unique_indexes = {}
    for idx in all_indexes:
        code = idx["index_code"]
        if code not in unique_indexes:
            unique_indexes[code] = idx["index_name"]

    # 匹配逻辑（复用 app.py 的前缀剥离策略）
    _prefixes = ("中证", "国证", "沪", "深", "恒生")
    _middles = ("全指", "综指", "50", "100", "200", "300", "500", "800", "1000")
    matched = []
    seen_codes = set()

    for code, name in unique_indexes.items():
        if code in seen_codes:
            continue
        if name in index_name or index_name in name:
            seen_codes.add(code)
            matched.append({"code": code, "name": name})
            continue
        for prefix in _prefixes:
            core = name.replace(prefix, "", 1)
            if len(core) >= 2 and (core in index_name or index_name in core):
                seen_codes.add(code)
                matched.append({"code": code, "name": name})
                break

    if not matched:
        # 尝试数据库关键词搜索
        db_results = search_indexes_by_keyword(index_name)
        for r in db_results:
            if r["index_code"] not in seen_codes:
                seen_codes.add(r["index_code"])
                matched.append({"code": r["index_code"], "name": r["index_name"]})

    if not matched:
        return json.dumps({"error": f"未找到'{index_name}'相关的指数数据"}, ensure_ascii=False)

    # 查询每个匹配指数的详细估值
    results = []
    for m in matched[:3]:  # 最多返回 3 个指数
        code, name = m["code"], m["name"]
        index_metrics = [i for i in all_indexes if i["index_code"] == code]
        metrics = []

        for metric in index_metrics:
            mt = metric["metric_type"]
            latest = get_latest_valuation(code, mt)
            if not latest:
                continue

            entry = {
                "metric_type": mt,
                "current_value": latest.get("current_value"),
                "percentile": latest.get("percentile"),
                "danger_value": latest.get("danger_value"),
                "opportunity_value": latest.get("opportunity_value"),
                "zscore": latest.get("zscore"),
                "date": latest.get("snapshot_date"),
            }

            # 判断估值水平
            pct = latest.get("percentile")
            if pct is not None:
                if pct < 30:
                    entry["level"] = "低估"
                elif pct < 70:
                    entry["level"] = "合理"
                else:
                    entry["level"] = "高估"

            # 近 5 日趋势
            history = get_valuation_history(code, 5, mt)
            if history:
                entry["trend"] = [h["current_value"] for h in reversed(history)]

            metrics.append(entry)

        results.append({"index_code": code, "index_name": name, "metrics": metrics})

    return json.dumps(results, ensure_ascii=False)


def _search_knowledge(args: dict) -> str:
    """从知识库中检索相关内容。"""
    from rag import build_rag_context_with_details

    query = args.get("query", "")
    content_types = args.get("content_types")
    limit = args.get("limit", 5)

    result = build_rag_context_with_details(query, content_types=content_types, limit=limit)

    # 精简返回（去掉过长的 body）
    slim_results = []
    for r in result.get("results", []):
        slim_results.append({
            "content_type": r.get("content_type"),
            "title": r.get("title"),
            "body_preview": r.get("body", "")[:300],
            "score": round(r.get("_score", 0), 3),
        })

    return json.dumps({
        "results": slim_results,
        "total": len(slim_results),
        "keywords": result.get("keywords", []),
    }, ensure_ascii=False)


def _get_bond_temperature() -> str:
    """获取债市温度数据。"""
    try:
        resp = req.get(
            "https://youzhiyouxing.cn/data/macro",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        return json.dumps({"error": f"债市数据获取失败: {e}"}, ensure_ascii=False)

    match = re.search(r'data-cbond-history="([^"]+)"', resp.text)
    if not match:
        return json.dumps({"error": "页面结构变化，未找到数据"}, ensure_ascii=False)

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

    try:
        data = json.loads(raw[:end_idx])
    except json.JSONDecodeError:
        return json.dumps({"error": "数据解析失败"}, ensure_ascii=False)

    last = data[-1] if data else {}
    temperature = last.get("degree")
    rate = float(last["yield"]) if last.get("yield") else None

    # 判断债市冷热
    level = ""
    if temperature is not None:
        if temperature < 30:
            level = "偏冷（债市有投资价值）"
        elif temperature < 70:
            level = "适中"
        else:
            level = "偏热（债市偏贵，谨慎配置）"

    return json.dumps({
        "temperature": temperature,
        "rate": rate,
        "level": level,
        "date": last.get("date"),
        "recent_5": [
            {"date": d.get("date"), "temp": d.get("degree"), "rate": float(d["yield"]) if d.get("yield") else None}
            for d in data[-5:]
        ],
    }, ensure_ascii=False)


def _get_valuation_list(args: dict) -> str:
    """获取所有指数估值概览。"""
    filter_type = args.get("filter", "all")

    all_indexes = list_valuation_indexes()
    unique_indexes = {}
    for idx in all_indexes:
        code = idx["index_code"]
        if code not in unique_indexes:
            unique_indexes[code] = idx["index_name"]

    results = []
    for code, name in unique_indexes.items():
        # 取 PE 或第一个指标的最新数据
        latest = get_latest_valuation(code, "pe") or get_latest_valuation(code)
        if not latest:
            continue

        pct = latest.get("percentile")
        if pct is None:
            continue

        level = "低估" if pct < 30 else ("合理" if pct < 70 else "高估")

        if filter_type == "undervalued" and pct >= 30:
            continue
        if filter_type == "overvalued" and pct <= 70:
            continue

        results.append({
            "index_code": code,
            "index_name": name,
            "current_value": latest.get("current_value"),
            "percentile": pct,
            "level": level,
            "metric_type": latest.get("metric_type"),
            "date": latest.get("snapshot_date"),
        })

    # 按百分位排序（低估的排前面）
    results.sort(key=lambda x: x.get("percentile", 50))

    return json.dumps({
        "count": len(results),
        "filter": filter_type,
        "indexes": results,
    }, ensure_ascii=False)


def _get_author_opinions(args: dict) -> str:
    """获取作者投资观点文章。"""
    from db import _get_conn

    author = args.get("author", "")
    topic = args.get("topic", "")
    limit = args.get("limit", 3)

    conn = _get_conn()
    conn.row_factory = __import__("sqlite3").Row

    if topic:
        # FTS 搜索（如果 author_articles 已建 FTS 索引）
        try:
            from rag import _tokenize
            tokenized_topic = _tokenize(topic)
            rows = conn.execute("""
                SELECT aa.id, aa.title, aa.author, aa.publish_time, aa.summary, aa.content_text
                FROM author_articles aa
                WHERE aa.author LIKE ? AND aa.status = 'done'
                AND (aa.title LIKE ? OR aa.content_text LIKE ?)
                ORDER BY aa.publish_time DESC
                LIMIT ?
            """, (f"%{author}%", f"%{topic}%", f"%{topic}%", limit)).fetchall()
        except Exception:
            rows = conn.execute("""
                SELECT id, title, author, publish_time, summary, content_text
                FROM author_articles
                WHERE author LIKE ? AND status = 'done'
                AND (title LIKE ? OR content_text LIKE ?)
                ORDER BY publish_time DESC
                LIMIT ?
            """, (f"%{author}%", f"%{topic}%", f"%{topic}%", limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, title, author, publish_time, summary, content_text
            FROM author_articles
            WHERE author LIKE ? AND status = 'done'
            ORDER BY publish_time DESC
            LIMIT ?
        """, (f"%{author}%", limit)).fetchall()

    conn.close()

    results = []
    for r in rows:
        r = dict(r)
        content = r.get("content_text", "")
        results.append({
            "id": r["id"],
            "title": r["title"],
            "author": r.get("author"),
            "publish_time": r.get("publish_time"),
            "summary": r.get("summary", ""),
            "content_preview": content[:500] if content else "",
        })

    return json.dumps({
        "count": len(results),
        "author": author,
        "topic": topic or "全部",
        "articles": results,
    }, ensure_ascii=False)


def _calculate_metrics(args: dict) -> str:
    """计算投资指标（基于估值历史数据的简化计算）。"""
    metric_type = args.get("metric_type", "")
    index_name = args.get("index_name", "")
    period = args.get("period", "3年")

    if not index_name:
        return json.dumps({"error": "请指定指数名称"}, ensure_ascii=False)

    # 匹配指数
    all_indexes = list_valuation_indexes()
    unique_indexes = {}
    for idx in all_indexes:
        code = idx["index_code"]
        if code not in unique_indexes:
            unique_indexes[code] = idx["index_name"]

    matched_code = None
    matched_name = None
    for code, name in unique_indexes.items():
        if name in index_name or index_name in name:
            matched_code, matched_name = code, name
            break
        for prefix in ("中证", "国证", "沪", "深"):
            core = name.replace(prefix, "", 1)
            if len(core) >= 2 and (core in index_name or index_name in core):
                matched_code, matched_name = code, name
                break
        if matched_code:
            break

    if not matched_code:
        return json.dumps({"error": f"未找到'{index_name}'相关指数"}, ensure_ascii=False)

    # 获取历史数据
    days_map = {"1年": 365, "2年": 730, "3年": 1095, "5年": 1825}
    days = days_map.get(period, 1095)
    history = get_valuation_history(matched_code, days)

    if not history or len(history) < 10:
        return json.dumps({
            "error": f"{matched_name}历史数据不足（{len(history) if history else 0}条），无法计算",
        }, ensure_ascii=False)

    values = [h["current_value"] for h in history if h.get("current_value") is not None]
    if len(values) < 10:
        return json.dumps({"error": "有效数据点不足"}, ensure_ascii=False)

    result = {"index_name": matched_name, "period": period, "data_points": len(values)}

    if metric_type == "max_drawdown":
        # 最大回撤
        peak = values[0]
        max_dd = 0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        result["max_drawdown"] = round(max_dd * 100, 2)
        result["unit"] = "%"
        result["description"] = f"{matched_name}近{period}最大回撤为 {result['max_drawdown']}%"

    elif metric_type == "annualized_return":
        # 年化收益率（基于估值变化）
        first_val = values[0]
        last_val = values[-1]
        years = len(values) / 250  # 约 250 个交易日/年
        if first_val > 0 and years > 0:
            total_return = (last_val / first_val) - 1
            annualized = (1 + total_return) ** (1 / years) - 1
            result["total_return"] = round(total_return * 100, 2)
            result["annualized_return"] = round(annualized * 100, 2)
            result["unit"] = "%"
            result["description"] = (
                f"{matched_name}近{period}总收益 {result['total_return']}%，"
                f"年化 {result['annualized_return']}%"
            )
        else:
            result["error"] = "数据异常，无法计算"

    elif metric_type == "dca_return":
        # 定投收益率模拟（等额定投）
        n = len(values)
        monthly_interval = max(1, n // (int(period[0]) * 12)) if period[0].isdigit() else n // 36
        total_invested = 0
        total_shares = 0
        for i in range(0, n, monthly_interval):
            total_invested += 1000  # 每期定投 1000
            total_shares += 1000 / values[i] if values[i] > 0 else 0

        if total_shares > 0:
            current_value = total_shares * values[-1]
            dca_return = (current_value - total_invested) / total_invested * 100
            result["total_invested"] = total_invested
            result["current_value"] = round(current_value, 2)
            result["dca_return"] = round(dca_return, 2)
            result["unit"] = "%"
            result["description"] = (
                f"{matched_name}近{period}定投模拟：投入¥{total_invested}，"
                f"当前市值¥{result['current_value']}，收益率 {result['dca_return']}%"
            )

    elif metric_type == "risk_level":
        # 风险等级评估
        import statistics
        mean_val = statistics.mean(values)
        std_val = statistics.stdev(values) if len(values) > 1 else 0
        cv = std_val / mean_val if mean_val > 0 else 0  # 变异系数

        latest_val = values[-1]
        pct_from_mean = (latest_val - mean_val) / mean_val * 100 if mean_val > 0 else 0

        # 获取百分位
        latest_pct = None
        for mt in ["pe", "pb"]:
            lv = get_latest_valuation(matched_code, mt)
            if lv and lv.get("percentile") is not None:
                latest_pct = lv["percentile"]
                break

        if cv < 0.15:
            risk = "低风险"
        elif cv < 0.30:
            risk = "中等风险"
        else:
            risk = "高风险"

        result["risk_level"] = risk
        result["coefficient_of_variation"] = round(cv, 4)
        result["current_vs_mean"] = round(pct_from_mean, 2)
        result["percentile"] = latest_pct
        result["description"] = (
            f"{matched_name}风险等级：{risk}（变异系数{cv:.3f}），"
            f"当前值偏离均值 {pct_from_mean:+.1f}%"
        )

    return json.dumps(result, ensure_ascii=False)


def _web_search(args: dict) -> str:
    """获取最新财经新闻和市场信息。优先用 akshare（稳定），备用搜狗搜索。"""
    query = args.get("query", "")
    max_results = args.get("max_results", 5)

    if not query:
        return json.dumps({"error": "搜索关键词不能为空"}, ensure_ascii=False)

    results = []

    # 优先：akshare 财经新闻（稳定可靠）
    try:
        import akshare as ak

        # 东方财富财经新闻
        try:
            df = ak.stock_news_em(symbol="A股")
            if df is not None and len(df) > 0:
                for _, row in df.head(max_results).iterrows():
                    results.append({
                        "title": str(row.get("新闻标题", "")),
                        "snippet": str(row.get("新闻内容", ""))[:200] if row.get("新闻内容") else "",
                        "url": str(row.get("新闻链接", "")),
                        "time": str(row.get("发布时间", "")),
                        "source": "东方财富",
                    })
        except Exception:
            pass

        # 如果结果不够，补充央视新闻
        if len(results) < max_results:
            try:
                from datetime import datetime
                today = datetime.now().strftime("%Y%m%d")
                df2 = ak.news_cctv(date=today)
                if df2 is not None and len(df2) > 0:
                    for _, row in df2.head(max_results - len(results)).iterrows():
                        title = str(row.get("title", ""))
                        # 过滤掉与金融无关的新闻
                        if any(kw in title for kw in ["股", "基金", "央行", "利率", "经济", "金融", "市场", "投资", "GDP", "通胀"]):
                            results.append({
                                "title": title,
                                "snippet": str(row.get("content", ""))[:200] if row.get("content") else "",
                                "url": "",
                                "time": str(row.get("date", "")),
                                "source": "央视新闻",
                            })
            except Exception:
                pass

    except ImportError:
        logger.warning("akshare 未安装，跳过财经新闻获取")

    # 备用：搜狗搜索（可能被反爬）
    if len(results) < 2:
        try:
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            }
            resp = req.get(
                "https://www.sogou.com/web",
                params={"query": query},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            for h3 in soup.select("h3"):
                if len(results) >= max_results:
                    break
                a = h3.select_one("a")
                if not a:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 5:
                    continue
                parent = h3.parent
                snippet = ""
                if parent:
                    for sel in ["p", ".str_info", ".text-layout"]:
                        el = parent.select_one(sel)
                        if el:
                            snippet = el.get_text(strip=True)[:200]
                            break
                results.append({
                    "title": title[:100],
                    "snippet": snippet,
                    "url": a.get("href", ""),
                    "source": "搜狗搜索",
                })
        except Exception:
            pass

    if not results:
        return json.dumps({
            "query": query,
            "count": 0,
            "results": [],
            "note": "未找到相关新闻，请基于知识库和估值数据回答。",
        }, ensure_ascii=False)

    return json.dumps({
        "query": query,
        "count": len(results[:max_results]),
        "results": results[:max_results],
    }, ensure_ascii=False)


def _auto_refresh_if_stale(holdings: list) -> list:
    """如果持仓净值数据超过 1 天未更新，自动刷新。"""
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    stale_ids = []
    for h in holdings:
        updated = h.get("price_updated_at", "")
        if not updated or updated < yesterday:
            stale_ids.append(h["id"])

    if stale_ids:
        logger.info(f"[portfolio] 自动刷新 {len(stale_ids)} 个过期持仓净值")
        for hid in stale_ids:
            try:
                refresh_holding_price(hid)
            except Exception as e:
                logger.warning(f"[portfolio] 刷新持仓 {hid} 失败: {e}")
        # 重新获取更新后的数据
        return list_holdings()

    return holdings


def _query_portfolio(args: dict) -> str:
    """查询用户持仓信息。"""
    query_type = args.get("query_type", "summary")

    if query_type == "summary":
        # 先自动刷新过期数据
        holdings = list_holdings()
        holdings = _auto_refresh_if_stale(holdings)
        summary = get_portfolio_summary()
        return json.dumps(summary, ensure_ascii=False)

    elif query_type == "detail":
        holdings = list_holdings()
        holdings = _auto_refresh_if_stale(holdings)
        return json.dumps({
            "holding_count": len(holdings),
            "holdings": holdings,
        }, ensure_ascii=False)

    elif query_type == "by_index":
        index_name = args.get("index_name", "")
        holdings = list_holdings()
        holdings = _auto_refresh_if_stale(holdings)
        matched = [
            h for h in holdings
            if index_name in (h.get("index_name") or "")
            or index_name in (h.get("fund_name") or "")
            or (h.get("index_name") or "") in index_name
        ]
        return json.dumps({
            "index_name": index_name,
            "matched_count": len(matched),
            "holdings": matched,
        }, ensure_ascii=False)

    elif query_type == "refresh":
        results = refresh_all_fund_prices()
        holdings = list_holdings()
        return json.dumps({
            "refreshed": results,
            "holding_count": len(holdings),
            "holdings": holdings,
        }, ensure_ascii=False)

    return json.dumps({"error": f"未知查询类型: {query_type}"}, ensure_ascii=False)


def _query_fund_info(args: dict) -> str:
    """查询基金详细信息。"""
    fund_code = args.get("fund_code", "").strip()
    detail_type = args.get("detail_type", "all")

    if not fund_code:
        return json.dumps({"error": "请提供基金代码"}, ensure_ascii=False)

    result = {"fund_code": fund_code}

    # 基本信息
    if detail_type in ("basic", "all"):
        info = lookup_fund_info(fund_code)
        if info:
            result["basic_info"] = info
        else:
            result["basic_info_error"] = f"未找到基金 {fund_code} 的基本信息"

    # 持仓详情
    if detail_type in ("holdings", "all"):
        holdings = get_fund_holdings(fund_code)
        result["holdings"] = holdings

        # 生成简要分析
        analysis_parts = []
        if holdings.get("top_stocks"):
            top3 = holdings["top_stocks"][:3]
            names = "、".join(s["stock_name"] for s in top3)
            analysis_parts.append(f"重仓股：{names}")
        if holdings.get("asset_allocation"):
            for a in holdings["asset_allocation"]:
                analysis_parts.append(f"{a['type']}: {a['pct']}")
        if holdings.get("bond_type_summary"):
            bt = holdings["bond_type_summary"]
            if bt:
                bt_str = "、".join(f"{k}{v}%" for k, v in bt.items())
                analysis_parts.append(f"债券类型：{bt_str}")
        result["brief_analysis"] = "；".join(analysis_parts)

    return json.dumps(result, ensure_ascii=False)
