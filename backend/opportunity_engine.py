"""短线主题机会引擎。

MVP 版本采用确定性规则生成机会卡，后续可叠加 LLM 多 Agent 评审。
"""

from datetime import datetime, timedelta

from db import (
    get_portfolio_summary,
    get_total_cash_balance,
    list_holdings,
    list_valuation_indexes,
)
from db.opportunities import save_opportunity, list_opportunities


THEME_RULES = [
    {
        "theme": "红利低波",
        "keywords": ["红利", "高股息", "分红", "中特估", "低波"],
        "policy_terms": ["政策", "新国九条", "分红", "央企", "市值管理"],
        "future_direction": "低利率和重视股东回报环境下，高股息资产具备中期配置关注度。",
        "funds": [
            {
                "fund_code": "009051",
                "fund_name": "易方达中证红利ETF联接发起式A",
                "index_name": "中证红利",
                "vehicle_type": "otc_fund",
                "short_term_suitable": False,
            },
        ],
    },
    {
        "theme": "人工智能",
        "keywords": ["AI", "人工智能", "大模型", "算力", "数据中心"],
        "policy_terms": ["新质生产力", "人工智能", "算力", "数字经济"],
        "future_direction": "AI 应用、算力基础设施和国产替代仍是中长期产业方向。",
        "funds": [
            {
                "fund_code": "159819",
                "fund_name": "人工智能ETF",
                "index_name": "人工智能",
                "vehicle_type": "etf",
                "short_term_suitable": True,
            },
        ],
    },
    {
        "theme": "半导体",
        "keywords": ["半导体", "芯片", "集成电路", "晶圆", "封测"],
        "policy_terms": ["自主可控", "国产替代", "半导体", "科技"],
        "future_direction": "国产替代和先进制造政策支持下，半导体方向具备高弹性但波动较大。",
        "funds": [
            {
                "fund_code": "159995",
                "fund_name": "芯片ETF",
                "index_name": "芯片",
                "vehicle_type": "etf",
                "short_term_suitable": True,
            },
        ],
    },
    {
        "theme": "机器人",
        "keywords": ["机器人", "人形机器人", "自动化", "智能制造"],
        "policy_terms": ["机器人", "智能制造", "新质生产力"],
        "future_direction": "机器人处在产业化验证阶段，政策和新品催化会带来阶段性交易机会。",
        "funds": [
            {
                "fund_code": "562500",
                "fund_name": "机器人ETF",
                "index_name": "机器人",
                "vehicle_type": "etf",
                "short_term_suitable": True,
            },
        ],
    },
    {
        "theme": "新能源",
        "keywords": ["新能源", "光伏", "储能", "锂电", "电池"],
        "policy_terms": ["新能源", "储能", "碳中和", "设备更新"],
        "future_direction": "新能源长期方向明确，但短线需要确认供需改善和价格企稳。",
        "funds": [
            {
                "fund_code": "516160",
                "fund_name": "新能源ETF",
                "index_name": "新能源",
                "vehicle_type": "etf",
                "short_term_suitable": True,
            },
        ],
    },
]


def _contains_any(text: str, words: list[str]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def _latest_valuation_for_theme(theme_rule: dict) -> dict | None:
    indexes = list_valuation_indexes()
    fund_indexes = [f.get("index_name", "") for f in theme_rule.get("funds", [])]
    candidates = []
    for idx in indexes:
        name = idx.get("index_name") or ""
        if any(key and key in name for key in [theme_rule["theme"], *fund_indexes]):
            candidates.append(idx)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.get("percentile") if item.get("percentile") is not None else 100)
    return candidates[0]


def _portfolio_fit(theme_rule: dict, user_id: str = "default") -> dict:
    holdings = list_holdings(user_id)
    active = [h for h in holdings if (h.get("shares") or 0) > 0]
    matched = []
    for h in active:
        text = f"{h.get('fund_name','')} {h.get('index_name','')}"
        if _contains_any(text, [theme_rule["theme"], *theme_rule.get("keywords", [])]):
            matched.append(h)

    summary = get_portfolio_summary(user_id)
    total_assets = summary.get("total_assets", 0) or 0
    exposure = sum(h.get("current_value", 0) or 0 for h in matched)
    exposure_pct = exposure / total_assets if total_assets > 0 else 0
    cash = get_total_cash_balance()
    suggested_budget = round(min(cash * 0.1, max(total_assets * 0.01, 1000)), 2) if cash > 0 else 0

    return {
        "already_have": bool(matched),
        "related_holdings": [
            {"fund_code": h.get("fund_code"), "fund_name": h.get("fund_name"), "current_value": h.get("current_value")}
            for h in matched[:5]
        ],
        "theme_exposure_pct": round(exposure_pct, 4),
        "overlap_risk": "high" if exposure_pct >= 0.1 else ("medium" if exposure_pct >= 0.05 else "low"),
        "suggested_budget": suggested_budget,
        "max_position_pct": 3,
    }


