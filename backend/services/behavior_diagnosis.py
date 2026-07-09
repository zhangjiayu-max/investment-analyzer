"""行为金融诊断模块 — 量化 4 类投资者行为偏差

诊断维度：
1. 处置效应（Disposition Effect）：PGR - PLR，衡量"卖出盈利、持有亏损"倾向
2. 锚定效应（Anchoring）：亏损后平均持有天数 + 亏损状态下加仓比例
3. 羊群效应（Herding）：近 30 天净值高点后买入延迟 + 热门基金占比
4. 过度交易（Overtrading）：年化换手率 + 平均持有天数 + 成本占比

纯算法实现，不调 LLM。数据不足时返回 score=0 + detail="数据不足"。
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from db import list_transactions, list_holdings, get_portfolio_summary

logger = logging.getLogger(__name__)


# ── 工具函数 ──────────────────────────────────────────────
def _parse_date(s) -> Optional[datetime]:
    """解析日期字符串（取前 10 位 YYYY-MM-DD），失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except Exception:
        return None


def _clamp01(x: float) -> float:
    """归一化到 [0, 1]。"""
    if x is None:
        return 0.0
    return max(0.0, min(1.0, float(x)))


def _safe_div(num: float, den: float) -> float:
    """安全除法，分母为 0 返回 0。"""
    return num / den if den else 0.0


# ── 1. 处置效应 ──────────────────────────────────────────
def _diagnose_disposition(txs: list, holdings: list) -> dict:
    """处置效应：PGR - PLR。

    PGR = 卖出盈利数 / (卖出盈利数 + 持有盈利数)
    PLR = 卖出亏损数 / (卖出亏损数 + 持有亏损数)
    DE = PGR - PLR（>0 表示处置效应严重）
    """
    # 持仓端：用 profit_rate 判定盈亏（仅持有份额>0的）
    winners_held = 0
    losers_held = 0
    for h in holdings:
        if (h.get("shares") or 0) <= 0:
            continue
        pr = h.get("profit_rate")
        if pr is None:
            continue
        if pr > 0:
            winners_held += 1
        elif pr < 0:
            losers_held += 1

    # 交易端：按基金分组，按时间升序模拟，遇到卖出时用累计均价判定盈亏
    fund_txs = defaultdict(list)
    for t in txs:
        fc = t.get("fund_code")
        if fc:
            fund_txs[fc].append(t)
    for fc in fund_txs:
        fund_txs[fc].sort(key=lambda x: (x.get("transaction_date", ""), x.get("id", 0)))

    sell_wins = 0
    sell_losses = 0
    for fc, ft in fund_txs.items():
        cum_shares = 0.0
        cum_cost = 0.0
        for t in ft:
            tt = t.get("transaction_type")
            shares = t.get("shares") or 0
            price = t.get("price") or 0
            if tt == "buy":
                cum_shares += shares
                cum_cost += shares * price
            elif tt == "sell":
                if cum_shares > 0 and price > 0:
                    avg_buy = cum_cost / cum_shares
                    if price > avg_buy:
                        sell_wins += 1
                    elif price < avg_buy:
                        sell_losses += 1
                    # FIFO 平均扣减
                    ratio = min(shares / cum_shares, 1.0)
                    cum_cost -= cum_cost * ratio
                    cum_shares -= shares
                    if cum_shares < 0:
                        cum_shares = 0.0
                        cum_cost = 0.0

    pgr_den = sell_wins + winners_held
    plr_den = sell_losses + losers_held
    if pgr_den == 0 or plr_den == 0:
        return {"score": 0.0, "pgr": 0.0, "plr": 0.0, "detail": "数据不足"}

    pgr = sell_wins / pgr_den
    plr = sell_losses / plr_den
    de = pgr - plr
    # DE ∈ [-1, 1]，>0 表示处置效应；归一化到 0-1
    score = _clamp01(de)
    detail = (
        f"PGR={pgr:.2f}(卖出盈利{sell_wins}笔/盈利仓{pgr_den})，"
        f"PLR={plr:.2f}(卖出亏损{sell_losses}笔/亏损仓{plr_den})，"
        f"DE={de:+.2f}"
    )
    return {"score": round(score, 3), "pgr": round(pgr, 3), "plr": round(plr, 3), "detail": detail}


