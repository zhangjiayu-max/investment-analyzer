"""行为画像反推器 — 从用户实际交易行为反推风险偏好。

P0-3: 解决"用户说的风险偏好与实际行为脱节"问题。
分析维度：
1. 交易频率 → 反推 investment_horizon（高频=短线，低频=长线）
2. 持仓周期 → 反推 investment_horizon
3. 止损纪律 → 反推 loss_tolerance（亏损时是否割肉）
4. 仓位集中度 → 反推 risk_tolerance（集中=激进，分散=保守）
5. 买卖方向偏好 → 反推 risk_tolerance（追涨杀跌=激进）
6. 品种偏好 → 反推 investment_experience 和 focus_assets

数据来源：portfolio_transactions 表（排除系统/假设交易）。
不修改原问卷画像，仅产出"行为画像"供融合。
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 行为画像缓存（user_id -> (profile, timestamp)），TTL 1 小时，避免每次查询重复计算
_BEHAVIOR_CACHE: dict[str, tuple[dict, float]] = {}
_BEHAVIOR_CACHE_TTL = 3600.0  # 秒

# 样本量阈值
_MIN_SAMPLES = 5            # 低于此值不反推（置信度太低）
_HIGH_CONFIDENCE_SAMPLES = 20  # 达到此值 confidence=0.8


def infer_behavior_profile(user_id: str = "default", use_cache: bool = True) -> dict:
    """从交易记录反推行为画像。

    Returns:
        {
            "inferred_risk_tolerance": "conservative"|"moderate"|"aggressive",
            "inferred_investment_horizon": "short"|"medium"|"long",
            "inferred_loss_tolerance": "low"|"medium"|"high",
            "inferred_experience_level": "beginner"|"intermediate"|"advanced",
            "behavior_metrics": {
                "avg_holding_days": float,
                "trade_frequency_per_month": float,
                "stop_loss_rate": float,
                "position_concentration": float,
                "chasing_high_rate": float,
                "loss_cutting_discipline": float,
            },
            "confidence": float,  # 0-1
            "sample_size": int,
            "divergence_from_questionnaire": dict,
        }
    """
    # 1. 缓存检查
    if use_cache:
        cached = _BEHAVIOR_CACHE.get(user_id)
        if cached is not None:
            profile, ts = cached
            if (time.time() - ts) < _BEHAVIOR_CACHE_TTL:
                return profile

    # 2. 拉取交易记录（排除系统/假设交易）
    from db import list_transactions
    transactions = list_transactions(
        user_id=user_id, limit=1000, include_system=False,
    )
    # 只保留 confirmed / settled 状态的 buy / sell 记录
    transactions = [
        t for t in transactions
        if (t.get("status") in ("confirmed", "settled") or t.get("status") is None)
        and t.get("transaction_type") in ("buy", "sell")
    ]

    sample_size = len(transactions)

    # 3. 样本量不足，返回低置信度空画像
    if sample_size < _MIN_SAMPLES:
        empty_profile = _empty_profile(sample_size)
        _BEHAVIOR_CACHE[user_id] = (empty_profile, time.time())
        return empty_profile

    # 4. 计算行为指标
    metrics = _compute_behavior_metrics(transactions, user_id)

    # 5. 反推画像维度
    inferred_risk_tolerance = _infer_risk_tolerance(metrics)
    inferred_investment_horizon = _infer_investment_horizon(metrics)
    inferred_loss_tolerance = _infer_loss_tolerance(metrics)
    inferred_experience_level = _infer_experience_level(metrics, transactions)

    # 6. 置信度
    confidence = _compute_confidence(sample_size)

    # 7. 与问卷画像的差异
    divergence = _compute_divergence(
        inferred_risk_tolerance, inferred_investment_horizon,
        inferred_loss_tolerance, inferred_experience_level, user_id,
    )

    profile = {
        "inferred_risk_tolerance": inferred_risk_tolerance,
        "inferred_investment_horizon": inferred_investment_horizon,
        "inferred_loss_tolerance": inferred_loss_tolerance,
        "inferred_experience_level": inferred_experience_level,
        "behavior_metrics": metrics,
        "confidence": confidence,
        "sample_size": sample_size,
        "divergence_from_questionnaire": divergence,
    }

    _BEHAVIOR_CACHE[user_id] = (profile, time.time())
    return profile


def _empty_profile(sample_size: int) -> dict:
    """样本不足时的空画像。"""
    return {
        "inferred_risk_tolerance": None,
        "inferred_investment_horizon": None,
        "inferred_loss_tolerance": None,
        "inferred_experience_level": None,
        "behavior_metrics": {},
        "confidence": 0.3,
        "sample_size": sample_size,
        "divergence_from_questionnaire": {},
        "note": f"样本量不足（{sample_size}<{_MIN_SAMPLES}），不反推行为画像",
    }


def _compute_behavior_metrics(transactions: list[dict], user_id: str) -> dict:
    """计算行为指标。"""
    now = datetime.now()
    cutoff_90d = (now - timedelta(days=90)).strftime("%Y-%m-%d")

    # 按基金分组，按时间排序（用于 buy→sell 配对）
    by_fund: dict[str, list[dict]] = defaultdict(list)
    for t in transactions:
        fc = t.get("fund_code", "")
        if not fc:
            continue
        by_fund[fc].append(t)
    for fc in by_fund:
        by_fund[fc].sort(key=lambda x: (x.get("transaction_date", "") or "", x.get("id", 0)))

    # 1. 平均持仓周期（buy → sell 配对，FIFO）
    holding_days_list: list[int] = []
    stop_loss_count = 0   # 止损卖出数（亏损>5%）
    total_sell_count = 0  # 有对应买入的卖出数
    for fc, txs in by_fund.items():
        buy_queue: list[tuple[datetime, float]] = []
        for t in txs:
            ttype = t.get("transaction_type")
            tdate_str = (t.get("transaction_date", "") or "")[:10]
            price = t.get("price") or 0
            try:
                tdate = datetime.strptime(tdate_str, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            if ttype == "buy":
                buy_queue.append((tdate, price))
            elif ttype == "sell":
                if not buy_queue:
                    continue
                total_sell_count += 1
                buy_date, buy_price = buy_queue.pop(0)  # FIFO 配对
                days = (tdate - buy_date).days
                if days >= 0:
                    holding_days_list.append(days)
                # 止损判断：卖出价 < 买入价 * 0.95（亏损>5%）
                if buy_price > 0 and price < buy_price * 0.95:
                    stop_loss_count += 1

    avg_holding_days = (
        sum(holding_days_list) / len(holding_days_list)
        if holding_days_list else 0.0
    )
    stop_loss_rate = (
        stop_loss_count / total_sell_count if total_sell_count > 0 else 0.0
    )

    # 2. 月交易频率（近90天交易笔数 / 3）
    recent_txs = [
        t for t in transactions
        if (t.get("transaction_date", "") or "")[:10] >= cutoff_90d
    ]
    trade_frequency_per_month = len(recent_txs) / 3.0

    # 3. 仓位集中度（最大单只持仓占比 0-1）—— 复用 diversification
    position_concentration = 0.0
    try:
        from db import get_portfolio_diversification
        div = get_portfolio_diversification(user_id)
        position_concentration = (div.get("max_holding_pct") or 0) / 100.0
    except Exception as e:
        logger.debug(f"获取仓位集中度失败: {e}")

    # 4. 追涨率（近90天高点买入占比）
    chasing_high_rate = _compute_chasing_high_rate(recent_txs)

    # 5. 止损纪律评分 0-100
    loss_cutting_discipline = _compute_loss_cutting_discipline(
        stop_loss_rate, holding_days_list
    )

    return {
        "avg_holding_days": round(avg_holding_days, 1),
        "trade_frequency_per_month": round(trade_frequency_per_month, 2),
        "stop_loss_rate": round(stop_loss_rate, 3),
        "position_concentration": round(position_concentration, 3),
        "chasing_high_rate": round(chasing_high_rate, 3),
        "loss_cutting_discipline": round(loss_cutting_discipline, 1),
    }


def _compute_chasing_high_rate(recent_txs: list[dict]) -> float:
    """计算追涨率：近90天高点买入占比。

    判定：买入价高于该基金近20日均价 10% 视为追涨。
    无净值数据的样本不计入分母。
    """
    from db._conn import _get_conn

    buy_txs = [
        t for t in recent_txs
        if t.get("transaction_type") == "buy" and (t.get("price") or 0) > 0
    ]
    if not buy_txs:
        return 0.0

    chasing_count = 0
    valid_count = 0
    for t in buy_txs:
        fund_code = t.get("fund_code", "")
        buy_price = t.get("price") or 0
        tdate_str = (t.get("transaction_date", "") or "")[:10]
        if not fund_code or not tdate_str:
            continue
        try:
            datetime.strptime(tdate_str, "%Y-%m-%d")
        except ValueError:
            continue
        # 查询该买入日之前近20个交易日的净值
        try:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT nav FROM fund_nav_history
                   WHERE fund_code = ? AND nav_date < ? AND nav IS NOT NULL
                   ORDER BY nav_date DESC LIMIT 20""",
                (fund_code, tdate_str)
            ).fetchall()
            conn.close()
        except Exception:
            continue
        navs = [r["nav"] for r in rows if r["nav"]]
        if not navs:
            continue
        avg_nav = sum(navs) / len(navs)
        if avg_nav <= 0:
            continue
        valid_count += 1
        if buy_price > avg_nav * 1.10:
            chasing_count += 1

    return chasing_count / valid_count if valid_count > 0 else 0.0


