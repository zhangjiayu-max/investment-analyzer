# backend/tests/test_run_phase.py
"""测试 run_phase 字段过滤：恢复模式只排除 primary 阶段已完成的专家。"""
import pytest
from db.agents import create_pending_agent_run, get_completed_agents_for_message, update_agent_run_status
from db.conversations import create_conversation, create_message


def test_run_phase_filter():
    """primary 阶段已完成的专家应被返回，cross_review 阶段的不影响 primary 查询。"""
    conv_id = create_conversation("test run_phase")
    msg_id = create_message(conv_id, "user", "test")
    try:
        # 创建 primary 阶段的 risk_assessor run（已完成）
        run1 = create_pending_agent_run(conv_id, msg_id, "risk_assessor", "风控专家", trace_id="t1")
        update_agent_run_status(run1, "completed", run_phase="primary")

        # 创建 cross_review 阶段的 risk_assessor run（不应影响 primary 过滤）
        run2 = create_pending_agent_run(conv_id, msg_id, "risk_assessor", "风控专家", trace_id="t1")
        update_agent_run_status(run2, "completed", run_phase="cross_review")

        # 查询 primary 阶段已完成的专家
        completed_primary = get_completed_agents_for_message(msg_id, run_phase="primary")
        assert any(r["agent_key"] == "risk_assessor" for r in completed_primary), \
            "primary 阶段已完成的专家应被返回"

        # primary 查询应只返回 1 条（primary 那条），不含 cross_review 那条
        assert len(completed_primary) == 1, \
            f"primary 查询应只返回 1 条 primary run，实际 {len(completed_primary)}"

        # 查询所有阶段已完成的专家（向后兼容）
        completed_all = get_completed_agents_for_message(msg_id)
        assert any(r["agent_key"] == "risk_assessor" for r in completed_all), \
            "不传 run_phase 时应返回所有"
        assert len(completed_all) == 2, \
            f"不传 run_phase 时应返回 2 条（primary + cross_review），实际 {len(completed_all)}"
    finally:
        from db.conversations import delete_conversation
        delete_conversation(conv_id)
