"""智能补仓计划器 — 估值 z-score 加权定投 + 金字塔补仓双引擎。

定位：增强现有 daily_position_advisor DCA 引擎，不替代。
- 现有引擎产出"今日信号"（触发即建议）
- 本模块产出"多档位前瞻计划表"（预规划未来补仓路径）

双引擎：
1. 估值 z-score 加权定投（日常）
   - 估值倍数 = clamp(1.0 + (-z_score) × 0.5, 0, 3.0)
   - z=-2（深度低估）→ ×2.0；z=0（中位数）→ ×1.0；z=+2（高估）→ ×0
2. 金字塔补仓（极端下跌触发）
   - 补仓资金池 = 总资产 × 15%
   - 亏损分档释放：-10%→15%, -20%→25%, -30%→30%, -40%→20%, -50%→10%

安全阀：
- 单标的累计补仓 ≤ 总仓位 25%
- 资金池总额封顶
- 估值分位回升到 60%+ 暂停引擎2
- 数据过期保护（>14天降级 PB，PB 也过期则跳过）
- 冷却期 7 天
"""

import logging
from typing import Optional

from db.config import get_config_bool, get_config_float, get_config_int
from db.portfolio import list_holdings, get_portfolio_summary, get_cash_balance
from db.valuations import get_best_valuation
from db.smart_add_snapshots import create_snapshot_with_hypothetical
from services.advisor.smart_add_metrics import (
    classify_fund,
    calc_kelly_limit,
    calc_recovery_time,
    calc_valuation_win_rate,
)

logger = logging.getLogger(__name__)


# ── 默认配置 ──────────────────────────────────

_DEFAULT_PYRAMID_TIERS = [
    {"loss_pct": -10, "release_pct": 15},
    {"loss_pct": -20, "release_pct": 25},
    {"loss_pct": -30, "release_pct": 30},
    {"loss_pct": -40, "release_pct": 20},
    {"loss_pct": -50, "release_pct": 10},
]


def _load_config() -> dict:
    """读取智能补仓配置。"""
    # 金字塔档位解析："10:15,20:25,30:30,40:20,50:10"
    raw_tiers = get_config_str_safe("smart_add.pyramid_tiers", "10:15,20:25,30:30,40:20,50:10")
    tiers = []
    try:
        for part in raw_tiers.split(","):
            loss_str, release_str = part.strip().split(":")
            tiers.append({"loss_pct": -int(loss_str), "release_pct": int(release_str)})
        tiers.sort(key=lambda t: t["loss_pct"])
    except Exception:
        tiers = list(_DEFAULT_PYRAMID_TIERS)

    return {
        "enabled": get_config_bool_safe("smart_add.enabled", True),
        "base_dca_pct": get_config_float_safe("smart_add.base_dca_pct", 4.0),
        "pyramid_enabled": get_config_bool_safe("smart_add.pyramid_enabled", True),
        "pool_pct": get_config_float_safe("smart_add.pool_pct", 15.0),
        "tiers": tiers,
        "loss_threshold": get_config_float_safe("smart_add.loss_threshold", -10.0),
        "max_single_position_pct": get_config_float_safe("smart_add.max_single_position_pct", 25.0),
        "valuation_pause_pct": get_config_float_safe("smart_add.valuation_pause_pct", 60.0),
        "stale_days": get_config_int_safe("smart_add.stale_days", 14),
        # 修复5：单标的补仓金额上限 = 原市值 × 此倍数（避免小仓位标的巨额补仓）
        "max_add_vs_position_mult": get_config_float_safe("smart_add.max_add_vs_position_mult", 2.0),
    }


def get_config_str_safe(key: str, default: str) -> str:
    try:
        from db.config import get_config
        return get_config(key, default)
    except Exception:
        return default

def get_config_bool_safe(key: str, default: bool) -> bool:
    try:
        return get_config_bool(key, default)
    except Exception:
        return default

def get_config_float_safe(key: str, default: float) -> float:
    try:
        return get_config_float(key, default)
    except Exception:
        return default

def get_config_int_safe(key: str, default: int) -> int:
    try:
        return get_config_int(key, default)
    except Exception:
        return default


# ── 引擎1：估值 z-score 加权定投 ──────────────

