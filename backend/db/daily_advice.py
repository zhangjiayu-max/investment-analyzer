"""每日持仓提示 — 数据层。"""

import sqlite3
import json
import logging
from datetime import datetime
from db._conn import DB_PATH, _get_conn, _row_to_dict

logger = logging.getLogger(__name__)


def init_daily_advice_tables():
    """创建每日提示相关表。"""
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_advice_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default',
                run_date TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                summary TEXT DEFAULT '',
                stats_json TEXT DEFAULT '{}',
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(user_id, run_date, trigger_type)
            );

            CREATE TABLE IF NOT EXISTS daily_position_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES daily_advice_runs(id) ON DELETE CASCADE,
                user_id TEXT DEFAULT 'default',
                signal_date TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                action_type TEXT NOT NULL,
                target_type TEXT DEFAULT 'fund',
                target_code TEXT DEFAULT '',
                target_name TEXT DEFAULT '',
                severity TEXT DEFAULT 'info',
                score INTEGER DEFAULT 0,
                suggested_amount REAL,
                suggested_ratio REAL,
                summary TEXT NOT NULL,
                rationale TEXT DEFAULT '',
                evidence_json TEXT DEFAULT '{}',
                risk_json TEXT DEFAULT '{}',
                source_snapshot_json TEXT DEFAULT '{}',
                candidate_id INTEGER,
                alert_id INTEGER,
                status TEXT DEFAULT 'new',
                dedupe_key TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(user_id, signal_date, dedupe_key)
            );

            CREATE INDEX IF NOT EXISTS idx_signals_run ON daily_position_signals(run_id);
            CREATE INDEX IF NOT EXISTS idx_signals_user_date ON daily_position_signals(user_id, signal_date);
            CREATE INDEX IF NOT EXISTS idx_signals_status ON daily_position_signals(status);
        """)
        conn.commit()
        logger.info("daily_advice 表初始化完成")
    finally:
        conn.close()


# ── Run 管理 ──────────────────────────────────────────────

def create_run(user_id: str, run_date: str, trigger_type: str) -> int:
    """创建一次运行记录。如果当天同类型已存在，返回已有 ID。"""
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM daily_advice_runs WHERE user_id=? AND run_date=? AND trigger_type=?",
            (user_id, run_date, trigger_type)
        ).fetchone()
        if existing:
            return existing["id"]

        cur = conn.execute(
            "INSERT INTO daily_advice_runs (user_id, run_date, trigger_type, status) VALUES (?,?,?,?)",
            (user_id, run_date, trigger_type, "running")
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_run(run_id: int, status: str, summary: str = "", stats: dict = None, error: str = ""):
    """更新运行状态。"""
    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE daily_advice_runs
               SET status=?, summary=?, stats_json=?, error=?, updated_at=datetime('now','localtime')
               WHERE id=?""",
            (status, summary, json.dumps(stats or {}, ensure_ascii=False), error, run_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_run(run_id: int) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM daily_advice_runs WHERE id=?", (run_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_today_run(user_id: str, run_date: str) -> dict | None:
    """获取今日最新运行。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM daily_advice_runs WHERE user_id=? AND run_date=? ORDER BY id DESC LIMIT 1",
            (user_id, run_date)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_runs(user_id: str = "default", limit: int = 30) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM daily_advice_runs WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ── Signal 管理 ────────────────────────────────────────────

def create_signal(signal: dict) -> int | None:
    """创建信号。如果 dedupe_key 重复则返回 None。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO daily_position_signals
               (run_id, user_id, signal_date, signal_type, action_type,
                target_type, target_code, target_name, severity, score,
                suggested_amount, suggested_ratio, summary, rationale,
                evidence_json, risk_json, source_snapshot_json,
                dedupe_key, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                signal["run_id"],
                signal.get("user_id", "default"),
                signal["signal_date"],
                signal["signal_type"],
                signal["action_type"],
                signal.get("target_type", "fund"),
                signal.get("target_code", ""),
                signal.get("target_name", ""),
                signal.get("severity", "info"),
                signal.get("score", 0),
                signal.get("suggested_amount"),
                signal.get("suggested_ratio"),
                signal["summary"],
                signal.get("rationale", ""),
                json.dumps(signal.get("evidence", {}), ensure_ascii=False),
                json.dumps(signal.get("risks", {}), ensure_ascii=False),
                json.dumps(signal.get("source_snapshot", {}), ensure_ascii=False),
                signal.get("dedupe_key", ""),
                "new",
            )
        )
        conn.commit()
        return cur.lastrowid if cur.rowcount > 0 else None
    finally:
        conn.close()


def list_signals_by_run(run_id: int) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM daily_position_signals WHERE run_id=? ORDER BY score DESC, id ASC",
            (run_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def list_today_signals(user_id: str, signal_date: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM daily_position_signals WHERE user_id=? AND signal_date=? AND status != 'expired' ORDER BY score DESC, id ASC",
            (user_id, signal_date)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_signal(signal_id: int) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM daily_position_signals WHERE id=?", (signal_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def update_signal_status(signal_id: int, status: str, candidate_id: int = None):
    conn = _get_conn()
    try:
        if candidate_id:
            conn.execute(
                "UPDATE daily_position_signals SET status=?, candidate_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                (status, candidate_id, signal_id)
            )
        else:
            conn.execute(
                "UPDATE daily_position_signals SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                (status, signal_id)
            )
        conn.commit()
    finally:
        conn.close()


def expire_old_signals(user_id: str, signal_date: str, signal_type: str, target_code: str):
    """将同标的旧信号标记为过期（force 重跑时用）。"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE daily_position_signals SET status='expired', updated_at=datetime('now','localtime') "
            "WHERE user_id=? AND signal_date=? AND signal_type=? AND target_code=? AND status='new'",
            (user_id, signal_date, signal_type, target_code)
        )
        conn.commit()
    finally:
        conn.close()
