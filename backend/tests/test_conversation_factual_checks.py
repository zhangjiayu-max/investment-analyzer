"""最终正文只对能由结构化原始事实明确证伪的陈述降级。"""
import copy
import json

import pytest


SNAPSHOT = {"as_of": "2026-10-18T20:00:00", "user_id": "default",
            "concentration_policy": {"max_single_position_pct": 15, "denominator": "total_assets"}}
BOND = [{"name": "get_bond_temperature", "result": json.dumps({"temperature": 93, "date": "2026-10-16"})}]


@pytest.mark.parametrize("text", ["债市温度工具返回空数据。", "get_bond_temperature 未返回有效数据。", "债市温度无数据，无法据此分析。"])
def test_temperature_missing_claim_is_disproved_by_raw_record(text):
    from services.conversation.factual_checks import check_final_facts
    assert "bond_temperature_falsely_missing" in check_final_facts(text, SNAPSHOT, BOND)


@pytest.mark.parametrize("text", [
    "债市温度为93，但底层久期数据缺失。", "不能声称债市温度无数据；实际值为93。",
    "旧报告误称债市温度无数据，本次已核实为93。", "如果债市温度无数据，需要补查。",
    "债市温度并非无数据。", "债市温度可用，其他工具返回空数据。",
    "债市温度无数据的说法有误。", "不应再说债市温度无数据。",
])
def test_temperature_does_not_flag_negation_hypothetical_or_other_missing_data(text):
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts(text, SNAPSHOT, BOND) == []


def test_truncated_empty_or_nonfinite_tool_evidence_does_not_prove_temperature():
    from services.conversation.factual_checks import check_final_facts
    for result in ('{"temperature":93,', '{"temperature":null}', '{"temperature":true}', '{"temperature":NaN}', '{}'):
        assert check_final_facts("债市温度无数据。", SNAPSHOT, [{"name": "get_bond_temperature", "result": result}]) == []


@pytest.mark.parametrize("text,code", [
    ("用户单只基金持仓上限为25%（持仓市值口径）。", "kyc_single_limit_value_mismatch"),
    ("KYC单基金上限15%，分母是持仓市值。", "kyc_single_limit_denominator_mismatch"),
    ("单只基金占持仓25%是用户红线。", "kyc_single_limit_value_mismatch"),
    ("用户单只基金上限为持仓市值的15%。", "kyc_single_limit_denominator_mismatch"),
])
def test_explicit_user_single_position_limit_must_match_snapshot(text, code):
    from services.conversation.factual_checks import check_final_facts
    assert code in check_final_facts(text, SNAPSHOT, [])


@pytest.mark.parametrize("text", [
    "KYC单只基金上限为15%（含现金总资产口径）。",
    "持仓集中度25%只是分析阈值；用户单只基金上限为总资产15%。",
    "25%不是用户单只基金上限，用户单只基金上限为15%总资产。",
    "若用户将单只基金上限调整为25%，需先确认风险预算。",
    "用户目标权益比例15%，单基金分析集中度阈值25%。",
])
def test_single_position_checker_does_not_promote_scenarios_or_generic_thresholds_to_kyc(text):
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts(text, SNAPSHOT, []) == []


def _old_trades():
    return {"transactions": [{"transaction_date": "2026-08-16", "transaction_type": "buy", "status": "confirmed"}],
            "confirmed_count": 30, "date_scope": {"start": None, "end": "2026-10-18", "last_confirmed_date": "2026-08-16"}}


def test_recent_n_trades_with_last_confirmed_date_before_window_disprove_recent_activity():
    from services.conversation.factual_checks import check_final_facts
    calls = [{"name": "query_transaction_history", "arguments": {"limit": 30}, "result": _old_trades()}]
    assert "recent_30d_trade_count_mismatch" in check_final_facts("近30天20笔买入、0笔卖出。", SNAPSHOT, calls)
    assert check_final_facts("最近30笔记录中20笔买入，不代表近30天。", SNAPSHOT, calls) == []
    assert check_final_facts("近30天0笔买入、0笔卖出。", SNAPSHOT, calls) == []


def test_date_scoped_filtered_or_truncated_rows_cannot_prove_global_30_day_count():
    from services.conversation.factual_checks import check_final_facts
    for args, evidence in [
        ({"fund_code": "014846"}, _old_trades()),
        ({"transaction_type": "sell"}, _old_trades()),
        ({}, {"transactions": [{"transaction_date": "2026-08-16", "transaction_type": "buy"}]}),
        ({}, {**_old_trades(), "date_scope": {"start": None, "end": "2026-09-01", "last_confirmed_date": "2026-08-16"}}),
    ]:
        assert check_final_facts("近30天20笔买入。", SNAPSHOT, [{"name": "query_transaction_history", "arguments": args, "result": evidence}]) == []


def test_complete_transaction_snapshot_counts_only_real_completed_trades_without_mutation():
    from services.conversation.factual_checks import check_final_facts
    snapshot = {**SNAPSHOT, "transaction_evidence": {
        "transactions_complete": True, "transactions": [
            {"transaction_date": "2026-10-01", "transaction_type": "buy", "status": "confirmed"},
            {"transaction_date": "2026-10-02", "transaction_type": "buy", "status": "pending"},
            {"transaction_date": "2026-10-03", "transaction_type": "sell", "status": "settled", "is_system": 1},
            {"transaction_date": "2026-10-04", "transaction_type": "sell", "status": "settled", "is_hypothetical": 1},
        ]}}
    original = copy.deepcopy(snapshot)
    assert check_final_facts("近30天1笔买入、0笔卖出。", snapshot, []) == []
    assert "recent_30d_trade_count_mismatch" in check_final_facts("近30天2笔买入。", snapshot, [])
    assert snapshot == original
