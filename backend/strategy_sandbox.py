"""策略沙盒：历史回测引擎。

支持 5 种基础策略的回测：
1. 固定金额定投 (DCA)
2. 低估多投、高估少投（估值加权定投）
3. 估值分位买入 / 止盈
4. 定期再平衡
5. 偏离阈值再平衡

数据来源：
- 指数估值历史：db/valuations.py → index_valuations 表
- 基金净值历史：akshare → fund_open_fund_info_em()
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── 预设策略 ──
PRESETS = [
    {
        "id": "dca_basic",
        "name": "普通定投",
        "description": "每月固定金额买入，不看估值",
        "strategy": "dca",
        "params": {"monthly_amount": 1000},
    },
    {
        "id": "valuation_weighted",
        "name": "估值加权定投",
        "description": "低估多投、高估少投，根据 PE 分位动态调整投入倍数",
        "strategy": "valuation_dca",
        "params": {
            "monthly_amount": 1000,
            "low_pct": 30,
            "high_pct": 70,
            "min_multiplier": 0.5,
            "max_multiplier": 2.0,
        },
    },
    {
        "id": "percentile_buy_sell",
        "name": "估值分位买卖",
        "description": "PE 分位 <30% 买入，>70% 止盈卖出",
        "strategy": "percentile_trade",
        "params": {
            "buy_threshold": 30,
            "sell_threshold": 70,
            "buy_amount": 2000,
            "sell_ratio": 0.3,
        },
    },
    {
        "id": "periodic_rebalance",
        "name": "定期再平衡",
        "description": "每季度将组合恢复到目标配置",
        "strategy": "periodic_rebalance",
        "params": {"frequency_months": 3, "equity_target": 0.6},
    },
    {
        "id": "threshold_rebalance",
        "name": "偏离阈值再平衡",
        "description": "当权益仓位偏离目标超过 5% 时触发再平衡",
        "strategy": "threshold_rebalance",
        "params": {"equity_target": 0.6, "drift_threshold": 0.05},
    },
]


def _get_valuation_series(index_code: str, days: int = 365 * 3) -> list[dict]:
    """获取指数估值历史序列（按日期升序）。"""
    from db import get_valuation_history
    rows = get_valuation_history(index_code, days=days, metric_type="市盈率")
    # 按日期升序
    rows.sort(key=lambda r: r.get("snapshot_date", ""))
    return rows


def _get_nav_series(fund_code: str, days: int = 365 * 3) -> list[dict]:
    """获取基金净值序列（按日期升序）。"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势')
        if df is None or len(df) == 0:
            return []
        series = []
        for _, row in df.iterrows():
            series.append({
                "date": str(row["净值日期"]),
                "nav": float(row["单位净值"]),
            })
        series.sort(key=lambda r: r["date"])
        if days > 0 and len(series) > days:
            series = series[-days:]
        return series
    except Exception as e:
        logger.warning(f"获取基金 {fund_code} 净值历史失败: {e}")
        return []


def _build_monthly_series(valuation_series: list[dict], nav_series: list[dict]) -> list[dict]:
    """将估值和净值按月合并，生成回测用的月度数据点。

    返回: [{"date": "2024-01", "nav": float, "percentile": float|None}, ...]
    """
    # 建立估值按月映射
    val_by_month: dict[str, float] = {}
    for v in valuation_series:
        month_key = (v.get("snapshot_date") or "")[:7]  # "2024-01"
        if month_key and v.get("percentile") is not None:
            val_by_month[month_key] = float(v["percentile"])

    # 建立净值按月映射（取月末最后一个交易日）
    nav_by_month: dict[str, float] = {}
    for n in nav_series:
        month_key = (n.get("date") or "")[:7]
        if month_key:
            nav_by_month[month_key] = float(n["nav"])

    # 合并
    all_months = sorted(set(list(val_by_month.keys()) + list(nav_by_month.keys())))
    result = []
    for m in all_months:
        if m in nav_by_month:
            result.append({
                "date": m,
                "nav": nav_by_month[m],
                "percentile": val_by_month.get(m),
            })
    return result


