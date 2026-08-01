"""组合风险建模 — 相关性矩阵 / 边际风险贡献 / 分数凯利 / 协方差收缩。

三模块联动优化 P0-S1（2026-08-01）：见 doc/plans/2026-08-01-三模块联动优化.md

定位：补齐智能补仓最大的金融风险盲区——「只看单标的、不看组合相关性」。
补仓前计算标的与现有持仓的相关性，对高相关品种降权，避免"越补越集中"；
用分数凯利 + 协方差收缩提升仓位估计的鲁棒性，降低估计误差导致的过配。

设计原则：
- 核心数学为纯函数（可单测）：prices_to_returns / correlation_matrix /
  shrink_covariance / portfolio_volatility / marginal_risk_contribution / fractional_kelly
- 所有阈值/系数走 system_config（smartadd.risk.* 组，金融严谨性，禁止硬编码）
- 优雅降级：价格数据不足/不可得时 downweight=1.0（不惩罚），不阻断补仓主流程
"""
import logging
import math

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 纯函数层（可单测，无外部依赖）
# ════════════════════════════════════════════════════════════════

def prices_to_returns(prices: list[float]) -> list[float]:
    """价格序列 → 简单收益率序列（长度 n-1）。"""
    if not prices or len(prices) < 2:
        return []
    out = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev:
            out.append((prices[i] - prev) / prev)
    return out


def align_returns(returns: dict[str, list[float]]) -> dict[str, list[float]]:
    """按最短序列长度对齐各资产收益率（取尾部对齐，保证同期可比）。"""
    if not returns:
        return {}
    min_len = min(len(v) for v in returns.values())
    if min_len < 2:
        return {}
    return {k: v[-min_len:] for k, v in returns.items() if len(v) >= min_len}


