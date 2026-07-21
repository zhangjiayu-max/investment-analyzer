"""仓位规模决策层 — 加减仓金额多维度计算体系。

核心理念：原始投入驱动（max(total_cost, current_value)）
- 亏损时不缩水（用 total_cost 避免深套标的被误判低仓位）
- 盈利时不虚增（用 current_value 反映真实暴露）

六维体系：
- 维度1：目标仓位锚定（缺口÷动态周期 → 月度建议）
- 维度2：穿透指数集中度（软提示，不硬拦截）
- 维度3：首次建仓标准仓（分3个月补足）
- 维度4：收益弹性预警（UI 提示，不直接改金额）
- 维度5：动态减仓曲线（保留止盈信号+增强）
- 维度6：资金约束（现金+减仓释放+月度现金流）

被 smart_add_planner.py 调用，作为"上层决策层"，与现有5个执行引擎协同：
- 上层：目标驱动金额 + 多维约束
- 下层：信号触发金额（金字塔/趋势/大跌/价值平均/网格）
- 最终：max(目标驱动, 信号触发) × 多维约束
"""

import logging
from typing import Optional

from db.config import get_config_float, get_config_int, get_config_bool
from db.portfolio import get_cash_balance, list_holdings, get_portfolio_summary
from services.advisor.smart_add_metrics import classify_fund, calc_kelly_limit

logger = logging.getLogger(__name__)


# ── 常量配置 ──────────────────────────────────

# 维度1：估值系数（估值越低目标仓位越高）
_VALUATION_COEFF = {
    "extremely_undervalued": 1.0,   # 分位 0-20%
    "undervalued":          0.7,    # 分位 20-40%
    "reasonable":           0.5,    # 分位 40-60%
    "overvalued":           0.3,    # 分位 60-80%
    "extremely_overvalued": 0.1,    # 分位 80%+
}

# 维度1：动态调整周期（月）
_ADJUST_MONTHS = {
    "extremely_undervalued": 3,   # 极低估，3个月快速补足
    "undervalued":          6,    # 低估，6个月
    "reasonable":           9,    # 合理，9个月
    "overvalued":           12,   # 偏高，12个月慢慢减
    "extremely_overvalued": 12,
}

# 维度2：穿透集中度上限（%）
INDEX_EXPOSURE_LIMITS = {
    "broad":        40,
    "industry":     30,
    "theme":        20,
    "bond":         50,
    "hk_overseas":  25,
    "unknown":      30,
}

# 维度3：首次标准仓（%）
FIRST_POSITION_PCT = {
    "broad":        8,
    "industry":     5,
    "theme":        3,
    "bond":         10,
    "hk_overseas":  5,
    "unknown":      5,
}

# 维度4：估值预期涨幅（基于历史均值，用于弹性计算）
_EXPECTED_RETURN_BY_PERCENTILE = {
    "extremely_undervalued": 30,   # 极低估，历史均值+30%
    "undervalued":          15,
    "reasonable":           5,
    "overvalued":           -5,
    "extremely_overvalued": -10,   # 高估，预期下跌
}


def _percentile_bucket(percentile: Optional[float]) -> str:
    """将估值分位映射到区间名。"""
    if percentile is None:
        return "reasonable"
    if percentile < 20:
        return "extremely_undervalued"
    if percentile < 40:
        return "undervalued"
    if percentile < 60:
        return "reasonable"
    if percentile < 80:
        return "overvalued"
    return "extremely_overvalued"


def _effective_base(holding: dict) -> float:
    """统一基准：max(total_cost, current_value)。
    
    亏损时用 total_cost（不缩水），盈利时用 current_value（不虚增）。
    """
    total_cost = holding.get("total_cost") or 0
    current_value = holding.get("current_value") or 0
    return max(total_cost, current_value)


# ── 维度1：目标仓位锚定 ──────────────────────────