# ── 2. 锚定效应 ──────────────────────────────────────────
def _diagnose_anchoring(txs: list, holdings: list, today: datetime) -> dict:
    """锚定效应：亏损后平均持有天数 + 亏损状态下加仓比例。"""
    # 亏损后平均持有天数：当前仍持有且处于亏损的持仓，从买入日到今天
    hold_since_loss_days = []
    for h in holdings:
        if (h.get("shares") or 0) <= 0:
            continue
        pr = h.get("profit_rate")
        if pr is None or pr >= 0:
            continue
        buy_date = _parse_date(h.get("buy_date"))
        if not buy_date:
            continue
        hold_since_loss_days.append((today - buy_date).days)

    # 亏损状态下加仓比例：每只基金按时间升序遍历买入，若加仓价低于此前均价则视为摊薄成本
    fund_buys = defaultdict(list)
    for t in txs:
        if t.get("transaction_type") != "buy":
            continue
        fc = t.get("fund_code")
        if fc:
            fund_buys[fc].append(t)
    for fc in fund_buys:
        fund_buys[fc].sort(key=lambda x: (x.get("transaction_date", ""), x.get("id", 0)))

    add_in_loss = 0
    add_total = 0
    for fc, buys in fund_buys.items():
        cum_shares = 0.0
        cum_cost = 0.0
        for b in buys:
            shares = b.get("shares") or 0
            price = b.get("price") or 0
            if cum_shares > 0 and price > 0:
                # 非首次买入，判断是否摊薄（低于此前均价）
                avg_before = cum_cost / cum_shares
                add_total += 1
                if price < avg_before:
                    add_in_loss += 1
            cum_shares += shares
            cum_cost += shares * price

    if not hold_since_loss_days and add_total == 0:
        return {"score": 0.0, "avg_hold_since_loss": 0, "cost_reference_ratio": 0.0, "detail": "数据不足"}

    avg_hold = sum(hold_since_loss_days) / len(hold_since_loss_days) if hold_since_loss_days else 0
    cost_ref_ratio = add_in_loss / add_total if add_total > 0 else 0.0

    # 评分：持有越久越严重（180天封顶）+ 加仓比例越高越严重
    score_hold = _clamp01(avg_hold / 180.0)
    score = 0.5 * score_hold + 0.5 * _clamp01(cost_ref_ratio)
    detail = (
        f"亏损持仓平均持有{avg_hold:.0f}天，"
        f"亏损状态下加仓{add_in_loss}/{add_total}笔（占比{cost_ref_ratio:.0%}）"
    )
    return {
        "score": round(score, 3),
        "avg_hold_since_loss": round(avg_hold, 1),
        "cost_reference_ratio": round(cost_ref_ratio, 3),
        "detail": detail,
    }


# ── 3. 羊群效应 ──────────────────────────────────────────
def _diagnose_herding(txs: list) -> dict:
    """羊群效应：近 30 天净值高点后买入延迟 + 近 7 天涨幅>5% 的买入占比。"""
    buy_txs = [t for t in txs if t.get("transaction_type") == "buy" and t.get("fund_code")]
    if not buy_txs:
        return {"score": 0.0, "avg_delay_days": 0, "hot_topic_buy_ratio": 0.0, "detail": "数据不足"}

    try:
        from services.market_data import get_fund_nav
    except Exception as e:
        logger.warning(f"导入 services.market_data.get_fund_nav 失败: {e}")
        return {"score": 0.0, "avg_delay_days": 0, "hot_topic_buy_ratio": 0.0, "detail": "数据不足"}

    # 按基金缓存净值历史，避免重复拉取
    nav_cache: dict[str, list] = {}

    def _get_nav_series(fund_code: str) -> list:
        """返回 [(date_str, nav), ...] 升序列表，失败返回 []。"""
        if fund_code in nav_cache:
            return nav_cache[fund_code]
        series = []
        try:
            df = get_fund_nav(fund_code)
            if df is None or len(df) == 0:
                nav_cache[fund_code] = []
                return []
            # 兼容单位净值/累计净值列
            date_col = "净值日期" if "净值日期" in df.columns else df.columns[0]
            nav_col = "单位净值" if "单位净值" in df.columns else ("累计净值" if "累计净值" in df.columns else df.columns[1])
            for _, row in df.iterrows():
                try:
                    d = str(row[date_col])[:10]
                    n = float(row[nav_col])
                    series.append((d, n))
                except Exception:
                    continue
            series.sort(key=lambda x: x[0])
        except Exception as e:
            logger.debug(f"获取 {fund_code} 净值失败: {e}")
        nav_cache[fund_code] = series
        return series

    delays = []
    hot_topic_count = 0
    analyzed = 0

    for t in buy_txs:
        buy_date_str = (t.get("transaction_date") or "")[:10]
        buy_dt = _parse_date(buy_date_str)
        if not buy_dt:
            continue
        series = _get_nav_series(t["fund_code"])
        if not series:
            continue

        # 找买入日及之前的净值
        before = [(d, n) for d, n in series if d <= buy_date_str]
        if len(before) < 2:
            continue
        buy_nav = before[-1][1]
        buy_idx = len(before) - 1

        # 近 30 天高点
        window_start_idx = max(0, buy_idx - 29)
        window = before[window_start_idx:buy_idx + 1]
        if not window:
            continue
        high_nav = max(n for _, n in window)
        high_date_str = next(d for d, n in window if n == high_nav)
        high_dt = _parse_date(high_date_str)
        if high_dt:
            delay = (buy_dt - high_dt).days
            delays.append(max(delay, 0))

        # 近 7 天涨幅
        seven_idx = max(0, buy_idx - 7)
        seven_window = before[seven_idx:buy_idx + 1]
        if len(seven_window) >= 2:
            start_nav = seven_window[0][1]
            if start_nav > 0:
                ret_7d = (buy_nav - start_nav) / start_nav
                if ret_7d > 0.05:
                    hot_topic_count += 1

        analyzed += 1

    if analyzed == 0 or not delays:
        return {"score": 0.0, "avg_delay_days": 0, "hot_topic_buy_ratio": 0.0, "detail": "数据不足"}

    avg_delay = sum(delays) / len(delays)
    hot_ratio = hot_topic_count / analyzed

    # 评分：高点后延迟越短越严重（追高）+ 热门买入占比越高越严重
    score_delay = _clamp01((30.0 - avg_delay) / 30.0)
    score = 0.5 * score_delay + 0.5 * _clamp01(hot_ratio)
    detail = (
        f"分析{analyzed}笔买入，高点后平均延迟{avg_delay:.1f}天，"
        f"近7天涨幅>5%买入{hot_topic_count}笔（占比{hot_ratio:.0%}）"
    )
    return {
        "score": round(score, 3),
        "avg_delay_days": round(avg_delay, 1),
        "hot_topic_buy_ratio": round(hot_ratio, 3),
        "detail": detail,
    }