def pearson_corr(a: list[float], b: list[float]) -> float | None:
    """皮尔逊相关系数。样本不足或零方差返回 None。"""
    n = min(len(a), len(b))
    if n < 5:
        return None
    a, b = a[-n:], b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    sxx = sum((x - ma) ** 2 for x in a)
    syy = sum((y - mb) ** 2 for y in b)
    sxy = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def correlation_matrix(returns: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """两两相关系数矩阵。返回 {code_i: {code_j: corr}}。"""
    aligned = align_returns(returns)
    codes = list(aligned.keys())
    matrix: dict[str, dict[str, float]] = {c: {} for c in codes}
    for i, ci in enumerate(codes):
        for cj in codes[i:]:
            if ci == cj:
                matrix[ci][cj] = 1.0
                continue
            corr = pearson_corr(aligned[ci], aligned[cj])
            if corr is not None:
                matrix[ci][cj] = round(corr, 4)
                matrix[cj][ci] = round(corr, 4)
    return matrix


def _mean_var(series: list[float]) -> tuple[float, float]:
    n = len(series)
    if n == 0:
        return 0.0, 0.0
    m = sum(series) / n
    var = sum((x - m) ** 2 for x in series) / n
    return m, var


def shrink_covariance(returns: dict[str, list[float]], shrinkage: float | None = None
                      ) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """协方差矩阵 + Ledoit-Wolf 风格收缩（向对角阵收缩，提升小样本鲁棒性）。

    金融原理：样本协方差在高维/短样本下估计噪声大，向"对角阵（仅保留各自方差）"
    收缩可显著降低估计误差。shrinkage∈[0,1]，越大越接近对角阵。
    未指定 shrinkage 时用经验启发式（样本越少收缩越强）。

    Returns:
        (cov_matrix, means)  cov_matrix: {i:{j:cov}}，means: {i: mean_return}
    """
    aligned = align_returns(returns)
    codes = list(aligned.keys())
    n_obs = len(next(iter(aligned.values()))) if aligned else 0
    means = {c: _mean_var(aligned[c])[0] for c in codes}
    # 样本协方差
    raw: dict[str, dict[str, float]] = {c: {} for c in codes}
    for i, ci in enumerate(codes):
        for cj in codes[i:]:
            a, b = aligned[ci], aligned[cj]
            ma, mb = means[ci], means[cj]
            cov = sum((a[k] - ma) * (b[k] - mb) for k in range(n_obs)) / n_obs if n_obs else 0.0
            raw[ci][cj] = cov
            raw[cj][ci] = cov
    # 收缩强度：样本越少越强（30 条→约0.5，120 条→约0.2）
    if shrinkage is None:
        shrinkage = max(0.0, min(0.8, 30.0 / max(n_obs, 1)))
    shrunk: dict[str, dict[str, float]] = {c: {} for c in codes}
    for ci in codes:
        for cj in codes:
            if ci == cj:
                shrunk[ci][cj] = raw[ci][cj]  # 对角不收缩（保留各自方差）
            else:
                shrunk[ci][cj] = (1 - shrinkage) * raw[ci][cj]
    return shrunk, means


def portfolio_volatility(weights: dict[str, float], cov: dict[str, dict[str, float]]) -> float:
    """组合波动率 σ = sqrt(wᵀ Σ w)。"""
    total = 0.0
    for i, wi in weights.items():
        if i not in cov:
            continue
        for j, wj in weights.items():
            if j in cov.get(i, {}):
                total += wi * wj * cov[i][j]
    return math.sqrt(max(total, 0.0))


def marginal_risk_contribution(weights: dict[str, float], cov: dict[str, dict[str, float]],
                               asset: str) -> float | None:
    """资产 asset 的边际风险贡献 ∂σ/∂w = (Σw)_asset / σ。

    金融含义：每增加一单位该资产权重，组合波动率增加多少。
    高边际贡献意味着该资产是当前组合风险的主要来源，补仓需谨慎。
    """
    sigma = portfolio_volatility(weights, cov)
    if sigma <= 0 or asset not in cov:
        return None
    sigma_w = sum(cov[asset].get(j, 0.0) * wj for j, wj in weights.items())
    return sigma_w / sigma


def fractional_kelly(mu: float, sigma: float, rf: float = 0.0,
                     fraction: float = 0.5, cap: float = 0.25) -> dict:
    """分数凯利仓位（鲁棒版）。

    金融原理：全凯利 f*=(μ-rf)/σ² 对 μ/σ 估计误差极敏感，易过配；
    取分数凯利（默认半凯利 fraction=0.5）+ 上限 cap，显著降低过配风险。

    Args:
        mu: 预期收益率（年化或周期）
        sigma: 收益率标准差（与 mu 同周期）
        rf: 无风险利率（同周期）
        fraction: 凯利分数（0.5=半凯利）
        cap: 仓位上限（占组合比例）

    Returns:
        {raw, fractional, capped, fraction, cap}
    """
    if sigma <= 0:
        return {"raw": 0.0, "fractional": 0.0, "capped": 0.0, "fraction": fraction, "cap": cap}
    raw = (mu - rf) / (sigma ** 2)
    raw = max(raw, 0.0)            # 负期望不下注
    fractional = raw * fraction
    capped = min(fractional, cap)
    return {
        "raw": round(raw, 4),
        "fractional": round(fractional, 4),
        "capped": round(capped, 4),
        "fraction": fraction,
        "cap": cap,
    }


# ════════════════════════════════════════════════════════════════
# 数据获取层（本地优先 + akshare 兜底，优雅降级）
# ════════════════════════════════════════════════════════════════

def _fetch_closes(index_code: str, days: int) -> list[float]:
    """获取指数收盘价序列（升序）。本地 index_price_history 优先，akshare 兜底。"""
    if not index_code:
        return []
    try:
        from services.index.index_history_fetcher import get_index_price_history
        history = get_index_price_history(index_code, days=days * 2)
        closes = [h["close"] for h in history if h.get("close") is not None]
        if len(closes) >= 30:
            return closes[-days:]
    except Exception as e:
        logger.debug(f"[portfolio_risk] 本地价格查询失败 {index_code}: {e}")
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=index_code)
        if df is not None and len(df) >= 30:
            return [float(c) for c in df["close"].values[-days:]]
    except Exception as e:
        logger.debug(f"[portfolio_risk] akshare 价格兜底失败 {index_code}: {e}")
    return []


# ════════════════════════════════════════════════════════════════
# 组合风险评估（配置驱动，供智能补仓调用）
# ════════════════════════════════════════════════════════════════

