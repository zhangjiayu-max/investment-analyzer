"""
跨系统桥接层 — 分析结论数据层。

provides:
  save_analysis_conclusion(...)          — 插入一条分析结论
  get_latest_analysis_conclusions(...)   — 获取最近结论
  get_conclusions_by_target(...)         — 获取某目标的所有结论
  get_conflicting_conclusions(...)       — 检测同一目标存在相反 action 的冲突
  init_conclusions_tables(conn)          — 建表（DDL）

设计稿: docs/designs/2026-07-01-cross-system-bridge-layer.md Section 4.1
"""

from __future__ import annotations

import json
import logging

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# ── DDL ──────────────────────────────────────────────────


def init_conclusions_tables(conn=None):
    """创建 analysis_conclusions 与 cross_system_references 表（幂等）。"""
    close_conn = False
    if conn is None:
        conn = _get_conn()
        close_conn = True

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_conclusions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_system TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id INTEGER,
                target_subject TEXT NOT NULL,
                action TEXT,
                summary TEXT NOT NULL,
                reasoning TEXT,
                key_variables TEXT,
                data_basis TEXT,
                market_context_at_time TEXT,
                confidence REAL DEFAULT 0.5,
                urgent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                expires_at TEXT DEFAULT (datetime('now','localtime','+24 hours'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cross_system_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_conclusion_id INTEGER REFERENCES analysis_conclusions(id),
                target_conclusion_id INTEGER REFERENCES analysis_conclusions(id),
                relationship TEXT NOT NULL,
                reason TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 索引
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conclusions_target "
            "ON analysis_conclusions(target_subject)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conclusions_source "
            "ON analysis_conclusions(source_system, source_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conclusions_time "
            "ON analysis_conclusions(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conclusions_keywords "
            "ON analysis_conclusions(target_subject, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_refs_source "
            "ON cross_system_references(source_conclusion_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_refs_target "
            "ON cross_system_references(target_conclusion_id)"
        )

        # P1-1：扩展字段（幂等 ALTER） — 关联 AI 对话的 conversation_id / message_id
        try:
            from db._utils import _add_column_if_not_exists
            _add_column_if_not_exists(conn, "analysis_conclusions", "conversation_id", "INTEGER")
            _add_column_if_not_exists(conn, "analysis_conclusions", "message_id", "INTEGER")
        except Exception as e:
            logger.debug(f"add_column conversation_id/message_id 跳过: {e}")

        # 幂等检查索引 — 用于同 conversation_id+message_id 已有记录的快速判断
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ac_conv_msg "
            "ON analysis_conclusions(conversation_id, message_id)"
        )

        conn.commit()
    except Exception as e:
        logger.warning(f"init_conclusions_tables 失败: {e}")
    finally:
        if close_conn:
            try:
                conn.close()
            except Exception:
                pass


# ── CRUD ─────────────────────────────────────────────────