def _calc_valuation_multiplier(zscore: Optional[float]) -> tuple[float, str]:
    """根据 z-score 计算估值倍数。

    Returns:
        (倍数, 说明)
    """
    if zscore is None:
        return 1.0, "z-score 缺失，使用基准倍数"

    # 估值倍数 = clamp(1.0 + (-z) × 0.5, 0, 3.0)
    multiplier = max(0.0, min(3.0, 1.0 + (-zscore) * 0.5))

    if zscore <= -2.5:
        label = "极度低估"
    elif zscore <= -1.5:
        label = "深度低估"
    elif zscore <= -0.5:
        label = "低估"
    elif zscore <= 0.5:
        label = "合理区间"
    elif zscore <= 1.5:
        label = "高估"
    else:
        label = "深度高估"

    return round(multiplier, 2), label


# ── 引擎2：金字塔补仓 ─────────────────────────

def _calc_pyramid_tiers(
    profit_rate: float,
    pool_total: float,
    already_released: float,
    tiers: list,
    loss_threshold: float,
) -> list:
    """计算金字塔补仓档位。

    Args:
        profit_rate: 当前盈亏率（如 -0.35 = -35%）
        pool_total: 资金池总额
        already_released: 已释放金额
        tiers: 档位配置 [{loss_pct, release_pct}]
        loss_threshold: 触发阈值（如 -10）

    Returns:
        档位列表 [{tier, loss_pct, release_amount, triggered, cumulative}]
    """
    result = []
    cumulative = 0
    for i, tier in enumerate(tiers):
        release_amount = round(pool_total * tier["release_pct"] / 100, 2)
        cumulative += release_amount
        triggered = profit_rate * 100 <= tier["loss_pct"]
        result.append({
            "tier": i + 1,
            "loss_pct": tier["loss_pct"],
            "release_amount": release_amount,
            "cumulative": round(cumulative, 2),
            "triggered": triggered,
        })
    return result


def _calc_avg_cost_after_add(
    total_cost: float,
    total_shares: float,
    add_amount: float,
    current_price: float,
) -> Optional[float]:
    """预估补仓后的平均成本价。"""
    if not total_shares or not current_price or current_price <= 0:
        return None
    add_shares = add_amount / current_price
    new_total_cost = total_cost + add_amount
    new_total_shares = total_shares + add_shares
    if new_total_shares <= 0:
        return None
    return round(new_total_cost / new_total_shares, 4)


def _calc_pool_warning(released_amount: float, pool_total: float, deep_loss_count: int) -> str:
    """资金池配额预警：当标的已释放额超过平均池配额时标记。

    资金池为全局共享，平均配额 = 资金池总额 / 深套标的数。
    单标的释放额若超过其平均份额，会占用其他标的的补仓资金，标记预警。
    """
    if deep_loss_count <= 0 or pool_total <= 0:
        return ""
    avg_quota = pool_total / deep_loss_count
    if released_amount > avg_quota:
        return "超出平均配额"
    return ""


# ── 主入口 ──────────────────────────────────

