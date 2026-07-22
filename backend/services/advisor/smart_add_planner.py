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
# S-1（2026-07-22）：计划持久化
from db.smart_add_plans import save_smart_add_plan
from services.advisor.smart_add_metrics import (
    classify_fund,
    calc_kelly_limit,
    calc_recovery_time,
    calc_valuation_win_rate,
)
from services.advisor.position_sizing import (
    generate_position_sizing_plan,
    _effective_base,
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
        "trend_base_ratio": get_config_float_safe("smart_add.trend_base_ratio", 5.0),
        "dip_signal_enabled": get_config_bool_safe("smart_add.dip_signal_enabled", True),
        "dip_base_ratio": get_config_float_safe("smart_add.dip_base_ratio", 5.0),
        "dca_drop_step_pct": get_config_float_safe("smart_add.dca_drop_step_pct", 4.0),
        "dca_tiers": get_config_str_safe("smart_add.dca_tiers", "4:1.0,8:1.5,12:2.0"),
        # 退出信号配置（2026-07-17 新增）
        "exit_signal_enabled": get_config_bool_safe("smart_add.exit_signal_enabled", False),
        "take_profit_broad_pct": get_config_float_safe("smart_add.take_profit_broad_pct", 20.0),
        "take_profit_theme_pct": get_config_float_safe("smart_add.take_profit_theme_pct", 30.0),
        "stop_loss_pct": get_config_float_safe("smart_add.stop_loss_pct", -30.0),
        "stop_loss_valuation_pct": get_config_float_safe("smart_add.stop_loss_valuation_pct", 50.0),
        "max_drawdown_from_peak_pct": get_config_float_safe("smart_add.max_drawdown_from_peak_pct", 25.0),
        "max_consecutive_failed_adds": get_config_int_safe("smart_add.max_consecutive_failed_adds", 3),
        # 价值平均法（2026-07-17 新增）
        "va_enabled": get_config_bool_safe("smart_add.va_enabled", False),
        "va_target_growth_pct": get_config_float_safe("smart_add.va_target_growth_pct", 0.33),
        "va_max_monthly_mult": get_config_float_safe("smart_add.va_max_monthly_mult", 3.0),
        "va_allow_sell": get_config_bool_safe("smart_add.va_allow_sell", False),
        # 网格交易（2026-07-17 新增）
        "grid_enabled": get_config_bool_safe("smart_add.grid_enabled", False),
        "grid_count": get_config_int_safe("smart_add.grid_count", 5),
        "grid_range_pct": get_config_float_safe("smart_add.grid_range_pct", 20.0),
        # 基本面健康检查（2026-07-17 新增）
        "fund_health_enabled": get_config_bool_safe("smart_add.fund_health_enabled", False),
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
    total_cost: float = 0,
) -> list:
    """计算金字塔补仓档位。

    2026-07-17 重构：
    1. 金额计算从"资金池×释放率"改为"标的市值×加仓比例"
    2. 只预估下次补仓金额（不累加历史已触发档位）

    2026-07-10 基准修正：
    金额计算基准从 current_value 改为 max(total_cost, current_value)
    原因：深套标的当前市值严重缩水，按市值计算的补仓金额过小，永远补不起来

    金字塔补仓法本意：每跌一档补一次，不是一次把所有档都补了。
    当前-31.3%触发-10/-20/-30三档，但下次实际只会补"跌到-40%时"那一档。
    所以"建议金额"=下一档待触发档位的加仓额，而非所有已触发档位累加。

    Args:
        profit_rate: 当前盈亏率（如 -0.35 = -35%）
        pool_total: 资金池总额（组合层硬约束，仅在缩减时使用）
        already_released: 已释放金额
        tiers: 档位配置 [{loss_pct, release_pct, add_ratio}]
        loss_threshold: 触发阈值（如 -10）
        current_value: 标的当前市值
        total_cost: 标的原始投入本金（2026-07-10 新增，用于基准修正）

    Returns:
        档位列表 [{tier, loss_pct, release_amount, triggered, cumulative, add_ratio, is_next}]
        is_next=True 表示这是"下次待触发"的档位（建议金额以此为准）
    """
    result = []
    cumulative = 0
    # 基准修正：用 max(total_cost, current_value) 避免深套标的补仓金额过小
    effective_base = max(total_cost, current_value)
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
        # 金额 = 有效基准 × 加仓比例（%）；无数据时降级回池子×释放率
        add_ratio = tier.get("add_ratio", tier.get("release_pct", 0))
        if effective_base > 0:
            release_amount = round(effective_base * add_ratio / 100, 2)
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
# ── 价值平均法引擎 ──────────────────────────

