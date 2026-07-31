"""用户上下文结合器 — 结合持仓/关注列表生成推荐分级。

推荐分级矩阵：
| 利好程度 | 低估 | 合理 | 偏高 | 高估 | 无估值 |
| 强利好 | strong_buy | watch | observe_only | observe_only | watch |
| 中利好 | watch | watch | observe_only | observe_only | observe_only |
| 弱利好 | observe_only | observe_only | observe_only | observe_only | observe_only |

持仓补仓例外：已持仓 + profit_rate < -0.15 + 低估 → add_position
持仓集中度风险：已持仓 + theme_exposure_pct > 0.10 → observe_only

match_score 计算（0-100）：
- benefit_level: strong=40 / medium=25 / weak=10
- valuation_status: undervalued=30 / fair=20 / overvalued=10 / expensive=0 / unknown=15
- is_watching: +10
- is_holding: +5
总分上限 100

总开关：alerts.context_integrator_enabled（默认 false）
"""
import logging

from db.config import get_config_bool
from db import list_holdings, get_portfolio_summary
from db.watchlist import list_watchlist

logger = logging.getLogger(__name__)

_CONTEXT_INTEGRATOR_SWITCH = "alerts.context_integrator_enabled"

# 推荐矩阵：[benefit_level][valuation_status] -> tier
_MATRIX = {
    "strong": {
        "undervalued": "strong_buy",
        "fair": "watch",
        "overvalued": "observe_only",
        "expensive": "observe_only",
        "unknown": "watch",
    },
    "medium": {
        "undervalued": "watch",
        "fair": "watch",
        "overvalued": "observe_only",
        "expensive": "observe_only",
        "unknown": "observe_only",
    },
    "weak": {
        "undervalued": "observe_only",
        "fair": "observe_only",
        "overvalued": "observe_only",
        "expensive": "observe_only",
        "unknown": "observe_only",
    },
}

# match_score 权重
_SCORE_BENEFIT = {"strong": 40, "medium": 25, "weak": 10}
_SCORE_VALUATION = {
    "undervalued": 30,
    "fair": 20,
    "overvalued": 10,
    "expensive": 0,
    "unknown": 15,
}

# 集中度风险阈值（同指数持仓市值占总资产比例）
_CONCENTRATION_THRESHOLD = 0.10
# 补仓亏损阈值（profit_rate < -0.15 触发补仓例外）
_ADD_POSITION_LOSS_THRESHOLD = -0.15


def apply_user_context(beneficiaries: list[dict], user_id: str = "default") -> list[dict]:
    """结合持仓和关注列表生成推荐分级。

    推荐分级矩阵：
    | 利好程度 | 低估 | 合理 | 偏高 | 高估 | 无估值 |
    | 强利好 | strong_buy | watch | observe_only | observe_only | watch |
    | 中利好 | watch | watch | observe_only | observe_only | observe_only |
    | 弱利好 | observe_only | observe_only | observe_only | observe_only | observe_only |

    持仓补仓例外：已持仓 + profit_rate < -0.15 + 低估 → add_position
    持仓集中度风险：已持仓 + theme_exposure_pct > 0.10 → observe_only

    match_score 计算（0-100）：
    - benefit_level: strong=40 / medium=25 / weak=10
    - valuation_status: undervalued=30 / fair=20 / overvalued=10 / expensive=0 / unknown=15
    - is_watching: +10
    - is_holding: +5
    总分上限 100

    总开关：alerts.context_integrator_enabled（默认 false）
    """
    try:
        if not get_config_bool(_CONTEXT_INTEGRATOR_SWITCH, False):
            return beneficiaries
    except Exception:
        return beneficiaries

    if not beneficiaries:
        return beneficiaries

    # 加载持仓/关注/组合汇总
    try:
        holdings = list_holdings(user_id)
    except Exception as e:
        logger.warning(f"[context_integrator] 加载持仓失败: {e}")
        holdings = []
    try:
        watchlist = list_watchlist(user_id)
    except Exception as e:
        logger.warning(f"[context_integrator] 加载关注列表失败: {e}")
        watchlist = []
    try:
        summary = get_portfolio_summary(user_id)
    except Exception as e:
        logger.warning(f"[context_integrator] 加载组合汇总失败: {e}")
        summary = {}

    total_assets = summary.get("total_assets", 0) or 0

    # fund_code → 持仓 dict
    holding_map: dict[str, dict] = {}
    for h in holdings:
        code = h.get("fund_code", "")
        if code:
            holding_map[code] = h

    # index_code → 同指数持仓市值（用于集中度计算）
    index_value_map: dict[str, float] = {}
    for h in holdings:
        if (h.get("shares") or 0) <= 0:
            continue
        idx = h.get("index_code", "") or ""
        if not idx:
            continue
        index_value_map[idx] = index_value_map.get(idx, 0) + (h.get("current_value") or 0)

    # fund_code → 关注 dict
    watch_map: dict[str, dict] = {}
    for w in watchlist:
        code = w.get("fund_code", "")
        if code:
            watch_map[code] = w

    for b in beneficiaries:
        code = b.get("fund_code", "")
        is_holding = 1 if code in holding_map else 0
        is_watching = 1 if code in watch_map else 0
        b["is_holding"] = is_holding
        b["is_watching"] = is_watching

        benefit_level = b.get("benefit_level", "weak")
        valuation_status = b.get("valuation_status", "unknown") or "unknown"

        tier = (
            _MATRIX.get(benefit_level, _MATRIX["weak"])
            .get(valuation_status, "observe_only")
        )

        # 持仓补仓例外：已持仓 + 低估 + 亏损 > 15% → add_position
        if is_holding and valuation_status == "undervalued":
            h = holding_map.get(code, {})
            profit_rate = h.get("profit_rate", 0) or 0
            if profit_rate < _ADD_POSITION_LOSS_THRESHOLD:
                tier = "add_position"

        # 持仓集中度风险：已持仓 + 同指数占比 > 10% → observe_only
        if is_holding:
            idx = b.get("index_code", "") or ""
            if idx and total_assets > 0:
                exposure = index_value_map.get(idx, 0) / total_assets
                if exposure > _CONCENTRATION_THRESHOLD:
                    tier = "observe_only"

        b["recommendation_tier"] = tier
        b["match_score"] = _calc_score(benefit_level, valuation_status, is_holding, is_watching)

    return beneficiaries


def _calc_score(benefit_level: str, valuation_status: str, is_holding: int, is_watching: int) -> float:
    """计算 match_score（0-100，上限 100）。"""
    score = _SCORE_BENEFIT.get(benefit_level, 10)
    score += _SCORE_VALUATION.get(valuation_status, 15)
    if is_watching:
        score += 10
    if is_holding:
        score += 5
    return min(score, 100)