def generate_smart_add_plan(user_id: str = "default") -> dict:
    """生成智能补仓计划（双引擎 + 计划表 + 组合视角）。

    Returns:
        {
            "plans": [...],          # 每标的的计划表
            "portfolio_view": {...},  # 组合视角
            "config": {...},          # 当前配置
            "summary": {...},         # 总览
        }
    """
    cfg = _load_config()
    if not cfg["enabled"]:
        return {"enabled": False, "message": "智能补仓计划器未开启"}

    # 1. 获取持仓 + 总资产 + 现金
    summary = get_portfolio_summary(user_id=user_id)
    total_assets = summary.get("total_assets", 0) or 0
    holdings = summary.get("holdings", []) or list_holdings(user_id=user_id)

    if not holdings:
        return {"enabled": True, "message": "暂无持仓", "plans": [], "portfolio_view": {}}

    # 资金池
    pool_total = round(total_assets * cfg["pool_pct"] / 100, 2)

    # 基础月投额（年化4% → 月度）
    base_monthly = round(total_assets * cfg["base_dca_pct"] / 100 / 12, 2)

    # 2. 对每个持仓计算计划
    plans = []
    for h in holdings:
        plan = _generate_single_plan(h, cfg, total_assets, pool_total, base_monthly)
        if plan:
            plans.append(plan)

    # 3. 组合视角：优先级排序
    deep_loss_plans = [p for p in plans if (p.get("pyramid") or {}).get("triggered_tiers", 0) > 0]
    deep_loss_plans.sort(key=lambda p: (
        p.get("profit_rate", 0),  # 亏损越大越靠前
        -((p.get("valuation") or {}).get("zscore") or 0),  # z-score越低越靠前
    ))
    deep_loss_count = len(deep_loss_plans)

    # 资金池为全局共享：回填 pool_warning（基于平均配额 = 池总额 / 深套标的数）
    for p in deep_loss_plans:
        pyr = p.get("pyramid")
        if pyr:
            pyr["pool_warning"] = _calc_pool_warning(
                pyr.get("released_amount", 0), pool_total, deep_loss_count,
            )

    # 资金池为全局共享：各标的的 released_amount 仅为"资金充足时的预估"，
    # 实际已用不能超过池总额，剩余不能为负。
    pool_demand = sum((p.get("pyramid") or {}).get("released_amount", 0) for p in plans)
    pool_used = min(pool_demand, pool_total)
    pool_remaining = max(0.0, pool_total - pool_used)

    # 修复6：资金池硬约束
    # 原逻辑各标的独立计算 released_amount，需求合计可能远超池总额（实测30.6万 vs 12.2万，超额150%）
    # 新逻辑：超额时按优先级等比缩减各标的释放额至池总额内
    if pool_demand > pool_total and pool_demand > 0:
        scale = pool_total / pool_demand
        for p in deep_loss_plans:
            pyr = p.get("pyramid")
            if pyr and pyr.get("released_amount", 0) > 0:
                original = pyr["released_amount"]
                pyr["scaled_from_pool"] = original
                pyr["released_amount"] = round(original * scale, 2)
                pyr["scale_reason"] = f"资金池不足，按{scale:.0%}缩减"
        # 缩减后重新计算 pool_used/pool_remaining
        pool_used = min(sum((p.get("pyramid") or {}).get("released_amount", 0) for p in plans), pool_total)
        pool_remaining = max(0.0, pool_total - pool_used)
    portfolio_view = {
        "total_assets": total_assets,
        "pool_total": pool_total,
        "pool_used": round(pool_used, 2),
        "pool_remaining": round(pool_remaining, 2),
        "deep_loss_count": len(deep_loss_plans),
        "priority_list": [
            {
                "fund_code": p["fund_code"],
                "fund_name": p["fund_name"],
                "profit_rate": p["profit_rate"],
                "zscore": (p.get("valuation") or {}).get("zscore"),
                "valuation_level": (p.get("valuation") or {}).get("level"),
                "released_amount": (p.get("pyramid") or {}).get("released_amount", 0),
                "remaining_tiers": (p.get("pyramid") or {}).get("remaining_tiers", 0),
                "next_trigger": (p.get("pyramid") or {}).get("next_trigger"),
                "priority": _calc_priority(p),
            }
            for p in deep_loss_plans
        ],
    }

    # ── 反事实决策验证：建议快照 + 假设交易自动落库 ──
    try:
        snapshot_enabled = get_config_bool("smart_add.snapshot_enabled", True)
    except Exception:
        snapshot_enabled = True
    if snapshot_enabled:
        for p in plans:
            try:
                # 修复9：仅金字塔触发的标的落库假设交易，engine1 月投不再落库
                # 原逻辑 engine1 月投(recurring)被当作一次性 buy 落库，语义错位且无法验证定投效果
                pyr = p.get("pyramid") or {}
                saf = p.get("safety") or {}
                suggested_amount = pyr.get("released_amount", 0) if pyr else 0
                suggested_tier = None
                if pyr and pyr.get("triggered_tiers", 0) > 0:
                    # 取已触发档位的描述
                    triggered = [t for t in pyr.get("tiers", []) if t.get("triggered")]
                    if triggered:
                        t0 = triggered[0]
                        suggested_tier = f"{t0.get('loss_pct', '?')}%档 ×{t0.get('release_pct', 0)}%"

                # 安全阀拦截时不落库（修复3 配套）
                if suggested_amount > 0 and saf.get("can_add", True):
                    val = p.get("valuation") or {}
                    create_snapshot_with_hypothetical(
                        fund_code=p["fund_code"],
                        fund_name=p.get("fund_name", ""),
                        suggested_amount=round(suggested_amount, 2),
                        suggested_tier=suggested_tier,
                        profit_rate_at_snapshot=p.get("profit_rate"),
                        valuation_zscore=val.get("zscore"),
                        current_price_at_snapshot=p.get("current_price"),
                        user_id=user_id,
                    )
            except Exception as e:
                logger.debug(f"[smart_add] 快照落库失败 {p.get('fund_code')}: {e}")

    # 自动将补仓建议转为决策候选（去重：14天内同标的同来源不重复）
    _plans_to_candidates(plans)

    return {
        "enabled": True,
        "plans": plans,
        "portfolio_view": portfolio_view,
        "config": cfg,
        "summary": {
            "total_assets": total_assets,
            "holdings_count": len(holdings),
            "deep_loss_count": len(deep_loss_plans),
            "pool_total": pool_total,
            "pool_used": portfolio_view["pool_used"],
            "pool_remaining": portfolio_view["pool_remaining"],
            "base_monthly": base_monthly,
        },
    }


