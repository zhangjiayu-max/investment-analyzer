"""全账户资产健康度诊断 2.0 — 聚合服务层。

定位：只做数据聚合与编排，不重复实现计算逻辑。
复用：health_score、four_pots、portfolio_intelligence、smart_add_planner、alerts、decisions。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from db import (
    list_holdings, get_portfolio_summary, get_total_cash_balance,
    get_user_investment_profile, save_user_investment_profile,
    save_health_score_v2, get_health_score_v2, list_health_scores_v2,
    track_health_action, update_health_action_status,
    get_config_bool, get_config_float, get_config_int,
)
from db.decisions import create_candidate_from_structured_recommendation
from infra.utils import _safe_float

logger = logging.getLogger(__name__)

# 5 分钟内存缓存，避免每次请求重复聚合多个模块
_DASHBOARD_CACHE: dict = {"data": None, "ts": 0, "user_id": None}
_CACHE_TTL = 300

# H-1（2026-07-22）：费率/排名7天缓存（避免每次诊断都调akshare）
_FEE_CACHE: dict[str, tuple[float, float]] = {}   # fund_code -> (fee, cached_ts)
_RANK_CACHE: dict[str, tuple[float, float]] = {}   # fund_code -> (rank_pct, cached_ts)
_FEE_RANK_CACHE_TTL = 7 * 24 * 3600               # 7天


def _get_fund_fee_cached(fund_code: str) -> float:
    """获取基金综合费率（管理费+托管费），7天缓存。H-1从V1迁移。"""
    now = time.time()
    cached = _FEE_CACHE.get(fund_code)
    if cached and (now - cached[1]) < _FEE_RANK_CACHE_TTL:
        return cached[0]
    try:
        from services.fund.fund_analysis import _fetch_fund_fee
        fee = _fetch_fund_fee(fund_code)
        if fee > 0:
            _FEE_CACHE[fund_code] = (fee, now)
            return fee
    except Exception as e:
        logger.debug(f"[health_v2] 获取费率失败 {fund_code}: {e}")
    return 0.0


def _get_peer_ranking_cached(fund_code: str) -> float | None:
    """获取同类排名百分位（0-100，越小越好），7天缓存。H-1从V1迁移。"""
    now = time.time()
    cached = _RANK_CACHE.get(fund_code)
    if cached and (now - cached[1]) < _FEE_RANK_CACHE_TTL:
        return cached[0]
    try:
        from services.fund.fund_analysis import _fetch_peer_ranking
        rank = _fetch_peer_ranking(fund_code)
        if rank is not None:
            _RANK_CACHE[fund_code] = (rank, now)
            return rank
    except Exception as e:
        logger.debug(f"[health_v2] 获取同类排名失败 {fund_code}: {e}")
    return None

# 默认目标配置（按风险等级）
DEFAULT_TARGET_POTS = {
    "conservative": {"cash": 20, "steady": 50, "long_term": 25, "insurance": 5},
    "steady":       {"cash": 10, "steady": 35, "long_term": 50, "insurance": 5},
    "aggressive":   {"cash": 5,  "steady": 20, "long_term": 70, "insurance": 5},
}

# 四笔钱中文映射
POT_LABELS = {
    "cash": "活钱管理",
    "steady": "稳健理财",
    "long_term": "长期投资",
    "insurance": "保险保障",
}


def _json_loads(s: str, default):
    try:
        return json.loads(s or default)
    except Exception:
        return json.loads(default)


def _score_level(score: int) -> str:
    if score >= 800:
        return "优秀"
    if score >= 600:
        return "良好"
    if score >= 400:
        return "一般"
    if score >= 200:
        return "较差"
    return "危险"


def _score_rating(score: int, max_score: int = 200) -> str:
    ratio = score / max_score if max_score > 0 else 0
    if ratio >= 0.8:
        return "excellent"
    if ratio >= 0.6:
        return "good"
    if ratio >= 0.4:
        return "fair"
    return "poor"


# ════════════════════════════════════════════════════════════
# 模块1：资产全景
# ════════════════════════════════════════════════════════════

def build_asset_overview(user_id: str = "default") -> dict:
    """构建资产全景（净资产视图）。"""
    summary = get_portfolio_summary(user_id=user_id)
    total_value = _safe_float(summary.get("total_value", 0))
    total_cost = _safe_float(summary.get("total_cost", 0))
    total_profit = _safe_float(summary.get("total_profit", 0))
    profit_rate = _safe_float(summary.get("profit_rate", 0))
    cash_balance = get_total_cash_balance()
    total_assets = total_value + cash_balance

    cash_ratio = cash_balance / total_assets if total_assets > 0 else 0
    investment_ratio = total_value / total_assets if total_assets > 0 else 0

    alerts = []
    cash_warning = get_config_float("cash.ratio_warning", 0.20)
    cash_low = get_config_float("cash.ratio_low", 0.03)
    if cash_ratio > cash_warning:
        alerts.append({
            "level": "warning",
            "message": f"现金占比{cash_ratio:.1%}偏高，资金闲置会拖低整体收益",
        })
    elif cash_ratio < cash_low and total_assets > 0:
        alerts.append({
            "level": "info",
            "message": f"现金占比仅{cash_ratio:.1%}，建议保留少量流动性",
        })

    return {
        "total_assets": round(total_assets, 2),
        "total_liabilities": 0.0,
        "net_worth": round(total_assets, 2),
        "cash_balance": round(cash_balance, 2),
        "cash_ratio": round(cash_ratio, 4),
        "investment_value": round(total_value, 2),
        "investment_ratio": round(investment_ratio, 4),
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_profit, 2),
        "profit_rate": round(profit_rate, 4),
        "holding_count": len(summary.get("holdings", []) or []),
        "alerts": alerts,
    }


# ════════════════════════════════════════════════════════════
# 模块2：健康分 2.0（Phase 1 轻量实现，复用既有评分逻辑）
# ════════════════════════════════════════════════════════════

def _calc_quality_score(holdings: list) -> tuple[int, dict]:
    """选品质量：盈亏贡献 + 同类排名 + 费率水平（H-1从V1迁移）。

    V1原逻辑：akshare查同类排名(80)+费率(60)+估值(60)
    V2增强：同类排名(60)+费率(40)+盈亏(40)+基础60 = 200
    """
    if not holdings:
        return 100, {"reason": "无持仓数据，给予中性分"}

    total_value = sum(_safe_float(h.get("current_value", 0)) for h in holdings)
    scores = []
    details = []
    good_count = 0
    bad_count = 0

    for h in holdings:
        value = _safe_float(h.get("current_value", 0))
        weight = value / total_value if total_value > 0 else 0
        profit_rate = _safe_float(h.get("profit_rate", 0))
        fund_code = h.get("fund_code", "")
        fund_score = 60  # 基础分
        reasons = []

        # 1. 盈亏贡献（±40）
        if profit_rate > 0.1:
            fund_score += 40
            reasons.append(f"盈利{profit_rate*100:.1f}%")
        elif profit_rate > 0:
            fund_score += 15
        elif profit_rate > -0.1:
            fund_score -= 20
            reasons.append(f"亏损{abs(profit_rate)*100:.1f}%")
        else:
            fund_score -= 40
            reasons.append(f"深亏{abs(profit_rate)*100:.1f}%")

        # 2. 同类排名（+60，H-1从V1迁移，7天缓存）
        rank_pct = _get_peer_ranking_cached(fund_code)
        if rank_pct is not None:
            if rank_pct <= 10:
                fund_score += 60
                reasons.append("同类排名前10%")
            elif rank_pct <= 25:
                fund_score += 45
                reasons.append("同类排名前25%")
            elif rank_pct <= 50:
                fund_score += 30
            elif rank_pct <= 75:
                fund_score += 15
            else:
                fund_score += 5
                reasons.append("同类排名后25%")
        else:
            fund_score += 25  # 无数据给中性分

        # 3. 费率水平（±40，H-1从V1迁移，使用缓存查询）
        fee = _get_fund_fee_cached(fund_code)
        if fee > 0:
            if fee <= 0.5:
                fund_score += 40
                reasons.append(f"低费率{fee}%")
            elif fee <= 1.0:
                fund_score += 25
            elif fee <= 1.5:
                fund_score += 10
                reasons.append(f"费率偏高{fee}%")
            else:
                fund_score -= 20
                reasons.append(f"高费率{fee}%")
        else:
            fund_score += 15  # 无数据给中性分

        fund_score = max(0, min(200, fund_score))
        scores.append(fund_score * weight)
        if fund_score >= 140:
            good_count += 1
        elif fund_score <= 80:
            bad_count += 1
        details.append({
            "fund_name": h.get("fund_name", ""),
            "fund_code": fund_code,
            "score": fund_score,
            "weight": round(weight, 4),
            "reasons": reasons,
            "peer_ranking": rank_pct,
            "fee": fee,
        })

    avg_score = int(sum(scores))
    avg_score = max(0, min(200, avg_score))

    top_issue = ""
    if bad_count > 0:
        top_issue = f"{bad_count} 只基金选品质量较差，建议关注费率或基本面"
    elif good_count > 0:
        top_issue = f"{good_count} 只基金表现良好"

    return avg_score, {
        "matched_count": len(holdings),
        "good_count": good_count,
        "bad_count": bad_count,
        "avg_score": avg_score,
        "top_issue": top_issue,
        "holdings": sorted(details, key=lambda x: x["score"])[:5],
    }


def _calc_diversification_score(holdings: list, profile: dict) -> tuple[int, dict]:
    """分散配置：持仓数量、集中度、四笔钱偏离。"""
    if not holdings:
        return 100, {"reason": "无持仓数据"}

    n = len(holdings)
    total_value = sum(_safe_float(h.get("current_value", 0)) for h in holdings)
    max_value = max(_safe_float(h.get("current_value", 0)) for h in holdings)
    max_pct = max_value / total_value * 100 if total_value > 0 else 0

    score = 0
    # 持仓数量
    if n >= 10:
        score += 60
    elif n >= 7:
        score += 50
    elif n >= 5:
        score += 40
    elif n >= 3:
        score += 30
    else:
        score += 15

    # 集中度
    if max_pct <= 15:
        score += 70
    elif max_pct <= 25:
        score += 55
    elif max_pct <= 35:
        score += 35
    elif max_pct <= 50:
        score += 20
    else:
        score += 5

    # 四笔钱偏离
    actual_pots = _classify_pots(holdings)
    target_pots = profile.get("target_pots") or DEFAULT_TARGET_POTS["steady"]
    max_drift = max(abs(actual_pots.get(k, 0) - target_pots.get(k, 0)) for k in target_pots)
    if max_drift <= 5:
        score += 70
    elif max_drift <= 10:
        score += 50
    elif max_drift <= 20:
        score += 30
    else:
        score += 10

    score = max(0, min(200, score))

    top_issue = ""
    if max_pct > 35:
        top_issue = f"最大单只持仓占比 {max_pct:.1f}%，过于集中"
    elif max_drift > 10:
        top_issue = f"四笔钱最大偏离 {max_drift:.1f}%，建议再平衡"

    return score, {
        "holding_count": n,
        "max_single_pct": round(max_pct, 1),
        "max_drift_pct": round(max_drift, 1),
        "actual_pots": actual_pots,
        "target_pots": target_pots,
        "top_issue": top_issue,
    }


def _classify_pots(holdings: list) -> dict:
    """按市值计算四笔钱实际占比。"""
    total_value = sum(_safe_float(h.get("current_value", 0)) for h in holdings)
    if total_value <= 0:
        return {"cash": 0, "steady": 0, "long_term": 0, "insurance": 0}

    from routers.analysis.four_pots import classify_fund
    pots = {"cash": 0, "steady": 0, "long_term": 0, "insurance": 0}
    for h in holdings:
        value = _safe_float(h.get("current_value", 0))
        pot = classify_fund(h.get("fund_name", ""), h.get("fund_type", ""))
        key = "cash" if pot == "活钱管理" else "steady" if pot == "稳健理财" else "long_term" if pot == "长期投资" else "insurance"
        pots[key] += value

    return {k: round(v / total_value * 100, 1) for k, v in pots.items()}


def _calc_valuation_score(holdings: list) -> tuple[int, dict]:
    """估值合理：按市值加权的持仓估值分位。"""
    if not holdings:
        return 100, {"reason": "无持仓数据"}

    from db.valuations import get_latest_valuation
    weighted_pcts = []
    total_value = sum(_safe_float(h.get("current_value", 0)) for h in holdings)
    details = []

    for h in holdings:
        index_code = h.get("index_code", "")
        value = _safe_float(h.get("current_value", 0))
        if not index_code or value <= 0:
            continue
        val = get_latest_valuation(index_code)
        if not val:
            continue
        pct = _safe_float(val.get("percentile"))
        if pct is None or pct == 0:
            continue
        weight = value / total_value if total_value > 0 else 0
        weighted_pcts.append(pct * weight)
        details.append({
            "fund_name": h.get("fund_name", ""),
            "index_code": index_code,
            "percentile": pct,
            "weight": round(weight, 4),
        })

    if not weighted_pcts:
        return 100, {"reason": "无有效估值数据，给予中性分"}

    avg_pct = sum(weighted_pcts)
    if avg_pct < 20:
        score = 180
    elif avg_pct < 30:
        score = 160
    elif avg_pct < 50:
        score = 140
    elif avg_pct < 70:
        score = 100
    elif avg_pct < 85:
        score = 70
    else:
        score = 50

    top_issue = ""
    if avg_pct > 70:
        top_issue = f"持仓整体估值分位 {avg_pct:.1f}%，偏高"
    elif avg_pct < 30:
        top_issue = f"持仓整体估值分位 {avg_pct:.1f}%，偏低"

    return score, {
        "avg_percentile": round(avg_pct, 1),
        "top_issue": top_issue,
        "holdings": details[:5],
    }


def _calc_behavior_score(user_id: str, holdings: list) -> tuple[int, dict]:
    """持有行为：交易频率 + 追涨杀跌检测 + 持有时长（H-1从V1迁移）。

    V1原逻辑：交易频率(80)+追涨杀跌(60)+持有时长(60)=200
    V2原逻辑：仅看交易次数（基础150只减不加）
    V2增强：三维度评分，满分200，与V1对齐
    """
    from db.portfolio import list_transactions
    txs = list_transactions(user_id=user_id, limit=200)
    if not txs:
        return 130, {"reason": "暂无交易记录，给予默认分"}

    now = datetime.now()

    # 1. 交易频率（80分）
    cutoff_90d = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    recent_90d = [t for t in txs if str(t.get("transaction_date", "")) >= cutoff_90d]
    freq = len(recent_90d)
    if freq <= 3:
        freq_score = 80
    elif freq <= 6:
        freq_score = 60
    elif freq <= 12:
        freq_score = 40
    elif freq <= 20:
        freq_score = 20
    else:
        freq_score = 5

    # 近30天（用于提示）
    cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    recent_30d = [t for t in txs if str(t.get("transaction_date", "")) >= cutoff_30d]

    # 2. 追涨杀跌检测（60分，H-1从V1迁移）
    # 买入时对应指数估值百分位 > 70% 视为追涨
    from db.valuations import get_valuation_history
    buy_trades = [t for t in txs if t.get("transaction_type") == "buy"]
    chase_count = 0
    if buy_trades:
        for t in buy_trades:
            trade_date = str(t.get("transaction_date", ""))
            fund_code = t.get("fund_code", "")
            holding = next((h for h in holdings if h.get("fund_code") == fund_code), None)
            index_code = holding.get("index_code", "") if holding else ""
            if index_code and trade_date:
                try:
                    val_hist = get_valuation_history(index_code, days=5)
                    closest_pct = None
                    for v in val_hist:
                        if str(v.get("snapshot_date", "")) <= trade_date:
                            closest_pct = _safe_float(v.get("percentile"))
                            break
                    if closest_pct is None and val_hist:
                        closest_pct = _safe_float(val_hist[0].get("percentile"))
                    if closest_pct and closest_pct > 70:
                        chase_count += 1
                except Exception:
                    pass
        chase_ratio = chase_count / len(buy_trades)
        if chase_ratio <= 0.1:
            chase_score = 60
        elif chase_ratio <= 0.3:
            chase_score = 40
        elif chase_ratio <= 0.5:
            chase_score = 20
        else:
            chase_score = 5
    else:
        chase_ratio = 0.0
        chase_score = 40  # 无买入给中性分

    # 3. 平均持有时长（60分，H-1从V1迁移）
    avg_days = 180  # 默认值
    try:
        active = [h for h in holdings if _safe_float(h.get("shares", 0)) > 0]
        holding_days_list = []
        for h in active:
            buy_date_str = h.get("buy_date", "")
            if not buy_date_str:
                # 从交易记录推算最早买入日期
                fund_code = h.get("fund_code", "")
                if fund_code:
                    try:
                        from db._conn import _get_conn
                        conn = _get_conn()
                        try:
                            earliest = conn.execute(
                                "SELECT MIN(transaction_date) FROM portfolio_transactions "
                                "WHERE fund_code = ? AND transaction_type = 'buy'",
                                (fund_code,)
                            ).fetchone()
                            if earliest and earliest[0]:
                                buy_date_str = earliest[0]
                        finally:
                            conn.close()
                    except Exception:
                        pass
            if buy_date_str:
                try:
                    bd = datetime.strptime(str(buy_date_str)[:10], "%Y-%m-%d")
                    days_held = (now - bd).days
                    if days_held > 0:
                        holding_days_list.append(days_held)
                except Exception:
                    pass
        if holding_days_list:
            avg_days = int(sum(holding_days_list) / len(holding_days_list))
    except Exception:
        pass

    if avg_days >= 365:
        hold_score = 60
    elif avg_days >= 180:
        hold_score = 45
    elif avg_days >= 90:
        hold_score = 30
    else:
        hold_score = 10

    score = freq_score + chase_score + hold_score
    score = max(0, min(200, score))

    top_issue = ""
    if len(recent_30d) >= 6:
        top_issue = f"近30天交易 {len(recent_30d)} 次，存在过度交易嫌疑"
    elif chase_ratio > 0.3:
        top_issue = f"追涨买入占比 {chase_ratio*100:.0f}%，建议在低估时买入"
    elif avg_days < 90:
        top_issue = f"平均持有时长 {avg_days} 天偏短，建议长期持有"

    return score, {
        "trades_90d": freq,
        "trades_30d": len(recent_30d),
        "buy_count": len(buy_trades),
        "chase_ratio": round(chase_ratio * 100, 1) if buy_trades else 0,
        "chase_count": chase_count,
        "avg_holding_days": avg_days,
        "freq_score": freq_score,
        "chase_score": chase_score,
        "hold_score": hold_score,
        "top_issue": top_issue,
    }


def _calc_risk_score(holdings: list) -> tuple[int, dict]:
    """风控纪律：单只仓位 + 高估持仓占比 + 止损意识（H-1从V1迁移）。

    V1原逻辑：单只仓位(80)+高估持仓占比(60)+止损意识(60)=200
    V2原逻辑：单只仓位+最大亏损（基础150只减不加）
    V2增强：三维度评分，满分200，与V1对齐
    """
    if not holdings:
        return 130, {"reason": "无持仓数据"}

    total_value = sum(_safe_float(h.get("current_value", 0)) for h in holdings)

    # 1. 单只仓位控制（80分）
    max_value = max(_safe_float(h.get("current_value", 0)) for h in holdings)
    max_pct = max_value / total_value * 100 if total_value > 0 else 0
    if max_pct <= 10:
        pos_score = 80
    elif max_pct <= 15:
        pos_score = 65
    elif max_pct <= 20:
        pos_score = 50
    elif max_pct <= 30:
        pos_score = 30
    else:
        pos_score = 10

    # 2. 高估持仓占比（60分，H-1从V1迁移）
    # 估值百分位 > 80% 的持仓市值占比
    from db.valuations import get_latest_valuation
    overvalued_value = 0
    for h in holdings:
        index_code = h.get("index_code", "")
        if not index_code:
            continue
        val = get_latest_valuation(index_code)
        if not val:
            continue
        pe_pct = _safe_float(val.get("pe_percentile") or val.get("percentile"))
        if pe_pct > 80:
            overvalued_value += _safe_float(h.get("current_value", 0))
    overvalued_pct = overvalued_value / total_value * 100 if total_value > 0 else 0
    if overvalued_pct <= 10:
        val_score = 60
    elif overvalued_pct <= 20:
        val_score = 45
    elif overvalued_pct <= 30:
        val_score = 30
    elif overvalued_pct <= 50:
        val_score = 15
    else:
        val_score = 5

    # 3. 止损意识（60分，H-1从V1迁移）
    from db.portfolio import list_transactions
    txs = list_transactions(limit=200)
    sell_trades = [t for t in txs if t.get("transaction_type") == "sell"]
    stop_loss_count = sum(
        1 for t in sell_trades if _safe_float(t.get("profit_loss_pct", 0)) < -10
    )
    if sell_trades:
        stop_loss_ratio = stop_loss_count / len(sell_trades)
        if stop_loss_ratio <= 0.1:
            stop_score = 60  # 很少大亏卖出
        elif stop_loss_ratio <= 0.3:
            stop_score = 40
        else:
            stop_score = 15
    else:
        stop_score = 40  # 无卖出给中性分

    # 最大亏损（用于提示）
    max_loss = min(_safe_float(h.get("profit_rate", 0)) for h in holdings)

    score = pos_score + val_score + stop_score
    score = max(0, min(200, score))

    top_issue = ""
    if max_pct > 35:
        top_issue = f"单只基金仓位 {max_pct:.1f}%，风险过于集中"
    elif overvalued_pct > 30:
        top_issue = f"高估持仓占比 {overvalued_pct:.1f}%，建议分批止盈"
    elif max_loss < -0.3:
        top_issue = f"最大亏损标的 {max_loss*100:.1f}%，建议检查止损纪律"

    return score, {
        "max_single_pct": round(max_pct, 1),
        "overvalued_pct": round(overvalued_pct, 1),
        "stop_loss_count": stop_loss_count,
        "max_loss_pct": round(max_loss * 100, 1),
        "pos_score": pos_score,
        "val_score": val_score,
        "stop_score": stop_score,
        "top_issue": top_issue,
    }


def calc_health_score_v2(user_id: str = "default") -> dict:
    """计算健康分 2.0。"""
    profile = get_user_investment_profile(user_id) or {}
    holdings = list_holdings(user_id=user_id) or []
    active = [h for h in holdings if _safe_float(h.get("shares", 0)) > 0]

    quality_score, quality_detail = _calc_quality_score(active)
    div_score, div_detail = _calc_diversification_score(active, profile)
    val_score, val_detail = _calc_valuation_score(active)
    beh_score, beh_detail = _calc_behavior_score(user_id, active)
    risk_score, risk_detail = _calc_risk_score(active)

    total = quality_score + div_score + val_score + beh_score + risk_score

    return {
        "total_score": total,
        "score_level": _score_level(total),
        "score_change_7d": 0,
        "dimensions": [
            {"key": "quality", "label": "选品质量", "score": quality_score, "max": 200,
             "rating": _score_rating(quality_score), "top_issue": quality_detail.get("top_issue", "")},
            {"key": "diversification", "label": "分散配置", "score": div_score, "max": 200,
             "rating": _score_rating(div_score), "top_issue": div_detail.get("top_issue", "")},
            {"key": "valuation", "label": "估值合理", "score": val_score, "max": 200,
             "rating": _score_rating(val_score), "top_issue": val_detail.get("top_issue", "")},
            {"key": "behavior", "label": "持有行为", "score": beh_score, "max": 200,
             "rating": _score_rating(beh_score), "top_issue": beh_detail.get("top_issue", "")},
            {"key": "risk", "label": "风控纪律", "score": risk_score, "max": 200,
             "rating": _score_rating(risk_score), "top_issue": risk_detail.get("top_issue", "")},
        ],
        "dimension_details": {
            "quality": quality_detail,
            "diversification": div_detail,
            "valuation": val_detail,
            "behavior": beh_detail,
            "risk": risk_detail,
        },
    }


# ════════════════════════════════════════════════════════════
# 模块3：四笔钱偏离诊断
# ════════════════════════════════════════════════════════════

def build_four_pots_diagnosis(user_id: str = "default") -> dict:
    """构建四笔钱配置诊断：实际 vs 目标。"""
    profile = get_user_investment_profile(user_id) or {}
    target_pots = profile.get("target_pots") or DEFAULT_TARGET_POTS["steady"]
    holdings = list_holdings(user_id=user_id) or []
    active = [h for h in holdings if _safe_float(h.get("shares", 0)) > 0]
    actual_pots = _classify_pots(active)

    pots = []
    for key in ["cash", "steady", "long_term", "insurance"]:
        actual = actual_pots.get(key, 0)
        target = target_pots.get(key, 0)
        drift = actual - target
        status = "ok" if abs(drift) <= 5 else "warning" if abs(drift) <= 10 else "alert"
        pots.append({
            "key": key,
            "label": POT_LABELS[key],
            "actual_pct": actual,
            "target_pct": target,
            "drift_pct": round(drift, 1),
            "status": status,
        })

    return {
        "risk_level": profile.get("risk_level", "steady"),
        "target_pots": target_pots,
        "actual_pots": actual_pots,
        "pots": pots,
        "max_drift": round(max(abs(p["drift_pct"]) for p in pots), 1),
    }


# ════════════════════════════════════════════════════════════
# 模块4：行动清单
# ════════════════════════════════════════════════════════════

def _generate_actions_from_smart_add(user_id: str) -> list[dict]:
    """从智能补仓计划生成行动项。"""
    try:
        from services.advisor.smart_add_planner import generate_smart_add_plan
        plan = generate_smart_add_plan(user_id=user_id)
    except Exception as e:
        logger.warning(f"[health_v2] 生成智能补仓计划失败: {e}")
        return []

    actions = []
    for p in plan.get("plans", []) or []:
        pyr = p.get("pyramid") or {}
        if not pyr.get("triggered"):
            continue
        fund_code = p.get("fund_code", "")
        fund_name = p.get("fund_name", fund_code)
        amount = pyr.get("suggested_amount") or pyr.get("released_amount", 0)
        if amount <= 0:
            continue

        action_id = f"smart_add:{fund_code}"  # H-2：去掉日期后缀，避免跨日重复创建候选
        actions.append({
            "action_id": action_id,
            "title": f"{fund_name} 触发金字塔补仓",
            "subtitle": f"当前亏损 {p.get('profit_rate', 0) * 100:.1f}%，建议加仓 ¥{amount:,.0f}",
            "impact": 85,
            "urgency": 95,
            "category": "smart_add",
            "action_type": "buy",
            "target_code": fund_code,
            "target_name": fund_name,
            "amount": round(amount, 2),
            "cta": "查看补仓计划",
            "navigate_to": "smart-add",
        })

    # 再平衡建议
    for rb in (plan.get("portfolio_view") or {}).get("rebalance_suggestions", []) or []:
        actions.append({
            "action_id": f"rebalance:{rb.get('fund_code', '')}",  # H-2：去掉日期后缀
            "title": f"{rb.get('fund_name', '')} 偏离目标仓位",
            "subtitle": rb.get("reason", ""),
            "impact": 60,
            "urgency": 50,
            "category": "rebalance",
            "action_type": "rebalance",
            "target_code": rb.get("fund_code", ""),
            "target_name": rb.get("fund_name", ""),
            "cta": "查看调仓建议",
            "navigate_to": "allocation-dashboard",
        })

    return actions


def _generate_actions_from_cash(user_id: str, asset_overview: dict) -> list[dict]:
    """根据现金占比生成行动项。"""
    cash_ratio = asset_overview.get("cash_ratio", 0)
    cash_balance = asset_overview.get("cash_balance", 0)
    if cash_ratio <= 0.03 or cash_balance <= 0:
        return []

    return [{
        "action_id": "cash_deploy",  # H-2：去掉日期后缀
        "title": "现金占比偏高，建议增配债券或低估权益",
        "subtitle": f"当前现金 {cash_ratio:.1%}（¥{cash_balance:,.0f}），可适当配置",
        "impact": 60,
        "urgency": 50,
        "category": "cash_deploy",
        "action_type": "rebalance",
        "cta": "生成配置方案",
        "navigate_to": "allocation-dashboard",
    }]


def generate_actions(user_id: str = "default", asset_overview: dict = None,
                     health_score: dict = None) -> list[dict]:
    """生成今日行动清单。"""
    actions = []
    actions.extend(_generate_actions_from_smart_add(user_id))
    if asset_overview:
        actions.extend(_generate_actions_from_cash(user_id, asset_overview))

    # 根据健康分维度补充行动
    if health_score:
        for dim in health_score.get("dimensions", []):
            if dim["key"] == "risk" and dim["score"] < 120:
                actions.append({
                    "action_id": "risk_review",  # H-2：去掉日期后缀
                    "title": "风控分偏低，建议检查止损与仓位",
                    "subtitle": dim.get("top_issue", ""),
                    "impact": 70,
                    "urgency": 55,
                    "category": "risk_review",
                    "action_type": "review",
                    "cta": "查看风险报告",
                    "navigate_to": "alert-center",
                })
                break

    # 排序：影响力 × 紧迫性
    for a in actions:
        executable = 1.0 if a.get("action_type") != "review" else 0.7
        a["priority_score"] = int(a.get("impact", 50) * a.get("urgency", 50) * executable)

    actions.sort(key=lambda x: x["priority_score"], reverse=True)
    return actions[:10]


def _persist_actions(actions: list, user_id: str = "default") -> list[dict]:
    """将行动项沉淀为决策候选并记录追踪。

    H-2（2026-07-22）：查询已存在的 pending/accepted 行动项，跳过已活跃项，避免重复沉淀。
    """
    # H-2：查询已存在的 pending/accepted 行动项，跳过已活跃项
    existing_action_ids: set = set()
    try:
        from db.health_score import list_health_action_tracking
        for status in ("pending", "accepted"):
            existing = list_health_action_tracking(user_id=user_id, status=status, limit=200)
            existing_action_ids.update(a.get("action_id", "") for a in existing)
    except Exception as e:
        logger.debug(f"[health_v2] 查询已存在行动项失败: {e}")

    # 过滤掉已存在的活跃行动项
    if existing_action_ids:
        before_count = len(actions)
        actions = [a for a in actions if a.get("action_id", "") not in existing_action_ids]
        skipped = before_count - len(actions)
        if skipped > 0:
            logger.info(f"[health_v2] H-2 跳过 {skipped} 个已活跃行动项")

    for a in actions:
        try:
            candidate_id = create_candidate_from_structured_recommendation({
                "source_type": "health_v2",
                "scenario_type": a.get("category", "health_action"),
                "action_type": a.get("action_type", "review"),
                "target_type": "fund" if a.get("target_code") else "portfolio",
                "target_code": a.get("target_code", "portfolio"),
                "target_name": a.get("target_name", "组合"),
                "summary": a.get("title", ""),
                "reason": a.get("subtitle", ""),
                "suggested_amount": a.get("amount"),
                "confidence": "medium",
                "evidence": {
                    "impact": a.get("impact"),
                    "urgency": a.get("urgency"),
                    "priority_score": a.get("priority_score"),
                    "navigate_to": a.get("navigate_to"),
                },
                "source_snapshot": a,
                "dedupe_key": f"health_v2:{a.get('action_id')}",
                "priority": min(10, max(1, a.get("priority_score", 500) // 1000)),
            }, user_id=user_id)
            a["candidate_id"] = candidate_id
            track_health_action(
                action_id=a["action_id"],
                user_id=user_id,
                candidate_id=candidate_id,
                title=a.get("title", ""),
                category=a.get("category", ""),
                impact_estimate=a.get("impact"),
            )
        except Exception as e:
            logger.warning(f"[health_v2] 沉淀行动项失败 {a.get('action_id')}: {e}")
    return actions


# ════════════════════════════════════════════════════════════
# 模块5：仪表盘聚合
# ════════════════════════════════════════════════════════════

def get_health_v2_dashboard(user_id: str = "default", force_refresh: bool = False) -> dict:
    """获取健康度诊断仪表盘数据。"""
    global _DASHBOARD_CACHE
    now = time.time()
    if not force_refresh and _DASHBOARD_CACHE["data"] and _DASHBOARD_CACHE["user_id"] == user_id and (now - _DASHBOARD_CACHE["ts"]) < _CACHE_TTL:
        return _DASHBOARD_CACHE["data"]

    today = datetime.now().strftime("%Y-%m-%d")
    asset_overview = build_asset_overview(user_id)
    health_score = calc_health_score_v2(user_id)
    four_pots = build_four_pots_diagnosis(user_id)
    actions = generate_actions(user_id, asset_overview, health_score)

    # 沉淀行动项为候选（Phase 1 开启，后续可加开关）
    try:
        if get_config_bool("health_v2.persist_actions_enabled", True):
            actions = _persist_actions(actions, user_id)
    except Exception as e:
        logger.warning(f"[health_v2] 沉淀行动项失败: {e}")

    # 获取 7 天前分数计算变化
    try:
        history = list_health_scores_v2(user_id=user_id, limit=8)
        if len(history) >= 2:
            latest = history[0].get("total_score", 0)
            prev = history[1].get("total_score", 0)
            health_score["score_change_7d"] = latest - prev
    except Exception:
        pass

    result = {
        "date": today,
        "asset_overview": asset_overview,
        "health_score": health_score,
        "four_pots": four_pots,
        "actions": actions,
        "roadmap": _build_roadmap(actions),
    }

    # 保存快照
    try:
        save_health_score_v2(
            score_date=today,
            user_id=user_id,
            total_score=health_score["total_score"],
            score_quality=health_score["dimensions"][0]["score"],
            score_diversification=health_score["dimensions"][1]["score"],
            score_valuation=health_score["dimensions"][2]["score"],
            score_behavior=health_score["dimensions"][3]["score"],
            score_risk=health_score["dimensions"][4]["score"],
            asset_snapshot=asset_overview,
            dimension_details=health_score["dimension_details"],
            actions=actions,
        )
    except Exception as e:
        logger.warning(f"[health_v2] 保存快照失败: {e}")

    _DASHBOARD_CACHE = {"data": result, "ts": now, "user_id": user_id}
    return result


def _build_roadmap(actions: list) -> dict:
    """按时间维度聚合行动路线图。"""
    immediately = []
    this_week = []
    this_month = []
    long_term = []

    for a in actions:
        cat = a.get("category", "")
        if cat in ("smart_add", "exit_signal"):
            immediately.append(a)
        elif cat in ("cash_deploy", "rebalance"):
            this_week.append(a)
        elif cat in ("fund_replace", "diversification"):
            this_month.append(a)
        else:
            long_term.append(a)

    return {
        "immediately": immediately[:3],
        "this_week": this_week[:3],
        "this_month": this_month[:3],
        "long_term": long_term[:3],
    }


def recalculate_health_v2(user_id: str = "default") -> dict:
    """重新计算健康分并返回仪表盘。"""
    return get_health_v2_dashboard(user_id=user_id, force_refresh=True)


# ════════════════════════════════════════════════════════════
# 模块6：用户画像
# ════════════════════════════════════════════════════════════

def get_or_create_profile(user_id: str = "default") -> dict:
    """获取用户画像，不存在则创建默认。"""
    profile = get_user_investment_profile(user_id)
    if profile:
        return profile
    save_user_investment_profile(user_id=user_id, risk_level="steady")
    return get_user_investment_profile(user_id) or {
        "user_id": user_id,
        "risk_level": "steady",
        "target_pots": DEFAULT_TARGET_POTS["steady"],
    }


def update_profile(user_id: str = "default", risk_level: str = None,
                   target_date: str = None, target_pots: dict = None,
                   monthly_investable: float = None,
                   emergency_months: int = None) -> dict:
    """更新用户画像。"""
    existing = get_user_investment_profile(user_id) or {}
    save_user_investment_profile(
        user_id=user_id,
        risk_level=risk_level or existing.get("risk_level", "steady"),
        target_date=target_date if target_date is not None else existing.get("target_date"),
        target_pots=target_pots or existing.get("target_pots") or DEFAULT_TARGET_POTS["steady"],
        monthly_investable=monthly_investable if monthly_investable is not None else existing.get("monthly_investable", 0),
        emergency_months=emergency_months if emergency_months is not None else existing.get("emergency_months", 6),
    )
    # 清除缓存，使新目标配置立即生效
    global _DASHBOARD_CACHE
    _DASHBOARD_CACHE = {"data": None, "ts": 0, "user_id": None}
    return get_user_investment_profile(user_id)


# ════════════════════════════════════════════════════════════
# 模块7：历史趋势
# ════════════════════════════════════════════════════════════

def get_health_v2_history(user_id: str = "default", days: int = 30) -> list[dict]:
    """获取健康分 2.0 历史趋势。"""
    rows = list_health_scores_v2(user_id=user_id, limit=days)
    result = []
    for r in reversed(rows):  # 按日期升序
        result.append({
            "score_date": r.get("score_date"),
            "total_score": r.get("total_score", 0),
            "quality": r.get("score_quality", 0),
            "diversification": r.get("score_diversification", 0),
            "valuation": r.get("score_valuation", 0),
            "behavior": r.get("score_behavior", 0),
            "risk": r.get("score_risk", 0),
        })
    return result


# ════════════════════════════════════════════════════════════
# 模块8：行动项操作
# ════════════════════════════════════════════════════════════

def update_action_status(action_id: str, user_id: str = "default",
                         status: str = "pending", feedback: str = None,
                         actual_return: float = None) -> bool:
    """更新行动项状态。"""
    return update_health_action_status(action_id, user_id, status, feedback, actual_return)