def _compute_loss_cutting_discipline(stop_loss_rate: float,
                                     holding_days_list: list[int]) -> float:
    """止损纪律评分 0-100。

    - 止损率 >= 0.5 → 80-100 分（敢割肉，纪律好）
    - 止损率 0.2-0.5 → 50-80 分
    - 止损率 < 0.2 且平均持仓 > 180 天 → 20-50 分（套牢不动，纪律差）
    - 无卖出样本 → 50 分（中性）
    """
    if not holding_days_list:
        return 50.0
    if stop_loss_rate >= 0.5:
        return min(80 + (stop_loss_rate - 0.5) * 40, 100.0)
    elif stop_loss_rate >= 0.2:
        return 50 + (stop_loss_rate - 0.2) / 0.3 * 30  # 0.2→50, 0.5→80
    else:
        avg_days = sum(holding_days_list) / len(holding_days_list)
        if avg_days > 180:
            return 20 + min(avg_days / 180, 1.0) * 30  # 持仓越久纪律越差
        return 40.0


def _infer_risk_tolerance(metrics: dict) -> str:
    """反推风险偏好：conservative / moderate / aggressive。

    仓位集中度 >0.5 = aggressive, 0.2-0.5 = moderate, <0.2 = conservative
    追涨率 >0.3 加权升级
    """
    concentration = metrics.get("position_concentration", 0)
    chasing = metrics.get("chasing_high_rate", 0)

    score = 0  # 越大越激进
    if concentration > 0.5:
        score += 2
    elif concentration > 0.2:
        score += 1
    if chasing > 0.3:
        score += 1

    if score >= 2:
        return "aggressive"
    elif score >= 1:
        return "moderate"
    return "conservative"


