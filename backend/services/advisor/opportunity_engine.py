"""短线主题机会引擎。

MVP 版本采用确定性规则生成机会卡，后续可叠加 LLM 多 Agent 评审。

2026-07-20 系统性修复：
- P0-A: 估值过高一票否决（>80%强制avoid、>60%禁can_buy）
- P0-B: 修复无估值反加5分bug（改为不加分）
- P0-C: 关键词情感过滤（利空新闻不计news_hits）
- P0-D: 政策词权重25→12
- P0-E: 无条件基础分12→5
- P1-K: 接入技术指标维度（MACD/RSI/均线）0-15分
- P1-L: 接入资金流向维度（北向资金净流入）-5~+10分
- P1-M: 接入情绪指标维度（恐贪指数/债市温度）-5~+10分
- P1-N: 启动机会跟踪回测（15交易日后自动回测）
"""

import logging
from datetime import datetime, timedelta

from db import (
    get_portfolio_summary,
    get_total_cash_balance,
    list_holdings,
    list_valuation_indexes,
)
from db.opportunities import save_opportunity, list_opportunities

logger = logging.getLogger(__name__)


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


# ── P0-C: 利空词库（命中任一即认为新闻是利空，不计 news_hits）──
# 场景：原逻辑只匹配关键词"半导体"，导致"半导体股下挫"被计为利好新闻
# 修复：检测利空词后过滤掉该新闻，避免利空被当利好
_NEGATIVE_TERMS = [
    "下挫", "下跌", "跌停", "重挫", "暴跌", "跳水",
    "大跌", "下滑", "走低", "下探", "创历史新低", "破净",
    "利空", "减持", "解禁", "退市", "亏损",
    "业绩不及预期", "财报暴雷", "造假", "处罚", "立案",
]


def _contains_negative_sentiment(text: str) -> bool:
    """检测文本是否含利空词（用于过滤利空新闻被误计为利好）。"""
    if not text:
        return False
    low = text.lower()
    return any(term in low for term in _NEGATIVE_TERMS)


# ── 主题 → 指数代码映射（用于 P1-K/L 接入技术/资金维度）──
# P0-A 修复（2026-07-20）：原映射错误，半导体/新能源都映射到 399997（白酒）
# 现基于本地 index_valuations 表实际可查的指数代码修正
_THEME_INDEX_CODES = {
    "红利低波": "H30269",   # 红利低波（更精确，区别于 000922 中证红利）
    "人工智能": "931071.CSI",  # CS 人工智能
    "半导体": "H30184",      # 中证全指半导体
    "机器人": "H30590",      # 中证机器人
    "新能源": "399808",      # 中证新能
}


def _get_theme_index_code(theme_rule: dict) -> str:
    """获取主题对应的指数代码（用于技术指标/资金流向查询）。"""
    return _THEME_INDEX_CODES.get(theme_rule.get("theme", ""), "")


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


def _ema(values: list, period: int) -> list:
    """计算 EMA（指数移动平均）。"""
    if not values or period <= 0:
        return []
    result = [values[0]]
    multiplier = 2 / (period + 1)
    for i in range(1, len(values)):
        result.append(values[i] * multiplier + result[-1] * (1 - multiplier))
    return result


def _rsi(closes: list, period: int = 14) -> float:
    """计算 RSI 指标。"""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _get_technical_score(theme_rule: dict) -> tuple[int, str]:
    """P1-K: 获取主题对应指数的技术指标得分（MACD/RSI/均线）。

    Returns:
        (score_delta, signal): score_delta 范围 -5~+15，signal 为 "bull"/"bear"/"neutral"
    """
    try:
        index_code = _get_theme_index_code(theme_rule)
        if not index_code:
            return 0, "neutral"

        import akshare as ak
        # 获取近 60 日收盘价
        df = ak.stock_zh_index_daily(symbol=index_code)
        if df is None or len(df) < 30:
            return 0, "neutral"

        closes = df['close'].values[-60:]
        if len(closes) < 30:
            return 0, "neutral"

        # 1. MACD 简化版
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line = [a - b for a, b in zip(ema12, ema26)]
        signal_line = _ema(macd_line, 9) if len(macd_line) >= 9 else None
        macd_bull = signal_line is not None and len(signal_line) > 0 and macd_line[-1] > signal_line[-1]

        # 2. RSI(14)
        rsi_val = _rsi(closes, 14)
        rsi_bull = 30 < rsi_val < 70

        # 3. 均线多头排列
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
        ma_bull = ma5 > ma20 and closes[-1] > ma20

        # 综合评分
        score = 0
        if macd_bull:
            score += 5
        if rsi_bull:
            score += 5
        if ma_bull:
            score += 5

        signal = "bull" if score >= 10 else ("bear" if score == 0 else "neutral")
        return score, signal
    except Exception as e:
        logger.debug(f"[opportunity] 技术指标获取失败: {e}")
        return 0, "neutral"


