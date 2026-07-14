"""组合层面智能引擎 — 组合风险度量 + 7维聚合 + 大师组合视角。

打通现有组合分析"零件"的割裂状态：
- 组合风险度量（波动率/VaR/CVaR/最大回撤/夏普/Sortino/风险贡献）
- 7维体检组合聚合版（按持仓权重加权）
- 大师矩阵组合版（6位大师基于组合数据做组合视角决策）

依赖：
- fund_data_service.get_or_refresh_fund_nav_history（基金净值序列）
- fund_analysis.calculate_fund_health_report（单基金7维体检）
- master_perspectives.build_master_perspectives_matrix（单基金大师矩阵）
- db.portfolio.list_holdings / get_portfolio_summary（持仓数据）
"""
import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 无风险利率（年化，用于夏普/Sortino计算）
RISK_FREE_RATE = 0.02
# 交易日数（年化）
TRADING_DAYS = 252


def _safe_float(v, default: float = 0.0) -> float:
    """安全转float。"""
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (ValueError, TypeError):
        return default


def _score_to_rating(score: float) -> str:
    if score >= 80:
        return "excellent"
    elif score >= 60:
        return "good"
    elif score >= 40:
        return "fair"
    else:
        return "poor"


# ════════════════════════════════════════════════════════════
# 模块1：组合风险度量引擎
# ════════════════════════════════════════════════════════════

def _get_portfolio_weights(user_id: str = "default") -> list[dict]:
    """获取持仓列表及权重。

    Returns:
        [{fund_code, fund_name, weight, current_value, shares, fund_category}, ...]
    """
    from db.portfolio import list_holdings, get_portfolio_summary
    holdings = list_holdings(user_id)
    if not holdings:
        return []

    summary = get_portfolio_summary(user_id)
    total_value = _safe_float(summary.get("total_value", 0))
    if total_value <= 0:
        return []

    weights = []
    for h in holdings:
        value = _safe_float(h.get("current_value", 0))
        if value <= 0:
            continue
        weights.append({
            "fund_code": h.get("fund_code", ""),
            "fund_name": h.get("fund_name", ""),
            "weight": value / total_value,
            "current_value": value,
            "shares": _safe_float(h.get("shares", 0)),
            "fund_category": h.get("fund_category", ""),
            "index_code": h.get("index_code", ""),
            "index_name": h.get("index_name", ""),
        })
    return weights


def _fetch_nav_series(fund_code: str, days: int = 365) -> dict | None:
    """获取基金净值序列。

    Returns:
        {fund_code, dates: [...], navs: [...]} 或 None
    """
    try:
        from services.fund_data_service import get_or_refresh_fund_nav_history
        df = get_or_refresh_fund_nav_history(fund_code, days=days)
        if df is None or len(df) < 30:
            return None
        # 按日期升序
        df = df.sort_values("nav_date")
        return {
            "fund_code": fund_code,
            "dates": df["nav_date"].tolist(),
            "navs": df["nav"].astype(float).tolist(),
        }
    except Exception as e:
        logger.warning(f"[portfolio_intel] 获取净值失败 {fund_code}: {e}")
        return None


def _align_nav_series(series_list: list[dict]) -> dict:
    """对齐多只基金的净值序列（取日期交集）。

    Returns:
        {dates: [...], nav_matrix: [[nav_fund1_date1, ...], ...], fund_codes: [...]}
    """
    if not series_list:
        return {"dates": [], "nav_matrix": [], "fund_codes": []}

    # 取所有日期的交集
    date_sets = [set(s["dates"]) for s in series_list]
    common_dates = sorted(set.intersection(*date_sets)) if len(date_sets) > 1 else sorted(date_sets[0])

    if len(common_dates) < 30:
        # 交集不足，放宽到并集（用前值填充）
        all_dates = sorted(set.union(*date_sets))
        common_dates = all_dates

    if len(common_dates) < 30:
        return {"dates": [], "nav_matrix": [], "fund_codes": []}

    # 构建对齐的净值矩阵（前值填充）
    nav_matrix = []
    fund_codes = []
    for s in series_list:
        fund_codes.append(s["fund_code"])
        date_to_nav = dict(zip(s["dates"], s["navs"]))
        navs = []
        last_nav = None
        for d in common_dates:
            if d in date_to_nav:
                last_nav = date_to_nav[d]
            if last_nav is not None:
                navs.append(last_nav)
        nav_matrix.append(navs)

    return {"dates": common_dates, "nav_matrix": nav_matrix, "fund_codes": fund_codes}


