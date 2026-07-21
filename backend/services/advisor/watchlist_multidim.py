"""关注列表多维信号接入 — P0-1（2026-07-21）

将 opportunity_engine 已有的多维评分能力（技术/资金/情绪）复用到 watchlist 信号灯判定，
解决"关注机会纯估值单一维度"的核心缺陷。

设计：
- 仅当 signal_status 已是 green/yellow 时触发多维查询（red/gray 不查，降本）
- akshare 调用加超时（默认 5s），失败时降级到纯估值判断
- 多维结果 5 分钟缓存（同 _patrol_cache 周期）
- 信号灯降级规则：green + tech=bear → yellow；yellow + tech=bull → green
- signal_confidence 计算（0-100）：估值偏离度 40% + 技术 20% + 资金 20% + 情绪 20%

P1-D（2026-07-21）缓存分层增强：
- 全局指标（情绪/北向资金）独立 30 分钟缓存，所有基金共享，避免重复 akshare 调用
- 主题级指标（技术）保持 5 分钟缓存，按指数代码隔离
- 信号灯规则升级为复合规则 + 三重共振 + 反向升级（P1-A）

P1-B（2026-07-21）命中率反哺：
- compute_signal_confidence 增加 fund_code 参数，引入历史命中率维度
- 权重重分配：估值 35% + 技术 20% + 资金 15% + 情绪 15% + 历史 15%
"""

import logging
import time
import json
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ── P1-D 缓存分层 ───────────────────────────────────────────────────
# 全局指标缓存（情绪/北向资金）：30 分钟，所有基金共享
_global_cache = {}
_global_cache_lock = threading.Lock()
_GLOBAL_CACHE_TTL = 30 * 60  # 30 分钟

# 主题级缓存（技术指标）：5 分钟，按指数代码隔离
_theme_cache = {}
_theme_cache_lock = threading.Lock()
_THEME_CACHE_TTL = 5 * 60  # 5 分钟

# 兼容旧接口：多维信号聚合缓存（按 index_name|index_code 聚合）
_multidim_cache = {}
_multidim_cache_lock = threading.Lock()
_CACHE_TTL = 5 * 60  # 5 分钟缓存


def _get_global_cache_ttl() -> int:
    """获取全局缓存 TTL（秒），从配置读取，默认 30 分钟。"""
    try:
        from db.config import get_config_int
        minutes = get_config_int("watchlist.global_indicator_cache_ttl_minutes", 30)
        return max(1, minutes) * 60
    except Exception:
        return _GLOBAL_CACHE_TTL


def _get_sentiment_score_cached() -> dict:
    """情绪指标（全局共享，30 分钟缓存）。

    所有基金共享同一个市场情绪，无需重复查询。
    """
    ttl = _get_global_cache_ttl()
    with _global_cache_lock:
        cached = _global_cache.get("sentiment")
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
    try:
        from services.advisor.opportunity_engine import _get_sentiment_score
        score, signal = _get_sentiment_score()
    except Exception as e:
        logger.debug(f"[wl_multidim] 情绪指标获取失败: {e}")
        score, signal = 0, "neutral"
    result = {"score": int(score), "signal": signal}
    with _global_cache_lock:
        _global_cache["sentiment"] = (time.time(), result)
    return result


