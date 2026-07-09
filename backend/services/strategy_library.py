"""策略库 + 回测引擎。

提供 4 种策略模板：
1. DCAStrategy          — 定投策略（fixed / ma / valuation 三种触发方式）
2. GridStrategy         — 网格交易策略
3. TwoEightStrategy     — 二八股债平衡策略
4. CoreSatelliteStrategy — 核心卫星策略

回测引擎：
- run_backtest()       — 运行单次回测，保存结果到 backtest_results 表
- parameter_sweep()    — 参数扫描，按 sharpe_ratio 排序

指标计算（纯 Python，不依赖 numpy/scipy）：
- total_return / annual_return / max_drawdown / sharpe_ratio

数据来源：db.valuations.get_valuation_history（指数估值历史）
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime

from db.valuations import get_valuation_history
from db.backtest_results import save_backtest, list_backtests  # noqa: F401（list_backtests 供路由复用）

logger = logging.getLogger(__name__)

# 无风险利率（年化 3%）
RISK_FREE_RATE_ANNUAL = 0.03
# 年化交易日数
TRADING_DAYS_PER_YEAR = 252


# ══════════════════════════════════════════════════════
# 策略基类
# ══════════════════════════════════════════════════════

class Strategy:
    """策略基类。子类需实现 generate_signals，可选重写 run。

    nav_series 约定（按日期升序）：
        [{"date": "2024-01-15", "nav": 3456.78,
          "percentile": 35.2, "pe": 12.5}, ...]
    """

    name: str = "base"

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    def generate_signals(self, nav_series: list[dict]) -> list[dict]:
        """生成买卖信号列表。子类必须实现。

        Returns:
            [{"date": str, "type": "buy"|"sell", "amount": float, "reason": str}, ...]
        """
        raise NotImplementedError

    def run(self, nav_series: list[dict], initial_cash: float) -> dict:
        """运行回测，返回净值曲线 + 指标。

        默认实现：按信号从现有现金中买卖，total_invested = initial_cash。
        定投类策略（有持续外部资金注入）需重写此方法。
        """
        if not nav_series:
            return _empty_result(initial_cash)
        signals = self.generate_signals(nav_series)
        return _execute_signals(signals, nav_series, initial_cash, self.name)


# ══════════════════════════════════════════════════════
# 辅助函数：指标计算 + 技术指标
# ══════════════════════════════════════════════════════

def _empty_result(initial_cash: float) -> dict:
    """数据不足时的空结果。"""
    return {
        "strategy": "",
        "total_invested": round(initial_cash, 2),
        "final_value": round(initial_cash, 2),
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "nav_curve": [],
        "trades": 0,
        "days": 0,
    }


def _calc_ma(values: list[float], window: int) -> list[float | None]:
    """计算简单移动平均（SMA）。不足窗口返回 None。"""
    result: list[float | None] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(values[i - window + 1: i + 1]) / window)
    return result


def _calc_rsi(values: list[float], period: int = 14) -> list[float | None]:
    """计算 RSI 指标（Wilder 平滑法）。"""
    result: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = _rsi_value(avg_gain, avg_loss)

    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        result[i] = _rsi_value(avg_gain, avg_loss)

    return result


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    """计算单点 RSI。"""
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _execute_signals(signals: list[dict], nav_series: list[dict],
                     initial_cash: float, strategy_name: str) -> dict:
    """通用信号执行器：买入从现金扣，卖出回现金。

    适用于资金已在initial_cash中、买卖均在组合内部完成的策略（网格等）。
    total_invested = initial_cash（无外部资金注入）。
    """
    if not nav_series:
        return _empty_result(initial_cash)

    cash = initial_cash
    shares = 0.0
    total_invested = initial_cash
    nav_curve: list[dict] = []
    trades = 0

    # 信号按日期索引
    sig_by_date: dict[str, list[dict]] = {}
    for sig in signals:
        sig_by_date.setdefault(sig.get("date", ""), []).append(sig)

    for point in nav_series:
        d = point["date"]
        nav = point["nav"]
        if nav <= 0:
            continue

        for sig in sig_by_date.get(d, []):
            sig_type = sig.get("type")
            amount = float(sig.get("amount", 0))
            if sig_type == "buy" and amount > 0 and cash >= amount:
                shares += amount / nav
                cash -= amount
                trades += 1
            elif sig_type == "sell" and amount > 0 and shares > 0:
                sell_shares = min(shares, amount / nav)
                cash += sell_shares * nav
                shares -= sell_shares
                trades += 1

        total_value = cash + shares * nav
        nav_curve.append({"date": d, "value": round(total_value, 2)})

    return _build_result(strategy_name, nav_series, nav_curve, total_invested, trades)


def _build_result(strategy_name: str, nav_series: list[dict],
                  nav_curve: list[dict], total_invested: float,
                  trades: int) -> dict:
    """构建回测结果 + 计算 4 项核心指标。"""
    if not nav_curve:
        return _empty_result(total_invested)

    values = [p["value"] for p in nav_curve]
    final_value = values[-1]
    total_return = (final_value - total_invested) / total_invested if total_invested > 0 else 0.0
    days = _calc_days(nav_series)
    annual_return = _calc_annual_return(total_return, days)
    max_drawdown = _calc_max_drawdown(values)
    sharpe_ratio = _calc_sharpe(values)

    return {
        "strategy": strategy_name,
        "total_invested": round(total_invested, 2),
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "nav_curve": nav_curve,
        "trades": trades,
        "days": days,
    }


def _calc_days(nav_series: list[dict]) -> int:
    """计算回测区间天数。"""
    if len(nav_series) < 2:
        return 0
    try:
        d1 = datetime.strptime(str(nav_series[0]["date"]), "%Y-%m-%d")
        d2 = datetime.strptime(str(nav_series[-1]["date"]), "%Y-%m-%d")
        return max(1, (d2 - d1).days)
    except Exception:
        return len(nav_series)


def _calc_annual_return(total_return: float, days: int) -> float:
    """年化收益率 = (1 + total_return) ** (365/days) - 1。"""
    if days <= 0 or total_return <= -1:
        return 0.0
    return (1 + total_return) ** (365.0 / days) - 1


def _calc_max_drawdown(values: list[float]) -> float:
    """最大回撤 = max((peak - valley) / peak)。"""
    if len(values) < 2:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _calc_sharpe(values: list[float]) -> float:
    """夏普比率 = (mean(daily_return) - rf) / std(daily_return) * sqrt(252)。

    rf（日频无风险利率）= 0.03 / 252。
    """
    if len(values) < 3:
        return 0.0
    returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            returns.append((values[i] - values[i - 1]) / values[i - 1])
    if len(returns) < 2:
        return 0.0
    mean_ret = statistics.mean(returns)
    std_ret = statistics.pstdev(returns)
    if std_ret == 0:
        return 0.0
    rf_daily = RISK_FREE_RATE_ANNUAL / TRADING_DAYS_PER_YEAR
    return (mean_ret - rf_daily) / std_ret * math.sqrt(TRADING_DAYS_PER_YEAR)


# ══════════════════════════════════════════════════════
# 策略 1：定投策略 DCA
# ══════════════════════════════════════════════════════

class DCAStrategy(Strategy):
    """定投策略。

    参数：
      interval_days: 定投间隔天数（默认 30）
      amount:        每次定投金额（默认 1000）
      trigger:       触发方式
        - fixed:     固定金额
        - ma:        MA20 上穿 MA60 加倍，下穿减半
        - valuation: PE 分位 <30% 加倍，>70% 减半
    """

    name = "dca"

    def generate_signals(self, nav_series: list[dict]) -> list[dict]:
        if not nav_series:
            return []

        interval_days = int(self.params.get("interval_days", 30))
        amount = float(self.params.get("amount", 1000))
        trigger = self.params.get("trigger", "fixed")

        # 预计算均线（trigger=ma 时使用）
        ma20_list: list[float | None] = []
        ma60_list: list[float | None] = []
        if trigger == "ma":
            nav_values = [p["nav"] for p in nav_series]
            ma20_list = _calc_ma(nav_values, 20)
            ma60_list = _calc_ma(nav_values, 60)

        signals: list[dict] = []
        last_buy_idx = -interval_days  # 让第一个数据点就能触发买入

        for i, point in enumerate(nav_series):
            # 按 interval_days 间隔触发
            if i - last_buy_idx < interval_days:
                continue
            last_buy_idx = i

            mult = 1.0
            reason = "固定定投"

            if trigger == "ma" and i >= 60:
                m20, m60 = ma20_list[i], ma60_list[i]
                p20, p60 = ma20_list[i - 1], ma60_list[i - 1]
                if None not in (m20, m60, p20, p60):
                    # MA20 上穿 MA60 → 加倍
                    if p20 <= p60 and m20 > m60:
                        mult = 2.0
                        reason = "MA20上穿MA60，加倍买入"
                    # MA20 下穿 MA60 → 减半
                    elif p20 >= p60 and m20 < m60:
                        mult = 0.5
                        reason = "MA20下穿MA60，减半买入"

            elif trigger == "valuation":
                pct = point.get("percentile")
                if pct is not None:
                    if pct < 30:
                        mult = 2.0
                        reason = f"PE分位{pct:.0f}%<30%，加倍买入"
                    elif pct > 70:
                        mult = 0.5
                        reason = f"PE分位{pct:.0f}%>70%，减半买入"

            signals.append({
                "date": point["date"],
                "type": "buy",
                "amount": round(amount * mult, 2),
                "reason": reason,
            })

        return signals

    def run(self, nav_series: list[dict], initial_cash: float) -> dict:
        """DCA 模式：初始资金建仓 + 定期新资金注入买入。

        total_invested = initial_cash + sum(每次定投金额)。
        """
        if not nav_series:
            return _empty_result(initial_cash)

        first_nav = nav_series[0]["nav"]
        if first_nav <= 0:
            return _empty_result(initial_cash)

        signals = self.generate_signals(nav_series)

        # 初始资金在首日全部建仓（作为底仓）
        shares = initial_cash / first_nav
        total_invested = initial_cash
        trades = 1
        nav_curve: list[dict] = []

        # 信号按日期索引
        sig_by_date: dict[str, list[dict]] = {}
        for sig in signals:
            sig_by_date.setdefault(sig["date"], []).append(sig)

        for point in nav_series:
            d = point["date"]
            nav = point["nav"]
            if nav <= 0:
                continue

            # 执行定投信号：新资金注入并立即买入
            for sig in sig_by_date.get(d, []):
                if sig.get("type") == "buy":
                    amount = float(sig.get("amount", 0))
                    if amount > 0:
                        total_invested += amount
                        shares += amount / nav
                        trades += 1

            nav_curve.append({"date": d, "value": round(shares * nav, 2)})

        return _build_result(self.name, nav_series, nav_curve, total_invested, trades)


# ══════════════════════════════════════════════════════
# 策略 2：网格策略
# ══════════════════════════════════════════════════════

class GridStrategy(Strategy):
    """网格交易策略。

    参数：
      grid_steps:  网格数（默认 10）
      grid_pct:    每格涨跌幅（默认 0.05，即 5%）
      base_amount: 基础仓位金额（默认 5000）

    逻辑：
      - 初始半仓建仓
      - 价格每跌 grid_pct 加仓一份（per_grid = base_amount / grid_steps）
      - 价格每涨 grid_pct 减仓一份
    """

    name = "grid"

    def generate_signals(self, nav_series: list[dict]) -> list[dict]:
        if not nav_series:
            return []

        grid_steps = int(self.params.get("grid_steps", 10))
        grid_pct = float(self.params.get("grid_pct", 0.05))
        base_amount = float(self.params.get("base_amount", 5000))
        per_grid = base_amount / grid_steps if grid_steps > 0 else base_amount

        signals: list[dict] = []

        # 初始半仓建仓
        first_point = nav_series[0]
        signals.append({
            "date": first_point["date"],
            "type": "buy",
            "amount": round(base_amount / 2, 2),
            "reason": "网格初始建仓（半仓）",
        })

        base_price = first_point["nav"]
        current_level = 0  # 当前所处的网格档位

        for point in nav_series[1:]:
            price = point["nav"]
            d = point["date"]
            if base_price <= 0:
                continue

            # 计算价格相对基准价偏离的网格档位
            change_ratio = (price - base_price) / base_price
            target_level = int(change_ratio / grid_pct)

            if target_level > current_level:
                # 价格上涨到更高档位 → 卖出
                levels = target_level - current_level
                signals.append({
                    "date": d,
                    "type": "sell",
                    "amount": round(per_grid * levels, 2),
                    "reason": f"上涨{levels}格至{target_level}档，卖出",
                })
                current_level = target_level
            elif target_level < current_level:
                # 价格下跌到更低档位 → 买入
                levels = current_level - target_level
                signals.append({
                    "date": d,
                    "type": "buy",
                    "amount": round(per_grid * levels, 2),
                    "reason": f"下跌{levels}格至{target_level}档，买入",
                })
                current_level = target_level

        return signals


# ══════════════════════════════════════════════════════
# 策略 3：二八股债平衡
# ══════════════════════════════════════════════════════

class TwoEightStrategy(Strategy):
    """二八股债平衡策略。

    参数：
      rebalance_day:       每月再平衡日（默认 1）
      equity_ratio:        股票目标仓位比例（默认 0.8）
      bond_return_annual:  债券年化收益率（默认 0.03）

    逻辑：
      - 初始按 equity_ratio 买入股票，剩余为债券（按固定年化计息）
      - 每月 rebalance_day 再平衡到目标比例
    """

    name = "two_eight"

    def generate_signals(self, nav_series: list[dict]) -> list[dict]:
        if not nav_series:
            return []

        rebalance_day = int(self.params.get("rebalance_day", 1))
        equity_ratio = float(self.params.get("equity_ratio", 0.8))

        signals: list[dict] = []
        last_month: str | None = None

        for point in nav_series:
            d = point["date"]
            try:
                dt = datetime.strptime(str(d), "%Y-%m-%d")
            except Exception:
                continue

            month_key = f"{dt.year}-{dt.month:02d}"
            # 每月再平衡日触发一次
            if dt.day >= rebalance_day and month_key != last_month:
                last_month = month_key
                signals.append({
                    "date": d,
                    "type": "rebalance",
                    "amount": 0,
                    "reason": f"月度再平衡至股{int(equity_ratio * 100)}%债{int((1 - equity_ratio) * 100)}%",
                })

        return signals

    def run(self, nav_series: list[dict], initial_cash: float) -> dict:
        """重写 run：同时跟踪股票仓位和债券仓位。"""
        if not nav_series:
            return _empty_result(initial_cash)

        rebalance_day = int(self.params.get("rebalance_day", 1))
        equity_ratio = float(self.params.get("equity_ratio", 0.8))
        bond_return_annual = float(self.params.get("bond_return_annual", 0.03))
        bond_daily_rate = bond_return_annual / 365

        first_nav = nav_series[0]["nav"]
        if first_nav <= 0:
            return _empty_result(initial_cash)

        # 初始按目标比例分配
        equity_value = initial_cash * equity_ratio
        bond_value = initial_cash * (1 - equity_ratio)
        shares = equity_value / first_nav
        total_invested = initial_cash
        trades = 1  # 初始建仓
        last_month: str | None = None
        prev_date = nav_series[0]["date"]
        nav_curve: list[dict] = []

        for point in nav_series:
            d = point["date"]
            nav = point["nav"]
            if nav <= 0:
                continue

            # 债券按日计息
            try:
                dt_prev = datetime.strptime(str(prev_date), "%Y-%m-%d")
                dt_cur = datetime.strptime(str(d), "%Y-%m-%d")
                days_diff = max(0, (dt_cur - dt_prev).days)
                if days_diff > 0:
                    bond_value *= (1 + bond_daily_rate) ** days_diff
            except Exception:
                pass
            prev_date = d

            equity_value = shares * nav

            # 月度再平衡
            try:
                dt = datetime.strptime(str(d), "%Y-%m-%d")
                month_key = f"{dt.year}-{dt.month:02d}"
                if dt.day >= rebalance_day and month_key != last_month:
                    last_month = month_key
                    total_value = equity_value + bond_value
                    target_equity = total_value * equity_ratio
                    diff = target_equity - equity_value
                    if abs(diff) > 1:
                        if diff > 0:
                            # 债转股
                            shares += diff / nav
                            bond_value -= diff
                        else:
                            # 股转债
                            shares -= (-diff) / nav
                            bond_value += (-diff)
                        trades += 1
                    equity_value = shares * nav
            except Exception:
                pass

            nav_curve.append({"date": d, "value": round(equity_value + bond_value, 2)})

        return _build_result(self.name, nav_series, nav_curve, total_invested, trades)


# ══════════════════════════════════════════════════════
# 策略 4：核心卫星
# ══════════════════════════════════════════════════════

class CoreSatelliteStrategy(Strategy):
    """核心卫星策略。

    参数：
      core_ratio:  核心仓位比例（默认 0.6）
      core_code:   核心指数代码（默认 000300，当前回测用 target_code 数据）
      satellites:  卫星列表（预留，当前回测基于 target_code 的均线信号）
      ma_short:    短期均线（默认 20）
      ma_long:     长期均线（默认 60）

    逻辑：
      - 核心仓位（core_ratio）买入持有不动
      - 卫星仓位按均线金叉/死叉调仓
    """

    name = "core_satellite"

    def generate_signals(self, nav_series: list[dict]) -> list[dict]:
        if not nav_series:
            return []

        ma_short = int(self.params.get("ma_short", 20))
        ma_long = int(self.params.get("ma_long", 60))

        nav_values = [p["nav"] for p in nav_series]
        ma_short_list = _calc_ma(nav_values, ma_short)
        ma_long_list = _calc_ma(nav_values, ma_long)

        signals: list[dict] = []
        for i in range(len(nav_series)):
            if i < ma_long:
                continue
            m_s, m_l = ma_short_list[i], ma_long_list[i]
            p_s, p_l = ma_short_list[i - 1], ma_long_list[i - 1]
            if None in (m_s, m_l, p_s, p_l):
                continue

            d = nav_series[i]["date"]
            # 金叉 → 卫星加仓
            if p_s <= p_l and m_s > m_l:
                signals.append({
                    "date": d, "type": "buy", "amount": 1000,
                    "reason": "卫星仓位：均线金叉，加仓",
                })
            # 死叉 → 卫星减仓
            elif p_s >= p_l and m_s < m_l:
                signals.append({
                    "date": d, "type": "sell", "amount": 1000,
                    "reason": "卫星仓位：均线死叉，减仓",
                })

        return signals

    def run(self, nav_series: list[dict], initial_cash: float) -> dict:
        """重写 run：核心仓位买入持有，卫星仓位按信号调仓。"""
        if not nav_series:
            return _empty_result(initial_cash)

        core_ratio = float(self.params.get("core_ratio", 0.6))
        first_nav = nav_series[0]["nav"]
        if first_nav <= 0:
            return _empty_result(initial_cash)

        # 核心仓位买入持有
        core_shares = (initial_cash * core_ratio) / first_nav
        # 卫星仓位初始为现金
        sat_cash = initial_cash * (1 - core_ratio)
        sat_shares = 0.0
        total_invested = initial_cash
        trades = 1
        nav_curve: list[dict] = []

        signals = self.generate_signals(nav_series)
        sig_by_date: dict[str, list[dict]] = {}
        for sig in signals:
            sig_by_date.setdefault(sig["date"], []).append(sig)

        for point in nav_series:
            d = point["date"]
            nav = point["nav"]
            if nav <= 0:
                continue

            # 执行卫星仓位信号
            for sig in sig_by_date.get(d, []):
                sig_type = sig.get("type")
                amount = float(sig.get("amount", 0))
                if sig_type == "buy" and sat_cash >= amount:
                    sat_shares += amount / nav
                    sat_cash -= amount
                    trades += 1
                elif sig_type == "sell" and sat_shares > 0:
                    sell_shares = min(sat_shares, amount / nav)
                    sat_cash += sell_shares * nav
                    sat_shares -= sell_shares
                    trades += 1

            total_value = core_shares * nav + sat_cash + sat_shares * nav
            nav_curve.append({"date": d, "value": round(total_value, 2)})

        return _build_result(self.name, nav_series, nav_curve, total_invested, trades)


# ══════════════════════════════════════════════════════
# 策略注册表 + 模板描述
# ══════════════════════════════════════════════════════

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "dca": DCAStrategy,
    "grid": GridStrategy,
    "two_eight": TwoEightStrategy,
    "core_satellite": CoreSatelliteStrategy,
}

# 策略模板描述（供 API /list 返回，便于前端渲染参数表单）
STRATEGY_TEMPLATES = [
    {
        "name": "dca",
        "label": "定投策略",
        "description": "按固定间隔买入，支持固定/均线/估值三种触发方式",
        "params": {
            "interval_days": {"type": "int", "default": 30, "description": "定投间隔天数"},
            "amount": {"type": "float", "default": 1000, "description": "每次定投金额"},
            "trigger": {
                "type": "str", "default": "fixed",
                "options": ["fixed", "ma", "valuation"],
                "description": "触发方式：fixed=固定, ma=均线交叉, valuation=估值分位",
            },
        },
    },
    {
        "name": "grid",
        "label": "网格策略",
        "description": "价格每跌一格加仓，每涨一格减仓",
        "params": {
            "grid_steps": {"type": "int", "default": 10, "description": "网格数"},
            "grid_pct": {"type": "float", "default": 0.05, "description": "每格涨跌幅"},
            "base_amount": {"type": "float", "default": 5000, "description": "基础仓位金额"},
        },
    },
    {
        "name": "two_eight",
        "label": "二八股债平衡",
        "description": "每月再平衡到 80% 股 20% 债",
        "params": {
            "rebalance_day": {"type": "int", "default": 1, "description": "每月再平衡日"},
            "equity_ratio": {"type": "float", "default": 0.8, "description": "股票目标比例"},
            "bond_return_annual": {"type": "float", "default": 0.03, "description": "债券年化收益率"},
        },
    },
    {
        "name": "core_satellite",
        "label": "核心卫星策略",
        "description": "核心仓位持有不动，卫星仓位按均线信号调仓",
        "params": {
            "core_ratio": {"type": "float", "default": 0.6, "description": "核心仓位比例"},
            "core_code": {"type": "str", "default": "000300", "description": "核心指数代码"},
            "satellites": {"type": "list", "default": [], "description": "卫星列表"},
            "ma_short": {"type": "int", "default": 20, "description": "短期均线窗口"},
            "ma_long": {"type": "int", "default": 60, "description": "长期均线窗口"},
        },
    },
]


# ══════════════════════════════════════════════════════
# 回测引擎
# ══════════════════════════════════════════════════════

def _fetch_nav_series(target_code: str, start_date: str | None = None,
                      end_date: str | None = None) -> list[dict]:
    """从 db.valuations 取历史净值序列（按日期升序）。

    将 index_valuations 表的 current_point 作为 nav，percentile 用于估值触发。

    Returns:
        [{"date": str, "nav": float, "percentile": float|None, "pe": float|None}, ...]
    """
    rows = get_valuation_history(target_code, days=3650, metric_type="市盈率")
    if not rows:
        return []

    series: list[dict] = []
    for r in rows:
        d = r.get("snapshot_date")
        nav = r.get("current_point")
        if not d or nav is None:
            continue
        try:
            nav = float(nav)
        except (TypeError, ValueError):
            continue
        if nav <= 0:
            continue
        # 日期过滤
        if start_date and str(d) < start_date:
            continue
        if end_date and str(d) > end_date:
            continue
        pct = r.get("percentile")
        pe = r.get("current_value")
        series.append({
            "date": str(d),
            "nav": nav,
            "percentile": float(pct) if pct is not None else None,
            "pe": float(pe) if pe is not None else None,
        })

    # 按日期升序
    series.sort(key=lambda x: x["date"])
    return series


def run_backtest(strategy_name: str, target_code: str, params: dict,
                 start_date: str | None = None, end_date: str | None = None,
                 initial_cash: float = 100000) -> dict:
    """运行单次回测。

    流程：
      1. 从 db.valuations 取历史净值
      2. 实例化策略类
      3. 调 strategy.run() 跑回测
      4. 计算指标（total_return / annual_return / max_drawdown / sharpe_ratio）
      5. 保存到 backtest_results 表
      6. 返回结果 + 净值曲线 + 信号

    Args:
        strategy_name: 策略名称（dca / grid / two_eight / core_satellite）
        target_code:   目标指数代码
        params:        策略参数
        start_date:    起始日期（YYYY-MM-DD），可选
        end_date:      结束日期，可选
        initial_cash:  初始资金

    Returns:
        回测结果 dict。数据不足时返回 {"status": "error", ...}。
    """
    # 1. 取历史净值
    nav_series = _fetch_nav_series(target_code, start_date, end_date)
    if len(nav_series) < 2:
        return {
            "status": "error",
            "error": f"数据不足：{target_code} 仅有 {len(nav_series)} 条记录，至少需要 2 条",
            "nav_series_count": len(nav_series),
        }

    # 2. 实例化策略
    strategy_cls = STRATEGY_REGISTRY.get(strategy_name)
    if strategy_cls is None:
        return {
            "status": "error",
            "error": f"未知策略: {strategy_name}，可选: {list(STRATEGY_REGISTRY.keys())}",
        }
    strategy = strategy_cls(params)

    # 3. 跑回测
    result = strategy.run(nav_series, initial_cash)

    # 补充元信息
    result["status"] = "ok"
    result["target_code"] = target_code
    result["strategy_name"] = strategy_name
    result["params"] = params
    result["start_date"] = nav_series[0]["date"]
    result["end_date"] = nav_series[-1]["date"]
    result["data_points"] = len(nav_series)
    result["signals"] = strategy.generate_signals(nav_series)

    # 4-5. 保存到 backtest_results 表
    try:
        name = f"{strategy_name}_{target_code}_{nav_series[-1]['date']}"
        benchmark = {"total_return": 0}
        months = max(1, result.get("days", 0) // 30)
        backtest_id = save_backtest(
            name=name,
            target_code=target_code,
            target_type="index",
            strategy=strategy_name,
            params=params,
            result=result,
            benchmark=benchmark,
            months=months,
        )
        result["backtest_id"] = backtest_id
    except Exception as e:
        logger.warning(f"保存回测结果失败: {e}")

    return result


def parameter_sweep(strategy_name: str, target_code: str,
                    param_ranges: dict, start_date: str | None = None,
                    end_date: str | None = None,
                    initial_cash: float = 100000) -> list[dict]:
    """参数扫描：遍历参数组合，各跑一次回测，按 sharpe_ratio 降序返回。

    Args:
        strategy_name: 策略名称
        target_code:   目标指数代码
        param_ranges:  参数范围 {"param_name": [val1, val2, ...]}
        start_date:    起始日期
        end_date:      结束日期
        initial_cash:  初始资金

    Returns:
        各参数组合的回测结果列表。数据不足时返回空列表。
    """
    # 取数据一次（避免重复查询）
    nav_series = _fetch_nav_series(target_code, start_date, end_date)
    if len(nav_series) < 2:
        return []

    strategy_cls = STRATEGY_REGISTRY.get(strategy_name)
    if strategy_cls is None:
        return []

    # 展开参数组合
    combos = _expand_param_combinations(param_ranges)
    if not combos:
        combos = [{}]

    results: list[dict] = []
    for combo in combos:
        strategy = strategy_cls(combo)
        result = strategy.run(nav_series, initial_cash)
        result["params"] = combo
        result["strategy_name"] = strategy_name
        result["target_code"] = target_code
        results.append(result)

    # 按 sharpe_ratio 降序
    results.sort(key=lambda x: x.get("sharpe_ratio", 0), reverse=True)
    return results


def _expand_param_combinations(param_ranges: dict) -> list[dict]:
    """展开参数范围为笛卡尔积。

    Args:
        param_ranges: {"param_name": [val1, val2, ...], ...}
                      单值也会被包装成列表

    Returns:
        [{"param_name": val, ...}, ...]
    """
    if not param_ranges:
        return []

    keys = list(param_ranges.keys())
    value_lists = [
        param_ranges[k] if isinstance(param_ranges[k], list) else [param_ranges[k]]
        for k in keys
    ]

    combos: list[dict] = []

    def _recurse(idx: int, current: dict):
        if idx == len(keys):
            combos.append(dict(current))
            return
        for val in value_lists[idx]:
            current[keys[idx]] = val
            _recurse(idx + 1, current)
            current.pop(keys[idx], None)

    _recurse(0, {})
    return combos


# ── 策略模板清单 ──────────────────────────────────

_STRATEGY_TEMPLATES = [
    {
        "name": "dca",
        "label": "定投策略",
        "description": "按固定周期买入，可选均线/估值触发加倍",
        "params": {
            "interval_days": {"type": "int", "default": 7, "description": "定投间隔天数"},
            "amount": {"type": "float", "default": 1000, "description": "每次定投金额"},
            "trigger": {"type": "str", "default": "fixed", "options": ["fixed", "ma", "valuation"], "description": "触发方式"},
        },
    },
    {
        "name": "grid",
        "label": "网格策略",
        "description": "价格每跌一格加仓，每涨一格减仓，适合震荡市",
        "params": {
            "grid_steps": {"type": "int", "default": 5, "description": "网格层数"},
            "grid_pct": {"type": "float", "default": 0.05, "description": "每格涨跌幅"},
            "base_amount": {"type": "float", "default": 5000, "description": "基础仓位金额"},
        },
    },
    {
        "name": "two_eight",
        "label": "二八股债平衡",
        "description": "80% 股票 + 20% 债券，每月再平衡",
        "params": {
            "equity_ratio": {"type": "float", "default": 0.8, "description": "股票比例"},
            "rebalance_day": {"type": "int", "default": 1, "description": "每月再平衡日"},
        },
    },
    {
        "name": "core_satellite",
        "label": "核心卫星策略",
        "description": "核心仓位持有不动，卫星仓位按信号调仓",
        "params": {
            "core_ratio": {"type": "float", "default": 0.6, "description": "核心仓位比例"},
            "core_code": {"type": "str", "default": "000300", "description": "核心指数代码"},
        },
    },
]


def list_strategies() -> list[dict]:
    """返回所有策略模板清单。"""
    return _STRATEGY_TEMPLATES