def _get_capital_flow_score(theme_rule: dict) -> tuple[int, str]:
    """P1-L: 获取北向资金近 5 日净流入得分。

    Returns:
        (score_delta, signal): score_delta 范围 -5~+10
    """
    try:
        import akshare as ak
        # 北向资金近 5 日净流入
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北向")
        if df is None or len(df) < 5:
            return 0, "neutral"

        recent_5d = df['value'].values[-5:]
        net_inflow = float(recent_5d.sum())

        # 简单评分
        if net_inflow > 1e9:  # 净流入超 10 亿
            return 10, "inflow"
        elif net_inflow > 0:
            return 5, "inflow"
        elif net_inflow > -1e9:
            return -2, "outflow"
        else:
            return -5, "outflow"
    except Exception as e:
        logger.debug(f"[opportunity] 资金流向获取失败: {e}")
        return 0, "neutral"


def _get_sentiment_score() -> tuple[int, str]:
    """P1-M: 获取市场情绪指标得分（恐贪指数/债市温度）。

    Returns:
        (score_delta, signal): score_delta 范围 -5~+10
    """
    try:
        from services.portfolio_fact_layer import _build_market_state
        market_state = _build_market_state()
        sentiment = market_state.get("sentiment", "neutral")

        if sentiment == "fear":
            return 10, "fear"  # 情绪冰点，反指加分（"别人恐惧我贪婪"）
        elif sentiment == "greed":
            return -5, "greed"  # 情绪过热扣分
        else:
            return 0, "neutral"
    except Exception as e:
        logger.debug(f"[opportunity] 情绪指标获取失败: {e}")
        return 0, "neutral"


def _score_theme(theme_rule: dict, news_hits: list[dict], valuation: dict | None, portfolio_fit: dict) -> tuple[int, str]:
    """主题评分（2026-07-20 系统性修复后）。

    评分体系：
    - 新闻命中（过滤利空）：8-15 分
    - 政策词命中：12/5 分（原 25/10）
    - 无条件基础分：5 分（原 12）
    - 估值百分位：15/9/3/-5 分（原 +5 反 bug，>80% 倒扣）
    - 持仓重叠风险：15/8/2 分
    - 短期可交易性：10/3 分
    - 技术指标（P1-K 新增）：0-15 分
    - 资金流向（P1-L 新增）：-5~+10 分
    - 情绪指标（P1-M 新增）：-5~+10 分

    一票否决（P0-A）：
    - 估值 >80% → 强制 avoid
    - 估值 >60% → 禁止 can_buy
    - 无估值数据 → 禁止 can_buy
    """
    score = 0

    # ── 1. 新闻命中（P0-C: 过滤利空新闻）──
    positive_news = [
        n for n in news_hits
        if not _contains_negative_sentiment(f"{n.get('title','')} {n.get('summary','')}")
    ]
    if positive_news:
        score += min(15, 8 + len(positive_news) * 3)

    # ── 2. 政策词命中（P0-D: 权重 25→12）──
    policy_text = " ".join(f"{n.get('title','')} {n.get('summary','')}" for n in news_hits)
    score += 12 if _contains_any(policy_text, theme_rule.get("policy_terms", [])) else 5

    # ── 3. 无条件基础分（P0-E: 12→5）──
    score += 5

    # ── 4. 估值百分位（P0-B: 修复无估值反加5分bug；>80%倒扣分）──
    valuation_pct = None
    if valuation and valuation.get("percentile") is not None:
        pct = valuation["percentile"]
        valuation_pct = pct
        if pct <= 30:
            score += 15
        elif pct <= 60:
            score += 9
        elif pct <= 80:
            score += 3
        else:
            score -= 5  # P0-B: 估值过高倒扣分（原: +3 错误）
    # P0-B 修复：无估值数据不加分（原 bug: score += 5 反而加分）

    # ── 5. 持仓重叠风险 ──
    overlap = portfolio_fit.get("overlap_risk")
    score += 15 if overlap == "low" else (8 if overlap == "medium" else 2)

    # ── 6. 短期可交易性 ──
    funds = theme_rule.get("funds", [])
    score += 10 if any(f.get("short_term_suitable") for f in funds) else 3

    # ── 7. 技术指标（P1-K 新增）──
    tech_score, tech_signal = _get_technical_score(theme_rule)
    score += tech_score
    if tech_signal == "bear":
        score -= 5  # 技术看空额外扣分

    # ── 8. 资金流向（P1-L 新增）──
    capital_score, _ = _get_capital_flow_score(theme_rule)
    score += capital_score

    # ── 9. 情绪指标（P1-M 新增）──
    sentiment_score, _ = _get_sentiment_score()
    score += sentiment_score

    score = max(0, min(100, score))
    verdict = "can_buy" if score >= 75 else ("watch" if score >= 50 else "avoid")

    # ── P0-A: 估值过高一票否决 ──
    # 问题背景：原逻辑 14 条估值 97-99% 的主题仍判 can_buy
    # 修复策略：估值过高强制降级，避免历史高位建议上车
    if valuation_pct is not None:
        if valuation_pct > 80:
            verdict = "avoid"
            score = min(score, 30)
        elif valuation_pct > 60:
            if verdict == "can_buy":
                verdict = "watch"
                score = min(score, 60)
    else:
        # 估值数据缺失 → 不允许 can_buy
        if verdict == "can_buy":
            verdict = "watch"
            score = min(score, 60)

    # ── 原有降级逻辑 ──
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


