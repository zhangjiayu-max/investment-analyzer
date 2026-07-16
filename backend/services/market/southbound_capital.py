"""南向资金（港股通）数据服务（M4 新增）。

数据源：akshare stock_hsgt_south_net_flow_in_em（仍在公布实时数据）。
作为辅助信号判断港股资金动向，特别是恒生科技/港股行情归因时使用。

缓存：复用 market_data._get_cached/_set_cached 5 分钟 TTL。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import akshare as ak
    import pandas as pd
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False
    ak = None
    pd = None


def _safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _get_cached(key: str):
    try:
        from services.market_data import _get_cached as _gc
        return _gc(key)
    except Exception:
        return None


def _set_cached(key: str, value, ttl: int = 300):
    try:
        from services.market_data import _set_cached as _sc
        _sc(key, value)
    except Exception:
        pass


def _empty_southbound() -> dict:
    return {
        "series": [],
        "latest": None,
        "recent_5d_net_yi": 0.0,
        "recent_20d_net_yi": 0.0,
        "recent_60d_net_yi": 0.0,
        "trend": "unknown",
        "strength": "weak",
        "note": "南向资金数据不可用（akshare 接口失败或无数据）",
    }


def get_southbound_capital_flow(days: int = 30) -> dict:
    """南向资金净流入数据（港股通）。

    akshare 接口：stock_hsgt_hist_em(symbol="南向资金")
    返回字段：日期、当日成交净买额（单位：亿元）、买入成交额、卖出成交额、历史累计净买额等。

    Returns:
        {
            "series": [{"date": "2026-07-16", "net_flow_yi": 50.39, "cumulative_yi": 30000.0}, ...],
            "latest": {"date": "...", "net_flow_yi": ..., },
            "recent_5d_net_yi": float,    # 近 5 日累计净流入（亿元）
            "recent_20d_net_yi": float,   # 近 20 日累计净流入（亿元）
            "recent_60d_net_yi": float,   # 近 60 日累计净流入（亿元）
            "trend": "inflow" | "outflow" | "neutral",
            "strength": "strong" | "moderate" | "weak",
        }
    """
    cache_key = f"southbound_capital:{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    if not _HAS_AKSHARE or pd is None:
        return _empty_southbound()

    try:
        # akshare 接口：stock_hsgt_hist_em(symbol="南向资金")
        # 返回每日南向资金数据，单位已是亿元
        df = ak.stock_hsgt_hist_em(symbol="南向资金")
        if df is None or len(df) == 0:
            return _empty_southbound()

        # 字段标准化：东方财富接口返回中文列名
        # 主要字段：日期、当日成交净买额（亿元）、历史累计净买额
        date_col = "日期"
        net_col = "当日成交净买额"
        cum_col = "历史累计净买额"

        # 兼容字段名变体
        for c in df.columns:
            if "日期" in str(c) or "date" in str(c).lower():
                date_col = c
            elif "净买额" in str(c) or "净流入" in str(c):
                if "累计" not in str(c) and "历史" not in str(c):
                    net_col = c
            elif "累计" in str(c) or "历史累计" in str(c):
                cum_col = c

        # 转换日期为字符串
        df["_date"] = df[date_col].astype(str).str[:10]
        # 净流入已为亿元单位（stock_hsgt_hist_em 返回亿元）
        df["_net_yi"] = df[net_col].apply(lambda x: _safe_float(x))

        # 排序并截取最近 days 天
        df_sorted = df.sort_values("_date").tail(days)

        series = []
        for _, row in df_sorted.iterrows():
            net_yi = _safe_float(row["_net_yi"])
            # 累计值优先取数据源，否则本地累加
            cum_yi = _safe_float(row.get(cum_col, 0)) if cum_col in df.columns else 0.0
            series.append({
                "date": str(row["_date"]),
                "net_flow_yi": round(net_yi, 2),
                "cumulative_yi": round(cum_yi, 2) if cum_yi else None,
            })

        if not series:
            return _empty_southbound()

        latest = series[-1]
        recent_5d = sum(s["net_flow_yi"] for s in series[-5:]) if len(series) >= 5 else sum(s["net_flow_yi"] for s in series)
        recent_20d = sum(s["net_flow_yi"] for s in series[-20:]) if len(series) >= 20 else sum(s["net_flow_yi"] for s in series)
        recent_60d = sum(s["net_flow_yi"] for s in series[-60:]) if len(series) >= 60 else sum(s["net_flow_yi"] for s in series)

        # 趋势与强度判断
        avg_5d = recent_5d / min(5, len(series))

        if avg_5d > 0:
            trend = "inflow"
        elif avg_5d < 0:
            trend = "outflow"
        else:
            trend = "neutral"

        abs_avg = abs(avg_5d)
        if abs_avg > 30:
            strength = "strong"
        elif abs_avg > 10:
            strength = "moderate"
        else:
            strength = "weak"

        result = {
            "series": series[-30:] if len(series) > 30 else series,  # 限制返回数量避免 token 膨胀
            "latest": latest,
            "recent_5d_net_yi": round(recent_5d, 2),
            "recent_20d_net_yi": round(recent_20d, 2),
            "recent_60d_net_yi": round(recent_60d, 2),
            "trend": trend,
            "strength": strength,
            "note": (
                "南向资金=港股通内地资金净买入港股。正值=净流入港股市场（推升港股），"
                "负值=净流出。趋势 inflow 时与恒生科技/港股看多建议共振。"
                "单位：亿元。"
            ),
        }
        _set_cached(cache_key, result)
        return result

    except Exception as e:
        logger.warning(f"南向资金查询失败: {e}")
        return _empty_southbound()


def get_southbound_capital_summary() -> dict:
    """南向资金汇总（用于工具调用）。"""
    flow = get_southbound_capital_flow(days=60)
    # 兼容 flow.get("latest") 为 None 的情况
    latest = flow.get("latest") or {}
    return {
        "latest_date": latest.get("date", ""),
        "latest_net_yi": latest.get("net_flow_yi", 0.0),
        "recent_5d_net_yi": flow.get("recent_5d_net_yi", 0.0),
        "recent_20d_net_yi": flow.get("recent_20d_net_yi", 0.0),
        "recent_60d_net_yi": flow.get("recent_60d_net_yi", 0.0),
        "trend": flow.get("trend", "unknown"),
        "strength": flow.get("strength", "weak"),
        "note": flow.get("note", ""),
    }


def get_southbound_capital_signal() -> dict:
    """南向资金信号：trend/strength 综合判断。"""
    summary = get_southbound_capital_summary()
    trend = summary.get("trend", "neutral")
    strength = summary.get("strength", "weak")

    # 信号映射：与估值/持仓共振判断
    if trend == "inflow" and strength in ("strong", "moderate"):
        signal = "bullish_resonance"
        advice = "南向资金持续净流入，与港股看多信号共振"
    elif trend == "inflow" and strength == "weak":
        signal = "weak_bullish"
        advice = "南向资金小幅净流入，信号偏弱不宜单独决策"
    elif trend == "outflow" and strength in ("strong", "moderate"):
        signal = "bearish_resonance"
        advice = "南向资金持续净流出，注意港股减仓风险"
    elif trend == "outflow" and strength == "weak":
        signal = "weak_bearish"
        advice = "南向资金小幅净流出，信号偏弱"
    else:
        signal = "neutral"
        advice = "南向资金无明显方向"

    return {
        "signal": signal,
        "advice": advice,
        "trend": trend,
        "strength": strength,
        "recent_5d_net_yi": summary.get("recent_5d_net_yi", 0.0),
        "recent_20d_net_yi": summary.get("recent_20d_net_yi", 0.0),
    }
