"""前瞻性事件雷达 — market_events 表 CRUD。

事件结构见 doc/plans/2026-07-10-forward-looking-event-radar.md §3。
状态流转见 §5：upcoming → imminent → materialized → expired。"""
import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Optional

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# 事件列表缓存（5分钟）
_events_cache = {}
_events_cache_time = 0


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
            time_frame TEXT,
            evidence TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(market_events)").fetchall()]
    if "verification_result" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN verification_result TEXT")
    if "time_frame" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN time_frame TEXT")
    if "evidence" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN evidence TEXT")
    # P1-2: 原始置信度/方向（校准前），供前端展示"原始 vs 校准后"
    if "original_confidence" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN original_confidence REAL")
    if "original_direction" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN original_direction TEXT")
    # ── Batch1 增强点 3：事件影响量化 ──
    if "expected_impact_pct" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN expected_impact_pct REAL")  # 预估影响幅度（如 3.5 = +3.5%）
    if "impact_direction" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN impact_direction TEXT")     # 影响方向（up/down/flat）
    if "impact_duration" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN impact_duration TEXT")     # 影响持续期（short_term/medium_term/long_term）
    if "impact_analysis" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN impact_analysis TEXT")     # LLM 影响分析全文（缓存）
    if "impact_analyzed_at" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN impact_analyzed_at TEXT")   # 分析时间戳
    # 事件影响时间跨度（short_term / medium_term / long_term），受益标的验证窗口用
    if "impact_horizon" not in cols:
        conn.execute("ALTER TABLE market_events ADD COLUMN impact_horizon TEXT DEFAULT 'medium_term'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_status ON market_events(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_expected ON market_events(expected_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_relevance ON market_events(relevance_to_user)")

    # ── 事件受益标的表：事件 → 受益标的 → 估值 → 个性化推荐 ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_beneficiaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            index_code TEXT,
            index_name TEXT,
            vehicle_type TEXT,
            benefit_level TEXT NOT NULL,        -- strong / medium / weak
            benefit_logic TEXT NOT NULL,
            valuation_percentile REAL,
            valuation_status TEXT,              -- undervalued / fair / overvalued / expensive / unknown
            is_holding INTEGER DEFAULT 0,
            is_watching INTEGER DEFAULT 0,
            recommendation_tier TEXT,           -- strong_buy / watch / add_position / observe_only
            match_score REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (event_id) REFERENCES market_events(event_id) ON DELETE CASCADE,
            UNIQUE (event_id, fund_code)
        )
    """)
    # verification_result：T+N 相对收益验证结果（JSON）
    ben_cols = [r[1] for r in conn.execute("PRAGMA table_info(event_beneficiaries)").fetchall()]
    if "verification_result" not in ben_cols:
        conn.execute("ALTER TABLE event_beneficiaries ADD COLUMN verification_result TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_beneficiaries_event ON event_beneficiaries(event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_beneficiaries_tier ON event_beneficiaries(recommendation_tier)")


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


def _update_similar_event(
    conn, event_id: str, detected_date: str, summary: str, direction: str,
    confidence: float, affected_sectors: list, affected_themes: list,
    sources: list, time_frame: str, evidence: str,
    original_confidence: float | None, original_direction: str | None,
) -> None:
    """P0 修复（2026-08-03）：相似事件命中时更新已有记录的关键字段。

    原问题：create_market_event 发现相似事件后直接 return，不更新任何字段，
    导致 detected_date/updated_at/confidence/direction/sources 停留在旧值，
    前端按 detected_date 排序看不到"新"记录。

    修复：更新 detected_date（让前端排在最前）+ updated_at + confidence + direction +
    sources + summary + affected_sectors/themes，让重复扫描能刷新已有事件。
    """
    # 合并 sources（去重，保留已有 + 新增）
    try:
        existing_row = conn.execute(
            "SELECT sources FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        old_sources = json.loads(existing_row["sources"]) if existing_row and existing_row["sources"] else []
        merged_sources = old_sources[:]
        for s in (sources or []):
            if s not in merged_sources:
                merged_sources.append(s)
        # 最多保留 10 条 sources
        merged_sources = merged_sources[-10:]
    except Exception:
        merged_sources = sources or []

    conn.execute("""
        UPDATE market_events SET
            detected_date = ?,
            updated_at = datetime('now','localtime'),
            summary = ?,
            direction = ?,
            confidence = ?,
            sources = ?,
            time_frame = ?,
            evidence = ?,
            original_confidence = ?,
            original_direction = ?,
            affected_sectors = CASE WHEN ? IS NOT NULL THEN ? ELSE affected_sectors END,
            affected_themes = CASE WHEN ? IS NOT NULL THEN ? ELSE affected_themes END
        WHERE event_id = ?
    """, (
        detected_date,
        summary,
        direction,
        confidence,
        json.dumps(merged_sources, ensure_ascii=False),
        time_frame,
        evidence,
        original_confidence if original_confidence is not None else confidence,
        original_direction if original_direction is not None else direction,
        json.dumps(affected_sectors, ensure_ascii=False) if affected_sectors else None,
        json.dumps(affected_sectors, ensure_ascii=False) if affected_sectors else None,
        json.dumps(affected_themes, ensure_ascii=False) if affected_themes else None,
        json.dumps(affected_themes, ensure_ascii=False) if affected_themes else None,
        event_id,
    ))
    conn.commit()
    # P0 修复（2026-08-03）：相似事件更新后必须清除列表缓存，
    # 否则扫描后前端 loadEvents 命中 5 分钟缓存，返回旧数据，用户看不到"新"事件。
    _clear_events_cache()


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
    time_frame: str = "",
    evidence: str = "",
    original_confidence: float | None = None,
    original_direction: str | None = None,
) -> str:
    """创建事件（幂等：相同 title+expected_date 不重复创建）。

    增强去重：除了精确匹配，还会检测标题相似度 > 0.6 的同类事件。

    Args:
        time_frame: 趋势时间跨度（short/medium/long），趋势类型事件使用
        evidence: 趋势证据，趋势类型事件使用
        original_confidence: 校准前的原始置信度（P1-2 前端展示用）
        original_direction: 校准前的原始方向（P1-1 方向降级追踪用）

    Returns:
        event_id（已存在则返回已有 id，不覆盖）
    """
    event_id = _gen_event_id(title, expected_date or "")
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT event_id FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing:
            return event_id

        # 增强去重：检测标题相似度 >= 0.6 的同类事件
        all_events = conn.execute(
            "SELECT event_id, title, expected_date FROM market_events"
        ).fetchall()
        for row in all_events:
            # ── P0-I 修复：原逻辑要求 expected_date 完全相同，导致 LLM 微调日期即可绕过 ──
            # 问题案例：同事件 LLM 给出 "7-17"/"7-15" 等不同日期 → 去重失效，17 条重复
            # 新逻辑：expected_date 在 ±3 天范围内 + 相似度 >= 0.6 即判重
            row_date_str = row["expected_date"] or ""
            new_date_str = expected_date or ""
            if row_date_str and new_date_str:
                try:
                    row_date = datetime.strptime(row_date_str, "%Y-%m-%d")
                    new_date = datetime.strptime(new_date_str, "%Y-%m-%d")
                    date_diff = abs((row_date - new_date).days)
                except (ValueError, TypeError):
                    date_diff = 0 if row_date_str == new_date_str else 999
            else:
                date_diff = 0 if row_date_str == new_date_str else 999

            if date_diff <= 3 and _title_similarity(title, row["title"]) >= 0.6:
                # P0 修复（2026-08-03）：相似事件不再跳过，而是更新已有记录的关键字段
                # 原问题：相似事件直接 return，导致 detected_date/updated_at/confidence/direction/sources
                # 都不更新，前端按 detected_date 排序看不到"新"记录，用户感觉"扫描了但没新数据"
                # 修复：更新 detected_date + updated_at + confidence + direction + sources + summary
                logger.info(
                    f"[market_events] 检测到相似事件(日期差{date_diff}天)，更新已有记录: "
                    f"'{title}' -> '{row['title']}'"
                )
                _update_similar_event(
                    conn, row["event_id"], today, summary, direction, confidence,
                    affected_sectors, affected_themes, sources, time_frame, evidence,
                    original_confidence, original_direction,
                )
                return row["event_id"]

        timeline = json.dumps(
            [{"date": today, "event": "首次检测"}], ensure_ascii=False
        )
        conn.execute("""
            INSERT INTO market_events (
                event_id, title, summary, event_type, status, direction, confidence,
                expected_date, detected_date, affected_sectors, affected_themes,
                relevance_to_user, sources, timeline, time_frame, evidence,
                original_confidence, original_direction
            ) VALUES (?, ?, ?, ?, 'upcoming', ?, ?, ?, ?, ?, ?, 'market_watch', ?, ?, ?, ?, ?, ?)
        """, (
            event_id, title, summary, event_type, direction, confidence,
            expected_date, today,
            json.dumps(affected_sectors, ensure_ascii=False),
            json.dumps(affected_themes, ensure_ascii=False),
            json.dumps(sources, ensure_ascii=False),
            timeline,
            time_frame,
            evidence,
            original_confidence if original_confidence is not None else confidence,
            original_direction if original_direction is not None else direction,
        ))
        conn.commit()
        _clear_events_cache()
        return event_id
    finally:
        conn.close()


def update_market_event_fields(event_id: str, fields: dict) -> bool:
    """批量更新事件字段（回溯校准用）。

    Args:
        event_id: 事件 ID
        fields: 要更新的字段字典，如 {"confidence": 0.6, "direction": "neutral"}

    Returns:
        True if updated, False if event not found.
    """
    if not fields:
        return False
    conn = _get_conn()
    try:
        allowed = {"confidence", "direction", "status", "relevance_to_user",
                   "original_confidence", "original_direction",
                   # Batch1 增强点 3：事件影响量化字段
                   "expected_impact_pct", "impact_direction", "impact_duration",
                   "impact_analysis", "impact_analyzed_at",
                   # O-2/O-8（2026-07-21）：backfill sources 字段
                   "sources"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        # sources/matched_holdings 等 JSON 字段需要序列化
        json_fields = {"sources", "matched_holdings", "candidate_funds",
                       "affected_sectors", "affected_themes", "timeline",
                       "verification_result", "evidence"}
        serialized_updates = {}
        for k, v in updates.items():
            if k in json_fields and not isinstance(v, str):
                serialized_updates[k] = json.dumps(v, ensure_ascii=False)
            else:
                serialized_updates[k] = v
        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(serialized_updates.values()) + [event_id]
        cursor = conn.execute(
            f"UPDATE market_events SET {set_clauses}, updated_at = datetime('now','localtime') WHERE event_id = ?",
            values,
        )
        conn.commit()
        if cursor.rowcount > 0:
            _clear_events_cache()
            return True
        return False
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


def _clear_events_cache():
    """清除事件列表缓存（写入时调用）。"""
    global _events_cache, _events_cache_time
    _events_cache = {}
    _events_cache_time = 0

def list_market_events(
    status: Optional[str] = None,
    relevance: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """查询事件列表（可按 status/relevance 过滤）。"""
    global _events_cache, _events_cache_time
    
    cache_key = f"{status}_{relevance}_{limit}"
    now = time.time()
    if now - _events_cache_time < 5 * 60 and cache_key in _events_cache:
        logger.info("[market_events] 使用事件列表缓存")
        return _events_cache[cache_key]
    
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
    # P0 修复（2026-08-04）：排序统一为 detected_date DESC, expected_date DESC
    # 原问题：expected_date ASC 导致扫描后新检测的远期事件排在底部，用户感知"扫描没生效"。
    # 修复：detected_date DESC（最新检测排顶部）+ expected_date DESC（同检测时间下最新预期事件靠前）。
    # 前端 filteredEvents 已对齐此排序逻辑。
    sql += " ORDER BY detected_date DESC, expected_date DESC LIMIT ?"
    params.append(limit)

    conn = _get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        result = [dict(r) for r in rows]
        _events_cache[cache_key] = result
        _events_cache_time = now
        return result
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


def update_market_event_status(event_id: str, new_status: str, timeline_note: str = "") -> bool:
    """更新事件状态，追加 timeline 记录。

    Args:
        event_id: 事件 ID
        new_status: 新状态
        timeline_note: 自定义 timeline 文本（如回溯校准说明），为空则用默认"状态更新为 X"

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
        note = timeline_note if timeline_note else f"状态更新为 {new_status}"
        timeline.append({"date": today, "event": note})

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
        _clear_events_cache()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_market_event(event_id: str) -> bool:
    """删除事件。"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM market_events WHERE event_id = ?", (event_id,))
        conn.commit()
        _clear_events_cache()
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


def list_events_by_date_range(
    start_date: str,
    end_date: str,
    event_types: tuple = None,
    limit: int = 200,
) -> list[dict]:
    """LI-5（2026-07-22）：按日期范围和事件类型查询领先指标事件。

    Args:
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        event_types: 事件类型元组（如 ('policy_draft', 'capex_announcement')），None 则不过滤
        limit: 最多返回条数

    Returns:
        list[dict] 事件列表
    """
    conn = _get_conn()
    try:
        sql = (
            "SELECT * FROM market_events "
            "WHERE detected_date >= ? AND detected_date <= ? "
        )
        params: list = [start_date, end_date]
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            sql += f"AND event_type IN ({placeholders}) "
            params.extend(event_types)
        sql += "ORDER BY detected_date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── 事件受益标的 CRUD（event_beneficiaries 表）────────────────────────


def save_event_beneficiaries(event_id: str, beneficiaries: list[dict]) -> int:
    """批量写入受益标的（先删除该 event_id 的旧记录再插入），返回写入数量。

    Args:
        event_id: 事件 ID
        beneficiaries: discover_beneficiaries 输出的标的列表，每个 dict 需含
            fund_code / fund_name / benefit_level / benefit_logic 等字段
    """
    if not event_id:
        return 0
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM event_beneficiaries WHERE event_id = ?", (event_id,))
        inserted = 0
        for b in beneficiaries or []:
            fund_code = b.get("fund_code", "")
            if not fund_code:
                continue
            conn.execute("""
                INSERT INTO event_beneficiaries (
                    event_id, fund_code, fund_name, index_code, index_name,
                    vehicle_type, benefit_level, benefit_logic,
                    valuation_percentile, valuation_status,
                    is_holding, is_watching, recommendation_tier, match_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, fund_code, b.get("fund_name", ""),
                b.get("index_code"), b.get("index_name"),
                b.get("vehicle_type"), b.get("benefit_level", "weak"),
                b.get("benefit_logic", ""),
                b.get("valuation_percentile"), b.get("valuation_status", "unknown"),
                int(b.get("is_holding", 0) or 0), int(b.get("is_watching", 0) or 0),
                b.get("recommendation_tier"), float(b.get("match_score", 0) or 0),
            ))
            inserted += 1
        conn.commit()
        logger.info(f"[market_events] 保存受益标的 event_id={event_id} 共 {inserted} 条")
        return inserted
    finally:
        conn.close()


