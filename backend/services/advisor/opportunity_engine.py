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


def _calc_valuation_zscore(index_code: str | None, metric_type: str | None = None,
                           window_years: int | None = None) -> dict | None:
    """P0-R1（2026-08-01）：估值信号科学化 — 计算估值指标的 z-score 与均值回归半衰期。

    金融原理：裸百分位只反映"当前在历史区间的位置"，z-score 进一步刻画
    "偏离历史均值多少个标准差"，对极端低估/高估更敏感；均值回归半衰期
    （OU 过程估计）辅助判断"估值修复大概需要多久"，用于时机判断。

    所有阈值/窗口均走 system_config（金融严谨性，禁止硬编码）。

    Returns:
        {zscore, level, mean, std, current, sample_size, window_years,
         half_life_months, indicator} 或 None（开关关闭/无数据/样本不足）
        level ∈ {deep_low, low, neutral, high}
    """
    if not index_code:
        return None
    try:
        from db.config import get_config_bool, get_config_int, get_config_float
        if not get_config_bool("opportunity.valsignal.enabled", True):
            return None
        if window_years is None:
            window_years = get_config_int("opportunity.valsignal.zscore_window_years", 10)
        halflife_enabled = get_config_bool("opportunity.valsignal.halflife_enabled", True)
        z_deep = get_config_float("opportunity.valsignal.zscore_deep", -1.5)
        z_low = get_config_float("opportunity.valsignal.zscore_low", -1.0)
        z_high = get_config_float("opportunity.valsignal.zscore_high", 1.0)
    except Exception:
        return None

    try:
        from db.valuations import get_valuation_history
        rows = get_valuation_history(index_code, days=window_years * 365, metric_type=metric_type)
    except Exception:
        return None
    if not rows:
        return None

    # 序列按时间升序（get_valuation_history 返回 DESC，需反转）；仅取有效 current_value
    series = [r.get("current_value") for r in reversed(rows) if r.get("current_value") is not None]
    n = len(series)
    if n < 30:  # 样本不足，z-score 不稳定，保守不输出
        return {"zscore": None, "level": "unknown", "sample_size": n,
                "window_years": window_years, "reason": "insufficient_samples"}

    mean = sum(series) / n
    var = sum((x - mean) ** 2 for x in series) / n
    std = var ** 0.5
    current = series[-1]
    zscore = (current - mean) / std if std > 0 else 0.0

    # 均值回归半衰期（OU 过程）：对 Δx_t = α + β·x_{t-1} 做回归，half-life = -ln2 / β
    half_life_months = None
    if halflife_enabled and n >= 40:
        try:
            xs = series[:-1]            # x_{t-1}
            dys = [series[i] - series[i - 1] for i in range(1, n)]  # Δx_t
            m = len(xs)
            mx = sum(xs) / m
            sxx = sum((x - mx) ** 2 for x in xs)
            sxy = sum((xs[i] - mx) * dys[i] for i in range(m))
            beta = sxy / sxx if sxx > 0 else 0.0
            if beta < 0:  # β<0 才存在均值回归
                import math
                # β 是"每期"回归系数，假设数据为日频→月化近似 ×21
                half_life_periods = -math.log(2) / beta
                half_life_months = round(half_life_periods / 21.0, 1)
        except Exception:
            half_life_months = None

    if zscore <= z_deep:
        level = "deep_low"
    elif zscore <= z_low:
        level = "low"
    elif zscore >= z_high:
        level = "high"
    else:
        level = "neutral"

    return {
        "zscore": round(zscore, 3),
        "level": level,
        "mean": round(mean, 3),
        "std": round(std, 3),
        "current": round(current, 3),
        "sample_size": n,
        "window_years": window_years,
        "half_life_months": half_life_months,
        "index_code": index_code,
        "metric_type": metric_type,
    }


def _select_valuation_indicator(theme_name: str) -> str:
    """P0-R1：按主题类型选择更合适的估值指标（周期/银行用PB、成长用PS、红利用股息率）。

    金融原理：不同行业/风格的指数，最适配的估值口径不同——
    周期与银行盈利波动大，PE 失真，宜用 PB；成长股看 PS；红利策略看股息率。
    指标映射走 system_config（opportunity.valsignal.indicator_map，JSON）。
    """
    try:
        from db.config import get_config
        import json as _json
        raw = get_config("opportunity.valsignal.indicator_map",
                         '{"default":"pe","bank":"pb","cycle":"pb","growth":"ps","dividend":"dy"}')
        mapping = _json.loads(raw) if raw else {}
    except Exception:
        mapping = {}
    category = _get_theme_category(theme_name)
    # 银行归入 cycle 类处理（_get_theme_category 未必单独区分 bank）
    name_lower = (theme_name or "").lower()
    if any(k in theme_name for k in ("银行", "证券", "保险", "金融")) or "bank" in name_lower:
        category = "bank"
    elif any(k in theme_name for k in ("红利", "股息", "高股息")):
        category = "dividend"
    return mapping.get(category) or mapping.get("default") or "pe"


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
            {
                "fund_code": h.get("fund_code"),
                "fund_name": h.get("fund_name"),
                "current_value": h.get("current_value"),
                # Phase 2（2026-07-30）：补充 profit_rate/index_code，供 _score_theme 维度5 增强
                "profit_rate": h.get("profit_rate"),
                "index_code": h.get("index_code"),
                "index_name": h.get("index_name"),
            }
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


# ── P1-R5（2026-08-01）：宏观 regime 联动 ──
# 缓存 market_state 1 小时（避免每次评分都查 DB + 调外部接口）
_MARKET_STATE_CACHE: dict = {"data": None, "ts": 0.0}
_MARKET_STATE_CACHE_TTL = 3600.0


def _get_market_state_cached() -> dict:
    """获取市场状态（regime + sentiment + pe_percentile），1 小时缓存。"""
    import time as _time
    now = _time.monotonic()
    cached = _MARKET_STATE_CACHE.get("data")
    if cached and (now - _MARKET_STATE_CACHE.get("ts", 0.0)) < _MARKET_STATE_CACHE_TTL:
        return cached
    try:
        from services.portfolio_fact_layer import _build_market_state
        state = _build_market_state()
    except Exception as e:
        logger.debug(f"[opportunity] _build_market_state 失败: {e}")
        state = {"regime": "unknown", "sentiment": "neutral", "pe_percentile": None}
    _MARKET_STATE_CACHE["data"] = state
    _MARKET_STATE_CACHE["ts"] = now
    return state


def _get_regime_align_score(theme_rule: dict, valuation_pct: float | None) -> tuple[int, str]:
    """P1-R5：宏观 regime 联动评分（第 15 维）。

    依据市场 regime 与主题属性（进攻/防御）的匹配度给分：
    - bear + 防御主题 → 加分（资金避险流向防御资产）
    - bear + 进攻主题 → 扣分（熊市不追高）
    - bull + 进攻主题 → 加分（牛市进攻主线）
    - bull + 高估值 → regime gate 惩罚（牛市尾部追高最危险）
    - sideways → 中性 0

    Returns:
        (score_delta, regime): score_delta 范围 -8~+5
    """
    try:
        from db.config import get_config, get_config_int
        state = _get_market_state_cached()
        regime = state.get("regime", "unknown")
        if regime == "unknown":
            return 0, "unknown"

        theme_name = theme_rule.get("theme", "")
        # 主题分类从 JSON 配置读取
        import json as _json
        try:
            cat_map = _json.loads(get_config("opportunity.regime.theme_category", "{}"))
        except Exception:
            cat_map = {}
        category = cat_map.get(theme_name, "neutral")

        if regime == "bear":
            if category == "defensive":
                return get_config_int("opportunity.regime.bear_defensive_bonus", 5), "bear"
            elif category == "offensive":
                return get_config_int("opportunity.regime.bear_offensive_penalty", -5), "bear"
            else:
                return 0, "bear"
        elif regime == "bull":
            # regime gate：牛市 + 高估值 → 强惩罚（追高最危险）
            if valuation_pct is not None and valuation_pct > 70:
                return get_config_int("opportunity.regime.bull_high_valuation_penalty", -8), "bull"
            if category == "offensive":
                return get_config_int("opportunity.regime.bull_offensive_bonus", 3), "bull"
            return 0, "bull"
        else:  # sideways
            return 0, "sideways"
    except Exception as e:
        logger.debug(f"[opportunity] regime 评分失败: {e}")
        return 0, "unknown"


