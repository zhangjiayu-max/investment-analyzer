"""P1-2 组合优化引擎。

实现三种经典资产配置模型：
  - 马科维茨效率前沿（均值-方差优化 + 蒙特卡洛）
  - 风险平价（Risk Parity，按波动率倒数分配）
  - 黑利特曼模型（Black-Litterman，市场均衡 + 主观观点融合）

数据来源：db.valuations 的 get_valuation_history，以指数 PE/PB 序列作为价格代理。
不调用 LLM。numpy 可用时优先使用，否则降级为纯 Python 实现。
"""

import logging
import math
import random
from datetime import datetime, timedelta

# numpy 可选依赖：优先使用，缺失时降级为纯 Python
try:  # pragma: no cover - 依赖环境
    import numpy as np
    HAS_NUMPY = True
except Exception:  # noqa: BLE001
    HAS_NUMPY = False

from db.valuations import get_valuation_history
from db.portfolio import list_holdings

logger = logging.getLogger(__name__)

# ── 全局参数 ──────────────────────────────────────
RF = 0.03           # 无风险利率
LAMBDA = 2.5        # 风险厌恶系数（黑利特曼市场均衡）
TAU = 0.025         # 黑利特曼观点缩放系数
TRADING_DAYS = 252  # 年化交易日数
MIN_DATA_DAYS = 30  # 最少数据天数，不足则返回空结果


# ══════════════════════════════════════════════════════
# 数据获取与预处理
# ══════════════════════════════════════════════════════

def _parse_date(d) -> datetime | None:
    """安全解析 ISO 日期字符串，失败返回 None。"""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d
    try:
        return datetime.fromisoformat(str(d)[:10])
    except Exception:
        return None


def _date_range_to_days(start_date, end_date) -> int:
    """把 [start_date, end_date] 转为天数窗口。"""
    end = _parse_date(end_date) or datetime.now()
    start = _parse_date(start_date) or (end - timedelta(days=365))
    delta = (end - start).days
    return max(delta, MIN_DATA_DAYS)


def _fetch_asset_series(index_code: str, start_date, end_date) -> list[tuple[str, float]]:
    """取某指数在日期区间内的估值序列（升序），以 current_value 作为价格代理。

    返回 [(snapshot_date, value), ...]，过滤掉非法值。
    """
    days = _date_range_to_days(start_date, end_date)
    # 多取一些以应对节假日缺口，再按日期过滤
    raw = get_valuation_history(index_code, days=days + 30)
    if not raw:
        return []

    start = _parse_date(start_date)
    end = _parse_date(end_date)

    items = []
    for row in raw:
        val = row.get("current_value")
        if val is None:
            continue
        try:
            fval = float(val)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(fval) or fval <= 0:
            continue
        snap = str(row.get("snapshot_date", ""))[:10]
        dt = _parse_date(snap)
        if dt is None:
            continue
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        items.append((snap, fval))

    # 按日期升序并去重
    items.sort(key=lambda x: x[0])
    return items


def _calc_daily_returns(series: list[tuple[str, float]]) -> list[float]:
    """由价格序列计算日收益率。"""
    if len(series) < 2:
        return []
    rets = []
    for i in range(1, len(series)):
        prev = series[i - 1][1]
        curr = series[i][1]
        if prev > 0:
            rets.append((curr - prev) / prev)
    return rets


def _fetch_aligned_returns(assets: list[str], start_date, end_date) -> dict[str, list[float]]:
    """取多资产的日收益率序列，按日期对齐到公共交易日。

    返回 {asset: [daily_return, ...]}，各序列等长且日期对齐。
    数据不足的资产会被剔除。
    """
    series_map: dict[str, list[tuple[str, float]]] = {}
    for code in assets:
        s = _fetch_asset_series(code, start_date, end_date)
        if len(s) >= MIN_DATA_DAYS:
            series_map[code] = s

    if len(series_map) < 2:
        # 单资产或无数据，仍返回可用的（单资产场景由调用方处理）
        return {code: _calc_daily_returns(s) for code, s in series_map.items()}

    # 日期对齐：取所有资产日期的交集
    date_sets = [{pt[0] for pt in s} for s in series_map.values()]
    common = sorted(set.intersection(*date_sets)) if date_sets else []
    if len(common) < MIN_DATA_DAYS:
        # 交集太少，退化为各资产独立收益率（不等长）
        return {code: _calc_daily_returns(s) for code, s in series_map.items()}

    aligned: dict[str, list[float]] = {}
    for code, s in series_map.items():
        m = {pt[0]: pt[1] for pt in s}
        navs = [m[d] for d in common if d in m]
        aligned[code] = _calc_daily_returns([(common[i], navs[i]) for i in range(len(navs))])
    return aligned