def _calc_value_averaging(
    holding: dict,
    cfg: dict,
    base_monthly: float,
    months_held: int = 0,
) -> Optional[dict]:
    """价值平均法（Value Averaging）：目标市值驱动的定投策略。

    核心公式：target_value = initial_value + month × target_monthly_growth
    本期投入 = target_value - actual_value

    Args:
        months_held: 持仓月数，用于计算目标市值路径。无法获取时默认0。

    Returns:
        {
            "type": "va",
            "label": "价值平均",
            "triggered": bool,
            "amount": float,       # 正=买入，负=建议卖出
            "target_value": float,
            "actual_value": float,
            "gap_pct": float,      # 偏离目标百分比
            "action": "buy" | "sell" | "hold",
            "reason": str,
            "tag": str,
        }
    """
    if not cfg.get("va_enabled", False):
        return None

    current_value = holding.get("current_value") or 0
    total_cost = holding.get("total_cost") or 0
    if current_value <= 0:
        return None

    # 目标月增长金额 = 总资产 × va_target_growth_pct% / 100
    # 默认 0.33% ≈ 年化4% / 12
    total_assets = holding.get("_total_assets", 0)
    target_monthly_growth = total_assets * cfg.get("va_target_growth_pct", 0.33) / 100

    # 初始市值 = 总成本（首次买入时的投入）
    initial_value = total_cost
    if initial_value <= 0:
        initial_value = current_value

    # 目标市值路径
    target_value = initial_value + months_held * target_monthly_growth
    required = target_value - current_value

    # 单月最大投入/撤出限制
    max_monthly = target_monthly_growth * cfg.get("va_max_monthly_mult", 3.0)
    allow_sell = cfg.get("va_allow_sell", False)

    if required > 0:
        # 需要买入
        amount = min(required, max_monthly)
        action = "buy"
        tag = "市值低于目标"
        reason = f"市值{current_value:,.0f}低于目标{target_value:,.0f}，建议买入{amount:,.0f}"
    elif required < 0 and allow_sell:
        # 允许卖出
        amount = max(required, -max_monthly)
        action = "sell"
        tag = "市值高于目标"
        reason = f"市值{current_value:,.0f}高于目标{target_value:,.0f}，建议卖出{abs(amount):,.0f}"
    elif required < 0:
        # 不允许卖出，但标记为"暂停"
        amount = 0
        action = "hold"
        tag = "市值高于目标"
        reason = f"市值{current_value:,.0f}高于目标{target_value:,.0f}，暂停定投"
    else:
        amount = 0
        action = "hold"
        tag = "市值达标"
        reason = f"市值{current_value:,.0f}接近目标{target_value:,.0f}，维持不变"

    gap_pct = round((current_value - target_value) / target_value * 100, 2) if target_value else 0

    return {
        "type": "va",
        "label": "价值平均",
        "triggered": action != "hold",
        "amount": round(amount, 2),
        "target_value": round(target_value, 2),
        "actual_value": round(current_value, 2),
        "gap_pct": gap_pct,
        "action": action,
        "reason": reason,
        "tag": tag,
        "max_monthly": round(max_monthly, 2),
        "months_held": months_held,
    }


# ── 网格交易策略 ──────────────────────────

def _generate_grid_plan(
    holding: dict,
    valuation: Optional[dict],
    cfg: dict,
) -> Optional[dict]:
    """网格交易策略：适用于估值合理区间（30-70%分位）的标的。

    原理：设定价格区间 [P_low, P_high]，划分为 N 格，每格预设买卖金额。
    当前价在网格中的位置 → 建议操作。

    触发条件：
    - 估值分位 30-70%（合理区间）
    - 非深度亏损（亏损 > -15% 不适合网格，应优先金字塔）
    - 非债券基金

    Returns:
        {
            "type": "grid",
            "label": "网格交易",
            "triggered": bool,
            "action": "buy" | "sell" | "wait",
            "current_grid": int,     # 当前所在格位（0=底格，N=顶格）
            "total_grids": int,
            "grid_price": float,     # 当前格对应的价格
            "next_grid_price": float, # 下一格价格
            "suggested_amount": float,
            "reason": str,
            "grid_levels": [...],    # 所有网格档位详情
        }
    """
    if not cfg.get("grid_enabled", False):
        return None

    profit_rate = holding.get("profit_rate") or 0
    current_price = holding.get("current_price") or 0
    current_value = holding.get("current_value") or 0
    if current_price <= 0 or current_value <= 0:
        return None

    # 触发条件：估值分位 30-70%（数据缺失时不生成网格）
    val_pct = valuation.get("percentile") if valuation else None
    if val_pct is None or val_pct < 30 or val_pct > 70:
        # 估值不在合理区间或数据缺失，不适合网格
        return None

    # 触发条件：非深度亏损
    if profit_rate < -0.15:
        return None

    grid_count = cfg.get("grid_count", 5)
    grid_range_pct = cfg.get("grid_range_pct", 20.0)

    # 网格区间：当前价 ± range_pct%
    price_low = current_price * (1 - grid_range_pct / 100)
    price_high = current_price * (1 + grid_range_pct / 100)
    grid_step = (price_high - price_low) / grid_count

    # 每格仓位 = 当前市值 / grid_count
    grid_amount = current_value / grid_count

    # 构建网格档位
    grid_levels = []
    current_grid = -1
    for i in range(grid_count + 1):
        level_price = round(price_low + i * grid_step, 4)
        level = {
            "grid_index": i,
            "price": level_price,
            "action": "buy" if i < grid_count // 2 else ("sell" if i > grid_count // 2 else "wait"),
            "amount": round(grid_amount, 2),
        }
        grid_levels.append(level)
        # 找到当前价所在的网格
        if current_grid < 0 and current_price >= level_price:
            current_grid = i

    if current_grid < 0:
        current_grid = 0  # 当前价低于最低格

    # 当前格操作
    mid_grid = grid_count // 2
    if current_grid < mid_grid:
        action = "buy"
        suggested = grid_amount
        tag = "网格低位"
        reason = f"当前价在第{current_grid}格（共{grid_count}格），处于低位，建议买入"
    elif current_grid > mid_grid:
        action = "sell"
        suggested = grid_amount
        tag = "网格高位"
        reason = f"当前价在第{current_grid}格（共{grid_count}格），处于高位，建议卖出"
    else:
        action = "wait"
        suggested = 0
        tag = "网格中位"
        reason = f"当前价在第{current_grid}格（共{grid_count}格），处于中位，等待触发"

    next_grid_price = None
    if current_grid < grid_count:
        next_grid_price = grid_levels[current_grid + 1]["price"]

    return {
        "type": "grid",
        "label": "网格交易",
        "triggered": action != "wait",
        "action": action,
        "current_grid": current_grid,
        "total_grids": grid_count,
        "price_low": round(price_low, 4),
        "price_high": round(price_high, 4),
        "grid_step": round(grid_step, 4),
        "grid_price": grid_levels[current_grid]["price"],
        "next_grid_price": next_grid_price,
        "suggested_amount": round(suggested, 2),
        "reason": reason,
        "tag": tag,
        "grid_levels": grid_levels,
    }


# ── 基本面健康检查 ──────────────────────────

