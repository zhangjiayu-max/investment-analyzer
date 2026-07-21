"""关注列表信号共振 + 持仓系统性风险检测 — P2-C（2026-07-21）

设计目的：
- 单标的信号灯只反映个体估值，无法捕捉"多标的同向"组合级信号
- 持仓端缺乏系统性风险预警，盈亏同质化（同跌同涨）无监控
- 关注列表多 green / 持仓多 red 是相反的共振信号，应联合呈现

核心能力：
1. detect_watchlist_resonance() — 关注列表多标的同向共振
   - strong / moderate / weak / none / strong_bearish 五级
   - bullish / bearish / neutral 三类
2. detect_holding_systemic_risk() — 持仓系统性风险预警
   - 大面积亏损（亏损持仓数 / 总持仓 >= 阈值）
   - 大面积单日下跌（今日跌幅 >3% 持仓数 / 总持仓 >= 阈值）
   - systemic_risk 布尔标志 + triggered_count

调用关系：
- /api/watchlist/resonance 路由调用本模块两个函数
- 数据源：routers.portfolio.watchlist._patrol_cache（缓存命中时）+ portfolio_holdings 表
- 失败时降级返回 none 级别，不抛异常
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_patrol_items() -> list[dict]:
    """从 router 的 patrol 缓存读取 items。

    缓存 TTL 由 router 模块控制（5 分钟）。
    缓存不可用时返回空列表，调用方需自行降级。
    """
    try:
        from routers.portfolio.watchlist import _patrol_cache, _patrol_cache_time
        import time as _time
        if _patrol_cache and _time.time() - _patrol_cache_time < 5 * 60:
            return _patrol_cache.get("all_items", []) or []
    except Exception as e:
        logger.debug(f"[wl_resonance] 读取 patrol 缓存失败: {e}")
    return []


def detect_watchlist_resonance() -> dict:
    """检测关注列表的信号共振级别。

    Returns:
        {
            "green_count": int,
            "yellow_count": int,
            "red_count": int,
            "gray_count": int,
            "total": int,
            "green_ratio": float,
            "red_ratio": float,
            "resonance_level": "strong" | "moderate" | "weak" | "none" | "strong_bearish",
            "resonance_type": "bullish" | "bearish" | "neutral",
            "alert_funds": list[dict],   # 触发强共振的基金列表（最多 5 个）
            "suggestion": str,
        }
    """
    # 读取配置阈值
    try:
        from db.config import get_config_int, get_config_float, get_config_bool
        strong_count_threshold = get_config_int("watchlist.resonance_strong_threshold", 3)
        strong_ratio_threshold = get_config_float("watchlist.resonance_ratio_threshold", 0.3)
        bearish_count_threshold = get_config_int("watchlist.resonance_bearish_count_threshold", 3)
        bearish_ratio_threshold = get_config_float("watchlist.resonance_bearish_ratio_threshold", 0.5)
        detection_enabled = get_config_bool("watchlist.resonance_detection_enabled", True)
    except Exception:
        strong_count_threshold = 3
        strong_ratio_threshold = 0.3
        bearish_count_threshold = 3
        bearish_ratio_threshold = 0.5
        detection_enabled = True

    empty_result = {
        "green_count": 0, "yellow_count": 0, "red_count": 0, "gray_count": 0,
        "total": 0, "green_ratio": 0.0, "red_ratio": 0.0,
        "resonance_level": "none", "resonance_type": "neutral",
        "alert_funds": [], "suggestion": "",
    }

    if not detection_enabled:
        empty_result["suggestion"] = "共振检测已关闭"
        return empty_result

    items = _get_patrol_items()
    if not items:
        empty_result["suggestion"] = "请先调用 /api/watchlist/patrol 触发巡检后再查看共振"
        return empty_result

    # 统计信号灯
    green_count = sum(1 for it in items if it.get("signal_status") == "green")
    yellow_count = sum(1 for it in items if it.get("signal_status") == "yellow")
    red_count = sum(1 for it in items if it.get("signal_status") == "red")
    gray_count = sum(1 for it in items if it.get("signal_status") == "gray")
    total = len(items)
    green_ratio = green_count / total if total > 0 else 0.0
    red_ratio = red_count / total if total > 0 else 0.0

    # 判定共振级别
    # 优先级：strong_bearish > strong > moderate > weak > none
    resonance_level = "none"
    resonance_type = "neutral"

    if (red_count >= bearish_count_threshold
            and red_ratio >= bearish_ratio_threshold):
        resonance_level = "strong_bearish"
        resonance_type = "bearish"
    elif (green_count >= strong_count_threshold
          and green_ratio >= strong_ratio_threshold):
        resonance_level = "strong"
        resonance_type = "bullish"
    elif green_count >= 2 and green_ratio >= 0.2:
        resonance_level = "moderate"
        resonance_type = "bullish"
    elif red_count >= 2 and red_ratio >= 0.3:
        resonance_level = "moderate"
        resonance_type = "bearish"
    elif green_count >= 1 or red_count >= 1:
        resonance_level = "weak"
        resonance_type = "bullish" if green_count >= red_count else "bearish"

    # 收集 alert_funds（强共振 / 强看空时才填充）
    alert_funds: list[dict] = []
    if resonance_level in ("strong", "strong_bearish"):
        target_status = "green" if resonance_type == "bullish" else "red"
        for it in items:
            if it.get("signal_status") == target_status:
                alert_funds.append({
                    "fund_code": it.get("fund_code"),
                    "fund_name": it.get("fund_name"),
                    "signal_status": it.get("signal_status"),
                    "signal_reason": it.get("signal_reason", ""),
                    "signal_confidence": it.get("signal_confidence"),
                    "current_percentile": it.get("current_percentile"),
                    "target_percentile": it.get("target_percentile"),
                })
                if len(alert_funds) >= 5:
                    break

    # 生成建议
    suggestion = _build_resonance_suggestion(
        resonance_level, resonance_type,
        green_count, red_count, yellow_count, gray_count, total,
    )

    return {
        "green_count": green_count,
        "yellow_count": yellow_count,
        "red_count": red_count,
        "gray_count": gray_count,
        "total": total,
        "green_ratio": round(green_ratio, 4),
        "red_ratio": round(red_ratio, 4),
        "resonance_level": resonance_level,
        "resonance_type": resonance_type,
        "alert_funds": alert_funds,
        "suggestion": suggestion,
    }


def _build_resonance_suggestion(level: str, rtype: str,
                                  green: int, red: int,
                                  yellow: int, gray: int, total: int) -> str:
    """根据共振级别生成操作建议。"""
    if level == "strong" and rtype == "bullish":
        return (f"关注列表 {green}/{total} 只标的同时触发 green 信号，"
                f"市场可能处于阶段低估区域，建议优先选择 signal_confidence ≥ 70 的标的分批介入；"
                f"避免一次性满仓，保留资金应对极端下行。")
    if level == "strong_bearish":
        return (f"关注列表 {red}/{total} 只标的同时触发 red 信号，"
                f"市场整体偏贵或趋势走弱，建议观望为主；"
                f"已持有同主题标的需审视是否减仓，避免高位接盘。")
    if level == "moderate" and rtype == "bullish":
        return (f"关注列表 {green} 只 green / {yellow} 只 yellow，"
                f"存在局部机会，可结合估值分位与置信度精选 1-2 只布局。")
    if level == "moderate" and rtype == "bearish":
        return (f"关注列表 {red} 只 red，局部高估，建议等待回调或选择其他主题。")
    if level == "weak":
        return (f"信号分散（green {green} / yellow {yellow} / red {red}），"
                f"无显著共振，按个股逻辑独立判断。")
    return "无共振信号，关注列表无显著同向标的。"


def detect_holding_systemic_risk() -> dict:
    """检测持仓端的系统性风险。

    判定规则：
    - 大面积亏损：profit_rate < -10% 的持仓数 / 总持仓 >= 0.4
    - 大面积单日下跌：today_change_pct < -3% 的持仓数 / 总持仓 >= 0.4
    - 两者满足其一即标记 systemic_risk=True

    Returns:
        {
            "total": int,
            "loss_count": int,                  # 亏损持仓数（profit_rate < 0）
            "severe_loss_count": int,           # 严重亏损持仓数（profit_rate <= -10%）
            "loss_ratio": float,
            "today_drop_count": int,            # 今日跌幅 >3% 持仓数
            "today_drop_ratio": float,
            "triggered_count": int,             # 触发系统性风险条件的持仓数
            "systemic_risk": bool,
            "triggered_holdings": list[dict],   # 触发风险的持仓详情（最多 5 个）
            "suggestion": str,
        }
    """
    try:
        from db.config import get_config_float, get_config_bool, get_config_int
        loss_threshold = get_config_float("watchlist.systemic_loss_threshold", -10.0)  # %
        drop_threshold = get_config_float("watchlist.systemic_drop_threshold", -3.0)    # %
        ratio_threshold = get_config_float("watchlist.systemic_ratio_threshold", 0.4)
        detection_enabled = get_config_bool("watchlist.resonance_detection_enabled", True)
        max_triggered_display = get_config_int("watchlist.systemic_max_display", 5)
    except Exception:
        loss_threshold = -10.0
        drop_threshold = -3.0
        ratio_threshold = 0.4
        detection_enabled = True
        max_triggered_display = 5

    empty_result = {
        "total": 0, "loss_count": 0, "severe_loss_count": 0,
        "loss_ratio": 0.0, "today_drop_count": 0, "today_drop_ratio": 0.0,
        "triggered_count": 0, "systemic_risk": False,
        "triggered_holdings": [], "suggestion": "",
    }

    if not detection_enabled:
        empty_result["suggestion"] = "系统性风险检测已关闭"
        return empty_result

    try:
        from db.portfolio import list_holdings
        holdings = list_holdings()
    except Exception as e:
        logger.debug(f"[wl_resonance] 读取持仓失败: {e}")
        empty_result["suggestion"] = "持仓读取失败"
        return empty_result

    if not holdings:
        empty_result["suggestion"] = "暂无持仓，无系统性风险"
        return empty_result

    total = len(holdings)
    loss_count = 0
    severe_loss_count = 0
    today_drop_count = 0
    triggered_holdings: list[dict] = []

    for h in holdings:
        profit_rate = h.get("profit_rate")
        today_change = h.get("today_change_pct")

        is_loss = False
        is_severe_loss = False
        is_today_drop = False

        if profit_rate is not None:
            if profit_rate < 0:
                loss_count += 1
                is_loss = True
            if profit_rate <= loss_threshold:
                severe_loss_count += 1
                is_severe_loss = True

        if today_change is not None and today_change <= drop_threshold:
            today_drop_count += 1
            is_today_drop = True

        # 触发条件：严重亏损 或 今日大幅下跌
        if is_severe_loss or is_today_drop:
            triggered_holdings.append({
                "fund_code": h.get("fund_code"),
                "fund_name": h.get("fund_name"),
                "profit_rate": profit_rate,
                "today_change_pct": today_change,
                "current_value": h.get("current_value"),
                "trigger_reason": _build_trigger_reason(is_severe_loss, is_today_drop,
                                                        profit_rate, today_change,
                                                        loss_threshold, drop_threshold),
            })

    loss_ratio = loss_count / total if total > 0 else 0.0
    severe_loss_ratio = severe_loss_count / total if total > 0 else 0.0
    today_drop_ratio = today_drop_count / total if total > 0 else 0.0

    # 系统性风险判定
    systemic_risk = (severe_loss_ratio >= ratio_threshold
                     or today_drop_ratio >= ratio_threshold)

    # 限制返回数量
    triggered_holdings = triggered_holdings[:max_triggered_display]

    suggestion = _build_systemic_suggestion(
        systemic_risk, total, severe_loss_count, today_drop_count,
        severe_loss_ratio, today_drop_ratio, ratio_threshold,
    )

    return {
        "total": total,
        "loss_count": loss_count,
        "severe_loss_count": severe_loss_count,
        "loss_ratio": round(loss_ratio, 4),
        "today_drop_count": today_drop_count,
        "today_drop_ratio": round(today_drop_ratio, 4),
        "triggered_count": len(triggered_holdings),
        "systemic_risk": systemic_risk,
        "triggered_holdings": triggered_holdings,
        "suggestion": suggestion,
    }


def _build_trigger_reason(is_severe_loss: bool, is_today_drop: bool,
                          profit_rate: Any, today_change: Any,
                          loss_threshold: float, drop_threshold: float) -> str:
    """构造单只持仓触发原因。"""
    reasons = []
    if is_severe_loss and profit_rate is not None:
        reasons.append(f"亏损 {profit_rate:.1f}% ≤ {loss_threshold:.0f}%")
    if is_today_drop and today_change is not None:
        reasons.append(f"今日 {today_change:.1f}% ≤ {drop_threshold:.0f}%")
    return "；".join(reasons) if reasons else ""


def _build_systemic_suggestion(systemic: bool, total: int,
                                severe_loss: int, today_drop: int,
                                severe_ratio: float, today_ratio: float,
                                threshold: float) -> str:
    """根据系统性风险判定生成操作建议。"""
    if systemic:
        parts = []
        if severe_ratio >= threshold:
            parts.append(f"严重亏损（≤-10%）持仓 {severe_loss}/{total}（{severe_ratio*100:.0f}%）")
        if today_ratio >= threshold:
            parts.append(f"今日大幅下跌（≤-3%）持仓 {today_drop}/{total}（{today_ratio*100:.0f}%）")
        return (
            "⚠️ 检测到系统性风险：" + "，".join(parts) +
            "。建议：(1) 审视持仓是否同质化过高；(2) 优先减仓亏损最大且基本面恶化的标的；"
            "(3) 保留底仓等待市场企稳；(4) 关注是否与关注列表 red 共振（双重看空信号）。"
        )
    if severe_loss > 0 or today_drop > 0:
        return (f"存在个别风险持仓（严重亏损 {severe_loss} 只 / 今日大跌 {today_drop} 只），"
                f"未达系统性风险阈值（{threshold*100:.0f}%），按个股逻辑独立处理。")
    return "持仓整体稳健，无系统性风险信号。"