# ── 4. 过度交易 ──────────────────────────────────────────
def _diagnose_overtrading(txs: list, holdings: list, period_days: int, today: datetime) -> dict:
    """过度交易：年化换手率 + 平均持有天数 + 成本占比。"""
    summary = get_portfolio_summary()
    total_value = summary.get("total_value", 0) or 0
    total_cost = summary.get("total_cost", 0) or 0

    # 卖出金额合计
    sell_amounts = sum((t.get("amount") or 0) for t in txs if t.get("transaction_type") == "sell")
    # 手续费合计
    total_fee = sum((t.get("fee") or 0) for t in txs)

    # 平均市值：用 (当前市值 + 总成本) / 2 近似
    avg_market_value = (total_value + total_cost) / 2.0
    if avg_market_value <= 0:
        return {"score": 0.0, "turnover_rate": 0.0, "avg_hold_days": 0, "cost_ratio": 0.0, "detail": "数据不足"}

    # 年化换手率
    annual_factor = 365.0 / max(period_days, 1)
    turnover_rate = _safe_div(sell_amounts, avg_market_value) * annual_factor

    # 平均持有天数：按基金 FIFO 匹配卖出与买入
    fund_txs = defaultdict(list)
    for t in txs:
        fc = t.get("fund_code")
        if fc:
            fund_txs[fc].append(t)
    for fc in fund_txs:
        fund_txs[fc].sort(key=lambda x: (x.get("transaction_date", ""), x.get("id", 0)))

    hold_days_list = []
    for fc, ft in fund_txs.items():
        buy_queue = []  # [{shares, date}]
        for t in ft:
            tt = t.get("transaction_type")
            shares = t.get("shares") or 0
            date_str = t.get("transaction_date", "")
            if tt == "buy":
                buy_queue.append({"shares": shares, "date": date_str})
            elif tt == "sell":
                remaining = shares
                while remaining > 0 and buy_queue:
                    lot = buy_queue[0]
                    if lot["shares"] <= remaining:
                        d_buy = _parse_date(lot["date"])
                        d_sell = _parse_date(date_str)
                        if d_buy and d_sell:
                            hold_days_list.append((d_sell - d_buy).days)
                        remaining -= lot["shares"]
                        buy_queue.pop(0)
                    else:
                        d_buy = _parse_date(lot["date"])
                        d_sell = _parse_date(date_str)
                        if d_buy and d_sell:
                            hold_days_list.append((d_sell - d_buy).days)
                        lot["shares"] -= remaining
                        remaining = 0

    avg_hold_days = sum(hold_days_list) / len(hold_days_list) if hold_days_list else 0.0
    cost_ratio = _safe_div(total_fee, total_value)

    if sell_amounts <= 0 and not hold_days_list:
        return {"score": 0.0, "turnover_rate": 0.0, "avg_hold_days": 0, "cost_ratio": 0.0, "detail": "数据不足"}

    # 评分：换手率越高越严重（5倍封顶）+ 持有越短越严重（90天基准）+ 成本占比越高越严重（5%封顶）
    score_turnover = _clamp01(turnover_rate / 5.0)
    score_hold = _clamp01(1.0 - avg_hold_days / 90.0)
    score_cost = _clamp01(cost_ratio / 0.05)
    score = (score_turnover + score_hold + score_cost) / 3.0

    detail = (
        f"年化换手率{turnover_rate:.2f}倍，平均持有{avg_hold_days:.0f}天，"
        f"成本占比{cost_ratio:.2%}（手续费{total_fee:.0f}/市值{total_value:.0f}）"
    )
    return {
        "score": round(score, 3),
        "turnover_rate": round(turnover_rate, 3),
        "avg_hold_days": round(avg_hold_days, 1),
        "cost_ratio": round(cost_ratio, 4),
        "detail": detail,
    }


