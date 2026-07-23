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
import time
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
    """获取主题对应的指数代码（用于技术指标/资金流向查询）。

    O-2（2026-07-22）：优先取 theme_rule 上的 index_code（来自 DB），
    其次查硬编码 _THEME_INDEX_CODES 兜底。
    """
    # DB 配置化后，theme_rule 可能直接带 index_code 字段
    if theme_rule.get("index_code"):
        return theme_rule["index_code"]
    return _THEME_INDEX_CODES.get(theme_rule.get("theme", ""), "")


# ── O-2（2026-07-22）：THEME_RULES 配置化加载 ──
# DB 优先 + 硬编码兜底。模块级缓存避免每次扫描都查库。
_theme_rules_cache: list[dict] | None = None
_theme_rules_cache_ts: float = 0.0
_THEME_RULES_CACHE_TTL = 300.0  # 5 分钟


def _load_theme_rules_from_db() -> list[dict]:
    """从 theme_rules 表加载启用的主题规则。

    Returns:
        主题规则列表（与硬编码 THEME_RULES 结构对齐）；DB 异常或开关关闭时返回空列表。
    """
    try:
        from db.config import get_config
        if get_config("opportunity.theme_rules_db_enabled", "true") != "true":
            return []
        from db.theme_rules import list_theme_rules
        rules = list_theme_rules(active_only=True)
        if rules:
            logger.info(f"[opportunity] 从 DB 加载 {len(rules)} 条主题规则")
        return rules
    except Exception as e:
        logger.warning(f"[opportunity] 从 DB 加载主题规则失败，降级到硬编码: {e}")
        return []


def _get_active_theme_rules() -> list[dict]:
    """获取当前生效的主题规则列表（DB 优先，硬编码兜底 + 5 分钟缓存）。"""
    global _theme_rules_cache, _theme_rules_cache_ts
    import time as _time
    now = _time.time()
    if _theme_rules_cache is not None and (now - _theme_rules_cache_ts) < _THEME_RULES_CACHE_TTL:
        return _theme_rules_cache

    db_rules = _load_theme_rules_from_db()
    if db_rules:
        _theme_rules_cache = db_rules
        _theme_rules_cache_ts = now
        return db_rules

    # 兜底：使用硬编码 THEME_RULES
    _theme_rules_cache = THEME_RULES
    _theme_rules_cache_ts = now
    return THEME_RULES


