"""任务规划器行为测试。"""

import pytest

from agent.core.task_planner import classify_question_type, build_shared_evidence_keys


@pytest.fixture(autouse=True)
def tmp_db():
    yield


def test_classify_question_type_action():
    assert classify_question_type("白酒能买吗") == "action"


def test_classify_question_type_attribution():
    assert classify_question_type("恒生科技为什么涨") == "attribution"


def test_shared_evidence_keys_for_action_complex():
    assert build_shared_evidence_keys("action", "complex") == [
        "portfolio",
        "valuation",
        "risk",
        "market_signal",
        "knowledge",
        "regression",
        "memory",
    ]
