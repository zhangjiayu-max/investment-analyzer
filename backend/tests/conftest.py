"""Pytest 共享 fixtures — 使用临时数据库隔离测试。"""
import os
import sys
import sqlite3
import tempfile
import pytest

# 确保 backend/ 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch, request):
    """每个测试使用独立的临时 SQLite 数据库。"""
    db_path = tmp_path / "test.db"

    # monkeypatch 数据库路径
    import db._conn as conn_mod
    monkeypatch.setattr(conn_mod, "DB_PATH", db_path)

    # 某些纯单元测试不依赖真实 DB，避免初始化时抢占 SQLite 锁
    if request.node.path.name == "test_unified_evidence.py":
        yield db_path
        return

    # 用真实 schema 初始化
    from db import init_db
    init_db()

    # 综合阶段流式会直接打真实模型通道；单测默认走非流式分支，
    # 专测流式的用例在自身内部重新打开。
    import agent.core.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "_synthesis_stream_enabled", lambda: False, raising=False)

    yield db_path