def _infer_investment_horizon(metrics: dict) -> str:
    """反推投资期限：short / medium / long。

    平均持仓 <7天 = short, 7-90天 = medium, >90天 = long
    月交易频率 >10 加权降级
    """
    avg_days = metrics.get("avg_holding_days", 0)
    freq = metrics.get("trade_frequency_per_month", 0)

    if avg_days < 7:
        return "short"
    elif avg_days <= 90:
        if freq > 10:
            return "short"
        return "medium"
    else:
        if freq > 10:
            return "medium"
        return "long"


def _infer_loss_tolerance(metrics: dict) -> str:
    """反推亏损承受度：low / medium / high。

    止损率高 = loss_tolerance 高（敢割肉）
    止损率低但持仓久 = loss_tolerance 低（套牢不动）
    """
    stop_loss_rate = metrics.get("stop_loss_rate", 0)
    discipline = metrics.get("loss_cutting_discipline", 50)

    if stop_loss_rate >= 0.4 or discipline >= 80:
        return "high"
    elif stop_loss_rate >= 0.15 or discipline >= 50:
        return "medium"
    return "low"


def _infer_experience_level(metrics: dict, transactions: list[dict]) -> str:
    """反推投资经验：beginner / intermediate / advanced。

    品种数多 + 交易频率适中 + 样本量大 = 资深
    """
    fund_codes = {t.get("fund_code", "") for t in transactions if t.get("fund_code")}
    fund_count = len(fund_codes)
    freq = metrics.get("trade_frequency_per_month", 0)
    sample = len(transactions)

    score = 0
    if fund_count >= 5:
        score += 2
    elif fund_count >= 2:
        score += 1
    if sample >= 30:
        score += 1
    if freq >= 3:
        score += 1

    if score >= 3:
        return "advanced"
    elif score >= 1:
        return "intermediate"
    return "beginner"