def _generate_single_plan(
    holding: dict,
    cfg: dict,
    total_assets: float,
    pool_total: float,
    base_monthly: float,
    deep_loss_count: int = 0,
) -> Optional[dict]:
    """生成单个持仓的补仓计划。

    Args:
        deep_loss_count: 全局深套标的数，用于计算资金池平均配额预警。
            主流程在汇总所有计划后回填该值，首次生成时为 0（不触发预警）。
    """
    fund_code = holding.get("fund_code", "")
    fund_name = holding.get("fund_name", "")
    index_code = holding.get("index_code", "")
    profit_rate = holding.get("profit_rate") or 0
    current_value = holding.get("current_value") or 0
    total_cost = holding.get("total_cost") or 0
    shares = holding.get("shares") or 0
    current_price = holding.get("current_price") or 0

    # 估值数据
    valuation = None
    zscore = None
    valuation_level = "未知"
    if index_code:
        # 先查市盈率，查不到则降级查市净率（部分指数如银行/地产只有 PB 数据）
        val = get_best_valuation(index_code, metric_type="市盈率", query_source="smart_add", enable_online=False)
        if not val:
            val = get_best_valuation(index_code, metric_type="市净率", query_source="smart_add", enable_online=False)
        if val:
            zscore = val.get("zscore")
            percentile = val.get("percentile")
            multiplier, level = _calc_valuation_multiplier(zscore)
            valuation_level = level
            valuation = {
                "index_code": index_code,
                "index_name": val.get("index_name", ""),
                "metric_type": val.get("metric_type", ""),
                "current_value": val.get("current_value"),
                "percentile": percentile,
                "zscore": zscore,
                "snapshot_date": val.get("snapshot_date", ""),
                "is_expired": val.get("is_expired", False),
                "days_old": val.get("days_old", 0),
                "multiplier": multiplier,
                "level": level,
            }

    # L2：基金类型分类
    fund_type_info = classify_fund(holding)
    fund_type = fund_type_info["fund_type"]
    type_strategy = fund_type_info["strategy"]

    # L3：半凯利上限（替代 25% 拍脑袋）
    try:
        kelly = calc_kelly_limit(fund_code, index_code, user_id="default")
    except Exception as e:
        logger.debug(f"[smart_add] L3 kelly 计算失败 {fund_code}: {e}")
        kelly = {
            "kelly_full": 0.5, "kelly_half": 0.25, "mu": 0.0, "sigma": 0.0,
            "risk_free_rate": 0.025, "limit_pct": 25.0, "sample_days": 0,
            "data_source": "error",
        }
    # 修复4：实际上限 = min(凯利上限, 类型硬上限, 用户配置全局上限)
    # 原代码未纳入 cfg["max_single_position_pct"]，导致该配置项是死代码（前端可改不生效）
    max_position_pct = min(
        kelly["limit_pct"],
        type_strategy["hard_cap_pct"],
        cfg["max_single_position_pct"],
    )

    # L4：修复时间调整节奏
    recovery = {}
    rhythm_adjust = 1.0
    try:
        if index_code:
            recovery = calc_recovery_time(index_code)
            if recovery and recovery.get("median_recovery_months"):
                if recovery["median_recovery_months"] > 24:
                    rhythm_adjust = 0.5  # 双月投
                elif recovery["median_recovery_months"] < 12:
                    rhythm_adjust = 1.5  # 半月投
    except Exception as e:
        logger.debug(f"[smart_add] L4 recovery 计算失败 {index_code}: {e}")
        recovery = {}

    # L5：胜率调整倍数
    win_rate = {}
    confidence_mult = 1.0
    try:
        if valuation and valuation.get("percentile") is not None and index_code:
            win_rate = calc_valuation_win_rate(index_code, valuation["percentile"])
            if win_rate and win_rate.get("win_rate_12m") is not None:
                if win_rate["win_rate_12m"] > 0.80:
                    confidence_mult = 1.3
                elif win_rate["win_rate_12m"] < 0.60:
                    confidence_mult = 0.7
    except Exception as e:
        logger.debug(f"[smart_add] L5 win_rate 计算失败 {index_code}: {e}")
        win_rate = {}

    # 引擎1：估值 z-score 加权定投（含 L2/L4/L5 调整）
    multiplier = valuation["multiplier"] if valuation else 1.0
    # 修复1：最终月投 = 基础 × z-score倍数 × 类型倍数 × 胜率倍数 × 节奏调整
    # 原代码为 ÷ rhythm_adjust，导致长修复(>24月,rhythm=0.5)时金额翻倍，与"拉长周期减半"意图相反
    final_monthly = round(
        base_monthly * multiplier * type_strategy["dca_mult"] * confidence_mult * rhythm_adjust, 2
    )

    engine1 = {
        "base_monthly": base_monthly,
        "zscore_multiplier": multiplier,
        "multiplier": multiplier,  # 向后兼容前端展示
        "type_multiplier": type_strategy["dca_mult"],
        "confidence_mult": confidence_mult,
        "rhythm_adjust": rhythm_adjust,
        "monthly_dca": final_monthly,
        "valuation_level": valuation_level,
        "formula": f"{base_monthly} × {multiplier} × {type_strategy['dca_mult']} × {confidence_mult} × {rhythm_adjust}",
    }

    # 引擎2：金字塔补仓
    engine2 = None
    profit_rate_pct = profit_rate * 100

    # 修复2：估值暂停机制
    # 原逻辑 `not valuation or (percentile or 0) < 60` 在无估值数据时直接放行，导致高估值但数据缺失的标的误补
    # 新逻辑：有分位数据时检查阈值；无分位数据时权益类保守不触发，债券类放行（债券不走估值逻辑）
    valuation_ok = True
    if valuation and valuation.get("percentile") is not None:
        valuation_ok = valuation["percentile"] < cfg["valuation_pause_pct"]
    elif fund_type != "bond":
        # 无估值数据且非债券：保守不触发（避免高估值标的数据缺失时误补）
        valuation_ok = False
    # bond 类型无指数估值是正常的，不因此阻止

    # 修复7：债券基金排除金字塔
    # bond 类型 pyramid_aggressive=False，但原逻辑未检查此字段；债券靠票息修复，不需金字塔
    pyramid_aggressive = type_strategy.get("pyramid_aggressive", True)

    if (
        cfg["pyramid_enabled"]
        and profit_rate_pct <= cfg["loss_threshold"]
        and valuation_ok
        and pyramid_aggressive
    ):
        tiers = _calc_pyramid_tiers(
            profit_rate, pool_total, 0, cfg["tiers"], cfg["loss_threshold"],
        )
        triggered_count = sum(1 for t in tiers if t["triggered"])
        released_amount = sum(t["release_amount"] for t in tiers if t["triggered"])
        remaining_tiers = len(tiers) - triggered_count
        next_trigger = next((t for t in tiers if not t["triggered"]), None)

        # 预估摊薄效果（全触发后）
        total_release = sum(t["release_amount"] for t in tiers)
        avg_cost_after = _calc_avg_cost_after_add(total_cost, shares, total_release, current_price)
        current_avg_cost = total_cost / shares if shares else 0
        improvement = None
        if avg_cost_after and current_avg_cost:
            # 改善 = 补仓后盈亏率 - 当前盈亏率
            new_profit = (current_price - avg_cost_after) / avg_cost_after if avg_cost_after else 0
            improvement = round((new_profit - profit_rate) * 100, 2)

        engine2 = {
            "pool_total": pool_total,
            "released_amount": round(released_amount, 2),
            "remaining_amount": round(pool_total - released_amount, 2),
            "triggered_tiers": triggered_count,
            "remaining_tiers": remaining_tiers,
            "tiers": tiers,
            "next_trigger": {
                "loss_pct": next_trigger["loss_pct"] if next_trigger else None,
                "release_amount": next_trigger["release_amount"] if next_trigger else None,
            } if next_trigger else None,
            "avg_cost_after_full_add": avg_cost_after,
            "current_avg_cost": round(current_avg_cost, 4) if current_avg_cost else None,
            "improvement_pct": improvement,
            "pool_warning": _calc_pool_warning(released_amount, pool_total, deep_loss_count),
        }

        # 修复5：小仓位标的补仓上限
        # 原逻辑无"补仓金额 ≤ 原市值 × N倍"约束，导致0.3%仓位标的建议补4.9万（原市值20倍）
        max_add_amount = current_value * cfg["max_add_vs_position_mult"]
        if engine2["released_amount"] > max_add_amount and max_add_amount > 0:
            engine2["scaled_from_position_cap"] = engine2["released_amount"]
            engine2["released_amount"] = round(max_add_amount, 2)
            engine2["capped_reason"] = f"受原市值{cfg['max_add_vs_position_mult']}倍上限约束"

    # 持仓占比 + 安全阀（用 L3 凯利上限替代原 25%）
    position_pct = round(current_value / total_assets * 100, 2) if total_assets else 0
    can_add = position_pct < max_position_pct

    # 修复3：安全阀拦截
    # 原逻辑 can_add=False 时仅设标志位，released_amount 照常计算并落库假设交易
    # 新逻辑：安全阀未通过时，金字塔释放额归零，标记拦截原因
    if engine2 and not can_add:
        engine2["blocked_reason"] = f"已达仓位上限 {max_position_pct:.1f}%，暂停补仓"
        engine2["released_amount"] = 0

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "index_code": index_code,
        "index_name": holding.get("index_name", ""),
        "shares": shares,
        "cost_price": holding.get("cost_price"),
        "current_price": current_price,
        "total_cost": total_cost,
        "current_value": current_value,
        "profit_rate": round(profit_rate, 4),
        "profit_rate_pct": round(profit_rate * 100, 2),
        "position_pct": position_pct,
        "valuation": valuation,
        "engine1": engine1,
        "pyramid": engine2,
        "fund_type": fund_type_info["label"],
        "fund_type_code": fund_type,
        "type_strategy": type_strategy,
        "kelly": kelly,
        "recovery": recovery,
        "win_rate": win_rate,
        "confidence_mult": confidence_mult,
        "rhythm_adjust": rhythm_adjust,
        "safety": {
            "max_position_pct": max_position_pct,
            "current_position_pct": position_pct,
            "can_add": can_add,
            "kelly_limit": kelly["limit_pct"],
            "type_hard_cap": type_strategy["hard_cap_pct"],
            "reason": "" if can_add else f"已达配置上限 {max_position_pct:.1f}%（凯利{kelly['limit_pct']:.0f}%/类型{type_strategy['hard_cap_pct']:.0f}%）",
        },
    }