def _check_fund_health(
    fund_code: str,
    cfg: dict,
    holding: dict | None = None,
) -> dict:
    """检查基金基本面是否健康。

    数据源：
    - 基金经理/规模：services.fund.fund_manager.get_fund_manager（akshare 雪球接口）
    - 费率：services.fund.fund_analysis._fetch_fund_fee（akshare 费率接口）
    - 经理变更：对比 portfolio_holdings.manager_name 与 akshare 最新经理名

    Returns:
        {
            "healthy": bool,
            "warnings": [...],
            "risks": [...],
            "data_available": bool,
        }
    """
    if not cfg.get("fund_health_enabled", False):
        return {"healthy": True, "warnings": [], "risks": [], "data_available": False}

    warnings: list[str] = []
    risks: list[str] = []
    data_available = False

    # 1. 基金经理 + 规模（akshare 雪球接口）
    try:
        from services.fund.fund_manager import get_fund_manager, check_manager_change

        mgr_info = get_fund_manager(fund_code)
        if mgr_info:
            data_available = True

            # 经理变更检测：对比持仓表存储的经理名
            stored_manager = (holding or {}).get("manager_name", "")
            if stored_manager and mgr_info.get("manager_name"):
                change = check_manager_change(fund_code, stored_manager)
                if change:
                    risks.append(
                        f"基金经理变更：{change['old_manager']}→{change['new_manager']}，需关注风格变化"
                    )

            # 规模检查（scale 字段为字符串，如"95.44亿"）
            scale_str = mgr_info.get("scale", "")
            if scale_str:
                try:
                    # 提取数值部分
                    import re as _re

                    num_match = _re.search(r"[\d.]+", scale_str)
                    if num_match:
                        size = float(num_match.group())
                        if "亿" in scale_str and size > 100:
                            warnings.append(f"基金规模{size:.1f}亿较大，可能影响灵活性")
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.debug(f"[smart_add] fund_health 经理信息获取失败 {fund_code}: {e}")

    # 2. 费率检查（akshare 费率接口，返回管理费+托管费综合）
    try:
        from services.fund.fund_analysis import _fetch_fund_fee

        total_fee = _fetch_fund_fee(fund_code)
        if total_fee > 0:
            data_available = True
            if total_fee > 1.5:
                warnings.append(f"综合费率{total_fee:.2f}%偏高，长期持有成本较高")
    except Exception as e:
        logger.debug(f"[smart_add] fund_health 费率获取失败 {fund_code}: {e}")

    healthy = len(risks) == 0
    return {
        "healthy": healthy,
        "warnings": warnings,
        "risks": risks,
        "data_available": data_available,
    }


# ── 退出信号检测 ──────────────────────────

def _detect_exit_signals(
    holding: dict,
    valuation: Optional[dict],
    cfg: dict,
    total_assets: float,
    fund_type: str,
) -> list:
    """检测退出信号（止盈/止损/暂停）。

    Returns:
        list of exit signals, 每个信号:
        {
            "type": "take_profit" | "stop_loss" | "pause",
            "label": str,
            "triggered": bool,
            "reason": str,
            "suggested_action": str,
            "severity": "info" | "warning" | "danger",
        }
    """
    if not cfg.get("exit_signal_enabled", False):
        return []

    exit_signals = []
    profit_rate = holding.get("profit_rate") or 0
    profit_rate_pct = profit_rate * 100
    current_value = holding.get("current_value") or 0

    # 信号D：止盈退出
    if profit_rate_pct > 0:
        # 不同基金类型不同止盈阈值
        if fund_type in ("broad",):
            take_profit_threshold = cfg.get("take_profit_broad_pct", 20)
        else:
            take_profit_threshold = cfg.get("take_profit_theme_pct", 30)

        if profit_rate_pct >= take_profit_threshold * 1.5:
            exit_signals.append({
                "type": "take_profit",
                "label": "止盈清仓",
                "triggered": True,
                "reason": f"盈利{profit_rate_pct:.1f}%远超止盈线{take_profit_threshold}%",
                "suggested_action": "建议清仓锁定利润",
                "severity": "warning",
            })
        elif profit_rate_pct >= take_profit_threshold:
            exit_signals.append({
                "type": "take_profit",
                "label": "止盈减仓",
                "triggered": True,
                "reason": f"盈利{profit_rate_pct:.1f}%已达止盈线{take_profit_threshold}%",
                "suggested_action": "建议减仓50%",
                "severity": "info",
            })

        # 估值过高时额外警示
        if valuation and (valuation.get("percentile") or 0) > 80:
            exit_signals.append({
                "type": "take_profit",
                "label": "高估减仓",
                "triggered": True,
                "reason": f"估值分位{valuation['percentile']:.0f}%>80%，建议减仓",
                "suggested_action": "建议减仓（无论盈亏）",
                "severity": "danger",
            })

    # 信号E：止损退出
    stop_loss_pct = cfg.get("stop_loss_pct", -30)
    if profit_rate_pct <= stop_loss_pct:
        val_pct = valuation.get("percentile") if valuation else None
        stop_loss_val_pct = cfg.get("stop_loss_valuation_pct", 50)
        # 只在估值非低估时建议止损（低估时应该继续持有/补仓而非止损）
        # 估值数据缺失时不触发止损（避免数据缺失标的被错误止损）
        if val_pct is not None and val_pct > stop_loss_val_pct:
            exit_signals.append({
                "type": "stop_loss",
                "label": "止损",
                "triggered": True,
                "reason": f"亏损{profit_rate_pct:.1f}%超过止损线{stop_loss_pct}%，估值分位{val_pct:.0f}%非低估",
                "suggested_action": "建议止损50%",
                "severity": "danger",
            })

    # 信号F：暂停观望
    # 估值过高暂停
    if valuation and (valuation.get("percentile") or 0) > 70:
        exit_signals.append({
            "type": "pause",
            "label": "估值过高",
            "triggered": True,
            "reason": f"估值分位{valuation['percentile']:.0f}%>70%，暂停补仓",
            "suggested_action": "暂停金字塔和定投，等待估值回落",
            "severity": "warning",
        })

    return exit_signals


# ── 组合再平衡视角 ──────────────────────────