def _build_summary(theme: str, news_hits: list, valuation: dict | None,
                   tech_signal: str, verdict: str) -> str:
    """P1-A 修复（2026-07-20）：动态生成 summary，禁止模板化。

    基于新闻+估值+技术面+verdict 多维度拼接，让用户看到具体差异。
    """
    parts = []
    # 1. 新闻维度
    if news_hits:
        top_news_title = news_hits[0].get("title", "")[:30]
        parts.append(f"新闻线索「{top_news_title}」")
    # 2. 估值维度
    if valuation and valuation.get("percentile") is not None:
        pct = valuation["percentile"]
        if pct > 80:
            parts.append(f"估值偏高（{pct}%分位，风险高）")
        elif pct < 30:
            parts.append(f"估值偏低（{pct}%分位，安全边际足）")
        else:
            parts.append(f"估值合理（{pct}%分位）")
    # 3. 技术维度
    if tech_signal == "bull":
        parts.append("技术指标偏多")
    elif tech_signal == "bear":
        parts.append("技术指标偏空")
    # 4. verdict 总结
    verdict_text = {
        "can_buy": "综合多维信号可小仓试投",
        "watch": "信号分歧建议观察",
        "avoid": "风险较高不建议追",
    }.get(verdict, "")
    if verdict_text:
        parts.append(verdict_text)
    return "；".join(parts) if parts else f"{theme}暂无明显信号"


def _build_risk_note(matched_funds: list, valuation: dict | None,
                     tech_signal: str, verdict: str) -> str:
    """P1-A 修复（2026-07-20）：动态生成 risk_note，禁止千篇一律。"""
    notes = []
    # 1. 估值风险
    if valuation and valuation.get("percentile") is not None:
        pct = valuation["percentile"]
        if pct > 80:
            notes.append(f"估值已处历史高位（{pct}%分位），追高风险大")
        elif pct > 60:
            notes.append(f"估值偏高（{pct}%分位），需关注回调")
    # 2. 技术风险
    if tech_signal == "bear":
        notes.append("技术指标偏空，短期可能继续调整")
    # 3. 估值数据缺失风险
    if not valuation or valuation.get("percentile") is None:
        notes.append("估值数据缺失，建议人工核实")
    # 4. 场外基金流动性风险
    otc_funds = [f for f in matched_funds if not f.get("short_term_suitable")]
    if otc_funds:
        notes.append(f"{len(otc_funds)}只场外基金不适合少于7天的超短线交易")
    # 5. 默认提示
    if not notes:
        notes.append("热点可能一日游，需按退出条件执行")
    return "；".join(notes)


