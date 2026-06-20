"""理财决策档案数据层。"""

from __future__ import annotations

import json

from db._conn import _get_conn

VALID_DECISION_STATUSES = {
    "proposed",
    "accepted",
    "rejected",
    "deferred",
    "executed",
    "expired",
    "reviewed",
}
VALID_ACTION_STATUSES = {"todo", "doing", "done", "skipped"}
VALID_REVIEW_OUTCOMES = {"helpful", "neutral", "unhelpful"}


def init_decision_tables(conn):
    """初始化决策档案与行动清单表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            source_type TEXT NOT NULL,
            source_id INTEGER,
            decision_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_code TEXT DEFAULT '',
            target_name TEXT DEFAULT '',
            summary TEXT NOT NULL,
            rationale TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '{}',
            risk_json TEXT DEFAULT '{}',
            suitability_json TEXT DEFAULT '{}',
            confidence TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'proposed',
            user_note TEXT DEFAULT '',
            due_at TEXT,
            review_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_user ON decision_records(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_status ON decision_records(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_created ON decision_records(created_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL REFERENCES decision_records(id) ON DELETE CASCADE,
            action_type TEXT NOT NULL,
            title TEXT NOT NULL,
            params_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'todo',
            scheduled_at TEXT,
            completed_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_actions_decision ON decision_actions(decision_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_actions_status ON decision_actions(status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL UNIQUE REFERENCES decision_records(id) ON DELETE CASCADE,
            outcome TEXT NOT NULL,
            result_note TEXT DEFAULT '',
            profit_change REAL,
            lesson TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_reviews_decision ON decision_reviews(decision_id)")


def _json_dumps(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_loads(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, TypeError):
        return fallback


def _row_to_decision(row, actions: list[dict] | None = None) -> dict:
    item = dict(row)
    item["evidence_json"] = _json_loads(item.get("evidence_json"), {})
    item["risk_json"] = _json_loads(item.get("risk_json"), {})
    item["suitability_json"] = _json_loads(item.get("suitability_json"), {})
    item["actions"] = actions or []
    return item


def _row_to_action(row) -> dict:
    item = dict(row)
    item["params_json"] = _json_loads(item.get("params_json"), {})
    return item


def _row_to_review(row) -> dict:
    return dict(row) if row else {}


def _format_ratio(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.0%}"
    return ""


def _format_percent(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}%" if value % 1 else f"{value:.0f}%"
    return ""


def create_decision(
    source_type: str,
    decision_type: str,
    target_type: str,
    summary: str,
    user_id: str = "default",
    source_id: int | None = None,
    target_code: str = "",
    target_name: str = "",
    rationale: str = "",
    evidence: dict | None = None,
    risk: dict | None = None,
    suitability: dict | None = None,
    confidence: str = "medium",
    status: str = "proposed",
    due_at: str | None = None,
    review_at: str | None = None,
    actions: list[dict] | None = None,
) -> int:
    """创建一条理财决策档案，可同时创建行动项。"""
    if status not in VALID_DECISION_STATUSES:
        status = "proposed"

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO decision_records
                (user_id, source_type, source_id, decision_type, target_type,
                 target_code, target_name, summary, rationale, evidence_json,
                 risk_json, suitability_json, confidence, status, due_at, review_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                source_type,
                source_id,
                decision_type,
                target_type,
                target_code or "",
                target_name or "",
                summary,
                rationale or "",
                _json_dumps(evidence),
                _json_dumps(risk),
                _json_dumps(suitability),
                confidence or "medium",
                status,
                due_at,
                review_at,
            ),
        )
        decision_id = cur.lastrowid
        for action in actions or []:
            create_decision_action(
                decision_id=decision_id,
                action_type=action.get("action_type", "review"),
                title=action.get("title", ""),
                params=action.get("params_json") or action.get("params") or {},
                status=action.get("status", "todo"),
                scheduled_at=action.get("scheduled_at"),
                conn=conn,
            )
        conn.commit()
        return decision_id
    finally:
        conn.close()


def create_decision_action(
    decision_id: int,
    action_type: str,
    title: str,
    params: dict | None = None,
    status: str = "todo",
    scheduled_at: str | None = None,
    conn=None,
) -> int:
    """创建行动项。传入 conn 时由调用方提交事务。"""
    if status not in VALID_ACTION_STATUSES:
        status = "todo"
    owns_conn = conn is None
    conn = conn or _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO decision_actions
                (decision_id, action_type, title, params_json, status, scheduled_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (decision_id, action_type, title, _json_dumps(params), status, scheduled_at),
        )
        if owns_conn:
            conn.commit()
        return cur.lastrowid
    finally:
        if owns_conn:
            conn.close()


def _load_actions(conn, decision_ids: list[int]) -> dict[int, list[dict]]:
    if not decision_ids:
        return {}
    placeholders = ",".join("?" for _ in decision_ids)
    rows = conn.execute(
        f"SELECT * FROM decision_actions WHERE decision_id IN ({placeholders}) ORDER BY id",
        decision_ids,
    ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        action = _row_to_action(row)
        grouped.setdefault(action["decision_id"], []).append(action)
    return grouped


def _load_reviews(conn, decision_ids: list[int]) -> dict[int, dict]:
    if not decision_ids:
        return {}
    placeholders = ",".join("?" for _ in decision_ids)
    rows = conn.execute(
        f"SELECT * FROM decision_reviews WHERE decision_id IN ({placeholders})",
        decision_ids,
    ).fetchall()
    return {row["decision_id"]: _row_to_review(row) for row in rows}


def _attach_reviews(conn, items: list[dict]) -> list[dict]:
    reviews = _load_reviews(conn, [item["id"] for item in items])
    for item in items:
        item["review"] = reviews.get(item["id"], {})
    return items


def list_decisions(
    user_id: str = "default",
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """列出决策档案，默认按创建时间倒序。"""
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM decision_records
                WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM decision_records
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        ids = [r["id"] for r in rows]
        actions = _load_actions(conn, ids)
        items = [_row_to_decision(row, actions.get(row["id"], [])) for row in rows]
        return _attach_reviews(conn, items)
    finally:
        conn.close()


def list_today_decisions(user_id: str = "default", limit: int = 20) -> list[dict]:
    """列出今日仍需关注的决策与行动。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM decision_records
            WHERE user_id = ?
              AND date(created_at) = date('now','localtime')
              AND status IN ('proposed', 'accepted', 'deferred')
            ORDER BY
              CASE status
                WHEN 'proposed' THEN 0
                WHEN 'accepted' THEN 1
                ELSE 2
              END,
              id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        ids = [r["id"] for r in rows]
        actions = _load_actions(conn, ids)
        items = [_row_to_decision(row, actions.get(row["id"], [])) for row in rows]
        return _attach_reviews(conn, items)
    finally:
        conn.close()