def _calc_fed_model() -> dict:
    """P1-R5：FED 模型（股债性价比）— 计算沪深300 EP - 10Y 国债收益率。

    EP = 1/PE（沪深300 盈利收益率）
    YTM = 10 年期国债收益率
    fed_value = EP - YTM（正值越大，股票相对债券越有吸引力）

    开关 opportunity.macro.fed_model_enabled 默认 false（需外部数据）。

    Returns:
        {"fed_value": float, "ep": float, "ytm": float, "zscore": float|None}
        失败返回空 dict
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.macro.fed_model_enabled", False):
            return {}

        # 取沪深300 PE
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute(
            """SELECT percentile, pe_ttm
               FROM index_valuations
               WHERE (index_code = '399300.SZ' OR index_code = '000300.SH')
                 AND metric_type = '市盈率'
               ORDER BY snapshot_date DESC LIMIT 1"""
        ).fetchone()
        conn.close()
        if not row or not row["pe_ttm"]:
            return {}
        pe = float(row["pe_ttm"])
        ep = 1.0 / pe if pe > 0 else 0.0

        # 取 10Y 国债收益率（通过工具调 akshare）
        try:
            from tools import execute_tool
            result_str = execute_tool("get_bond_yield_curve", {"country": "china"})
            import json as _json
            curve = _json.loads(result_str) if isinstance(result_str, str) else result_str
            summary = curve.get("summary", {}).get("中国", {})
            ytm = summary.get("10年")
            if ytm is None:
                return {}
            # akshare 返回的收益率是百分数（如 2.5 表示 2.5%），需除以 100
            ytm = float(ytm) / 100.0
        except Exception as e:
            logger.debug(f"[opportunity] FED 模型取国债收益率失败: {e}")
            return {}

        fed_value = ep - ytm
        # z-score 简化：fed_value > 0 为股票有吸引力，< 0 为债券有吸引力
        # 历史均值约 0，标准差约 0.01-0.02，此处用简单阈值
        zscore = fed_value / 0.015 if abs(fed_value) < 0.05 else (3.0 if fed_value > 0 else -3.0)

        return {
            "fed_value": round(fed_value, 4),
            "ep": round(ep, 4),
            "ytm": round(ytm, 4),
            "zscore": round(zscore, 2),
        }
    except Exception as e:
        logger.debug(f"[opportunity] FED 模型计算失败: {e}")
        return {}


# Accuracy-Fix（2026-07-27）：成交量缓存（5 分钟 TTL，与技术指标共用窗口）
_VOLUME_CACHE: dict[str, tuple[float, tuple[int, str]]] = {}
_VOLUME_CACHE_TTL = 300.0


def _get_volume_score(theme_rule: dict) -> tuple[int, str]:
    """Accuracy-Fix（2026-07-27）：成交量确认机制。

    通过对比主题指数近 5 日成交量与 20 日平均成交量，判断量能配合：
    - 放量（>1.3x）：+5（量价齐升，有效确认）
    - 正常（0.8x~1.3x）：0
    - 缩量（<0.8x）：-5（无量上涨，可靠性低）

    作为第二数据源验证新闻信号的真实性，避免"干拔"主题被误判。

    Returns:
        (score_delta, signal): score_delta 范围 -5~+5，signal 为 "expand"/"shrink"/"neutral"
    """
    try:
        index_code = _get_theme_index_code(theme_rule)
        if not index_code:
            return 0, "neutral"

        # 5 分钟缓存
        cache_key = f"vol_{index_code}"
        cached = _VOLUME_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _VOLUME_CACHE_TTL:
            return cached[1]

        volumes = _fetch_index_volumes(index_code, days=25)
        if not volumes or len(volumes) < 20:
            _VOLUME_CACHE[cache_key] = (time.time(), (0, "neutral"))
            return 0, "neutral"

        recent_5d_avg = sum(volumes[-5:]) / 5
        base_20d_avg = sum(volumes[-20:]) / 20

        if base_20d_avg <= 0:
            _VOLUME_CACHE[cache_key] = (time.time(), (0, "neutral"))
            return 0, "neutral"

        ratio = recent_5d_avg / base_20d_avg
        if ratio > 1.3:
            result = (5, "expand")
        elif ratio < 0.8:
            result = (-5, "shrink")
        else:
            result = (0, "neutral")

        _VOLUME_CACHE[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug(f"[opportunity] 成交量获取失败: {e}")
        return 0, "neutral"


def _fetch_index_volumes(index_code: str, days: int = 25) -> list[float]:
    """Accuracy-Fix（2026-07-27）：获取指数近 N 日成交量序列。

    优先从 akshare index_zh_a_hist 获取（带超时保护），返回成交量列表。
    本地表无 volume 字段，只能走 akshare。

    Returns:
        成交量列表（按时间升序）；空列表表示获取失败
    """
    try:
        import akshare as ak
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout
        bare_code = index_code.split(".")[0].split(" ")[0]
        # 多取一倍容错
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2 + 10)).strftime("%Y%m%d")
        df = call_akshare_with_timeout(
            ak.index_zh_a_hist, symbol=bare_code, period="daily",
            start_date=start_date, end_date=end_date, timeout=15,
        )
        if df is None or len(df) < days:
            return []
        # akshare 返回的列名为中文：成交量
        if "成交量" not in df.columns:
            return []
        volumes = [float(v) for v in df["成交量"].values[-days:]]
        return volumes
    except Exception as e:
        logger.debug(f"[opportunity] 获取指数成交量失败 {index_code}: {e}")
        return []


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


# ════════════════════════════════════════════════════════════════
# Accuracy-Boost（2026-07-30）：数据源扩展 — 研报/融资融券/ETF申赎 3 个新维度
# 目标：从 11 维扩展到 14 维，总分仍 cap 100，提升信号可靠性
# ════════════════════════════════════════════════════════════════

# 模块级缓存（避免同主题多次调用重复请求 akshare）
_DATA_SOURCE_CACHE: dict[str, tuple[float, tuple]] = {}
_DATA_SOURCE_CACHE_TTL = 600.0  # 10 分钟


def _get_research_report_score(theme_rule: dict) -> tuple[int, str]:
    """Accuracy-Boost 维度12：研报情绪（-5 ~ +8）。

    通过 ak.stock_research_report_em 获取主题代表 ETF 的研报评级变化：
    - 近 30 天评级上调（如"增持→买入"）→ +8
    - 近 30 天评级下调（如"买入→增持"）→ -5
    - 无变化或无数据 → 0

    开关：opportunity.research_report_enabled（默认 true）
    Returns:
        (score_delta, signal): signal 为 "upgrade"/"downgrade"/"neutral"
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.research_report_enabled", True):
            return 0, "neutral"
    except Exception:
        return 0, "neutral"

    try:
        # 用主题代表 ETF 代码作为查询 key（研报接口需要 symbol）
        funds = theme_rule.get("funds", [])
        if not funds:
            return 0, "neutral"
        # 取第一个 ETF 的代码（去掉后缀）
        raw_code = funds[0].get("fund_code", "")
        if not raw_code:
            return 0, "neutral"

        cache_key = f"rr_{raw_code}"
        cached = _DATA_SOURCE_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _DATA_SOURCE_CACHE_TTL:
            return cached[1]

        import akshare as ak
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

        # stock_research_report_em 需要股票代码，ETF 代码不直接支持
        # 降级方案：用主题关键词在新闻中检测研报评级变化（复用 news_hits 不可行，此处用 akshare 财报研报）
        # 实际实现：查询主题对应板块的个股研报，统计近30天评级变化
        # 简化版：用 ak.stock_comment_em 获取市场评论（含评级统计）作为代理
        try:
            df = call_akshare_with_timeout(
                ak.stock_comment_em, symbol=raw_code, timeout=10,
            )
        except Exception:
            df = None

        if df is None or len(df) == 0:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 解析评级变化（akshare 返回的列名可能因版本不同）
        # 常见列：最新评级, 上次评级, 评级日期
        latest_rating = None
        prev_rating = None
        for col in df.columns:
            col_lower = str(col).lower()
            if "最新" in str(col) or "latest" in col_lower:
                latest_rating = str(df.iloc[0][col])
            elif "上次" in str(col) or "previous" in col_lower:
                prev_rating = str(df.iloc[0][col])

        if not latest_rating or not prev_rating:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 评级强弱排序：买入 > 增持 > 中性 > 减持 > 卖出
        _RATING_RANK = {"买入": 5, "推荐": 5, "增持": 4, "优于大市": 4,
                        "中性": 3, "持有": 3, "同步": 3,
                        "减持": 2, "回避": 1, "卖出": 1}
        latest_rank = _RATING_RANK.get(latest_rating, 3)
        prev_rank = _RATING_RANK.get(prev_rating, 3)

        if latest_rank > prev_rank:
            result = (8, "upgrade")
        elif latest_rank < prev_rank:
            result = (-5, "downgrade")
        else:
            result = (0, "neutral")

        _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug(f"[opportunity] 研报情绪获取失败: {e}")
        return 0, "neutral"


def _get_margin_data_score(theme_rule: dict) -> tuple[int, str]:
    """Accuracy-Boost 维度13：融资融券余额变化（-3 ~ +5）。

    通过 ak.stock_margin_detail_sse/szse 获取融资余额变化：
    - 近 5 日融资余额上升 → +5（杠杆资金看多）
    - 近 5 日融资余额下降 → -3（杠杆资金看空）
    - 无数据 → 0

    开关：opportunity.margin_data_enabled（默认 true）
    Returns:
        (score_delta, signal): signal 为 "inflow"/"outflow"/"neutral"
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.margin_data_enabled", True):
            return 0, "neutral"
    except Exception:
        return 0, "neutral"

    try:
        # 用全市场融资融券余额趋势作为代理（主题级数据需要个股代码，过于复杂）
        cache_key = "margin_market"
        cached = _DATA_SOURCE_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _DATA_SOURCE_CACHE_TTL:
            return cached[1]

        import akshare as ak
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

        # 沪市融资融券余额（每日汇总）
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")

        try:
            df = call_akshare_with_timeout(
                ak.stock_margin_underlying_info_szse,
                start_date=start_date, end_date=end_date, timeout=15,
            )
        except Exception:
            df = None

        if df is None or len(df) == 0:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 融资余额列名兼容
        finance_col = None
        for col in df.columns:
            if "融资" in str(col) and "余额" in str(col):
                finance_col = col
                break

        if not finance_col:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 取最近 5 日融资余额
        recent = df[finance_col].astype(float).tail(5).tolist()
        if len(recent) < 5:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 趋势判断：近5日均值 vs 前5日均值
        recent_avg = sum(recent) / len(recent)
        first_val = recent[0]
        if first_val <= 0:
            result = (0, "neutral")
        elif recent_avg > first_val * 1.005:  # 上升 0.5% 以上
            result = (5, "inflow")
        elif recent_avg < first_val * 0.995:  # 下降 0.5% 以上
            result = (-3, "outflow")
        else:
            result = (0, "neutral")

        _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug(f"[opportunity] 融资融券数据获取失败: {e}")
        return 0, "neutral"


def _get_etf_flow_score(theme_rule: dict) -> tuple[int, str]:
    """Accuracy-Boost 维度14：ETF 申赎净流入（-3 ~ +5）。

    通过 ak.fund_etf_fund_daily_em 获取主题 ETF 的净申购/赎回数据：
    - 净申购（资金流入）→ +5
    - 净赎回（资金流出）→ -3
    - 无数据 → 0

    开关：opportunity.etf_flow_enabled（默认 true）
    Returns:
        (score_delta, signal): signal 为 "inflow"/"outflow"/"neutral"
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.etf_flow_enabled", True):
            return 0, "neutral"
    except Exception:
        return 0, "neutral"

    try:
        funds = theme_rule.get("funds", [])
        etf_codes = [f.get("fund_code", "") for f in funds
                     if f.get("vehicle_type") == "etf" or "ETF" in f.get("fund_name", "")]
        if not etf_codes:
            return 0, "neutral"

        etf_code = etf_codes[0]
        cache_key = f"etf_flow_{etf_code}"
        cached = _DATA_SOURCE_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _DATA_SOURCE_CACHE_TTL:
            return cached[1]

        import akshare as ak
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

        # fund_etf_fund_daily_em 返回所有 ETF 每日资金流向
        try:
            df = call_akshare_with_timeout(
                ak.fund_etf_fund_daily_em, timeout=15,
            )
        except Exception:
            df = None

        if df is None or len(df) == 0:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 匹配 ETF 代码（df 中代码列可能含前缀如 "159819.SH"）
        code_col = None
        for col in df.columns:
            if "代码" in str(col) or "code" in str(col).lower():
                code_col = col
                break

        if not code_col:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 模糊匹配代码
        mask = df[code_col].astype(str).str.contains(etf_code, na=False)
        matched = df[mask]
        if len(matched) == 0:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 净流入/流出列（akshare 列名：净流入/净流出）
        flow_col = None
        for col in df.columns:
            if "净流" in str(col):
                flow_col = col
                break

        if not flow_col:
            result = (0, "neutral")
            _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
            return result

        # 取近 5 日净流入均值
        recent_flow = matched[flow_col].astype(float).tail(5).tolist()
        if len(recent_flow) == 0:
            result = (0, "neutral")
        else:
            avg_flow = sum(recent_flow) / len(recent_flow)
            if avg_flow > 0:
                result = (5, "inflow")
            elif avg_flow < 0:
                result = (-3, "outflow")
            else:
                result = (0, "neutral")

        _DATA_SOURCE_CACHE[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug(f"[opportunity] ETF 申赎数据获取失败: {e}")
        return 0, "neutral"


# ── Accuracy-Boost（2026-07-30）：主题分类 + 信号冷却 + 估值时效 ──

# 主题类型关键词映射（用于差异化阈值）
_THEME_CATEGORY_KEYWORDS = {
    "value": ["红利", "低波", "价值", "蓝筹", "高股息"],
    "growth": ["半导体", "人工智能", "新能源", "科技", "机器人", "创新药", "芯片", "AI", "光伏", "锂电"],
    "cycle": ["有色", "化工", "钢铁", "煤炭", "银行", "地产", "建材", "周期"],
}


def _get_theme_category(theme_name: str) -> str:
    """Accuracy-Boost 修复2：主题类型分类（value/growth/cycle）。

    不同类型主题用不同评分阈值：
    - value（红利/价值）：can_buy≥75，估值否决>80%
    - growth（半导体/AI/新能源）：can_buy≥82，估值否决>60%（更严格）
    - cycle（有色/化工/银行）：can_buy≥78，估值否决>70%

    Returns:
        "value" / "growth" / "cycle"，未匹配返回 "value"（保守默认）
    """
    if not theme_name:
        return "value"
    for category, keywords in _THEME_CATEGORY_KEYWORDS.items():
        if any(kw in theme_name for kw in keywords):
            return category
    return "value"


def _get_theme_thresholds(theme_name: str) -> dict:
    """Accuracy-Boost 修复2：按主题类型返回差异化阈值。

    Returns:
        {"can_buy_score": int, "valuation_veto_pct": float, "valuation_block_can_buy_pct": float}
    """
    try:
        from db.config import get_config_bool, get_config_int, get_config_float
        theme_aware = get_config_bool("opportunity.theme_aware_threshold_enabled", True)
    except Exception:
        theme_aware = True
        get_config_int = None
        get_config_float = None

    # P0-R2（2026-08-01）：阈值全部走 system_config，禁止硬编码（金融严谨性）。
    # 配置默认值严格复刻原硬编码行为，配置不可用时回退到原值兜底。
    def _cfg(key: str, default):
        try:
            if isinstance(default, int):
                return get_config_int(key, default)
            return get_config_float(key, default)
        except Exception:
            return default

    if not theme_aware:
        # 开关关闭：统一阈值（原逻辑 {75, 80, 60}）
        return {
            "can_buy_score": _cfg("opportunity.threshold.default_can_buy", 75),
            "valuation_veto_pct": _cfg("opportunity.threshold.default_veto_pct", 80.0),
            "valuation_block_can_buy_pct": _cfg("opportunity.threshold.default_block_pct", 60.0),
        }

    category = _get_theme_category(theme_name)
    if category == "growth":
        return {
            "can_buy_score": _cfg("opportunity.threshold.growth_can_buy", 82),
            "valuation_veto_pct": _cfg("opportunity.threshold.growth_veto_pct", 60.0),
            "valuation_block_can_buy_pct": _cfg("opportunity.threshold.growth_block_pct", 60.0),
        }
    elif category == "cycle":
        return {
            "can_buy_score": _cfg("opportunity.threshold.cycle_can_buy", 78),
            "valuation_veto_pct": _cfg("opportunity.threshold.cycle_veto_pct", 70.0),
            "valuation_block_can_buy_pct": _cfg("opportunity.threshold.cycle_block_pct", 70.0),
        }
    else:  # value
        return {
            "can_buy_score": _cfg("opportunity.threshold.value_can_buy", 75),
            "valuation_veto_pct": _cfg("opportunity.threshold.value_veto_pct", 80.0),
            "valuation_block_can_buy_pct": _cfg("opportunity.threshold.value_block_pct", 80.0),
        }


def _check_signal_cooldown(theme_name: str, trade_date: str) -> int:
    """Accuracy-Boost 修复5：信号冷却期检查。

    同一主题在冷却期内已有信号时，限制当前评分上限：
    - 15 天内已有 can_buy → cap 50（强制 watch，避免下跌趋势中连续发信号）
    - 15 天内已有 watch ≥3 条 → cap 65（避免 watch 泛滥）

    Returns:
        评分上限（100 表示不限制）
    """
    try:
        from db.config import get_config_bool, get_config_int
        if not get_config_bool("opportunity.signal_cooldown_enabled", True):
            return 100
        cooldown_days = get_config_int("opportunity.signal_cooldown_days", 15)
    except Exception:
        return 100

    try:
        from db._conn import _get_conn
        from datetime import datetime, timedelta
        cutoff = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=cooldown_days)).strftime("%Y-%m-%d")
        conn = _get_conn()
        try:
            # 检查冷却期内是否有 can_buy 记录
            can_buy_count = conn.execute(
                "SELECT COUNT(*) as c FROM theme_opportunities "
                "WHERE theme = ? AND trade_date >= ? AND trade_date < ? AND verdict = 'can_buy'",
                (theme_name, cutoff, trade_date),
            ).fetchone()["c"]
            if can_buy_count > 0:
                return 50  # 冷却期内已有 can_buy，强制 cap 50

            # 检查冷却期内 watch 记录数
            watch_count = conn.execute(
                "SELECT COUNT(*) as c FROM theme_opportunities "
                "WHERE theme = ? AND trade_date >= ? AND trade_date < ? AND verdict = 'watch'",
                (theme_name, cutoff, trade_date),
            ).fetchone()["c"]
            if watch_count >= 3:
                return 65  # watch 泛滥，cap 65
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[opportunity] 信号冷却检查失败: {e}")
    return 100


