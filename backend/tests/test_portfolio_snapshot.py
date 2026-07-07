"""测试持仓快照 CRUD。"""
import json
import pytest
from db.portfolio import (
    create_holding, delete_holding,
    create_snapshot, list_snapshots, get_latest_snapshot,
)


def test_create_and_list_snapshot(tmp_db):
    """创建快照并查询。"""
    holding_id = create_holding(
        fund_code="110040", fund_name="测试快照基金",
        shares=1000, cost_price=1.0, current_price=1.5,
    )
    try:
        snap = create_snapshot()
        assert snap is not None
        assert snap["snapshot_date"] is not None
        assert snap["total_value"] is not None
        assert snap["holdings_json"] is not None

        # 查询快照列表
        snaps = list_snapshots(limit=10)
        assert len(snaps) >= 1

        # 查询最新快照
        latest = get_latest_snapshot()
        assert latest is not None
    finally:
        delete_holding(holding_id)


def test_snapshot_idempotent_same_day(tmp_db):
    """同一天快照幂等（覆盖不报错）。"""
    snap1 = create_snapshot()
    snap2 = create_snapshot()
    # 同一天应该覆盖，不是新增
    snaps = list_snapshots(limit=10)
    today_snaps = [s for s in snaps if s["snapshot_date"] == snap1["snapshot_date"]]
    assert len(today_snaps) == 1