def _today_decision_exists(conn, user_id: str, source_type: str, decision_type: str,
                           target_type: str, target_code: str = "") -> bool:
    row = conn.execute(
        """
        SELECT id FROM decision_records
        WHERE user_id = ?
          AND source_type = ?
          AND decision_type = ?
          AND target_type = ?
          AND target_code = ?
          AND date(created_at) = date('now','localtime')
        LIMIT 1
        """,
        (user_id, source_type, decision_type, target_type, target_code or ""),
    ).fetchone()
    return row is not None


def ensure_dashboard_decisions(signals: dict, user_id: str = "default") -> int:
    """根据每日看板信号生成今日行动，已存在则不重复创建。"""
    created = 0
    undervalued = signals.get("undervalued_indexes") or []
    cash_management = signals.get("cash_management") or {}
    cash_suggestion = cash_management.get("suggestion") or {}
    portfolio_health = signals.get("portfolio_health") or {}
    cash_ratio = cash_suggestion.get("cash_ratio")

    for item in undervalued[:3]:
        code = item.get("index_code") or ""
        name = item.get("index_name") or code or "低估指数"
        conn = _get_conn()
        try:
            if _today_decision_exists(conn, user_id, "dashboard", "watch", "index", code):
                continue
        finally:
            conn.close()

        percentile = item.get("percentile")
        pct_text = f"{percentile:.0f}%" if isinstance(percentile, (int, float)) else "未知"
        latest_date = item.get("latest_date") or ""
        rationale = f"{name} 当前估值处于{item.get('assessment') or '低估'}区间，适合进入观察清单。"
        if isinstance(cash_ratio, (int, float)):
            rationale += f" 当前现金占比约 {cash_ratio:.0%}，执行前仍需确认资金期限和仓位上限。"

        decision_id = create_decision(
            user_id=user_id,
            source_type="dashboard",
            decision_type="watch",
            target_type="index",
            target_code=code,
            target_name=name,
            summary=f"{name} 低估，进入今日观察",
            rationale=rationale,
            evidence={
                "data_points": [
                    {
                        "name": "估值百分位",
                        "value": pct_text,
                        "source": "dashboard.undervalued_indexes",
                        "as_of": latest_date,
                        "freshness": "fresh" if latest_date else "unknown",
                    }
                ],
                "portfolio_context": {
                    "cash_ratio": f"{cash_ratio:.0%}" if isinstance(cash_ratio, (int, float)) else "",
                },
                "missing_data": ["执行前需确认目标仓位、资金期限和是否已有相关持仓"],
                "counter_arguments": ["低估可能来自盈利下修或行业基本面变化，不能仅凭分位点买入"],
            },
            risk={
                "level": "medium",
                "notes": ["低估不等于立即上涨", "建议先观察或分批执行"],
            },
            suitability={
                "notes": ["适合作为观察动作，不直接生成买入指令"],
            },
            confidence="medium",
            actions=[
                {
                    "action_type": "set_alert",
                    "title": f"跟踪 {name} 估值和仓位",
                }
            ],
        )
        if decision_id > 0:
            created += 1

    if isinstance(cash_ratio, (int, float)) and cash_ratio >= 0.2 and undervalued:
        conn = _get_conn()
        try:
            cash_exists = _today_decision_exists(conn, user_id, "dashboard", "add", "cash", "cash_plan")
        finally:
            conn.close()
        if not cash_exists:
            cash_ratio_text = _format_ratio(cash_ratio)
            opportunity_names = "、".join(
                item.get("index_name") or item.get("index_code") or "低估指数"
                for item in undervalued[:2]
            )
            balance = cash_management.get("balance")
            balance_text = f"{balance:,.0f}" if isinstance(balance, (int, float)) else ""
            rationale = f"现金占比 {cash_ratio_text} 偏高，且 {opportunity_names} 处于低估区间，可把闲置资金拆成观察、试投、复盘三步。"
            if balance_text:
                rationale += f" 当前可用零钱约 {balance_text} 元，建议先确定不会影响备用金。"
            decision_id = create_decision(
                user_id=user_id,
                source_type="dashboard",
                decision_type="add",
                target_type="cash",
                target_code="cash_plan",
                target_name="零钱配置",
                summary=f"现金占比 {cash_ratio_text}，可制定低估指数分批配置计划",
                rationale=rationale,
                evidence={
                    "data_points": [
                        {
                            "name": "现金占比",
                            "value": cash_ratio_text,
                            "source": "dashboard.cash_management",
                            "freshness": "fresh",
                        }
                    ],
                    "portfolio_context": {
                        "cash_ratio": cash_ratio_text,
                        "cash_balance": balance_text,
                        "opportunity_names": opportunity_names,
                    },
                    "missing_data": ["执行前需补充备用金目标、单次投入上限、目标权益仓位"],
                    "counter_arguments": ["先确认 3-6 个月备用金，再决定可投入金额"],
                },
                risk={
                    "level": "medium",
                    "notes": ["现金偏高不代表必须立刻买入", "低估资产仍可能继续下跌"],
                },
                suitability={
                    "cash_ratio": cash_ratio_text,
                    "notes": ["适合作为资金计划，不直接生成一次性买入指令"],
                },
                confidence="medium",
                actions=[
                    {
                        "action_type": "review_cash_plan",
                        "title": "确认备用金和分批投入上限",
                    }
                ],
            )
            if decision_id > 0:
                created += 1

    concentration_level = portfolio_health.get("concentration_level")
    top3_concentration = portfolio_health.get("top3_concentration")
    if concentration_level == "high" or (
        isinstance(top3_concentration, (int, float)) and top3_concentration >= 60
    ):
        conn = _get_conn()
        try:
            rebalance_exists = _today_decision_exists(
                conn, user_id, "dashboard", "rebalance", "portfolio", "portfolio"
            )
        finally:
            conn.close()
        if not rebalance_exists:
            top3_text = _format_percent(top3_concentration)
            max_holding = portfolio_health.get("max_holding_pct")
            max_holding_text = _format_percent(max_holding)
            assessment = portfolio_health.get("concentration_assessment") or f"前3持仓占比 {top3_text}，集中度偏高"
            decision_id = create_decision(
                user_id=user_id,
                source_type="dashboard",
                decision_type="rebalance",
                target_type="portfolio",
                target_code="portfolio",
                target_name="整体组合",
                summary=f"前3持仓占比 {top3_text}，建议做一次组合集中度复盘",
                rationale=f"{assessment}。先识别是否是主动集中配置，若不是，应检查是否需要分散到现金、债券或低相关资产。",
                evidence={
                    "data_points": [
                        {
                            "name": "前3持仓占比",
                            "value": top3_text,
                            "source": "dashboard.portfolio_health",
                            "freshness": "fresh",
                        },
                        {
                            "name": "最大单一持仓",
                            "value": max_holding_text,
                            "source": "dashboard.portfolio_health",
                            "freshness": "fresh",
                        },
                    ],
                    "portfolio_context": {
                        "holding_count": portfolio_health.get("holding_count"),
                        "total_value": portfolio_health.get("total_value"),
                        "concentration_level": concentration_level or "high",
                    },
                    "missing_data": ["需要确认每只基金的策略重叠度、是否同一风险来源"],
                    "counter_arguments": ["如果这是有意识的核心资产配置，降低集中度可能牺牲长期收益"],
                },
                risk={
                    "level": "medium",
                    "notes": ["再平衡应考虑交易费率、税费和持有期", "不要仅因集中度高而机械卖出"],
                },
                suitability={
                    "notes": ["适合作为复盘提醒，先诊断再决定是否调整"],
                },
                confidence="medium",
                actions=[
                    {
                        "action_type": "review_rebalance",
                        "title": "检查前3持仓是否策略重叠",
                    }
                ],
            )
            if decision_id > 0:
                created += 1
    return created


