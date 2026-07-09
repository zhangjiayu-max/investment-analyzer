"""估值预测信号模块 — 均值回归分析 + 极值预警 + 全市场信号扫描。

P1-3：基于历史 PE/PB 序列的统计预测，不依赖 LLM。
  - mean_reversion_analysis: OU 过程拟合半衰期 + 回归概率 + 历史类比预期收益 + 信号判定
  - extreme_warning:         极值分位预警 + 历史相似场景后续收益
  - forecast_signals:        全市场扫描强信号
"""

import logging
import math
import statistics
from datetime import datetime, timedelta

from db.valuations import get_valuation_history, list_valuation_indexes

logger = logging.getLogger(__name__)

# 历史回看窗口（天）—— 约 5 年
_LOOKBACK_YEARS = 5
# 最少数据点
_MIN_DATA_POINTS = 50
# 最少历史跨度（天）—— 1 年
_MIN_HISTORY_DAYS = 365
# 历史类比时分位点匹配窗口（±5%）
_PERCENTILE_MATCH_WINDOW = 0.05
# 后续收益查找的容忍天数
_RETURN_TOLERANCE_DAYS = 60


# ──────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────

def _safe_float(v, default=None) -> float | None:
    """安全转 float，空值/非法值返回 default。"""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _normalize_pct(v) -> float | None:
    """把 percentile 统一为 0~1 的 float（历史数据可能是 0~100 或 0~1）。"""
    f = _safe_float(v)
    if f is None:
        return None
    if f > 1.5:
        return f / 100.0
    return f


def _parse_date(date_str) -> datetime | None:
    """解析日期字符串，兼容 'YYYY-MM-DD' 等格式。"""
    if not date_str:
        return None
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _load_history(index_code: str, metric_type: str, years: int = _LOOKBACK_YEARS) -> list[dict]:
    """加载指数估值历史，按时间升序返回（剔除 current_value 为空的记录）。

    返回字段：date(datetime)、date_str、value、point、percentile(0~1)。
    """
    days = years * 365 + 10
    rows = get_valuation_history(index_code, days=days, metric_type=metric_type)
    if not rows:
        return []
    cleaned = []
    for r in rows:
        v = _safe_float(r.get("current_value"))
        if v is None:
            continue
        d = _parse_date(r.get("snapshot_date"))
        if d is None:
            continue
        cleaned.append({
            "date": d,
            "date_str": str(r.get("snapshot_date"))[:10],
            "value": v,
            "point": _safe_float(r.get("current_point")),
            "percentile": _normalize_pct(r.get("percentile")),
        })
    cleaned.sort(key=lambda x: x["date"])
    return cleaned


def _least_squares_alpha(pe_series: list[float], mu: float) -> float | None:
    """简化 OU 过程：ΔPE = -α × (PE[t-1] - μ)，用最小二乘估 α。

    回归方程无截距（均值处 ΔPE 期望为 0）：
        α = -Σ(x·y) / Σ(x²)，其中 x = PE[t-1] - μ，y = ΔPE = PE[t] - PE[t-1]
    返回 α（>0 表示存在均值回归），无法估计时返回 None。
    """
    if len(pe_series) < 3:
        return None
    sum_xy = 0.0
    sum_xx = 0.0
    for i in range(1, len(pe_series)):
        x = pe_series[i - 1] - mu
        y = pe_series[i] - pe_series[i - 1]
        sum_xy += x * y
        sum_xx += x * x
    if sum_xx == 0:
        return None
    alpha = -sum_xy / sum_xx
    return alpha


def _find_subsequent_return(history: list[dict], target_date: datetime,
                            holding_days: int = 365) -> float | None:
    """从 target_date 起，找 holding_days 天后的指数点位，计算收益率。

    策略：起始点取 <= target_date 的最近一条有点位的记录；
          结束点取 target_date + holding_days 附近（±容忍天数）最近的有点位记录。
    """
    # 起始点
    start = None
    for h in history:
        if h["point"] and h["point"] > 0 and h["date"] <= target_date:
            start = h
    if start is None:
        for h in history:
            if h["point"] and h["point"] > 0:
                start = h
                break
    if start is None or not start["point"] or start["point"] <= 0:
        return None

    target_end = start["date"] + timedelta(days=holding_days)
    best = None
    best_diff = None
    for h in history:
        if not h["point"] or h["point"] <= 0:
            continue
        if h["date"] <= start["date"]:
            continue
        diff = abs((h["date"] - target_end).days)
        if diff <= _RETURN_TOLERANCE_DAYS and (best_diff is None or diff < best_diff):
            best_diff = diff
            best = h
    if best is None:
        return None
    return (best["point"] - start["point"]) / start["point"]