def _stats_from_returns(returns_map: dict[str, list[float]]) -> tuple[list[str], list[float], list[list[float]]]:
    """由收益率序列计算年化期望收益与协方差矩阵。

    返回 (assets, means, cov_matrix)，其中 means/cov 已年化。
    """
    assets = list(returns_map.keys())
    n = len(assets)
    if n == 0:
        return [], [], []

    means = []
    for a in assets:
        r = returns_map[a]
        means.append((sum(r) / len(r) * TRADING_DAYS) if r else 0.0)

    # 协方差矩阵（年化）
    lengths = [len(returns_map[a]) for a in assets]
    L = min(lengths) if lengths else 0
    if L < 2:
        cov = [[0.0] * n for _ in range(n)]
        return assets, means, cov

    cols = {a: returns_map[a][:L] for a in assets}
    col_means = {a: sum(cols[a]) / L for a in assets}

    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        ai = assets[i]
        for j in range(i, n):
            aj = assets[j]
            s = sum((cols[ai][k] - col_means[ai]) * (cols[aj][k] - col_means[aj]) for k in range(L))
            c = s / (L - 1) if L > 1 else 0.0
            c_annual = c * TRADING_DAYS
            cov[i][j] = c_annual
            cov[j][i] = c_annual
    return assets, means, cov


# ══════════════════════════════════════════════════════
# 纯 Python 矩阵运算（numpy 降级时使用）
# ══════════════════════════════════════════════════════

def _matvec(M: list[list[float]], v: list[float]) -> list[float]:
    """矩阵 × 向量。"""
    return [sum(M[i][j] * v[j] for j in range(len(v))) for i in range(len(M))]


def _matmul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    """矩阵 × 矩阵。"""
    n, m, p = len(A), len(B), len(B[0]) if B else 0
    return [[sum(A[i][k] * B[k][j] for k in range(m)) for j in range(p)] for i in range(n)]


def _transpose(M: list[list[float]]) -> list[list[float]]:
    return [[M[i][j] for i in range(len(M))] for j in range(len(M[0]))] if M else []


