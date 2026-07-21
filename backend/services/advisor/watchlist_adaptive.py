"""关注列表自适应阈值调整（P2-A）。

根据历史命中率动态调整 target_percentile：
- hit_rate >= 70%（高命中率）：放宽 10%（更多机会触发）
- hit_rate < 40%（低命中率）：收紧 20%（更难触发，避免误信号）
- 40% <= hit_rate < 70%：不调整
- reviewed < 5（样本不足）：不调整

闭合 P1-B 遗留断点：命中率仅反哺 confidence，未反哺到信号灯触发阈值。
"""
import logging

from db.config import get_config_bool, get_config_int
from db.watchlist import get_signal_backtest_stats

logger = logging.getLogger(__name__)


def apply_adaptive_threshold(fund_code: str, target_pct: float | None) -> tuple[float | None, str]:
    """根据历史命中率自适应调整 target_percentile 阈值。

    Args:
        fund_code: 基金代码
        target_pct: 原始目标百分位

    Returns:
        (adjusted_target_pct, adjustment_reason)
        - 未调整时 adjustment_reason 为空字符串
    """
    # 开关检查
    try:
        if not get_config_bool("watchlist.adaptive_threshold_enabled", True):
            return target_pct, ""
    except Exception:
        pass

    if not fund_code or target_pct is None:
        return target_pct, ""

    # 最小样本量
    try:
        min_samples = get_config_int("watchlist.adaptive_min_samples", 5)
    except Exception:
        min_samples = 5

    try:
        stats = get_signal_backtest_stats(fund_code=fund_code)
    except Exception as _e:
        logger.debug(f"[adaptive] 获取命中率统计失败 {fund_code}: {_e}")
        return target_pct, ""

    reviewed = stats.get("reviewed", 0) or 0
    if reviewed < min_samples:
        return target_pct, ""

    hit_rate = stats.get("hit_rate") or 0
    if hit_rate >= 70:
        # 高命中率：放宽 10%
        adjusted = round(target_pct * 1.1, 2)
        return adjusted, f"命中率 {hit_rate:.0f}% 偏高，阈值放宽至 {adjusted}%"
    elif hit_rate < 40:
        # 低命中率：收紧 20%
        adjusted = round(target_pct * 0.8, 2)
        return adjusted, f"命中率 {hit_rate:.0f}% 偏低，阈值收紧至 {adjusted}%"
    return target_pct, ""
