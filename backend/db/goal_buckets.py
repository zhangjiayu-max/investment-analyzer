"""目标账户 / 资金桶数据层。"""

from __future__ import annotations

from datetime import datetime

from db._conn import _get_conn

VALID_BUCKET_TYPES = {"emergency", "stable", "long_term", "opportunity", "learning"}
RISK_ASSET_BLOCKED_TYPES = {"emergency"}


def init_goal_bucket_tables(conn):
    """初始化资金桶表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goal_buckets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            bucket_type TEXT NOT NULL,
            target_amount REAL DEFAULT 0,
            current_amount REAL DEFAULT 0,
            target_ratio REAL,
            risk_level TEXT DEFAULT '',
            liquidity_days INTEGER,
            priority INTEGER DEFAULT 3,
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_buckets_user ON goal_buckets(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_buckets_type ON goal_buckets(bucket_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_goal_buckets_status ON goal_buckets(status)")


def _normalize_bucket_type(bucket_type: str) -> str:
    value = (bucket_type or "").strip()
    if value not in VALID_BUCKET_TYPES:
        raise ValueError(f"无效资金桶类型: {bucket_type}")
    return value


def _row_to_bucket(row) -> dict:
    item = dict(row)
    for key in ("target_amount", "current_amount", "target_ratio"):
        if item.get(key) is not None:
            item[key] = float(item[key])
    if item.get("liquidity_days") is not None:
        item["liquidity_days"] = int(item["liquidity_days"])
    item["progress_pct"] = _calc_progress_pct(item.get("current_amount"), item.get("target_amount"))
    item["guardrail_level"] = (
        "blocked_for_risk_assets"
        if item.get("bucket_type") in RISK_ASSET_BLOCKED_TYPES
        else "risk_assets_allowed"
    )
    return item


def _calc_progress_pct(current_amount, target_amount) -> float:
    if not target_amount or target_amount <= 0:
        return 0.0
    return round(min((current_amount or 0) / target_amount * 100, 999.0), 2)


def create_goal_bucket(
    name: str,
    bucket_type: str,
    target_amount: float = 0,
    current_amount: float = 0,
    target_ratio: float | None = None,
    risk_level: str = "",
    liquidity_days: int | None = None,
    priority: int = 3,
    notes: str = "",
    user_id: str = "default",
) -> int:
    """创建资金桶，返回 ID。"""
    bucket_type = _normalize_bucket_type(bucket_type)
    if not (name or "").strip():
        raise ValueError("资金桶名称不能为空")

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO goal_buckets
                (user_id, name, bucket_type, target_amount, current_amount, target_ratio,
                 risk_level, liquidity_days, priority, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                name.strip(),
                bucket_type,
                target_amount or 0,
                current_amount or 0,
                target_ratio,
                risk_level or "",
                liquidity_days,
                priority,
                notes or "",
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_goal_bucket(bucket_id: int, user_id: str = "default") -> dict | None:
    """获取单个资金桶。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM goal_buckets WHERE id = ? AND user_id = ?",
            (bucket_id, user_id),
        ).fetchone()
        return _row_to_bucket(row) if row else None
    finally:
        conn.close()


def list_goal_buckets(user_id: str = "default", status: str = "active") -> list[dict]:
    """列出资金桶。"""
    conn = _get_conn()
    try:
        conditions = ["user_id = ?"]
        params: list = [user_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        rows = conn.execute(
            f"""
            SELECT * FROM goal_buckets
            WHERE {' AND '.join(conditions)}
            ORDER BY priority ASC, id ASC
            """,
            params,
        ).fetchall()
        return [_row_to_bucket(row) for row in rows]
    finally:
        conn.close()


def update_goal_bucket(bucket_id: int, user_id: str = "default", **fields) -> bool:
    """更新资金桶。"""
    allowed = {
        "name",
        "bucket_type",
        "target_amount",
        "current_amount",
        "target_ratio",
        "risk_level",
        "liquidity_days",
        "priority",
        "notes",
        "status",
    }
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"非法字段名: {invalid}")
    if not fields:
        return False
    if "bucket_type" in fields:
        fields["bucket_type"] = _normalize_bucket_type(fields["bucket_type"])
    if "name" in fields and not (fields["name"] or "").strip():
        raise ValueError("资金桶名称不能为空")
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    set_clause = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [bucket_id, user_id]

    conn = _get_conn()
    try:
        cur = conn.execute(
            f"UPDATE goal_buckets SET {set_clause} WHERE id = ? AND user_id = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_goal_bucket(bucket_id: int, user_id: str = "default") -> bool:
    """删除资金桶。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM goal_buckets WHERE id = ? AND user_id = ?",
            (bucket_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_goal_bucket_summary(user_id: str = "default") -> dict:
    """资金桶总览。"""
    items = list_goal_buckets(user_id=user_id)
    type_counts: dict[str, int] = {}
    for item in items:
        bucket_type = item.get("bucket_type") or "unknown"
        type_counts[bucket_type] = type_counts.get(bucket_type, 0) + 1
    total_current = round(sum(item.get("current_amount") or 0 for item in items), 2)
    total_target = round(sum(item.get("target_amount") or 0 for item in items), 2)
    emergency_bucket = next((item for item in items if item.get("bucket_type") == "emergency"), None)
    return {
        "count": len(items),
        "total_current_amount": total_current,
        "total_target_amount": total_target,
        "overall_progress_pct": _calc_progress_pct(total_current, total_target),
        "type_counts": type_counts,
        "emergency_bucket": emergency_bucket,
    }
