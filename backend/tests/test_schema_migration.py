# backend/tests/test_schema_migration.py
"""测试 DB schema 迁移：cancel_requested 和 run_phase 字段。"""
import sqlite3
import pytest
from db._conn import DB_PATH


def test_conversations_has_cancel_requested_column():
    """conversations 表应有 cancel_requested 字段（默认 0）。"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute("PRAGMA table_info(conversations)")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    assert "cancel_requested" in columns, f"conversations 缺少 cancel_requested 字段: {columns}"


def test_agent_runs_has_run_phase_column():
    """agent_runs 表应有 run_phase 字段（默认 'primary'）。"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute("PRAGMA table_info(agent_runs)")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    assert "run_phase" in columns, f"agent_runs 缺少 run_phase 字段: {columns}"