def save_analysis_conclusion(
    source_system: str,
    source_type: str,
    source_id: int | None,
    target_subject: str,
    action: str | None,
    summary: str,
    reasoning: str | None = None,
    key_variables: list | None = None,
    data_basis: list | None = None,
    confidence: float = 0.5,
    urgent: int = 0,
    conversation_id: int | None = None,
    message_id: int | None = None,
) -> int | None:
    """
    插入一条分析结论。

    Args:
        source_system:  来源大系统 — 'ai_dialogue' | 'independent_analysis'
        source_type:    子类型 — 'daily_report' | 'deep_dive' | 'orchestrator' | ...
        source_id:      源记录ID（可为 None）
        target_subject: 结论标的 — 基金代码、组合名称、品类名称
        action:         建议操作 — 'buy' | 'sell' | 'hold' | 'increase' | 'decrease' | ...
        summary:        核心结论（≤100字）
        reasoning:      核心理由
        key_variables:  驱动结论的关键变量列表
        data_basis:     所引用的数据源列表
        confidence:     置信度 0-1
        urgent:         是否紧急 0/1
        conversation_id: P1-1 关联 AI 对话 ID（ai_dialogue 路径专用）
        message_id:      P1-1 关联 AI 对话消息 ID

    Returns:
        插入记录的 id，失败返回 None
    """
    try:
        conn = _get_conn()

        key_vars_json = json.dumps(key_variables, ensure_ascii=False) if key_variables else None
        data_basis_json = json.dumps(data_basis, ensure_ascii=False) if data_basis else None

        cur = conn.execute(
            """INSERT INTO analysis_conclusions
               (source_system, source_type, source_id, target_subject, action,
                summary, reasoning, key_variables, data_basis, confidence, urgent,
                conversation_id, message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_system,
                source_type,
                source_id,
                target_subject,
                action or "",
                summary,
                reasoning or "",
                key_vars_json,
                data_basis_json,
                confidence,
                urgent,
                conversation_id,
                message_id,
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id
    except Exception as e:
        logger.warning(f"save_analysis_conclusion 失败: {e}")
        return None


def get_latest_analysis_conclusions(
    user_id: str = "default",
    hours: int = 24,
    limit: int = 5,
) -> list[dict]:
    """
    获取最近 N 小时的分析结论（按创建时间倒序）。

    Args:
        user_id: 用户ID（预留，暂未使用）
        hours:   时间窗口（小时）
        limit:   最大返回条数

    Returns:
        [{id, source_system, source_type, target_subject, action, summary, ...}, ...]
        失败返回空列表
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, source_system, source_type, source_id, target_subject,
                      action, summary, reasoning, key_variables, data_basis,
                      confidence, urgent, created_at, expires_at,
                      conversation_id, message_id
               FROM analysis_conclusions
               WHERE created_at >= datetime('now', 'localtime', ?)
               ORDER BY created_at DESC
               LIMIT ?""",
            (f"-{hours} hours", limit),
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            d = dict(r)
            for field in ("key_variables", "data_basis"):
                if d.get(field) and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results
    except Exception as e:
        logger.warning(f"get_latest_analysis_conclusions 失败: {e}")
        return []


def get_conclusions_by_target(
    target_subject: str,
    hours: int = 24,
) -> list[dict]:
    """
    获取指定标的在最近 N 小时内的所有分析结论。

    Returns:
        [{id, source_system, source_type, action, summary, ...}, ...]
        失败返回空列表
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, source_system, source_type, source_id, target_subject,
                      action, summary, reasoning, key_variables, data_basis,
                      confidence, urgent, created_at, expires_at,
                      conversation_id, message_id
               FROM analysis_conclusions
               WHERE target_subject = ?
                 AND created_at >= datetime('now', 'localtime', ?)
               ORDER BY created_at DESC""",
            (target_subject, f"-{hours} hours"),
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            d = dict(r)
            for field in ("key_variables", "data_basis"):
                if d.get(field) and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(d)
        return results
    except Exception as e:
        logger.warning(f"get_conclusions_by_target 失败: {e}")
        return []


def get_related_orchestrator_decisions(
    keywords: list[str],
    hours: int = 48,
    limit: int = 3,
) -> list[dict]:
    """
    根据关键词查找 source_system='ai_dialogue' 的相关分析结论。

    用于独立分析报告末尾关联最近AI对话内容（桥接B）。

    Args:
        keywords: 关键词列表（基金代码、名称、话题标签等）
        hours:    时间窗口（小时）
        limit:    最大返回条数

    Returns:
        [{id, source_type, target_subject, action, summary, reasoning, created_at}, ...]
        失败返回空列表
    """
    if not keywords:
        return []
    try:
        conn = _get_conn()
        # 用 LIKE 做关键词匹配
        conditions = []
        params = []
        for kw in keywords:
            kw_clean = kw.strip()
            if not kw_clean:
                continue
            pattern = f"%{kw_clean}%"
            conditions.append(
                "(target_subject LIKE ? OR summary LIKE ? OR reasoning LIKE ?)"
            )
            params.extend([pattern, pattern, pattern])

        if not conditions:
            conn.close()
            return []

        where_clause = " OR ".join(conditions)
        sql = f"""SELECT id, source_system, source_type, target_subject,
                         action, summary, reasoning, confidence, created_at
                  FROM analysis_conclusions
                  WHERE source_system = 'ai_dialogue'
                    AND ({where_clause})
                    AND created_at >= datetime('now', 'localtime', ?)
                  ORDER BY created_at DESC
                  LIMIT ?"""
        params.extend([f"-{hours} hours", limit])

        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_related_orchestrator_decisions 失败: {e}")
        return []


def get_conflicting_conclusions(
    target_subject: str,
    hours: int = 24,
) -> list[dict]:
    """
    检测同一标的在时间窗口内是否存在相反操作方向的结论。

    冲突判定规则：
        - buy / increase   ↔  sell / decrease / clear
        - 即至少一方是"做多"(buy/increase)，另一方是"做空"(sell/decrease/clear)

    Returns:
        如存在冲突，按冲突对分组返回 [{id_a, action_a, id_b, action_b, ...}, ...]
        无冲突或失败返回空列表
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, action
               FROM analysis_conclusions
               WHERE target_subject = ?
                 AND created_at >= datetime('now', 'localtime', ?)
                 AND action IS NOT NULL
                 AND action != ''
               ORDER BY created_at DESC""",
            (target_subject, f"-{hours} hours"),
        ).fetchall()
        conn.close()

        if len(rows) < 2:
            return []

        # 分组
        bullish_actions = {"buy", "increase"}
        bearish_actions = {"sell", "decrease", "clear"}

        bullish = []
        bearish = []

        for r in rows:
            action = (r["action"] or "").lower().strip()
            if action in bullish_actions:
                bullish.append(dict(r))
            elif action in bearish_actions:
                bearish.append(dict(r))

        if not bullish or not bearish:
            return []

        # 构建冲突对（笛卡尔积）
        conflicts = []
        for b_bull in bullish:
            for b_bear in bearish:
                conflicts.append({
                    "target_subject": target_subject,
                    "conclusion_a_id": b_bull["id"],
                    "conclusion_a_action": b_bull["action"],
                    "conclusion_b_id": b_bear["id"],
                    "conclusion_b_action": b_bear["action"],
                })

        return conflicts
    except Exception as e:
        logger.warning(f"get_conflicting_conclusions 失败: {e}")
        return []


def cleanup_expired_conclusions() -> int:
    """清理 analysis_conclusions 表中已过期的记录。

    设计稿 P0-3.3：桥接层激活后的定期清理任务。
    表默认 expires_at = created_at + 24h，本函数删除 expires_at < now 的记录。

    Returns:
        删除条数
    """
    try:
        conn = _get_conn()
        cur = conn.execute(
            "DELETE FROM analysis_conclusions WHERE expires_at < datetime('now','localtime')"
        )
        # 同时清理孤立的 cross_system_references（引用的 conclusion 已删除）
        conn.execute(
            "DELETE FROM cross_system_references "
            "WHERE source_conclusion_id NOT IN (SELECT id FROM analysis_conclusions) "
            "OR target_conclusion_id NOT IN (SELECT id FROM analysis_conclusions)"
        )
        conn.commit()
        deleted = cur.rowcount or 0
        conn.close()
        if deleted > 0:
            logger.info(f"清理过期分析结论 {deleted} 条")
        return deleted
    except Exception as e:
        logger.warning(f"cleanup_expired_conclusions 失败: {e}")
        return 0


# ── P1-1：幂等检查 — 同 conversation_id+message_id 已有记录则跳过 ──


def has_conclusions_for_message(conversation_id: int, message_id: int) -> bool:
    """检查指定的 (conversation_id, message_id) 是否已持久化过结论。

    用于 orchestrator 收尾时避免重复写入。
    """
    if not conversation_id or not message_id:
        return False
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM analysis_conclusions "
            "WHERE conversation_id = ? AND message_id = ?",
            (conversation_id, message_id),
        ).fetchone()
        conn.close()
        return bool(row and row["cnt"] and row["cnt"] > 0)
    except Exception as e:
        logger.warning(f"has_conclusions_for_message 失败: {e}")
        return False  # 查询失败时返回 False（保守写入，宁可重复也不丢）


# ── P1-3：跨系统桥接 — 把 conclusion 链接到相关 decision_records ──


def link_conclusion_to_decisions(conclusion_id: int, target_subject: str) -> int:
    """把一条 analysis_conclusion 链接到所有匹配的已接受 decision_records。

    匹配规则：decision_records.target_code LIKE target_subject AND status='accepted'。
    对每条匹配的决策，INSERT OR IGNORE 到 cross_system_references。

    Args:
        conclusion_id: analysis_conclusions.id
        target_subject: 结论标的（基金代码或板块名）

    Returns:
        新建链接条数
    """
    if not conclusion_id or not target_subject:
        return 0
    try:
        conn = _get_conn()
        # 查找匹配的已接受决策（target_code 含目标标的）
        # 注：decision_records 表在 db/portfolio.py 创建
        rows = conn.execute(
            "SELECT id FROM decision_records "
            "WHERE target_code LIKE ? AND status = 'accepted'",
            (f"%{target_subject}%",),
        ).fetchall()
        if not rows:
            conn.close()
            return 0
        inserted = 0
        for r in rows:
            try:
                # cross_system_references 无 UNIQUE 约束，用 NOT EXISTS 子查询去重
                conn.execute(
                    """INSERT INTO cross_system_references
                       (source_conclusion_id, target_conclusion_id, relationship, reason)
                       SELECT ?, ?, ?, ?
                       WHERE NOT EXISTS (
                           SELECT 1 FROM cross_system_references
                           WHERE source_conclusion_id = ?
                             AND target_conclusion_id = ?
                             AND relationship = 'informs_decision'
                       )""",
                    (
                        conclusion_id,
                        r["id"],
                        "informs_decision",
                        f"自动桥接：结论标的 {target_subject} 关联到已接受决策",
                        conclusion_id,
                        r["id"],
                    ),
                )
                inserted += 1
            except Exception:
                continue
        conn.commit()
        conn.close()
        if inserted > 0:
            logger.info(f"[P1-3] 链接 conclusion#{conclusion_id} → {inserted} 条决策")
        return inserted
    except Exception as e:
        logger.warning(f"link_conclusion_to_decisions 失败: {e}")
        return 0