def calc_target_position(
    kelly: dict,
    valuation: Optional[dict],
    fund_type: str,
    type_strategy: dict,
    exposure_warning: dict,
    cash_constraint: dict,
    user_id: str = "default",
) -> dict:
    """维度1：计算目标仓位。
    
    Returns:
        {
            "target_pct": float,          # 目标仓位%
            "valuation_coeff": float,     # 估值系数
            "bucket": str,                # 分位区间
            "adjust_months": int,         # 动态调整周期
            "components": {...},          # 各约束值
        }
    """
    percentile = valuation.get("percentile") if valuation else None
    bucket = _percentile_bucket(percentile)
    valuation_coeff = _VALUATION_COEFF[bucket]
    adjust_months = _ADJUST_MONTHS[bucket]

    # 凯利上限 × 估值系数
    kelly_limit_pct = kelly.get("limit_pct", 25.0)
    kelly_adjusted = kelly_limit_pct * valuation_coeff

    # 类型硬上限
    type_hard_cap = type_strategy.get("hard_cap_pct", 25.0)

    # 用户配置全局上限
    try:
        user_max_pct = get_config_float("smart_add.max_single_position_pct", 25.0)
    except Exception:
        user_max_pct = 25.0

    # 维度2穿透余量（软约束：未超限时用余量作为约束，超限时仍用上限不硬截）
    exposure_room = exposure_warning.get("room_pct")
    if exposure_room is None or exposure_room <= 0:
        # 超限或无数据时不通过此约束硬截（软提示）
        exposure_constraint = user_max_pct
    else:
        exposure_constraint = exposure_room

    # 维度6资金余量（软约束：资金不足时约束目标仓位）
    cash_room_pct = cash_constraint.get("position_room_pct")
    if cash_room_pct is None or cash_room_pct <= 0:
        cash_constraint_val = user_max_pct
    else:
        cash_constraint_val = cash_room_pct

    # 用户风险偏好调节
    risk_mult = _get_user_risk_multiplier(user_id)

    target_pct = min(
        kelly_adjusted * risk_mult,
        type_hard_cap,
        user_max_pct,
        exposure_constraint,
        cash_constraint_val,
    )
    # 目标仓位最低 0（高估时可能为 0）
    target_pct = max(0.0, target_pct)

    return {
        "target_pct": round(target_pct, 2),
        "valuation_coeff": valuation_coeff,
        "bucket": bucket,
        "adjust_months": adjust_months,
        "components": {
            "kelly_adjusted": round(kelly_adjusted, 2),
            "type_hard_cap": type_hard_cap,
            "user_max_pct": user_max_pct,
            "exposure_constraint": round(exposure_constraint, 2),
            "cash_constraint": round(cash_constraint_val, 2),
            "risk_mult": risk_mult,
        },
    }


