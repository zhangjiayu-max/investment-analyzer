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
from datetime import datetime, timedelta
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
    # 2026-07-17 重构：金额基准从"资金池×释放率"改为"标的市值×加仓比例"
    # add_ratio = 加仓额占标的当前市值的百分比（亏损越深加仓比例越大，但单次不超过25%）
    # 每次只补"下一档"，不累加历史档位
    {"loss_pct": -10, "release_pct": 15, "add_ratio": 5},
    {"loss_pct": -20, "release_pct": 25, "add_ratio": 10},
    {"loss_pct": -30, "release_pct": 30, "add_ratio": 15},
    {"loss_pct": -40, "release_pct": 20, "add_ratio": 20},
    {"loss_pct": -50, "release_pct": 10, "add_ratio": 25},
]


def _load_config() -> dict:
    """读取智能补仓配置。"""
    # 金字塔档位解析：支持新格式"10:15:5,20:25:10,30:30:15,40:20:20,50:10:25"（亏损:释放率:加仓比例）
    # 兼容旧格式"10:15,20:25,30:30,40:20,50:10"（无add_ratio时用release_pct兜底）
    raw_tiers = get_config_str_safe("smart_add.pyramid_tiers", "10:15:5,20:25:10,30:30:15,40:20:20,50:10:25")
    tiers = []
    try:
        for part in raw_tiers.split(","):
            fields = part.strip().split(":")
            if len(fields) >= 3:
                # 新格式：亏损:释放率:加仓比例
                loss_str, release_str, add_str = fields[0], fields[1], fields[2]
                tiers.append({"loss_pct": -int(loss_str), "release_pct": int(release_str), "add_ratio": int(add_str)})
            elif len(fields) == 2:
                # 旧格式兼容：亏损:释放率（add_ratio=release_pct兜底）
                loss_str, release_str = fields[0], fields[1]
                tiers.append({"loss_pct": -int(loss_str), "release_pct": int(release_str), "add_ratio": int(release_str)})
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
        # 2026-07-17 调整：从2.0降至1.0（配合档位重构，单次补仓不超过当前市值）
        "max_add_vs_position_mult": get_config_float_safe("smart_add.max_add_vs_position_mult", 1.0),
        # 多维度触发器配置（2026-07-17 新增）
        "cooldown_days": get_config_int_safe("smart_add.cooldown_days", 10),
        "max_buys_in_cooldown": get_config_int_safe("smart_add.max_buys_in_cooldown", 2),
        "trend_signal_enabled": get_config_bool_safe("smart_add.trend_signal_enabled", True),
        "trend_lookback_days": get_config_int_safe("smart_add.trend_lookback_days", 20),
        "trend_min_gain_pct": get_config_float_safe("smart_add.trend_min_gain_pct", 3.0),
        "trend_position_pct": get_config_float_safe("smart_add.trend_position_pct", 5.0),
        "dip_signal_enabled": get_config_bool_safe("smart_add.dip_signal_enabled", True),
        "dca_drop_step_pct": get_config_float_safe("smart_add.dca_drop_step_pct", 4.0),
        "dca_tiers": get_config_str_safe("smart_add.dca_tiers", "4:1.0,8:1.5,12:2.0"),
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
    current_value: float = 0,
) -> list:
    """计算金字塔补仓档位。

    2026-07-17 重构：
    1. 金额计算从"资金池×释放率"改为"标的市值×加仓比例"
    2. 只预估下次补仓金额（不累加历史已触发档位）

    金字塔补仓法本意：每跌一档补一次，不是一次把所有档都补了。
    当前-31.3%触发-10/-20/-30三档，但下次实际只会补"跌到-40%时"那一档。
    所以"建议金额"=下一档待触发档位的加仓额，而非所有已触发档位累加。

    Args:
        profit_rate: 当前盈亏率（如 -0.35 = -35%）
        pool_total: 资金池总额（组合层硬约束，仅在缩减时使用）
        already_released: 已释放金额
        tiers: 档位配置 [{loss_pct, release_pct, add_ratio}]
        loss_threshold: 触发阈值（如 -10）
        current_value: 标的当前市值（新参数，金额计算基准）

    Returns:
        档位列表 [{tier, loss_pct, release_amount, triggered, cumulative, add_ratio, is_next}]
        is_next=True 表示这是"下次待触发"的档位（建议金额以此为准）
    """
    result = []
    cumulative = 0
    # 找到"下次待触发"档位 = 亏损加深后下一个会触发的档位
    # tiers 已按 loss_pct 升序排序（-50 < -40 < -30 < -20 < -10）
    # 当前亏损-16.9%，已触发-10%（浅档），未触发-20/-30/-40/-50
    # 下次待触发 = 未触发档位中 loss_pct 最接近当前亏损率的那个（即最浅的未触发档）
    # 即：第一个 triggered=False 的档位（从浅到深方向）
    # 但 tiers 是从深(-50)到浅(-10)排序，所以要反向找
    next_untriggered_idx = None
    # 从浅档往深档找第一个未触发的
    for i in range(len(tiers) - 1, -1, -1):
        if not (profit_rate * 100 <= tiers[i]["loss_pct"]):
            next_untriggered_idx = i
            break
    # 特殊处理：如果所有档都触发（亏损极深），取最深的档
    if next_untriggered_idx is None and tiers:
        next_untriggered_idx = 0  # 最深档（-50%）

    for i, tier in enumerate(tiers):
        # 金额 = 标的市值 × 加仓比例（%）；无市值数据时降级回池子×释放率
        add_ratio = tier.get("add_ratio", tier.get("release_pct", 0))
        if current_value > 0:
            release_amount = round(current_value * add_ratio / 100, 2)
        else:
            # 降级：原逻辑兜底
            release_amount = round(pool_total * tier["release_pct"] / 100, 2)
        cumulative += release_amount
        triggered = profit_rate * 100 <= tier["loss_pct"]
        result.append({
            "tier": i + 1,
            "loss_pct": tier["loss_pct"],
            "release_amount": release_amount,
            "cumulative": round(cumulative, 2),
            "triggered": triggered,
            "add_ratio": add_ratio,
            "is_next": (i == next_untriggered_idx),  # 下次待触发的档位
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


# ── 多维度触发器：信号 B 趋势加仓 + 信号 C 大跌定投 + 冷却期 ───

def _check_cooldown(fund_code: str, cfg: dict) -> tuple[bool, int, str]:
    """冷却期检查：近 cooldown_days 内同基金买入次数是否超限。

    Returns:
        (can_proceed, recent_buy_count, reason)
        - can_proceed=True: 可继续补仓
        - can_proceed=False: 已达冷却期上限，应拦截
    """
    try:
        from db._conn import _get_conn
        cooldown_days = cfg.get("cooldown_days", 10)
        max_buys = cfg.get("max_buys_in_cooldown", 2)
        cutoff = (datetime.now() - timedelta(days=cooldown_days)).strftime("%Y-%m-%d")
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM portfolio_transactions "
                "WHERE fund_code=? AND transaction_type='buy' "
                "AND transaction_date >= ? AND status IN ('confirmed','pending','submitted')",
                (fund_code, cutoff),
            ).fetchone()
            count = row["cnt"] if row else 0
        finally:
            conn.close()
        if count >= max_buys:
            return False, count, f"冷却期内已补仓{count}次（{cooldown_days}天内上限{max_buys}次）"
        return True, count, ""
    except Exception as e:
        logger.debug(f"[smart_add] 冷却期检查失败 {fund_code}: {e}")
        return True, 0, ""  # 失败时放行


def _get_last_buy_price(fund_code: str) -> Optional[float]:
    """获取最近一次买入价（用于计算累计跌幅）。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT price FROM portfolio_transactions "
                "WHERE fund_code=? AND transaction_type='buy' AND status='confirmed' "
                "AND price IS NOT NULL AND price > 0 "
                "ORDER BY transaction_date DESC LIMIT 1",
                (fund_code,),
            ).fetchone()
            return float(row["price"]) if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[smart_add] 获取上次买入价失败 {fund_code}: {e}")
        return None


def _calc_recent_nav_change(fund_code: str, lookback_days: int) -> Optional[float]:
    """计算近 N 日基金净值涨跌幅（%）。

    Returns:
        涨跌幅（如 3.5 表示 +3.5%），数据不足返回 None
    """
    try:
        from services.fund_data_service import get_fund_nav_history_from_cache
        navs = get_fund_nav_history_from_cache(fund_code, days=lookback_days)
        if not navs or len(navs) < 2:
            return None
        valid = [n for n in navs if n.get("nav") and n["nav"] > 0]
        if len(valid) < 2:
            return None
        first = valid[0]["nav"]
        last = valid[-1]["nav"]
        if first <= 0:
            return None
        return round((last - first) / first * 100, 2)
    except Exception as e:
        logger.debug(f"[smart_add] 计算近{lookback_days}日涨跌失败 {fund_code}: {e}")
        return None


def _detect_trend_signal(
    holding: dict,
    valuation: Optional[dict],
    cfg: dict,
    base_monthly: float,
) -> Optional[dict]:
    """信号 B：趋势加仓（近期涨势好）。

    触发条件（三选二）：
    1. 估值分位 35-60%（合理区间，非高估）
    2. 近 20 日基金净值涨幅 > trend_min_gain_pct%
    3. 近 5 日涨幅 > 1%（短趋势确认，数据不可得时降级为条件2放大）

    建议金额：基础月投 × 1.5（轻仓试探，仓位上限 5% 总资产）
    """
    if not cfg.get("trend_signal_enabled", True):
        return None

    fund_code = holding.get("fund_code", "")
    fund_name = holding.get("fund_name", "")

    # 条件1：估值分位 35-60%
    cond1 = False
    val_pct = None
    if valuation and valuation.get("percentile") is not None:
        val_pct = valuation["percentile"]
        cond1 = 35 <= val_pct < 60

    # 条件2：近 20 日涨幅 > trend_min_gain_pct%
    lookback = cfg.get("trend_lookback_days", 20)
    min_gain = cfg.get("trend_min_gain_pct", 3.0)
    gain_20d = _calc_recent_nav_change(fund_code, lookback)
    cond2 = gain_20d is not None and gain_20d > min_gain

    # 条件3：近 5 日短趋势确认（数据不足时降级为条件2放大）
    gain_5d = _calc_recent_nav_change(fund_code, 5)
    cond3 = gain_5d is not None and gain_5d > 1.0
    if gain_5d is None and gain_20d is not None and gain_20d > min_gain * 1.5:
        cond3 = True  # 降级：近20日涨幅超1.5倍阈值视为短趋势确认

    # 三选二
    hits = sum([cond1, cond2, cond3])
    if hits < 2:
        return None

    # 冷却期检查
    can_proceed, recent_count, block_reason = _check_cooldown(fund_code, cfg)
    if not can_proceed:
        return {
            "type": "trend",
            "label": "趋势加仓",
            "triggered": False,
            "blocked_reason": block_reason,
            "conditions_met": [c for c, v in zip(["估值合理", f"近{lookback}日涨{gain_20d}%", "近5日涨{gain_5d}%"], [cond1, cond2, cond3]) if v],
        }

    # 建议金额动态化：基础月投 × 趋势强度系数 × 估值系数 × 仓位余量系数
    # 1. 趋势强度系数：涨3%→×1.0，涨5%→×1.3，涨8%→×1.5（线性插值，上限1.5）
    trend_strength_mult = 1.0
    if gain_20d is not None:
        if gain_20d >= 8:
            trend_strength_mult = 1.5
        elif gain_20d >= 5:
            trend_strength_mult = 1.0 + (gain_20d - 5) * 0.1  # 5→1.0, 8→1.3... 线性
        elif gain_20d >= 3:
            trend_strength_mult = 1.0  # 刚达阈值
        else:
            trend_strength_mult = max(0.8, 1.0 - (3 - gain_20d) * 0.1)

    # 2. 估值系数：35-45%分位→×1.2，45-55%→×1.0，55-60%→×0.8
    valuation_mult = 1.0
    if val_pct is not None:
        if val_pct < 45:
            valuation_mult = 1.2
        elif val_pct < 55:
            valuation_mult = 1.0
        else:
            valuation_mult = 0.8

    # 3. 仓位余量系数：(上限-当前仓位)/上限，仓位越低补越多
    position_cap_pct = cfg.get("trend_position_pct", 5)
    total_assets = holding.get("_total_assets", 0)  # 由调用方注入
    current_value = holding.get("current_value") or 0
    current_position_pct = (current_value / total_assets * 100) if total_assets else 0
    # 仓位余量 = max(0.3, (上限-当前)/上限)，最低0.3避免仓位已高时建议为0
    room_mult = max(0.3, (position_cap_pct - current_position_pct) / position_cap_pct) if position_cap_pct > 0 else 1.0

    amount = round(base_monthly * trend_strength_mult * valuation_mult * room_mult, 2)
    # 仓位上限硬约束：5% 总资产
    if total_assets:
        cap_amount = round(total_assets * position_cap_pct / 100, 2)
        if amount > cap_amount:
            amount = cap_amount

    reasons = []
    if cond1:
        reasons.append(f"估值合理({val_pct:.0f}%分位)")
    if cond2:
        reasons.append(f"近{lookback}日涨{gain_20d}%")
    if cond3:
        reasons.append("短趋势确认")

    return {
        "type": "trend",
        "label": "趋势加仓",
        "triggered": True,
        "amount": amount,
        "reason": "近期涨势好，轻仓试探（短期波段，严格止损-5%）",
        "conditions_met": reasons,
        "position_cap_pct": position_cap_pct,
        "tag": "短期波段",
        # 金额计算依据（让用户看懂"一次加多少"是怎么来的）
        "amount_formula": {
            "base_monthly": base_monthly,
            "trend_strength_mult": round(trend_strength_mult, 2),
            "valuation_mult": valuation_mult,
            "room_mult": round(room_mult, 2),
            "current_position_pct": round(current_position_pct, 2),
            "formula": f"{base_monthly} × {round(trend_strength_mult,2)} × {valuation_mult} × {round(room_mult,2)} = {amount}",
        },
    }


def _detect_dip_signal(
    holding: dict,
    valuation: Optional[dict],
    cfg: dict,
    base_monthly: float,
) -> Optional[dict]:
    """信号 C：大跌定投（连续大跌4%定投）。

    触发条件：
    1. 相对上次买入价累计跌幅 ≥ dca_drop_step_pct（默认4%）
    2. 估值分位 < valuation_pause_pct（非高估，避免接飞刀）
    3. 冷却期内买入次数 < max_buys_in_cooldown（跌幅≥8%可突破）

    档位：4%→月投×1.0，8%→月投×1.5，12%→月投×2.0
    """
    if not cfg.get("dip_signal_enabled", True):
        return None

    fund_code = holding.get("fund_code", "")
    current_price = holding.get("current_price") or 0
    if current_price <= 0:
        return None

    # 条件1：累计跌幅 ≥ 4%
    last_buy_price = _get_last_buy_price(fund_code)
    if not last_buy_price or last_buy_price <= 0:
        return None  # 无历史买入价，无法计算跌幅
    drop_pct = (last_buy_price - current_price) / last_buy_price * 100
    step_pct = cfg.get("dca_drop_step_pct", 4)
    if drop_pct < step_pct:
        return None

    # 条件2：估值分位 < valuation_pause_pct（非高估）
    if valuation and valuation.get("percentile") is not None:
        if valuation["percentile"] >= cfg.get("valuation_pause_pct", 60):
            return None  # 高估，不接飞刀

    # 档位计算：4%→×1.0，8%→×1.5，12%→×2.0
    tiers_str = cfg.get("dca_tiers", "4:1.0,8:1.5,12:2.0")
    try:
        tiers = []
        for t in tiers_str.split(","):
            parts = t.strip().split(":")
            if len(parts) == 2:
                tiers.append((float(parts[0]), float(parts[1])))
        tiers.sort(key=lambda x: x[0])
    except Exception:
        tiers = [(4, 1.0), (8, 1.5), (12, 2.0)]

    # 找命中的最高档
    matched_tier = None
    for thresh, mult in tiers:
        if drop_pct >= thresh:
            matched_tier = (thresh, mult)
    if not matched_tier:
        return None

    # 条件3：冷却期检查（跌幅≥8%可突破）
    can_proceed, recent_count, block_reason = _check_cooldown(fund_code, cfg)
    if not can_proceed and drop_pct < 8:
        return {
            "type": "dip",
            "label": "大跌定投",
            "triggered": False,
            "blocked_reason": block_reason,
            "drop_pct": round(drop_pct, 2),
            "tier": f"-{matched_tier[0]}%",
        }

    # 建议金额动态化：基础月投 × 跌幅系数 × 亏损系数 × 仓位余量系数
    # 1. 跌幅系数：使用档位倍数（4%→×1.0, 8%→×1.5, 12%→×2.0），档位间线性插值
    drop_mult = matched_tier[1]

    # 2. 亏损系数：亏损0-10%→×1.0，亏损10-20%→×1.2，亏损>20%→×1.3
    profit_rate = holding.get("profit_rate") or 0
    loss_mult = 1.0
    if profit_rate < -0.20:
        loss_mult = 1.3
    elif profit_rate < -0.10:
        loss_mult = 1.2

    # 3. 仓位余量系数：(25%上限-当前仓位)/25%，仓位越低补越多，最低0.3
    max_pos_pct = cfg.get("max_single_position_pct", 25.0)
    total_assets = holding.get("_total_assets", 0)
    current_value = holding.get("current_value") or 0
    current_position_pct = (current_value / total_assets * 100) if total_assets else 0
    room_mult = max(0.3, (max_pos_pct - current_position_pct) / max_pos_pct) if max_pos_pct > 0 else 1.0

    amount = round(base_monthly * drop_mult * loss_mult * room_mult, 2)

    return {
        "type": "dip",
        "label": "大跌定投",
        "triggered": True,
        "amount": amount,
        "drop_pct": round(drop_pct, 2),
        "last_buy_price": last_buy_price,
        "current_price": current_price,
        "tier": f"-{matched_tier[0]}%",
        "multiplier": matched_tier[1],
        "reason": f"较上次买入跌{drop_pct:.1f}%，分批定投（档位-{matched_tier[0]}%，月投×{matched_tier[1]}）",
        "tag": "分批定投",
        # 金额计算依据
        "amount_formula": {
            "base_monthly": base_monthly,
            "drop_mult": drop_mult,
            "loss_mult": loss_mult,
            "room_mult": round(room_mult, 2),
            "current_position_pct": round(current_position_pct, 2),
            "profit_rate_pct": round(profit_rate * 100, 2),
            "formula": f"{base_monthly} × {drop_mult} × {loss_mult} × {round(room_mult,2)} = {amount}",
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
    # 注入总资产供信号 B 仓位上限计算
    holding["_total_assets"] = total_assets
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
            current_value=current_value,  # 新增：金额计算基准为标的市值
        )
        triggered_count = sum(1 for t in tiers if t["triggered"])
        # 2026-07-17 重构：只预估下次补仓金额（不累加历史已触发档位）
        # 下次补仓 = 第一个未触发档位（is_next=True）的金额；若所有档位都触发，取最后一档
        next_tier = next((t for t in tiers if t.get("is_next")), None)
        if next_tier is None:
            # 所有档位都触发，取最后一档作为"下次"档
            next_tier = tiers[-1] if tiers else None
        released_amount = next_tier["release_amount"] if next_tier else 0
        remaining_tiers = len(tiers) - triggered_count
        next_trigger = next_tier

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
                "loss_pct": next_trigger["loss_pct"],
                "release_amount": next_trigger["release_amount"],
                "add_ratio": next_trigger.get("add_ratio"),
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

    # ── 多维度触发器：信号 A 金字塔(已有) + 信号 B 趋势 + 信号 C 大跌定投 ──
    triggered_signals = []
    # 信号 A：金字塔（已有 engine2，转成统一格式）
    if engine2 and engine2.get("released_amount", 0) > 0 and not engine2.get("blocked_reason"):
        triggered_signals.append({
            "type": "pyramid",
            "label": "金字塔补仓",
            "triggered": True,
            "amount": engine2["released_amount"],
            "reason": f"亏损{profit_rate*100:.1f}%+估值合理，金字塔档位释放",
            "tag": "摊低成本",
        })

    # 信号 B：趋势加仓（近期涨势好）— 与信号 A 可叠加
    signal_b = _detect_trend_signal(holding, valuation, cfg, base_monthly)
    if signal_b:
        triggered_signals.append(signal_b)

    # 信号 C：大跌定投（连续大跌4%）— 与信号 A 互斥（亏损-10%已触发金字塔不再触发4%定投）
    signal_c = None
    if not (engine2 and engine2.get("released_amount", 0) > 0):
        signal_c = _detect_dip_signal(holding, valuation, cfg, base_monthly)
        if signal_c:
            triggered_signals.append(signal_c)

    # 总建议金额（所有命中信号汇总，受安全阀约束）
    total_suggested = sum(
        s.get("amount", 0) for s in triggered_signals if s.get("triggered")
    )
    if not can_add:
        total_suggested = 0

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
        "triggered_signals": triggered_signals,  # 多维度触发器命中的信号列表
        "total_suggested": round(total_suggested, 2),  # 所有命中信号的建议金额汇总
        "has_signal": len([s for s in triggered_signals if s.get("triggered")]) > 0,
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