def _ann_return(total_return: float, months: int) -> float:
    """计算年化收益率。"""
    if months <= 0:
        return 0.0
    years = months / 12
    if total_return <= -1:
        return -1.0
    return (1 + total_return) ** (1 / years) - 1


def _max_drawdown(equity_curve: list[float]) -> float:
    """计算最大回撤。"""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _volatility(returns: list[float]) -> float:
    """计算波动率（标准差）。"""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance)


# ── 回测策略实现 ──

def _run_dca(series: list[dict], initial_cash: float, monthly_amount: float,
             buy_fee: float = 0.0015, sell_fee: float = 0.005, mgmt_fee_annual: float = 0.015) -> dict:
    """固定金额定投。"""
    cash = initial_cash
    shares = 0.0
    total_invested = initial_cash
    trades = 0
    total_fees = 0.0
    equity_curve = []

    for i, point in enumerate(series):
        nav = point["nav"]
        # 每月投入（跳过第一个月，从第二个月开始定投）
        if i > 0:
            cash += monthly_amount
            total_invested += monthly_amount
            # 买入（扣除申购费）
            fee = monthly_amount * buy_fee
            net_amount = monthly_amount - fee
            buy_shares = net_amount / nav
            shares += buy_shares
            cash -= monthly_amount
            total_fees += fee
            trades += 1
        # 管理费（按月扣除）
        total_value = cash + shares * nav
        mgmt_fee = total_value * mgmt_fee_annual / 12
        total_fees += mgmt_fee
        equity_curve.append(total_value - mgmt_fee)

    final_value = equity_curve[-1] if equity_curve else initial_cash
    total_return = (final_value - total_invested) / total_invested if total_invested > 0 else 0
    months = len(series)
    cash_idle = cash / final_value if final_value > 0 else 0

    return {
        "total_invested": round(total_invested, 2),
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 4),
        "ann_return": round(_ann_return(total_return, months), 4),
        "max_drawdown": round(_max_drawdown(equity_curve), 4),
        "volatility": round(_volatility([
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
        ]) if len(equity_curve) > 1 else 0, 4),
        "trades": trades,
        "cash_idle_ratio": round(cash_idle, 4),
        "total_fees": round(total_fees, 2),
        "equity_curve": [round(v, 2) for v in equity_curve],
        "months": months,
    }


def _run_valuation_dca(
    series: list[dict], initial_cash: float, monthly_amount: float,
    low_pct: float = 30, high_pct: float = 70,
    min_mult: float = 0.5, max_mult: float = 2.0,
    buy_fee: float = 0.0015, sell_fee: float = 0.005, mgmt_fee_annual: float = 0.015,
) -> dict:
    """估值加权定投：低估多投、高估少投。"""
    cash = initial_cash
    shares = 0.0
    total_invested = initial_cash
    trades = 0
    total_fees = 0.0
    equity_curve = []

    for i, point in enumerate(series):
        nav = point["nav"]
        pct = point.get("percentile")

        if i > 0:
            # 计算投入倍数
            if pct is not None:
                if pct <= low_pct:
                    mult = max_mult
                elif pct >= high_pct:
                    mult = min_mult
                else:
                    ratio = (pct - low_pct) / (high_pct - low_pct)
                    mult = max_mult - ratio * (max_mult - min_mult)
            else:
                mult = 1.0

            actual_amount = monthly_amount * mult
            cash += actual_amount
            total_invested += actual_amount
            fee = actual_amount * buy_fee
            net_amount = actual_amount - fee
            buy_shares = net_amount / nav
            shares += buy_shares
            cash -= actual_amount
            total_fees += fee
            trades += 1

        total_value = cash + shares * nav
        mgmt_fee = total_value * mgmt_fee_annual / 12
        total_fees += mgmt_fee
        equity_curve.append(total_value - mgmt_fee)

    final_value = equity_curve[-1] if equity_curve else initial_cash
    total_return = (final_value - total_invested) / total_invested if total_invested > 0 else 0
    months = len(series)
    cash_idle = cash / final_value if final_value > 0 else 0

    return {
        "total_invested": round(total_invested, 2),
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 4),
        "ann_return": round(_ann_return(total_return, months), 4),
        "max_drawdown": round(_max_drawdown(equity_curve), 4),
        "volatility": round(_volatility([
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
        ]) if len(equity_curve) > 1 else 0, 4),
        "trades": trades,
        "cash_idle_ratio": round(cash_idle, 4),
        "total_fees": round(total_fees, 2),
        "equity_curve": [round(v, 2) for v in equity_curve],
        "months": months,
    }


