"""Pytest 共享 fixtures — 使用临时数据库隔离测试。"""
import os
import sys
import sqlite3
import tempfile
import pytest

# 确保 backend/ 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """每个测试使用独立的临时 SQLite 数据库。"""
    db_path = tmp_path / "test.db"

    # monkeypatch 数据库路径
    import db._conn as conn_mod
    monkeypatch.setattr(conn_mod, "DB_PATH", db_path)

    # 用真实 schema 初始化
    from db import init_db
    init_db()

    yield db_path