def _calc_daily_returns(nav_series: list[float]) -> list[float]:
    """计算日收益率序列。"""
    if len(nav_series) < 2:
        return []
    returns = []
    for i in range(1, len(nav_series)):
        if nav_series[i - 1] > 0:
            returns.append((nav_series[i] - nav_series[i - 1]) / nav_series[i - 1])
        else:
            returns.append(0.0)
    return returns


def _calc_max_drawdown(nav_series: list[float]) -> tuple[float, int]:
    """计算最大回撤及恢复期。

    Returns:
        (max_drawdown_pct, recovery_days)
        max_drawdown_pct 为正数（如0.3表示-30%）
        recovery_days 为从最低点恢复到前高的天数（未恢复则返回-1）
    """
    if len(nav_series) < 2:
        return 0.0, 0

    peak = nav_series[0]
    max_dd = 0.0
    trough_idx = 0
    peak_idx = 0

    for i, nav in enumerate(nav_series):
        if nav > peak:
            peak = nav
            peak_idx = i
        dd = (peak - nav) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            trough_idx = i

    # 恢复期：从trough_idx开始找到恢复到peak的日期
    recovery_days = -1
    if trough_idx < len(nav_series) - 1:
        peak_value = nav_series[peak_idx]
        for i in range(trough_idx + 1, len(nav_series)):
            if nav_series[i] >= peak_value:
                recovery_days = i - trough_idx
                break

    return max_dd, recovery_days


