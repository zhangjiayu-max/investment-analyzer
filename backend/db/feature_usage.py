"""功能使用埋点 — feature_usage 表。

记录前端页面/功能的使用行为（访问、点击、停留时长等），
用于分析功能热度与用户行为路径。
"""
import logging
from datetime import datetime

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def _now() -> str:
    """当前本地时间字符串（兼容旧版SQLite的DEFAULT不生效问题）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_feature_usage_table(conn=None):
    """建表（由 init_db 调用）。"""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                page_key TEXT NOT NULL,
                feature_key TEXT,
                action_type TEXT NOT NULL,
                duration_ms INTEGER,
                referrer_page TEXT,
                created_at TEXT DEFAULT (datetime('localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_usage_page ON feature_usage(page_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_usage_feature ON feature_usage(feature_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_usage_created ON feature_usage(created_at)")
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def track_feature_usage(session_id: str, page_key: str, action_type: str,
                        feature_key: str = None, duration_ms: int = None,
                        referrer_page: str = None) -> int:
    """记录单条功能使用，返回插入行 id。"""
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO feature_usage (session_id, page_key, feature_key, action_type, duration_ms, referrer_page, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, page_key, feature_key, action_type, duration_ms, referrer_page, _now())
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def batch_track_feature_usage(events: list[dict]) -> int:
    """批量记录，events 是 dict 列表，返回写入条数。"""
    if not events:
        return 0
    conn = _get_conn()
    now = _now()
    count = 0
    for e in events:
        conn.execute(
            """INSERT INTO feature_usage (session_id, page_key, feature_key, action_type, duration_ms, referrer_page, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (e.get("session_id", ""), e.get("page_key", ""), e.get("feature_key"),
             e.get("action_type", ""), e.get("duration_ms"), e.get("referrer_page"), now)
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_feature_usage_stats(days: int = 30) -> dict:
    """聚合统计：会话数、动作数、页面排行、功能排行、按天趋势。"""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    try:
        # 总体：会话数（DISTINCT session_id）+ 动作数（总记录数）
        row = conn.execute("""
            SELECT
                COUNT(DISTINCT session_id) AS total_sessions,
                COUNT(*) AS total_actions
            FROM feature_usage
            WHERE created_at >= ?
        """, (cutoff,)).fetchone()

        # 页面排行：访问次数 + 平均停留时长（毫秒）
        page_rows = conn.execute("""
            SELECT
                page_key,
                COUNT(*) AS visits,
                CAST(AVG(duration_ms) AS INTEGER) AS avg_duration_ms
            FROM feature_usage
            WHERE created_at >= ?
            GROUP BY page_key
            ORDER BY visits DESC
        """, (cutoff,)).fetchall()

        # 功能排行：点击次数（仅统计有 feature_key 的记录）
        feature_rows = conn.execute("""
            SELECT
                feature_key,
                COUNT(*) AS clicks
            FROM feature_usage
            WHERE feature_key IS NOT NULL AND feature_key != ''
              AND created_at >= ?
            GROUP BY feature_key
            ORDER BY clicks DESC
        """, (cutoff,)).fetchall()

        # 按天趋势：动作数 + 会话数
        daily_rows = conn.execute("""
            SELECT
                date(created_at) AS day,
                COUNT(*) AS actions,
                COUNT(DISTINCT session_id) AS sessions
            FROM feature_usage
            WHERE created_at >= ?
            GROUP BY date(created_at)
            ORDER BY day ASC
        """, (cutoff,)).fetchall()

        return {
            "days": days,
            "total_sessions": row["total_sessions"] if row else 0,
            "total_actions": row["total_actions"] if row else 0,
            "page_ranking": [dict(r) for r in page_rows],
            "feature_ranking": [dict(r) for r in feature_rows],
            "daily_trend": [dict(r) for r in daily_rows],
        }
    finally:
        conn.close()


def cleanup_old_feature_usage(days: int = 90) -> int:
    """清理 N 天前的旧数据，返回删除条数。"""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM feature_usage WHERE created_at < ?",
        (cutoff,),
    )
    conn.commit()
    conn.close()
    return cur.rowcount
