"""Thread/conversation summary storage."""

import json
import logging
from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_thread_summaries_table():
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS thread_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'default',
                thread_id TEXT NOT NULL,
                summary TEXT,
                key_decisions TEXT DEFAULT '[]',
                positions_discussed TEXT DEFAULT '[]',
                unresolved_questions TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                summary_type TEXT DEFAULT 'auto'
            )
        """)
        conn.commit()
        logger.info("thread_summaries 表初始化完成")
    finally:
        conn.close()


def create_thread_summary(user_id, thread_id, summary, key_decisions=None, positions_discussed=None, unresolved_questions=None, summary_type="auto"):
    conn = _get_conn()
    try:
        from datetime import datetime
        conn.execute(
            "INSERT INTO thread_summaries (user_id, thread_id, summary, key_decisions, positions_discussed, unresolved_questions, created_at, summary_type) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, thread_id, summary, json.dumps(key_decisions or []), json.dumps(positions_discussed or []), json.dumps(unresolved_questions or []), datetime.now().isoformat(), summary_type)
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def list_thread_summaries(user_id="default", limit=20):
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM thread_summaries WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["key_decisions"] = json.loads(d.get("key_decisions") or "[]")
            d["positions_discussed"] = json.loads(d.get("positions_discussed") or "[]")
            d["unresolved_questions"] = json.loads(d.get("unresolved_questions") or "[]")
            result.append(d)
        return result
    finally:
        conn.close()
