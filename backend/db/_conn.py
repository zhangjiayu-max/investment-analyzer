"""数据库连接管理 — 共享基础。"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "valuations.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    # 启用 WAL 模式：允许读写并发，减少锁等待
    conn.execute("PRAGMA journal_mode=WAL")
    # 设置 busy_timeout 更友好地处理锁竞争（15秒，适应并发写入密集场景）
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("codes_found", "market_data", "local_images"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
