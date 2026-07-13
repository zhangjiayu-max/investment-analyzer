"""基金质量评分 DB 层 — fund_quality_scores 表 CRUD。

表结构：单基金级缓存，每次计算覆盖更新（PRIMARY KEY = fund_code）。
缓存策略：24 小时内不重复计算（除非 force_refresh）。
"""
import json
from datetime import datetime

from db._conn import _get_conn


def init_fund_quality_tables(conn):
    """基金质量评分相关表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_quality_scores (
            fund_code   TEXT NOT NULL,
            fund_name   TEXT,
            quality_score       REAL DEFAULT 0,
            drawdown_score      REAL DEFAULT 0,
            trend_score         REAL DEFAULT 0,
            capital_score       REAL DEFAULT 0,
            sentiment_score     REAL DEFAULT 0,
            total_score         REAL DEFAULT 0,
            rating      TEXT,
            detail_json TEXT,
            advice      TEXT,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (fund_code)
        )
    """)
    # 第一阶段扩展：基本面维度 + 调仓动作
    from db._utils import _add_column_if_not_exists
    _add_column_if_not_exists(conn, "fund_quality_scores", "fundamental_score", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "fund_quality_scores", "fundamental_detail", "TEXT DEFAULT '{}'")
    _add_column_if_not_exists(conn, "fund_quality_scores", "holding_changes", "TEXT DEFAULT '[]'")


def save_fund_quality_score(
    fund_code: str,
    fund_name: str,
    *,
    quality_score: float = 0,
    drawdown_score: float = 0,
    trend_score: float = 0,
    capital_score: float = 0,
    sentiment_score: float = 0,
    total_score: float = 0,
    rating: str = None,
    detail: dict = None,
    advice: str = None,
) -> None:
    """UPSERT 基金质量评分（按 fund_code 覆盖更新）。"""
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO fund_quality_scores (
                fund_code, fund_name,
                quality_score, drawdown_score, trend_score,
                capital_score, sentiment_score, total_score,
                rating, detail_json, advice, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fund_code) DO UPDATE SET
                fund_name = excluded.fund_name,
                quality_score = excluded.quality_score,
                drawdown_score = excluded.drawdown_score,
                trend_score = excluded.trend_score,
                capital_score = excluded.capital_score,
                sentiment_score = excluded.sentiment_score,
                total_score = excluded.total_score,
                rating = excluded.rating,
                detail_json = excluded.detail_json,
                advice = excluded.advice,
                computed_at = excluded.computed_at
            """,
            (
                fund_code, fund_name,
                quality_score, drawdown_score, trend_score,
                capital_score, sentiment_score, total_score,
                rating,
                json.dumps(detail or {}, ensure_ascii=False),
                advice,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_fund_quality_score(fund_code: str) -> dict | None:
    """获取单个基金的质量评分缓存。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM fund_quality_scores WHERE fund_code = ?",
            (fund_code,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # 解析 detail_json
        if d.get("detail_json"):
            try:
                d["detail"] = json.loads(d["detail_json"])
            except (json.JSONDecodeError, TypeError):
                d["detail"] = {}
        else:
            d["detail"] = {}
        return d
    finally:
        conn.close()


def list_fund_quality_scores(fund_codes: list = None) -> list[dict]:
    """批量获取基金质量评分。fund_codes 为空则返回全部。"""
    conn = _get_conn()
    try:
        if fund_codes:
            placeholders = ",".join("?" * len(fund_codes))
            rows = conn.execute(
                f"SELECT * FROM fund_quality_scores WHERE fund_code IN ({placeholders})",
                fund_codes,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fund_quality_scores ORDER BY total_score DESC"
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("detail_json"):
                try:
                    d["detail"] = json.loads(d["detail_json"])
                except (json.JSONDecodeError, TypeError):
                    d["detail"] = {}
            else:
                d["detail"] = {}
            results.append(d)
        return results
    finally:
        conn.close()


def delete_fund_quality_score(fund_code: str) -> bool:
    """删除基金质量评分缓存。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM fund_quality_scores WHERE fund_code = ?", (fund_code,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