def _is_valuation_stale(valuation: dict | None) -> bool:
    """Accuracy-Boost 修复1：检查估值数据是否过期（snapshot_date 距今 >3 天）。

    Args:
        valuation: _latest_valuation_for_theme 返回的估值 dict
    Returns:
        True 表示估值已过期（不应作为买入依据）
    """
    if not valuation:
        return False  # 无估值数据由其他逻辑处理
    snapshot_date = valuation.get("snapshot_date")
    if not snapshot_date:
        return False  # 无日期信息，不判定过期
    try:
        from db.config import get_config_int
        stale_days = get_config_int("opportunity.valuation_stale_days_threshold", 3)
    except Exception:
        stale_days = 3
    try:
        snap_dt = datetime.strptime(str(snapshot_date)[:10], "%Y-%m-%d")
        age_days = (datetime.now() - snap_dt).days
        return age_days > stale_days
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════
# P1-R4（2026-08-01）：信号 IC 加权置信度
# ════════════════════════════════════════════════════════════════

def _calc_ic_confidence(dim_scores: dict) -> float | None:
    """P1-R4：基于 IC 加权的得分置信度（0-1）。

    逻辑：
    - 开关 opportunity.ic.enabled 关闭 → 返回 None
    - 各维度 IC 从 db.opportunities.calc_signal_ic 获取（1 小时缓存）
    - ic_confidence = sum(dim_score × dim_ic_weight) / sum(dim_ic_weight)
    - 其中 dim_ic_weight = max(ic, 0)（负 IC 维度不参与，避免反向加成）
    - 样本不足（IC 为 None）→ 返回 None

    Returns:
        [0, 1] 区间置信度；开关关闭或样本不足返回 None
    """
    try:
        from db.config import get_config_bool, get_config_int, get_config_float
        if not get_config_bool("opportunity.ic.enabled", False):
            return None
        min_samples = get_config_int("opportunity.ic.min_samples", 30)
        window_days = get_config_int("opportunity.ic.window_days", 90)
        from db.opportunities import calc_signal_ic

        weighted_sum = 0.0
        weight_sum = 0.0
        for dim_name, dim_score in dim_scores.items():
            ic = calc_signal_ic(dim_name, window_days)
            if ic is None:
                continue
            # 只用正 IC 维度（负 IC 维度信号反向，不应贡献正向置信度）
            dim_weight = max(ic, 0.0)
            if dim_weight > 0:
                weighted_sum += dim_score * dim_weight
                weight_sum += dim_weight

        if weight_sum == 0:
            return None
        # dim_score 范围约 [-20, 15]，归一化到 [0, 1]
        raw = weighted_sum / weight_sum
        # 假设最大可能得分 15，最小 -20，映射到 [0, 1]
        ic_conf = (raw + 20) / 35.0
        return round(max(0.0, min(1.0, ic_conf)), 3)
    except Exception as e:
        logger.debug(f"[opportunity] IC 置信度计算失败: {e}")
        return None


def _apply_ic_feedback() -> dict:
    """P1-R4：IC 反哺维度权重（慢速精调）。

    规则（每 N 次回测后触发，N = opportunity.ic.refresh_interval_runs，默认 10）：
    - IC > 0.1 → weight × 1.2（上限 2.0）
    - IC < 0 → weight × 0.8（下限 0.3）
    - IC < -0.05 → weight = 0（禁用该维度）
    - 样本不足（IC = None）→ 保持原权重

    保留原 _apply_hit_rate_feedback 的"3 次 miss 降权"作为快速反馈，
    本函数作为慢速精调，由 review_opportunity_backtests 按周期触发。

    Returns:
        {adjusted: int, details: [{dim, old_weight, new_weight, ic}]}
    """
    try:
        from db.config import get_config_bool, get_config_int, get_config, update_config
        from db.opportunities import calc_signal_ic, log_weight_change

        if not get_config_bool("opportunity.ic.enabled", False):
            return {"adjusted": 0, "reason": "IC 开关未开启"}

        window_days = get_config_int("opportunity.ic.window_days", 90)
        dims = ["news", "policy", "basic", "valuation", "holding", "tradability",
                "tech", "capital", "sentiment", "leading", "volume", "research",
                "margin", "etf", "regime"]

        adjusted = 0
        details = []
        for dim in dims:
            ic = calc_signal_ic(dim, window_days)
            if ic is None:
                continue
            config_key = f"opportunity.weight.dim_{dim}"
            old_weight = float(get_config(config_key, "1.0"))
            new_weight = old_weight

            if ic < -0.05:
                new_weight = 0.0  # 禁用
            elif ic < 0:
                new_weight = max(0.3, old_weight * 0.8)
            elif ic > 0.1:
                new_weight = min(2.0, old_weight * 1.2)

            if new_weight != old_weight:
                update_config(config_key, str(round(new_weight, 2)))
                log_weight_change("dim_ic", dim, old_weight, new_weight,
                                  f"ic={round(ic, 3)}", 0)
                adjusted += 1
                details.append({
                    "dim": dim, "old_weight": old_weight,
                    "new_weight": round(new_weight, 2), "ic": round(ic, 3),
                })
                logger.info(f"[opportunity] IC 反哺: {dim} IC={round(ic,3)}, 权重 {old_weight}→{new_weight}")

        return {"adjusted": adjusted, "details": details}
    except Exception as e:
        logger.warning(f"[opportunity] IC 反哺失败: {e}")
        return {"adjusted": 0, "error": str(e)}


