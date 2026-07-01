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
                summary, reasoning, key_variables, data_basis, confidence, urgent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                      confidence, urgent, created_at, expires_at
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
                      confidence, urgent, created_at, expires_at
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