def _calc_rebalance_suggestion(holdings: list[dict], total_assets: float) -> list[dict]:
    """计算组合再平衡建议。

    原理：对比每标的当前仓位与目标仓位（等权或按配置），偏离>5%标记为需再平衡。

    Returns:
        list of rebalance items, 每个:
        {
            "fund_code": str,
            "fund_name": str,
            "current_pct": float,
            "target_pct": float,
            "deviation": float,      # 正=超配，负=低配
            "action": "reduce" | "add" | "hold",
            "suggested_amount": float,  # 建议调整金额
        }
    """
    if not holdings or total_assets <= 0:
        return []

    n = len(holdings)
    if n == 0:
        return []

    # 等权目标配置
    target_pct = 100 / n

    rebalance_items = []
    for h in holdings:
        current_value = h.get("current_value") or 0
        current_pct = round(current_value / total_assets * 100, 2) if total_assets else 0
        deviation = round(current_pct - target_pct, 2)
        target_value = round(total_assets * target_pct / 100, 2)

        if abs(deviation) < 5:
            action = "hold"
            suggested_amount = 0
        elif deviation > 0:
            action = "reduce"
            suggested_amount = round(current_value - target_value, 2)
        else:
            action = "add"
            suggested_amount = round(target_value - current_value, 2)

        rebalance_items.append({
            "fund_code": h.get("fund_code", ""),
            "fund_name": h.get("fund_name", ""),
            "current_pct": current_pct,
            "target_pct": round(target_pct, 2),
            "deviation": deviation,
            "action": action,
            "suggested_amount": suggested_amount,
        })

    return rebalance_items


# ── 策略对比模拟器 ──────────────────────────

def simulate_strategies(
    fund_code: str,
    monthly_drop_pct: float,
    months: int = 6,
    user_id: str = "default",
) -> dict:
    """对比不同补仓策略在持续下跌场景下的效果。

    策略对比：
    1. 不补仓（躺平）
    2. 等额定投（DCA）
    3. 金字塔补仓
    4. 价值平均法（VA）

    Args:
        fund_code: 基金代码
        monthly_drop_pct: 每月下跌百分比（如 -5 表示每月跌5%）
        months: 模拟月数（默认6个月）
        user_id: 用户ID

    Returns:
        {
            "fund_code": str,
            "fund_name": str,
            "scenario": {...},
            "strategies": [
                {"name": "不补仓", "final_cost": float, "final_value": float, "profit_rate": float, "total_invested": float},
                ...
            ],
            "best_strategy": "xxx",
        }
    """
    from db.portfolio import get_holding_by_fund, get_portfolio_summary
    from db.config import get_config_float, get_config

    holding = get_holding_by_fund(fund_code, user_id=user_id)
    if not holding:
        return {"error": f"未找到持仓 {fund_code}"}

    current_price = holding.get("current_price") or 0
    shares = holding.get("shares") or 0
    total_cost = holding.get("total_cost") or 0

    # 获取总资产
    summary = get_portfolio_summary(user_id=user_id)
    total_assets = summary.get("total_assets", 0) if summary else 0

    if not current_price or not shares:
        return {"error": "持仓数据不完整"}

    # 场景参数
    base_monthly = total_assets * get_config_float("smart_add.base_dca_pct", 4) / 100 / 12
    tiers_str = get_config("smart_add.pyramid_tiers", "10:15:5,20:25:10,30:30:15,40:20:20,50:10:25")
    tiers = []
    for t in tiers_str.split(","):
        parts = t.strip().split(":")
        if len(parts) == 3:
            tiers.append((float(parts[0]), float(parts[1]), float(parts[2])))

    # 模拟价格路径
    prices = [current_price]
    for i in range(1, months + 1):
        prices.append(round(prices[-1] * (1 + monthly_drop_pct / 100), 4))

    strategies = []

    # 策略1：不补仓
    final_price = prices[-1]
    final_value = shares * final_price
    strategies.append({
        "name": "不补仓",
        "icon": "minus-circle",
        "total_invested": total_cost,
        "final_shares": round(shares, 2),
        "final_cost": round(total_cost / shares, 4) if shares else 0,
        "final_value": round(final_value, 2),
        "profit_rate": round((final_value - total_cost) / total_cost * 100, 2) if total_cost else 0,
        "description": "不操作，观望",
    })

    # 策略2：等额定投（每月固定金额）
    dca_shares = shares
    dca_invested = total_cost
    for i in range(1, months + 1):
        buy_price = prices[i]
        dca_shares += base_monthly / buy_price
        dca_invested += base_monthly
    dca_value = dca_shares * final_price
    dca_cost = dca_invested / dca_shares if dca_shares else 0
    strategies.append({
        "name": "等额定投",
        "icon": "repeat",
        "total_invested": round(dca_invested, 2),
        "final_shares": round(dca_shares, 2),
        "final_cost": round(dca_cost, 4),
        "final_value": round(dca_value, 2),
        "profit_rate": round((dca_value - dca_invested) / dca_invested * 100, 2) if dca_invested else 0,
        "description": f"每月固定投入¥{base_monthly:,.0f}",
    })

    # 策略3：金字塔补仓
    pyr_shares = shares
    pyr_invested = total_cost
    last_buy_price = current_price
    for i in range(1, months + 1):
        buy_price = prices[i]
        loss_pct = (buy_price - last_buy_price) / last_buy_price * 100
        # 检查是否触发金字塔档位
        triggered = False
        for loss_threshold, release_pct, add_ratio in tiers:
            if loss_pct <= -loss_threshold:
                # 触发该档位
                position_value = pyr_shares * buy_price
                add_amount = position_value * add_ratio / 100
                pyr_shares += add_amount / buy_price
                pyr_invested += add_amount
                triggered = True
                break
        if not triggered:
            # 未触发金字塔，按等额定投
            pyr_shares += base_monthly / buy_price
            pyr_invested += base_monthly
    pyr_value = pyr_shares * final_price
    pyr_cost = pyr_invested / pyr_shares if pyr_shares else 0
    strategies.append({
        "name": "金字塔补仓",
        "icon": "layers",
        "total_invested": round(pyr_invested, 2),
        "final_shares": round(pyr_shares, 2),
        "final_cost": round(pyr_cost, 4),
        "final_value": round(pyr_value, 2),
        "profit_rate": round((pyr_value - pyr_invested) / pyr_invested * 100, 2) if pyr_invested else 0,
        "description": f"按档位{tiers[0][0]:.0f}%起触发的金字塔补仓",
    })

    # 策略4：价值平均法
    va_shares = shares
    va_invested = total_cost
    target_monthly = total_assets * get_config_float("smart_add.va_target_growth_pct", 0.33) / 100
    target_value = total_cost  # 初始目标市值 = 总成本
    for i in range(1, months + 1):
        buy_price = prices[i]
        target_value += target_monthly
        actual_value = va_shares * buy_price
        required = target_value - actual_value
        if required > 0:
            max_monthly = target_monthly * get_config_float("smart_add.va_max_monthly_mult", 3.0)
            invest = min(required, max_monthly)
            va_shares += invest / buy_price
            va_invested += invest
    va_value = va_shares * final_price
    va_cost = va_invested / va_shares if va_shares else 0
    strategies.append({
        "name": "价值平均法",
        "icon": "trending-up",
        "total_invested": round(va_invested, 2),
        "final_shares": round(va_shares, 2),
        "final_cost": round(va_cost, 4),
        "final_value": round(va_value, 2),
        "profit_rate": round((va_value - va_invested) / va_invested * 100, 2) if va_invested else 0,
        "description": f"市值驱动，目标月增长¥{target_monthly:,.0f}",
    })

    # 找最佳策略（最高最终价值）
    best = max(strategies, key=lambda s: s["final_value"])
    best["is_best"] = True

    return {
        "fund_code": fund_code,
        "fund_name": holding.get("fund_name", ""),
        "scenario": {
            "current_price": current_price,
            "monthly_drop_pct": monthly_drop_pct,
            "months": months,
            "price_path": prices,
        },
        "strategies": strategies,
        "best_strategy": best["name"],
    }


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
        plan = _generate_single_plan(h, cfg, total_assets, pool_total, base_monthly, holdings=holdings)
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
        # 全局退出信号：资金池耗尽
        "pool_exit_signals": [
            {
                "type": "pause",
                "label": "资金耗尽",
                "triggered": True,
                "reason": "补仓资金池已耗尽",
                "suggested_action": "暂停所有补仓，等待持仓修复或追加资金",
                "severity": "danger",
            }
        ] if pool_remaining <= 0 and cfg.get("exit_signal_enabled", False) else [],
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
        # 组合再平衡建议（2026-07-17 新增）
        "rebalance_suggestions": _calc_rebalance_suggestion(holdings, total_assets),
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

    # ── S-1（2026-07-22）：计划持久化到 smart_add_plans 表 ──
    # 用途：历史回溯、计划vs实际对比、所有信号的反事实验证
    try:
        persist_enabled = get_config_bool("smart_add.persist_plans_enabled", True)
    except Exception:
        persist_enabled = True
    if persist_enabled:
        today = datetime.now().strftime("%Y-%m-%d")
        for p in plans:
            try:
                val = p.get("valuation") or {}
                saf = p.get("safety") or {}
                # 提取触发的信号类型标签
                triggered = [
                    {"type": s.get("type", ""), "label": s.get("label", ""),
                     "amount": s.get("amount", 0), "triggered": s.get("triggered", False)}
                    for s in (p.get("triggered_signals") or [])
                    if s.get("triggered")
                ]
                exit_sigs = [
                    {"type": s.get("type", ""), "label": s.get("label", ""),
                     "severity": s.get("severity", "")}
                    for s in (p.get("exit_signals") or [])
                    if s.get("triggered")
                ]
                save_smart_add_plan(
                    user_id=user_id,
                    fund_code=p["fund_code"],
                    fund_name=p.get("fund_name", ""),
                    snapshot_date=today,
                    triggered_signals=triggered,
                    exit_signals=exit_sigs,
                    total_suggested=p.get("total_suggested", 0),
                    final_suggested_amount=p.get("final_suggested_amount", 0),
                    safety_status="can_add" if saf.get("can_add", True) else "blocked",
                    valuation_percentile=val.get("percentile"),
                    profit_rate_pct=p.get("profit_rate_pct"),
                    position_pct=p.get("position_pct"),
                    plan_detail=p,  # 完整 plan 对象
                )
            except Exception as e:
                logger.debug(f"[smart_add] 计划持久化失败 {p.get('fund_code')}: {e}")

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

