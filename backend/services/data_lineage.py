"""数据血缘追踪。"""
import json
import logging
from db._conn import _get_conn

logger = logging.getLogger(__name__)


def _ensure_table():
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS data_lineage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_table TEXT DEFAULT '',
                record_id INTEGER,
                source_label TEXT DEFAULT '',
                extra_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def track_sources(analysis_id: str, sources: list[dict]):
    """记录分析结果的数据来源。

    sources: [
        {"type": "valuation", "source": "index_valuations", "record_id": 123, "label": "估值"},
        {"type": "knowledge", "source": "knowledge_base", "record_id": 456, "label": "知识库"},
    ]
    """
    _ensure_table()
    conn = _get_conn()
    try:
        for s in sources:
            conn.execute("""
                INSERT INTO data_lineage (analysis_id, source_type, source_table, record_id, source_label, extra_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                analysis_id,
                s.get("type", ""),
                s.get("source", ""),
                s.get("record_id"),
                s.get("label", s.get("title", "")),
                json.dumps({k: v for k, v in s.items() if k not in ("type", "source", "record_id", "label", "title")}, ensure_ascii=False),
            ))
        conn.commit()
    finally:
        conn.close()


def get_lineage(analysis_id: str) -> list[dict]:
    """获取分析结果的数据来源。"""
    _ensure_table()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM data_lineage WHERE analysis_id = ? ORDER BY id", (analysis_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_decision_lineage(decision_id: int) -> list[dict]:
    """获取决策关联的数据来源。"""
    _ensure_table()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM data_lineage WHERE analysis_id = ? ORDER BY id",
            (f"decision:{decision_id}",)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
