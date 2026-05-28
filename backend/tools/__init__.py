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
    get_portfolio_diversification,
    get_transaction_summary,
    get_holding,
    create_alert,
    get_transaction_tags,
    add_transaction_tag,
    remove_transaction_tag,
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
    {
        "type": "function",
        "function": {
            "name": "analyze_holding_performance",
            "description": "分析单只持仓基金的投资表现，包括累计盈亏、收益率、持有时间、交易频率、操作质量评估。当用户问到某只基金赚了还是亏了、操作怎么样时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_code": {
                        "type": "string",
                        "description": "基金代码，如 '161725'",
                    },
                    "holding_id": {
                        "type": "integer",
                        "description": "持仓ID（可选，传 fund_code 可替代）",
                    },
                },
                "required": ["fund_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_transaction_history",
            "description": "查询交易记录并附带分析，用于基金操作复盘。当用户问到操作记录、买入卖出记录、交易历史时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fund_code": {
                        "type": "string",
                        "description": "基金代码，如 '161725'；为空则查全部",
                    },
                    "transaction_type": {
                        "type": "string",
                        "enum": ["buy", "sell", ""],
                        "description": "交易类型筛选：buy=买入, sell=卖出, 空=全部",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数",
                        "default": 20,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_portfolio_diversification",
            "description": "分析持仓分散度，包括基金数量、指数分布、基金类型分布、仓位集中度。当用户问到持仓是否分散、仓位集中度、配置均衡性时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_portfolio_alert",
            "description": "根据当前持仓状况、市场估值、新闻动态等，生成风险预警或加减仓提醒。当需要提醒用户注意持仓风险或加减仓机会时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "alert_type": {
                        "type": "string",
                        "enum": ["risk_warning", "add_position", "reduce_position", "news_impact", "valuation_alert"],
                        "description": "预警类型：risk_warning=风险警告, add_position=加仓提示, reduce_position=减仓提示, news_impact=新闻影响, valuation_alert=估值预警",
                    },
                    "fund_code": {
                        "type": "string",
                        "description": "关联的基金代码（可选）",
                    },
                    "reason": {
                        "type": "string",
                        "description": "触发预警的原因说明",
                    },
                },
                "required": ["alert_type", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bond_yield_curve",
            "description": "获取中国/美国国债收益率曲线数据，包括1Y/2Y/5Y/10Y/30Y各期限收益率及利差。用于分析利率走向、判断债市环境、久期管理参考。",
            "parameters": {
                "type": "object",
                "properties": {
                    "country": {
                        "type": "string",
                        "enum": ["china", "us", "both"],
                        "description": "选择数据范围：china=中国国债, us=美国国债, both=两者",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bond_market_overview",
            "description": "获取当前债市综合概况，包括债市温度、国债收益率曲线最新数据、收益率变化趋势。用于回答\"现在债市怎么样\"\"利率走势如何\"\"债券值得配置吗\"等宏观债市问题。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro_policy_data",
            "description": "获取当前中国宏观货币政策关键数据，包括LPR利率、存款准备金率(RRR)最新调整、SHIBOR各期限利率、CPI数据。用于分析货币政策松紧、判断利率环境、评估通胀/通缩压力，辅助债券配置决策。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
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
        elif name == "analyze_holding_performance":
            return _analyze_holding_performance(arguments)
        elif name == "query_transaction_history":
            return _query_transaction_history(arguments)
        elif name == "analyze_portfolio_diversification":
            return _analyze_portfolio_diversification(arguments)
        elif name == "generate_portfolio_alert":
            return _generate_portfolio_alert(arguments)
        elif name == "get_bond_yield_curve":
            return _get_bond_yield_curve(arguments)
        elif name == "get_bond_market_overview":
            return _get_bond_market_overview()
        elif name == "get_macro_policy_data":
            return _get_macro_policy_data()
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


def _analyze_holding_performance(args: dict) -> str:
    """分析单只持仓基金的投资表现。"""
    fund_code = args.get("fund_code", "").strip()
    holding_id = args.get("holding_id")

    if not fund_code and not holding_id:
        return json.dumps({"error": "请提供基金代码或持仓ID"}, ensure_ascii=False)

    holdings = list_holdings()
    target = None
    if holding_id:
        target = get_holding(holding_id)
    if not target:
        for h in holdings:
            if h.get("fund_code") == fund_code:
                target = h
                break

    if not target:
        return json.dumps({"error": f"未找到基金 {fund_code} 的持仓记录"}, ensure_ascii=False)

    fund_code = target["fund_code"]
    txs = list_transactions(fund_code=fund_code, limit=100)

    buy_txs = [t for t in txs if t["transaction_type"] == "buy"]
    sell_txs = [t for t in txs if t["transaction_type"] == "sell"]
    buy_total = sum(t.get("amount", 0) or 0 for t in buy_txs)
    sell_total = sum(t.get("amount", 0) or 0 for t in sell_txs)

    profit = target.get("profit_loss", 0) or 0
    profit_rate = target.get("profit_rate", 0) or 0
    shares = target.get("shares", 0) or 0
    cost_price = target.get("cost_price", 0) or 0
    current_price = target.get("current_price", 0) or 0

    buy_dates = [t.get("transaction_date", "") for t in buy_txs if t.get("transaction_date")]
    first_buy = min(buy_dates) if buy_dates else None

    result = {
        "fund_code": fund_code,
        "fund_name": target.get("fund_name", ""),
        "shares": shares,
        "cost_price": cost_price,
        "current_price": current_price,
        "total_cost": target.get("total_cost", 0),
        "current_value": target.get("current_value", 0),
        "profit_loss": round(profit, 2),
        "profit_rate": round(profit_rate * 100, 2),
        "buy_count": len(buy_txs),
        "sell_count": len(sell_txs),
        "buy_total": round(buy_total, 2),
        "sell_total": round(sell_total, 2),
        "first_buy_date": first_buy,
        "notes": target.get("notes", ""),
    }
    return json.dumps(result, ensure_ascii=False)


def _query_transaction_history(args: dict) -> str:
    """查询交易记录并附带分析。"""
    fund_code = args.get("fund_code", "").strip() or None
    tx_type = args.get("transaction_type", "") or None
    limit = args.get("limit", 50)

    txs = list_transactions(fund_code=fund_code, limit=limit)

    if tx_type:
        txs = [t for t in txs if t["transaction_type"] == tx_type]

    for t in txs:
        tags = get_transaction_tags(t["id"])
        t["tags"] = tags

    buy_count = sum(1 for t in txs if t["transaction_type"] == "buy")
    sell_count = sum(1 for t in txs if t["transaction_type"] == "sell")
    buy_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "buy")
    sell_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "sell")

    return json.dumps({
        "count": len(txs),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_total": round(buy_total, 2),
        "sell_total": round(sell_total, 2),
        "transactions": txs,
    }, ensure_ascii=False)


def _analyze_portfolio_diversification(args: dict) -> str:
    """分析持仓分散度。"""
    div = get_portfolio_diversification()
    tx_summary = get_transaction_summary()
    result = {**div, "transaction_summary": tx_summary}
    return json.dumps(result, ensure_ascii=False)


def _generate_portfolio_alert(args: dict) -> str:
    """生成风险预警。"""
    alert_type = args.get("alert_type", "risk_warning")
    reason = args.get("reason", "")
    fund_code = args.get("fund_code", "")

    fund_name = ""
    if fund_code:
        info = lookup_fund_info(fund_code)
        if info:
            fund_name = info.get("fund_name", "")

    type_labels = {
        "risk_warning": "风险警告",
        "add_position": "加仓提醒",
        "reduce_position": "减仓提醒",
        "news_impact": "新闻影响",
        "valuation_alert": "估值预警",
    }
    type_label = type_labels.get(alert_type, alert_type)
    title = f"{type_label}"
    if fund_name:
        title += f"：{fund_name}"

    severity_map = {
        "risk_warning": "danger",
        "add_position": "info",
        "reduce_position": "warning",
        "news_impact": "info",
        "valuation_alert": "warning",
    }
    severity = severity_map.get(alert_type, "info")

    alert_id = create_alert(
        alert_type=alert_type,
        title=title,
        content=reason,
        severity=severity,
        related_fund_code=fund_code or None,
        related_fund_name=fund_name or None,
        source="ai_analysis",
    )

    return json.dumps({
        "ok": True,
        "alert_id": alert_id,
        "title": title,
        "severity": severity,
    }, ensure_ascii=False)


# ── 债券市场工具 ──────────────────────────────────


def _get_bond_yield_curve(args: dict) -> str:
    """获取国债收益率曲线数据。"""
    country = args.get("country", "china")
    try:
        import akshare as ak
        df = ak.bond_zh_us_rate()
        if df.empty:
            return json.dumps({"error": "暂无收益率曲线数据"}, ensure_ascii=False)
        # 取最近 10 个交易日
        df = df.tail(10).copy()
        df = df.where(df.notna(), None)

        china_cols = ["中国国债收益率2年", "中国国债收益率5年", "中国国债收益率10年",
                      "中国国债收益率30年", "中国国债收益率10年-2年"]
        us_cols = ["美国国债收益率2年", "美国国债收益率5年", "美国国债收益率10年",
                   "美国国债收益率30年", "美国国债收益率10年-2年"]

        result = {"dates": df["日期"].tolist()}
        if country in ("china", "both"):
            china_data = {}
            for col in china_cols:
                if col in df.columns:
                    china_data[col] = df[col].tolist()
            result["china"] = china_data
        if country in ("us", "both"):
            us_data = {}
            for col in us_cols:
                if col in df.columns:
                    us_data[col] = df[col].tolist()
            result["us"] = us_data

        # 最新数据摘要
        latest = df.iloc[-1]
        summary = {}
        if country in ("china", "both"):
            summary["中国"] = {
                "2年": latest.get("中国国债收益率2年"),
                "5年": latest.get("中国国债收益率5年"),
                "10年": latest.get("中国国债收益率10年"),
                "30年": latest.get("中国国债收益率30年"),
                "10-2年利差": latest.get("中国国债收益率10年-2年"),
            }
        if country in ("us", "both"):
            summary["美国"] = {
                "2年": latest.get("美国国债收益率2年"),
                "5年": latest.get("美国国债收益率5年"),
                "10年": latest.get("美国国债收益率10年"),
                "30年": latest.get("美国国债收益率30年"),
                "10-2年利差": latest.get("美国国债收益率10年-2年"),
            }
        result["summary"] = summary

        # 趋势判断
        if country in ("china", "both") and len(df) >= 5:
            ch_10y = df["中国国债收益率10年"].tolist()
            ch_10y_valid = [v for v in ch_10y if v is not None]
            if len(ch_10y_valid) >= 5:
                trend = "下行" if ch_10y_valid[-1] < ch_10y_valid[0] - 0.05 else \
                        "上行" if ch_10y_valid[-1] > ch_10y_valid[0] + 0.05 else "平稳"
                result["china_trend"] = f"近5日中国10年期国债收益率{trend}"
                ch_spread = latest.get("中国国债收益率10年-2年")
                if ch_spread is not None:
                    if ch_spread < 0.3:
                        result["china_curve"] = "平坦（利差<0.3%，经济放缓预期）"
                    elif ch_spread < 0.7:
                        result["china_curve"] = "正常（利差0.3-0.7%）"
                    else:
                        result["china_curve"] = "陡峭（利差>0.7%，经济复苏预期）"

        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"获取收益率曲线失败: {e}")
        return json.dumps({"error": f"获取收益率曲线失败: {e}"}, ensure_ascii=False)


def _get_bond_market_overview() -> str:
    """获取当前债市综合概况。"""
    try:
        # 1. 债市温度
        temp_data = json.loads(_get_bond_temperature())

        # 2. 收益率曲线
        curve_data = json.loads(_get_bond_yield_curve({"country": "china"}))

        result = {"bond_market": {}}

        if "error" not in temp_data:
            result["bond_market"]["temperature"] = temp_data.get("temperature")
            result["bond_market"]["rate"] = temp_data.get("rate")
            result["bond_market"]["level"] = temp_data.get("level")
            result["bond_market"]["date"] = temp_data.get("date")

        if "error" not in curve_data:
            result["bond_market"]["yield_curve"] = {
                "china_summary": curve_data.get("summary", {}).get("中国", {}),
                "trend": curve_data.get("china_trend", ""),
                "curve_shape": curve_data.get("china_curve", ""),
            }

        # 3. 综合判断
        temp = temp_data.get("temperature")
        assessments = []
        if temp is not None:
            if temp < 30:
                assessments.append("债市温度偏低，债券有配置价值")
            elif temp < 70:
                assessments.append("债市温度适中")
            else:
                assessments.append("债市温度偏高，谨慎配置")

        # 收益率用 yield_curve 中的 10Y 值（百分比格式）
        china_summary = curve_data.get("summary", {}).get("中国", {})
        rate_10y = china_summary.get("10年")
        if rate_10y is not None:
            assessments.append(f"当前10Y国债收益率约{rate_10y}%")
        if curve_data.get("china_trend"):
            assessments.append(curve_data["china_trend"])
        if curve_data.get("china_curve"):
            assessments.append(f"收益率曲线形态：{curve_data['china_curve']}")

        result["bond_market"]["assessment"] = "；".join(assessments) if assessments else "暂无数据"

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error(f"获取债市概况失败: {e}")
        return json.dumps({"error": f"获取债市概况失败: {e}"}, ensure_ascii=False)


def _get_macro_policy_data() -> str:
    """获取当前中国宏观货币政策关键数据（LPR、降准、SHIBOR、CPI）。"""
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    import akshare as ak

    result = {}

    # 1. LPR 利率
    try:
        lpr_df = ak.macro_china_lpr()
        if lpr_df is not None and not lpr_df.empty:
            latest = lpr_df.iloc[-1]
            result["lpr"] = {
                "date": str(latest.get("TRADE_DATE", "")),
                "lpr_1y": float(latest.get("LPR1Y", 0)),
                "lpr_5y": float(latest.get("LPR5Y", 0)),
            }
            # 取最近3条看趋势
            recent = lpr_df.tail(3)
            result["lpr"]["recent_trend"] = [
                {"date": str(r.get("TRADE_DATE", "")), "1y": float(r.get("LPR1Y", 0)), "5y": float(r.get("LPR5Y", 0))}
                for _, r in recent.iterrows()
            ]
    except Exception as e:
        logger.warning(f"获取LPR数据失败: {e}")
        result["lpr"] = {"error": str(e)}

    # 2. 存款准备金率 (RRR) 最新调整
    try:
        rrr_df = ak.macro_china_reserve_requirement_ratio()
        if rrr_df is not None and not rrr_df.empty:
            latest_rrr = rrr_df.iloc[0]  # 最新一条
            result["rrr"] = {
                "announce_date": str(latest_rrr.get("公布时间", "")),
                "effective_date": str(latest_rrr.get("生效时间", "")),
                "large_before": float(latest_rrr.get("大型金融机构-调整前", 0)),
                "large_after": float(latest_rrr.get("大型金融机构-调整后", 0)),
                "large_change": float(latest_rrr.get("大型金融机构-调整幅度", 0)),
                "note": str(latest_rrr.get("备注", ""))[:100],
            }
    except Exception as e:
        logger.warning(f"获取RRR数据失败: {e}")
        result["rrr"] = {"error": str(e)}

    # 3. SHIBOR 各期限利率
    try:
        shibor_df = ak.macro_china_shibor_all()
        if shibor_df is not None and not shibor_df.empty:
            latest_shibor = shibor_df.iloc[-1]
            result["shibor"] = {
                "date": str(latest_shibor.get("日期", "")),
                "overnight": float(latest_shibor.get("O/N-定价", 0)),
                "1w": float(latest_shibor.get("1W-定价", 0)),
                "1m": float(latest_shibor.get("1M-定价", 0)),
                "3m": float(latest_shibor.get("3M-定价", 0)),
                "6m": float(latest_shibor.get("6M-定价", 0)),
                "1y": float(latest_shibor.get("1Y-定价", 0)),
            }
            # SHIBOR 趋势（最近5个交易日的3M和1Y）
            recent_shibor = shibor_df.tail(5)
            result["shibor"]["recent_3m_trend"] = [float(r.get("3M-定价", 0)) for _, r in recent_shibor.iterrows()]
            result["shibor"]["recent_1y_trend"] = [float(r.get("1Y-定价", 0)) for _, r in recent_shibor.iterrows()]
    except Exception as e:
        logger.warning(f"获取SHIBOR数据失败: {e}")
        result["shibor"] = {"error": str(e)}

    # 4. CPI 数据
    try:
        cpi_df = ak.macro_china_cpi_monthly()
        if cpi_df is not None and not cpi_df.empty:
            latest_cpi = cpi_df.iloc[-1]
            def _safe_val(v):
                """将 NaN 转为 None（NaN 不是合法 JSON）。"""
                try:
                    import math
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        return None
                    return v
                except Exception:
                    return v
            result["cpi"] = {
                "date": str(latest_cpi.get("日期", "")),
                "value": _safe_val(latest_cpi.get("今值")),
                "forecast": _safe_val(latest_cpi.get("预测值")),
                "previous": _safe_val(latest_cpi.get("前值")),
            }
    except Exception as e:
        logger.warning(f"获取CPI数据失败: {e}")
        result["cpi"] = {"error": str(e)}

    # 5. 综合政策环境判断
    policy_signals = []
    try:
        lpr_1y = result.get("lpr", {}).get("lpr_1y", 0)
        rrr_change = result.get("rrr", {}).get("large_change", 0)
        shibor_3m = result.get("shibor", {}).get("3m", 0)
        cpi_val = result.get("cpi", {}).get("value")

        if rrr_change and rrr_change < 0:
            policy_signals.append(f"近期降准{abs(rrr_change)}个百分点，货币宽松")
        elif rrr_change and rrr_change > 0:
            policy_signals.append(f"近期升准{rrr_change}个百分点，货币收紧")

        if lpr_1y:
            if lpr_1y <= 3.1:
                policy_signals.append(f"LPR 1Y {lpr_1y}%处于历史低位，宽松环境")
            elif lpr_1y >= 4.0:
                policy_signals.append(f"LPR 1Y {lpr_1y}%偏高，偏紧环境")
            else:
                policy_signals.append(f"LPR 1Y {lpr_1y}%，中性水平")

        if shibor_3m:
            if shibor_3m < 1.5:
                policy_signals.append(f"SHIBOR 3M {shibor_3m}%极低，流动性充裕")
            elif shibor_3m < 2.0:
                policy_signals.append(f"SHIBOR 3M {shibor_3m}%偏低，流动性较松")
            elif shibor_3m > 3.0:
                policy_signals.append(f"SHIBOR 3M {shibor_3m}%偏高，流动性偏紧")

        if cpi_val is not None:
            try:
                cpi_num = float(cpi_val)
                if cpi_num < 0:
                    policy_signals.append(f"CPI环比{cpi_num}%，通缩压力")
                elif cpi_num > 1:
                    policy_signals.append(f"CPI环比{cpi_num}%，通胀压力")
            except (ValueError, TypeError):
                pass
    except Exception:
        pass

    result["policy_summary"] = "；".join(policy_signals) if policy_signals else "暂无明确政策信号"

    return json.dumps(result, ensure_ascii=False)