def _get_user_risk_multiplier(user_id: str) -> float:
    """读取用户风险偏好，返回目标仓位调节系数。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT risk_tolerance FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return 1.0
        risk = (row["risk_tolerance"] or "").strip()
        return {
            "conservative": 0.6,
            "steady":       0.8,
            "balanced":     1.0,
            "aggressive":   1.2,
            "radical":      1.4,
        }.get(risk, 1.0)
    except Exception:
        return 1.0


# ── 维度2：穿透指数集中度 ──────────────────────────

def calc_index_exposure(holdings: list, total_assets: float) -> dict:
    """计算每个指数的穿透暴露占比。

    数据源优先级：
    1. holding.index_code（已由 index_fund_mapper 回填）
    2. fund_metadata.tracking_index（指数名称，作为聚合 key）
    3. holding.index_name（兜底）

    基准：max(total_cost, current_value)（避免深套标的被低估暴露）

    Returns:
        {index_code_or_name: {total_pct, funds, fund_names}}
    """
    exposure = {}
    if total_assets <= 0:
        return exposure

    # 懒加载 fund_metadata 缓存（避免每个持仓重复查询）
    _meta_cache = {}

    def _resolve_index(h: dict) -> str:
        """解析持仓对应的指数标识（代码或名称）。"""
        # 优先用 index_code
        code = (h.get("index_code") or "").strip()
        if code:
            return code
        # 回退1: fund_metadata.tracking_index
        fund_code = h.get("fund_code", "")
        if fund_code and fund_code not in _meta_cache:
            try:
                from services.fund.fund_data_service import get_fund_metadata
                _meta_cache[fund_code] = get_fund_metadata(fund_code) or {}
            except Exception:
                _meta_cache[fund_code] = {}
        meta = _meta_cache.get(fund_code, {})
        tracking = (meta.get("tracking_index") or "").strip()
        if tracking and "无跟踪" not in tracking:
            return tracking
        # 回退2: index_name
        idx_name = (h.get("index_name") or "").strip()
        if idx_name:
            return idx_name
        return ""

    for h in holdings:
        index_key = _resolve_index(h)
        if not index_key:
            continue
        effective_base = _effective_base(h)
        pct = effective_base / total_assets * 100
        exposure.setdefault(index_key, {
            "total_pct": 0.0,
            "funds": [],
            "fund_names": [],
        })
        exposure[index_key]["total_pct"] += pct
        exposure[index_key]["funds"].append(h.get("fund_code", ""))
        exposure[index_key]["fund_names"].append(h.get("fund_name", ""))
    # 四舍五入
    for code, info in exposure.items():
        info["total_pct"] = round(info["total_pct"], 2)
    return exposure


def check_index_exposure_warning(
    exposure: dict,
    index_code: str,
    fund_type: str,
) -> dict:
    """检查当前指数穿透是否超限，返回软提示。
    
    软提示：不拦截，仅警告。超限时仍允许加仓，但前端展示橙色警告。
    """
    if not index_code:
        return {"level": "ok", "room_pct": None, "exceeded": False, "message": ""}
    current = exposure.get(index_code, {}).get("total_pct", 0)
    limit = INDEX_EXPOSURE_LIMITS.get(fund_type, 30)
    if current >= limit:
        return {
            "level": "warning",
            "current_pct": round(current, 2),
            "limit_pct": limit,
            "exceeded": True,
            "room_pct": 0.0,
            "message": (
                f"该指数穿透仓位{current:.1f}%已超{fund_type}上限{limit}%，"
                f"建议优先补仓其他低估品种"
            ),
        }
    return {
        "level": "ok",
        "current_pct": round(current, 2),
        "limit_pct": limit,
        "exceeded": False,
        "room_pct": round(limit - current, 2),
        "message": "",
    }


# ── 维度3：首次建仓标准仓 ──────────────────────────

def check_first_position_needed(
    holding: dict,
    fund_type: str,
    total_assets: float,
) -> dict:
    """判断是否需要首次仓补足。
    
    关键：基于 total_cost 判断，而非 current_value
    原因：首次建仓只关心"投入了多少"，不关心"现在值多少"
    """
    if total_assets <= 0:
        return {"needed": False}

    first_pct = FIRST_POSITION_PCT.get(fund_type, 5)
    total_cost = holding.get("total_cost") or 0

    # 基于原始投入判断
    cost_position_pct = total_cost / total_assets * 100

    if cost_position_pct < first_pct * 0.5:
        # 仓位严重不足（< 标准仓50%）
        target_add = total_assets * first_pct / 100 - total_cost
        monthly = target_add / 3
        return {
            "needed": True,
            "level": "critical",
            "first_pct": first_pct,
            "current_cost_pct": round(cost_position_pct, 2),
            "target_add_total": round(target_add, 2),
            "monthly_add": round(monthly, 2),
            "period_months": 3,
            "reason": (
                f"原投入{cost_position_pct:.1f}% < 首次标准仓{first_pct}%的50%，"
                f"分3个月补足"
            ),
        }
    return {"needed": False}


# ── 维度4：收益弹性预警 ──────────────────────────

def calc_return_elasticity(
    holding: dict,
    valuation: Optional[dict],
    total_assets: float,
) -> dict:
    """计算持仓收益弹性。
    
    弹性 = 仓位 × 指数预期涨幅（基于估值分位的历史均值）
    仓位基准：max(total_cost, current_value)
    """
    if total_assets <= 0:
        return {"elasticity": 0, "level": "normal", "message": ""}

    effective_base = _effective_base(holding)
    position_pct = effective_base / total_assets

    percentile = valuation.get("percentile") if valuation else None
    bucket = _percentile_bucket(percentile)
    expected_return_pct = _EXPECTED_RETURN_BY_PERCENTILE[bucket]

    elasticity = position_pct * expected_return_pct / 100  # 组合收益贡献(%)

    level = "normal"
    message = ""
    if position_pct < 3 and expected_return_pct > 10:
        level = "low"
        message = (
            f"仓位{position_pct:.1f}%，即使涨{expected_return_pct}%"
            f"也只贡献组合{elasticity:.2f}%收益。"
            f"建议补至5%+，让涨{expected_return_pct}%能贡献"
            f"{5 * expected_return_pct / 100:.2f}%组合收益"
        )
    elif position_pct > 30 and expected_return_pct < 0:
        level = "high_risk"
        message = (
            f"仓位{position_pct:.1f}%，跌{abs(expected_return_pct)}%"
            f"会损失组合{abs(elasticity):.2f}%收益。建议减仓至15%"
        )

    return {
        "position_pct": round(position_pct, 2),
        "expected_return_pct": expected_return_pct,
        "elasticity": round(elasticity, 2),
        "level": level,
        "message": message,
        "bucket": bucket,
    }


# ── 维度5：动态减仓曲线（增强版） ──────────────────────────

def detect_exit_signals_enhanced(
    holding: dict,
    valuation: Optional[dict],
    cfg: dict,
    total_assets: float,
    fund_type: str,
    target_position_pct: float,
) -> list:
    """维度5：动态减仓信号检测（保留止盈信号+增强）。
    
    增强：
    - 估值>80% → 减至目标仓位
    - 估值70-80% → 减仓30%
    - 穿透超限+高估 → 优先减超限标的
    
    Returns:
        list of exit signals（与现有 _detect_exit_signals 格式兼容，新增 reduce_amount 字段）
    """
    if not cfg.get("exit_signal_enabled", False):
        return []

    exit_signals = []
    profit_rate = holding.get("profit_rate") or 0
    profit_rate_pct = profit_rate * 100
    current_value = holding.get("current_value") or 0
    effective_base = _effective_base(holding)
    current_position_pct = effective_base / total_assets * 100 if total_assets else 0

    val_pct = valuation.get("percentile") if valuation else None

    # 信号1：估值过高减仓（>80%）→ 减至目标仓位
    if val_pct is not None and val_pct > 80:
        target_value = total_assets * target_position_pct / 100
        reduce_amount = max(0, current_value - target_value)
        exit_signals.append({
            "type": "valuation_overvalued",
            "label": "估值过高减仓",
            "triggered": reduce_amount > 0,
            "reason": f"估值分位{val_pct:.0f}%>80%，减至目标仓位{target_position_pct:.1f}%",
            "suggested_action": f"建议减仓¥{reduce_amount:,.0f}",
            "severity": "danger",
            "reduce_amount": round(reduce_amount, 2),
            "target_position_pct": target_position_pct,
        })

    # 信号2：估值偏高减仓（70-80%）→ 减仓30%
    elif val_pct is not None and 70 <= val_pct <= 80:
        reduce_amount = current_value * 0.3
        exit_signals.append({
            "type": "valuation_high",
            "label": "估值偏高减仓",
            "triggered": reduce_amount > 0,
            "reason": f"估值分位{val_pct:.0f}%偏高，减仓30%",
            "suggested_action": f"建议分3次减仓¥{reduce_amount:,.0f}",
            "severity": "warning",
            "reduce_amount": round(reduce_amount, 2),
        })

    # 信号3：止盈清仓（盈利 > 止盈线×1.5）
    if profit_rate_pct > 0:
        if fund_type == "broad":
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
                "reduce_amount": round(current_value, 2),
            })
        elif profit_rate_pct >= take_profit_threshold:
            reduce_amount = current_value / 3
            exit_signals.append({
                "type": "take_profit",
                "label": "止盈减仓",
                "triggered": True,
                "reason": f"盈利{profit_rate_pct:.1f}%已达止盈线{take_profit_threshold}%",
                "suggested_action": f"建议减仓1/3 ¥{reduce_amount:,.0f}（分3次间隔10天）",
                "severity": "info",
                "reduce_amount": round(reduce_amount, 2),
            })

    # 信号4：止损退出（亏损超止损线且估值非低估）
    stop_loss_pct = cfg.get("stop_loss_pct", -30)
    if profit_rate_pct <= stop_loss_pct:
        stop_loss_val_pct = cfg.get("stop_loss_valuation_pct", 50)
        if val_pct is not None and val_pct > stop_loss_val_pct:
            reduce_amount = current_value * 0.5
            exit_signals.append({
                "type": "stop_loss",
                "label": "止损",
                "triggered": True,
                "reason": f"亏损{profit_rate_pct:.1f}%超过止损线{stop_loss_pct}%，估值分位{val_pct:.0f}%非低估",
                "suggested_action": f"建议止损50% ¥{reduce_amount:,.0f}",
                "severity": "danger",
                "reduce_amount": round(reduce_amount, 2),
            })

    # 信号5：超配减仓（当前仓位 > 目标仓位 × 1.5）
    if current_position_pct > target_position_pct * 1.5 and target_position_pct > 0:
        target_value = total_assets * target_position_pct / 100
        reduce_amount = max(0, current_value - target_value)
        if reduce_amount > 0:
            exit_signals.append({
                "type": "overweight",
                "label": "超配减仓",
                "triggered": True,
                "reason": f"当前仓位{current_position_pct:.1f}% > 目标{target_position_pct:.1f}%×1.5",
                "suggested_action": f"建议减至目标仓位 ¥{reduce_amount:,.0f}",
                "severity": "info",
                "reduce_amount": round(reduce_amount, 2),
                "target_position_pct": target_position_pct,
            })

    return exit_signals


# ── 维度6：资金约束 ──────────────────────────

def calc_cash_constraint(
    user_id: str,
    cfg: dict,
    holdings: list,
    total_assets: float,
) -> dict:
    """计算可用补仓资金及仓位约束。
    
    Returns:
        {
            "cash_balance": float,
            "usable_cash": float,        # 留 20% 应急
            "monthly_inflow": float,
            "total_available_3m": float,  # 3个月可用
            "position_room_pct": float,  # 资金允许的最大仓位增量%
        }
    """
    try:
        cash_info = get_cash_balance(user_id)
        cash = (cash_info or {}).get("balance", 0) or 0
    except Exception:
        cash = 0.0

    usable_cash = cash * 0.8  # 留 20% 应急

    # 月度现金流（用户可配置，默认 0）
    try:
        monthly_inflow = get_config_float("smart_add.monthly_cash_inflow", 0.0)
    except Exception:
        monthly_inflow = 0.0

    # 3个月可用资金（保守估算，不含减仓释放，因减仓需用户手动执行）
    total_available_3m = usable_cash + monthly_inflow * 3

    # 资金允许的最大仓位增量% = 3个月可用 / 总资产 × 100
    position_room_pct = (total_available_3m / total_assets * 100) if total_assets > 0 else 0

    return {
        "cash_balance": round(cash, 2),
        "usable_cash": round(usable_cash, 2),
        "monthly_inflow": round(monthly_inflow, 2),
        "total_available_3m": round(total_available_3m, 2),
        "position_room_pct": round(position_room_pct, 2),
    }


# ── 主入口 ──────────────────────────────────

def generate_position_sizing_plan(
    holding: dict,
    cfg: dict,
    total_assets: float,
    all_holdings: list,
    kelly: dict,
    valuation: Optional[dict],
    fund_type: str,
    type_strategy: dict,
    user_id: str = "default",
) -> dict:
    """生成单标的多维度仓位建议。

    Args:
        holding: 单标的持仓
        cfg: 智能补仓配置
        total_assets: 组合总资产
        all_holdings: 全部持仓（用于穿透计算）
        kelly: L3 凯利计算结果
        valuation: 估值数据
        fund_type: L2 基金类型
        type_strategy: L2 类型策略
        user_id: 用户ID

    Returns:
        {
            "effective_base": float,         # 统一基准
            "current_position_pct": float,   # 当前仓位%
            "target_position": {...},        # 维度1
            "index_exposure": {...},         # 维度2
            "first_position": {...},         # 维度3
            "elasticity": {...},             # 维度4
            "exit_signals": [...],           # 维度5
            "cash_constraint": {...},        # 维度6
            "target_driven_monthly": float,  # 目标驱动月度金额
            "reduce_amount": float,          # 减仓金额（如有）
            "summary": str,                  # 一句话建议
        }
    """
    # 统一基准
    effective_base = _effective_base(holding)
    current_position_pct = (effective_base / total_assets * 100) if total_assets > 0 else 0

    # 维度2：穿透集中度（全局计算）
    exposure = calc_index_exposure(all_holdings, total_assets)
    index_code = holding.get("index_code") or ""
    exposure_warning = check_index_exposure_warning(exposure, index_code, fund_type)

    # 维度6：资金约束（组合层，缓存）
    cash_constraint = calc_cash_constraint(user_id, cfg, all_holdings, total_assets)

    # 维度1：目标仓位
    target_position = calc_target_position(
        kelly=kelly,
        valuation=valuation,
        fund_type=fund_type,
        type_strategy=type_strategy,
        exposure_warning=exposure_warning,
        cash_constraint=cash_constraint,
        user_id=user_id,
    )
    target_pct = target_position["target_pct"]
    adjust_months = target_position["adjust_months"]

    # 维度5：减仓信号（需先算目标仓位）
    exit_signals = detect_exit_signals_enhanced(
        holding=holding,
        valuation=valuation,
        cfg=cfg,
        total_assets=total_assets,
        fund_type=fund_type,
        target_position_pct=target_pct,
    )

    # 维度3：首次仓补足
    first_position = check_first_position_needed(holding, fund_type, total_assets)

    # 维度4：收益弹性
    elasticity = calc_return_elasticity(holding, valuation, total_assets)

    # 计算目标驱动月度金额
    gap_pct = target_pct - current_position_pct
    target_driven_monthly = 0.0
    reduce_amount = 0.0

    # 减仓信号优先：如有触发，加仓归零
    has_exit = any(s.get("triggered") for s in exit_signals)
    if has_exit:
        # 取最大减仓金额
        reduce_amount = max((s.get("reduce_amount", 0) for s in exit_signals if s.get("triggered")), default=0)
        target_driven_monthly = 0.0
    elif gap_pct > 0:
        # 低配：目标驱动加仓
        target_add_total = total_assets * gap_pct / 100
        target_driven_monthly = target_add_total / adjust_months
        # 维度3首次仓补足取较大值
        if first_position.get("needed"):
            target_driven_monthly = max(target_driven_monthly, first_position.get("monthly_add", 0))
    elif gap_pct < 0:
        # 超配：无加仓（减仓由维度5处理）
        target_driven_monthly = 0.0

    # 生成一句话建议
    summary = _build_summary(
        gap_pct=gap_pct,
        target_pct=target_pct,
        current_position_pct=current_position_pct,
        target_driven_monthly=target_driven_monthly,
        reduce_amount=reduce_amount,
        exposure_warning=exposure_warning,
        elasticity=elasticity,
        first_position=first_position,
    )

    return {
        "effective_base": round(effective_base, 2),
        "current_position_pct": round(current_position_pct, 2),
        "target_position": target_position,
        "index_exposure": {
            "warning": exposure_warning,
            "all_exposure": exposure,  # 全部指数穿透数据（前端可展示同类列表）
        },
        "first_position": first_position,
        "elasticity": elasticity,
        "exit_signals": exit_signals,
        "cash_constraint": cash_constraint,
        "target_driven_monthly": round(target_driven_monthly, 2),
        "reduce_amount": round(reduce_amount, 2),
        "gap_pct": round(gap_pct, 2),
        "summary": summary,
    }


def _build_summary(
    gap_pct: float,
    target_pct: float,
    current_position_pct: float,
    target_driven_monthly: float,
    reduce_amount: float,
    exposure_warning: dict,
    elasticity: dict,
    first_position: dict,
) -> str:
    """生成一句话建议摘要。"""
    parts = []

    # 减仓优先
    if reduce_amount > 0:
        parts.append(f"建议减仓¥{reduce_amount:,.0f}")
    elif gap_pct > 0 and target_driven_monthly > 0:
        parts.append(
            f"目标仓位{target_pct:.1f}%，当前{current_position_pct:.1f}%，"
            f"缺口{gap_pct:.1f}%，月度建议¥{target_driven_monthly:,.0f}"
        )
    elif gap_pct < 0:
        parts.append(f"超配{abs(gap_pct):.1f}%，建议持有或减仓")
    else:
        parts.append("仓位达标，维持持有")

    # 首次仓补足提示
    if first_position.get("needed"):
        parts.append(f"⚠️{first_position['reason']}")

    # 穿透超限提示
    if exposure_warning.get("exceeded"):
        parts.append(f"⚠️{exposure_warning['message']}")

    # 弹性预警
    if elasticity.get("level") == "low":
        parts.append(f"⚠️{elasticity['message']}")

    return "；".join(parts)