def _check_cooldown(fund_code: str, cfg: dict) -> tuple[bool, int, float, str]:
    """冷却期检查：近 cooldown_days 内同基金买入次数是否超限，并返回已补金额。

    S-2（2026-07-22）修复：SQL 增加 `AND (is_hypothetical=0 OR is_hypothetical IS NULL)`
    排除假设交易，避免假设交易污染冷却期计数导致真实补仓被误拦截。

    Returns:
        (can_proceed, recent_buy_count, recent_buy_amount, reason)
        - can_proceed=True: 可继续补仓（但建议金额应减去 recent_buy_amount）
        - can_proceed=False: 已达冷却期上限，应拦截
        - recent_buy_amount: 冷却期内已补仓总金额（供信号减扣，避免重复补仓）
    """
    try:
        from db._conn import _get_conn
        cooldown_days = cfg.get("cooldown_days", 10)
        max_buys = cfg.get("max_buys_in_cooldown", 2)
        cutoff = (datetime.now() - timedelta(days=cooldown_days)).strftime("%Y-%m-%d")
        conn = _get_conn()
        try:
            # S-2: 排除 is_hypothetical=1 的假设交易（仅统计真实交易）
            row = conn.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(amount),0) as total_amount FROM portfolio_transactions "
                "WHERE fund_code=? AND transaction_type='buy' "
                "AND transaction_date >= ? AND status IN ('confirmed','pending','submitted') "
                "AND (is_hypothetical=0 OR is_hypothetical IS NULL)",
                (fund_code, cutoff),
            ).fetchone()
            count = row["cnt"] if row else 0
            recent_amount = float(row["total_amount"]) if row and row["total_amount"] else 0.0
        finally:
            conn.close()
        if count >= max_buys:
            return False, count, recent_amount, f"冷却期内已补仓{count}次¥{recent_amount:,.0f}（{cooldown_days}天内上限{max_buys}次）"
        return True, count, recent_amount, ""
    except Exception as e:
        logger.debug(f"[smart_add] 冷却期检查失败 {fund_code}: {e}")
        return True, 0, 0.0, ""  # 失败时放行


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

    # 冷却期检查（返回已补金额，供减扣）
    can_proceed, recent_count, recent_buy_amount, block_reason = _check_cooldown(fund_code, cfg)
    if not can_proceed:
        return {
            "type": "trend",
            "label": "趋势加仓",
            "triggered": False,
            "blocked_reason": block_reason,
            "conditions_met": [
                c for c, v in zip(
                    [f"估值合理({val_pct:.0f}%分位)" if val_pct is not None else "估值合理",
                     f"近{lookback}日涨{gain_20d}%" if gain_20d is not None else "近N日涨",
                     f"近5日涨{gain_5d}%" if gain_5d is not None else "短趋势确认"],
                    [cond1, cond2, cond3]
                ) if v
            ],
        }

    # 建议金额动态化：有效基准 × 趋势加仓比例 × 趋势强度系数 × 估值系数 × 仓位余量系数
    # 2026-07-17 改进：基数从全局 base_monthly 改为"标的市值×趋势加仓比例"
    #   原因：全局2760对小仓位基金(¥7766)占比过大，对大仓位基金(¥51534)占比过小
    # 2026-07-10 基准修正：current_value → max(total_cost, current_value)
    #   原因：深套标的市值缩水，按市值计算的加仓金额过小
    # 趋势加仓比例默认5%（小仓位试探），可通过 trend_base_ratio 配置
    current_value = holding.get("current_value") or 0
    total_cost = holding.get("total_cost") or 0
    effective_base = max(total_cost, current_value)
    trend_base_ratio = cfg.get("trend_base_ratio", 5.0)  # 有效基准的5%作为趋势加仓基数
    base_amount = effective_base * trend_base_ratio / 100

    # 1. 趋势强度系数：涨3%→×1.0，涨5%→×1.3，涨8%→×1.5（线性插值，上限1.5）
    trend_strength_mult = 1.0
    if gain_20d is not None:
        if gain_20d >= 8:
            trend_strength_mult = 1.5
        elif gain_20d >= 5:
            trend_strength_mult = 1.0 + (gain_20d - 5) * 0.1
        elif gain_20d >= 3:
            trend_strength_mult = 1.0
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
    # 2026-07-10 基准修正：仓位计算用 effective_base 避免深套误判低仓位
    position_cap_pct = cfg.get("trend_position_pct", 5)
    total_assets = holding.get("_total_assets", 0)
    current_position_pct = (effective_base / total_assets * 100) if total_assets else 0
    room_mult = max(0.3, (position_cap_pct - current_position_pct) / position_cap_pct) if position_cap_pct > 0 else 1.0

    amount = round(base_amount * trend_strength_mult * valuation_mult * room_mult, 2)
    # 仓位上限硬约束：5% 总资产
    if total_assets:
        cap_amount = round(total_assets * position_cap_pct / 100, 2)
        if amount > cap_amount:
            amount = cap_amount

    # 减去冷却期内已补金额（避免重复补仓）
    deducted_amount = 0
    if recent_buy_amount > 0:
        deducted_amount = min(amount, recent_buy_amount)
        amount = max(0, round(amount - recent_buy_amount, 2))

    reasons = []
    if cond1:
        reasons.append(f"估值合理({val_pct:.0f}%分位)")
    if cond2:
        reasons.append(f"近{lookback}日涨{gain_20d}%")
    if cond3:
        reasons.append(f"近5日涨{gain_5d}%" if gain_5d is not None else "短趋势确认")

    formula_base = round(base_amount, 2)
    total_assets = holding.get("_total_assets", 0)
    max_loss = round(amount * 0.05, 2)
    max_loss_pct = round(amount * 0.05 / total_assets * 100, 2) if total_assets else 0
    return {
        "type": "trend",
        "label": "趋势加仓",
        "triggered": True,
        "amount": amount,
        "reason": "近期涨势好，轻仓试探（短期波段，严格止损-5%）",
        "conditions_met": reasons,
        "position_cap_pct": position_cap_pct,
        "tag": "短期波段",
        # S-3（2026-07-22）：信号B风险提示增强（保持默认开启+强提示）
        # 用户决策：trend_signal_enabled 保持 true，但触发时增加醒目风险提示
        "risk_note": "⚠️ 此为趋势追涨信号，与价值投资「低买」理念不同，请谨慎评估。"
                     "严格止损-5%，仓位≤5%，持有≤30天。"
                     "若您为保守型投资者，建议关闭 trend_signal_enabled 开关。",
        "risk_level": "warning",  # 新增：风险级别 warning/danger
        "max_loss_amount": max_loss,
        "max_loss_pct_of_portfolio": max_loss_pct,
        "recent_buy_amount": round(recent_buy_amount, 2),
        "deducted_amount": round(deducted_amount, 2),
        "amount_formula": {
            "base_amount": formula_base,
            "trend_strength_mult": round(trend_strength_mult, 2),
            "valuation_mult": valuation_mult,
            "room_mult": round(room_mult, 2),
            "current_position_pct": round(current_position_pct, 2),
            "recent_buy_deducted": round(deducted_amount, 2),
            "formula": f"{formula_base} × {round(trend_strength_mult,2)} × {valuation_mult} × {round(room_mult,2)} - {round(deducted_amount,2)}(已补) = {amount}",
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
    can_proceed, recent_count, recent_buy_amount, block_reason = _check_cooldown(fund_code, cfg)
    if not can_proceed and drop_pct < 8:
        return {
            "type": "dip",
            "label": "大跌定投",
            "triggered": False,
            "blocked_reason": block_reason,
            "drop_pct": round(drop_pct, 2),
            "tier": f"-{matched_tier[0]}%",
        }

    # 建议金额动态化：有效基准 × 大跌定投比例 × 跌幅系数 × 亏损系数 × 仓位余量系数
    # 2026-07-17 改进：基数从全局 base_monthly 改为"标的市值×大跌定投比例"
    # 2026-07-10 基准修正：current_value → max(total_cost, current_value)
    # 大跌定投比例默认8%（比趋势加仓5%略大，因跌幅已确认）
    dip_base_ratio = cfg.get("dip_base_ratio", 8.0)
    current_value = holding.get("current_value") or 0
    total_cost = holding.get("total_cost") or 0
    effective_base = max(total_cost, current_value)
    base_amount = effective_base * dip_base_ratio / 100

    # 1. 跌幅系数：使用档位倍数（4%→×1.0, 8%→×1.5, 12%→×2.0）
    drop_mult = matched_tier[1]

    # 2. 亏损系数：亏损0-10%→×1.0，亏损10-20%→×1.2，亏损>20%→×1.3
    profit_rate = holding.get("profit_rate") or 0
    loss_mult = 1.0
    if profit_rate < -0.20:
        loss_mult = 1.3
    elif profit_rate < -0.10:
        loss_mult = 1.2

    # 3. 仓位余量系数：(25%上限-当前仓位)/25%，仓位越低补越多，最低0.3
    # 2026-07-10 基准修正：仓位计算用 effective_base 避免深套误判低仓位
    max_pos_pct = cfg.get("max_single_position_pct", 25.0)
    total_assets = holding.get("_total_assets", 0)
    current_position_pct = (effective_base / total_assets * 100) if total_assets else 0
    room_mult = max(0.3, (max_pos_pct - current_position_pct) / max_pos_pct) if max_pos_pct > 0 else 1.0

    amount = round(base_amount * drop_mult * loss_mult * room_mult, 2)

    # 减去冷却期内已补金额（避免重复补仓）
    deducted_amount = 0
    if recent_buy_amount > 0:
        deducted_amount = min(amount, recent_buy_amount)
        amount = max(0, round(amount - recent_buy_amount, 2))

    formula_base = round(base_amount, 2)
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
        "reason": f"较上次买入跌{drop_pct:.1f}%，分批定投（档位-{matched_tier[0]}%，×{matched_tier[1]}）",
        "tag": "分批定投",
        "recent_buy_amount": round(recent_buy_amount, 2),
        "deducted_amount": round(deducted_amount, 2),
        "amount_formula": {
            "base_amount": formula_base,
            "drop_mult": drop_mult,
            "loss_mult": loss_mult,
            "room_mult": round(room_mult, 2),
            "current_position_pct": round(current_position_pct, 2),
            "profit_rate_pct": round(profit_rate * 100, 2),
            "recent_buy_deducted": round(deducted_amount, 2),
            "formula": f"{formula_base} × {drop_mult} × {loss_mult} × {round(room_mult,2)} - {round(deducted_amount,2)}(已补) = {amount}",
        },
    }


def _generate_single_plan(
    holding: dict,
    cfg: dict,
    total_assets: float,
    pool_total: float,
    base_monthly: float,
    deep_loss_count: int = 0,
    holdings: list = None,
) -> Optional[dict]:
    """生成单个持仓的补仓计划。

    Args:
        deep_loss_count: 全局深套标的数，用于计算资金池平均配额预警。
            主流程在汇总所有计划后回填该值，首次生成时为 0（不触发预警）。
        holdings: 全部持仓列表，用于维度2穿透集中度计算（2026-07-10 新增）
    """
    fund_code = holding.get("fund_code", "")
    fund_name = holding.get("fund_name", "")
    index_code = holding.get("index_code", "")
    # P2-1: profit_rate/current_price 为 None 时标记数据缺失，不按 0 处理
    _profit_rate_raw = holding.get("profit_rate")
    profit_rate = _profit_rate_raw if _profit_rate_raw is not None else 0
    _price_stale = holding.get("current_price") is None  # 净值为空标记数据过期
    # P3-5: 检查 price_updated_at 是否过期（超过3天标记数据陈旧）
    if not _price_stale:
        price_updated = holding.get("price_updated_at", "")
        if price_updated:
            try:
                from datetime import datetime as _dt
                update_dt = _dt.fromisoformat(price_updated)
                if (_dt.now() - update_dt).days > 3:
                    _price_stale = True
            except Exception:
                pass
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
        # P2-3: 单标的查询启用在线兜底（非批量场景），避免本地无估值时 valuation=None
        val = get_best_valuation(index_code, metric_type="市盈率", query_source="smart_add", enable_online=True)
        if not val:
            val = get_best_valuation(index_code, metric_type="市净率", query_source="smart_add", enable_online=True)
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
    # 凯利数据不足时告警（data_source 为 default/error 时为默认 25%）
    kelly_warning = ""
    if kelly.get("data_source") in ("default", "error", "insufficient"):
        kelly_warning = f"历史数据不足（{kelly.get('data_source', 'default')}），仓位上限为默认值{max_position_pct:.0f}%"

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
            current_value=current_value,
            total_cost=total_cost,  # 2026-07-10 基准修正
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
        # 2026-07-10 基准修正：小仓位上限用 effective_base 避免深套误判
        effective_base_for_cap = max(total_cost, current_value)
        max_add_amount = effective_base_for_cap * cfg["max_add_vs_position_mult"]
        if engine2["released_amount"] > max_add_amount and max_add_amount > 0:
            engine2["scaled_from_position_cap"] = engine2["released_amount"]
            engine2["released_amount"] = round(max_add_amount, 2)
            engine2["capped_reason"] = f"受有效基准{cfg['max_add_vs_position_mult']}倍上限约束"

    # 持仓占比 + 安全阀（用 L3 凯利上限替代原 25%）
    # 2026-07-10 基准修正：仓位计算用 effective_base 避免深套误判低仓位
    effective_base_for_pos = max(total_cost, current_value)
    position_pct = round(effective_base_for_pos / total_assets * 100, 2) if total_assets else 0
    can_add = position_pct < max_position_pct

    # 修复3：安全阀拦截
    # 原逻辑 can_add=False 时仅设标志位，released_amount 照常计算并落库假设交易
    # 新逻辑：安全阀未通过时，金字塔释放额归零，标记拦截原因
    if engine2 and not can_add:
        engine2["blocked_reason"] = f"已达仓位上限 {max_position_pct:.1f}%，暂停补仓"
        engine2["released_amount"] = 0

    # ── 多维度触发器：信号 A 金字塔(已有) + 信号 B 趋势 + 信号 C 大跌定投 + 信号 D 价值平均 + 信号 E 网格 ──
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

    # 信号 C：大跌定投（连续大跌4%）— 始终计算，与信号A同时触发时仅保留金字塔
    signal_c = _detect_dip_signal(holding, valuation, cfg, base_monthly)
    if engine2 and engine2.get("released_amount", 0) > 0 and signal_c:
        signal_c = None
    if signal_c:
        triggered_signals.append(signal_c)

    # 信号 D：价值平均法（VA）— 市值驱动，替代/补充 DCA
    signal_d = None
    if cfg.get("va_enabled", False):
        # 估算持仓月数（从首次买入至今）
        months_held = 0
        buy_date = holding.get("last_buy_date") or holding.get("buy_date") or ""
        if buy_date:
            try:
                from datetime import datetime
                delta = datetime.now() - datetime.strptime(buy_date[:10], "%Y-%m-%d")
                months_held = max(0, delta.days // 30)
            except Exception:
                pass
        signal_d = _calc_value_averaging(holding, cfg, base_monthly, months_held)
        if signal_d and signal_d.get("triggered") and signal_d.get("action") == "buy":
            triggered_signals.append(signal_d)

    # 信号 E：网格交易 — 估值合理区间低买高卖
    signal_e = _generate_grid_plan(holding, valuation, cfg)
    if signal_e and signal_e.get("triggered"):
        triggered_signals.append(signal_e)

    # 基本面健康检查
    fund_health = _check_fund_health(fund_code, cfg, holding=holding)

    # 总建议金额（所有命中信号汇总，受安全阀约束）
    total_suggested = sum(
        s.get("amount", 0) for s in triggered_signals if s.get("triggered")
    )
    if not can_add:
        total_suggested = 0

    # 退出信号检测（止盈/止损/暂停，需开关开启）
    exit_signals = _detect_exit_signals(holding, valuation, cfg, total_assets, fund_type)

    # ── 多维度仓位决策层（2026-07-10 新增）──
    # 维度1目标仓位 + 维度2穿透 + 维度3首次仓 + 维度4弹性 + 维度5增强减仓 + 维度6资金约束
    position_sizing = None
    target_driven_monthly = 0.0
    final_suggested_amount = total_suggested
    try:
        position_sizing = generate_position_sizing_plan(
            holding=holding,
            cfg=cfg,
            total_assets=total_assets,
            all_holdings=holdings or [holding],
            kelly=kelly,
            valuation=valuation,
            fund_type=fund_type,
            type_strategy=type_strategy,
            user_id="default",
        )
        target_driven_monthly = position_sizing.get("target_driven_monthly", 0.0)

        # 最终金额 = max(目标驱动, 信号触发) × 多维约束
        # 维度5减仓信号优先：触发减仓时加仓归零
        enhanced_exits = position_sizing.get("exit_signals", [])
        has_enhanced_exit = any(s.get("triggered") for s in enhanced_exits)
        if has_enhanced_exit:
            final_suggested_amount = 0
            # 合并增强减仓信号到 exit_signals
            exit_signals = (exit_signals or []) + enhanced_exits
        else:
            # 取目标驱动与信号触发的较大值
            base_amount = max(target_driven_monthly, total_suggested)
            # 多维约束：安全阀 + 资金约束
            cash_room = (position_sizing.get("cash_constraint") or {}).get("total_available_3m", 0)
            # 资金约束为组合层3个月总额，单标的月度建议不超过资金池的合理份额
            # 此处保守约束：单标的月度 ≤ 资金3月可用 / 深套标的数（避免单标的吃掉全部资金）
            # 仅在目标驱动 > 信号触发时启用（信号触发由各自引擎已约束）
            if target_driven_monthly > total_suggested and cash_room > 0:
                # 资金约束软上限：单标的月度不超过可用资金的 1/3（保守）
                cash_cap = cash_room / 3
                if base_amount > cash_cap:
                    base_amount = cash_cap
                    position_sizing["cash_warning"] = (
                        f"资金约束：月度建议上限¥{cash_cap:,.0f}（3月可用¥{cash_room:,.0f}/3）"
                    )
            final_suggested_amount = round(base_amount, 2)

        # 安全阀未通过时归零
        if not can_add:
            final_suggested_amount = 0
    except Exception as e:
        logger.debug(f"[smart_add] 多维度仓位决策失败 {fund_code}: {e}")
        position_sizing = None

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
        "effective_base": max(total_cost, current_value),  # 2026-07-10 新增：统一基准
        "profit_rate": round(profit_rate, 4),
        "profit_rate_pct": round(profit_rate * 100, 2),
        "position_pct": position_pct,
        "valuation": valuation,
        "engine1": engine1,
        "pyramid": engine2,
        "triggered_signals": triggered_signals,  # 多维度触发器命中的信号列表
        "exit_signals": exit_signals,  # 退出信号（止盈/止损/暂停 + 维度5增强）
        "va_result": signal_d,  # 价值平均法结果（含触发/未触发）
        "grid_result": signal_e,  # 网格交易结果（含触发/未触发）
        "fund_health": fund_health,  # 基本面健康检查
        "total_suggested": round(total_suggested, 2),  # 信号触发金额汇总（旧字段，向后兼容）
        "final_suggested_amount": round(final_suggested_amount, 2),  # 2026-07-10 新增：多维度最终金额
        "has_signal": len([s for s in triggered_signals if s.get("triggered")]) > 0,
        "fund_type": fund_type_info["label"],
        "fund_type_code": fund_type,
        "type_strategy": type_strategy,
        "kelly": kelly,
        "recovery": recovery,
        "win_rate": win_rate,
        "confidence_mult": confidence_mult,
        "rhythm_adjust": rhythm_adjust,
        # 2026-07-10 新增：多维度仓位决策详情
        "position_sizing": position_sizing,
        "target_driven_monthly": round(target_driven_monthly, 2),
        "safety": {
            "max_position_pct": max_position_pct,
            "current_position_pct": position_pct,
            "can_add": can_add,
            "kelly_limit": kelly["limit_pct"],
            "type_hard_cap": type_strategy["hard_cap_pct"],
            "kelly_warning": kelly_warning,
            "data_warning": ("持仓净值数据缺失或过期（>3天），盈亏可能不准" if _price_stale
                             else ""),
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