def list_event_beneficiaries(event_id: str) -> list[dict]:
    """查询某事件的受益标的列表（按 match_score 降序、benefit_level 优先级排序）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM event_beneficiaries WHERE event_id = ? "
            "ORDER BY match_score DESC, CASE benefit_level "
            "WHEN 'strong' THEN 1 WHEN 'medium' THEN 2 WHEN 'weak' THEN 3 ELSE 4 END",
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_beneficiary_stats(days: int = 30) -> dict:
    """统计推荐分布（tier_distribution / valuation_distribution / top_recommended）。

    Args:
        days: 统计最近 N 天的受益标的
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM event_beneficiaries WHERE created_at >= ?",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    beneficiaries = [dict(r) for r in rows]

    # 推荐分级分布
    tier_distribution: dict[str, int] = {}
    valuation_distribution: dict[str, int] = {}
    for b in beneficiaries:
        tier = b.get("recommendation_tier") or "unknown"
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1
        vst = b.get("valuation_status") or "unknown"
        valuation_distribution[vst] = valuation_distribution.get(vst, 0) + 1

    # 推荐度最高的标的（match_score 排序，取前 10）
    top_sorted = sorted(
        beneficiaries,
        key=lambda x: x.get("match_score", 0) or 0,
        reverse=True,
    )[:10]
    top_recommended = [
        {
            "fund_code": b.get("fund_code"),
            "fund_name": b.get("fund_name"),
            "event_id": b.get("event_id"),
            "benefit_level": b.get("benefit_level"),
            "recommendation_tier": b.get("recommendation_tier"),
            "match_score": b.get("match_score"),
            "valuation_status": b.get("valuation_status"),
        }
        for b in top_sorted
    ]

    # 按基金代码聚合统计（推荐次数 / 平均估值分位 / 最新分级）
    fund_agg: dict[str, dict] = {}
    for b in beneficiaries:
        code = b.get("fund_code") or ""
        if not code:
            continue
        if code not in fund_agg:
            fund_agg[code] = {
                "fund_code": code,
                "fund_name": b.get("fund_name") or code,
                "recommend_count": 0,
                "_valuation_sum": 0.0,
                "_valuation_n": 0,
                "_latest_created": "",
                "last_tier": None,
            }
        agg = fund_agg[code]
        agg["recommend_count"] += 1
        vp = b.get("valuation_percentile")
        if vp is not None:
            try:
                agg["_valuation_sum"] += float(vp)
                agg["_valuation_n"] += 1
            except (TypeError, ValueError):
                pass
        created = b.get("created_at") or ""
        if created > agg["_latest_created"]:
            agg["_latest_created"] = created
            agg["last_tier"] = b.get("recommendation_tier")
    # 汇总输出（推荐次数降序，取前 10）
    top_funds = []
    for agg in sorted(fund_agg.values(), key=lambda x: x["recommend_count"], reverse=True)[:10]:
        avg_val = round(agg["_valuation_sum"] / agg["_valuation_n"], 1) if agg["_valuation_n"] else None
        top_funds.append({
            "fund_code": agg["fund_code"],
            "fund_name": agg["fund_name"],
            "recommend_count": agg["recommend_count"],
            "avg_valuation": avg_val,
            "last_tier": agg["last_tier"],
        })

    return {
        "days": days,
        "total": len(beneficiaries),
        "tier_distribution": tier_distribution,
        "valuation_distribution": valuation_distribution,
        "top_recommended": top_recommended,
        "top_funds": top_funds,
    }


def update_beneficiary_verification(event_id: str, fund_results: list[dict]) -> bool:
    """更新单个标的的验证结果（写入 verification_result 字段）。

    Args:
        event_id: 事件 ID
        fund_results: [{"fund_code": str, "verification_result": {...}}]

    Returns:
        True if 至少更新一条，False otherwise
    """
    if not event_id or not fund_results:
        return False
    conn = _get_conn()
    try:
        updated = 0
        for item in fund_results:
            fund_code = item.get("fund_code", "")
            if not fund_code:
                continue
            result = item.get("verification_result") or {}
            conn.execute(
                "UPDATE event_beneficiaries SET verification_result = ? "
                "WHERE event_id = ? AND fund_code = ?",
                (json.dumps(result, ensure_ascii=False), event_id, fund_code),
            )
            updated += 1
        conn.commit()
        logger.info(f"[market_events] 更新受益标的验证结果 event_id={event_id} 共 {updated} 条")
        return updated > 0
    finally:
        conn.close()