def calculate_portfolio_risk_metrics(user_id: str = "default", days: int = 365) -> dict:
    """组合风险度量引擎。

    计算组合波动率、VaR、CVaR、最大回撤、夏普比率、Sortino比率、风险贡献。
    """
    weights_data = _get_portfolio_weights(user_id)
    if not weights_data:
        return _default_risk_metrics("无持仓数据")

    # 过滤掉权重过小的基金（<1%不参与风险计算）
    active_holdings = [w for w in weights_data if w["weight"] >= 0.01]
    if not active_holdings:
        return _default_risk_metrics("所有持仓权重过小")

    # 获取所有基金净值序列
    nav_series_list = []
    active_codes = []
    for h in active_holdings:
        series = _fetch_nav_series(h["fund_code"], days=days)
        if series and len(series["navs"]) >= 30:
            nav_series_list.append(series)
            active_codes.append(h["fund_code"])

    if len(nav_series_list) < 1:
        return _default_risk_metrics("净值数据不足")

    # 对齐净值序列
    aligned = _align_nav_series(nav_series_list)
    if not aligned["dates"]:
        return _default_risk_metrics("对齐后数据不足")

    # 构建权重向量（按active_codes顺序）
    code_to_weight = {h["fund_code"]: h["weight"] for h in active_holdings}
    fund_weights = np.array([code_to_weight.get(c, 0) for c in aligned["fund_codes"]])
    # 归一化
    total_w = fund_weights.sum()
    if total_w > 0:
        fund_weights = fund_weights / total_w

    # 计算各基金日收益率
    returns_matrix = []
    for navs in aligned["nav_matrix"]:
        rets = _calc_daily_returns(navs)
        if rets:
            returns_matrix.append(rets)

    if not returns_matrix:
        return _default_risk_metrics("收益率计算失败")

    # 对齐收益率长度（取最短）
    min_len = min(len(r) for r in returns_matrix)
    returns_matrix = [r[:min_len] for r in returns_matrix]
    returns_array = np.array(returns_matrix)  # shape: (n_funds, n_days)

    # 组合日收益率
    portfolio_returns = np.dot(fund_weights, returns_array)

    # 组合年化波动率
    portfolio_vol_daily = np.std(portfolio_returns, ddof=1) if len(portfolio_returns) > 1 else 0
    portfolio_vol_annual = portfolio_vol_daily * math.sqrt(TRADING_DAYS)

    # VaR 95%（1日）
    var_95 = np.percentile(portfolio_returns, 5) if len(portfolio_returns) > 0 else 0

    # CVaR 95%
    var_threshold = np.percentile(portfolio_returns, 5) if len(portfolio_returns) > 0 else 0
    tail_returns = portfolio_returns[portfolio_returns <= var_threshold]
    cvar_95 = np.mean(tail_returns) if len(tail_returns) > 0 else var_95

    # 组合净值曲线
    portfolio_nav = np.cumprod(1 + portfolio_returns)
    portfolio_nav_list = portfolio_nav.tolist()

    # 最大回撤
    max_dd, recovery_days = _calc_max_drawdown(portfolio_nav_list)

    # 年化收益率
    total_return = portfolio_nav_list[-1] - 1 if portfolio_nav_list else 0
    n_days = len(portfolio_returns)
    annual_return = (1 + total_return) ** (TRADING_DAYS / max(n_days, 1)) - 1 if n_days > 0 else 0

    # 夏普比率
    sharpe = (annual_return - RISK_FREE_RATE) / portfolio_vol_annual if portfolio_vol_annual > 0 else 0

    # Sortino比率（只考虑下行波动）
    downside_returns = portfolio_returns[portfolio_returns < 0]
    downside_vol = np.std(downside_returns, ddof=1) * math.sqrt(TRADING_DAYS) if len(downside_returns) > 1 else 0
    sortino = (annual_return - RISK_FREE_RATE) / downside_vol if downside_vol > 0 else 0

    # Effective N（有效分散数）
    if len(returns_array) > 1:
        corr_matrix = np.corrcoef(returns_array)
        if corr_matrix.ndim == 2 and corr_matrix.shape[0] > 1:
            # Effective N = (Σw)² / Σ(w_i × w_j × ρ_ij)
            numerator = (fund_weights.sum()) ** 2
            denominator = 0
            for i in range(len(fund_weights)):
                for j in range(len(fund_weights)):
                    denominator += fund_weights[i] * fund_weights[j] * corr_matrix[i, j]
            effective_n = numerator / denominator if denominator > 0 else len(fund_weights)
            avg_corr = float(np.mean(corr_matrix[np.triu_indices(len(fund_weights), k=1)])) if len(fund_weights) > 1 else 0
        else:
            effective_n = 1.0
            avg_corr = 0.0
    else:
        effective_n = 1.0
        avg_corr = 0.0

    # 风险贡献（各基金对组合方差的贡献）
    risk_contributions = []
    if len(returns_array) > 1 and portfolio_vol_daily > 0:
        cov_matrix = np.cov(returns_array)
        portfolio_var = fund_weights @ cov_matrix @ fund_weights
        if portfolio_var > 0:
            marginal_contrib = cov_matrix @ fund_weights
            risk_contrib_raw = fund_weights * marginal_contrib / portfolio_var
            for i, code in enumerate(aligned["fund_codes"]):
                risk_contributions.append({
                    "fund_code": code,
                    "fund_name": next((h["fund_name"] for h in active_holdings if h["fund_code"] == code), code),
                    "weight": round(float(fund_weights[i]), 4),
                    "risk_contribution": round(float(risk_contrib_raw[i]), 4),
                })
    risk_contributions.sort(key=lambda x: x["risk_contribution"], reverse=True)

    # 组合总市值
    total_value = sum(h["current_value"] for h in active_holdings)

    return {
        "portfolio_volatility": round(float(portfolio_vol_annual), 4),
        "var_95_daily": round(float(var_95), 4),
        "cvar_95_daily": round(float(cvar_95), 4),
        "var_95_amount": round(float(var_95 * total_value), 2),
        "cvar_95_amount": round(float(cvar_95 * total_value), 2),
        "max_drawdown": round(float(max_dd), 4),
        "max_drawdown_recovery_days": recovery_days,
        "annual_return": round(float(annual_return), 4),
        "sharpe_ratio": round(float(sharpe), 4),
        "sortino_ratio": round(float(sortino), 4),
        "effective_n": round(float(effective_n), 2),
        "avg_correlation": round(float(avg_corr), 4),
        "total_value": round(float(total_value), 2),
        "fund_count": len(active_codes),
        "data_days": n_days,
        "risk_contributions": risk_contributions[:10],  # Top10
        "data_status": "ok",
    }