def _calc_priority(plan: dict) -> str:
    """计算补仓优先级。"""
    profit_rate = plan.get("profit_rate", 0)
    zscore = plan.get("valuation", {}).get("zscore") if plan.get("valuation") else None
    position_pct = plan.get("position_pct", 0)

    # 亏损大 + 低估 + 仓位低 → 高优先级
    score = 0
    if profit_rate <= -0.30:
        score += 3
    elif profit_rate <= -0.20:
        score += 2
    elif profit_rate <= -0.10:
        score += 1

    if zscore is not None and zscore <= -2:
        score += 2
    elif zscore is not None and zscore <= -1:
        score += 1

    if position_pct < 10:
        score += 1
    elif position_pct > 20:
        score -= 1

    if score >= 4:
        return "★★★"
    elif score >= 2:
        return "★★☆"
    else:
        return "★☆☆"


def get_smart_add_config() -> dict:
    """读取智能补仓配置（API 用）。"""
    return _load_config()


def preview_add_scenario(
    fund_code: str,
    additional_drop_pct: float,
    add_amount: float,
    user_id: str = "default",
) -> dict:
    """模拟"如果再跌X%后补Y元"的摊薄效果。

    Args:
        fund_code: 基金代码
        additional_drop_pct: 额外下跌百分比（如 -5 表示再跌5%）
        add_amount: 补仓金额
        user_id: 用户ID

    Returns:
        {current_cost, simulated_price, new_cost, improvement_pct, ...}
    """
    from db.portfolio import get_holding_by_fund
    holding = get_holding_by_fund(fund_code, user_id=user_id)
    if not holding:
        return {"error": f"未找到持仓 {fund_code}"}

    current_price = holding.get("current_price") or 0
    cost_price = holding.get("cost_price") or 0
    shares = holding.get("shares") or 0
    total_cost = holding.get("total_cost") or 0

    if not current_price or not shares:
        return {"error": "持仓数据不完整（价格或份额缺失）"}

    # 模拟下跌后的价格
    simulated_price = round(current_price * (1 + additional_drop_pct / 100), 4)

    # 补仓后平均成本
    new_cost = _calc_avg_cost_after_add(total_cost, shares, add_amount, simulated_price)
    current_avg_cost = total_cost / shares if shares else 0

    current_profit = (current_price - current_avg_cost) / current_avg_cost if current_avg_cost else 0
    new_profit = (simulated_price - new_cost) / new_cost if new_cost else 0

    return {
        "fund_code": fund_code,
        "fund_name": holding.get("fund_name", ""),
        "current_price": current_price,
        "simulated_price": simulated_price,
        "additional_drop_pct": additional_drop_pct,
        "add_amount": add_amount,
        "current_avg_cost": round(current_avg_cost, 4),
        "new_avg_cost": new_cost,
        "current_profit_rate": round(current_profit, 4),
        "new_profit_rate": round(new_profit, 4),
        "improvement_pct": round((new_profit - current_profit) * 100, 2),
        "new_shares": round(shares + add_amount / simulated_price, 2),
    }


