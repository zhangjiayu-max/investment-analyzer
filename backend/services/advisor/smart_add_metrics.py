"""智能补仓增强算法 — L3 凯利公式 + L4 回撤修复时间 + L5 估值分位胜率。

被 smart_add_planner.py 调用，为每个持仓提供差异化指标：
- L3 calc_kelly_limit：基于基金净值历史，计算半凯利最大配置比例
- L4 calc_recovery_time：基于指数点位历史，统计回撤修复时间
- L5 calc_valuation_win_rate：基于 PE 历史，计算当前分位买入胜率

数据依赖：
- L3：本地 fund_nav_history 表（8-13年基金净值）
- L4/L5：本地 index_price_history 表（需 index_history_fetcher 回填）
"""

import logging
import math
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 无风险利率（10年期国债，默认2.5%）
RISK_FREE_RATE = 0.025


# ── L3：半凯利公式 ──────────────────────────

def calc_kelly_limit(fund_code: str, index_code: str = "", user_id: str = "default") -> dict:
    """半凯利公式计算单标的最大配置比例。

    f* = (μ - r) / σ²
    实际上限 = f* × 0.5（半凯利，安全边际）

    数据来源优先级：
    1. 本地 index_price_history（指数点位，无分红污染）—— 需传入 index_code
    2. 本地 fund_nav_history 的 acc_nav（累计净值）—— 需有累计净值数据
    3. 本地 fund_nav_history 的 nav（单位净值，前复权处理）
    4. 默认 25% 上限

    Returns:
        {
            "kelly_full": float, "kelly_half": float,
            "mu": float, "sigma": float,
            "risk_free_rate": float, "limit_pct": float,
            "sample_days": int, "data_source": str,
        }
    """
    # 优先用指数点位（无分红污染）
    prices = _get_index_close_prices(index_code) if index_code else []

    if prices and len(prices) >= 60:
        data_source = "index_price_history"
        sample = prices
    else:
        # 降级到基金净值
        navs = _get_fund_nav_history(fund_code, user_id)
        if not navs or len(navs) < 30:
            return _default_kelly_result(len(navs) if navs else 0)
        # 检查是否有累计净值（acc_nav 非 None）
        sample = [n["nav"] for n in navs]
        data_source = "fund_nav_history"

    # 计算日收益率序列
    daily_returns = []
    for i in range(1, len(sample)):
        prev = sample[i - 1]
        curr = sample[i]
        if prev and curr and prev > 0:
            daily_returns.append((curr - prev) / prev)

    if len(daily_returns) < 30:
        return _default_kelly_result(len(daily_returns))

    # 年化收益率 μ = 日均收益 × 252
    avg_daily_return = sum(daily_returns) / len(daily_returns)
    mu = avg_daily_return * 252

    # 年化波动率 σ = 日收益标准差 × √252
    variance = sum((r - avg_daily_return) ** 2 for r in daily_returns) / len(daily_returns)
    sigma = math.sqrt(variance) * math.sqrt(252)

    # 凯利公式：f* = (μ - r) / σ²
    if sigma > 0:
        kelly_full = (mu - RISK_FREE_RATE) / (sigma ** 2)
    else:
        kelly_full = 0.5

    # 限制在合理范围 [0, 1.5]
    kelly_full = max(0.0, min(1.5, kelly_full))
    kelly_half = kelly_full * 0.5
    limit_pct = min(kelly_half * 100, 60.0)

    logger.debug(f"[kelly] {fund_code}/{index_code}: μ={mu:.4f} σ={sigma:.4f} f*={kelly_full:.3f} 半凯利={kelly_half:.3f}")

    return {
        "kelly_full": round(kelly_full, 4),
        "kelly_half": round(kelly_half, 4),
        "mu": round(mu, 4),
        "sigma": round(sigma, 4),
        "risk_free_rate": RISK_FREE_RATE,
        "limit_pct": round(limit_pct, 2),
        "sample_days": len(daily_returns),
        "data_source": data_source,
    }


def _default_kelly_result(sample_days: int) -> dict:
    return {
        "kelly_full": 0.5,
        "kelly_half": 0.25,
        "mu": 0.0,
        "sigma": 0.0,
        "risk_free_rate": RISK_FREE_RATE,
        "limit_pct": 25.0,
        "sample_days": sample_days,
        "data_source": "default",
    }


def _get_index_close_prices(index_code: str) -> list:
    """从 index_price_history 读取指数收盘点位序列。"""
    if not index_code:
        return []
    from services.index_history_fetcher import get_index_price_history
    history = get_index_price_history(index_code, days=365 * 10)
    return [h["close"] for h in history if h["close"] and h["close"] > 0]