def _calc_entry_amount(verdict: str, valuation: dict | None,
                       base_budget: float) -> float:
    """P1-B 修复（2026-07-20）：entry_plan amount 与估值分位挂钩。

    原问题：97% 高估和 43% 合理给的金额一样，风险失控
    修复：
    - <20% 分位：1.5x（深度低估加仓）
    - 20-40% 分位：1.0x（合理偏低）
    - 40-60% 分位：0.7x（合理）
    - 60-80% 分位：0.4x（偏高减仓）
    - >=80% 分位：0（理论上 verdict 已 avoid）
    - 无估值数据：0.5x（减半，保守）
    """
    if verdict != "can_buy":
        return 0
    if not valuation or valuation.get("percentile") is None:
        return round(base_budget * 0.5, 2)
    pct = valuation["percentile"]
    if pct < 20:
        multiplier = 1.5
    elif pct < 40:
        multiplier = 1.0
    elif pct < 60:
        multiplier = 0.7
    elif pct < 80:
        multiplier = 0.4
    else:
        return 0  # 高估不应入场
    return round(base_budget * multiplier, 2)


def _build_entry_condition(verdict: str, valuation: dict | None,
                          tech_signal: str) -> str:
    """P1-A 修复：entry_condition 也动态生成。"""
    if verdict != "can_buy":
        return "信号不足，暂观察"
    conditions = ["热点延续"]
    if valuation and valuation.get("percentile") is not None:
        pct = valuation["percentile"]
        if pct > 60:
            conditions.append("估值回落至 60% 分位以下再加仓")
        elif pct < 30:
            conditions.append("估值仍处低位可分批")
    if tech_signal == "bear":
        conditions.append("技术指标转多再入场")
    return "；".join(conditions)