def _insufficient(index_code: str, metric_type: str, n: int) -> dict:
    """数据不足时的统一返回。"""
    return {
        "index_code": index_code,
        "metric_type": metric_type,
        "signal": "insufficient_data",
        "signal_strength": 0,
        "data_points": n,
    }


def _estimate_reversion_probability(deviation_sigma: float, alpha: float | None) -> float:
    """估算 1 年内回归到 ±1σ 内的概率。

    基于 OU 过程的指数衰减：1 年后预期偏离 = |偏离度| × exp(-α × 365)。
    偏离度越小、衰减越快，回归概率越高。
    """
    d = abs(deviation_sigma)
    if d <= 1.0:
        # 当前已在 ±1σ 内
        return 0.95
    if not alpha or alpha <= 0:
        # 无均值回归迹象
        return 0.05
    decay = math.exp(-alpha * 365)
    expected_dev = d * decay
    return max(0.05, min(0.95, 1 - expected_dev))


def _estimate_expected_return(history: list[dict], current_percentile: float,
                              window: float = _PERCENTILE_MATCH_WINDOW) -> float | None:
    """历史类比法：找全窗口分位点相近（±window）且至少 1 年前的样本，算后续 1 年收益均值。"""
    values = [h["value"] for h in history]
    n = len(values)
    if n == 0:
        return None
    latest_date = history[-1]["date"]
    cutoff = latest_date - timedelta(days=365)
    returns = []
    for h in history:
        if h["date"] > cutoff:
            continue
        below = sum(1 for v in values if v < h["value"])
        pct = below / n
        if abs(pct - current_percentile) <= window:
            r = _find_subsequent_return(history, h["date"], holding_days=365)
            if r is not None:
                returns.append(r)
    if not returns:
        return None
    return sum(returns) / len(returns)


def _build_suggestion(direction: str, is_extreme: bool, percentile: float,
                      subsequent_return: float | None) -> str:
    """生成极值预警建议文本。"""
    if not is_extreme:
        return "当前估值处于正常区间，暂无极值信号"
    ret_text = ""
    if subsequent_return is not None:
        sign = "+" if subsequent_return >= 0 else ""
        ret_text = f"（历史相似时点后1年收益 {sign}{subsequent_return * 100:.1f}%）"
    if direction == "low":
        return f"历史极低位置（分位 {percentile * 100:.1f}%），可考虑分批布局{ret_text}"
    return f"历史极高水平（分位 {percentile * 100:.1f}%），注意减仓风险{ret_text}"


# ──────────────────────────────────────────────────────
# 核心分析函数
# ──────────────────────────────────────────────────────

def mean_reversion_analysis(index_code: str, metric_type: str = "市盈率") -> dict:
    """均值回归分析：半衰期 + 回归概率 + 历史类比预期收益 + 信号判定。

    算法：
      1. 取过去 5 年 PE 序列
      2. 计算历史均值 μ 和标准差 σ
      3. 当前值 = 序列最后一个 current_value
      4. 偏离度 = (current - μ) / σ
      5. 当前分位 = 历史序列中小于当前值的比例
      6. 半衰期：简化 OU 过程拟合，half_life = ln(2) / α
      7. 回归概率：基于偏离度和半衰期估算 1 年内回归到 ±1σ 内的概率
      8. 预期收益：历史类比法，找相同分位点的后续 1 年收益均值
      9. 信号判定：分位 <20% → low_estimate；>80% → high_estimate；否则 fair
    """
    try:
        history = _load_history(index_code, metric_type, years=_LOOKBACK_YEARS)
        if len(history) < _MIN_DATA_POINTS:
            return _insufficient(index_code, metric_type, len(history))

        span_days = (history[-1]["date"] - history[0]["date"]).days
        if span_days < _MIN_HISTORY_DAYS:
            return _insufficient(index_code, metric_type, len(history))

        values = [h["value"] for h in history]
        n = len(values)

        mu = statistics.fmean(values)
        try:
            sigma = statistics.pstdev(values)
        except Exception:
            sigma = 0.0
        if sigma <= 0:
            return _insufficient(index_code, metric_type, n)

        current_value = values[-1]
        deviation_sigma = (current_value - mu) / sigma

        below = sum(1 for v in values if v < current_value)
        current_percentile = below / n

        # 半衰期（OU 过程）
        alpha = _least_squares_alpha(values, mu)
        if alpha and alpha > 0:
            half_life_days = math.log(2) / alpha
        else:
            half_life_days = None

        reversion_probability = _estimate_reversion_probability(deviation_sigma, alpha)
        expected_return_1y = _estimate_expected_return(history, current_percentile)

        # 信号判定
        if current_percentile < 0.20:
            signal = "low_estimate"
            strength = (0.20 - current_percentile) / 0.20
        elif current_percentile > 0.80:
            signal = "high_estimate"
            strength = (current_percentile - 0.80) / 0.20
        else:
            signal = "fair"
            strength = 0.0

        return {
            "index_code": index_code,
            "metric_type": metric_type,
            "current_percentile": round(current_percentile, 4),
            "historical_mean": round(mu, 4),
            "current_value": round(current_value, 4),
            "deviation_sigma": round(deviation_sigma, 4),
            "half_life_days": round(half_life_days, 1) if half_life_days is not None else None,
            "reversion_probability": round(reversion_probability, 4),
            "expected_return_1y": round(expected_return_1y, 4) if expected_return_1y is not None else None,
            "signal": signal,
            "signal_strength": round(strength, 4),
            "history_years": round(span_days / 365, 2),
            "data_points": n,
        }
    except Exception as e:
        logger.warning(f"[forecast] mean_reversion_analysis 失败 {index_code}: {e}", exc_info=True)
        return {
            "index_code": index_code,
            "metric_type": metric_type,
            "signal": "insufficient_data",
            "signal_strength": 0,
            "error": str(e),
        }


