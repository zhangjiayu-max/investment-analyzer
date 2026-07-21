"""关注列表多维信号接入 — P0-1（2026-07-21）

将 opportunity_engine 已有的多维评分能力（技术/资金/情绪）复用到 watchlist 信号灯判定，
解决"关注机会纯估值单一维度"的核心缺陷。

设计：
- 仅当 signal_status 已是 green/yellow 时触发多维查询（red/gray 不查，降本）
- akshare 调用加超时（默认 5s），失败时降级到纯估值判断
- 多维结果 5 分钟缓存（同 _patrol_cache 周期）
- 信号灯降级规则：green + tech=bear → yellow；yellow + tech=bull → green
- signal_confidence 计算（0-100）：估值偏离度 40% + 技术 20% + 资金 20% + 情绪 20%
"""

import logging
import time
import json
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 多维信号缓存：{index_key: (timestamp, result_dict)}
_multidim_cache = {}
_multidim_cache_lock = threading.Lock()
_CACHE_TTL = 5 * 60  # 5 分钟缓存


def _build_theme_rule_from_index(index_name: str, index_code: str = None) -> dict:
    """根据 watchlist 项的 index_name/index_code 构造一个虚拟 theme_rule。

    opportunity_engine._get_technical_score 接受 theme_rule dict，
    通过 _get_theme_index_code(theme_rule) 获取指数代码。

    策略：
    1. 若 index_code 已提供，直接用
    2. 否则用 index_name 反查 _THEME_INDEX_CODES
    3. 否则用 index_name 本身（akshare 可能识别）
    """
    from services.advisor.opportunity_engine import _THEME_INDEX_CODES
    theme_rule = {"theme": index_name or ""}
    if index_code:
        # 直接构造映射：把 index_name 当 theme，index_code 当映射值
        # 通过 monkey-patch 方式不行，改为直接传 index_code 到 theme_rule
        theme_rule["_direct_index_code"] = index_code
    else:
        # 反查 _THEME_INDEX_CODES
        for theme, code in _THEME_INDEX_CODES.items():
            if index_name and theme in index_name:
                theme_rule = {"theme": theme}
                break
    return theme_rule


def _get_index_code_for_multidim(index_name: str, index_code: str = None) -> str:
    """获取多维信号查询要用的指数代码。"""
    if index_code:
        # 去后缀
        return index_code.split(".")[0]
    if not index_name:
        return ""
    from services.advisor.opportunity_engine import _THEME_INDEX_CODES
    for theme, code in _THEME_INDEX_CODES.items():
        if theme in index_name:
            return code.split(".")[0]
    # 退化：用 index_name 反查 index_valuations 表
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            search_term = index_name.replace("指数", "").replace("中证", "").replace("全指", "").replace("上证", "").strip()
            row = conn.execute(
                "SELECT DISTINCT index_code FROM index_valuations WHERE index_name LIKE ? OR index_code LIKE ? LIMIT 1",
                (f"%{search_term}%", f"%{search_term}%"),
            ).fetchone()
            if row and row["index_code"]:
                return row["index_code"].split(".")[0]
        finally:
            conn.close()
    except Exception:
        pass
    return ""