def _default_risk_metrics(reason: str = "") -> dict:
    """风险度量降级默认值。"""
    return {
        "portfolio_volatility": 0,
        "var_95_daily": 0,
        "cvar_95_daily": 0,
        "var_95_amount": 0,
        "cvar_95_amount": 0,
        "max_drawdown": 0,
        "max_drawdown_recovery_days": 0,
        "annual_return": 0,
        "sharpe_ratio": 0,
        "sortino_ratio": 0,
        "effective_n": 1.0,
        "avg_correlation": 0,
        "total_value": 0,
        "fund_count": 0,
        "data_days": 0,
        "risk_contributions": [],
        "data_status": "degraded",
        "degraded_reason": reason,
    }


# ════════════════════════════════════════════════════════════
# 模块2：7维体检组合聚合
# ════════════════════════════════════════════════════════════

def calculate_portfolio_health_report(user_id: str = "default", force_refresh: bool = False) -> dict:
    """组合7维体检报告（按持仓权重聚合）。

    对每只持仓基金调用单基金7维体检，按持仓权重加权聚合。
    """
    weights_data = _get_portfolio_weights(user_id)
    if not weights_data:
        return _default_portfolio_report("无持仓数据")

    # 过滤有效持仓
    active_holdings = [w for w in weights_data if w["weight"] >= 0.01]
    if not active_holdings:
        return _default_portfolio_report("所有持仓权重过小")

    # 对每只基金调用7维体检
    from services.fund_analysis import calculate_fund_health_report
    holding_reports = []
    for h in active_holdings:
        try:
            report = calculate_fund_health_report(h["fund_code"], force_refresh=force_refresh)
            holding_reports.append({
                "fund_code": h["fund_code"],
                "fund_name": h["fund_name"],
                "weight": h["weight"],
                "current_value": h["current_value"],
                "health_report": report,
            })
        except Exception as e:
            logger.warning(f"[portfolio_intel] 基金 {h['fund_code']} 7维体检失败: {e}")
            holding_reports.append({
                "fund_code": h["fund_code"],
                "fund_name": h["fund_name"],
                "weight": h["weight"],
                "current_value": h["current_value"],
                "health_report": None,
                "error": str(e),
            })

    # 聚合7维分数（按权重加权）
    dimension_keys = ["quality", "drawdown", "trend", "capital", "sentiment", "valuation", "fundamental"]
    portfolio_report = {}
    total_weight_used = 0

    for dim_key in dimension_keys:
        weighted_score = 0
        used_weight = 0
        for hr in holding_reports:
            if hr.get("health_report") is None:
                continue
            dim_data = hr["health_report"].get("report", {}).get(dim_key)
            if dim_data and dim_data.get("score") is not None:
                weighted_score += dim_data["score"] * hr["weight"]
                used_weight += hr["weight"]

        if used_weight > 0:
            avg_score = weighted_score / used_weight
            portfolio_report[dim_key] = {
                "score": round(avg_score, 1),
                "rating": _score_to_rating(avg_score),
            }
            if dim_key == "quality":
                total_weight_used = max(total_weight_used, used_weight)

    # 组合总分（7维加权，与单基金一致）
    dim_weights_7 = {
        "quality": 0.17, "drawdown": 0.15, "trend": 0.15,
        "capital": 0.13, "sentiment": 0.12, "valuation": 0.15, "fundamental": 0.13,
    }
    # 只用存在的维度计算（债基可能无fundamental）
    available_dims = [k for k in dimension_keys if k in portfolio_report]
    if "fundamental" not in available_dims and len(available_dims) == 6:
        dim_weights_6 = {
            "quality": 0.20, "drawdown": 0.20, "trend": 0.15,
            "capital": 0.15, "sentiment": 0.10, "valuation": 0.20,
        }
        weights_to_use = dim_weights_6
    else:
        weights_to_use = dim_weights_7

    total_score = sum(
        portfolio_report[k]["score"] * weights_to_use.get(k, 0)
        for k in available_dims
    )
    # 归一化（维度缺失时权重不完整）
    total_weight_sum = sum(weights_to_use.get(k, 0) for k in available_dims)
    if total_weight_sum > 0:
        total_score = total_score / total_weight_sum

    # 组合风险度量
    risk_metrics = calculate_portfolio_risk_metrics(user_id)

    # 组合决策矩阵（基于组合7维 + 风险度量）
    portfolio_decision = _build_portfolio_decision(portfolio_report, risk_metrics)

    return {
        "user_id": user_id,
        "portfolio_total_score": round(total_score, 1),
        "portfolio_rating": _score_to_rating(total_score),
        "portfolio_report": portfolio_report,
        "portfolio_decision": portfolio_decision,
        "risk_metrics": risk_metrics,
        "holding_reports": [
            {
                "fund_code": hr["fund_code"],
                "fund_name": hr["fund_name"],
                "weight": round(hr["weight"], 4),
                "current_value": hr["current_value"],
                "total_score": hr["health_report"].get("total_score") if hr.get("health_report") else None,
                "rating": hr["health_report"].get("rating") if hr.get("health_report") else None,
                "decision": hr["health_report"].get("decision_matrix", {}).get("action_label") if hr.get("health_report") else None,
            }
            for hr in holding_reports
        ],
        "holding_count": len(active_holdings),
        "data_status": "ok" if total_weight_used > 0 else "degraded",
    }


