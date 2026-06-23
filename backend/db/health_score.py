"""综合理财健康分 — DB 层"""
import json
from datetime import datetime
from db._conn import _get_conn


def init_health_score_tables(conn):
    """健康分相关表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score_date TEXT NOT NULL,
            total_score INTEGER DEFAULT 0,
            score_quality INTEGER DEFAULT 0,
            score_diversification INTEGER DEFAULT 0,
            score_valuation INTEGER DEFAULT 0,
            score_behavior INTEGER DEFAULT 0,
            score_risk INTEGER DEFAULT 0,
            advice_json TEXT DEFAULT '[]',
            detail_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_date ON health_scores(score_date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bond_yield_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            yield_10y REAL,
            yield_3y REAL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(trade_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bond_date ON bond_yield_history(trade_date)")


def save_health_score(score_date: str, total_score: int,
                      score_quality: int, score_diversification: int,
                      score_valuation: int, score_behavior: int,
                      score_risk: int, advice: list = None,
                      detail: dict = None) -> int:
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO health_scores
        (score_date, total_score, score_quality, score_diversification,
         score_valuation, score_behavior, score_risk, advice_json, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        score_date, total_score, score_quality, score_diversification,
        score_valuation, score_behavior, score_risk,
        json.dumps(advice or [], ensure_ascii=False),
        json.dumps(detail or {}, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()
    return 0


def get_health_score(score_date: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM health_scores WHERE score_date = ?", (score_date,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_health_scores(limit: int = 30) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM health_scores ORDER BY score_date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_bond_yield(trade_date: str, yield_10y: float, yield_3y: float = None) -> int:
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO bond_yield_history (trade_date, yield_10y, yield_3y)
        VALUES (?, ?, ?)
    """, (trade_date, yield_10y, yield_3y))
    conn.commit()
    conn.close()
    return 0


def get_latest_bond_yield() -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM bond_yield_history ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_bond_yield_history(days: int = 365) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bond_yield_history ORDER BY trade_date DESC LIMIT ?", (days,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