def _compute_confidence(sample_size: int) -> float:
    """置信度：>=20 → 0.8, <5 → 0.3, 中间线性插值。"""
    if sample_size >= _HIGH_CONFIDENCE_SAMPLES:
        return 0.8
    if sample_size < _MIN_SAMPLES:
        return 0.3
    # 线性插值 0.3 → 0.8
    ratio = (sample_size - _MIN_SAMPLES) / (_HIGH_CONFIDENCE_SAMPLES - _MIN_SAMPLES)
    return round(0.3 + ratio * 0.5, 2)


def _compute_divergence(inferred_risk, inferred_horizon, inferred_loss,
                        inferred_exp, user_id) -> dict:
    """与问卷画像的差异对比。仅在维度取值不一致时记录。"""
    from agent.kyc.kyc import get_kyc_profile
    try:
        questionnaire = get_kyc_profile(user_id)
    except Exception:
        return {}

    divergence: dict = {}

    # 风险偏好差异（归一化到三档对比）
    q_risk = questionnaire.get("risk_tolerance", "")
    if q_risk and inferred_risk:
        risk_level = {"conservative": 0, "steady": 0, "balanced": 1,
                      "moderate": 1, "aggressive": 2, "radical": 2}
        q_lvl = risk_level.get(q_risk)
        i_lvl = risk_level.get(inferred_risk)
        if q_lvl is not None and i_lvl is not None and q_lvl != i_lvl:
            divergence["risk_tolerance"] = {
                "questionnaire": q_risk,
                "inferred": inferred_risk,
                "direction": "higher" if i_lvl > q_lvl else "lower",
            }

    # 投资期限差异
    q_horizon = questionnaire.get("investment_horizon", "")
    if q_horizon and inferred_horizon:
        horizon_level = {"short": 0, "medium": 1, "long": 2}
        q_lvl = horizon_level.get(q_horizon)
        i_lvl = horizon_level.get(inferred_horizon)
        if q_lvl is not None and i_lvl is not None and q_lvl != i_lvl:
            divergence["investment_horizon"] = {
                "questionnaire": q_horizon,
                "inferred": inferred_horizon,
                "direction": "longer" if i_lvl > q_lvl else "shorter",
            }

    # 亏损承受度差异
    q_loss = questionnaire.get("loss_tolerance", "")
    if q_loss and inferred_loss:
        loss_level = {"low": 0, "medium": 1, "high": 2}
        q_lvl = loss_level.get(q_loss)
        i_lvl = loss_level.get(inferred_loss)
        if q_lvl is not None and i_lvl is not None and q_lvl != i_lvl:
            divergence["loss_tolerance"] = {
                "questionnaire": q_loss,
                "inferred": inferred_loss,
                "direction": "higher" if i_lvl > q_lvl else "lower",
            }

    # 投资经验差异（novice↔beginner 归一化）
    q_exp = questionnaire.get("investment_experience", "")
    if q_exp and inferred_exp:
        exp_level = {"novice": 0, "beginner": 0, "intermediate": 1,
                     "advanced": 2, "professional": 2}
        q_lvl = exp_level.get(q_exp)
        i_lvl = exp_level.get(inferred_exp)
        if q_lvl is not None and i_lvl is not None and q_lvl != i_lvl:
            divergence["investment_experience"] = {
                "questionnaire": q_exp,
                "inferred": inferred_exp,
                "direction": "higher" if i_lvl > q_lvl else "lower",
            }

    return divergence


def clear_behavior_cache(user_id: str = None):
    """清除行为画像缓存。user_id=None 清除全部。"""
    if user_id is None:
        _BEHAVIOR_CACHE.clear()
    else:
        _BEHAVIOR_CACHE.pop(user_id, None)