def _matrix_inverse(M: list[list[float]]) -> list[list[float]]:
    """高斯-若尔当消元法求逆。M 为方阵。"""
    n = len(M)
    A = [list(M[i]) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        # 选主元
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-12:
            raise ValueError("奇异矩阵，无法求逆")
        A[col], A[piv] = A[piv], A[col]
        pv = A[col][col]
        A[col] = [x / pv for x in A[col]]
        for r in range(n):
            if r != col:
                f = A[r][col]
                A[r] = [a - f * b for a, b in zip(A[r], A[col])]
    return [row[n:] for row in A]


def _portfolio_stats(weights: list[float], means: list[float], cov: list[list[float]]) -> tuple[float, float]:
    """计算组合期望收益与风险（年化）。"""
    ret = sum(w * m for w, m in zip(weights, means))
    var = 0.0
    for i in range(len(weights)):
        for j in range(len(weights)):
            var += weights[i] * weights[j] * cov[i][j]
    var = max(var, 0.0)
    return ret, math.sqrt(var)


# ══════════════════════════════════════════════════════
# 1. 马科维茨效率前沿
# ══════════════════════════════════════════════════════

def _random_weights(n: int, rng: random.Random) -> list[float]:
    """生成一组随机权重（和为1，非负）。无 numpy 时用 -log(uniform) 近似 Dirichlet(1)。"""
    if HAS_NUMPY:
        w = np.random.dirichlet(np.ones(n))
        return [float(x) for x in w]
    vals = [-math.log(rng.random() + 1e-12) for _ in range(n)]
    s = sum(vals)
    return [v / s for v in vals] if s > 0 else [1.0 / n] * n


def _current_weights(assets: list[str]) -> list[float]:
    """从当前持仓（按 index_code 聚合 current_value）计算权重。无持仓返回等权。"""
    try:
        holdings = list_holdings("default")
    except Exception:
        holdings = []
    value_map: dict[str, float] = {}
    for h in holdings:
        code = (h.get("index_code") or "").strip()
        val = h.get("current_value") or 0
        try:
            val = float(val)
        except (ValueError, TypeError):
            val = 0.0
        if code and val > 0:
            value_map[code] = value_map.get(code, 0.0) + val

    if not value_map:
        return [1.0 / len(assets)] * len(assets) if assets else []

    w = [value_map.get(a, 0.0) for a in assets]
    total = sum(w)
    if total <= 0:
        return [1.0 / len(assets)] * len(assets) if assets else []
    return [x / total for x in w]


def efficient_frontier(assets: list, start_date=None, end_date=None, num_points: int = 50) -> dict:
    """马科维茨效率前沿（蒙特卡洛）。

    参数:
        assets: 指数代码列表
        start_date, end_date: 日期区间（ISO 字符串或 None）
        num_points: 蒙特卡洛随机组合数

    返回:
        {frontier, max_sharpe, min_variance, current_portfolio}
    """
    assets = [str(a) for a in assets if a]
    if len(assets) < 2:
        return {"error": "至少需要 2 个资产", "frontier": [], "max_sharpe": None, "min_variance": None, "current_portfolio": None}

    returns_map = _fetch_aligned_returns(assets, start_date, end_date)
    # 剔除收益率序列过短的资产
    valid = [a for a in assets if a in returns_map and len(returns_map[a]) >= MIN_DATA_DAYS]
    if len(valid) < 2:
        return {"error": f"有效数据不足（需≥{MIN_DATA_DAYS}天）", "frontier": [], "max_sharpe": None, "min_variance": None, "current_portfolio": None}

    assets_v, means, cov = _stats_from_returns({a: returns_map[a] for a in valid})
    n = len(assets_v)

    if HAS_NUMPY:
        return _frontier_numpy(assets_v, means, cov, n, num_points)
    return _frontier_pure(assets_v, means, cov, n, num_points)


def _frontier_numpy(assets, means, cov, n, num_points):
    """numpy 版效率前沿。"""
    means_arr = np.array(means, dtype=float)
    cov_arr = np.array(cov, dtype=float)

    records = []
    for _ in range(num_points):
        w = np.random.dirichlet(np.ones(n))
        ret = float(np.dot(w, means_arr))
        risk = float(np.sqrt(max(np.dot(w, cov_arr @ w), 0.0)))
        sharpe = (ret - RF) / risk if risk > 0 else 0.0
        records.append({"return": ret, "risk": risk, "sharpe": sharpe, "weights": {assets[i]: float(w[i]) for i in range(n)}})

    return _assemble_frontier(records, assets, means, cov)


def _frontier_pure(assets, means, cov, n, num_points):
    """纯 Python 版效率前沿。"""
    rng = random.Random(42)
    records = []
    for _ in range(num_points):
        w = _random_weights(n, rng)
        ret, risk = _portfolio_stats(w, means, cov)
        sharpe = (ret - RF) / risk if risk > 0 else 0.0
        records.append({"return": ret, "risk": risk, "sharpe": sharpe, "weights": {assets[i]: w[i] for i in range(n)}})

    return _assemble_frontier(records, assets, means, cov)


def _assemble_frontier(records: list[dict], assets: list[str], means: list[float], cov: list[list[float]]) -> dict:
    """汇总蒙特卡洛结果：有效前沿、最大夏普、最小方差、当前组合。"""
    if not records:
        return {"frontier": [], "max_sharpe": None, "min_variance": None, "current_portfolio": None}

    # 最大夏普
    max_sharpe = max(records, key=lambda r: r["sharpe"])
    # 最小方差
    min_variance = min(records, key=lambda r: r["risk"])

    # 有效前沿：按风险升序，取收益上包络
    by_risk = sorted(records, key=lambda r: r["risk"])
    frontier = []
    best_ret = -math.inf
    for r in by_risk:
        if r["return"] > best_ret:
            frontier.append(r)
            best_ret = r["return"]

    # 当前组合
    cur_w = _current_weights(assets)
    cur_ret, cur_risk = _portfolio_stats(cur_w, means, cov)
    cur_sharpe = (cur_ret - RF) / cur_risk if cur_risk > 0 else 0.0
    current_portfolio = {
        "return": cur_ret,
        "risk": cur_risk,
        "sharpe": cur_sharpe,
        "weights": {assets[i]: cur_w[i] for i in range(len(assets))},
    }

    return {
        "frontier": frontier,
        "max_sharpe": max_sharpe,
        "min_variance": min_variance,
        "current_portfolio": current_portfolio,
    }


# ══════════════════════════════════════════════════════
# 2. 风险平价
# ══════════════════════════════════════════════════════

def risk_parity_allocation(assets: list, start_date=None, end_date=None) -> dict:
    """风险平价分配：权重 ∝ 1/波动率，风险贡献均衡。

    返回 {weights, risk_contributions, total_risk}
    """
    assets = [str(a) for a in assets if a]
    if not assets:
        return {"error": "资产列表为空", "weights": {}, "risk_contributions": {}, "total_risk": 0.0}

    returns_map = _fetch_aligned_returns(assets, start_date, end_date)
    valid = [a for a in assets if a in returns_map and len(returns_map[a]) >= MIN_DATA_DAYS]
    if not valid:
        return {"error": f"有效数据不足（需≥{MIN_DATA_DAYS}天）", "weights": {}, "risk_contributions": {}, "total_risk": 0.0}

    # 各资产年化波动率
    vols = {}
    for a in valid:
        r = returns_map[a]
        mean_r = sum(r) / len(r) if r else 0.0
        var = sum((x - mean_r) ** 2 for x in r) / (len(r) - 1) if len(r) > 1 else 0.0
        vols[a] = math.sqrt(max(var, 0.0) * TRADING_DAYS)

    # 权重 ∝ 1/波动率
    inv = {a: (1.0 / v if v > 0 else 0.0) for a, v in vols.items()}
    total_inv = sum(inv.values())
    if total_inv <= 0:
        # 全部零波动，等权
        weights = {a: 1.0 / len(valid) for a in valid}
    else:
        weights = {a: inv[a] / total_inv for a in valid}

    # 风险贡献：权重 × 波动率 / 总风险
    weighted_vol = sum(weights[a] * vols[a] for a in valid)
    total_risk = weighted_vol  # 简化为加权波动率
    risk_contributions = {a: (weights[a] * vols[a] / weighted_vol if weighted_vol > 0 else 0.0) for a in valid}

    return {
        "weights": weights,
        "risk_contributions": risk_contributions,
        "total_risk": total_risk,
    }


# ══════════════════════════════════════════════════════
# 3. 黑利特曼模型
# ══════════════════════════════════════════════════════

def black_litterman(market_weights: dict, views: list, confidence: float = 0.5) -> dict:
    """黑利特曼模型：融合市场均衡收益与主观观点。

    参数:
        market_weights: {asset: weight} 市场权重
        views: [{"assets": [a, b], "weights": [1.0, -1.0], "return": 0.02}, ...]
        confidence: 观点置信度 (0,1]，构成 Ω 对角阵的缩放

    返回 {posterior_returns, posterior_weights, market_returns}
    """
    if not market_weights:
        return {"error": "市场权重为空", "posterior_returns": {}, "posterior_weights": {}, "market_returns": {}}

    assets = list(market_weights.keys())
    n = len(assets)
    w_market = [float(market_weights[a]) for a in assets]
    s = sum(w_market)
    if s > 0:
        w_market = [w / s for w in w_market]  # 归一化

    # 协方差矩阵 Σ：用近一年估值数据估计
    end = datetime.now()
    start = end - timedelta(days=365)
    returns_map = _fetch_aligned_returns(assets, start.isoformat()[:10], end.isoformat()[:10])
    valid = [a for a in assets if a in returns_map and len(returns_map[a]) >= MIN_DATA_DAYS]
    if len(valid) < n:
        # 数据不足的资产用极小波动率兜底
        logger.warning("[BL] 部分资产数据不足，协方差矩阵将退化")

    _, _, cov = _stats_from_returns({a: returns_map.get(a, [0.0] * MIN_DATA_DAYS) for a in assets})
    # 防止奇异：对角加微小扰动
    for i in range(n):
        cov[i][i] = max(cov[i][i], 1e-6)

    if HAS_NUMPY:
        return _bl_numpy(assets, w_market, cov, views, confidence)
    return _bl_pure(assets, w_market, cov, views, confidence)


def _build_PQ(assets: list[str], views: list) -> tuple[list[list[float]], list[float]]:
    """由结构化 views 构建 P 矩阵与 Q 向量。"""
    P = []
    Q = []
    for v in views or []:
        v_assets = v.get("assets", [])
        v_weights = v.get("weights", [])
        v_ret = v.get("return", 0.0)
        row = [0.0] * len(assets)
        for a, wv in zip(v_assets, v_weights):
            if a in assets:
                row[assets.index(a)] = float(wv)
        P.append(row)
        Q.append(float(v_ret))
    return P, Q


def _bl_numpy(assets, w_market, cov, views, confidence):
    """numpy 版黑利特曼。"""
    Sigma = np.array(cov, dtype=float)
    w_mkt = np.array(w_market, dtype=float)

    # 市场均衡收益 π = λ Σ w
    pi = LAMBDA * Sigma @ w_mkt

    P, Q = _build_PQ(assets, views)
    k = len(P)
    market_returns = {assets[i]: float(pi[i]) for i in range(len(assets))}

    if k == 0:
        # 无观点：后验等于先验
        post_ret = pi.copy()
    else:
        P_arr = np.array(P, dtype=float)
        Q_arr = np.array(Q, dtype=float)
        # Ω = confidence 缩放的对角阵，对角 = diag(P τΣ P') * (1-confidence)/confidence
        tau_sigma = TAU * Sigma
        omega_diag = np.diag(P_arr @ tau_sigma @ P_arr.T)
        # confidence 越高，Ω 越小（观点越可信）
        omega = np.diag(np.where(omega_diag > 0, omega_diag * (1.0 - min(max(confidence, 0.01), 0.99)) / max(confidence, 0.01), 1e-6))

        inv_tau_sigma = np.linalg.inv(tau_sigma)
        inv_omega = np.linalg.inv(omega)
        P_t = P_arr.T

        A = inv_tau_sigma + P_t @ inv_omega @ P_arr
        b = inv_tau_sigma @ pi + P_t @ inv_omega @ Q_arr
        post_ret = np.linalg.solve(A, b)

    # 后验权重 = (1/λ) Σ⁻¹ posterior_returns
    inv_sigma = np.linalg.inv(Sigma)
    post_w = inv_sigma @ post_ret / LAMBDA
    total_w = post_w.sum()
    if abs(total_w) > 0:
        post_w = post_w / total_w  # 归一化

    return {
        "posterior_returns": {assets[i]: float(post_ret[i]) for i in range(len(assets))},
        "posterior_weights": {assets[i]: float(post_w[i]) for i in range(len(assets))},
        "market_returns": market_returns,
    }


def _bl_pure(assets, w_market, cov, views, confidence):
    """纯 Python 版黑利特曼。"""
    n = len(assets)
    # π = λ Σ w
    pi = _matvec(cov, w_market)
    pi = [LAMBDA * x for x in pi]

    P, Q = _build_PQ(assets, views)
    k = len(P)
    market_returns = {assets[i]: pi[i] for i in range(n)}

    if k == 0:
        post_ret = pi[:]
    else:
        # τΣ
        tau_sigma = [[TAU * cov[i][j] for j in range(n)] for i in range(n)]
        # Ω 对角
        conf = min(max(confidence, 0.01), 0.99)
        omega_diag = []
        for r in range(k):
            # P_r τΣ P_r'
            tmp = _matvec(tau_sigma, P[r])
            val = sum(P[r][i] * tmp[i] for i in range(n))
            omega_diag.append(val * (1.0 - conf) / conf if val > 0 else 1e-6)
        omega = [[omega_diag[i] if i == j else 0.0 for j in range(k)] for i in range(k)]

        inv_tau_sigma = _matrix_inverse(tau_sigma)
        inv_omega = _matrix_inverse(omega)
        P_t = _transpose(P)

        # A = inv(τΣ) + P' inv(Ω) P
        P_t_inv_omega = _matmul(P_t, inv_omega)
        A = _matmul(P_t_inv_omega, P)
        for i in range(n):
            for j in range(n):
                A[i][j] += inv_tau_sigma[i][j]

        # b = inv(τΣ) π + P' inv(Ω) Q
        b1 = _matvec(inv_tau_sigma, pi)
        Q_trans = _matvec(inv_omega, Q)  # inv(Ω) Q
        b2 = _matvec(P_t, Q_trans)
        b = [b1[i] + b2[i] for i in range(n)]

        post_ret = _matvec(_matrix_inverse(A), b)

    # 后验权重 = (1/λ) Σ⁻¹ posterior_returns
    inv_sigma = _matrix_inverse(cov)
    post_w_raw = _matvec(inv_sigma, post_ret)
    post_w = [x / LAMBDA for x in post_w_raw]
    total_w = sum(post_w)
    if abs(total_w) > 0:
        post_w = [x / total_w for x in post_w]

    return {
        "posterior_returns": {assets[i]: post_ret[i] for i in range(n)},
        "posterior_weights": {assets[i]: post_w[i] for i in range(n)},
        "market_returns": market_returns,
    }


# ══════════════════════════════════════════════════════
# 4. 综合优化建议
# ══════════════════════════════════════════════════════

def get_optimization_suggestion(user_id: str = "default") -> dict:
    """综合优化建议：对比当前组合与最大夏普组合，给出配置缺口与建议文本。"""
    try:
        holdings = list_holdings(user_id)
    except Exception:
        holdings = []

    # 提取持仓的 index_code 作为资产列表（按市值降序）
    active = [h for h in holdings if (h.get("index_code") or "").strip() and (h.get("current_value") or 0) > 0]
    if not active:
        return {"error": "当前无有效持仓，无法生成优化建议", "current": None, "optimal": None, "gap": None, "suggestion": ""}

    # 按指数聚合市值
    value_map: dict[str, float] = {}
    name_map: dict[str, str] = {}
    for h in active:
        code = (h.get("index_code") or "").strip()
        try:
            val = float(h.get("current_value") or 0)
        except (ValueError, TypeError):
            val = 0.0
        if val > 0:
            value_map[code] = value_map.get(code, 0.0) + val
            name_map.setdefault(code, h.get("index_name") or code)

    assets = sorted(value_map.keys(), key=lambda c: value_map[c], reverse=True)
    if len(assets) < 2:
        return {"error": "持仓覆盖指数不足 2 个，无法做组合优化", "current": None, "optimal": None, "gap": None, "suggestion": ""}

    # 默认取近一年数据
    end = datetime.now()
    start = end - timedelta(days=365)
    frontier = efficient_frontier(assets, start.isoformat()[:10], end.isoformat()[:10], num_points=50)

    if "error" in frontier or not frontier.get("max_sharpe"):
        return {"error": frontier.get("error", "效率前沿计算失败"), "current": None, "optimal": None, "gap": None, "suggestion": ""}

    current = frontier.get("current_portfolio") or {}
    optimal = frontier.get("max_sharpe") or {}

    cur_w = current.get("weights", {})
    opt_w = optimal.get("weights", {})

    # 配置缺口：各资产权重差
    gap = {}
    for a in assets:
        gap[a] = {
            "index_name": name_map.get(a, a),
            "current": round(cur_w.get(a, 0.0), 4),
            "optimal": round(opt_w.get(a, 0.0), 4),
            "delta": round(opt_w.get(a, 0.0) - cur_w.get(a, 0.0), 4),
        }

    cur_ret = current.get("return", 0.0)
    opt_ret = optimal.get("return", 0.0)
    cur_risk = current.get("risk", 0.0)
    opt_risk = optimal.get("risk", 0.0)
    cur_sharpe = current.get("sharpe", 0.0)
    opt_sharpe = optimal.get("sharpe", 0.0)

    # 生成建议文本（纯规则，不调 LLM）
    increase = [g for g in gap.values() if g["delta"] > 0.02]
    decrease = [g for g in gap.values() if g["delta"] < -0.02]
    parts = []
    parts.append(f"当前组合预期收益 {cur_ret:.2%}、风险 {cur_risk:.2%}、夏普 {cur_sharpe:.2f}；")
    parts.append(f"最大夏普组合预期收益 {opt_ret:.2%}、风险 {opt_risk:.2%}、夏普 {opt_sharpe:.2f}。")
    if opt_sharpe > cur_sharpe + 0.05:
        parts.append("当前组合风险调整后收益明显低于最优组合，建议调整配置。")
    else:
        parts.append("当前组合已接近最优，可小幅微调。")
    if increase:
        parts.append("建议增配：" + "、".join(f"{g['index_name']}(+{g['delta']:.1%})" for g in increase[:5]))
    if decrease:
        parts.append("建议减配：" + "、".join(f"{g['index_name']}({g['delta']:.1%})" for g in decrease[:5]))

    return {
        "current": {
            "return": round(cur_ret, 4),
            "risk": round(cur_risk, 4),
            "sharpe": round(cur_sharpe, 4),
            "weights": {a: round(cur_w.get(a, 0.0), 4) for a in assets},
        },
        "optimal": {
            "return": round(opt_ret, 4),
            "risk": round(opt_risk, 4),
            "sharpe": round(opt_sharpe, 4),
            "weights": {a: round(opt_w.get(a, 0.0), 4) for a in assets},
        },
        "gap": gap,
        "suggestion": "".join(parts),
    }
