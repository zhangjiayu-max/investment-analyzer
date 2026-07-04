"""公共工具函数 — 避免重复定义。"""

import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager


def _safe_float(v, default=0.0) -> float:
    """安全浮点数转换。"""
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _safe_int(v, default=0) -> int:
    """安全整数转换。"""
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


@contextmanager
def get_db_conn(db_path: str = None):
    """数据库连接上下文管理器，自动关闭。"""
    from db._conn import DB_PATH, _get_conn
    conn = _get_conn()
    try:
        yield conn
    finally:
        conn.close()


def truncate_text(text: str, max_len: int = 200) -> str:
    """截断文本。"""
    if not text or len(text) <= max_len:
        return text or ""
    return text[:max_len] + "..."