def _invalidate_theme_rules_cache():
    """外部更新 theme_rules 表后调用，清空缓存使下次重新加载。"""
    global _theme_rules_cache, _theme_rules_cache_ts
    _theme_rules_cache = None
    _theme_rules_cache_ts = 0.0


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

    O-3（2026-07-22）：本地 index_price_history 表优先（+5分钟缓存），akshare 兜底。
    原问题：ak.stock_zh_index_daily 走 sina API 经常 404，技术指标维度长期 0 分。

    Returns:
        (score_delta, signal): score_delta 范围 -5~+15，signal 为 "bull"/"bear"/"neutral"
    """
    try:
        index_code = _get_theme_index_code(theme_rule)
        if not index_code:
            return 0, "neutral"

        # O-3：5 分钟缓存（同指数多次调用避免重复查库/请求）
        cache_key = f"tech_{index_code}"
        cached = _TECH_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _TECH_CACHE_TTL:
            return cached[1]

        closes = _fetch_index_closes(index_code, days=90)
        if not closes or len(closes) < 30:
            _TECH_CACHE[cache_key] = (time.time(), (0, "neutral"))
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
        result = (score, signal)
        _TECH_CACHE[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug(f"[opportunity] 技术指标获取失败: {e}")
        return 0, "neutral"


# O-3（2026-07-22）：技术指标缓存（5 分钟 TTL）
_TECH_CACHE: dict[str, tuple[float, tuple[int, str]]] = {}
_TECH_CACHE_TTL = 300.0


def _fetch_index_closes(index_code: str, days: int = 90) -> list[float]:
    """获取指数近 N 日收盘价序列。

    O-3：本地 index_price_history 优先（开关 opportunity.tech_indicator_local_first_enabled），
    失败时降级 ak.stock_zh_index_daily 兜底。

    Args:
        index_code: 指数代码（可带后缀如 931071.CSI）
        days: 返回最近多少天

    Returns:
        收盘价列表（按时间升序）；空列表表示获取失败
    """
    # 1. 本地表优先
    try:
        from db.config import get_config
        local_first = get_config("opportunity.tech_indicator_local_first_enabled", "true") == "true"
    except Exception:
        local_first = True

    if local_first:
        try:
            from services.index.index_history_fetcher import get_index_price_history
            history = get_index_price_history(index_code, days=days * 2)  # 多取一倍容错
            closes = [h["close"] for h in history if h.get("close") is not None]
            if len(closes) >= 30:
                logger.debug(f"[opportunity] 技术指标本地命中 {index_code}: {len(closes)} 条")
                return closes[-days:]
        except Exception as e:
            logger.debug(f"[opportunity] 本地 index_price_history 查询失败 {index_code}: {e}")

    # 2. akshare 兜底
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=index_code)
        if df is None or len(df) < 30:
            return []
        closes = [float(c) for c in df['close'].values[-days:]]
        logger.debug(f"[opportunity] 技术指标 akshare 兜底命中 {index_code}: {len(closes)} 条")
        return closes
    except Exception as e:
        logger.debug(f"[opportunity] akshare stock_zh_index_daily 失败 {index_code}: {e}")
        return []


def _get_capital_flow_score(theme_rule: dict) -> tuple[int, str]:
    """P1-L: 获取资金流向得分。

    O-3（2026-07-22）：板块级资金流向优先（开关 opportunity.sector_capital_flow_enabled），
    按 theme_rule.sector 查询 ak.stock_sector_fund_flow_rank 板块净流入；
    无 sector 或开关关闭时降级为北向资金全市场流向。

    Returns:
        (score_delta, signal): score_delta 范围 -5~+10
    """
    # O-3：板块级资金流向优先
    try:
        from db.config import get_config
        sector_flow_enabled = get_config("opportunity.sector_capital_flow_enabled", "true") == "true"
    except Exception:
        sector_flow_enabled = True

    if sector_flow_enabled and theme_rule.get("sector"):
        sector_score = _get_sector_capital_flow(theme_rule)
        if sector_score is not None:
            return sector_score

    # 降级：北向资金全市场流向
    return _get_north_capital_flow_score()


def _get_sector_capital_flow(theme_rule: dict) -> tuple[int, str] | None:
    """O-3：板块级资金流向查询。

    通过 ak.stock_sector_fund_flow_rank(indicator="今日") 获取行业板块资金流排名，
    按 theme_rule.sector 匹配板块名，取主力净流入额评分。

    Returns:
        (score, signal) 或 None（查询失败/未匹配到板块时）
    """
    try:
        import akshare as ak
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        if df is None or len(df) == 0:
            return None

        sector = theme_rule.get("sector", "")
        # 匹配板块名（df 中"行业"列含"半导体"、"人工智能"等）
        sector_col = None
        for col in df.columns:
            if "行业" in str(col) or "板块" in str(col):
                sector_col = col
                break
        if sector_col is None:
            return None

        # 找主力净流入额列
        flow_col = None
        for col in df.columns:
            if "主力净流入" in str(col) and "净额" in str(col):
                flow_col = col
                break
        if flow_col is None:
            for col in df.columns:
                if "主力净流入" in str(col):
                    flow_col = col
                    break
        if flow_col is None:
            return None

        # 在 df 中查找匹配的板块行
        matched_row = None
        for _, row in df.iterrows():
            if str(row[sector_col]) == sector:
                matched_row = row
                break
        if matched_row is None:
            # 模糊匹配（如"半导体"匹配"半导体及元件"）
            for _, row in df.iterrows():
                if sector in str(row[sector_col]):
                    matched_row = row
                    break
        if matched_row is None:
            return None

        # 取净流入额（单位：元）
        try:
            net_inflow = float(matched_row[flow_col])
        except (TypeError, ValueError):
            return None

        # 评分：板块级按净流入规模分档
        # >5亿 → +10（强势流入）
        # >0   → +5（净流入）
        # >-5亿 → -2（小幅流出）
        # else → -5（大幅流出）
        if net_inflow > 5e8:
            return 10, "inflow"
        elif net_inflow > 0:
            return 5, "inflow"
        elif net_inflow > -5e8:
            return -2, "outflow"
        else:
            return -5, "outflow"
    except Exception as e:
        logger.debug(f"[opportunity] 板块级资金流向查询失败: {e}")
        return None


def _get_north_capital_flow_score() -> tuple[int, str]:
    """北向资金全市场近 5 日净流入评分（原 _get_capital_flow_score 逻辑，作为降级路径）。"""
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


def _get_leading_indicator_score(theme_rule: dict, trade_date: str) -> tuple[int, str]:
    """LI-5（2026-07-22）：计算领先指标得分。返回 (score, reason)。

    评分规则：
    - 近 7 天有 strong 领先指标命中该主题 → +15
    - 近 7 天有 medium 领先指标命中 → +8
    - 近 7 天有领先指标但方向 negative 占多数 → -10
    - 无领先指标命中 → 0

    开关：opportunity.leading_indicator_score_enabled（默认 false，新增维度需观察）
    """
    from db.config import get_config
    if get_config("opportunity.leading_indicator_score_enabled", "false") != "true":
        return 0, ""

    try:
        from db.market_events import list_events_by_date_range
        from datetime import datetime, timedelta
        import json as _json

        theme = theme_rule.get("theme", "")
        sectors = [theme_rule.get("sector", "")] if theme_rule.get("sector") else []

        # 查近 7 天领先指标事件
        lookback = int(get_config("opportunity.leading_indicator_lookback_days", "7"))
        end_date = trade_date
        start_dt = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=lookback)
        start_date = start_dt.strftime("%Y-%m-%d")

        leading_types = ("policy_draft", "capex_announcement", "insider_trading", "customs_data", "pmi_subitem")
        events = list_events_by_date_range(
            start_date=start_date,
            end_date=end_date,
            event_types=leading_types,
        )

        matched = []
        for ev in events:
            ev_sectors = _json.loads(ev.get("affected_sectors", "[]")) if ev.get("affected_sectors") else []
            ev_themes = _json.loads(ev.get("affected_themes", "[]")) if ev.get("affected_themes") else []
            if theme in ev_themes or (sectors and set(sectors) & set(ev_sectors)):
                matched.append(ev)

        if not matched:
            return 0, ""

        strong_count = sum(1 for e in matched if e.get("event_type") in ("policy_draft", "capex_announcement", "insider_trading"))
        medium_count = sum(1 for e in matched if e.get("event_type") in ("customs_data", "pmi_subitem"))
        negative_count = sum(1 for e in matched if e.get("direction") == "negative")

        score = 0
        if strong_count > 0:
            score += 15
        if medium_count > 0:
            score += 8
        if negative_count > len(matched) / 2:  # 过半利空
            score = -10

        reason = f"领先指标命中: strong={strong_count}, medium={medium_count}, negative={negative_count}"
        return score, reason
    except Exception as e:
        logger.debug(f"[opportunity] 领先指标评分失败: {e}")
        return 0, ""


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
    - 领先指标（LI-5 新增）：-10~+15 分（开关默认关闭）

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

    # ── 10. 领先指标（LI-5 新增，开关默认关闭）──
    trade_date = datetime.now().strftime("%Y-%m-%d")
    leading_score, _ = _get_leading_indicator_score(theme_rule, trade_date)
    score += leading_score

    # ── F-4+（2026-07-23）：命中率反哺降权 — 闭环关键 ──
    # 主题连续 miss ≥3 次后降权，使低命中率主题的评分自动降低
    try:
        from db.config import get_config
        theme_name = theme_rule.get("theme", "")
        theme_weight = float(get_config(f"opportunity.weight_adjust_theme_{theme_name}", "1.0"))
        if theme_weight < 1.0:
            score = int(score * theme_weight)
    except Exception:
        pass

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
    _, capital_signal = _get_capital_flow_score(theme_rule)
    _, sentiment_signal = _get_sentiment_score()

    # ── L1 政策解读 LLM 化（2026-07-21）──
    # 对 watch/can_buy 候选调用 LLM 做政策实质解读，调整 score
    llm_policy = None
    if verdict in ("watch", "can_buy"):
        llm_policy = _llm_policy_analysis(theme_rule, news_hits)
        if llm_policy and isinstance(llm_policy.get("score_adjust"), int):
            score += llm_policy["score_adjust"]
            score = max(0, min(100, score))
            # 重新计算 verdict（L1 可能改变 verdict）
            verdict = "can_buy" if score >= 75 else ("watch" if score >= 50 else "avoid")
            # 重新应用一票否决
            valuation_pct = valuation.get("percentile") if valuation else None
            if valuation_pct is not None:
                if valuation_pct > 80:
                    verdict = "avoid"
                    score = min(score, 30)
                elif valuation_pct > 60 and verdict == "can_buy":
                    verdict = "watch"
                    score = min(score, 60)
            elif verdict == "can_buy":
                verdict = "watch"
                score = min(score, 60)

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

    # O-3（2026-07-21）：填充主表核心字段，避免前端机会卡片缺失数据
    entry_price = _get_theme_index_current_price(theme_rule)
    valuation_percentile = valuation.get("percentile") if valuation else None

    # 构造 item（L2 评审需要完整 item）
    item = {
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
        # O-3 新增 4 个核心字段（与 theme_opportunity_backtests 同步）
        "entry_price": entry_price,
        "entry_amount": entry_amount,
        "valuation_percentile": valuation_percentile,
        "review_status": "pending",  # 默认 pending，回测完成后改为 completed
    }

    # ── L1 政策解读结果写入 item ──
    if llm_policy:
        item["llm_policy_analysis"] = llm_policy

    # ── L2 深度推理评审（2026-07-21）──
    # 对 can_buy 候选调用 LLM 做最终评审，可降级不可升级
    llm_review = _llm_deep_review(item, valuation, tech_signal, capital_signal, sentiment_signal)
    if llm_review:
        item["llm_review"] = llm_review
        # 应用 LLM 降级（仅可降级，不可升级）
        new_verdict = llm_review.get("final_verdict")
        if new_verdict in ("watch", "avoid") and item["verdict"] == "can_buy":
            item["verdict"] = new_verdict
            # 降级后重新计算 entry_amount
            item["entry_plan"]["amount"] = _calc_entry_amount(new_verdict, valuation, base_budget)
            item["entry_plan"]["action"] = "加入观察" if new_verdict == "watch" else "暂不入场"

    return item


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
        from db.config import get_config_bool
        entry_price = _get_theme_index_current_price(theme_rule)

        # LI-6（2026-07-22）：标记信号来源
        signal_source = "news"
        if get_config_bool("opportunity.signal_source_tracking_enabled", True):
            # 检查是否有领先指标命中该主题
            leading_score, _ = _get_leading_indicator_score(theme_rule, trade_date)
            if leading_score > 0:
                signal_source = "leading_strong"
            elif leading_score < 0:
                signal_source = "leading_medium"

        create_opportunity_backtest({
            "opportunity_id": opportunity_id,
            "theme": theme_rule.get("theme", ""),
            "entry_date": trade_date,
            "review_date": review_date,
            "entry_price": entry_price,
            "signal_source": signal_source,
        })
    except Exception as e:
        logger.debug(f"[opportunity] 创建回测记录失败: {e}")


def _get_theme_index_price_at(theme_rule: dict, target_date: str) -> float | None:
    """获取主题对应指数在指定日期（或之前最近一日）的收盘价。

    用于回测：review_date 当日价格查询。
    P0-B 修复（2026-07-20）：原用 ak.stock_zh_index_daily(sina API) 全部 404
    新策略：1. 优先查本地 index_price_history 表（F-5+ 回填的主题指数）
            2. 查本地 index_valuations 表 snapshot_date <= target_date 的最近一条
            3. 降级 akshare index_zh_a_hist（带超时保护）
            4. 返回 None
    """
    index_code = _get_theme_index_code(theme_rule)
    if not index_code:
        return None

    bare_code = index_code.split(".")[0].split(" ")[0]

    # 1. F-5+（2026-07-23）：优先查本地 index_price_history（启动回填的主题指数）
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            candidates = [bare_code, index_code]
            if "." not in index_code:
                candidates.append(f"{index_code}.CSI")
            placeholders = ",".join("?" * len(candidates))
            row = conn.execute(
                f"""SELECT close FROM index_price_history
                    WHERE index_code IN ({placeholders})
                      AND trade_date <= ?
                    ORDER BY trade_date DESC LIMIT 1""",
                (*candidates, target_date),
            ).fetchone()
            if row and row["close"]:
                return float(row["close"])
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[opportunity] 本地 index_price_history 查询失败 {index_code} @ {target_date}: {e}")

    # 2. 查本地估值表 snapshot_date <= target_date 的最近一条
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
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

    # 3. 降级 akshare index_zh_a_hist（带超时保护）
    try:
        import akshare as ak
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout
        end = target_date.replace("-", "")
        start_d = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y%m%d")
        df = call_akshare_with_timeout(
            ak.index_zh_a_hist, symbol=bare_code, period="daily",
            start_date=start_d, end_date=end, timeout=20,
        )
        if df is None or len(df) == 0:
            return None
        if "收盘" in df.columns:
            return float(df["收盘"].values[-1])
    except Exception as e:
        logger.debug(f"[opportunity] 获取指数历史价格失败 {index_code} @ {target_date}: {e}")

    return None


def _build_miss_reason(change_pct: float, benchmark_pct: float | None,
                       excess_return: float | None, entry_percentile: float | None) -> str:
    """F-4（2026-07-23）：拼接 miss 原因，用于反哺分析。

    根据回测数据拼接人类可读的 miss 原因，帮助人工分析命中率低的根因。
    """
    reasons = []
    if excess_return is not None:
        reasons.append(f"超额收益{excess_return:+.1f}%（未达+2%阈值）")
    else:
        reasons.append(f"绝对涨幅{change_pct:+.1f}%（未达+3%阈值）")
    if benchmark_pct is not None:
        reasons.append(f"沪深300同期{benchmark_pct:+.1f}%")
    if entry_percentile is not None:
        if entry_percentile > 60:
            reasons.append(f"入场估值偏高({entry_percentile:.0f}%)")
        elif entry_percentile < 30:
            reasons.append(f"入场估值偏低({entry_percentile:.0f}%)")
    return "；".join(reasons) if reasons else ""


def _build_hit_reason(change_pct: float, benchmark_pct: float | None,
                      excess_return: float | None, entry_percentile: float | None) -> str:
    """F-4：拼接命中原因（hit=1 时）。"""
    reasons = []
    if excess_return is not None:
        reasons.append(f"超额收益{excess_return:+.1f}%（达+2%阈值）")
    else:
        reasons.append(f"绝对涨幅{change_pct:+.1f}%（达+3%阈值）")
    if benchmark_pct is not None:
        reasons.append(f"沪深300同期{benchmark_pct:+.1f}%")
    if entry_percentile is not None:
        if entry_percentile <= 30:
            reasons.append(f"入场估值偏低({entry_percentile:.0f}%)")
        elif entry_percentile <= 60:
            reasons.append(f"入场估值合理({entry_percentile:.0f}%)")
    return "；".join(reasons) if reasons else ""


def backfill_miss_reason() -> dict:
    """F-4（2026-07-23）：批量回填已回测记录的 miss_reason。

    扫描 hit IS NOT NULL AND miss_reason IS NULL 的记录，
    根据 change_pct/benchmark_pct/excess_return/entry_percentile 拼接 miss 原因。

    Returns:
        {"scanned": int, "filled": int, "skipped": int}
    """
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            rows = conn.execute("""
                SELECT id, change_pct, benchmark_pct, excess_return, entry_percentile, hit
                FROM theme_opportunity_backtests
                WHERE hit IS NOT NULL AND (miss_reason IS NULL OR miss_reason = '')
            """).fetchall()
        finally:
            conn.close()

        if not rows:
            return {"scanned": 0, "filled": 0, "skipped": 0}

        scanned = len(rows)
        filled = 0
        conn = _get_conn()
        try:
            for r in rows:
                d = dict(r)
                hit = d.get("hit")
                if hit == 1:
                    # 命中记录也补充原因（标注命中原因）
                    reason = _build_hit_reason(d.get("change_pct"), d.get("benchmark_pct"),
                                               d.get("excess_return"), d.get("entry_percentile"))
                else:
                    reason = _build_miss_reason(d.get("change_pct"), d.get("benchmark_pct"),
                                                d.get("excess_return"), d.get("entry_percentile"))
                if reason:
                    conn.execute(
                        "UPDATE theme_opportunity_backtests SET miss_reason = ? WHERE id = ?",
                        (reason, d["id"])
                    )
                    filled += 1
            conn.commit()
        finally:
            conn.close()

        logger.info(f"[opportunity] miss_reason 回填: 扫描{scanned}, 填充{filled}")
        return {"scanned": scanned, "filled": filled, "skipped": scanned - filled}
    except Exception as e:
        logger.warning(f"[opportunity] miss_reason 回填失败: {e}")
        return {"scanned": 0, "filled": 0, "skipped": 0, "error": str(e)}


def _apply_hit_rate_feedback():
    """LI-6（2026-07-22）+ F-4+（2026-07-23）：命中率反哺评分权重。

    规则：
    - 某信号来源连续 3 次 miss → 降权 20%（写入 system_config）
    - F-4+：某主题连续 3 次 miss → 该主题降权 20%（opportunity.weight_adjust_theme_{theme}）
    - 连续 2 次 hit → 恢复原权重
    """
    try:
        from db.opportunities import get_consecutive_misses_by_source, get_consecutive_misses_by_theme
        from db.config import update_config, get_config

        # 1. per-source 降权
        consecutive_misses = get_consecutive_misses_by_source()
        for source, miss_count in consecutive_misses.items():
            config_key = f"opportunity.weight_adjust_{source}"
            if miss_count >= 3:
                current = float(get_config(config_key, "1.0"))
                new_weight = max(0.5, current * 0.8)  # 最低 50%
                if new_weight != current:
                    update_config(config_key, str(round(new_weight, 2)))
                    logger.info(f"[opportunity] 命中率反哺(source): {source} 连续{miss_count}次miss，权重 {current}→{new_weight}")

        # 2. F-4+：per-theme 降权（解决不同主题 hit/miss 交错导致 per-source 统计失效）
        consecutive_misses_theme = get_consecutive_misses_by_theme()
        for theme, miss_count in consecutive_misses_theme.items():
            config_key = f"opportunity.weight_adjust_theme_{theme}"
            if miss_count >= 3:
                current = float(get_config(config_key, "1.0"))
                new_weight = max(0.5, current * 0.8)  # 最低 50%
                if new_weight != current:
                    update_config(config_key, str(round(new_weight, 2)))
                    logger.info(f"[opportunity] 命中率反哺(theme): {theme} 连续{miss_count}次miss，权重 {current}→{new_weight}")
    except Exception as e:
        logger.debug(f"[opportunity] _apply_hit_rate_feedback 失败: {e}")


def review_opportunity_backtests() -> dict:
    """P1-N: 批量回测已到期的机会记录（review_date <= today AND hit IS NULL）。

    命中定义（L3 基准化后）：
    - 开关开：超额收益（涨幅 - 沪深300同期涨幅）>= 2% 视为命中
    - 开关关：绝对涨幅 >= 3% 视为命中（原逻辑）

    Returns:
        {"reviewed": int, "hit": int, "miss": int}
    """
    try:
        from db.config import get_config_bool
        from db.opportunities import list_pending_backtests, update_opportunity_backtest
        benchmark_enabled = get_config_bool("opportunity.benchmark_backtest_enabled", True)
        pending = list_pending_backtests()
        reviewed = 0
        hit_count = 0
        for track in pending:
            try:
                theme = track.get("theme", "")
                theme_rule = next((r for r in _get_active_theme_rules() if r["theme"] == theme), None)
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

                # L3 回测基准化：引入沪深300超额收益
                benchmark_pct = None
                excess_return = None
                hit = None
                if benchmark_enabled:
                    benchmark_pct = _get_benchmark_return(
                        track.get("entry_date", ""), review_date
                    )
                    if benchmark_pct is not None:
                        excess_return = change_pct - benchmark_pct
                        # 超额收益 >= 2% 视为命中
                        hit = 1 if excess_return >= 2.0 else 0
                    else:
                        # 基准获取失败，回退原逻辑
                        hit = 1 if change_pct >= 3.0 else 0
                else:
                    # 开关关：原逻辑
                    hit = 1 if change_pct >= 3.0 else 0

                update_fields = {
                    "review_price": review_price,
                    "hit": hit,
                    "change_pct": round(change_pct, 2),
                    "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if benchmark_pct is not None:
                    update_fields["benchmark_pct"] = round(benchmark_pct, 2)
                if excess_return is not None:
                    update_fields["excess_return"] = round(excess_return, 2)

                # F-4（2026-07-23）：拼接 miss_reason 用于反哺分析
                entry_pct = track.get("entry_percentile")
                if hit == 1:
                    miss_reason_text = _build_hit_reason(change_pct, benchmark_pct, excess_return, entry_pct)
                else:
                    miss_reason_text = _build_miss_reason(change_pct, benchmark_pct, excess_return, entry_pct)
                if miss_reason_text:
                    update_fields["miss_reason"] = miss_reason_text

                update_opportunity_backtest(track["id"], update_fields)

                # LI-6（2026-07-22）：miss 时记录原因
                if hit == 0:
                    try:
                        from db.opportunities import update_backtest_miss_reason
                        from db.config import get_config_bool
                        if get_config_bool("opportunity.signal_source_tracking_enabled", True):
                            # 拼接 miss 原因
                            reasons = []
                            if benchmark_pct is not None:
                                reasons.append(f"超额收益={excess_return:.1f}%（基准={benchmark_pct:.1f}%）")
                            else:
                                reasons.append(f"绝对涨幅={change_pct:.1f}%")
                            # 查该机会的估值分位
                            opp_val = None
                            try:
                                from db.opportunities import get_opportunity
                                opp = get_opportunity(track.get("opportunity_id"))
                                if opp:
                                    opp_val = opp.get("valuation_percentile")
                            except Exception:
                                pass
                            if opp_val is not None:
                                reasons.append(f"入场估值分位={opp_val:.0f}%")
                            miss_reason = " | ".join(reasons)
                            update_backtest_miss_reason(track["id"], miss_reason)
                    except Exception as e:
                        logger.debug(f"[opportunity] miss_reason 更新失败: {e}")

                reviewed += 1
                if hit:
                    hit_count += 1
            except Exception as e:
                logger.warning(f"[opportunity] 回测单条失败 {track.get('id')}: {e}")

        # LI-6（2026-07-22）：命中率反哺 — 连续3次miss降权
        try:
            from db.config import get_config_bool
            if get_config_bool("opportunity.hit_rate_feedback_enabled", True):
                _apply_hit_rate_feedback()
        except Exception as e:
            logger.debug(f"[opportunity] 命中率反哺失败: {e}")

        logger.info(f"[opportunity] 回测完成：{reviewed} 条，命中 {hit_count} 条")
        return {"reviewed": reviewed, "hit": hit_count, "miss": reviewed - hit_count}
    except Exception as e:
        logger.warning(f"[opportunity] 回测批量执行失败: {e}")
        return {"reviewed": 0, "hit": 0, "miss": 0, "error": str(e)}


def recompute_benchmark_for_reviewed() -> dict:
    """F-1 补充（2026-07-23）：重算已回测记录的 benchmark_pct + excess_return + 重新判定 hit。

    修复历史 24 条已回测记录中仅 1 条有 benchmark_pct 的问题。
    基于新的本地优先 _get_benchmark_return 重新计算沪深300基准，
    并根据基准化逻辑重新判定 hit（超额 ≥ 2% 命中）。

    Returns:
        {"scanned": int, "updated": int, "benchmark_filled": int, "hit_changed": int}
    """
    try:
        from db.config import get_config_bool
        from db.opportunities import update_opportunity_backtest
        benchmark_enabled = get_config_bool("opportunity.benchmark_backtest_enabled", True)
        if not benchmark_enabled:
            return {"scanned": 0, "updated": 0, "benchmark_filled": 0, "hit_changed": 0,
                    "message": "基准化回测开关关闭"}

        from db._conn import _get_conn
        conn = _get_conn()
        try:
            rows = conn.execute("""
                SELECT b.id, b.theme, b.entry_date, b.review_date, b.entry_price, b.review_price,
                       b.change_pct, b.hit, b.benchmark_pct, b.entry_percentile,
                       b.opportunity_id, t.valuation_percentile as opp_valuation_pct
                FROM theme_opportunity_backtests b
                LEFT JOIN theme_opportunities t ON b.opportunity_id = t.id
                WHERE b.hit IS NOT NULL AND b.entry_price IS NOT NULL AND b.entry_price > 0
                  AND b.review_price IS NOT NULL AND b.review_price > 0
            """).fetchall()
        finally:
            conn.close()

        scanned = len(rows)
        if not rows:
            return {"scanned": 0, "updated": 0, "benchmark_filled": 0, "hit_changed": 0}

        updated = 0
        benchmark_filled = 0
        hit_changed = 0

        for r in rows:
            d = dict(r)
            entry_date = d.get("entry_date", "")
            review_date = d.get("review_date", "")
            change_pct = d.get("change_pct")
            old_hit = d.get("hit")

            # 重新计算基准
            benchmark_pct = _get_benchmark_return(entry_date, review_date)
            if benchmark_pct is None:
                continue  # 基准仍获取失败，跳过

            excess_return = round(change_pct - benchmark_pct, 2) if change_pct is not None else None
            # 重新判定 hit（超额 ≥ 2% 命中）
            new_hit = 1 if (excess_return is not None and excess_return >= 2.0) else 0

            # F-4+：回填 entry_percentile（从 theme_opportunities.valuation_percentile）
            entry_pct = d.get("entry_percentile")
            if entry_pct is None:
                entry_pct = d.get("opp_valuation_pct")

            update_fields = {
                "benchmark_pct": round(benchmark_pct, 2),
                "excess_return": excess_return,
                "hit": new_hit,
            }
            # F-4+：回填 entry_percentile
            if entry_pct is not None:
                update_fields["entry_percentile"] = round(float(entry_pct), 2)
            # 拼接 miss_reason / hit_reason（用回填后的 entry_pct）
            if new_hit == 1:
                update_fields["miss_reason"] = _build_hit_reason(
                    change_pct, benchmark_pct, excess_return, entry_pct)
            else:
                update_fields["miss_reason"] = _build_miss_reason(
                    change_pct, benchmark_pct, excess_return, entry_pct)

            update_opportunity_backtest(d["id"], update_fields)
            updated += 1
            benchmark_filled += 1
            if new_hit != old_hit:
                hit_changed += 1

        # 重算后触发命中率反哺
        try:
            _apply_hit_rate_feedback()
        except Exception:
            pass

        logger.info(f"[opportunity] F-1 重算基准: 扫描{scanned}, 更新{updated}, "
                    f"基准填充{benchmark_filled}, hit变化{hit_changed}")
        return {"scanned": scanned, "updated": updated,
                "benchmark_filled": benchmark_filled, "hit_changed": hit_changed}
    except Exception as e:
        logger.warning(f"[opportunity] F-1 重算基准失败: {e}")
        return {"scanned": 0, "updated": 0, "benchmark_filled": 0, "hit_changed": 0,
                "error": str(e)}


def _get_benchmark_return(entry_date: str, review_date: str) -> float | None:
    """L3 回测基准化：获取沪深300涨幅（本地优先 + akshare 兜底）。

    用于计算超额收益，避免牛市普涨导致的假命中。
    F-1（2026-07-23）修复：本地 index_price_history 优先，akshare 带超时兜底。

    Returns:
        涨幅百分比（如 2.5 表示涨 2.5%），或 None（获取失败）
    """
    if not entry_date or not review_date:
        return None
    # 1. 本地 index_price_history 优先
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            entry_row = conn.execute(
                "SELECT close FROM index_price_history WHERE index_code='000300' "
                "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                (entry_date,)
            ).fetchone()
            review_row = conn.execute(
                "SELECT close FROM index_price_history WHERE index_code='000300' "
                "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                (review_date,)
            ).fetchone()
        finally:
            conn.close()
        if entry_row and review_row and entry_row[0] and review_row[0] and entry_row[0] > 0:
            return (review_row[0] - entry_row[0]) / entry_row[0] * 100
    except Exception as e:
        logger.debug(f"[opportunity] 本地基准查询失败: {e}")
    # 2. akshare 兜底（带超时保护，避免 zombie 线程）
    try:
        import akshare as ak
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout
        df = call_akshare_with_timeout(
            ak.index_zh_a_hist, symbol="000300", period="daily",
            start_date=entry_date.replace("-", ""),
            end_date=review_date.replace("-", ""),
            timeout=20,
        )
        if df is None or df.empty or len(df) < 2:
            return None
        first_close = float(df.iloc[0]["收盘"])
        last_close = float(df.iloc[-1]["收盘"])
        if first_close <= 0:
            return None
        return (last_close - first_close) / first_close * 100
    except Exception as e:
        logger.debug(f"[opportunity] akshare 基准获取失败: {e}")
        return None


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
                theme_rule = next((r for r in _get_active_theme_rules() if r["theme"] == theme), None)
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

        # F-5（2026-07-23）：修复 entry_price 缺失的记录
        try:
            from db._conn import _get_conn
            conn = _get_conn()
            try:
                null_price_rows = conn.execute(
                    "SELECT id, opportunity_id, theme, entry_date FROM theme_opportunity_backtests "
                    "WHERE entry_price IS NULL OR entry_price <= 0"
                ).fetchall()
            finally:
                conn.close()

            if null_price_rows:
                repaired = 0
                for r in null_price_rows:
                    d = dict(r)
                    theme = d.get("theme", "")
                    theme_rule = next((tr for tr in _get_active_theme_rules() if tr["theme"] == theme), None)
                    if not theme_rule:
                        continue
                    entry_date = d.get("entry_date", "")
                    if not entry_date:
                        continue
                    entry_price = _get_theme_index_price_at(theme_rule, entry_date)
                    if entry_price and entry_price > 0:
                        conn = _get_conn()
                        try:
                            conn.execute(
                                "UPDATE theme_opportunity_backtests SET entry_price = ? WHERE id = ?",
                                (entry_price, d["id"])
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        repaired += 1
                logger.info(f"[opportunity] F-5 entry_price 修复: 扫描{len(null_price_rows)}, 修复{repaired}")
        except Exception as e:
            logger.warning(f"[opportunity] F-5 entry_price 修复失败: {e}")

        return {"scanned": scanned, "created": created, "skipped": skipped}
    except Exception as e:
        logger.warning(f"[opportunity] backfill 批量执行失败: {e}")
        return {"scanned": 0, "created": 0, "skipped": 0, "error": str(e)}


def backfill_opportunity_fields(max_items: int = 100) -> dict:
    """O-3 backfill: 对历史 theme_opportunities 补齐 entry_price/entry_amount/valuation_percentile。

    场景：theme_opportunities 表中 entry_price IS NULL 的历史记录（save_opportunity 修复前）。
    本函数遍历主题规则（O-2: DB 优先 + 硬编码兜底），按 theme 反查 entry_price / valuation_percentile 并更新。

    Returns:
        {"scanned": N, "updated": M, "skipped": K}
    """
    from db._conn import _get_conn
    try:
        conn = _get_conn()
        # 查询所有 entry_price IS NULL 的记录
        rows = conn.execute(
            "SELECT id, theme, trade_date FROM theme_opportunities "
            "WHERE entry_price IS NULL OR entry_amount IS NULL "
            "ORDER BY id DESC LIMIT ?",
            (max_items,),
        ).fetchall()
        scanned = len(rows)
        updated = 0
        skipped = 0

        for r in rows:
            try:
                theme = r["theme"]
                # 通过 theme 反查（O-2：从 DB 加载）
                theme_rule = next((t for t in _get_active_theme_rules() if t["theme"] == theme), None)
                if not theme_rule:
                    skipped += 1
                    continue

                # 计算字段
                entry_price = _get_theme_index_current_price(theme_rule)
                valuation = _latest_valuation_for_theme(theme_rule)
                valuation_percentile = valuation.get("percentile") if valuation else None
                # 计算 entry_amount 需要 portfolio_fit
                portfolio_fit = _portfolio_fit(theme_rule)
                base_budget = portfolio_fit.get("suggested_budget", 0)
                # 查 verdict
                verdict_row = conn.execute(
                    "SELECT verdict FROM theme_opportunities WHERE id = ?", (r["id"],)
                ).fetchone()
                verdict = verdict_row["verdict"] if verdict_row else "watch"
                entry_amount = _calc_entry_amount(verdict, valuation, base_budget)

                # 更新主表
                conn.execute(
                    "UPDATE theme_opportunities SET entry_price = ?, entry_amount = ?, "
                    "valuation_percentile = ?, review_status = 'pending', "
                    "updated_at = datetime('now','localtime') WHERE id = ?",
                    (entry_price, entry_amount, valuation_percentile, r["id"]),
                )
                updated += 1
            except Exception as e:
                logger.warning(f"[opportunity] backfill fields 单条失败 {r['id']}: {e}")
                skipped += 1

        conn.commit()
        conn.close()
        logger.info(f"[opportunity] backfill_fields 完成：扫描 {scanned}，更新 {updated}，跳过 {skipped}")
        return {"scanned": scanned, "updated": updated, "skipped": skipped}
    except Exception as e:
        logger.warning(f"[opportunity] backfill_fields 批量执行失败: {e}")
        return {"scanned": 0, "updated": 0, "skipped": 0, "error": str(e)}


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
    # O-2（2026-07-22）：主题规则从 DB 加载（硬编码兜底）
    active_rules = _get_active_theme_rules()
    for rule in active_rules:
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


# ════════════════════════════════════════════════════════════════════
# 深度研究增强（2026-07-21）：L1 政策解读 LLM 化 + L2 深度推理评审
# ════════════════════════════════════════════════════════════════════

def _llm_policy_analysis(theme_rule: dict, news_hits: list[dict]) -> dict | None:
    """L1 政策解读 LLM 化。

    对 watch/can_buy 候选机会，调用 LLM 做政策实质解读。
    原规则只是字符串匹配 policy_terms，不理解政策实质利好/利空、力度强弱、落地概率。

    Returns:
        {
            "policy_substance": "strong|weak|neutral",
            "beneficiary_alignment": "high|medium|low",
            "implementation_probability": "high|medium|low",
            "key_risk": "...",
            "reasoning": "30-80字解读",
            "score_adjust": int  // +8 / -5 / 0
        }
        或 None（开关关闭/调用失败/无新闻）
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.llm_policy_analysis_enabled", False):
            return None
    except Exception:
        return None

    if not news_hits:
        return None

    # 构造新闻摘要（取前 3 条，每条 title+summary 前 100 字）
    news_text = ""
    for n in news_hits[:3]:
        title = n.get("title", "")
        summary = (n.get("summary", "") or "")[:100]
        news_text += f"- {title}：{summary}\n"

    prompt = f"""你是政策分析师。请分析以下新闻对"{theme_rule['theme']}"主题的实质影响。

主题关键词：{', '.join(theme_rule.get('keywords', []))}
政策词库：{', '.join(theme_rule.get('policy_terms', []))}

今日命中新闻：
{news_text}

请输出 JSON（不要 markdown 代码块）：
{{
  "policy_substance": "strong/weak/neutral（政策实质力度）",
  "beneficiary_alignment": "high/medium/low（与主题受益契合度）",
  "implementation_probability": "high/medium/low（落地概率）",
  "key_risk": "主要风险（20字内）",
  "reasoning": "综合解读（30-80字）"
}}"""

    try:
        from services.llm.llm_service import _call_llm
        resp = _call_llm(
            caller="opportunity_policy_analysis",
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
            timeout=15,
        )
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        import json as _json
        # 容错：提取 JSON
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = _json.loads(content)

        # 计算 score_adjust
        substance = result.get("policy_substance", "neutral")
        alignment = result.get("beneficiary_alignment", "medium")
        if substance == "strong" and alignment == "high":
            result["score_adjust"] = 8
        elif substance == "weak" or alignment == "low":
            result["score_adjust"] = -5
        else:
            result["score_adjust"] = 0
        return result
    except Exception as e:
        logger.warning(f"[opportunity] L1 政策解读失败: {e}")
        return None