def _build_portfolio_decision(portfolio_report: dict, risk_metrics: dict) -> dict:
    """组合决策矩阵（基于组合7维 + 风险度量）。"""
    quality_score = portfolio_report.get("quality", {}).get("score", 50)
    valuation_score = portfolio_report.get("valuation", {}).get("score", 50)
    trend_score = portfolio_report.get("trend", {}).get("score", 50)
    fundamental_score = portfolio_report.get("fundamental", {}).get("score", 50)

    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")
    effective_n = risk_metrics.get("effective_n", 1)
    max_dd = risk_metrics.get("max_drawdown", 0)
    sharpe = risk_metrics.get("sharpe_ratio", 0)

    # 决策逻辑
    if effective_n < 1.5 and len(portfolio_report) > 3:
        action = "reduce"
        reason = f"组合分散不足（Effective N={effective_n}），建议增加低相关资产"
    elif max_dd > 0.4:
        action = "reduce"
        reason = f"组合历史最大回撤{max_dd*100:.0f}%，风险偏高，建议降低仓位"
    elif valuation_level == "high" and quality_score < 50:
        action = "reduce"
        reason = "组合估值偏高+质量一般，建议减仓"
    elif valuation_level == "low" and quality_score >= 60 and effective_n >= 2:
        action = "strong_buy"
        reason = "组合低估+质量良好+分散充分，建议加仓"
    elif valuation_level == "low":
        action = "dca"
        reason = "组合整体低估，适合定投"
    elif sharpe < 0 and max_dd > 0.2:
        action = "wait"
        reason = f"组合夏普比率为负+回撤较大，建议观望"
    else:
        action = "hold"
        reason = "组合配置均衡，持有观察"

    action_labels = {
        "strong_buy": "强烈加仓",
        "dca": "定投加仓",
        "hold": "持有",
        "reduce": "减仓",
        "wait": "等待",
    }

    return {
        "action": action,
        "action_label": action_labels.get(action, "持有"),
        "reason": reason,
        "quality_score": round(quality_score, 1),
        "valuation_level": valuation_level,
        "effective_n": effective_n,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
    }


def _default_portfolio_report(reason: str = "") -> dict:
    """组合报告降级默认值。"""
    return {
        "user_id": "default",
        "portfolio_total_score": 0,
        "portfolio_rating": "fair",
        "portfolio_report": {},
        "portfolio_decision": {"action": "hold", "action_label": "持有", "reason": reason},
        "risk_metrics": _default_risk_metrics(reason),
        "holding_reports": [],
        "holding_count": 0,
        "data_status": "degraded",
        "degraded_reason": reason,
    }


# ════════════════════════════════════════════════════════════
# 模块3：大师矩阵组合版
# ════════════════════════════════════════════════════════════