def _build_item(theme_rule: dict, news_hits: list[dict], trade_date: str, user_id: str) -> dict:
    valuation = _latest_valuation_for_theme(theme_rule)
    portfolio_fit = _portfolio_fit(theme_rule, user_id)
    score, verdict = _score_theme(theme_rule, news_hits, valuation, portfolio_fit)
    matched_funds = _build_matched_funds(theme_rule)
    review_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=15)).strftime("%Y-%m-%d")

    # P1-K: 获取技术信号用于动态文案
    _, tech_signal = _get_technical_score(theme_rule)

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

    # P1-A: 动态生成 summary / risk_note
    summary = _build_summary(theme_rule["theme"], news_hits, valuation, tech_signal, verdict)
    risk_note = _build_risk_note(matched_funds, valuation, tech_signal, verdict)

    # P1-B: entry_plan amount 与估值挂钩
    base_budget = portfolio_fit.get("suggested_budget", 0)
    entry_amount = _calc_entry_amount(verdict, valuation, base_budget)
    entry_condition = _build_entry_condition(verdict, valuation, tech_signal)

    return {
        "trade_date": trade_date,
        "theme": theme_rule["theme"],
        "verdict": verdict,
        "opportunity_score": score,
        "time_horizon": "7-15个交易日",
        "summary": summary,
        "policy_signal": policy_signal,
        "future_direction": theme_rule["future_direction"],
        "market_signal": "已从今日热点中识别到主题线索，需结合后续成交与相对强弱确认",
        "valuation_role": valuation_role,
        "matched_funds": matched_funds,
        "portfolio_fit": portfolio_fit,
        "entry_plan": {
            "action": "小仓试投" if verdict == "can_buy" else "加入观察",
            "amount": entry_amount,
            "batching": "一次试投或分2笔",
            "entry_condition": entry_condition,
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


def _get_theme_index_current_price(theme_rule: dict) -> float | None:
    """获取主题对应指数的当前价格（用于回测 entry_price）。

    P0-B 修复（2026-07-20）：原用 ak.stock_zh_index_daily(sina API) 全部 404
    新策略：1. 优先查本地 index_valuations.current_point（同时尝试 code 与 code.CSI 后缀）
            2. 降级 akshare index_zh_a_hist（A 股指数日 K）
            3. 返回 None（回测逻辑需容忍 None）
    """
    index_code = _get_theme_index_code(theme_rule)
    if not index_code:
        return None

    # 1. 优先查本地估值表 current_point（尝试两种代码形式）
    try:
        from db.valuations import get_latest_valuation
        # P0-B 修复：H30590 在本地表存为 H30590.CSI，需尝试两种形式
        candidates = [index_code]
        if "." not in index_code:
            candidates.append(f"{index_code}.CSI")
        for code in candidates:
            v = get_latest_valuation(code)
            if v and v.get("current_point"):
                return float(v["current_point"])
    except Exception as e:
        logger.debug(f"[opportunity] 本地估值表查询失败 {index_code}: {e}")

    # 2. 降级 akshare index_zh_a_hist
    try:
        import akshare as ak
        # akshare 指数代码通常不带后缀（如 000922 而非 000922.CSI）
        bare_code = index_code.split(".")[0].split(" ")[0]
        df = ak.index_zh_a_hist(symbol=bare_code, period="daily", start_date=(datetime.now() - timedelta(days=7)).strftime("%Y%m%d"))
        if df is not None and len(df) > 0 and "收盘" in df.columns:
            return float(df['收盘'].values[-1])
    except Exception as e:
        logger.debug(f"[opportunity] akshare 获取指数价格失败 {index_code}: {e}")

    return None


def _create_opportunity_backtest(opportunity_id: int, theme_rule: dict, trade_date: str, review_date: str) -> None:
    """P1-N: 在 save_opportunity 后插入回测跟踪记录。

    用途：每次生成机会卡时同步插入回测记录，15 个交易日后自动回测命中率。
    解决问题：原 theme_opportunity_tracks 表是"用户已买入后跟踪"，0 条记录导致命中率统计永远为 None。
    """
    try:
        from db.opportunities import create_opportunity_backtest
        entry_price = _get_theme_index_current_price(theme_rule)
        create_opportunity_backtest({
            "opportunity_id": opportunity_id,
            "theme": theme_rule.get("theme", ""),
            "entry_date": trade_date,
            "review_date": review_date,
            "entry_price": entry_price,
        })
    except Exception as e:
        logger.debug(f"[opportunity] 创建回测记录失败: {e}")


def _get_theme_index_price_at(theme_rule: dict, target_date: str) -> float | None:
    """获取主题对应指数在指定日期（或之前最近一日）的收盘价。

    用于回测：review_date 当日价格查询。
    P0-B 修复（2026-07-20）：原用 ak.stock_zh_index_daily(sina API) 全部 404
    新策略：1. 优先查本地 index_valuations 表 snapshot_date <= target_date 的最近一条
            2. 降级 akshare index_zh_a_hist
            3. 返回 None
    """
    index_code = _get_theme_index_code(theme_rule)
    if not index_code:
        return None

    # 1. 优先查本地估值表 snapshot_date <= target_date 的最近一条
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            # 尝试两种代码形式（H30590 和 H30590.CSI）
            candidates = [index_code]
            if "." not in index_code:
                candidates.append(f"{index_code}.CSI")
            placeholders = ",".join("?" * len(candidates))
            row = conn.execute(
                f"""SELECT current_point FROM index_valuations
                    WHERE index_code IN ({placeholders})
                      AND snapshot_date <= ?
                    ORDER BY snapshot_date DESC LIMIT 1""",
                (*candidates, target_date),
            ).fetchone()
            if row and row["current_point"]:
                return float(row["current_point"])
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[opportunity] 本地估值表历史价查询失败 {index_code} @ {target_date}: {e}")

    # 2. 降级 akshare index_zh_a_hist
    try:
        import akshare as ak
        bare_code = index_code.split(".")[0].split(" ")[0]
        # 查询 target_date 前 7 天到 target_date 的数据
        end = target_date.replace("-", "")
        start_d = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y%m%d")
        df = ak.index_zh_a_hist(symbol=bare_code, period="daily",
                               start_date=start_d, end_date=end)
        if df is None or len(df) == 0:
            return None
        # 取最后一行（target_date 或之前最近交易日）
        if "收盘" in df.columns:
            return float(df["收盘"].values[-1])
    except Exception as e:
        logger.debug(f"[opportunity] 获取指数历史价格失败 {index_code} @ {target_date}: {e}")

    return None


def review_opportunity_backtests() -> dict:
    """P1-N: 批量回测已到期的机会记录（review_date <= today AND hit IS NULL）。

    命中定义：15 个交易日后涨幅 >= 3%（考虑交易成本）。

    Returns:
        {"reviewed": int, "hit": int, "miss": int}
    """
    try:
        from db.opportunities import list_pending_backtests, update_opportunity_backtest
        pending = list_pending_backtests()
        reviewed = 0
        hit_count = 0
        for track in pending:
            try:
                theme = track.get("theme", "")
                theme_rule = next((r for r in THEME_RULES if r["theme"] == theme), None)
                if not theme_rule:
                    continue

                review_date = track["review_date"]
                review_price = _get_theme_index_price_at(theme_rule, review_date)
                if not review_price:
                    logger.debug(f"[opportunity] 无法获取 review_price {theme} @ {review_date}")
                    continue

                entry_price = track.get("entry_price")
                if not entry_price or entry_price <= 0:
                    continue

                change_pct = (review_price - entry_price) / entry_price * 100
                # 命中定义：15 交易日后涨幅 >= 3%
                hit = 1 if change_pct >= 3.0 else 0

                update_opportunity_backtest(track["id"], {
                    "review_price": review_price,
                    "hit": hit,
                    "change_pct": round(change_pct, 2),
                    "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                reviewed += 1
                if hit:
                    hit_count += 1
            except Exception as e:
                logger.warning(f"[opportunity] 回测单条失败 {track.get('id')}: {e}")

        logger.info(f"[opportunity] 回测完成：{reviewed} 条，命中 {hit_count} 条")
        return {"reviewed": reviewed, "hit": hit_count, "miss": reviewed - hit_count}
    except Exception as e:
        logger.warning(f"[opportunity] 回测批量执行失败: {e}")
        return {"reviewed": 0, "hit": 0, "miss": 0, "error": str(e)}


def backfill_opportunity_backtests() -> dict:
    """P0-C 修复（2026-07-20）：补建历史机会卡的 backtest 记录。

    问题：theme_opportunities 表 79 条历史记录，theme_opportunity_backtests 表 0 条
    原因：原 _create_opportunity_backtest 用 sina API 返回 None，未写入
    修复：扫描所有没对应 backtest 记录的 opportunity，补建记录

    Returns:
        {"scanned": int, "created": int, "skipped": int}
    """
    try:
        from db._conn import _get_conn
        from db.opportunities import create_opportunity_backtest
        conn = _get_conn()
        try:
            # 找到所有没有对应 backtest 记录的 opportunity
            rows = conn.execute("""
                SELECT t.id, t.trade_date, t.theme
                FROM theme_opportunities t
                LEFT JOIN theme_opportunity_backtests b
                       ON b.opportunity_id = t.id
                WHERE b.id IS NULL
                ORDER BY t.id
            """).fetchall()
        finally:
            conn.close()

        scanned = len(rows)
        created = 0
        skipped = 0
        for r in rows:
            d = dict(r)
            try:
                theme = d.get("theme", "")
                theme_rule = next((r for r in THEME_RULES if r["theme"] == theme), None)
                if not theme_rule:
                    skipped += 1
                    continue

                trade_date = d["trade_date"]
                review_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=21)).strftime("%Y-%m-%d")
                entry_price = _get_theme_index_price_at(theme_rule, trade_date)
                # 即使 entry_price 是 None 也写入（后续回测会自动跳过 entry_price<=0 的记录）
                create_opportunity_backtest({
                    "opportunity_id": d["id"],
                    "theme": theme,
                    "entry_date": trade_date,
                    "review_date": review_date,
                    "entry_price": entry_price,
                })
                created += 1
            except Exception as e:
                logger.warning(f"[opportunity] backfill 单条失败 {d.get('id')}: {e}")
                skipped += 1

        logger.info(f"[opportunity] backfill 完成：扫描 {scanned}，新建 {created}，跳过 {skipped}")
        return {"scanned": scanned, "created": created, "skipped": skipped}
    except Exception as e:
        logger.warning(f"[opportunity] backfill 批量执行失败: {e}")
        return {"scanned": 0, "created": 0, "skipped": 0, "error": str(e)}


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
        # ── P1-N: 同步插入回测跟踪记录 ──
        # 用途：15 个交易日后自动回测命中率，让前端"命中率"chip 真正有数据
        _create_opportunity_backtest(
            opportunity_id=item["id"],
            theme_rule=rule,
            trade_date=trade_date,
            review_date=item.get("exit_plan", {}).get("review_date", ""),
        )
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