def _llm_deep_review(item: dict, valuation: dict | None,
                     tech_signal: str, capital_signal: str, sentiment_signal: str) -> dict | None:
    """L2 深度推理评审。

    对 can_buy 候选，调用 LLM 做最终多维度权衡评审。
    LLM 可降级 verdict（can_buy → watch/avoid），不可升级（避免过度乐观）。

    Returns:
        {
            "final_verdict": "can_buy|watch|avoid",
            "confidence": "high|medium|low",
            "key_pros": ["..."],
            "key_cons": ["..."],
            "net_assessment": "50-150字综合权衡",
            "timing_note": "最佳入场时机判断"
        }
        或 None（开关关闭/调用失败/非 can_buy）
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.llm_deep_review_enabled", False):
            return None
    except Exception:
        return None

    # 仅对 can_buy 候选做深度评审
    if item.get("verdict") != "can_buy":
        return None

    valuation_pct = valuation.get("percentile") if valuation else None
    prompt = f"""你是资深投资经理。请对以下"可上车"机会做最终深度评审。

主题：{item.get('theme', '')}
综合评分：{item.get('opportunity_score', 0)}/100
估值分位：{valuation_pct}%
技术信号：{tech_signal}
资金信号：{capital_signal}
情绪信号：{sentiment_signal}
持仓重叠：{item.get('portfolio_fit', {}).get('overlap_risk', 'unknown')}
摘要：{item.get('summary', '')[:200]}

评审规则：
- 你只能维持或降级（can_buy → watch/avoid），不能升级
- 权衡估值/技术/资金/情绪/持仓的矛盾点
- 给出明确的入场时机判断

输出 JSON（不要 markdown 代码块）：
{{
  "final_verdict": "can_buy/watch/avoid",
  "confidence": "high/medium/low",
  "key_pros": ["看多理由1", "看多理由2"],
  "key_cons": ["看空理由1", "看空理由2"],
  "net_assessment": "50-150字综合权衡",
  "timing_note": "入场时机判断（30字内）"
}}"""

    try:
        from services.llm.llm_service import _call_llm
        resp = _call_llm(
            caller="opportunity_deep_review",
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
            timeout=20,
        )
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        import json as _json
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = _json.loads(content)

        # 强制降级约束：LLM 不能升级
        if result.get("final_verdict") not in ("can_buy", "watch", "avoid"):
            result["final_verdict"] = "watch"  # 异常时保守
        return result
    except Exception as e:
        logger.warning(f"[opportunity] L2 深度评审失败: {e}")
        return None