def assess_add_position_risk(target_index_code: str,
                             holding_index_codes: list[str],
                             target_weight: float | None = None,
                             holding_weights: dict[str, float] | None = None,
                             lookback_days: int | None = None) -> dict:
    """评估「对 target 补仓」的组合风险，返回降权因子与风险标记。

    这是智能补仓 S1 的核心入口：补仓前判断该标的与现有持仓的相关性，
    若高度相关则降权（避免越补越集中），并给出组合 β / 边际风险贡献提示。

    Args:
        target_index_code: 拟补仓标的的指数代码
        holding_index_codes: 现有持仓的指数代码列表
        target_weight: 拟补仓后该标的占组合权重（0-1，可选，用于 β/MRC）
        holding_weights: 现有各持仓权重 {index_code: weight}（可选）
        lookback_days: 相关系数回看天数（None 则读配置）

    Returns:
        {enabled, data_quality, correlation, max_correlation, avg_correlation,
         highly_correlated, corr_downweight, portfolio_beta_proxy,
         marginal_vol_contribution, kelly, flags}
    """
    result = {
        "enabled": False,
        "target_index_code": target_index_code,
        "data_quality": "unavailable",
        "correlation": {},
        "max_correlation": None,
        "avg_correlation": None,
        "highly_correlated": [],
        "corr_downweight": 1.0,
        "portfolio_beta_proxy": None,
        "marginal_vol_contribution": None,
        "kelly": None,
        "flags": [],
    }
    try:
        from db.config import get_config_bool, get_config_int, get_config_float
        if not get_config_bool("smartadd.risk.correlation_enabled", True):
            return result
        result["enabled"] = True
        if lookback_days is None:
            lookback_days = get_config_int("smartadd.risk.corr_lookback_days", 120)
        corr_high = get_config_float("smartadd.risk.corr_high_threshold", 0.7)
        downweight_factor = get_config_float("smartadd.risk.corr_downweight_factor", 0.5)
        max_beta = get_config_float("smartadd.risk.max_portfolio_beta", 1.3)
        kelly_fraction = get_config_float("smartadd.risk.fractional_kelly", 0.5)
        kelly_cap = get_config_float("smartadd.risk.kelly_cap_pct", 25.0) / 100.0
        mrc_enabled = get_config_bool("smartadd.risk.marginal_risk_enabled", True)
        shrink_enabled = get_config_bool("smartadd.risk.shrinkage_enabled", True)
    except Exception as e:
        logger.warning(f"[portfolio_risk] 配置读取失败，降级: {e}")
        return result

    if not target_index_code:
        return result

    # 去重持仓指数（排除与目标相同的）
    holding_codes = [c for c in dict.fromkeys(holding_index_codes or []) if c and c != target_index_code]

    # 拉取价格 → 收益率
    returns: dict[str, list[float]] = {}
    target_closes = _fetch_closes(target_index_code, lookback_days)
    if len(target_closes) >= 30:
        returns[target_index_code] = prices_to_returns(target_closes)
    for code in holding_codes:
        closes = _fetch_closes(code, lookback_days)
        if len(closes) >= 30:
            returns[code] = prices_to_returns(closes)

    if target_index_code not in returns:
        result["data_quality"] = "unavailable"
        result["flags"].append("目标标的价格数据不足，相关性评估跳过（不惩罚）")
        return result
    if not holding_codes or len(returns) < 2:
        result["data_quality"] = "no_holdings"
        result["flags"].append("无可比持仓或持仓数据不足，按独立资产处理")
        # 仍可基于目标自身波动给出凯利参考
        _attach_kelly(result, returns, target_index_code, kelly_fraction, kelly_cap, shrink_enabled)
        return result

    result["data_quality"] = "ok"

    # 相关性矩阵
    corr_matrix = correlation_matrix(returns)
    target_corr = corr_matrix.get(target_index_code, {})
    holding_corrs = {c: target_corr[c] for c in holding_codes if c in target_corr}
    result["correlation"] = holding_corrs

    if holding_corrs:
        vals = list(holding_corrs.values())
        result["max_correlation"] = round(max(vals), 4)
        result["avg_correlation"] = round(sum(vals) / len(vals), 4)
        highly = [c for c, v in holding_corrs.items() if v >= corr_high]
        result["highly_correlated"] = highly
        # 降权：存在高相关持仓 → 补仓金额乘以降权因子
        if highly:
            result["corr_downweight"] = downweight_factor
            result["flags"].append(
                f"与 {len(highly)} 个持仓高度相关（≥{corr_high}），补仓金额降权至 {int(downweight_factor*100)}%"
            )
        elif result["avg_correlation"] is not None and result["avg_correlation"] >= corr_high:
            result["corr_downweight"] = downweight_factor
            result["flags"].append(f"平均相关性 {result['avg_correlation']} 偏高，补仓金额降权")

    # 协方差（可选收缩）+ 组合 β 代理 + 边际风险贡献
    cov, means = shrink_covariance(returns) if shrink_enabled else (_raw_cov(returns), {c: _mean_var(returns[c])[0] for c in returns})

    # 组合 β 代理：以等权持仓为基准组合，目标对其 β = cov(target, portfolio)/var(portfolio)
    try:
        weights = dict(holding_weights or {})
        if not weights:
            weights = {c: 1.0 / len(holding_codes) for c in holding_codes}
        # 组合收益 = Σ w·r
        aligned = align_returns(returns)
        port_codes = [c for c in weights if c in aligned]
        if port_codes and target_index_code in aligned:
            wsum = sum(weights[c] for c in port_codes) or 1.0
            n_obs = len(aligned[target_index_code])
            port_ret = [sum(weights[c] * aligned[c][k] for c in port_codes) / wsum for k in range(n_obs)]
            _, pvar = _mean_var(port_ret)
            tmean = means.get(target_index_code, 0.0)
            pmean = sum(port_ret) / n_obs
            cov_tp = sum((aligned[target_index_code][k] - tmean) * (port_ret[k] - pmean) for k in range(n_obs)) / n_obs
            if pvar > 0:
                beta = cov_tp / pvar
                result["portfolio_beta_proxy"] = round(beta, 4)
                if beta > max_beta:
                    result["flags"].append(f"目标对持仓组合 β≈{round(beta,2)} 偏高（>{max_beta}），系统性风险敞口大")
    except Exception as e:
        logger.debug(f"[portfolio_risk] β 计算失败: {e}")

    # 边际风险贡献
    if mrc_enabled:
        try:
            w_with_target = dict(holding_weights or {c: 1.0 / max(len(holding_codes), 1) for c in holding_codes})
            if target_weight is not None:
                w_with_target[target_index_code] = target_weight
            else:
                w_with_target[target_index_code] = 0.1  # 试探性 10% 权重估算 MRC
            mrc = marginal_risk_contribution(w_with_target, cov, target_index_code)
            if mrc is not None:
                result["marginal_vol_contribution"] = round(mrc, 4)
        except Exception as e:
            logger.debug(f"[portfolio_risk] MRC 计算失败: {e}")

    _attach_kelly(result, returns, target_index_code, kelly_fraction, kelly_cap, shrink_enabled)
    return result


def _raw_cov(returns: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """未收缩的样本协方差矩阵。"""
    cov, _ = shrink_covariance(returns, shrinkage=0.0)
    return cov


def _attach_kelly(result: dict, returns: dict[str, list[float]], target_code: str,
                  fraction: float, cap: float, shrink_enabled: bool):
    """基于目标自身收益分布附加分数凯利仓位参考（年化近似）。"""
    try:
        r = returns.get(target_code)
        if not r or len(r) < 30:
            return
        mu_d, var_d = _mean_var(r)
        sigma_d = math.sqrt(var_d)
        # 日频 → 年化近似（252 交易日）
        mu_ann = mu_d * 252
        sigma_ann = sigma_d * math.sqrt(252)
        kelly = fractional_kelly(mu_ann, sigma_ann, rf=0.0, fraction=fraction, cap=cap)
        kelly["mu_annualized"] = round(mu_ann, 4)
        kelly["sigma_annualized"] = round(sigma_ann, 4)
        result["kelly"] = kelly
    except Exception as e:
        logger.debug(f"[portfolio_risk] 凯利计算失败: {e}")