def _compute_multidim_signals(index_name: str, index_code: str = None,
                               timeout_seconds: int = 5) -> dict:
    """计算多维信号（技术/资金/情绪）。

    Returns:
        {
            "tech_signal": "bull|bear|neutral",
            "tech_score": int,         # 0-15
            "capital_signal": "inflow|outflow|neutral",
            "capital_score": int,      # -5~+10
            "sentiment_signal": "fear|greed|neutral",
            "sentiment_score": int,    # -5~+10
            "reasons": list[str],
            "fetched_at": str,
        }
    """
    cache_key = f"{index_name}|{index_code}"
    now = time.time()
    with _multidim_cache_lock:
        cached = _multidim_cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    bare_code = _get_index_code_for_multidim(index_name, index_code)
    if not bare_code:
        result = _empty_multidim("无指数代码")
        _set_cache(cache_key, result)
        return result

    # 构造虚拟 theme_rule，让 opportunity_engine 能识别
    from services.advisor.opportunity_engine import _THEME_INDEX_CODES
    theme_rule = {"theme": ""}
    for theme, code in _THEME_INDEX_CODES.items():
        if code.split(".")[0] == bare_code:
            theme_rule = {"theme": theme}
            break
    # 若 _THEME_INDEX_CODES 没匹配上，直接 hack _get_theme_index_code 的输出
    # 通过注入 _direct_index_code 让 wrapper 识别
    theme_rule["_direct_index_code"] = bare_code

    reasons = []
    tech_signal = "neutral"
    tech_score = 0
    capital_signal = "neutral"
    capital_score = 0
    sentiment_signal = "neutral"
    sentiment_score = 0

    # 1. 技术指标
    try:
        from services.advisor.opportunity_engine import _get_technical_score, _get_theme_index_code
        # 临时 monkey-patch 让 _get_theme_index_code 返回我们的 bare_code
        original_fn = _get_theme_index_code
        if theme_rule.get("theme") == "":
            # 直接调用底层 akshare，避免 _get_theme_index_code 返回空
            tech_score, tech_signal = _fetch_technical_score_direct(bare_code, timeout_seconds)
        else:
            tech_score, tech_signal = _get_technical_score(theme_rule)
        if tech_signal != "neutral":
            reasons.append(f"技术面{('多头' if tech_signal == 'bull' else '看空')}{'+' if tech_score > 0 else ''}{tech_score}")
    except Exception as e:
        logger.debug(f"[wl_multidim] 技术指标获取失败 {bare_code}: {e}")

    # 2. 资金流向（北向资金，全局指标，不依赖主题）
    try:
        from services.advisor.opportunity_engine import _get_capital_flow_score
        capital_score, capital_signal = _get_capital_flow_score(theme_rule)
        if capital_signal != "neutral":
            reasons.append(f"资金{'净流入' if capital_signal == 'inflow' else '净流出'}{'+' if capital_score > 0 else ''}{capital_score}")
    except Exception as e:
        logger.debug(f"[wl_multidim] 资金流向获取失败: {e}")

    # 3. 情绪指标（全局市场状态）
    try:
        from services.advisor.opportunity_engine import _get_sentiment_score
        sentiment_score, sentiment_signal = _get_sentiment_score()
        if sentiment_signal != "neutral":
            label = "恐惧" if sentiment_signal == "fear" else "贪婪"
            reasons.append(f"市场情绪{label}{'+' if sentiment_score > 0 else ''}{sentiment_score}")
    except Exception as e:
        logger.debug(f"[wl_multidim] 情绪指标获取失败: {e}")

    result = {
        "tech_signal": tech_signal,
        "tech_score": int(tech_score),
        "capital_signal": capital_signal,
        "capital_score": int(capital_score),
        "sentiment_signal": sentiment_signal,
        "sentiment_score": int(sentiment_score),
        "reasons": reasons,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _set_cache(cache_key, result)
    return result


def _set_cache(key: str, value: dict) -> None:
    """写入缓存。"""
    with _multidim_cache_lock:
        _multidim_cache[key] = (time.time(), value)
        # 清理过期项（超过 50 个时清理最早的）
        if len(_multidim_cache) > 50:
            now = time.time()
            for k in list(_multidim_cache.keys()):
                if now - _multidim_cache[k][0] > _CACHE_TTL * 2:
                    del _multidim_cache[k]


def _empty_multidim(reason: str = "") -> dict:
    """返回空多维信号结果。"""
    return {
        "tech_signal": "neutral",
        "tech_score": 0,
        "capital_signal": "neutral",
        "capital_score": 0,
        "sentiment_signal": "neutral",
        "sentiment_score": 0,
        "reasons": [reason] if reason else [],
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _fetch_technical_score_direct(bare_code: str, timeout_seconds: int = 5) -> tuple:
    """直接调用 akshare 获取技术指标（绕过 _get_theme_index_code）。

    复用 opportunity_engine 的 _ema / _rsi 算法。
    """
    import akshare as ak
    from services.advisor.opportunity_engine import _ema, _rsi
    df = ak.stock_zh_index_daily(symbol=bare_code)
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

    score = 0
    if macd_bull:
        score += 5
    if rsi_bull:
        score += 5
    if ma_bull:
        score += 5

    signal = "bull" if score >= 10 else ("bear" if score == 0 else "neutral")
    return score, signal


def adjust_signal_by_multidim(signal_status: str, signal_reason: str,
                                multidim: dict) -> tuple:
    """根据多维信号调整信号灯状态（降级/升级）。

    Args:
        signal_status: 原始估值信号（green/yellow/red/gray）
        signal_reason: 原始信号原因
        multidim: _compute_multidim_signals 返回的多维结果

    Returns:
        (adjusted_status, adjusted_reason, reasons_list)
    """
    if not multidim:
        return signal_status, signal_reason, []

    tech = multidim.get("tech_signal", "neutral")
    capital = multidim.get("capital_signal", "neutral")
    sentiment = multidim.get("sentiment_signal", "neutral")
    reasons = list(multidim.get("reasons", []))

    adjusted = signal_status
    extra_notes = []

    # green + 技术看空 → 降级 yellow
    if signal_status == "green" and tech == "bear":
        adjusted = "yellow"
        extra_notes.append("⚠️ 技术面看空，降级为接近上车")
    # green + 资金流出 → 维持 green 但提示
    elif signal_status == "green" and capital == "outflow":
        extra_notes.append("⚠️ 资金流出，谨慎上车")
    # yellow + 技术多头 → 升级 green
    elif signal_status == "yellow" and tech == "bull":
        adjusted = "green"
        extra_notes.append("✓ 技术面确认，升级为可上车")

    # 情绪极端时附加提示
    if signal_status == "green" and sentiment == "greed":
        extra_notes.append("⚠️ 市场过热，注意情绪反转风险")
    elif signal_status in ("green", "yellow") and sentiment == "fear":
        extra_notes.append("✓ 市场恐惧，逆向布局机会")

    final_reason = signal_reason
    if extra_notes:
        final_reason = f"{signal_reason}（{'，'.join(extra_notes)}）" if signal_reason else "，".join(extra_notes)

    return adjusted, final_reason, reasons


def compute_signal_confidence(signal_status: str, multidim: dict) -> int:
    """计算信号置信度（0-100）。

    权重：
    - 估值偏离度 40%：green=80、yellow=55、red=20、gray=0
    - 技术面 20%：bull=100、neutral=50、bear=10
    - 资金面 20%：inflow=90、neutral=50、outflow=20
    - 情绪面 20%：fear=80（逆向加分）、neutral=50、greed=30

    无多维数据时仅按估值偏离度计算，confidence 上限 60。
    """
    base_map = {"green": 80, "yellow": 55, "red": 20, "gray": 0}
    base = base_map.get(signal_status, 50)

    if not multidim or multidim.get("tech_signal") == "neutral" and multidim.get("capital_signal") == "neutral" and multidim.get("sentiment_signal") == "neutral":
        # 无多维数据，confidence 上限 60
        return min(60, int(base * 0.4 / 0.4))  # = min(60, base)

    tech_map = {"bull": 100, "neutral": 50, "bear": 10}
    capital_map = {"inflow": 90, "neutral": 50, "outflow": 20}
    sentiment_map = {"fear": 80, "neutral": 50, "greed": 30}

    tech_score = tech_map.get(multidim.get("tech_signal", "neutral"), 50)
    capital_score = capital_map.get(multidim.get("capital_signal", "neutral"), 50)
    sentiment_score = sentiment_map.get(multidim.get("sentiment_signal", "neutral"), 50)

    confidence = int(base * 0.4 + tech_score * 0.2 + capital_score * 0.2 + sentiment_score * 0.2)
    return max(0, min(100, confidence))
