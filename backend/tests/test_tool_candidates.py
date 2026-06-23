"""理财工具输出进入建议候选池测试。"""

from db import create_holding
from db.decisions import list_recommendation_candidates


def test_four_pots_result_creates_cash_reserve_candidate(tmp_db):
    from routers.analysis.four_pots import save_four_pots_candidates

    result = {
        "advice": ["活钱管理占比过低（3%），建议保留3-6个月生活费"],
        "total_value": 100000,
        "pots": {"活钱管理": {"percentage": 3}},
    }

    created = save_four_pots_candidates(result)

    assert created == 1
    item = list_recommendation_candidates(status="new")[0]
    assert item["action_type"] == "cash_reserve"
    assert item["scenario_type"] == "four_pots"


def test_dca_result_creates_dca_candidates(tmp_db):
    from routers.analysis.four_pots import save_dca_candidates

    result = {
        "suggestions": [
            {"fund_code": "000001", "fund_name": "定投基金", "final_amount": 1600, "decision": "提高定投金额"}
        ],
        "fear_greed": {"score": 25},
    }

    created = save_dca_candidates(result)

    assert created == 1
    item = list_recommendation_candidates(status="new")[0]
    assert item["action_type"] == "dca"
    assert item["target_code"] == "000001"
    assert item["suggested_amount"] == 1600


def test_watchlist_trigger_creates_add_candidate(tmp_db):
    from routers.watchlist import save_watchlist_trigger_candidate

    item = {
        "id": 3,
        "fund_code": "000002",
        "fund_name": "关注基金",
        "target_percentile": 30,
        "current_percentile": 25,
        "notes": "低估时分批买入",
    }

    candidate_id = save_watchlist_trigger_candidate(item)

    candidate = list_recommendation_candidates(status="new")[0]
    assert candidate_id == candidate["id"]
    assert candidate["action_type"] == "add"
    assert candidate["scenario_type"] == "watchlist_trigger"


def test_rebalance_drift_creates_rebalance_candidate(tmp_db):
    from routers.portfolio import save_rebalance_drift_candidate

    create_holding("000003", "偏离基金", shares=100, cost_price=1, current_price=1)
    candidate_id = save_rebalance_drift_candidate({
        "target_name": "整体组合",
        "summary": "权益仓位偏离目标 12%",
        "drift_pct": 12,
    })

    item = list_recommendation_candidates(status="new")[0]
    assert candidate_id == item["id"]
    assert item["action_type"] == "rebalance"
    assert item["scenario_type"] == "rebalance_drift"