def extreme_warning(index_code: str, metric_type: str = "市盈率") -> dict:
    """极值预警：当前分位 <5% 或 >95% 时触发。

    返回极值标记、方向、分位、历史频率、最近相似日期、该日后 1 年指数收益率及建议文本。
    """
    try:
        history = _load_history(index_code, metric_type, years=_LOOKBACK_YEARS)
        if len(history) < _MIN_DATA_POINTS:
            return _insufficient(index_code, metric_type, len(history))

        values = [h["value"] for h in history]
        n = len(values)
        current_value = values[-1]
        below = sum(1 for v in values if v < current_value)
        percentile = below / n

        is_extreme = percentile < 0.05 or percentile > 0.95
        direction = "low" if percentile < 0.50 else "high"
        historical_frequency = below / n

        # 找历史上最接近当前值且至少 1 年前的日期
        latest_date = history[-1]["date"]
        cutoff = latest_date - timedelta(days=365)
        best = None
        best_diff = None
        for h in history:
            if h["date"] > cutoff:
                continue
            diff = abs(h["value"] - current_value)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = h
        last_similar_date = best["date_str"] if best else None

        subsequent_return_1y = None
        if best is not None:
            subsequent_return_1y = _find_subsequent_return(history, best["date"], holding_days=365)

        suggestion = _build_suggestion(direction, is_extreme, percentile, subsequent_return_1y)

        return {
            "index_code": index_code,
            "metric_type": metric_type,
            "is_extreme": is_extreme,
            "direction": direction,
            "percentile": round(percentile, 4),
            "historical_frequency": round(historical_frequency, 4),
            "last_similar_date": last_similar_date,
            "subsequent_return_1y": round(subsequent_return_1y, 4) if subsequent_return_1y is not None else None,
            "suggestion": suggestion,
        }
    except Exception as e:
        logger.warning(f"[forecast] extreme_warning 失败 {index_code}: {e}", exc_info=True)
        return {
            "index_code": index_code,
            "metric_type": metric_type,
            "is_extreme": False,
            "error": str(e),
        }


def forecast_signals() -> list[dict]:
    """全市场信号扫描：对所有指数做均值回归分析，筛选 signal_strength > 0.5 的强信号。"""
    signals: list[dict] = []
    try:
        indexes = list_valuation_indexes()
    except Exception as e:
        logger.warning(f"[forecast] list_valuation_indexes 失败: {e}")
        return []

    seen: set[tuple] = set()
    for idx in indexes:
        index_code = idx.get("index_code")
        metric_type = idx.get("metric_type") or "市盈率"
        if not index_code:
            continue
        key = (index_code, metric_type)
        if key in seen:
            continue
        seen.add(key)
        try:
            res = mean_reversion_analysis(index_code, metric_type)
        except Exception as e:
            logger.debug(f"[forecast] {index_code} 分析失败: {e}")
            continue
        if res.get("signal") in ("low_estimate", "high_estimate") \
                and res.get("signal_strength", 0) > 0.5:
            res["index_name"] = idx.get("index_name")
            signals.append(res)

    # 按信号强度倒序
    signals.sort(key=lambda x: x.get("signal_strength", 0), reverse=True)
    return signals
