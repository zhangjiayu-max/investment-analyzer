"""交易日行情快照存储 — market_snapshots 表。

同一交易日只保留一条快照，供全系统（日报/情报/对话专家）共用，
避免各 agent 在不同时间点各自抓行情、口径不一却都称"今日"。

表结构：
    trade_date   行情对应交易日（唯一键）
    as_of        数据截止日（盘中=当天，收盘后=最近交易日）
    is_intraday  1=盘中实时，0=收盘数据
    payload      JSON：indices / sectors_top / sectors_bottom / breadth
    source       数据来源标识
    fetched_at   抓取时间（含时分秒）
"""
import json
import logging

from db._conn import _get_conn

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    trade_date  TEXT PRIMARY KEY,
    as_of       TEXT NOT NULL,
    is_intraday INTEGER DEFAULT 0,
    payload     TEXT NOT NULL,
    source      TEXT DEFAULT '',
    fetched_at  TEXT NOT NULL
)
"""


def init_table(conn=None):
    own = conn is None
    if own:
        conn = _get_conn()
    try:
        conn.execute(_DDL)
        conn.commit()
    finally:
        if own:
            conn.close()


def get_snapshot(trade_date: str) -> dict | None:
    """按交易日读取快照；不存在返回 None。"""
    if not trade_date:
        return None
    conn = _get_conn()
    try:
        conn.execute(_DDL)
        row = conn.execute(
            "SELECT trade_date, as_of, is_intraday, payload, source, fetched_at "
            "FROM market_snapshots WHERE trade_date = ?", (trade_date,)
        ).fetchone()
        if not row:
            return None
        return {
            "trade_date": row[0],
            "as_of": row[1],
            "is_intraday": bool(row[2]),
            "payload": json.loads(row[3] or "{}"),
            "source": row[4] or "",
            "fetched_at": row[5] or "",
        }
    except Exception as exc:
        logger.warning(f"[market_snapshot] 读取快照失败 {trade_date}: {exc}")
        return None
    finally:
        conn.close()


def save_snapshot(trade_date: str, as_of: str, payload: dict,
                  source: str = "", is_intraday: bool = False,
                  fetched_at: str = "") -> bool:
    """写入/覆盖当日快照（同一交易日只保留一条）。"""
    if not trade_date or not isinstance(payload, dict):
        return False
    conn = _get_conn()
    try:
        conn.execute(_DDL)
        conn.execute(
            "INSERT INTO market_snapshots(trade_date, as_of, is_intraday, payload, source, fetched_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(trade_date) DO UPDATE SET "
            "as_of=excluded.as_of, is_intraday=excluded.is_intraday, payload=excluded.payload, "
            "source=excluded.source, fetched_at=excluded.fetched_at",
            (trade_date, as_of, 1 if is_intraday else 0,
             json.dumps(payload, ensure_ascii=False), source, fetched_at),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning(f"[market_snapshot] 写入快照失败 {trade_date}: {exc}")
        return False
    finally:
        conn.close()
