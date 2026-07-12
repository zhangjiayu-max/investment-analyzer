"""前瞻性事件雷达 — market_events 表 CRUD。

事件结构见 doc/plans/2026-07-10-forward-looking-event-radar.md §3。
状态流转见 §5：upcoming → imminent → materialized → expired。
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_market_events_tables(conn) -> None:
    """创建 market_events 表（由 init_db 调用）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_events (
            event_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'upcoming',
            direction TEXT,
            confidence REAL DEFAULT 0.5,
            expected_date TEXT,
            detected_date TEXT NOT NULL,
            materialized_date TEXT,
            expired_date TEXT,
            affected_sectors TEXT,
            affected_themes TEXT,
            relevance_to_user TEXT NOT NULL DEFAULT 'market_watch',
            matched_holdings TEXT,
            candidate_funds TEXT,
            sources TEXT,
            timeline TEXT,
            verification_result TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 兼容已有表：若旧表无 verification_result 列则自动追加
    cols = [r[1] for r in conn.execute("PRAGMA table_info(market_events)").fetchall()]
    if "verification_result" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN verification_result TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_status ON market_events(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_expected ON market_events(expected_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_relevance ON market_events(relevance_to_user)")


def _gen_event_id(title: str, expected_date: str) -> str:
    """事件唯一 ID：sha1(title+expected_date)[:16]，保证幂等。"""
    raw = f"{title}|{expected_date}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _clean_title(s: str) -> str:
    """清洗标题：去除括号内容和空格。"""
    s = s.lower().replace(" ", "")
    s = s.replace("（", "(").replace("）", ")")
    while "(" in s and ")" in s:
        start = s.find("(")
        end = s.find(")")
        if end > start:
            s = s[:start] + s[end+1:]
        else:
            break
    return s


def _ngram_set(s: str, n: int = 2) -> set:
    """生成字符串的 n-gram 集合。"""
    s = _clean_title(s)
    return {s[i:i+n] for i in range(len(s) - n + 1)}


def _extract_keywords(s: str) -> set:
    """提取标题中的核心关键词（公司名、事件名等）。"""
    s = _clean_title(s)
    keywords = set()
    company_patterns = [
        "sk海力士", "meta", "英伟达", "微软", "苹果", "特斯拉", "阿里巴巴", "腾讯",
        "华为", "小米", "比亚迪", "宁德时代", "贵州茅台", "招商银行", "中国平安",
    ]
    event_patterns = [
        "ad定价", "ad开始交易", "复牌", "人工智能大会", "港股通", "美联储",
        "财报", "发布", "会议", "举行", "召开", "上市", "发行", "重组", "并购",
    ]
    for kw in company_patterns:
        if kw in s:
            keywords.add(kw)
    for kw in event_patterns:
        if kw in s:
            keywords.add(kw)
    if not keywords:
        ngrams = _ngram_set(s)
        if ngrams:
            keywords = set(list(ngrams)[:5])
    return keywords


def _title_similarity(a: str, b: str) -> float:
    """计算两个标题的相似度（双字符 Jaccard + 关键词匹配），用于去重。
    
    结合 2-gram 匹配和关键词匹配，更适合中文事件标题的去重。
    """
    a_ngrams = _ngram_set(a)
    b_ngrams = _ngram_set(b)
    if not a_ngrams or not b_ngrams:
        return 0.0
    
    ngram_sim = len(a_ngrams & b_ngrams) / len(a_ngrams | b_ngrams)
    
    a_keywords = _extract_keywords(a)
    b_keywords = _extract_keywords(b)
    if a_keywords and b_keywords:
        keyword_overlap = len(a_keywords & b_keywords) / len(a_keywords | b_keywords)
        return max(ngram_sim, keyword_overlap)
    
    return ngram_sim


def create_market_event(
    title: str,
    summary: str,
    event_type: str,
    direction: str,
    expected_date: str,
    affected_sectors: list,
    affected_themes: list,
    confidence: float,
    sources: list,
) -> str:
    """创建事件（幂等：相同 title+expected_date 不重复创建）。

    增强去重：除了精确匹配，还会检测标题相似度 > 0.7 的同类事件。

    Returns:
        event_id（已存在则返回已有 id，不覆盖）
    """
    event_id = _gen_event_id(title, expected_date)
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT event_id FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing:
            return event_id

        # 增强去重：检测同日期、标题相似度 >= 0.6 的事件
        same_date_events = conn.execute(
            "SELECT event_id, title FROM market_events WHERE expected_date = ?",
            (expected_date,),
        ).fetchall()
        for row in same_date_events:
            if _title_similarity(title, row["title"]) >= 0.6:
                logger.info(f"[market_events] 检测到相似事件，跳过创建: '{title}' -> '{row['title']}'")
                return row["event_id"]

        timeline = json.dumps(
            [{"date": today, "event": "首次检测"}], ensure_ascii=False
        )
        conn.execute("""
            INSERT INTO market_events (
                event_id, title, summary, event_type, status, direction, confidence,
                expected_date, detected_date, affected_sectors, affected_themes,
                relevance_to_user, sources, timeline
            ) VALUES (?, ?, ?, ?, 'upcoming', ?, ?, ?, ?, ?, ?, 'market_watch', ?, ?)
        """, (
            event_id, title, summary, event_type, direction, confidence,
            expected_date, today,
            json.dumps(affected_sectors, ensure_ascii=False),
            json.dumps(affected_themes, ensure_ascii=False),
            json.dumps(sources, ensure_ascii=False),
            timeline,
        ))
        conn.commit()
        return event_id
    finally:
        conn.close()


def get_market_event(event_id: str) -> Optional[dict]:
    """按 event_id 查询事件详情。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_market_events(
    status: Optional[str] = None,
    relevance: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """查询事件列表（可按 status/relevance 过滤）。"""
    sql = "SELECT * FROM market_events"
    params: list = []
    conditions = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if relevance:
        conditions.append("relevance_to_user = ?")
        params.append(relevance)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY expected_date ASC LIMIT ?"
    params.append(limit)

    conn = _get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_active_events() -> list[dict]:
    """查询所有 upcoming/imminent 状态事件（供状态流转扫描）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM market_events WHERE status IN ('upcoming','imminent') "
            "ORDER BY expected_date ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_market_event_status(event_id: str, new_status: str) -> bool:
    """更新事件状态，追加 timeline 记录。

    Returns:
        True if 更新成功，False if 事件不存在
    """
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT timeline FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if not row:
            return False

        timeline = json.loads(row["timeline"]) if row["timeline"] else []
        timeline.append({"date": today, "event": f"状态更新为 {new_status}"})

        date_field = ""
        date_val = None
        if new_status == "materialized":
            date_field = ", materialized_date = ?"
            date_val = today
        elif new_status == "expired":
            date_field = ", expired_date = ?"
            date_val = today

        sql = f"""
            UPDATE market_events
            SET status = ?, timeline = ?, updated_at = ?{date_field}
            WHERE event_id = ?
        """
        params: list = [new_status, json.dumps(timeline, ensure_ascii=False), today]
        if date_field:
            params.append(date_val)
        params.append(event_id)

        conn.execute(sql, params)
        conn.commit()
        return True
    finally:
        conn.close()


def update_event_relevance(
    event_id: str,
    relevance: str,
    matched_holdings: list,
    candidate_funds: list,
) -> bool:
    """更新事件的推送分级与关联基金（每次扫描重新计算）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        conn.execute("""
            UPDATE market_events
            SET relevance_to_user = ?, matched_holdings = ?, candidate_funds = ?,
                updated_at = ?
            WHERE event_id = ?
        """, (
            relevance,
            json.dumps(matched_holdings, ensure_ascii=False),
            json.dumps(candidate_funds, ensure_ascii=False),
            today,
            event_id,
        ))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_market_event(event_id: str) -> bool:
    """删除事件。"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM market_events WHERE event_id = ?", (event_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ── 事件落地验证 ──────────────────────────────────────


def list_pending_verification_events(days_after: int = 3) -> list[dict]:
    """查询已落地但尚未验证、且超过验证窗口（T+days_after）的事件。

    条件：
    - status = 'materialized'
    - verification_result IS NULL
    - materialized_date <= today - days_after
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days_after)).strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM market_events "
            "WHERE status = 'materialized' AND verification_result IS NULL "
            "AND materialized_date <= ? "
            "ORDER BY materialized_date ASC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_event_verification(event_id: str, result: dict) -> bool:
    """写入事件验证结果。

    result 结构：
    {
        "status": "correct" | "wrong" | "flat",
        "change_pct": float,        # 实际涨跌幅
        "verified_date": "YYYY-MM-DD",
        "index_code": str,          # 验证用的指数代码
        "index_name": str,
        "direction_predicted": str, # 事件预测方向
        "window_days": int          # 验证窗口
    }
    """
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        # 追加 timeline
        row = conn.execute(
            "SELECT timeline FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if not row:
            return False
        timeline = json.loads(row["timeline"]) if row["timeline"] else []
        status = result.get("status", "flat")
        change = result.get("change_pct", 0)
        timeline.append({
            "date": today,
            "event": f"验证完成：{status}（涨跌幅 {change:+.2f}%）",
        })

        conn.execute("""
            UPDATE market_events
            SET verification_result = ?, timeline = ?, updated_at = ?
            WHERE event_id = ?
        """, (
            json.dumps(result, ensure_ascii=False),
            json.dumps(timeline, ensure_ascii=False),
            today,
            event_id,
        ))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def list_verified_events(limit: int = 100) -> list[dict]:
    """查询已验证的事件（用于准确率统计）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM market_events "
            "WHERE verification_result IS NOT NULL "
            "ORDER BY materialized_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