def get_decision(decision_id: int) -> dict | None:
    """获取单条决策档案。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM decision_records WHERE id = ?", (decision_id,)).fetchone()
        if not row:
            return None
        actions = _load_actions(conn, [decision_id])
        item = _row_to_decision(row, actions.get(decision_id, []))
        item["review"] = _load_reviews(conn, [decision_id]).get(decision_id, {})
        return item
    finally:
        conn.close()


def list_due_decision_reviews(user_id: str = "default", limit: int = 20) -> list[dict]:
    """列出到期需要复盘的决策。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM decision_records
            WHERE user_id = ?
              AND status IN ('accepted', 'executed', 'deferred')
              AND review_at IS NOT NULL
              AND date(review_at) <= date('now','localtime')
              AND id NOT IN (SELECT decision_id FROM decision_reviews)
            ORDER BY review_at ASC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        ids = [r["id"] for r in rows]
        actions = _load_actions(conn, ids)
        items = [_row_to_decision(row, actions.get(row["id"], [])) for row in rows]
        return _attach_reviews(conn, items)
    finally:
        conn.close()


def record_decision_review(
    decision_id: int,
    outcome: str,
    result_note: str = "",
    profit_change: float | None = None,
    lesson: str = "",
) -> int:
    """记录决策复盘并把决策状态标记为已复盘。"""
    if outcome not in VALID_REVIEW_OUTCOMES:
        return 0
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM decision_records WHERE id = ?", (decision_id,)).fetchone()
        if not existing:
            return 0
        cur = conn.execute(
            """
            INSERT INTO decision_reviews
                (decision_id, outcome, result_note, profit_change, lesson)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(decision_id) DO UPDATE SET
                outcome = excluded.outcome,
                result_note = excluded.result_note,
                profit_change = excluded.profit_change,
                lesson = excluded.lesson,
                updated_at = datetime('now','localtime')
            """,
            (decision_id, outcome, result_note or "", profit_change, lesson or ""),
        )
        conn.execute(
            """
            UPDATE decision_records
            SET status = 'reviewed', updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (decision_id,),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM decision_reviews WHERE decision_id = ?", (decision_id,)).fetchone()
        return row["id"] if row else 0
    finally:
        conn.close()


def update_decision_status(decision_id: int, status: str, user_note: str = "") -> bool:
    """更新决策状态。"""
    if status not in VALID_DECISION_STATUSES:
        return False
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE decision_records
            SET status = ?, user_note = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (status, user_note or "", decision_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_decision_action_status(action_id: int, status: str) -> bool:
    """更新行动项状态。"""
    if status not in VALID_ACTION_STATUSES:
        return False
    completed_expr = "datetime('now','localtime')" if status == "done" else "NULL"
    conn = _get_conn()
    try:
        cur = conn.execute(
            f"""
            UPDATE decision_actions
            SET status = ?, completed_at = {completed_expr}, updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (status, action_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
