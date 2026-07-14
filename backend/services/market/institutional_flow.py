"""机构动向数据服务（P0 新增）。

作为辅助信号提升现有建议质量，而非独立跟风策略。
滞后效应导致纯跟风回测失真，机构动向的价值在于"与估值/持仓信号共振时增强置信度"。

数据源（2026-07 调研确认）：
- 北向资金实时净买额：2024年8月监管叫停公布，已不可用
- 融资融券余额：日频实时可查，杠杆资金动向，作为主信号
- 南向资金（港股通）：仍在公布实时数据，但方向相反，作辅助
- 龙虎榜机构席位：日频，机构短期动向（P1 扩展）

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
    """复用 market_data 的缓存机制。"""
    try:
        from services.market_data import _get_cached as _gc
        return _gc(key)
    except Exception:
        return None


def _set_cached(key: str, value, ttl: int = 300):
    """复用 market_data 的缓存机制（默认 5 分钟）。"""
    try:
        from services.market_data import _set_cached as _sc
        _sc(key, value)
    except Exception:
        pass


def get_margin_balance(days: int = 30) -> dict:
    """融资融券余额变化（机构杠杆资金动向主信号）。

    2026-07 调研确认：北向资金实时数据已不可用，改用融资余额作为主信号。
    融资余额上升 = 杠杆资金加仓，融资余额下降 = 杠杆资金减仓。

    Returns:
        {
            "series": [{"date": "20260609", "margin_balance": 1.46e12, "change": 12.3e8}, ...],
            "latest": {"date": "...", "margin_balance": ..., "change": ...},
            "recent_5d_change": float,  # 近5日融资余额净变化（亿元）
            "trend": "inflow" | "outflow" | "neutral",
            "strength": "strong" | "moderate" | "weak",
        }
    """
    cache_key = f"margin_balance:{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    if not _HAS_AKSHARE or pd is None:
        return _empty_margin_balance()

    try:
        # 上交所融资融券数据（按日期范围查询）
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
        df = ak.stock_margin_sse(start_date=start_date, end_date=end_date)
        if df is None or len(df) == 0:
            return _empty_margin_balance()

        # 数据是降序的（最新在前），转升序方便计算
        df = df.sort_values("信用交易日期").reset_index(drop=True)
        df = df.tail(days)

        series = []
        prev_balance = None
        for _, r in df.iterrows():
            balance = _safe_float(r.get("融资余额", 0))
            change = balance - prev_balance if prev_balance is not None else 0
            series.append({
                "date": str(r.get("信用交易日期", "")),
                "margin_balance": balance,
                "change": change,
            })
            prev_balance = balance

        if not series:
            return _empty_margin_balance()

        # 近5日净变化
        recent_5_changes = [s["change"] for s in series[-5:]]
        recent_5d_change = sum(recent_5_changes) / 1e8  # 转亿元

        # z-score 归一化
        all_changes = [s["change"] / 1e8 for s in series if s["change"] != 0]
        if len(all_changes) >= 10:
            mean_chg = sum(all_changes) / len(all_changes)
            std_chg = (sum((c - mean_chg) ** 2 for c in all_changes) / len(all_changes)) ** 0.5
            z_score = (recent_5d_change - mean_chg * 5) / (std_chg * 5 ** 0.5) if std_chg > 0 else 0
        else:
            z_score = 0

        # 趋势判断
        if recent_5d_change > 0:
            trend = "inflow"
        elif recent_5d_change < 0:
            trend = "outflow"
        else:
            trend = "neutral"

        # 强度
        abs_z = abs(z_score)
        if abs_z >= 1.5:
            strength = "strong"
        elif abs_z >= 0.5:
            strength = "moderate"
        else:
            strength = "weak"

        result = {
            "series": series,
            "latest": series[-1],
            "recent_5d_change": round(recent_5d_change, 2),
            "z_score_5d": round(z_score, 3),
            "trend": trend,
            "strength": strength,
        }
        _set_cached(cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"获取融资融券余额失败: {e}")
        return _empty_margin_balance()


def _empty_margin_balance() -> dict:
    return {
        "series": [],
        "latest": None,
        "recent_5d_change": 0,
        "z_score_5d": 0,
        "trend": "neutral",
        "strength": "weak",
    }


def get_institutional_flow_summary() -> dict:
    """机构动向摘要（TickerBar 用，轻量级）。

    与 get_institutional_flow_signal 共用 30 天窗口，保证 strength 一致。

    Returns:
        {"recent_5d_change_yi": float, "trend": "inflow|outflow|neutral", "strength": "strong|moderate|weak"}
    """
    data = get_margin_balance(days=30)
    return {
        "recent_5d_change_yi": data.get("recent_5d_change", 0),
        "trend": data.get("trend", "neutral"),
        "strength": data.get("strength", "weak"),
    }


def get_institutional_flow_signal() -> dict:
    """机构动向共振信号（guardrail 用）。

    返回归一化信号，供 _apply_institutional_confirm 判断建议方向是否与机构资金一致。

    Returns:
        {
            "direction": "inflow" | "outflow" | "neutral",  # 资金方向
            "strength": "strong" | "moderate" | "weak",     # 信号强度
            "z_score": float,                                # z-score，>0 净流入，<0 净流出
        }
    """
    data = get_margin_balance(days=30)
    return {
        "direction": data.get("trend", "neutral"),
        "strength": data.get("strength", "weak"),
        "z_score": data.get("z_score_5d", 0),
    }