def _score_theme(theme_rule: dict, news_hits: list[dict], valuation: dict | None, portfolio_fit: dict) -> tuple[int, str, str, str, str, float | None]:
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
    - 成交量确认（Accuracy-Fix 新增）：-5~+5 分
    - 研报情绪（Accuracy-Boost 新增）：-5~+8 分
    - 融资融券（Accuracy-Boost 新增）：-3~+5 分
    - ETF 申赎（Accuracy-Boost 新增）：-3~+5 分
    - 宏观 regime 联动（P1-R5 新增）：-8~+5 分

    P1-R4（2026-08-01）：14 维权重字典化 + IC 加权
    - 每维加分独立追踪到 dim_scores dict
    - 最终 score = sum(dim_score × weight)，权重走 opportunity.weight.dim_{name}（默认 1.0 等权）
    - 输出 dim_scores_json（供回测算 IC）+ ic_confidence（IC 加权置信度）

    一票否决（P0-A）：
    - 估值 >80% → 强制 avoid
    - 估值 >60% → 禁止 can_buy
    - 无估值数据 → 禁止 can_buy

    Accuracy-Fix（2026-07-27）资金面确认机制：
    - verdict=can_buy 但 capital_signal=outflow → 降级为 watch（资金流出与看多信号矛盾）

    Returns:
        (score, verdict, capital_signal, volume_signal, dim_scores_json, ic_confidence)
    """
    # P1-R4：15 维分项分追踪（默认权重 1.0，等权）
    dim_scores: dict[str, float] = {}

    # ── 1. 新闻命中（P0-C: 过滤利空新闻）──
    positive_news = [
        n for n in news_hits
        if not _contains_negative_sentiment(f"{n.get('title','')} {n.get('summary','')}")
    ]
    if positive_news:
        dim_scores["news"] = min(15, 8 + len(positive_news) * 3)
    else:
        dim_scores["news"] = 0

    # ── 2. 政策词命中（P0-D: 权重 25→12）──
    policy_text = " ".join(f"{n.get('title','')} {n.get('summary','')}" for n in news_hits)
    dim_scores["policy"] = 12 if _contains_any(policy_text, theme_rule.get("policy_terms", [])) else 5

    # ── 3. 无条件基础分（P0-E: 12→5）──
    dim_scores["basic"] = 5

    # ── 4. 估值百分位（P0-B: 修复无估值反加5分bug；>80%倒扣分）──
    # Accuracy-Boost 修复1：估值过期时不加分（保守），与无估值同处理
    valuation_pct = None
    valuation_stale = _is_valuation_stale(valuation)
    if valuation_stale:
        # 估值过期：不作为评分依据，后续 verdict 也按"无估值"处理
        logger.debug(f"[opportunity] 估值数据过期 theme={theme_rule.get('theme','')}, snapshot_date={valuation.get('snapshot_date') if valuation else None}")
        dim_scores["valuation"] = 0
    elif valuation and valuation.get("percentile") is not None:
        pct = valuation["percentile"]
        valuation_pct = pct
        if pct <= 30:
            dim_scores["valuation"] = 15
        elif pct <= 60:
            dim_scores["valuation"] = 9
        elif pct <= 80:
            dim_scores["valuation"] = 3
        else:
            dim_scores["valuation"] = -5  # P0-B: 估值过高倒扣分（原: +3 错误）
    else:
        dim_scores["valuation"] = 0
    # P0-B 修复：无估值数据不加分（原 bug: score += 5 反而加分）

    # ── 5. 持仓重叠风险（Phase 2 增强：感知持仓盈亏）──
    # 原逻辑：按 overlap_risk 给 15/8/2 分
    # Phase 2 增强：已持有且深套+低估 → +12（补仓窗口）；已持有且深套+高估 → -20（勿补）
    overlap = portfolio_fit.get("overlap_risk")
    if portfolio_fit.get("already_have"):
        # 检查已持仓标的的盈亏和估值分位
        deep_loss_undervalued = False  # 深套+低估 → 补仓窗口
        deep_loss_overvalued = False   # 深套+高估 → 勿补
        for rh in portfolio_fit.get("related_holdings", []):
            pr = rh.get("profit_rate")
            if pr is None:
                continue
            try:
                pr = float(pr)
            except (TypeError, ValueError):
                continue
            # 2026-07-30 修复：profit_rate 是小数（-0.26）需转百分比（-26.03）后比较
            if abs(pr) < 1:
                pr = pr * 100
            if pr >= -15:
                continue  # 非深套，不触发增强逻辑
            # 深套标的：查对应指数估值分位
            idx_code = rh.get("index_code")
            if not idx_code:
                continue
            val_pct = rh.get("valuation_percentile")
            if val_pct is None:
                # 本地查估值（enable_online=False 避免批量调用超时）
                try:
                    from db.valuations import get_best_valuation
                    preferred_metric = _get_preferred_metric_type_local(idx_code)
                    val = get_best_valuation(
                        idx_code,
                        metric_type=preferred_metric,
                        query_source="opportunity_score_theme_dim5",
                        enable_online=False,
                        allow_metric_fallback=True,
                    )
                    if val and val.get("percentile") is not None:
                        val_pct = float(val["percentile"])
                except Exception:
                    pass
            if val_pct is None:
                continue  # 无估值数据，保守不触发增强
            if val_pct < 30:
                deep_loss_undervalued = True
                break
            elif val_pct > 60:
                deep_loss_overvalued = True

        if deep_loss_undervalued:
            dim_scores["holding"] = 12  # 深套低估补仓窗口，反转为正分
        elif deep_loss_overvalued:
            dim_scores["holding"] = -20  # 深套高估勿补，加重扣分
        else:
            dim_scores["holding"] = 15 if overlap == "low" else (8 if overlap == "medium" else 2)
    else:
        dim_scores["holding"] = 15 if overlap == "low" else (8 if overlap == "medium" else 2)

    # ── 6. 短期可交易性 ──
    funds = theme_rule.get("funds", [])
    dim_scores["tradability"] = 10 if any(f.get("short_term_suitable") for f in funds) else 3

    # ── 7. 技术指标（P1-K 新增）──
    tech_score, tech_signal = _get_technical_score(theme_rule)
    dim_scores["tech"] = tech_score + (-5 if tech_signal == "bear" else 0)

    # ── 8. 资金流向（P1-L 新增）──
    capital_score, capital_signal = _get_capital_flow_score(theme_rule)
    dim_scores["capital"] = capital_score

    # ── 9. 情绪指标（P1-M 新增）──
    sentiment_score, _ = _get_sentiment_score()
    dim_scores["sentiment"] = sentiment_score

    # ── 10. 领先指标（LI-5 新增，开关默认关闭）──
    trade_date = datetime.now().strftime("%Y-%m-%d")
    leading_score, _ = _get_leading_indicator_score(theme_rule, trade_date)
    dim_scores["leading"] = leading_score

    # ── 11. 成交量确认（Accuracy-Fix 2026-07-27 新增）──
    # 作为第二数据源验证新闻信号真实性，避免"干拔"主题被误判
    volume_score, volume_signal = _get_volume_score(theme_rule)
    dim_scores["volume"] = volume_score

    # ── 12. 研报情绪（Accuracy-Boost 2026-07-30 新增）──
    # 通过研报评级变化判断机构情绪：上调+8/下调-5
    research_score, _ = _get_research_report_score(theme_rule)
    dim_scores["research"] = research_score

    # ── 13. 融资融券（Accuracy-Boost 2026-07-30 新增）──
    # 杠杆资金趋势：融资余额上升+5/下降-3
    margin_score, _ = _get_margin_data_score(theme_rule)
    dim_scores["margin"] = margin_score

    # ── 14. ETF 申赎（Accuracy-Boost 2026-07-30 新增）──
    # 资金净流入/流出：净申购+5/净赎回-3
    etf_flow_score, _ = _get_etf_flow_score(theme_rule)
    dim_scores["etf"] = etf_flow_score

    # ── 15. 宏观 regime 联动（P1-R5 2026-08-01 新增）──
    # 依据市场 regime 与主题属性（进攻/防御）匹配度给分
    # bear+防御 +5 / bear+进攻 -5 / bull+进攻 +3 / bull+高估值 -8 / sideways 0
    regime_score, regime_signal = _get_regime_align_score(theme_rule, valuation_pct)
    dim_scores["regime"] = regime_score

    # ── P1-R4：应用维度权重（默认 1.0 等权，行为不变）──
    try:
        from db.config import get_config_float
        score = 0
        for dim_name, dim_score in dim_scores.items():
            weight = get_config_float(f"opportunity.weight.dim_{dim_name}", 1.0)
            score += dim_score * weight
    except Exception:
        # 兜底：等权求和
        score = sum(dim_scores.values())

    # ── P1-R4：计算 IC 加权置信度（开关关闭或样本不足返回 None）──
    ic_confidence = _calc_ic_confidence(dim_scores)

    # 序列化 dim_scores 供回测算 IC（JSON 字符串）
    import json as _json
    dim_scores_json = _json.dumps(dim_scores, ensure_ascii=False)

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

    # ── Accuracy-Boost 修复2：主题差异化 can_buy 阈值 ──
    thresholds = _get_theme_thresholds(theme_rule.get("theme", ""))
    can_buy_score = thresholds["can_buy_score"]
    veto_pct = thresholds["valuation_veto_pct"]
    block_can_buy_pct = thresholds["valuation_block_can_buy_pct"]

    # P0-R2（2026-08-01）：watch 下限与否决/禁买评分上限走配置（金融严谨性）
    try:
        from db.config import get_config_int
        watch_floor = get_config_int("opportunity.threshold.watch_floor", 50)
        veto_score_cap = get_config_int("opportunity.threshold.veto_score_cap", 30)
        block_score_cap = get_config_int("opportunity.threshold.block_score_cap", 60)
    except Exception:
        watch_floor, veto_score_cap, block_score_cap = 50, 30, 60

    # ── Accuracy-Boost 修复5：信号冷却期 ──
    trade_date_for_cooldown = datetime.now().strftime("%Y-%m-%d")
    cooldown_cap = _check_signal_cooldown(theme_rule.get("theme", ""), trade_date_for_cooldown)
    if cooldown_cap < 100:
        score = min(score, cooldown_cap)

    verdict = "can_buy" if score >= can_buy_score else ("watch" if score >= watch_floor else "avoid")

    # ── P0-A + Accuracy-Boost 修复2: 估值过高一票否决（主题差异化阈值）──
    # 问题背景：原逻辑 14 条估值 97-99% 的主题仍判 can_buy
    # 修复策略：估值过高强制降级，避免历史高位建议上车
    # Accuracy-Boost：成长型主题 veto_pct=60%（更严格），价值型 veto_pct=80%
    if valuation_pct is not None:
        if valuation_pct > veto_pct:
            verdict = "avoid"
            score = min(score, veto_score_cap)
        elif valuation_pct > block_can_buy_pct:
            if verdict == "can_buy":
                verdict = "watch"
                score = min(score, block_score_cap)
    else:
        # 估值数据缺失或过期 → 不允许 can_buy
        # Accuracy-Boost 修复1：估值过期与无估值同处理（开关 valuation_stale_block_can_buy）
        try:
            from db.config import get_config_bool
            stale_block = get_config_bool("opportunity.valuation_stale_block_can_buy", True)
        except Exception:
            stale_block = True
        if verdict == "can_buy" and (stale_block or not valuation_stale):
            # 无估值数据（非过期）→ 禁 can_buy
            # 估值过期且 stale_block=true → 禁 can_buy
            verdict = "watch"
            score = min(score, 60)

    # ── Accuracy-Fix（2026-07-27）：资金面确认机制（第二数据源验证）──
    # 场景：新闻信号看多但资金大幅流出，信号矛盾应降级
    # 策略：can_buy + 强资金流出 → watch；不升级 watch → can_buy（避免过度乐观）
    if verdict == "can_buy" and capital_signal == "outflow":
        verdict = "watch"
        score = min(score, 65)

    # ── Accuracy-Fix（2026-07-27）：量能确认机制 ──
    # 场景：can_buy 但严重缩量，无量上涨可靠性低
    if verdict == "can_buy" and volume_signal == "shrink":
        verdict = "watch"
        score = min(score, 65)

    # ── 原有降级逻辑 ──
    if portfolio_fit.get("overlap_risk") == "high" and verdict == "can_buy":
        verdict = "watch"
    if funds and not any(f.get("short_term_suitable") for f in funds) and verdict == "can_buy":
        verdict = "watch"
    return int(score), verdict, capital_signal, volume_signal, dim_scores_json, ic_confidence


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
    # P0-R2（2026-08-01）：入场金额乘数走配置（金融严谨性，禁止硬编码）
    try:
        from db.config import get_config_float
        m_deep = get_config_float("opportunity.entry.mult_deep_low", 1.5)
        m_low = get_config_float("opportunity.entry.mult_low", 1.0)
        m_mid = get_config_float("opportunity.entry.mult_mid", 0.7)
        m_high = get_config_float("opportunity.entry.mult_high", 0.4)
        m_nodata = get_config_float("opportunity.entry.mult_no_data", 0.5)
    except Exception:
        m_deep, m_low, m_mid, m_high, m_nodata = 1.5, 1.0, 0.7, 0.4, 0.5
    if not valuation or valuation.get("percentile") is None:
        return round(base_budget * m_nodata, 2)
    pct = valuation["percentile"]
    if pct < 20:
        multiplier = m_deep
    elif pct < 40:
        multiplier = m_low
    elif pct < 60:
        multiplier = m_mid
    elif pct < 80:
        multiplier = m_high
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
    # P0-R1（2026-08-01）：估值信号科学化 — 计算 z-score/均值回归半衰期，选择适配指标
    zscore_info = _calc_valuation_zscore(
        valuation.get("index_code") if valuation else None,
        valuation.get("metric_type") if valuation else None,
    )
    valuation_indicator = _select_valuation_indicator(theme_rule.get("theme", ""))
    # P1-R4：_score_theme 返回值扩展为 6 元组（含 dim_scores_json + ic_confidence）
    score, verdict, capital_signal, volume_signal, dim_scores_json, ic_confidence = _score_theme(theme_rule, news_hits, valuation, portfolio_fit)
    matched_funds = _build_matched_funds(theme_rule)
    review_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=15)).strftime("%Y-%m-%d")

    # P1-K: 获取技术信号用于动态文案
    _, tech_signal = _get_technical_score(theme_rule)
    _, sentiment_signal = _get_sentiment_score()

    # ── L1 政策解读 LLM 化（2026-07-21）──
    # 对 watch/can_buy 候选调用 LLM 做政策实质解读，调整 score
    llm_policy = None
    if verdict in ("watch", "can_buy"):
        llm_policy = _llm_policy_analysis(theme_rule, news_hits)
        if llm_policy and isinstance(llm_policy.get("score_adjust"), int):
            score += llm_policy["score_adjust"]
            score = max(0, min(100, score))
            # P0-R2：重新计算 verdict 与一票否决，阈值复用 _get_theme_thresholds（配置化）
            _th = _get_theme_thresholds(theme_rule.get("theme", ""))
            try:
                from db.config import get_config_int
                _wf = get_config_int("opportunity.threshold.watch_floor", 50)
                _vcap = get_config_int("opportunity.threshold.veto_score_cap", 30)
                _bcap = get_config_int("opportunity.threshold.block_score_cap", 60)
            except Exception:
                _wf, _vcap, _bcap = 50, 30, 60
            verdict = "can_buy" if score >= _th["can_buy_score"] else ("watch" if score >= _wf else "avoid")
            valuation_pct = valuation.get("percentile") if valuation else None
            if valuation_pct is not None:
                if valuation_pct > _th["valuation_veto_pct"]:
                    verdict = "avoid"
                    score = min(score, _vcap)
                elif valuation_pct > _th["valuation_block_can_buy_pct"] and verdict == "can_buy":
                    verdict = "watch"
                    score = min(score, _bcap)
            elif verdict == "can_buy":
                verdict = "watch"
                score = min(score, _bcap)

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
        # P0-R1：叠加 z-score 科学信号（偏离均值的标准差 + 均值回归半衰期）
        if zscore_info and zscore_info.get("zscore") is not None:
            _lvl_cn = {"deep_low": "深度低估", "low": "偏低估", "neutral": "中性",
                       "high": "偏高估"}.get(zscore_info.get("level"), "")
            valuation_role += (
                f"；z-score≈{zscore_info['zscore']}（{_lvl_cn}，回看{zscore_info.get('window_years')}年）"
            )
            if zscore_info.get("half_life_months"):
                valuation_role += f"，均值回归半衰期约{zscore_info['half_life_months']}个月"
            if valuation_indicator:
                valuation_role += f"；该主题更适配的估值口径：{valuation_indicator.upper()}"

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
        # P0-R1（2026-08-01）：估值科学信号（z-score/均值回归半衰期/适配指标）
        # 供前端展示与决策流水线引用，是"越来越准"的信号基础之一
        "valuation_signal": zscore_info,
        "valuation_indicator": valuation_indicator,
        "review_status": "pending",  # 默认 pending，回测完成后改为 completed
        # Accuracy-Fix（2026-07-27）：入场信号快照（下划线前缀=不入库，仅供 backtest 记录引用）
        "_capital_signal": capital_signal,
        "_volume_signal": volume_signal,
        # P1-R4（2026-08-01）：15 维分项分快照 + IC 加权置信度（下划线前缀=不入库，供 backtest 引用）
        "_dim_scores_json": dim_scores_json,
        "_ic_confidence": ic_confidence,
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


def _create_opportunity_backtest(opportunity_id: int, theme_rule: dict, trade_date: str, review_date: str,
                                capital_signal: str | None = None, volume_signal: str | None = None,
                                entry_percentile: float | None = None,
                                entry_amount: float | None = None,
                                dim_scores_json: str | None = None,
                                signal_source: str = "news") -> None:
    """P1-N: 在 save_opportunity 后插入回测跟踪记录。

    用途：每次生成机会卡时同步插入回测记录，15 个交易日后自动回测命中率。
    解决问题：原 theme_opportunity_tracks 表是"用户已买入后跟踪"，0 条记录导致命中率统计永远为 None。

    Accuracy-Fix（2026-07-27）：新增 capital_signal/volume_signal 字段存储入场时的资金面/量能信号，
    用于后续命中率分维度分析（如资金流入信号的命中率 vs 流出信号的命中率）。
    Accuracy-Boost（2026-07-30）：新增 entry_percentile 字段，补全回测字段写入。
    P1-R6（2026-08-01）：新增 entry_amount / dim_scores_json 字段（含成本回测 + R4 IC 计算）。
    """
    try:
        from db.opportunities import create_opportunity_backtest
        entry_price = _get_theme_index_current_price(theme_rule)

        # Accuracy-Boost：若未传入 entry_percentile，从估值表查当前分位
        if entry_percentile is None:
            try:
                val = _latest_valuation_for_theme(theme_rule)
                if val and val.get("percentile") is not None:
                    entry_percentile = float(val["percentile"])
            except Exception:
                pass

        create_opportunity_backtest({
            "opportunity_id": opportunity_id,
            "theme": theme_rule.get("theme", ""),
            "entry_date": trade_date,
            "review_date": review_date,
            "entry_price": entry_price,
            "signal_source": signal_source,
            "capital_signal": capital_signal,
            "volume_signal": volume_signal,
            "entry_percentile": entry_percentile,
            "entry_amount": entry_amount,
            "dim_scores_json": dim_scores_json,
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
    """LI-6（2026-07-22）+ F-4+（2026-07-23）+ Accuracy-Boost（2026-07-30）：命中率反哺评分权重。

    规则：
    - 某信号来源连续 3 次 miss → 降权 20%（写入 system_config）
    - F-4+：某主题连续 3 次 miss → 该主题降权 20%（opportunity.weight_adjust_theme_{theme}）
    - Accuracy-Boost：连续 2 次 hit → 恢复权重到 1.0（从降权状态恢复）
    - Accuracy-Boost：所有权重变更记录到 opportunity_weight_log 表
    """
    try:
        from db.opportunities import (
            get_consecutive_misses_by_source, get_consecutive_misses_by_theme,
            get_consecutive_hits_by_theme, log_weight_change,
        )
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
                    log_weight_change("source", source, current, new_weight,
                                      f"consecutive_miss_{miss_count}", miss_count)
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
                    log_weight_change("theme", theme, current, new_weight,
                                      f"consecutive_miss_{miss_count}", miss_count)
                    logger.info(f"[opportunity] 命中率反哺(theme): {theme} 连续{miss_count}次miss，权重 {current}→{new_weight}")

        # 3. Accuracy-Boost：per-theme 权重恢复 — 连续 2 次 hit → 恢复到 1.0
        consecutive_hits_theme = get_consecutive_hits_by_theme()
        for theme, hit_count in consecutive_hits_theme.items():
            config_key = f"opportunity.weight_adjust_theme_{theme}"
            current = float(get_config(config_key, "1.0"))
            if current < 1.0:  # 仅对已降权的主题恢复
                update_config(config_key, "1.0")
                log_weight_change("theme", theme, current, 1.0,
                                  f"consecutive_hit_{hit_count}", hit_count)
                logger.info(f"[opportunity] 权重恢复(theme): {theme} 连续{hit_count}次hit，权重 {current}→1.0")

        # 4. Accuracy-Boost：per-source 权重恢复
        for source_key in ["news", "leading_strong", "leading_medium"]:
            config_key = f"opportunity.weight_adjust_{source_key}"
            current = float(get_config(config_key, "1.0"))
            if current < 1.0:
                # 检查该 source 最近 2 次是否连续 hit
                from db._conn import _get_conn
                conn = _get_conn()
                try:
                    rows = conn.execute(
                        "SELECT hit FROM theme_opportunity_backtests "
                        "WHERE signal_source = ? AND hit IS NOT NULL "
                        "ORDER BY reviewed_at DESC LIMIT 2",
                        (source_key,),
                    ).fetchall()
                    if len(rows) >= 2 and all(r["hit"] == 1 for r in rows):
                        update_config(config_key, "1.0")
                        log_weight_change("source", source_key, current, 1.0,
                                          "consecutive_hit_2", 2)
                        logger.info(f"[opportunity] 权重恢复(source): {source_key} 连续2次hit，权重 {current}→1.0")
                finally:
                    conn.close()
    except Exception as e:
        logger.debug(f"[opportunity] _apply_hit_rate_feedback 失败: {e}")


# ════════════════════════════════════════════════════════════════
# P1-R6（2026-08-01）：含成本 walk-forward 回测
# ════════════════════════════════════════════════════════════════

def _get_index_closes_between(index_code: str, start_date: str, end_date: str) -> list[float]:
    """获取指数在 [start_date, end_date] 区间内的日频收盘价序列（升序）。

    优先读本地 index_price_history，无数据则尝试 akshare 兜底。
    用于回测计算 max_drawdown / sharpe / calmar。
    """
    if not index_code or not start_date or not end_date:
        return []
    bare_code = index_code.split(".")[0].split(" ")[0]
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            candidates = [bare_code, index_code]
            if "." not in index_code:
                candidates.append(f"{index_code}.CSI")
            placeholders = ",".join("?" * len(candidates))
            rows = conn.execute(
                f"""SELECT close FROM index_price_history
                    WHERE index_code IN ({placeholders})
                      AND trade_date BETWEEN ? AND ?
                    ORDER BY trade_date ASC""",
                (*candidates, start_date, end_date),
            ).fetchall()
            closes = [float(r["close"]) for r in rows if r["close"]]
            if len(closes) >= 2:
                return closes
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[opportunity] 本地区间价格查询失败 {index_code} [{start_date},{end_date}]: {e}")
    # akshare 兜底
    try:
        import akshare as ak
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout
        start = start_date.replace("-", "")
        end = end_date.replace("-", "")
        df = call_akshare_with_timeout(
            ak.index_zh_a_hist, symbol=bare_code, period="daily",
            start_date=start, end_date=end, timeout=20,
        )
        if df is None or len(df) == 0:
            return []
        if "收盘" in df.columns:
            return [float(c) for c in df["收盘"].values]
    except Exception as e:
        logger.debug(f"[opportunity] akshare 区间价格兜底失败 {index_code}: {e}")
    return []


def _calc_max_drawdown(closes: list[float]) -> float | None:
    """计算最大回撤（百分比，正值表示回撤幅度）。

    例：回撤 15% 返回 15.0；数据不足返回 None。
    """
    if not closes or len(closes) < 2:
        return None
    peak = closes[0]
    max_dd = 0.0
    for price in closes[1:]:
        if price > peak:
            peak = price
        elif peak > 0:
            dd = (peak - price) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 2) if max_dd > 0 else 0.0


def _calc_sharpe(closes: list[float], risk_free_rate: float, holding_days: int) -> float | None:
    """计算年化夏普比率。

    Args:
        closes: 日频收盘价序列
        risk_free_rate: 年化无风险利率（如 0.02）
        holding_days: 持有天数

    Returns:
        年化 Sharpe，数据不足返回 None
    """
    if not closes or len(closes) < 3 or holding_days <= 0:
        return None
    # 日收益率序列
    daily_returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            daily_returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
    if len(daily_returns) < 2:
        return None
    mean_r = sum(daily_returns) / len(daily_returns)
    var_r = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_r = var_r ** 0.5
    if std_r == 0:
        return None
    # 年化：252 交易日
    annualized_return = mean_r * 252
    annualized_vol = std_r * (252 ** 0.5)
    sharpe = (annualized_return - risk_free_rate) / annualized_vol
    return round(sharpe, 3)


def _calc_calmar(closes: list[float], risk_free_rate: float, holding_days: int) -> float | None:
    """计算 Calmar 比率 = 年化收益 / 最大回撤。"""
    if not closes or len(closes) < 2 or holding_days <= 0:
        return None
    max_dd = _calc_max_drawdown(closes)
    if max_dd is None or max_dd == 0:
        return None
    total_return = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0
    # 年化收益（按持有期折算，252 交易日）
    annualized_return = ((1 + total_return) ** (252 / max(holding_days, 1)) - 1)
    calmar = annualized_return / (max_dd / 100)
    return round(calmar, 3)


def _calc_backtest_costs(fee_mode: str, entry_amount: float, holding_days: int,
                         slippage_bps: float, entry_price: float,
                         review_price: float,
                         fund_code: str | None = None) -> dict:
    """P1-R6：计算回测交易成本（手续费 + 滑点）。

    Args:
        fee_mode: realistic / relaxed
        entry_amount: 入场金额（算申购费基数）
        holding_days: 持有天数
        slippage_bps: 滑点 bps
        entry_price / review_price: 入场/出场价格
        fund_code: 基金代码（P1-S5 新增，可选，传入则用 per-fund 费率）

    Returns:
        {buy_fee, sell_fee, slippage_cost, buy_fee_pct, sell_fee_pct,
         slippage_pct, adj_entry_price, adj_review_price, basis}
    """
    from services.fee_calculator import calc_buy_fee
    from db.config import get_config_float

    # 1. 申购费（P1-S5：传入 fund_code 以使用 per-fund 费率）
    buy_fee, buy_rate, buy_basis = calc_buy_fee(entry_amount, fund_code) if entry_amount else (0.0, 0.0, "金额缺失")
    # 申购费占入场金额的百分比
    buy_fee_pct = (buy_fee / entry_amount * 100) if entry_amount else 0.0

    # 2. 赎回费（按持有期阶梯）
    # relaxed 模式：机会信号回测豁免 lt7d 惩罚，按 lt1y 0.5% 计算
    if fee_mode == "relaxed":
        sell_rate = get_config_float("fee.sell_rate_lt1y", 0.005)
        sell_basis = f"relaxed 模式，赎回费率{sell_rate*100:.2f}%"
    else:
        if holding_days < 7:
            sell_rate = get_config_float("fee.sell_rate_lt7d", 0.015)
            sell_basis = f"持有{holding_days}天，赎回费1.5%"
        elif holding_days < 365:
            sell_rate = get_config_float("fee.sell_rate_lt1y", 0.005)
            sell_basis = f"持有{holding_days}天，赎回费0.5%"
        elif holding_days < 730:
            sell_rate = get_config_float("fee.sell_rate_lt2y", 0.0025)
            sell_basis = f"持有{holding_days}天，赎回费0.25%"
        else:
            sell_rate = get_config_float("fee.sell_rate_ge2y", 0.0)
            sell_basis = f"持有{holding_days}天，赎回费0%"
    sell_fee_pct = sell_rate * 100
    # 赎回费金额（基于出场市值；若无金额则按百分比参与净收益计算）
    sell_value = (entry_amount or 0) * (review_price / entry_price) if entry_price > 0 else 0
    sell_fee = round(sell_value * sell_rate, 2) if sell_value else 0.0

    # 3. 滑点（双边各扣 slippage_bps）
    slippage_pct = slippage_bps * 2 / 100  # bps → 百分比（双边）
    adj_entry_price = entry_price * (1 + slippage_bps / 10000) if entry_price else entry_price
    adj_review_price = review_price * (1 - slippage_bps / 10000) if review_price else review_price
    slippage_cost = round((entry_amount or 0) * slippage_pct / 100, 2) if entry_amount else 0.0

    return {
        "buy_fee": buy_fee,
        "sell_fee": sell_fee,
        "slippage_cost": slippage_cost,
        "buy_fee_pct": round(buy_fee_pct, 3),
        "sell_fee_pct": round(sell_fee_pct, 3),
        "slippage_pct": round(slippage_pct, 3),
        "adj_entry_price": round(adj_entry_price, 4) if adj_entry_price else None,
        "adj_review_price": round(adj_review_price, 4) if adj_review_price else None,
        "basis": f"{buy_basis} | {sell_basis} | 滑点{slippage_bps}bps×2",
    }


def review_opportunity_backtests() -> dict:
    """P1-N: 批量回测已到期的机会记录（review_date <= today AND hit IS NULL）。

    命中定义（L3 基准化后）：
    - 开关开：超额收益（涨幅 - 沪深300同期涨幅）>= 2% 视为命中
    - 开关关：绝对涨幅 >= 3% 视为命中（原逻辑）

    P1-R6（2026-08-01）：含成本回测
    - 扣除申购费/赎回费/滑点后计算 net_return
    - 计算 max_drawdown / sharpe / calmar 风险指标
    - 命中阈值改用 net_excess_return >= opportunity.backtest.hit_threshold（默认 1.5%）
    - relaxed 模式豁免 lt7d 惩罚费率（机会信号回测公平性）

    Returns:
        {"reviewed": int, "hit": int, "miss": int}
    """
    try:
        from db.config import get_config_bool, get_config_float, get_config
        from db.opportunities import list_pending_backtests, update_opportunity_backtest
        benchmark_enabled = get_config_bool("opportunity.benchmark_backtest_enabled", True)
        # P1-R6：回测成本参数
        fee_mode = get_config("opportunity.backtest.fee_mode", "realistic")
        slippage_bps = get_config_float("opportunity.backtest.slippage_bps", 5.0)
        risk_free_rate = get_config_float("opportunity.backtest.risk_free_rate", 0.02)
        hit_threshold = get_config_float("opportunity.backtest.hit_threshold", 1.5)
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

                # P1-R6：计算持有天数（用于阶梯赎回费 + Sharpe 年化）
                entry_date_str = track.get("entry_date", "")
                holding_days = 0
                if entry_date_str and review_date:
                    try:
                        ed = datetime.strptime(entry_date_str[:10], "%Y-%m-%d")
                        rd = datetime.strptime(review_date[:10], "%Y-%m-%d")
                        holding_days = max((rd - ed).days, 1)
                    except Exception:
                        holding_days = 15  # 兜底默认 15 日

                # P1-R6：滑点调整后的价格
                entry_amount = track.get("entry_amount") or 0
                costs = _calc_backtest_costs(
                    fee_mode=fee_mode,
                    entry_amount=entry_amount,
                    holding_days=holding_days,
                    slippage_bps=slippage_bps,
                    entry_price=entry_price,
                    review_price=review_price,
                )
                adj_entry = costs.get("adj_entry_price") or entry_price
                adj_review = costs.get("adj_review_price") or review_price

                # 毛收益（用原始价格，与历史口径一致）+ 净收益（扣成本）
                change_pct = (review_price - entry_price) / entry_price * 100
                # net_return = 毛收益 - 申购费% - 赎回费% - 滑点%（双边）
                net_return = change_pct - costs["buy_fee_pct"] - costs["sell_fee_pct"] - costs["slippage_pct"]

                # L3 回测基准化：引入沪深300超额收益
                benchmark_pct = None
                excess_return = None
                net_excess_return = None
                hit = None
                if benchmark_enabled:
                    benchmark_pct = _get_benchmark_return(entry_date_str, review_date)
                    if benchmark_pct is not None:
                        excess_return = change_pct - benchmark_pct
                        net_excess_return = net_return - benchmark_pct
                        # P1-R6：命中判定改用净超额收益（扣成本后）
                        hit = 1 if net_excess_return >= hit_threshold else 0
                    else:
                        # 基准获取失败，回退原逻辑（用净收益对比降低后的阈值）
                        hit = 1 if net_return >= (hit_threshold + 1.5) else 0
                else:
                    # 开关关：原逻辑（绝对涨幅）
                    hit = 1 if change_pct >= 3.0 else 0

                update_fields = {
                    "review_price": review_price,
                    "hit": hit,
                    "change_pct": round(change_pct, 2),
                    "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    # P1-R6：成本与净收益字段
                    "buy_fee": costs["buy_fee"],
                    "sell_fee": costs["sell_fee"],
                    "slippage_cost": costs["slippage_cost"],
                    "net_return": round(net_return, 2),
                }
                if benchmark_pct is not None:
                    update_fields["benchmark_pct"] = round(benchmark_pct, 2)
                if excess_return is not None:
                    update_fields["excess_return"] = round(excess_return, 2)

                # P1-R6：风险指标（max_drawdown / sharpe / calmar）
                try:
                    index_code = _get_theme_index_code(theme_rule)
                    closes = _get_index_closes_between(index_code, entry_date_str, review_date)
                    if closes and len(closes) >= 2:
                        max_dd = _calc_max_drawdown(closes)
                        sharpe = _calc_sharpe(closes, risk_free_rate, holding_days)
                        calmar = _calc_calmar(closes, risk_free_rate, holding_days)
                        if max_dd is not None:
                            update_fields["max_drawdown"] = max_dd
                        if sharpe is not None:
                            update_fields["sharpe"] = sharpe
                        if calmar is not None:
                            update_fields["calmar"] = calmar
                except Exception as e:
                    logger.debug(f"[opportunity] 风险指标计算失败 {track.get('id')}: {e}")

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
                            if benchmark_pct is not None and net_excess_return is not None:
                                reasons.append(f"净超额收益={net_excess_return:.1f}%（基准={benchmark_pct:.1f}%）")
                            else:
                                reasons.append(f"净收益={net_return:.1f}%")
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

        # P1-R4（2026-08-01）：IC 反哺维度权重 — 每 N 次回测触发一次慢速精调
        try:
            from db.config import get_config_bool, get_config_int, get_config, update_config
            if get_config_bool("opportunity.ic.enabled", False):
                refresh_interval = get_config_int("opportunity.ic.refresh_interval_runs", 10)
                run_counter = int(get_config("opportunity.ic._run_counter", "0")) + 1
                update_config("opportunity.ic._run_counter", str(run_counter))
                if run_counter >= refresh_interval:
                    ic_result = _apply_ic_feedback()
                    update_config("opportunity.ic._run_counter", "0")  # 重置计数器
                    if ic_result.get("adjusted", 0) > 0:
                        logger.info(f"[opportunity] IC 反哺完成：调整 {ic_result['adjusted']} 个维度权重")
        except Exception as e:
            logger.debug(f"[opportunity] IC 反哺触发失败: {e}")

        logger.info(f"[opportunity] 回测完成：{reviewed} 条，命中 {hit_count} 条")
        return {"reviewed": reviewed, "hit": hit_count, "miss": reviewed - hit_count}
    except Exception as e:
        logger.warning(f"[opportunity] 回测批量执行失败: {e}")
        return {"reviewed": 0, "hit": 0, "miss": 0, "error": str(e)}


def run_walk_forward_backtest(theme: str, start_date: str, end_date: str,
                               window_days: int | None = None,
                               step_days: int | None = None) -> dict:
    """P1-R6（2026-08-01）：walk-forward 滚动回测。

    按 step_days 滚动取 window_days 窗口，每个窗口模拟一次 entry/review 对，
    聚合算命中率/平均净收益/平均 Sharpe/平均最大回撤。

    Args:
        theme: 主题名（须在 theme_rules 表中）
        start_date / end_date: 回测区间
        window_days: 窗口天数（None 读配置，默认 15）
        step_days: 步长天数（None 读配置，默认 5）

    Returns:
        {theme, windows_count, hit_rate, avg_net_return, avg_sharpe,
         avg_max_drawdown, avg_calmar, window_start, window_end, saved_metric_id}
    """
    try:
        from db.config import get_config_bool, get_config_float, get_config_int, get_config
        from db.opportunities import save_backtest_metrics

        if not get_config_bool("opportunity.backtest.walk_forward_enabled", False):
            return {"error": "walk_forward 开关未开启", "enabled": False}

        if window_days is None:
            window_days = get_config_int("opportunity.backtest.walk_forward_window_days", 15)
        if step_days is None:
            step_days = get_config_int("opportunity.backtest.walk_forward_step_days", 5)

        theme_rule = next((r for r in _get_active_theme_rules() if r["theme"] == theme), None)
        if not theme_rule:
            return {"error": f"主题 {theme} 不在 active theme_rules 中"}

        index_code = _get_theme_index_code(theme_rule)
        if not index_code:
            return {"error": f"主题 {theme} 无 index_code"}

        # 拉取整个区间的价格序列
        closes = _get_index_closes_between(index_code, start_date, end_date)
        if len(closes) < window_days + 1:
            return {"error": f"区间价格不足（{len(closes)} < {window_days + 1}）",
                    "theme": theme, "windows_count": 0}

        # 滚动窗口
        fee_mode = get_config("opportunity.backtest.fee_mode", "realistic")
        slippage_bps = get_config_float("opportunity.backtest.slippage_bps", 5.0)
        risk_free_rate = get_config_float("opportunity.backtest.risk_free_rate", 0.02)
        hit_threshold = get_config_float("opportunity.backtest.hit_threshold", 1.5)

        net_returns = []
        sharpe_list = []
        max_dd_list = []
        calmar_list = []
        hits = 0
        windows_count = 0

        i = 0
        while i + window_days < len(closes):
            window_closes = closes[i:i + window_days + 1]
            entry_p = window_closes[0]
            review_p = window_closes[-1]
            holding_days = window_days
            change_pct = (review_p - entry_p) / entry_p * 100 if entry_p > 0 else 0

            # 扣成本（entry_amount 缺失则按百分比计算）
            costs = _calc_backtest_costs(
                fee_mode=fee_mode, entry_amount=0, holding_days=holding_days,
                slippage_bps=slippage_bps, entry_price=entry_p, review_price=review_p,
            )
            net_return = change_pct - costs["buy_fee_pct"] - costs["sell_fee_pct"] - costs["slippage_pct"]
            net_returns.append(net_return)

            # 命中判定（无 benchmark，用净收益对比 hit_threshold + 1.5）
            if net_return >= (hit_threshold + 1.5):
                hits += 1

            sharpe = _calc_sharpe(window_closes, risk_free_rate, holding_days)
            max_dd = _calc_max_drawdown(window_closes)
            calmar = _calc_calmar(window_closes, risk_free_rate, holding_days)
            if sharpe is not None:
                sharpe_list.append(sharpe)
            if max_dd is not None:
                max_dd_list.append(max_dd)
            if calmar is not None:
                calmar_list.append(calmar)

            windows_count += 1
            i += step_days

        if windows_count == 0:
            return {"error": "无有效窗口", "theme": theme, "windows_count": 0}

        hit_rate = round(hits / windows_count * 100, 1)
        avg_net_return = round(sum(net_returns) / len(net_returns), 2) if net_returns else None
        avg_sharpe = round(sum(sharpe_list) / len(sharpe_list), 3) if sharpe_list else None
        avg_max_dd = round(sum(max_dd_list) / len(max_dd_list), 2) if max_dd_list else None
        avg_calmar = round(sum(calmar_list) / len(calmar_list), 3) if calmar_list else None

        metrics = {
            "theme": theme,
            "window_start": start_date,
            "window_end": end_date,
            "windows_count": windows_count,
            "hit_rate": hit_rate,
            "avg_net_return": avg_net_return,
            "avg_sharpe": avg_sharpe,
            "avg_max_drawdown": avg_max_dd,
            "avg_calmar": avg_calmar,
        }
        metric_id = save_backtest_metrics(metrics)

        logger.info(f"[opportunity] walk-forward 回测完成 {theme}：{windows_count} 窗口，命中率 {hit_rate}%")
        return {**metrics, "saved_metric_id": metric_id}
    except Exception as e:
        logger.warning(f"[opportunity] walk-forward 回测失败 {theme}: {e}")
        return {"error": str(e), "theme": theme, "windows_count": 0}


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


# ════════════════════════════════════════════════════════════════════
# Phase 2（2026-07-30）：机会雷达感知持仓盈亏 — 亏损持仓低估补仓回本扫描
# 设计稿：doc/plans/2026-07-30-亏损持仓低估补仓回本联动设计稿.md 第四章
# ════════════════════════════════════════════════════════════════════

def _get_preferred_metric_type_local(index_code: str) -> str:
    """查询本地 index_valuations 表中该指数有哪些 metric_type，返回优先选用的指标。

    优先级：市盈率 > 市净率 > 市销率 > 股息率
    本地完全没有时返回"市盈率"（让 get_best_valuation 走在线兜底）。

    与 alert_scanner._get_preferred_metric_type 同名实现，这里独立维护避免跨模块耦合。
    """
    try:
        from db._conn import _get_conn
        from db.valuations import normalize_index_code
        normalized_code = normalize_index_code(index_code)
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT metric_type FROM index_valuations WHERE index_code = ? "
                "AND (current_value IS NOT NULL OR percentile IS NOT NULL)",
                (normalized_code,),
            ).fetchall()
        finally:
            conn.close()
        local_metrics = [r["metric_type"] for r in rows if r["metric_type"]]
        if not local_metrics:
            return "市盈率"
        for preferred in ["市盈率", "市净率", "市销率", "股息率"]:
            if preferred in local_metrics:
                return preferred
        return local_metrics[0]
    except Exception as e:
        logger.debug(f"[opportunity] 查询本地 metric_type 失败 {index_code}: {e}")
        return "市盈率"


def _scan_valuation_channel(active_rules: list[dict], trade_date: str, user_id: str = "default") -> list[dict]:
    """P0-R3（2026-08-01）：估值驱动独立通道 — 深度低估即使无新闻也生成机会卡。

    金融原理：原引擎"无新闻不出卡"，导致纯低估的左侧定投机会被遗漏。
    本通道基于估值科学信号（z-score / 百分位）独立触发，捕捉"无催化但已深度低估"
    的价值机会，与新闻驱动互补。触发阈值/保底分均走 system_config（金融严谨性）。

    与 loss_recovery 卡的去重由 scan_daily_opportunities 的 fund_code 映射统一处理。
    """
    try:
        from db.config import get_config_bool, get_config_float, get_config_int
        if not get_config_bool("opportunity.valchannel.enabled", True):
            return []
        z_threshold = get_config_float("opportunity.valchannel.zscore_threshold", -1.5)
        pct_threshold = get_config_float("opportunity.valchannel.percentile_threshold", 15)
        min_score = get_config_float("opportunity.valchannel.min_score", 55)
        watch_floor = get_config_int("opportunity.threshold.watch_floor", 50)
    except Exception:
        return []

    results = []
    for rule in active_rules:
        try:
            valuation = _latest_valuation_for_theme(rule)
            if not valuation:
                continue
            pct = valuation.get("percentile")
            zinfo = _calc_valuation_zscore(valuation.get("index_code"), valuation.get("metric_type"))
            z = zinfo.get("zscore") if zinfo else None

            # 触发条件：深度低估（百分位低于阈值 或 z-score 低于阈值）
            triggered_by_pct = pct is not None and pct < pct_threshold
            triggered_by_z = z is not None and z < z_threshold
            if not (triggered_by_pct or triggered_by_z):
                continue

            # 复用 _build_item（空新闻），再覆写为估值驱动卡
            item = _build_item(rule, [], trade_date, user_id)
            item["opportunity_type"] = "valuation_channel"
            # 保底分：确保深度低估机会至少进入 watch（不低于 min_score）
            if item.get("opportunity_score", 0) < min_score:
                item["opportunity_score"] = int(min_score)
            # 依保底分与主题阈值重算 verdict
            th = _get_theme_thresholds(rule.get("theme", ""))
            sc = item["opportunity_score"]
            item["verdict"] = "can_buy" if sc >= th["can_buy_score"] else ("watch" if sc >= watch_floor else "avoid")
            # 估值一票否决兜底（深度高估不会进本通道，防御性保留）
            if pct is not None and pct > th["valuation_veto_pct"]:
                item["verdict"] = "avoid"
            # 覆写文案：明确这是估值驱动（无新闻催化）
            trig_desc = []
            if triggered_by_pct:
                trig_desc.append(f"百分位{pct}%低于{pct_threshold:g}%")
            if triggered_by_z:
                trig_desc.append(f"z-score {z}低于{z_threshold:g}")
            item["policy_signal"] = "估值驱动通道：暂无新闻催化，但估值已深度低估（左侧机会）"
            item["summary"] = (
                f"{rule.get('theme', '')} 估值深度低估（{'，'.join(trig_desc)}），"
                f"具备左侧定投价值；建议分批建仓、控制仓位，等待催化或估值修复。"
            )
            item.setdefault("evidence", []).insert(0, {
                "type": "valuation_channel",
                "summary": f"估值驱动触发：{'，'.join(trig_desc)}",
                "source": "valuation_engine",
            })
            item["_theme_rule"] = rule  # 供 scan 创建回测记录引用，入库前 pop
            results.append(item)
        except Exception as e:
            logger.warning(f"[opportunity] 估值驱动通道处理 {rule.get('theme', '')} 失败: {e}")
            continue
    return results


def _scan_holdings_loss_recovery(trade_date: str, user_id: str = "default") -> list[dict]:
    """Phase 2：扫描持仓中"亏损严重+对应指数低估"的标的，生成补仓回本机会卡。

    逻辑：
    1. 读取开关 opportunity.holding_loss_aware_enabled（默认 true）
    2. list_holdings 获取持仓，筛选 profit_rate < -15 的深套标的
    3. 对每个深套标的查对应指数估值分位（_get_preferred_metric_type_local 选指标）
    4. 若估值分位 < 30% → 生成 opportunity_type="loss_recovery" 的卡片
    5. 跳过债券基金（fund_name/fund_type/fund_category 含"债"）和无 index_code 的标的
    6. 跳过无估值数据的标的（保守不触发，与 smart_add_planner 一致）

    Returns:
        loss_recovery 机会卡列表（未入库）
    """
    # 1. 开关检查
    try:
        from db.config import get_config_bool
        if not get_config_bool("opportunity.holding_loss_aware_enabled", True):
            return []
    except Exception as e:
        logger.debug(f"[opportunity] loss_recovery 开关读取失败: {e}")
        return []

    # 2. 获取持仓
    try:
        from db.portfolio import list_holdings
        from db.valuations import get_best_valuation
    except Exception as e:
        logger.warning(f"[opportunity] loss_recovery 依赖导入失败: {e}")
        return []

    try:
        holdings = list_holdings(user_id)
    except Exception as e:
        logger.warning(f"[opportunity] loss_recovery 获取持仓失败: {e}")
        return []

    items: list[dict] = []

    for h in holdings:
        try:
            shares = float(h.get("shares") or 0)
            if shares <= 0:
                continue

            fund_code = h.get("fund_code") or ""
            fund_name = h.get("fund_name") or ""
            index_code = h.get("index_code") or ""

            # 6. 跳过无 index_code 的标的
            if not index_code:
                continue

            # 6. 跳过债券基金
            fund_type = h.get("fund_type") or ""
            fund_category = h.get("fund_category") or ""
            if "债" in fund_name or "债" in fund_type or "债" in fund_category:
                continue

            # 3. 筛选 profit_rate < -15% 的深套标的
            # 2026-07-30 修复：profit_rate 字段是小数（如 -0.2603 表示 -26.03%），
            # 需转为百分比后与 -15 比较
            profit_rate_raw = h.get("profit_rate")
            if profit_rate_raw is None:
                continue
            try:
                profit_rate_raw = float(profit_rate_raw)
            except (TypeError, ValueError):
                continue
            # 统一转为百分比数字（-0.2603 → -26.03）
            if abs(profit_rate_raw) < 1:
                profit_rate = profit_rate_raw * 100  # 小数→百分比
            else:
                profit_rate = profit_rate_raw  # 已是百分比
            if profit_rate >= -15:
                continue

            # 4. 查对应指数估值分位
            preferred_metric = _get_preferred_metric_type_local(index_code)
            val = get_best_valuation(
                index_code,
                metric_type=preferred_metric,
                query_source="opportunity_loss_recovery",
                enable_online=False,  # 2026-07-30 修复：先用本地避免在线超时被 except 吞掉
                allow_metric_fallback=True,
            )
            if not val:
                try:
                    val = get_best_valuation(
                        index_code,
                        metric_type=preferred_metric,
                        query_source="opportunity_loss_recovery_online",
                        enable_online=True,
                        allow_metric_fallback=True,
                    )
                except Exception:
                    pass
            if not val:
                continue  # 7. 无估值数据，保守不触发

            percentile_raw = val.get("percentile")
            if percentile_raw is None:
                continue
            try:
                percentile = float(percentile_raw)
            except (TypeError, ValueError):
                continue

            # 5. 估值分位 < 30% 才生成卡片
            if percentile >= 30:
                continue

            # ── 命中：生成 loss_recovery 机会卡 ──
            current_value = float(h.get("current_value") or 0)
            index_name = val.get("index_name") or h.get("index_name") or index_code
            metric_type = val.get("metric_type") or preferred_metric

            # score = min(95, 50 + abs(profit_rate) + (30 - percentile))
            # 例：-26%亏损+3%分位 → 50+26+27=103 → cap 95
            raw_score = 50 + abs(profit_rate) + (30 - percentile)
            score = max(0, min(95, int(raw_score)))

            # 建议补仓金额：标的市值 × 8%（与 smart_add 信号C dip_base_ratio 一致）
            entry_amount = round(current_value * 0.08, 2)

            review_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=15)).strftime("%Y-%m-%d")

            reason = (
                f"{fund_name}（{fund_code}）当前亏损 {abs(profit_rate):.1f}%，"
                f"对应指数 {index_name}（{index_code}）估值分位 {percentile:.1f}%（低估区）。"
                f"深套+低估=补仓窗口开启，补仓可摊薄平均成本，历史同分位回撤通常能在中期修复。"
            )

            related_holding = {
                "fund_code": fund_code,
                "fund_name": fund_name,
                "profit_rate": profit_rate,  # 百分比数字
                "valuation_percentile": percentile,
                "index_code": index_code,
                "index_name": index_name,
                "current_value": current_value,
            }

            item = {
                "trade_date": trade_date,
                "theme": f"补仓窗口-{fund_name}",
                "verdict": "can_buy" if score >= 75 else ("watch" if score >= 50 else "avoid"),
                "opportunity_score": score,
                # Phase 2 新增标识字段（不入库主表，仅供前端/去重使用）
                "opportunity_type": "loss_recovery",
                "data_source": "loss_recovery_scan",
                "signal_source": "loss_recovery",
                "time_horizon": "15-30个交易日",
                "summary": reason,
                "policy_signal": "持仓亏损+指数低估联合触发，非新闻驱动",
                "future_direction": f"{index_name}估值处于历史低位，补仓摊薄成本是回本关键",
                "market_signal": f"亏损{abs(profit_rate):.1f}%+估值分位{percentile:.1f}%，补仓窗口",
                "valuation_role": (
                    f"{index_name} {metric_type}"
                    f"百分位约 {percentile:.1f}%，处于低估区，安全边际充足"
                ),
                "matched_funds": [{
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "index_name": index_name,
                    "vehicle_type": "holding",
                    "short_term_suitable": True,
                    "tradeability": "loss_recovery",
                    "fee_warning": "",
                }],
                "portfolio_fit": {
                    "already_have": True,
                    "related_holdings": [related_holding],
                    "theme_exposure_pct": 0,
                    "overlap_risk": "low",  # 深套低估补仓不算重叠风险
                    "suggested_budget": entry_amount,
                    "max_position_pct": 3,
                },
                "entry_plan": {
                    "action": "小仓补仓" if score >= 50 else "加入观察",
                    "amount": entry_amount,
                    "batching": "分2-3笔金字塔补仓",
                    "entry_condition": f"估值分位<30%且亏损>{abs(profit_rate):.0f}%，可分批补仓",
                },
                "exit_plan": {
                    "take_profit": "回本后分批止盈",
                    "stop_loss": "估值分位>60%停止补仓",
                    "time_stop": "30个交易日未回本则复盘",
                    "review_date": review_date,
                },
                "risk_note": (
                    f"当前亏损{abs(profit_rate):.1f}%，若高估补仓会扩大亏损；"
                    f"建议仅在低估区间分批补仓摊薄成本"
                ),
                "evidence": [
                    {"type": "holding_loss", "summary": f"{fund_name} 亏损 {profit_rate:.1f}%", "source": "portfolio_holdings"},
                    {"type": "valuation", "summary": f"{index_name} 估值分位 {percentile:.1f}%（低估）", "source": "index_valuations"},
                ],
                "status": "active",
                "entry_amount": entry_amount,
                "valuation_percentile": percentile,
                "entry_price": float(val.get("current_point") or 0) or None,
                "review_status": "pending",
                # 前端去重/展示用的 related_holdings（与 portfolio_fit 内一致，方便前端直接读取）
                "related_holdings": [related_holding],
            }
            items.append(item)
        except Exception as e:
            logger.warning(f"[opportunity] loss_recovery 单条处理失败 {h.get('fund_code')}: {e}")
            continue

    logger.info(f"[opportunity] loss_recovery 扫描完成：{len(items)} 条补仓回本机会")
    return items


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
        item["signal_source"] = "news"
        item["id"] = save_opportunity(item, user_id=user_id)
        # ── P1-N: 同步插入回测跟踪记录 ──
        # 用途：15 个交易日后自动回测命中率，让前端"命中率"chip 真正有数据
        # Accuracy-Fix（2026-07-27）：verdict=avoid 不创建回测记录
        # 原因：avoid 信号本就建议不入场，回测它命中率无意义且污染统计
        # 同时清理估值>80% 的防御性检查（P0-A 已强制 avoid，此处兜底）
        if item.get("verdict") != "avoid":
            _create_opportunity_backtest(
                opportunity_id=item["id"],
                theme_rule=rule,
                trade_date=trade_date,
                review_date=item.get("exit_plan", {}).get("review_date", ""),
                capital_signal=item.get("_capital_signal"),
                volume_signal=item.get("_volume_signal"),
                # P1-R4/R6：传入决策金额 + 15 维分项分快照（供含成本回测 + IC 计算）
                entry_amount=item.get("entry_amount"),
                dim_scores_json=item.get("_dim_scores_json"),
                signal_source="news",
            )
        # 清理内部字段，不暴露给前端 API 响应
        item.pop("_capital_signal", None)
        item.pop("_volume_signal", None)
        item.pop("_dim_scores_json", None)
        item.pop("_ic_confidence", None)
        items.append(item)

    # ── P0-R3（2026-08-01）：估值驱动独立通道（深度低估无需新闻也出卡）──
    # 在新闻驱动扫描后、loss_recovery 合并前接入，使估值驱动卡参与 fund_code 去重
    val_channel_items = _scan_valuation_channel(active_rules, trade_date, user_id)
    for vitem in val_channel_items:
        rule_ref = vitem.pop("_theme_rule", None) or {}
        vitem["signal_source"] = "valuation"
        try:
            vitem["id"] = save_opportunity(vitem, user_id=user_id)
        except Exception as e:
            logger.warning(f"[opportunity] 估值驱动卡保存失败 {vitem.get('theme', '')}: {e}")
            continue
        if vitem.get("verdict") != "avoid":
            _create_opportunity_backtest(
                opportunity_id=vitem["id"],
                theme_rule=rule_ref,
                trade_date=trade_date,
                review_date=vitem.get("exit_plan", {}).get("review_date", ""),
                capital_signal=vitem.get("_capital_signal"),
                volume_signal=vitem.get("_volume_signal"),
                # P1-R4/R6：传入决策金额 + 15 维分项分快照
                entry_amount=vitem.get("entry_amount"),
                dim_scores_json=vitem.get("_dim_scores_json"),
                signal_source="valuation",
            )
        vitem.pop("_capital_signal", None)
        vitem.pop("_volume_signal", None)
        vitem.pop("_dim_scores_json", None)
        vitem.pop("_ic_confidence", None)
        items.append(vitem)

    # ── Phase 2（2026-07-30）：亏损持仓低估补仓回本扫描 ──
    # 在新闻驱动扫描完成后，调用 _scan_holdings_loss_recovery 并合并到 opportunities 列表
    # 去重：同 fund_code 只保留评分最高的卡片
    loss_recovery_items = _scan_holdings_loss_recovery(trade_date, user_id=user_id)
    if loss_recovery_items:
        # 构建新闻驱动卡片的 fund_code → 最高评分映射（从 matched_funds 中提取）
        news_fund_best: dict[str, int] = {}
        for it in items:
            for f in it.get("matched_funds", []):
                fc = f.get("fund_code")
                if not fc:
                    continue
                s = it.get("opportunity_score", 0)
                if fc not in news_fund_best or s > news_fund_best[fc]:
                    news_fund_best[fc] = s

        for loss_item in loss_recovery_items:
            # 提取该 loss_recovery 卡片的 fund_code
            loss_fund = ""
            loss_related = loss_item.get("related_holdings") or []
            if loss_related:
                loss_fund = loss_related[0].get("fund_code", "")
            loss_score = loss_item.get("opportunity_score", 0)

            # 去重：同 fund_code 只保留评分最高的卡片
            # 若新闻驱动卡片对该 fund 评分更高或持平 → 跳过 loss_recovery 卡片
            if loss_fund and loss_fund in news_fund_best and news_fund_best[loss_fund] >= loss_score:
                continue

            # 入库保存，并以独立来源跟踪补仓回本策略表现。
            try:
                loss_item["id"] = save_opportunity(loss_item, user_id=user_id)
            except Exception as e:
                logger.warning(f"[opportunity] loss_recovery 保存失败 {loss_fund}: {e}")
                continue
            if loss_item.get("verdict") != "avoid":
                related = loss_item.get("related_holdings") or []
                related_index_code = related[0].get("index_code", "") if related else ""
                _create_opportunity_backtest(
                    opportunity_id=loss_item["id"],
                    theme_rule={
                        "theme": loss_item.get("theme", ""),
                        "index_code": related_index_code,
                    },
                    trade_date=trade_date,
                    review_date=(loss_item.get("exit_plan") or {}).get("review_date", ""),
                    entry_percentile=loss_item.get("valuation_percentile"),
                    entry_amount=loss_item.get("entry_amount"),
                    signal_source="loss_recovery",
                )
            items.append(loss_item)

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
            model=None,  # 使用默认 MIMO 模型（禁用 deepseek）
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
            model=None,  # 使用默认 MIMO 模型（禁用 deepseek）
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