def _run_percentile_trade(
    series: list[dict], initial_cash: float,
    buy_threshold: float = 30, sell_threshold: float = 70,
    buy_amount: float = 2000, sell_ratio: float = 0.3,
    buy_fee: float = 0.0015, sell_fee: float = 0.005, mgmt_fee_annual: float = 0.015,
) -> dict:
    """估值分位买卖：低分位买入，高分位止盈。"""
    cash = initial_cash
    shares = 0.0
    total_invested = initial_cash
    trades = 0
    total_fees = 0.0
    equity_curve = []

    for point in series:
        nav = point["nav"]
        pct = point.get("percentile")

        if pct is not None:
            if pct < buy_threshold and cash >= buy_amount:
                # 低估买入（扣除申购费）
                amount = min(buy_amount, cash)
                fee = amount * buy_fee
                net_amount = amount - fee
                buy_shares = net_amount / nav
                shares += buy_shares
                cash -= amount
                total_fees += fee
                trades += 1
            elif pct > sell_threshold and shares > 0:
                # 高估止盈（扣除赎回费）
                sell_shares = shares * sell_ratio
                gross = sell_shares * nav
                fee = gross * sell_fee
                cash += gross - fee
                shares -= sell_shares
                total_fees += fee
                trades += 1

        total_value = cash + shares * nav
        mgmt_fee = total_value * mgmt_fee_annual / 12
        total_fees += mgmt_fee
        equity_curve.append(total_value - mgmt_fee)

    final_value = equity_curve[-1] if equity_curve else initial_cash
    total_return = (final_value - total_invested) / total_invested if total_invested > 0 else 0
    months = len(series)
    cash_idle = cash / final_value if final_value > 0 else 0

    return {
        "total_invested": round(total_invested, 2),
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 4),
        "ann_return": round(_ann_return(total_return, months), 4),
        "max_drawdown": round(_max_drawdown(equity_curve), 4),
        "volatility": round(_volatility([
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
        ]) if len(equity_curve) > 1 else 0, 4),
        "trades": trades,
        "cash_idle_ratio": round(cash_idle, 4),
        "total_fees": round(total_fees, 2),
        "equity_curve": [round(v, 2) for v in equity_curve],
        "months": months,
    }


def _run_buy_and_hold(series: list[dict], initial_cash: float) -> dict:
    """买入持有（基准策略）。"""
    if not series:
        return {"total_invested": initial_cash, "final_value": initial_cash, "total_return": 0,
                "ann_return": 0, "max_drawdown": 0, "volatility": 0, "trades": 1, "cash_idle_ratio": 0,
                "equity_curve": [initial_cash], "months": 0}

    first_nav = series[0]["nav"]
    shares = initial_cash / first_nav
    equity_curve = [initial_cash]
    for point in series[1:]:
        equity_curve.append(round(shares * point["nav"], 2))

    final_value = equity_curve[-1]
    total_return = (final_value - initial_cash) / initial_cash
    months = len(series)

    return {
        "total_invested": round(initial_cash, 2),
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 4),
        "ann_return": round(_ann_return(total_return, months), 4),
        "max_drawdown": round(_max_drawdown(equity_curve), 4),
        "volatility": round(_volatility([
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
        ]) if len(equity_curve) > 1 else 0, 4),
        "trades": 1,
        "cash_idle_ratio": 0,
        "total_fees": 0,
        "equity_curve": [round(v, 2) for v in equity_curve],
        "months": months,
    }