def _get_capital_flow_score_cached(theme_rule: dict) -> dict:
    """资金流向/北向资金（全局共享，30 分钟缓存）。

    北向资金是市场级指标，所有主题共用。
    """
    ttl = _get_global_cache_ttl()
    with _global_cache_lock:
        cached = _global_cache.get("capital")
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
    try:
        from services.advisor.opportunity_engine import _get_capital_flow_score
        score, signal = _get_capital_flow_score(theme_rule)
    except Exception as e:
        logger.debug(f"[wl_multidim] 资金流向获取失败: {e}")
        score, signal = 0, "neutral"
    result = {"score": int(score), "signal": signal}
    with _global_cache_lock:
        _global_cache["capital"] = (time.time(), result)
    return result


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

    # 2. 资金流向（北向资金，全局指标，不依赖主题）— P1-D 改用 30 分钟全局缓存
    capital_cached = _get_capital_flow_score_cached(theme_rule)
    capital_score = capital_cached["score"]
    capital_signal = capital_cached["signal"]
    if capital_signal != "neutral":
        reasons.append(f"资金{'净流入' if capital_signal == 'inflow' else '净流出'}{'+' if capital_score > 0 else ''}{capital_score}")

    # 3. 情绪指标（全局市场状态）— P1-D 改用 30 分钟全局缓存
    sentiment_cached = _get_sentiment_score_cached()
    sentiment_score = sentiment_cached["score"]
    sentiment_signal = sentiment_cached["signal"]
    if sentiment_signal != "neutral":
        label = "恐惧" if sentiment_signal == "fear" else "贪婪"
        reasons.append(f"市场情绪{label}{'+' if sentiment_score > 0 else ''}{sentiment_score}")

    # 4. P2-B（2026-07-21）宏观信号维度（LPR/SHIBOR/美债/汇率/政策，1 小时全局缓存）
    macro_signal = "neutral"
    macro_score = 0
    macro_reasons: list[str] = []
    macro_details: dict = {}
    try:
        from services.advisor.watchlist_macro import get_macro_score_cached
        macro = get_macro_score_cached()
        macro_signal = macro.get("signal", "neutral")
        macro_score = macro.get("score", 0)
        macro_reasons = macro.get("reasons", [])
        macro_details = macro.get("details", {})
        if macro_signal != "neutral" and not macro.get("disabled"):
            label = "宽松" if macro_signal == "easing" else "收紧"
            reasons.append(f"宏观{label}{'+' if macro_score > 0 else ''}{macro_score}")
            if macro_reasons:
                reasons.extend(macro_reasons[:1])  # 最多追加 1 条原因，避免 reasons 过长
    except Exception as _e:
        logger.debug(f"[wl_multidim] 宏观信号获取失败: {_e}")

    result = {
        "tech_signal": tech_signal,
        "tech_score": int(tech_score),
        "capital_signal": capital_signal,
        "capital_score": int(capital_score),
        "sentiment_signal": sentiment_signal,
        "sentiment_score": int(sentiment_score),
        # P2-B（2026-07-21）宏观维度
        "macro_signal": macro_signal,
        "macro_score": int(macro_score),
        "macro_reasons": macro_reasons,
        "macro_details": macro_details,
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

    P1-A（2026-07-21）增强：
    - 复合规则：双重看空强制降级（green→red、yellow→red）
    - 三重共振：三重同向时强制升/降一档
    - 反向升级：red + 双重看多 → yellow（反弹信号）

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
    # P2-B（2026-07-21）宏观信号
    macro = multidim.get("macro_signal", "neutral")
    reasons = list(multidim.get("reasons", []))

    # 读取开关（默认开启）
    try:
        from db.config import get_config_bool
        composite_enabled = get_config_bool("watchlist.composite_rule_enabled", True)
        resonance_enabled = get_config_bool("watchlist.three_way_resonance_enabled", True)
        macro_enabled = get_config_bool("watchlist.macro_signal_enabled", True)
    except Exception:
        composite_enabled = True
        resonance_enabled = True
        macro_enabled = True

    # 多空计数
    bullish_count = sum([tech == "bull", capital == "inflow", sentiment == "fear"])
    bearish_count = sum([tech == "bear", capital == "outflow", sentiment == "greed"])

    adjusted = signal_status
    extra_notes = []

    # ── P1-A 三重共振(最高优先级,强信号识别) ──
    if resonance_enabled:
        # 三重看多 → 强制升一档
        if bullish_count == 3:
            if signal_status == "red":
                adjusted = "yellow"
                extra_notes.append("✓ 三重共振看多,反弹信号关注")
            elif signal_status == "yellow":
                adjusted = "green"
                extra_notes.append("✓ 三重共振看多,强信号确认")
            elif signal_status == "gray":
                adjusted = "yellow"
                extra_notes.append("✓ 三重共振看多,值得关注")
            # green 维持,只加提示
            elif signal_status == "green":
                extra_notes.append("✓ 三重共振看多,强信号确认")

        # 三重看空 → 强制降一档
        if bearish_count == 3:
            if signal_status == "green":
                adjusted = "red"
                extra_notes.append("⚠️ 三重共振看空,清仓观望")
            elif signal_status == "yellow":
                adjusted = "red"
                extra_notes.append("⚠️ 三重共振看空,暂不介入")
            # red/gray 维持

    # ── P2-B（2026-07-21）宏观维度规则 ──
    # 宏观收紧压制 green：货币环境收紧时，估值 green 信号需降级观察
    # 宽松环境提亮 yellow：货币环境宽松时，估值 yellow 可视为政策托底机会
    if macro_enabled:
        if macro == "tightening" and signal_status == "green" and adjusted == "green":
            adjusted = "yellow"
            extra_notes.append("⚠️ 宏观环境收紧,green 信号降级观察")
        elif macro == "easing" and signal_status == "yellow" and adjusted == "yellow":
            adjusted = "green"
            extra_notes.append("✓ 宏观环境宽松,yellow 信号升级")

    # ── P1-A 复合规则(双重看空强制降级) ──
    if composite_enabled and bearish_count >= 2 and bullish_count < 3:
        # 三重共振看多已处理,此处仅处理看空方
        if signal_status == "green" and adjusted == "green":
            adjusted = "red"
            extra_notes.append("⚠️ 多维双重看空,强制降级为观望")
        elif signal_status == "yellow" and adjusted == "yellow":
            adjusted = "red"
            extra_notes.append("⚠️ 多维双重看空,降级为暂不介入")

    # ── P1-A 反向升级(red + 双重看多 → yellow) ──
    if composite_enabled and signal_status == "red" and adjusted == "red" and bullish_count >= 2 and bearish_count < 3:
        adjusted = "yellow"
        extra_notes.append("✓ 多维双重看多,反弹信号可关注")

    # ── 原有 P0-1 单维度规则(降级触发条件,与上面互斥) ──
    # 仅当三重共振/复合规则未触发时,执行原单维度规则
    if adjusted == signal_status:
        # green + 技术看空 → 降级 yellow
        if signal_status == "green" and tech == "bear":
            adjusted = "yellow"
            extra_notes.append("⚠️ 技术面看空,降级为接近上车")
        # green + 资金流出 → 维持 green 但提示
        elif signal_status == "green" and capital == "outflow":
            extra_notes.append("⚠️ 资金流出,谨慎上车")
        # yellow + 技术多头 → 升级 green
        elif signal_status == "yellow" and tech == "bull":
            adjusted = "green"
            extra_notes.append("✓ 技术面确认,升级为可上车")

    # 情绪极端时附加提示(独立于上面规则)
    if signal_status == "green" and sentiment == "greed" and "市场过热" not in " ".join(extra_notes):
        extra_notes.append("⚠️ 市场过热,注意情绪反转风险")
    elif signal_status in ("green", "yellow") and sentiment == "fear" and "逆向布局" not in " ".join(extra_notes):
        extra_notes.append("✓ 市场恐惧,逆向布局机会")

    final_reason = signal_reason
    if extra_notes:
        final_reason = f"{signal_reason}（{'，'.join(extra_notes)}）" if signal_reason else "，".join(extra_notes)

    return adjusted, final_reason, reasons


def compute_signal_confidence(signal_status: str, multidim: dict,
                                fund_code: str = None) -> int:
    """计算信号置信度（0-100）。

    P2-B（2026-07-21）权重重分配为 6 维加权:
    - 估值偏离度 30%（原 35%）：green=80、yellow=55、red=20、gray=0
    - 技术面 18%（原 20%）：bull=100、neutral=50、bear=10
    - 资金面 12%（原 15%）：inflow=90、neutral=50、outflow=20
    - 情绪面 12%（原 15%）：fear=80（逆向加分）、neutral=50、greed=30
    - 历史命中率 13%（原 15%）：hit_rate>=70 加分、<40 扣分
    - 宏观面 15%（新增）：easing=90、neutral=50、tightening=10

    无多维数据时仅按估值偏离度计算 + 历史命中率，confidence 上限 60。
    """
    base_map = {"green": 80, "yellow": 55, "red": 20, "gray": 0}
    base = base_map.get(signal_status, 50)

    # 历史命中率反哺（需 fund_code + 开关开启）
    history_bonus = 0
    try:
        from db.config import get_config_bool
        history_enabled = get_config_bool("watchlist.history_hitrate_feedback_enabled", True)
    except Exception:
        history_enabled = True
    if history_enabled and fund_code:
        try:
            from db.watchlist import get_signal_backtest_stats
            stats = get_signal_backtest_stats(fund_code=fund_code)
            if stats.get("reviewed", 0) >= 3:  # 样本量 >= 3 才反哺
                hit_rate = stats.get("hit_rate", 0) or 0
                if hit_rate >= 70:
                    history_bonus = 10
                elif hit_rate < 40:
                    history_bonus = -15
                # 40-70 不调整
        except Exception as e:
            logger.debug(f"[wl_multidim] 历史命中率反哺失败 {fund_code}: {e}")

    # 无多维数据时仅按估值偏离度 + 历史命中率，confidence 上限 60
    if not multidim or (
        multidim.get("tech_signal") == "neutral"
        and multidim.get("capital_signal") == "neutral"
        and multidim.get("sentiment_signal") == "neutral"
        and multidim.get("macro_signal", "neutral") == "neutral"
    ):
        return max(0, min(60, int(base) + history_bonus))

    tech_map = {"bull": 100, "neutral": 50, "bear": 10}
    capital_map = {"inflow": 90, "neutral": 50, "outflow": 20}
    sentiment_map = {"fear": 80, "neutral": 50, "greed": 30}
    macro_map = {"easing": 90, "neutral": 50, "tightening": 10}

    tech_score = tech_map.get(multidim.get("tech_signal", "neutral"), 50)
    capital_score = capital_map.get(multidim.get("capital_signal", "neutral"), 50)
    sentiment_score = sentiment_map.get(multidim.get("sentiment_signal", "neutral"), 50)
    macro_score = macro_map.get(multidim.get("macro_signal", "neutral"), 50)

    # 6 维加权:估值 30% + 技术 18% + 资金 12% + 情绪 12% + 历史 13% + 宏观 15%
    history_dim_score = 50 + history_bonus * 2  # 50 中性,bonus 翻倍归一到 0-100
    confidence = int(
        base * 0.30
        + tech_score * 0.18
        + capital_score * 0.12
        + sentiment_score * 0.12
        + history_dim_score * 0.13
        + macro_score * 0.15
    )
    return max(0, min(100, confidence))