def _score_theme(theme_rule: dict, news_hits: list[dict], valuation: dict | None, portfolio_fit: dict) -> tuple[int, str]:
    score = 0
    if news_hits:
        score += min(15, 8 + len(news_hits) * 3)
    policy_text = " ".join(f"{n.get('title','')} {n.get('summary','')}" for n in news_hits)
    score += 25 if _contains_any(policy_text, theme_rule.get("policy_terms", [])) else 10

    score += 12

    if valuation and valuation.get("percentile") is not None:
        pct = valuation["percentile"]
        if pct <= 30:
            score += 15
        elif pct <= 60:
            score += 9
        elif pct <= 80:
            score += 3
    else:
        score += 5

    overlap = portfolio_fit.get("overlap_risk")
    score += 15 if overlap == "low" else (8 if overlap == "medium" else 2)

    funds = theme_rule.get("funds", [])
    score += 10 if any(f.get("short_term_suitable") for f in funds) else 3

    score = max(0, min(100, score))
    verdict = "can_buy" if score >= 75 else ("watch" if score >= 50 else "avoid")
    if portfolio_fit.get("overlap_risk") == "high" and verdict == "can_buy":
        verdict = "watch"
    if funds and not any(f.get("short_term_suitable") for f in funds) and verdict == "can_buy":
        verdict = "watch"
    return score, verdict


def _build_matched_funds(theme_rule: dict) -> list[dict]:
    result = []
    for fund in theme_rule.get("funds", []):
        short_ok = bool(fund.get("short_term_suitable"))
        result.append({
            "fund_code": fund.get("fund_code"),
            "fund_name": fund.get("fund_name"),
            "index_name": fund.get("index_name"),
            "vehicle_type": fund.get("vehicle_type", "unknown"),
            "short_term_suitable": short_ok,
            "tradeability": "short_term_ok" if short_ok else "not_good_for_less_than_7d",
            "fee_warning": "" if short_ok else "场外基金持有少于7天赎回费可能较高，不适合超短线",
        })
    return result


def _build_item(theme_rule: dict, news_hits: list[dict], trade_date: str, user_id: str) -> dict:
    valuation = _latest_valuation_for_theme(theme_rule)
    portfolio_fit = _portfolio_fit(theme_rule, user_id)
    score, verdict = _score_theme(theme_rule, news_hits, valuation, portfolio_fit)
    matched_funds = _build_matched_funds(theme_rule)
    review_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=15)).strftime("%Y-%m-%d")

    policy_signal = (
        f"政策/新闻线索命中：{news_hits[0].get('title', theme_rule['theme'])}"
        if news_hits else "缺少明确政策/新闻催化，需观察"
    )
    valuation_role = "暂无估值数据，不能作为主要买入依据"
    if valuation:
        valuation_role = (
            f"{valuation.get('index_name')} {valuation.get('metric_type', '')}"
            f"百分位约 {valuation.get('percentile')}%，作为安全边际约束"
        )
    risk_note = "热点可能一日游，需按退出条件执行"
    if any(not f.get("short_term_suitable") for f in matched_funds):
        risk_note += "；场外基金不适合少于7天的超短线交易"

    return {
        "trade_date": trade_date,
        "theme": theme_rule["theme"],
        "verdict": verdict,
        "opportunity_score": score,
        "time_horizon": "7-15个交易日",
        "summary": f"{theme_rule['theme']}出现热点线索，当前结论为{'可小仓试投' if verdict == 'can_buy' else '观察优先' if verdict == 'watch' else '不建议追'}",
        "policy_signal": policy_signal,
        "future_direction": theme_rule["future_direction"],
        "market_signal": "已从今日热点中识别到主题线索，需结合后续成交与相对强弱确认",
        "valuation_role": valuation_role,
        "matched_funds": matched_funds,
        "portfolio_fit": portfolio_fit,
        "entry_plan": {
            "action": "小仓试投" if verdict == "can_buy" else "加入观察",
            "amount": portfolio_fit.get("suggested_budget", 0) if verdict == "can_buy" else 0,
            "batching": "一次试投或分2笔",
            "entry_condition": "热点延续且指数未明显冲高回落",
        },
        "exit_plan": {
            "take_profit": "上涨5%-8%分批止盈",
            "stop_loss": "回撤3%-5%或热点退潮退出",
            "time_stop": "15个交易日仍未兑现则复盘退出",
            "review_date": review_date,
        },
        "risk_note": risk_note,
        "evidence": [
            {"type": "news", "summary": n.get("title", ""), "source": n.get("source", "")}
            for n in news_hits[:3]
        ] + ([{"type": "valuation", "summary": valuation_role}] if valuation else []),
        "status": "active",
    }


def scan_daily_opportunities(news_items: list[dict] | None = None,
                             trade_date: str | None = None,
                             user_id: str = "default",
                             max_items: int = 8,
                             force_refresh: bool = True) -> dict:
    """生成并保存今日主题机会卡。"""
    trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
    if not force_refresh:
        existing = list_opportunities(trade_date=trade_date, user_id=user_id, limit=max_items)
        if existing:
            return {"date": trade_date, "items": existing, "source": "cache"}

    news_items = news_items or []
    items = []
    for rule in THEME_RULES:
        hits = [
            n for n in news_items
            if _contains_any(f"{n.get('title','')} {n.get('summary','')}", rule.get("keywords", []))
        ]
        if not hits:
            continue
        item = _build_item(rule, hits, trade_date, user_id)
        item["id"] = save_opportunity(item, user_id=user_id)
        items.append(item)

    items.sort(key=lambda x: x.get("opportunity_score", 0), reverse=True)
    return {
        "date": trade_date,
        "items": items[:max_items],
        "source": "rule_engine",
        "data_freshness": {
            "news": trade_date,
            "portfolio": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    }