def build_portfolio_master_matrix(portfolio_report: dict, risk_metrics: dict, holding_reports: list = None) -> dict:
    """6位大师基于组合数据做组合视角决策。

    Args:
        portfolio_report: calculate_portfolio_health_report 的 portfolio_report 字段
        risk_metrics: calculate_portfolio_risk_metrics 的返回
        holding_reports: 各基金7维明细（用于统计护城河覆盖率等）

    Returns:
        {masters: [6位大师组合视角评分], consensus: {...}}
    """
    from services.master_perspectives import _detect_consensus, ACTION_LABELS, MASTER_META

    quality_score = portfolio_report.get("quality", {}).get("score", 50)
    valuation_score = portfolio_report.get("valuation", {}).get("score", 50)
    trend_score = portfolio_report.get("trend", {}).get("score", 50)
    capital_score = portfolio_report.get("capital", {}).get("score", 50)
    sentiment_score = portfolio_report.get("sentiment", {}).get("score", 50)
    drawdown_score = portfolio_report.get("drawdown", {}).get("score", 50)
    fundamental_score = portfolio_report.get("fundamental", {}).get("score", 50)

    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")
    effective_n = risk_metrics.get("effective_n", 1)
    avg_corr = risk_metrics.get("avg_correlation", 0)
    max_dd = risk_metrics.get("max_drawdown", 0)
    sharpe = risk_metrics.get("sharpe_ratio", 0)

    # 统计持仓中"有护城河"的比例
    moat_coverage = 0
    fast_grower_ratio = 0
    if holding_reports:
        total_weight = sum(hr.get("weight", 0) for hr in holding_reports if hr.get("total_score"))
        if total_weight > 0:
            for hr in holding_reports:
                report = hr.get("health_report") or {}
                fund_report = report.get("report", {})
                fund_detail = report.get("details", {}).get("fundamental") or {}
                stock_scores = fund_detail.get("stock_scores", []) if fund_detail else []

                if stock_scores:
                    # 护城河：盈利能力平均分≥75
                    prof_scores = [s.get("profitability", {}).get("score", 50) for s in stock_scores]
                    avg_prof = sum(prof_scores) / len(prof_scores) if prof_scores else 50
                    if avg_prof >= 75:
                        moat_coverage += hr.get("weight", 0)

                    # 快速增长：成长性平均分≥70
                    growth_scores = [s.get("growth", {}).get("score", 50) for s in stock_scores]
                    avg_growth = sum(growth_scores) / len(growth_scores) if growth_scores else 50
                    if avg_growth >= 70:
                        fast_grower_ratio += hr.get("weight", 0)

    masters = []

    # 巴菲特组合视角：护城河覆盖率
    if moat_coverage >= 0.5 and valuation_level == "low":
        action, reason = "strong_buy", f"组合{moat_coverage*100:.0f}%持仓有护城河+整体低估，巴菲特式组合"
    elif moat_coverage >= 0.5:
        action, reason = "hold", f"组合{moat_coverage*100:.0f}%持仓有护城河，持有等待低估"
    elif moat_coverage < 0.2:
        action, reason = "wait", f"仅{moat_coverage*100:.0f}%持仓有护城河，巴菲特不会持有无护城河组合"
    else:
        action, reason = "hold", f"组合{moat_coverage*100:.0f}%持仓有护城河，部分标的需替换"
    masters.append({
        "master_key": "buffett", "master_name": "巴菲特", "master_icon": "🏰",
        "core_philosophy": "护城河覆盖率+安全边际",
        "score": round(quality_score * 0.4 + fundamental_score * 0.4 + valuation_score * 0.2, 1),
        "rating": _score_to_rating(quality_score * 0.4 + fundamental_score * 0.4 + valuation_score * 0.2),
        "action": action, "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {"moat_coverage": round(moat_coverage, 2)},
    })

    # 林奇组合视角：快速增长比例
    if fast_grower_ratio >= 0.5 and valuation_level != "high":
        action, reason = "strong_buy", f"组合{fast_grower_ratio*100:.0f}%为快速增长+估值合理，林奇式组合"
    elif fast_grower_ratio >= 0.3:
        action, reason = "dca", f"组合{fast_grower_ratio*100:.0f}%为快速增长，定投加仓"
    elif valuation_level == "high":
        action, reason = "reduce", "组合估值偏高，林奇会获利了结"
    else:
        action, reason = "hold", f"组合{fast_grower_ratio*100:.0f}%为快速增长，持有观察"
    masters.append({
        "master_key": "lynch", "master_name": "林奇", "master_icon": "📈",
        "core_philosophy": "快速增长比例+PEG分布",
        "score": round(fundamental_score * 0.5 + valuation_score * 0.3 + trend_score * 0.2, 1),
        "rating": _score_to_rating(fundamental_score * 0.5 + valuation_score * 0.3 + trend_score * 0.2),
        "action": action, "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {"fast_grower_ratio": round(fast_grower_ratio, 2)},
    })

    # 博格组合视角：成本+指数化+均值回归
    if effective_n >= 3 and valuation_level != "high":
        action, reason = "strong_buy", f"组合分散良好(N={effective_n})+估值合理，博格式理想组合"
    elif valuation_level == "high":
        action, reason = "reduce", "组合估值偏高，博格警惕均值回归"
    elif effective_n < 1.5:
        action, reason = "wait", f"组合分散不足(N={effective_n})，博格建议增加指数化配置"
    else:
        action, reason = "hold", f"组合分散度N={effective_n}，长期持有"
    masters.append({
        "master_key": "bogle", "master_name": "博格", "master_icon": "💰",
        "core_philosophy": "成本+指数化+均值回归",
        "score": round(quality_score * 0.3 + valuation_score * 0.4 + (effective_n * 20), 1),
        "rating": _score_to_rating(min(100, quality_score * 0.3 + valuation_score * 0.4 + effective_n * 20)),
        "action": action, "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {"effective_n": effective_n},
    })

    # 马克斯组合视角：周期位置+逆向
    if max_dd > 0.3 and valuation_level == "low":
        action, reason = "strong_buy", f"组合回撤{max_dd*100:.0f}%+低估，马克斯式逆向加仓"
    elif max_dd > 0.2 and sentiment_score < 50:
        action, reason = "dca", f"组合回撤{max_dd*100:.0f}%+情绪偏恐，分批逆向"
    elif valuation_level == "high" and sentiment_score > 60:
        action, reason = "reduce", "组合估值高+情绪贪婪，马克斯会减仓"
    else:
        action, reason = "hold", f"组合周期中位，持有观察"
    masters.append({
        "master_key": "marks", "master_name": "马克斯", "master_icon": "🔄",
        "core_philosophy": "周期位置+逆向投资",
        "score": round(drawdown_score * 0.4 + sentiment_score * 0.3 + valuation_score * 0.3, 1),
        "rating": _score_to_rating(drawdown_score * 0.4 + sentiment_score * 0.3 + valuation_score * 0.3),
        "action": action, "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {"max_drawdown": max_dd, "sentiment_score": sentiment_score},
    })

    # 达利欧组合视角：全天候+风险平价
    if effective_n >= 3 and max_dd < 0.2:
        action, reason = "hold", f"组合全天候配置(N={effective_n},回撤{max_dd*100:.0f}%)，达利欧式理想"
    elif effective_n < 1.5:
        action, reason = "reduce", f"组合分散不足(N={effective_n})，达利欧建议再平衡"
    elif max_dd > 0.35:
        action, reason = "reduce", f"组合回撤{max_dd*100:.0f}%过大，达利欧会降低风险"
    elif avg_corr > 0.7:
        action, reason = "wait", f"组合平均相关性{avg_corr:.2f}过高，达利欧建议增加低相关资产"
    else:
        action, reason = "hold", f"组合风险平衡，持有观察"
    masters.append({
        "master_key": "dalio", "master_name": "达利欧", "master_icon": "⚖️",
        "core_philosophy": "全天候配置+风险平价",
        "score": round(min(100, effective_n * 25 + (1 - max_dd) * 50), 1),
        "rating": _score_to_rating(min(100, effective_n * 25 + (1 - max_dd) * 50)),
        "action": action, "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {"effective_n": effective_n, "avg_correlation": avg_corr, "max_drawdown": max_dd},
    })

    # 段永平组合视角：好生意+好公司比例
    if fundamental_score >= 70 and valuation_level == "low":
        action, reason = "strong_buy", "组合重仓股基本面良好+整体低估，段永平式组合"
    elif fundamental_score < 40:
        action, reason = "reduce", "组合基本面偏弱，段永平会减仓"
    elif valuation_level == "high":
        action, reason = "reduce", "组合估值偏高，段永平不追高"
    else:
        action, reason = "hold", "组合质地尚可+价格合理，持有"
    masters.append({
        "master_key": "duanyongping", "master_name": "段永平", "master_icon": "🎯",
        "core_philosophy": "好生意+好公司比例+好价格",
        "score": round(quality_score * 0.35 + fundamental_score * 0.3 + valuation_score * 0.2 + trend_score * 0.15, 1),
        "rating": _score_to_rating(quality_score * 0.35 + fundamental_score * 0.3 + valuation_score * 0.2 + trend_score * 0.15),
        "action": action, "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {"fundamental_score": fundamental_score, "moat_coverage": round(moat_coverage, 2)},
    })

    # 共识检测
    consensus = _detect_consensus(masters)

    return {
        "masters": masters,
        "consensus": consensus,
    }