def _get_fund_nav_history(fund_code: str, user_id: str = "default") -> list[dict]:
    """读取本地 fund_nav_history 表，优先用累计净值（含分红复权）。"""
    from db._conn import _get_conn
    conn = _get_conn()
    try:
        # 优先用 acc_nav（累计净值，含分红复权），其次 nav
        rows = conn.execute("""
            SELECT nav_date, COALESCE(acc_nav, nav) as nav
            FROM fund_nav_history
            WHERE fund_code = ? AND COALESCE(acc_nav, nav) IS NOT NULL AND COALESCE(acc_nav, nav) > 0
            ORDER BY nav_date ASC
        """, (fund_code,)).fetchall()
    except Exception as e:
        logger.debug(f"读取 fund_nav_history 失败: {e}")
        return []
    finally:
        conn.close()

    return [{"nav_date": r[0], "nav": r[1]} for r in rows if r[1] and r[1] > 0]


# ── L4：历史回撤修复时间 ──────────────────────

def calc_recovery_time(index_code: str) -> dict:
    """历史回撤修复时间分析。

    算法：
    1. 获取指数历史收盘点位
    2. 识别所有"从高点下跌 X% 后回升至前高"的回撤-修复周期
    3. 统计 -20%/-30%/-40% 回撤的中位修复月数

    数据来源：本地 index_price_history 表

    Returns:
        {
            "median_recovery_months": float,  # 中位修复月数
            "scenarios": [...],               # 历史回撤场景
            "sample_count": int,              # 样本数
            "data_source": str,
        }
    """
    from services.index_history_fetcher import get_index_price_history

    history = get_index_price_history(index_code, days=365 * 10)

    if not history or len(history) < 250:  # 至少1年数据
        return {
            "median_recovery_months": None,
            "scenarios": [],
            "sample_count": 0,
            "data_source": "insufficient",
        }

    # 提取收盘点位序列
    closes = [(h["trade_date"], h["close"]) for h in history if h["close"] and h["close"] > 0]
    if len(closes) < 250:
        return {
            "median_recovery_months": None,
            "scenarios": [],
            "sample_count": 0,
            "data_source": "insufficient",
        }

    # 识别回撤-修复周期
    scenarios = _find_drawdown_recovery_cycles(closes)

    if not scenarios:
        return {
            "median_recovery_months": None,
            "scenarios": [],
            "sample_count": 0,
            "data_source": "no_cycles",
        }

    # 按回撤幅度分组统计（只统计已修复的）
    recovery_months_list = [s["recovery_months"] for s in scenarios if s.get("recovery_months") is not None]
    if not recovery_months_list:
        # 没有已修复的，但有 ongoing 的
        ongoing = [s for s in scenarios if s.get("status") == "ongoing"]
        return {
            "median_recovery_months": None,
            "scenarios": scenarios,
            "sample_count": len(scenarios),
            "ongoing_count": len(ongoing),
            "data_source": "index_price_history",
            "note": "当前回撤尚未修复，无历史修复时间参考" if ongoing else "",
        }

    recovery_months_list.sort()
    n = len(recovery_months_list)
    median = recovery_months_list[n // 2] if n % 2 == 1 else (recovery_months_list[n // 2 - 1] + recovery_months_list[n // 2]) / 2

    return {
        "median_recovery_months": round(median, 1),
        "scenarios": scenarios,
        "sample_count": len(scenarios),
        "data_source": "index_price_history",
    }


def _find_drawdown_recovery_cycles(closes: list) -> list:
    """识别回撤-修复周期。

    算法：
    1. 遍历历史点位，跟踪历史最高点
    2. 当前价相对最高点下跌超过阈值（-15%/-25%/-35%）时记录回撤开始
    3. 后续价格回升至前高时记录修复完成，计算修复月数
    4. 如果到数据末尾仍未修复，也记录一个"未修复"场景
    """
    scenarios = []
    thresholds = [-15, -25, -35]  # 三个回撤档位

    for threshold in thresholds:
        peak_price = closes[0][1]
        peak_date = closes[0][0]
        in_drawdown = False
        drawdown_start = None
        drawdown_pct = 0
        max_drawdown_pct = 0

        for i, (date, price) in enumerate(closes):
            if price > peak_price:
                peak_price = price
                peak_date = date

            drawdown = (price - peak_price) / peak_price * 100 if peak_price > 0 else 0

            if not in_drawdown and drawdown <= threshold:
                in_drawdown = True
                drawdown_start = date
                drawdown_pct = drawdown
                max_drawdown_pct = drawdown
            elif in_drawdown:
                # 更新最大回撤
                if drawdown < max_drawdown_pct:
                    max_drawdown_pct = drawdown

                if price >= peak_price:
                    # 修复完成
                    try:
                        start_dt = datetime.fromisoformat(drawdown_start) if drawdown_start else None
                        end_dt = datetime.fromisoformat(date)
                        if start_dt:
                            recovery_months = (end_dt - start_dt).days / 30.0
                            scenarios.append({
                                "threshold_pct": threshold,
                                "drawdown_pct": round(max_drawdown_pct, 1),
                                "start_date": drawdown_start,
                                "recovery_date": date,
                                "recovery_months": round(recovery_months, 1),
                                "status": "recovered",
                            })
                    except Exception:
                        pass
                    in_drawdown = False
                    drawdown_start = None
                    max_drawdown_pct = 0

        # 数据结束仍未修复
        if in_drawdown and drawdown_start:
            try:
                start_dt = datetime.fromisoformat(drawdown_start)
                end_dt = datetime.fromisoformat(closes[-1][0])
                ongoing_months = (end_dt - start_dt).days / 30.0
                scenarios.append({
                    "threshold_pct": threshold,
                    "drawdown_pct": round(max_drawdown_pct, 1),
                    "start_date": drawdown_start,
                    "recovery_date": None,
                    "recovery_months": None,
                    "ongoing_months": round(ongoing_months, 1),
                    "status": "ongoing",
                })
            except Exception:
                pass

    return scenarios


# ── L5：估值分位历史胜率 ──────────────────────

def calc_valuation_win_rate(index_code: str, current_percentile: Optional[float]) -> dict:
    """估值分位历史胜率分析。

    算法：
    1. 获取指数历史 PE + 收盘点位
    2. 计算每个历史日期的 PE 分位（0-100%）
    3. 对当前分位所在区间，统计"买入后 6/12/24 月正收益概率"

    数据来源：本地 index_price_history 表

    Returns:
        {
            "win_rate_6m": float,    # 6个月正收益概率 (0-1)
            "win_rate_12m": float,   # 12个月正收益概率
            "win_rate_24m": float,   # 24个月正收益概率
            "sample_count": int,     # 样本数
            "current_percentile": float,
            "data_source": str,
        }
    """
    if current_percentile is None:
        return {
            "win_rate_6m": None,
            "win_rate_12m": None,
            "win_rate_24m": None,
            "sample_count": 0,
            "current_percentile": None,
            "data_source": "no_percentile",
        }

    from services.index_history_fetcher import get_index_price_history

    history = get_index_price_history(index_code, days=365 * 10)

    if not history or len(history) < 250:
        return {
            "win_rate_6m": None,
            "win_rate_12m": None,
            "win_rate_24m": None,
            "sample_count": 0,
            "current_percentile": current_percentile,
            "data_source": "insufficient",
        }

    # 构建日期 → (close, pe) 映射
    data = []
    for h in history:
        if h["close"] and h["close"] > 0 and h["pe_ttm"] and h["pe_ttm"] > 0:
            data.append({
                "date": h["trade_date"],
                "close": h["close"],
                "pe": h["pe_ttm"],
            })

    if len(data) < 250:
        return {
            "win_rate_6m": None,
            "win_rate_12m": None,
            "win_rate_24m": None,
            "sample_count": 0,
            "current_percentile": current_percentile,
            "data_source": "insufficient",
        }

    # 计算每个日期的 PE 分位（滚动窗口：用之前所有数据）
    pe_values = [d["pe"] for d in data]
    for i, d in enumerate(data):
        if i < 30:
            d["pe_percentile"] = None
            continue
        # 用之前所有 PE 计算分位
        historical_pes = pe_values[:i + 1]
        sorted_pes = sorted(historical_pes)
        rank = sum(1 for p in sorted_pes if p <= d["pe"])
        d["pe_percentile"] = (rank / len(sorted_pes)) * 100

    # 找到与当前分位相近的历史日期（±5%区间）
    target_min = max(0, current_percentile - 5)
    target_max = min(100, current_percentile + 5)

    samples = [d for d in data if d.get("pe_percentile") is not None and target_min <= d["pe_percentile"] <= target_max]

    if len(samples) < 10:
        # 样本不足，扩大区间到 ±10%
        target_min = max(0, current_percentile - 10)
        target_max = min(100, current_percentile + 10)
        samples = [d for d in data if d.get("pe_percentile") is not None and target_min <= d["pe_percentile"] <= target_max]

    if len(samples) < 10:
        return {
            "win_rate_6m": None,
            "win_rate_12m": None,
            "win_rate_24m": None,
            "sample_count": len(samples),
            "current_percentile": current_percentile,
            "data_source": "insufficient_samples",
        }

    # 构建 date → index 映射
    date_to_idx = {d["date"]: i for i, d in enumerate(data)}

    # 计算 6/12/24 月后正收益概率
    wins_6m = 0
    wins_12m = 0
    wins_24m = 0
    count_6m = 0
    count_12m = 0
    count_24m = 0

    for sample in samples:
        idx = date_to_idx[sample["date"]]
        sample_close = sample["close"]

        # 6个月后（约126个交易日）
        future_idx_6m = idx + 126
        if future_idx_6m < len(data):
            future_close = data[future_idx_6m]["close"]
            if future_close > sample_close:
                wins_6m += 1
            count_6m += 1

        # 12个月后（约252个交易日）
        future_idx_12m = idx + 252
        if future_idx_12m < len(data):
            future_close = data[future_idx_12m]["close"]
            if future_close > sample_close:
                wins_12m += 1
            count_12m += 1

        # 24个月后（约504个交易日）
        future_idx_24m = idx + 504
        if future_idx_24m < len(data):
            future_close = data[future_idx_24m]["close"]
            if future_close > sample_close:
                wins_24m += 1
            count_24m += 1

    win_rate_6m = wins_6m / count_6m if count_6m > 0 else None
    win_rate_12m = wins_12m / count_12m if count_12m > 0 else None
    win_rate_24m = wins_24m / count_24m if count_24m > 0 else None

    return {
        "win_rate_6m": round(win_rate_6m, 3) if win_rate_6m is not None else None,
        "win_rate_12m": round(win_rate_12m, 3) if win_rate_12m is not None else None,
        "win_rate_24m": round(win_rate_24m, 3) if win_rate_24m is not None else None,
        "sample_count": len(samples),
        "current_percentile": current_percentile,
        "data_source": "index_price_history",
    }


# ── L2：基金类型分类 ──────────────────────────

def classify_fund(holding: dict) -> dict:
    """基金类型分类，返回对应策略参数。

    分类依据：fund_category + index_name + fund_name 关键词
    """
    fund_category = (holding.get("fund_category") or "").lower()
    index_name = holding.get("index_name") or ""
    fund_name = holding.get("fund_name") or ""

    # 关键词匹配
    text = f"{fund_name} {index_name} {fund_category}".lower()

    if any(kw in text for kw in ["债", "bond"]):
        fund_type = "bond"
        strategy = {
            "dca_mult": 1.5,       # 债基修复快，定投倍数高
            "pyramid_aggressive": False,  # 不用金字塔
            "hard_cap_pct": 40.0,  # 硬上限40%
            "rhythm": "monthly",   # 月投
        }
    elif any(kw in text for kw in ["沪深300", "中证500", "中证1000", "创业板", "科创", "上证50", "宽基"]):
        fund_type = "broad"
        strategy = {
            "dca_mult": 1.2,
            "pyramid_aggressive": True,
            "hard_cap_pct": 30.0,
            "rhythm": "biweekly",  # 双周投
        }
    elif any(kw in text for kw in ["新能源", "光伏", "半导体", "芯片", "人工智能", "ai", "军工", "主题"]):
        fund_type = "theme"
        strategy = {
            "dca_mult": 0.7,       # 主题基金谨慎
            "pyramid_aggressive": False,
            "hard_cap_pct": 15.0,  # 严格上限
            "rhythm": "bimonthly", # 双月投
        }
    elif any(kw in text for kw in ["白酒", "医药", "银行", "地产", "券商", "消费", "行业"]):
        fund_type = "industry"
        strategy = {
            "dca_mult": 1.0,
            "pyramid_aggressive": True,
            "hard_cap_pct": 25.0,
            "rhythm": "monthly",
        }
    elif any(kw in text for kw in ["恒生", "港股", "qDII", "海外"]):
        fund_type = "hk_overseas"
        strategy = {
            "dca_mult": 0.9,
            "pyramid_aggressive": True,
            "hard_cap_pct": 20.0,
            "rhythm": "monthly",
        }
    else:
        fund_type = "unknown"
        strategy = {
            "dca_mult": 1.0,
            "pyramid_aggressive": True,
            "hard_cap_pct": 25.0,
            "rhythm": "monthly",
        }

    return {"fund_type": fund_type, "strategy": strategy, "label": _fund_type_label(fund_type)}


def _fund_type_label(fund_type: str) -> str:
    labels = {
        "bond": "债基",
        "broad": "宽基",
        "theme": "主题",
        "industry": "行业",
        "hk_overseas": "港股/海外",
        "unknown": "未分类",
    }
    return labels.get(fund_type, "未分类")
