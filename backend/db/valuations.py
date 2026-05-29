"""估值数据 + 指数信息 CRUD。"""

from datetime import date, datetime

from db._conn import _get_conn


def save_valuation(data: dict, source_image: str = None, source_url: str = None, snapshot_date: str = None) -> int:
    """保存估值数据，返回 id。同指数同日期同类型会更新。"""
    if not snapshot_date:
        snapshot_date = date.today().isoformat()
    if not data.get("index_code"):
        data["index_code"] = "UNKNOWN"
    if not data.get("index_name"):
        data["index_name"] = "未知指数"
    metric_type = data.get("metric_type", "市盈率")

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO index_valuations (
            index_code, index_name, snapshot_date,
            current_point, change_pct, metric_type,
            current_value, percentile, danger_value, median,
            opportunity_value, max_value, min_value, avg_value, zscore,
            source_image, source_url, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(index_code, snapshot_date, metric_type) DO UPDATE SET
            index_name=excluded.index_name,
            current_point=excluded.current_point,
            change_pct=excluded.change_pct,
            current_value=excluded.current_value,
            percentile=excluded.percentile,
            danger_value=excluded.danger_value,
            median=excluded.median,
            opportunity_value=excluded.opportunity_value,
            max_value=excluded.max_value,
            min_value=excluded.min_value,
            avg_value=excluded.avg_value,
            zscore=excluded.zscore,
            source_image=excluded.source_image,
            source_url=excluded.source_url,
            raw_json=excluded.raw_json,
            created_at=datetime('now','localtime')
    """, (
        data.get("index_code"), data.get("index_name"), snapshot_date,
        data.get("current_point"), data.get("change_pct"), metric_type,
        data.get("current_value"), data.get("percentile"),
        data.get("danger_value"), data.get("median"),
        data.get("opportunity_value"), data.get("max_value"),
        data.get("min_value"), data.get("avg_value"), data.get("zscore"),
        source_image, source_url, data.get("raw_json"),
    ))
    if cur.lastrowid:
        valuation_id = cur.lastrowid
    else:
        valuation_id = conn.execute(
            "SELECT id FROM index_valuations WHERE index_code=? AND snapshot_date=?",
            (data.get("index_code"), snapshot_date)
        ).fetchone()[0]
    conn.commit()
    conn.close()
    return valuation_id


def get_valuation_history(index_code: str, days: int = 30, metric_type: str = None) -> list[dict]:
    """查询某指数最近 N 天的估值历史。"""
    conn = _get_conn()
    if metric_type:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ? AND metric_type = ?
            ORDER BY snapshot_date DESC LIMIT ?
        """, (index_code, metric_type, days)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ?
            ORDER BY snapshot_date DESC LIMIT ?
        """, (index_code, days)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_valuation(index_code: str, metric_type: str = None) -> dict | None:
    """获取某指数最新一条估值。"""
    conn = _get_conn()
    if metric_type:
        row = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ? AND metric_type = ?
            ORDER BY snapshot_date DESC LIMIT 1
        """, (index_code, metric_type)).fetchone()
    else:
        row = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ?
            ORDER BY snapshot_date DESC LIMIT 1
        """, (index_code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_valuation_indexes() -> list[dict]:
    """列出所有有估值数据的指数，按 metric_type 分别显示。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT iv.index_code, iv.index_name, iv.metric_type,
               iv.current_value, iv.percentile,
               iv.snapshot_date as latest_date,
               cnt.record_count
        FROM index_valuations iv
        INNER JOIN (
            SELECT index_code, metric_type, MAX(snapshot_date) as max_date
            FROM index_valuations
            GROUP BY index_code, metric_type
        ) latest ON iv.index_code = latest.index_code
                  AND iv.metric_type = latest.metric_type
                  AND iv.snapshot_date = latest.max_date
        INNER JOIN (
            SELECT index_code, metric_type, COUNT(*) as record_count
            FROM index_valuations
            GROUP BY index_code, metric_type
        ) cnt ON iv.index_code = cnt.index_code AND iv.metric_type = cnt.metric_type
        ORDER BY iv.index_code, iv.metric_type
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_index_freshness() -> list[dict]:
    """列出所有指数的最新数据日期和距今天数。"""
    conn = _get_conn()
    today = date.today().isoformat()
    rows = conn.execute("""
        SELECT index_code, index_name, MAX(snapshot_date) as latest_date,
               (julianday(?) - julianday(MAX(snapshot_date))) as stale_days
        FROM index_valuations
        GROUP BY index_code, index_name
        ORDER BY stale_days DESC
    """, (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_indexes_by_keyword(keyword: str) -> list[dict]:
    """按关键词模糊匹配指数名称或代码。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT index_code, index_name
        FROM index_valuations
        WHERE index_name LIKE ? OR index_code LIKE ?
        ORDER BY index_code
    """, (f"%{keyword}%", f"%{keyword}%")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_index_info(index_code: str) -> dict | None:
    """查询指数信息缓存。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM index_info WHERE index_code = ?", (index_code,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_index_info(index_code: str, index_name: str, info: str) -> int:
    """保存指数信息（UPSERT）。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO index_info (index_code, index_name, info)
        VALUES (?, ?, ?)
        ON CONFLICT(index_code) DO UPDATE SET
            index_name = excluded.index_name,
            info = excluded.info,
            created_at = datetime('now','localtime')
    """, (index_code, index_name, info))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM index_info WHERE index_code = ?", (index_code,)
    ).fetchone()
    conn.close()
    return row["id"] if row else 0


def get_valuation_by_image(image_path: str) -> dict | None:
    """按图片路径查找估值数据。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM index_valuations WHERE source_image = ? ORDER BY created_at DESC LIMIT 1",
        (image_path,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