# ── 综合诊断 ─────────────────────────────────────────────
def _build_suggestions(disp: dict, anchor: dict, herd: dict, over: dict) -> list[str]:
    """根据各项偏差分生成针对性建议。"""
    suggestions = []
    if disp.get("score", 0) > 0.4:
        suggestions.append("存在明显处置效应，倾向「卖出盈利、持有亏损」，建议为每笔持仓预设止盈止损线并严格执行")
    if anchor.get("score", 0) > 0.4:
        suggestions.append("存在锚定效应，亏损后长期持有且不断加仓摊薄成本，建议设定最大补仓次数与单只基金持有期限上限")
    if herd.get("score", 0) > 0.4:
        suggestions.append("存在羊群效应，倾向在热点高位追涨买入，建议买入前检查近 30 天净值高点延迟，避免追高")
    if over.get("score", 0) > 0.4:
        suggestions.append("存在过度交易，换手率偏高、持有周期偏短，建议降低交易频率、拉长持有周期以减少手续费损耗")
    if not suggestions:
        suggestions.append("各项行为偏差均在合理范围内，请继续保持纪律性投资")
    return suggestions


def diagnose_behavior(user_id: str = "default", period_days: int = 90) -> dict:
    """量化诊断 4 类行为偏差，返回综合报告。

    参数:
        user_id: 用户 ID
        period_days: 回溯天数（默认 90 天）

    返回: 见模块头部的返回结构示例。
    """
    today = datetime.now()
    end_date = today.strftime("%Y-%m-%d")
    start_date = (today - timedelta(days=period_days)).strftime("%Y-%m-%d")

    try:
        txs = list_transactions(
            user_id=user_id, start_date=start_date, end_date=end_date,
            limit=10000, include_system=False,
        )
    except Exception as e:
        logger.error(f"获取交易记录失败: {e}")
        txs = []
    try:
        holdings = list_holdings(user_id)
    except Exception as e:
        logger.error(f"获取持仓失败: {e}")
        holdings = []

    # 4 类偏差独立诊断，互不影响
    try:
        disp = _diagnose_disposition(txs, holdings)
    except Exception as e:
        logger.error(f"处置效应诊断异常: {e}")
        disp = {"score": 0.0, "pgr": 0.0, "plr": 0.0, "detail": f"诊断异常: {e}"}

    try:
        anchor = _diagnose_anchoring(txs, holdings, today)
    except Exception as e:
        logger.error(f"锚定效应诊断异常: {e}")
        anchor = {"score": 0.0, "avg_hold_since_loss": 0, "cost_reference_ratio": 0.0, "detail": f"诊断异常: {e}"}

    try:
        herd = _diagnose_herding(txs)
    except Exception as e:
        logger.error(f"羊群效应诊断异常: {e}")
        herd = {"score": 0.0, "avg_delay_days": 0, "hot_topic_buy_ratio": 0.0, "detail": f"诊断异常: {e}"}

    try:
        over = _diagnose_overtrading(txs, holdings, period_days, today)
    except Exception as e:
        logger.error(f"过度交易诊断异常: {e}")
        over = {"score": 0.0, "turnover_rate": 0.0, "avg_hold_days": 0, "cost_ratio": 0.0, "detail": f"诊断异常: {e}"}

    overall_score = (
        disp.get("score", 0) + anchor.get("score", 0) +
        herd.get("score", 0) + over.get("score", 0)
    ) / 4.0

    return {
        "disposition_effect": disp,
        "anchoring_effect": anchor,
        "herding_effect": herd,
        "overtrading": over,
        "overall_score": round(overall_score, 3),
        "suggestions": _build_suggestions(disp, anchor, herd, over),
    }


def get_behavior_score(user_id: str = "default") -> float:
    """综合偏差分（用于 Dashboard），越高越严重。"""
    try:
        return float(diagnose_behavior(user_id=user_id).get("overall_score", 0.0))
    except Exception as e:
        logger.error(f"获取行为偏差分失败: {e}")
        return 0.0
