"""主题机会回看统计测试。"""

from datetime import datetime

from db import (
    create_holding,
    mark_opportunity_bought,
    save_opportunity,
    list_transactions,
)
from db.opportunities import get_opportunity_track_stats


def test_opportunity_track_stats_can_compute_positive_return(tmp_db):
    today = datetime.now().strftime("%Y-%m-%d")
    holding_id = create_holding(
        fund_code="000998",
        fund_name="测试机会基金",
        shares=100,
        cost_price=1.0,
        current_price=1.2,
        user_id="default",
    )
    tx_id = list_transactions(holding_id=holding_id, user_id="default", include_system=True, limit=1)[0]["id"]
    opportunity_id = save_opportunity({
        "trade_date": today,
        "theme": "测试主题",
        "verdict": "can_buy",
        "opportunity_score": 88,
        "summary": "测试主题机会",
        "policy_signal": "测试政策",
        "future_direction": "测试方向",
        "market_signal": "测试市场信号",
        "valuation_role": "测试估值",
        "portfolio_fit": {},
        "matched_funds": [{
            "fund_code": "000998",
            "fund_name": "测试机会基金",
            "index_name": "测试指数",
            "vehicle_type": "etf",
            "short_term_suitable": True,
        }],
        "entry_plan": {"action": "小仓试投", "amount": 100},
        "exit_plan": {"review_date": today},
        "risk_note": "测试风险",
        "evidence": [],
        "status": "watching",
    })

    track_id = mark_opportunity_bought(
        opportunity_id=opportunity_id,
        fund_code="000998",
        amount=100,
        transaction_id=tx_id,
    )

    stats = get_opportunity_track_stats(user_id="default", limit=5)

    assert track_id > 0
    assert stats["bought_tracks"] >= 1
    assert stats["evaluated_tracks"] >= 1
    assert stats["hit_rate"] == 100.0
    assert stats["recent_items"][0]["theme"] == "测试主题"