def _plans_to_candidates(plans: list[dict]) -> None:
    """将智能补仓建议自动转为决策候选（去重：14天内同标的同来源不重复）。

    复用 smart_add.enabled 配置项，无需新增开关。
    """
    from db.decisions import create_candidate_from_structured_recommendation
    for plan in plans:
        fund_code = plan.get("fund_code", "")
        fund_name = plan.get("fund_name", "")
        if not fund_code:
            continue
        pyramid = plan.get("pyramid") or {}
        engine1 = plan.get("engine1") or {}
        released = pyramid.get("released_amount", 0) or 0
        monthly = engine1.get("monthly_dca", 0) or 0
        if released <= 0 and monthly <= 0:
            continue
        # 安全阀未通过的不创建
        if not (plan.get("safety") or {}).get("can_add", True):
            continue
        amount = released if released > 0 else monthly
        profit_rate = plan.get("profit_rate_pct", 0) or 0
        val = plan.get("valuation") or {}
        percentile = val.get("percentile", "N/A")
        rationale_parts = [f"当前盈亏 {profit_rate:.1f}%", f"估值分位 {percentile}", f"月定投 {monthly:.0f}元"]
        if released > 0:
            rationale_parts.append(f"金字塔释放 {released:.0f}元")
        try:
            create_candidate_from_structured_recommendation({
                "source_type": "smart_add",
                "action_type": "add",
                "target_type": "fund",
                "target_code": fund_code,
                "target_name": fund_name,
                "summary": f"{fund_name} 智能补仓建议：{amount:.0f}元",
                "rationale": "；".join(rationale_parts),
                "suggested_amount": amount,
                "confidence": "high" if plan.get("confidence_mult", 1) > 1 else "medium",
                "dedupe_key": f"smart_add_{fund_code}",
                "priority": 1 if profit_rate < -10 else 2,
                "source_snapshot": {
                    "profit_rate": plan.get("profit_rate"),
                    "valuation": val,
                    "safety": plan.get("safety"),
                },
            })
        except Exception as e:
            logger.debug(f"[smart_add] 自动创建决策候选失败 {fund_code}: {e}")