def run_backtest(params: dict) -> dict:
    """运行回测。

    params:
        target_code: 指数代码或基金代码
        target_type: "index" | "fund"
        strategy: "dca" | "valuation_dca" | "percentile_trade" | "periodic_rebalance" | "threshold_rebalance"
        initial_cash: 初始资金
        monthly_amount: 每月投入金额
        days: 回看天数（默认 365*3）
        ... 其他策略参数
    """
    target_code = params.get("target_code", "")
    target_type = params.get("target_type", "index")
    strategy = params.get("strategy", "dca")
    initial_cash = float(params.get("initial_cash", 10000))
    monthly_amount = float(params.get("monthly_amount", 1000))
    days = int(params.get("days", 365 * 3))

    # 获取数据
    if target_type == "fund":
        nav_series = _get_nav_series(target_code, days=days)
        valuation_series = _get_valuation_series(target_code, days=days)
    else:
        nav_series = _get_nav_series(target_code, days=days)
        valuation_series = _get_valuation_series(target_code, days=days)

    if not nav_series:
        return {
            "status": "error",
            "error": f"无法获取 {target_code} 的历史数据，请确认代码正确且有足够历史",
        }

    series = _build_monthly_series(valuation_series, nav_series)
    if len(series) < 3:
        return {
            "status": "error",
            "error": f"历史数据不足（仅 {len(series)} 个月），至少需要 3 个月数据",
        }

    # 费率参数
    buy_fee = float(params.get("buy_fee_rate", 0.0015))
    sell_fee = float(params.get("sell_fee_rate", 0.005))
    mgmt_fee = float(params.get("mgmt_fee_annual", 0.015))

    # 运行策略
    if strategy == "dca":
        result = _run_dca(series, initial_cash, monthly_amount, buy_fee, sell_fee, mgmt_fee)
    elif strategy == "valuation_dca":
        result = _run_valuation_dca(
            series, initial_cash, monthly_amount,
            low_pct=float(params.get("low_pct", 30)),
            high_pct=float(params.get("high_pct", 70)),
            min_mult=float(params.get("min_multiplier", 0.5)),
            max_mult=float(params.get("max_multiplier", 2.0)),
            buy_fee=buy_fee, sell_fee=sell_fee, mgmt_fee_annual=mgmt_fee,
        )
    elif strategy == "percentile_trade":
        result = _run_percentile_trade(
            series, initial_cash,
            buy_threshold=float(params.get("buy_threshold", 30)),
            sell_threshold=float(params.get("sell_threshold", 70)),
            buy_amount=float(params.get("buy_amount", 2000)),
            sell_ratio=float(params.get("sell_ratio", 0.3)),
            buy_fee=buy_fee, sell_fee=sell_fee, mgmt_fee_annual=mgmt_fee,
        )
    else:
        result = _run_dca(series, initial_cash, monthly_amount, buy_fee, sell_fee, mgmt_fee)

    # 基准对比：买入持有
    benchmark = _run_buy_and_hold(series, initial_cash)

    return {
        "status": "ok",
        "target_code": target_code,
        "target_type": target_type,
        "strategy": strategy,
        "params": params,
        "result": result,
        "benchmark": benchmark,
        "months": len(series),
        "disclaimer": "历史回测不代表未来收益，仅供参考。实际投资需考虑交易成本、流动性、市场冲击等因素。",
    }
