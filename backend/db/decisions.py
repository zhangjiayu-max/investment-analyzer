"""理财决策档案数据层。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

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
VALID_CANDIDATE_STATUSES = {"new", "saved", "ignored", "deferred", "expired"}
VALID_CANDIDATE_ACTIONS = {"add", "buy", "reduce", "sell", "hold", "watch", "rebalance", "dca", "cash_reserve"}


def _add_candidate_column_if_missing(conn, column: str, definition: str):
    try:
        conn.execute(f"ALTER TABLE recommendation_candidates ADD COLUMN {column} {definition}")
    except Exception:
        pass


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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_peer_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL REFERENCES decision_records(id) ON DELETE CASCADE,
            reviewer_type TEXT NOT NULL,
            model_name TEXT DEFAULT '',
            prompt_version TEXT DEFAULT '',
            verdict TEXT NOT NULL,
            score_json TEXT DEFAULT '{}',
            concerns_json TEXT DEFAULT '[]',
            suggestions_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_peer_reviews_decision ON decision_peer_reviews(decision_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            source_type TEXT NOT NULL,
            source_id INTEGER,
            scenario_type TEXT DEFAULT '',
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_code TEXT DEFAULT '',
            target_name TEXT DEFAULT '',
            summary TEXT NOT NULL,
            rationale TEXT DEFAULT '',
            suggested_amount REAL,
            suggested_ratio REAL,
            confidence TEXT DEFAULT 'medium',
            evidence_json TEXT DEFAULT '{}',
            risk_json TEXT DEFAULT '{}',
            source_snapshot_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'new',
            decision_id INTEGER,
            review_at TEXT,
            deferred_until TEXT,
            expires_at TEXT,
            dedupe_key TEXT DEFAULT '',
            priority INTEGER DEFAULT 3,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_candidate_column_if_missing(conn, "suggested_ratio", "REAL")
    _add_candidate_column_if_missing(conn, "source_snapshot_json", "TEXT DEFAULT '{}'")
    _add_candidate_column_if_missing(conn, "review_at", "TEXT")
    _add_candidate_column_if_missing(conn, "deferred_until", "TEXT")
    _add_candidate_column_if_missing(conn, "expires_at", "TEXT")
    _add_candidate_column_if_missing(conn, "dedupe_key", "TEXT DEFAULT ''")
    _add_candidate_column_if_missing(conn, "priority", "INTEGER DEFAULT 3")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_user ON recommendation_candidates(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_status ON recommendation_candidates(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_source ON recommendation_candidates(source_type, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_target ON recommendation_candidates(target_type, target_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_dedupe ON recommendation_candidates(user_id, dedupe_key)")


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


def _row_to_candidate(row) -> dict:
    item = dict(row)
    item["evidence_json"] = _json_loads(item.get("evidence_json"), {})
    item["risk_json"] = _json_loads(item.get("risk_json"), {})
    item["source_snapshot_json"] = _json_loads(item.get("source_snapshot_json"), {})
    return item


def _format_ratio(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.0%}"
    return ""


def _format_percent(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}%" if value % 1 else f"{value:.0f}%"
    return ""


def _compact_text(text: str, limit: int = 140) -> str:
    """压缩展示文本，保留决策卡片可读性。"""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _split_cn_sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？!?；;\n]+", text or "")
    return [p.strip(" ，,") for p in parts if p.strip(" ，,")]


def _infer_decision_type(text: str) -> str:
    content = text or ""
    if any(word in content for word in ["卖出", "减仓", "止盈", "止损", "退出"]):
        return "sell"
    if any(word in content for word in ["加仓", "买入", "配置", "定投", "建仓"]):
        return "add"
    if any(word in content for word in ["持有", "不动", "继续拿"]):
        return "hold"
    if any(word in content for word in ["观察", "等待", "跟踪", "暂不"]):
        return "watch"
    return "watch"


def _extract_data_points(text: str) -> list[dict]:
    data_points = []
    patterns = [
        ("估值/分位", r"(?:PE|PB|估值|百分位|分位)[^。；;\n]*?\d+(?:\.\d+)?%"),
        ("仓位/比例", r"(?:仓位|占比|比例|回撤|亏损)[^。；;\n]*?\d+(?:\.\d+)?%"),
    ]
    seen = set()
    for name, pattern in patterns:
        for match in re.findall(pattern, text or "", flags=re.IGNORECASE):
            value = match.strip(" ，,。；;")
            if value in seen:
                continue
            seen.add(value)
            data_points.append({
                "name": name,
                "value": value,
                "source": "chat.assistant_message",
                "freshness": "message_snapshot",
            })
            if len(data_points) >= 3:
                return data_points
    return data_points


def _extract_risk_sentences(text: str) -> list[str]:
    primary_words = ["风险", "下跌", "回撤", "亏损", "短期", "波动", "失败", "失效"]
    secondary_words = ["不能", "不要", "不宜"]
    primary = []
    secondary = []
    for sentence in _split_cn_sentences(text):
        if any(word in sentence for word in primary_words):
            primary.append(sentence)
        elif any(word in sentence for word in secondary_words):
            secondary.append(sentence)
        if len(primary) + len(secondary) >= 3:
            break
    results = (primary + secondary)[:3]
    if not results:
        results.append("执行前需补充反方观点，避免只根据单一结论行动")
    return results


def _build_chat_decision_summary(decision_type: str, target_name: str, assistant_content: str) -> str:
    action_labels = {
        "add": "制定分批加仓草案",
        "sell": "制定减仓/卖出草案",
        "hold": "继续持有并跟踪",
        "watch": "进入观察清单",
    }
    target = target_name or "本次建议"
    first_sentence = _split_cn_sentences(assistant_content)
    suffix = _compact_text(first_sentence[0], 48) if first_sentence else ""
    title = f"{target}：{action_labels.get(decision_type, '进入观察清单')}"
    return f"{title}（来自对话建议）" if not suffix else f"{title} - {suffix}"


def create_recommendation_candidate(
    source_type: str,
    action_type: str,
    target_type: str,
    summary: str,
    user_id: str = "default",
    source_id: int | None = None,
    scenario_type: str = "",
    target_code: str = "",
    target_name: str = "",
    rationale: str = "",
    suggested_amount: float | None = None,
    suggested_ratio: float | None = None,
    confidence: str = "medium",
    evidence: dict | None = None,
    risk: dict | None = None,
    source_snapshot: dict | None = None,
    review_at: str | None = None,
    deferred_until: str | None = None,
    expires_at: str | None = None,
    dedupe_key: str = "",
    priority: int = 3,
    status: str = "new",
) -> int:
    """创建一条待用户确认的 AI 建议候选。"""
    if status not in VALID_CANDIDATE_STATUSES:
        status = "new"
    action_type = action_type if action_type in VALID_CANDIDATE_ACTIONS else "watch"
    conn = _get_conn()
    try:
        if dedupe_key:
            existing = conn.execute(
                """
                SELECT id FROM recommendation_candidates
                WHERE user_id = ? AND dedupe_key = ? AND status IN ('new', 'saved', 'deferred')
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, dedupe_key),
            ).fetchone()
            if existing:
                return existing["id"]
        cur = conn.execute(
            """
            INSERT INTO recommendation_candidates
                (user_id, source_type, source_id, scenario_type, action_type,
                 target_type, target_code, target_name, summary, rationale,
                 suggested_amount, suggested_ratio, confidence, evidence_json, risk_json,
                 source_snapshot_json, status, review_at, deferred_until, expires_at,
                 dedupe_key, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                source_type,
                source_id,
                scenario_type or "",
                action_type,
                target_type or "portfolio",
                target_code or "",
                target_name or "",
                summary,
                rationale or "",
                suggested_amount,
                suggested_ratio,
                confidence or "medium",
                _json_dumps(evidence),
                _json_dumps(risk),
                _json_dumps(source_snapshot),
                status,
                review_at,
                deferred_until,
                expires_at,
                dedupe_key or "",
                priority,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recommendation_candidate(candidate_id: int) -> dict | None:
    """获取单条建议候选。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM recommendation_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        return _row_to_candidate(row) if row else None
    finally:
        conn.close()


def list_recommendation_candidates(
    user_id: str = "default",
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """列出建议候选，默认按创建时间倒序。"""
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM recommendation_candidates
                WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM recommendation_candidates
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]
    finally:
        conn.close()


def update_recommendation_candidate_status(
    candidate_id: int,
    status: str,
    decision_id: int | None = None,
) -> bool:
    """更新建议候选状态。"""
    if status not in VALID_CANDIDATE_STATUSES:
        return False
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE recommendation_candidates
            SET status = ?, decision_id = COALESCE(?, decision_id), updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (status, decision_id, candidate_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def defer_recommendation_candidate(candidate_id: int, deferred_until: str) -> bool:
    """延期处理建议候选。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE recommendation_candidates
            SET status = 'deferred', deferred_until = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (deferred_until, candidate_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def expire_recommendation_candidates(user_id: str = "default") -> int:
    """把超过 expires_at 的建议候选标记为过期。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE recommendation_candidates
            SET status = 'expired', updated_at = datetime('now','localtime')
            WHERE user_id = ?
              AND status IN ('new', 'deferred')
              AND expires_at IS NOT NULL
              AND date(expires_at) < date('now','localtime')
            """,
            (user_id,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def expire_stale_decisions(days: int = 14) -> int:
    """把超过 N 天仍为 proposed 的决策标记为 expired，防止无限累积。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE decision_records
            SET status = 'expired', updated_at = datetime('now','localtime')
            WHERE status = 'proposed'
              AND date(created_at) < date('now', ?)
            """,
            (f"-{days} days",),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def auto_expire_cleanup() -> dict:
    """启动时自动清理过期的决策和候选，返回清理计数。"""
    candidates_expired = expire_recommendation_candidates()
    decisions_expired = expire_stale_decisions()
    if candidates_expired or decisions_expired:
        logging.info(f"自动过期清理: {decisions_expired} 条决策, {candidates_expired} 条候选")
    return {"decisions": decisions_expired, "candidates": candidates_expired}


def create_candidate_from_structured_recommendation(payload: dict, user_id: str = "default") -> int:
    """从工具或结构化 AI 输出创建建议候选。"""
    payload = payload or {}
    return create_recommendation_candidate(
        user_id=user_id,
        source_type=payload.get("source_type") or "tool",
        source_id=payload.get("source_id"),
        scenario_type=payload.get("scenario_type") or "",
        action_type=payload.get("action_type") or "watch",
        target_type=payload.get("target_type") or "portfolio",
        target_code=payload.get("target_code") or "",
        target_name=payload.get("target_name") or "",
        summary=payload.get("summary") or "结构化理财建议",
        rationale=payload.get("rationale") or payload.get("reason") or "",
        suggested_amount=payload.get("suggested_amount"),
        suggested_ratio=payload.get("suggested_ratio"),
        confidence=payload.get("confidence") or "medium",
        evidence=payload.get("evidence") or {},
        risk=payload.get("risk") or payload.get("risks") or {},
        source_snapshot=payload.get("source_snapshot") or {},
        review_at=payload.get("review_at"),
        deferred_until=payload.get("deferred_until"),
        expires_at=payload.get("expires_at"),
        dedupe_key=payload.get("dedupe_key") or "",
        priority=payload.get("priority", 3),
        status=payload.get("status") or "new",
    )


def _extract_amount_hint(text: str) -> float | None:
    patterns = [
        r"(?:不超过|约|投入|买入|加仓|单次)[^\d]{0,8}(\d+(?:\.\d+)?)\s*(?:元|块|人民币)",
        r"(\d+(?:\.\d+)?)\s*(?:元|块|人民币)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


def _candidate_action_from_sentence(sentence: str) -> str:
    if any(word in sentence for word in ["减仓", "卖出", "止盈", "止损", "清仓", "降低仓位"]):
        return "reduce"
    if any(word in sentence for word in ["定投", "月投"]):
        return "dca"
    if any(word in sentence for word in ["备用金", "活钱", "现金储备"]):
        return "cash_reserve"
    if any(word in sentence for word in ["加仓", "买入", "建仓", "分批配置", "增加仓位"]):
        return "add"
    if any(word in sentence for word in ["再平衡", "调仓", "调整配置"]):
        return "rebalance"
    if any(word in sentence for word in ["持有", "不动", "继续拿"]):
        return "hold"
    if any(word in sentence for word in ["观察", "等待", "跟踪", "暂不"]):
        return "watch"
    return ""


def _candidate_summary(target_name: str, action_type: str, sentence: str) -> str:
    label = {
        "add": "可分批加仓",
        "buy": "可买入",
        "reduce": "可减仓",
        "sell": "可卖出",
        "rebalance": "需再平衡",
        "dca": "可优化定投",
        "cash_reserve": "需调整备用金",
        "hold": "继续持有",
        "watch": "进入观察",
    }.get(action_type, "进入观察")
    snippet = _compact_text(sentence, 72)
    return f"{target_name or '组合'}{label}：{snippet}"


def _candidate_exists(
    conn,
    user_id: str,
    source_type: str,
    source_id: int | None,
    action_type: str,
    target_type: str,
    target_code: str,
) -> bool:
    row = conn.execute(
        """
        SELECT id FROM recommendation_candidates
        WHERE user_id = ?
          AND source_type = ?
          AND COALESCE(source_id, 0) = COALESCE(?, 0)
          AND action_type = ?
          AND target_type = ?
          AND target_code = ?
          AND status IN ('new', 'saved', 'deferred')
        LIMIT 1
        """,
        (user_id, source_type, source_id, action_type, target_type, target_code or ""),
    ).fetchone()
    return row is not None


def extract_recommendation_candidates_from_analysis(
    record_id: int,
    analysis_type: str,
    result_text: str,
    user_id: str = "default",
) -> int:
    """从持仓 AI 分析文本中抽取可处理建议候选。

    这是确定性抽取器：只匹配当前持仓名称/代码与明确动作词，不让 AI 直接创建交易。
    """
    text = result_text or ""
    if not text.strip():
        return 0
    try:
        from db.portfolio import list_holdings
        holdings = list_holdings(user_id=user_id)
    except Exception:
        holdings = []
    holdings = [h for h in holdings if h.get("fund_code") or h.get("fund_name")]
    if not holdings:
        return 0

    created = 0
    seen: set[tuple[str, str]] = set()
    conn = _get_conn()
    try:
        for sentence in _split_cn_sentences(text):
            action_type = _candidate_action_from_sentence(sentence)
            if not action_type:
                continue
            for holding in holdings:
                fund_code = holding.get("fund_code") or ""
                fund_name = holding.get("fund_name") or fund_code
                if not fund_code and not fund_name:
                    continue
                if fund_code not in sentence and fund_name not in sentence:
                    continue
                dedupe_key = (fund_code or fund_name, action_type)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                if _candidate_exists(conn, user_id, "analysis", record_id, action_type, "fund", fund_code):
                    continue
                summary = _candidate_summary(fund_name, action_type, sentence)
                risk_sentences = _extract_risk_sentences(text)
                cur = conn.execute(
                    """
                    INSERT INTO recommendation_candidates
                        (user_id, source_type, source_id, scenario_type, action_type,
                         target_type, target_code, target_name, summary, rationale,
                         suggested_amount, confidence, evidence_json, risk_json, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
                    """,
                    (
                        user_id,
                        "analysis",
                        record_id,
                        analysis_type or "",
                        action_type,
                        "fund",
                        fund_code,
                        fund_name,
                        summary,
                        _compact_text(sentence, 500),
                        _extract_amount_hint(sentence),
                        "medium",
                        _json_dumps({
                            "source": {
                                "type": "portfolio_analysis_record",
                                "record_id": record_id,
                                "analysis_type": analysis_type or "",
                            },
                            "snippet": sentence,
                            "data_points": _extract_data_points(sentence),
                        }),
                        _json_dumps({
                            "level": "medium",
                            "notes": risk_sentences,
                        }),
                    ),
                )
                if cur.lastrowid:
                    created += 1
                break
        conn.commit()
        return created
    finally:
        conn.close()


def create_decision_from_candidate(
    candidate_id: int,
    user_id: str = "default",
    review_days: int = 30,
) -> dict:
    """把建议候选保存为正式决策草案。"""
    candidate = get_recommendation_candidate(candidate_id)
    if not candidate:
        return {"ok": False, "error": "建议候选不存在"}
    if candidate.get("user_id") != user_id:
        return {"ok": False, "error": "建议候选不存在"}
    if candidate.get("status") == "saved" and candidate.get("decision_id"):
        decision = get_decision(candidate["decision_id"])
        return {"ok": True, "decision_id": candidate["decision_id"], "decision": decision, "candidate": candidate}
    if candidate.get("status") not in {"new", "ignored", "deferred"}:
        return {"ok": False, "error": f"建议候选状态 {candidate.get('status')} 不可保存为决策"}

    review_at = candidate.get("review_at") or (datetime.now() + timedelta(days=max(1, review_days))).strftime("%Y-%m-%d")
    action_type = candidate.get("action_type") or "watch"
    decision_type = "sell" if action_type == "sell" else "add" if action_type == "buy" else action_type
    decision_id = create_decision(
        user_id=user_id,
        source_type="recommendation_candidate",
        source_id=candidate_id,
        decision_type=decision_type,
        target_type=candidate.get("target_type") or "portfolio",
        target_code=candidate.get("target_code") or "",
        target_name=candidate.get("target_name") or "",
        summary=candidate.get("summary") or "AI 建议候选",
        rationale=candidate.get("rationale") or "",
        evidence={
            "source": {
                "type": candidate.get("source_type"),
                "source_id": candidate.get("source_id"),
                "scenario_type": candidate.get("scenario_type"),
                "candidate_id": candidate_id,
            },
            **(candidate.get("evidence_json") or {}),
            "source_snapshot": candidate.get("source_snapshot_json") or {},
        },
        risk=candidate.get("risk_json") or {},
        suitability={
            "checklist": [
                "确认建议来源数据仍然有效",
                "确认资金用途、现金流和仓位上限",
                "补充至少一个反方观点",
            ],
            "suggested_amount": candidate.get("suggested_amount"),
            "suggested_ratio": candidate.get("suggested_ratio"),
            "candidate_priority": candidate.get("priority"),
        },
        confidence=candidate.get("confidence") or "medium",
        status="proposed",
        review_at=review_at,
        actions=[
            {
                "action_type": "pre_trade_check" if decision_type in {"add", "buy", "reduce", "sell"} else "review",
                "title": f"复核 {candidate.get('target_name') or candidate.get('target_code') or '建议'} 的执行条件",
                "params": {
                    "candidate_id": candidate_id,
                    "transaction_type": "buy" if decision_type in {"add", "buy", "dca"} else "sell" if decision_type in {"reduce", "sell"} else "",
                    "fund_code": candidate.get("target_code") or "",
                    "fund_name": candidate.get("target_name") or "",
                    "amount": candidate.get("suggested_amount"),
                    "ratio": candidate.get("suggested_ratio"),
                },
            },
            {
                "action_type": "schedule_review",
                "title": f"{review_at} 复盘这条建议是否有效",
                "scheduled_at": review_at,
            },
        ],
    )
    update_recommendation_candidate_status(candidate_id, "saved", decision_id=decision_id)
    return {
        "ok": True,
        "decision_id": decision_id,
        "decision": get_decision(decision_id),
        "candidate": get_recommendation_candidate(candidate_id),
    }


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

        # 记录数据血缘
        try:
            from services.data_lineage import track_sources
            sources = []
            if evidence:
                for e in (evidence if isinstance(evidence, list) else [evidence]):
                    if isinstance(e, dict) and e.get("source"):
                        sources.append({"type": e.get("type", "rag"), "source": e["source"]})
            if sources:
                track_sources(f"decision:{decision_id}", sources)
        except Exception:
            pass  # 血缘记录不应阻断决策创建

        return decision_id
    finally:
        conn.close()


def create_chat_decision_draft(
    conversation_id: int,
    assistant_message_id: int,
    assistant_content: str,
    user_message_id: int | None = None,
    user_query: str = "",
    target_type: str = "portfolio",
    target_code: str = "",
    target_name: str = "",
    user_id: str = "default",
    review_days: int = 30,
) -> int:
    """把一条 AI 对话建议保存为理财决策草案。

    该函数不做交易执行，只把已经生成的建议结构化沉淀为可检查、可复盘的草案。
    """
    content = assistant_content or ""
    decision_type = _infer_decision_type(content + "\n" + (user_query or ""))
    summary = _build_chat_decision_summary(decision_type, target_name, content)
    review_at = (datetime.now() + timedelta(days=max(1, review_days))).strftime("%Y-%m-%d")
    data_points = _extract_data_points(content)
    if not data_points:
        data_points = [{
            "name": "AI建议摘要",
            "value": _compact_text(content, 120),
            "source": "chat.assistant_message",
            "freshness": "message_snapshot",
        }]

    risk_sentences = _extract_risk_sentences(content)
    checklist = [
        "确认备用金、资金用途和不可动用期限",
        "确认目标仓位、单次投入上限和是否需要分批执行",
        "确认估值/价格/持仓数据仍然有效",
        "补充至少一个反方观点后再决定是否执行",
    ]
    target_label = target_name or target_code or "本次建议"
    action_title = {
        "add": f"执行前检查 {target_label} 的资金和仓位约束",
        "sell": f"执行前检查 {target_label} 的卖出理由和替代方案",
        "hold": f"跟踪 {target_label} 的继续持有条件",
        "watch": f"跟踪 {target_label} 的观察触发条件",
    }.get(decision_type, f"复核 {target_label} 的执行条件")

    return create_decision(
        user_id=user_id,
        source_type="chat",
        source_id=assistant_message_id,
        decision_type=decision_type,
        target_type=target_type or "portfolio",
        target_code=target_code or "",
        target_name=target_name or "",
        summary=summary,
        rationale=_compact_text(content, 500),
        evidence={
            "source": {
                "type": "conversation",
                "conversation_id": conversation_id,
                "assistant_message_id": assistant_message_id,
                "user_message_id": user_message_id,
            },
            "user_query": _compact_text(user_query, 240),
            "data_points": data_points,
            "missing_data": ["资金用途", "目标仓位", "执行期限", "数据更新时间"],
            "counter_arguments": risk_sentences,
        },
        risk={
            "level": "medium",
            "counter_arguments": risk_sentences,
            "notes": ["对话建议只作为决策草案，执行前必须完成检查清单"],
        },
        suitability={
            "checklist": checklist,
            "notes": ["需结合个人画像、现金流和持仓约束再确认"],
        },
        confidence="medium",
        status="proposed",
        review_at=review_at,
        actions=[
            {
                "action_type": "pre_trade_check",
                "title": action_title,
                "params": {
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message_id,
                    "checklist": checklist,
                },
            },
            {
                "action_type": "schedule_review",
                "title": f"{review_at} 复盘这次决策假设是否成立",
                "scheduled_at": review_at,
            },
        ],
    )


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


def build_decision_precheck(decision_id: int, user_id: str = "default") -> dict:
    """生成决策执行前检查结果。"""
    decision = get_decision(decision_id)
    if not decision:
        return {"exists": False, "ok_to_execute": False, "blockers": ["决策不存在"], "warnings": [], "checklist": []}

    try:
        from db.dashboard import get_user_profile
        profile = get_user_profile(user_id) or {}
    except Exception:
        profile = {}

    blockers = []
    warnings = []
    checklist = []
    checklist.extend(decision.get("suitability_json", {}).get("checklist") or [])
    checklist.extend(decision.get("evidence_json", {}).get("missing_data") or [])

    evidence_json = decision.get("evidence_json") or {}
    risk_json = decision.get("risk_json") or {}
    counter_arguments = []
    counter_arguments.extend(evidence_json.get("counter_arguments") or [])
    counter_arguments.extend(risk_json.get("counter_arguments") or [])
    counter_arguments.extend(risk_json.get("notes") or [])
    if not [item for item in counter_arguments if item]:
        warnings.append("缺少反方观点或风险说明，执行前需补充至少一个不行动理由")

    stale_points = []
    for point in evidence_json.get("data_points") or []:
        as_of = point.get("as_of") or point.get("as_of_date") or point.get("latest_date")
        if not as_of:
            continue
        try:
            parsed = datetime.strptime(str(as_of)[:10], "%Y-%m-%d")
            if (datetime.now() - parsed).days > 10:
                stale_points.append(point.get("name") or as_of)
        except Exception:
            continue
    if stale_points:
        warnings.append(f"证据数据较旧/可能过期：{', '.join(stale_points[:3])}，执行前需刷新估值或净值")

    target_code = decision.get("target_code") or ""
    target_type = decision.get("target_type") or ""
    target_name = decision.get("target_name") or ""
    if target_code:
        conn = _get_conn()
        try:
            row = conn.execute(
                """
                SELECT id FROM decision_records
                WHERE user_id = ?
                  AND id != ?
                  AND target_type = ?
                  AND target_code = ?
                  AND status IN ('proposed', 'accepted', 'executed', 'deferred')
                  AND id NOT IN (SELECT decision_id FROM decision_reviews)
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, decision_id, target_type, target_code),
            ).fetchone()
            if row:
                warnings.append(f"同标的存在未复盘/未完成决策 #{row['id']}，建议先处理后再新增行动")
        finally:
            conn.close()

    # P0-3.2：检索同标的历史教训（来自决策复盘沉淀到 knowledge_base）
    # 教训以 source_decision_id 关联历史决策，按 target_code+target_type 匹配
    if target_code:
        try:
            conn = _get_conn()
            lesson_rows = conn.execute(
                """
                SELECT kb.title, kb.content, kb.importance, kb.source_decision_id
                FROM knowledge_base kb
                WHERE kb.category = 'user_lesson'
                  AND kb.source_decision_id IN (
                      SELECT id FROM decision_records
                      WHERE target_code = ? AND target_type = ? AND status = 'reviewed'
                  )
                ORDER BY kb.importance DESC
                LIMIT 3
                """,
                (target_code, target_type),
            ).fetchall()
            conn.close()
            if lesson_rows:
                lesson_titles = [r["title"] for r in lesson_rows]
                warnings.append(
                    f"该标的已有 {len(lesson_rows)} 条历史复盘教训："
                    + "；".join(lesson_titles)
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"precheck 检索历史教训失败: {e}")

    # P0-3.3：历史结论桥接激活 — 检测 24h 内同标的的冲突结论
    if target_code:
        try:
            from db.analysis_conclusions import get_conflicting_conclusions
            conflicts = get_conflicting_conclusions(target_code, hours=24)
            if conflicts:
                warnings.append(
                    f"24h 内有 {len(conflicts)} 条相反分析结论 "
                    f"(如 {conflicts[0].get('conclusion_a_action','')} vs "
                    f"{conflicts[0].get('conclusion_b_action','')})，建议先核对分析差异"
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"precheck 检测历史结论冲突失败: {e}")

    # P0-4.2：多模型评审反哺 precheck — 高拒率阻断执行
    try:
        conn = _get_conn()
        peer_rows = conn.execute(
            "SELECT verdict FROM decision_peer_reviews WHERE decision_id = ?",
            (decision_id,),
        ).fetchall()
        conn.close()
        if peer_rows:
            reject_count = sum(1 for r in peer_rows if r["verdict"] in ("reject", "defer"))
            total = len(peer_rows)
            if reject_count >= 2:
                blockers.append(
                    f"多模型评审 {reject_count}/{total} 个建议拒绝/延后，需先解决评审关切"
                )
            elif reject_count == 1 and total >= 3:
                warnings.append(
                    f"多模型评审 {reject_count}/{total} 个建议拒绝，执行前请复核评审关切"
                )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"precheck 查询多模型评审失败: {e}")

    trade_plan = _extract_trade_plan_from_decision(decision)

    decision_type = decision.get("decision_type")
    source_bucket = None
    source_bucket_id = decision.get("suitability_json", {}).get("source_bucket_id")
    if source_bucket_id:
        try:
            from db.goal_buckets import get_goal_bucket
            source_bucket = get_goal_bucket(int(source_bucket_id), user_id=user_id)
        except Exception:
            source_bucket = None

    if decision_type in {"add", "buy"}:
        if source_bucket:
            bucket_type = source_bucket.get("bucket_type")
            if bucket_type == "emergency":
                blockers.append(
                    f"资金来源为「{source_bucket.get('name')}」备用金桶，禁止用于买入/加仓高波动资产"
                )
            elif source_bucket.get("liquidity_days") is not None and source_bucket.get("liquidity_days") < 365:
                warnings.append(
                    f"资金来源「{source_bucket.get('name')}」流动性期限较短，需确认是否适合承担波动"
                )

        emergency_months = profile.get("emergency_fund_months")
        if isinstance(emergency_months, (int, float)) and emergency_months < 3:
            blockers.append(f"备用金不足：当前约 {emergency_months:g} 个月，建议至少确认 3-6 个月")
        elif emergency_months is None:
            warnings.append("未填写备用金月数，无法判断这笔资金是否可用于风险资产")

        monthly_surplus = profile.get("monthly_surplus")
        if isinstance(monthly_surplus, (int, float)) and monthly_surplus <= 0:
            blockers.append("月结余不为正，暂不适合新增风险资产仓位")
        elif monthly_surplus is None:
            warnings.append("未填写月结余，建议先确认定投或加仓资金来源")

        amount = trade_plan.get("amount") or 0
        if amount > 0:
            try:
                from db.portfolio import get_cash_balance

                cash_info = get_cash_balance(user_id)
                cash_balance = cash_info.get("balance", 0) if cash_info else 0
                if cash_balance < amount:
                    blockers.append(f"现金余额不足：计划买入 ¥{amount:,.0f}，当前现金约 ¥{cash_balance:,.0f}")
            except Exception:
                warnings.append("无法读取现金余额，执行前需手动确认资金来源")

        fund_code = trade_plan.get("fund_code") or decision.get("target_code") or ""
        if fund_code:
            try:
                from db.portfolio import list_transactions, list_holdings, get_cash_balance

                pending = list_transactions(fund_code=fund_code, user_id=user_id, status="pending")
                if pending:
                    warnings.append(f"{fund_code} 已有待确认交易 {len(pending)} 笔，避免重复下单")

                holdings = [h for h in list_holdings(user_id=user_id) if (h.get("shares") or 0) > 0]
                cash_info = get_cash_balance(user_id)
                cash_balance = cash_info.get("balance", 0) if cash_info else 0
                total_value = sum(h.get("current_value", 0) or 0 for h in holdings)
                total_assets = total_value + cash_balance
                target_holding = next((h for h in holdings if h.get("fund_code") == fund_code), None)
                if total_assets > 0 and target_holding:
                    current_value = target_holding.get("current_value", 0) or 0
                    projected_value = current_value + max(amount, 0)
                    projected_assets = total_assets + max(amount - cash_balance, 0)
                    projected_ratio = projected_value / projected_assets if projected_assets > 0 else 0
                    max_single = profile.get("max_single_position_pct")
                    if not isinstance(max_single, (int, float)):
                        max_single = 0.30
                    if max_single > 1:
                        max_single = max_single / 100
                    if projected_ratio > max_single:
                        warnings.append(
                            f"单基金占比将达到 {projected_ratio:.0%}，超过上限 {max_single:.0%}，建议降低单次金额或分批执行"
                        )
            except Exception:
                warnings.append("无法完成持仓/待确认交易检查，执行前需手动确认")

    if decision.get("target_type") == "portfolio":
        target_equity_ratio = profile.get("target_equity_ratio")
        if target_equity_ratio is None:
            warnings.append("未设置目标权益仓位，执行前建议先确认组合目标比例")

    if not checklist:
        checklist = ["确认资金用途", "确认仓位上限", "确认数据新鲜度", "确认反方观点"]

    # P1 闭环：交易行为分析 → 决策预检查
    # 当用户创建买入/卖出决策时，查询历史行为偏差，提示负面操作模式
    if action_type in ("add", "reduce") and decision_id:
        try:
            conn = _get_conn()
            pattern_rows = conn.execute("""
                SELECT behavior_pattern, confidence, behavior_type
                FROM trade_patterns
                WHERE user_id = ? AND is_negative = 1 AND confidence >= 0.6
                ORDER BY analyzed_at DESC LIMIT 3
            """, (user_id,)).fetchall()
            conn.close()
            if pattern_rows:
                pattern_texts = []
                for r in pattern_rows:
                    pattern_texts.append(f"{r['behavior_pattern']}（置信度{r['confidence']:.0%}）")
                warnings.append("行为分析发现以下负面模式，请谨慎决策：" + "；".join(pattern_texts))
        except Exception:
            pass

    # 去重并保留顺序
    def unique(items):
        seen = set()
        result = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    blockers = unique(blockers)
    warnings = unique(warnings)
    checklist = unique(checklist)
    return {
        "exists": True,
        "decision_id": decision_id,
        "ok_to_execute": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "checklist": checklist,
        "profile_snapshot": {
            "emergency_fund_months": profile.get("emergency_fund_months"),
            "monthly_surplus": profile.get("monthly_surplus"),
            "target_equity_ratio": profile.get("target_equity_ratio"),
            "max_single_position_pct": profile.get("max_single_position_pct"),
            "primary_goal": profile.get("primary_goal", ""),
            "fund_usage": profile.get("fund_usage", ""),
        },
        "source_bucket": source_bucket,
        "trade_plan": trade_plan,
    }


def _extract_trade_plan_from_decision(decision: dict) -> dict:
    """从决策行动参数中提取交易草稿意图。"""
    plan = {
        "transaction_type": "",
        "fund_code": decision.get("target_code") or "",
        "fund_name": decision.get("target_name") or "",
        "amount": None,
        "shares": None,
    }
    for action in decision.get("actions") or []:
        params = action.get("params_json") or {}
        if not isinstance(params, dict):
            continue
        if params.get("fund_code"):
            plan["fund_code"] = params.get("fund_code") or ""
        if params.get("fund_name"):
            plan["fund_name"] = params.get("fund_name") or ""
        if params.get("transaction_type"):
            plan["transaction_type"] = params.get("transaction_type") or ""
        if params.get("amount") is not None:
            try:
                plan["amount"] = float(params.get("amount") or 0)
            except (TypeError, ValueError):
                pass
        if params.get("shares") is not None:
            try:
                plan["shares"] = float(params.get("shares") or 0)
            except (TypeError, ValueError):
                pass
    if not plan["transaction_type"]:
        decision_type = decision.get("decision_type")
        if decision_type in ("add", "buy"):
            plan["transaction_type"] = "buy"
        elif decision_type in ("reduce", "sell"):
            plan["transaction_type"] = "sell"
    return plan


def create_transaction_draft_from_decision(
    decision_id: int,
    user_id: str = "default",
    force: bool = False,
) -> dict:
    """从决策生成待确认交易草稿。

    只创建 `portfolio_transactions.status='pending'`，不确认交易、不更新真实持仓。
    """
    decision = get_decision(decision_id)
    if not decision:
        return {"ok": False, "error": "决策不存在"}

    decision_type = decision.get("decision_type")
    if decision_type not in ("add", "buy", "reduce", "sell"):
        return {"ok": False, "error": f"决策类型 {decision_type} 不支持生成交易草稿"}

    precheck = build_decision_precheck(decision_id, user_id=user_id)
    if precheck.get("blockers") and not force:
        return {"ok": False, "error": "；".join(precheck.get("blockers") or []), "precheck": precheck}

    plan = precheck.get("trade_plan") or _extract_trade_plan_from_decision(decision)
    tx_type = plan.get("transaction_type")
    fund_code = plan.get("fund_code") or decision.get("target_code") or ""
    fund_name = plan.get("fund_name") or decision.get("target_name") or fund_code
    amount = plan.get("amount") or 0
    shares = plan.get("shares")

    if tx_type not in ("buy", "sell"):
        return {"ok": False, "error": "交易草稿仅支持买入或卖出"}
    if not fund_code:
        return {"ok": False, "error": "缺少基金代码，无法生成交易草稿"}
    if tx_type == "buy" and amount <= 0:
        return {"ok": False, "error": "买入交易缺少有效金额"}
    if tx_type == "sell" and (shares is None or shares <= 0):
        return {"ok": False, "error": "卖出交易缺少有效份额"}

    from datetime import datetime
    from db.portfolio import create_transaction, get_holding_by_fund, list_transactions

    existing_pending = list_transactions(fund_code=fund_code, user_id=user_id, status="pending")
    if existing_pending and not force:
        return {"ok": False, "error": f"{fund_code} 已有待确认交易，请先处理后再生成草稿", "precheck": precheck}

    holding = get_holding_by_fund(fund_code, user_id=user_id)
    holding_id = holding.get("id") if holding else None
    today = datetime.now().strftime("%Y-%m-%d")

    tx_id = create_transaction(
        fund_code=fund_code,
        fund_name=fund_name,
        transaction_type=tx_type,
        amount=0,
        shares=None,
        price=None,
        transaction_date=today,
        holding_id=holding_id,
        notes=f"由决策 #{decision_id} 生成的交易草稿",
        user_id=user_id,
        status="pending",
        submitted_amount=amount if tx_type == "buy" else None,
        submitted_shares=shares if tx_type == "sell" else None,
    )

    action_id = create_decision_action(
        decision_id=decision_id,
        action_type="transaction_draft",
        title=f"已生成{fund_name}的{'买入' if tx_type == 'buy' else '卖出'}交易草稿",
        params={
            "transaction_id": tx_id,
            "transaction_type": tx_type,
            "fund_code": fund_code,
            "fund_name": fund_name,
            "submitted_amount": amount if tx_type == "buy" else None,
            "submitted_shares": shares if tx_type == "sell" else None,
        },
        status="todo",
    )
    update_decision_status(decision_id, "accepted", "已生成待确认交易草稿")

    tx = list_transactions(fund_code=fund_code, user_id=user_id, status="pending", limit=10)
    tx_item = next((item for item in tx if item.get("id") == tx_id), None)
    return {
        "ok": True,
        "transaction_id": tx_id,
        "action_id": action_id,
        "transaction": tx_item or {"id": tx_id, "status": "pending", "fund_code": fund_code, "transaction_type": tx_type},
        "decision": get_decision(decision_id),
        "precheck": precheck,
    }


def _save_review_lesson_knowledge(decision_id: int, outcome: str, result_note: str, lesson: str):
    """把决策复盘教训沉淀到知识库。"""
    if not lesson:
        return
    decision = get_decision(decision_id)
    if not decision:
        return
    try:
        from db.knowledge import add_knowledge

        target = decision.get("target_name") or decision.get("target_code") or decision.get("target_type") or "投资决策"
        title = f"复盘教训：{target} #{decision_id}"
        content = (
            f"决策：{decision.get('summary', '')}\n"
            f"结果：{outcome}\n"
            f"复盘：{result_note or '未填写'}\n"
            f"教训：{lesson}"
        )
        add_knowledge(
            category="user_lesson",
            subcategory=decision.get("decision_type") or "decision_review",
            title=title,
            content=content,
            source=f"decision_review:{decision_id}",
            keywords=[target, decision.get("decision_type") or "", "复盘", "教训"],
            importance=8 if outcome == "helpful" else 7,
            atom_type="user_lesson",
            evidence_level="user_memory",
            as_of_date=datetime.now().strftime("%Y-%m-%d"),
            limitations=["来自用户复盘，适用于相似资金用途和风险约束下的决策"],
            counterpoints=["若用户目标、现金流或市场环境明显变化，需要重新判断"],
            source_decision_id=decision_id,
        )
    except Exception:
        # 复盘主流程不能因知识沉淀失败而失败。
        return


def backfill_kb_decision_id() -> int:
    """回填 knowledge_base.source_decision_id（从 source 字符串解析）。

    历史 user_lesson 知识的 source 字段格式为 "decision_review:{id}"，
    本函数解析后填入 source_decision_id 字段，建立 FK 关联。

    Returns:
        回填条数
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, source FROM knowledge_base "
            "WHERE source_decision_id IS NULL AND source LIKE 'decision_review:%'"
        ).fetchall()
        updated = 0
        for r in rows:
            try:
                decision_id = int(r["source"].split(":", 1)[1])
                conn.execute(
                    "UPDATE knowledge_base SET source_decision_id = ? WHERE id = ?",
                    (decision_id, r["id"]),
                )
                updated += 1
            except (ValueError, IndexError):
                continue
        conn.commit()
        return updated
    finally:
        conn.close()


def expire_stale_decisions(days: int = 7) -> int:
    """accepted 状态超 N 天未确认交易的决策自动转 deferred。

    设计稿 P0-4.1：避免 accepted 状态永久堆积。

    Args:
        days: 过期天数，默认 7 天

    Returns:
        转换条数
    """
    conn = _get_conn()
    try:
        cur = conn.execute(
            f"""
            UPDATE decision_records
            SET status = 'deferred',
                notes = COALESCE(notes, '') || CHAR(10) || '[系统] {days}天未执行交易自动转延后',
                updated_at = datetime('now','localtime')
            WHERE status = 'accepted'
              AND updated_at < datetime('now','localtime', ?)
            """,
            (f"-{days} days",),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def _create_eval_case_from_bad_decision_review(decision_id: int, result_note: str, lesson: str):
    """把无帮助的决策复盘转成回归评测用例。"""
    decision = get_decision(decision_id)
    if not decision:
        return
    try:
        from db.eval import create_eval_case

        target = decision.get("target_name") or decision.get("target_code") or decision.get("target_type") or "投资决策"
        decision_type = decision.get("decision_type") or "decision"
        input_params = {
            "source": "decision_review",
            "decision_id": decision_id,
            "decision_type": decision_type,
            "target_type": decision.get("target_type", ""),
            "target_name": target,
            "original_summary": decision.get("summary", ""),
            "original_rationale": decision.get("rationale", ""),
            "review_note": result_note or "",
            "lesson": lesson or "",
        }
        expected_quality = (
            "回答必须先确认用户资金用途、备用金、期限和仓位约束；"
            "必须给出反方观点、执行前检查清单、复盘条件；"
            f"历史复盘教训：{lesson or result_note or '这类建议曾被评为无帮助'}"
        )
        create_eval_case(
            name=f"坏例复盘：{target} #{decision_id}",
            analysis_type=f"decision_{decision_type}",
            input_params=json.dumps(input_params, ensure_ascii=False),
            description="由无帮助的决策复盘自动生成，用于防止类似建议再次忽略用户约束。",
            expected_quality=expected_quality,
        )
    except Exception:
        return


def _append_profile_text(existing: str, addition: str, limit: int = 1200) -> str:
    existing = (existing or "").strip()
    addition = (addition or "").strip()
    if not addition:
        return existing
    if addition in existing:
        return existing
    combined = f"{existing}\n{addition}".strip() if existing else addition
    return combined[-limit:]


def _update_profile_from_decision_review(decision_id: int, outcome: str, result_note: str, lesson: str):
    """把复盘经验反哺用户画像。"""
    text = f"{result_note or ''}\n{lesson or ''}"
    if not text.strip():
        return
    try:
        from db.dashboard import get_user_profile, update_user_profile

        profile = get_user_profile("default") or {}
        updates = {}
        decision = get_decision(decision_id) or {}
        target = decision.get("target_name") or decision.get("target_code") or "投资决策"
        line = f"{target}：{_compact_text(text, 160)}"
        if outcome == "helpful":
            updates["positive_patterns"] = _append_profile_text(profile.get("positive_patterns") or "", line)
        elif outcome == "unhelpful":
            updates["negative_patterns"] = _append_profile_text(profile.get("negative_patterns") or "", line)

        biases_raw = profile.get("behavior_biases") or "[]"
        try:
            biases = json.loads(biases_raw) if isinstance(biases_raw, str) else biases_raw
        except Exception:
            biases = []
        if not isinstance(biases, list):
            biases = []
        bias_text = text
        bias_map = {
            "panic_sell": ["情绪化止损", "恐慌", "杀跌", "下跌时卖出"],
            "chasing_high": ["追涨", "高位买入", "踏空焦虑"],
            "overtrading": ["频繁交易", "操作过多"],
        }
        for bias, words in bias_map.items():
            if any(word in bias_text for word in words) and bias not in biases:
                biases.append(bias)
        updates["behavior_biases"] = biases
        if updates:
            update_user_profile("default", **updates)
    except Exception:
        return


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
        _save_review_lesson_knowledge(decision_id, outcome, result_note or "", lesson or "")
        _update_profile_from_decision_review(decision_id, outcome, result_note or "", lesson or "")
        if outcome == "unhelpful":
            _create_eval_case_from_bad_decision_review(decision_id, result_note or "", lesson or "")
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM decision_reviews WHERE decision_id = ?", (decision_id,)).fetchone()
        return row["id"] if row else 0
    finally:
        conn.close()


def get_decision_stats(user_id: str = "default") -> dict:
    """汇总个人决策质量与复盘反馈。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT status, decision_type, COUNT(*) as cnt
            FROM decision_records
            WHERE user_id = ?
            GROUP BY status, decision_type
            """,
            (user_id,),
        ).fetchall()
        by_status: dict[str, int] = {}
        by_decision_type: dict[str, int] = {}
        total = 0
        for row in rows:
            count = row["cnt"] or 0
            total += count
            status = row["status"] or "unknown"
            decision_type = row["decision_type"] or "unknown"
            by_status[status] = by_status.get(status, 0) + count
            by_decision_type[decision_type] = by_decision_type.get(decision_type, 0) + count

        review_row = conn.execute(
            """
            SELECT
                COUNT(*) as reviewed,
                SUM(CASE WHEN outcome = 'helpful' THEN 1 ELSE 0 END) as helpful_reviews,
                SUM(CASE WHEN outcome = 'unhelpful' THEN 1 ELSE 0 END) as unhelpful_reviews,
                SUM(CASE WHEN profit_change IS NOT NULL THEN profit_change ELSE 0 END) as total_profit_change,
                AVG(CASE WHEN profit_change IS NOT NULL THEN profit_change END) as avg_profit_change
            FROM decision_reviews r
            JOIN decision_records d ON d.id = r.decision_id
            WHERE d.user_id = ?
            """,
            (user_id,),
        ).fetchone()
        reviewed = review_row["reviewed"] or 0 if review_row else 0
        helpful_reviews = review_row["helpful_reviews"] or 0 if review_row else 0
        unhelpful_reviews = review_row["unhelpful_reviews"] or 0 if review_row else 0
        total_profit_change = review_row["total_profit_change"] or 0 if review_row else 0
        avg_profit_change = review_row["avg_profit_change"] or 0 if review_row else 0

        lesson_rows = conn.execute(
            """
            SELECT r.lesson
            FROM decision_reviews r
            JOIN decision_records d ON d.id = r.decision_id
            WHERE d.user_id = ? AND r.lesson != ''
            ORDER BY
              CASE r.outcome
                WHEN 'helpful' THEN 0
                WHEN 'neutral' THEN 1
                ELSE 2
              END,
              r.updated_at DESC,
              r.id DESC
            LIMIT 5
            """,
            (user_id,),
        ).fetchall()
        recent_lessons = [row["lesson"] for row in lesson_rows if row["lesson"]]

        return {
            "total": total,
            "by_status": by_status,
            "by_decision_type": by_decision_type,
            "reviewed": reviewed,
            "helpful_reviews": helpful_reviews,
            "unhelpful_reviews": unhelpful_reviews,
            "review_helpful_rate": round(helpful_reviews / reviewed * 100) if reviewed else 0,
            "total_profit_change": round(total_profit_change, 2),
            "avg_profit_change": round(avg_profit_change, 2),
            "recent_lessons": recent_lessons,
        }
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


# ── 多模型评审 CRUD ──

VALID_VERDICTS = {"approve", "approve_with_concerns", "reject", "defer"}


def create_peer_review(
    decision_id: int,
    reviewer_type: str,
    verdict: str,
    model_name: str = "",
    prompt_version: str = "",
    score_json: dict | None = None,
    concerns_json: list | None = None,
    suggestions_json: list | None = None,
) -> int | None:
    """创建决策评审记录，返回 id。"""
    if verdict not in VALID_VERDICTS:
        return None
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO decision_peer_reviews
                (decision_id, reviewer_type, model_name, prompt_version,
                 verdict, score_json, concerns_json, suggestions_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id, reviewer_type, model_name, prompt_version,
                verdict, _json_dumps(score_json), _json_dumps(concerns_json),
                _json_dumps(suggestions_json),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_peer_reviews(decision_id: int) -> list[dict]:
    """列出某决策的所有评审。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM decision_peer_reviews WHERE decision_id = ? ORDER BY created_at",
            (decision_id,),
        ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["score_json"] = _json_loads(item.get("score_json"), {})
            item["concerns_json"] = _json_loads(item.get("concerns_json"), [])
            item["suggestions_json"] = _json_loads(item.get("suggestions_json"), [])
            results.append(item)
        return results
    finally:
        conn.close()


def match_pending_decisions(user_id: str = "default") -> list[dict]:
    """检查待执行决策与当前持仓变化的匹配情况。

    扫描 status='accepted' 的决策，对比最近交易记录（7天内），
    如果决策目标(target_code)在持仓中有变化（新增/增仓/减仓），返回匹配结果。
    """
    from datetime import datetime, timedelta

    conn = _get_conn()
    try:
        # 1. 获取所有 accepted 的决策
        rows = conn.execute(
            """
            SELECT * FROM decision_records
            WHERE user_id = ? AND status = 'accepted'
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        if not rows:
            return []

        decisions = [dict(r) for r in rows]

        # 2. 获取最近 7 天内已确认的交易记录
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        tx_rows = conn.execute(
            """
            SELECT t.fund_code, t.fund_name, t.transaction_type, t.shares, t.amount,
                   t.transaction_date, t.price, h.shares AS current_shares, h.total_cost
            FROM portfolio_transactions t
            LEFT JOIN portfolio_holdings h ON t.holding_id = h.id
            WHERE t.user_id = ?
              AND t.status IN ('confirmed', 'settled')
              AND t.transaction_date >= ?
              AND (t.is_system IS NULL OR t.is_system = 0)
            ORDER BY t.transaction_date DESC
            """,
            (user_id, seven_days_ago),
        ).fetchall()
        recent_txs = [dict(r) for r in tx_rows]

        # 3. 按 fund_code 分组交易
        tx_by_code: dict[str, list[dict]] = {}
        for tx in recent_txs:
            code = tx.get("fund_code") or ""
            if code:
                tx_by_code.setdefault(code, []).append(tx)

        # 4. 匹配
        results = []
        for decision in decisions:
            target_code = (decision.get("target_code") or "").strip()
            target_type = decision.get("target_type") or ""
            decision_type = decision.get("decision_type") or ""

            if not target_code:
                continue

            matched_txs = []

            # 直接匹配 fund_code
            if target_code in tx_by_code:
                matched_txs = tx_by_code[target_code]

            # 如果 target_type 是 index，尝试通过持仓的 index_code 匹配
            if not matched_txs and target_type == "index":
                for code, txs in tx_by_code.items():
                    for tx in txs:
                        # 检查持仓的 index_code（通过 fund_name 或 index_name 粗匹配）
                        if target_code in (tx.get("fund_name") or ""):
                            matched_txs.extend(txs)
                            break

            if not matched_txs:
                continue

            # 汇总匹配的交易
            buy_shares = sum(t.get("shares") or 0 for t in matched_txs if t.get("transaction_type") == "buy")
            sell_shares = sum(t.get("shares") or 0 for t in matched_txs if t.get("transaction_type") == "sell")
            buy_amount = sum(t.get("amount") or 0 for t in matched_txs if t.get("transaction_type") == "buy")
            sell_amount = sum(t.get("amount") or 0 for t in matched_txs if t.get("transaction_type") == "sell")
            latest_tx_date = max(t.get("transaction_date") or "" for t in matched_txs) if matched_txs else ""

            # 判断执行方向是否匹配决策意图
            direction_match = False
            if decision_type in ("add", "buy") and buy_shares > 0:
                direction_match = True
            elif decision_type in ("reduce", "sell") and sell_shares > 0:
                direction_match = True
            elif decision_type == "rebalance" and (buy_shares > 0 or sell_shares > 0):
                direction_match = True
            elif decision_type in ("watch", "hold"):
                # 观察/持有类决策，任何交易都算匹配
                direction_match = bool(matched_txs)

            if not direction_match:
                continue

            results.append({
                "decision_id": decision["id"],
                "decision_type": decision_type,
                "target_code": target_code,
                "target_name": decision.get("target_name") or "",
                "target_type": target_type,
                "summary": decision.get("summary") or "",
                "matched": True,
                "direction_match": True,
                "buy_shares": round(buy_shares, 2),
                "sell_shares": round(sell_shares, 2),
                "buy_amount": round(buy_amount, 2),
                "sell_amount": round(sell_amount, 2),
                "tx_count": len(matched_txs),
                "latest_tx_date": latest_tx_date,
                "suggestion": "检测到持仓变化与此决策方向一致，建议确认执行。",
            })

        return results
    finally:
        conn.close()


def count_high_risk_reviews(decision_id: int) -> int:
    """统计某决策中高风险评审数量（reject 或 defer）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM decision_peer_reviews
            WHERE decision_id = ? AND verdict IN ('reject', 'defer')
            """,
            (decision_id,),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def generate_weekly_decision_review() -> dict:
    """生成每周决策回顾报告，返回统计数据。"""
    conn = _get_conn()
    try:
        from datetime import datetime, timedelta
        import json as _json
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

        stats = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM decision_records
            WHERE created_at >= ? GROUP BY status
        """, (week_ago,)).fetchall()
        stat_map = {r["status"]: r["cnt"] for r in stats}

        executed = conn.execute("""
            SELECT d.id, d.target_name, d.summary, d.status,
                   t.transaction_type, t.amount, t.confirmed_at
            FROM decision_records d
            LEFT JOIN portfolio_transactions t
              ON t.fund_code = d.target_code
              AND t.confirmed_at >= d.created_at
              AND t.status = 'confirmed'
            WHERE d.created_at >= ? AND d.status = 'executed'
            ORDER BY d.created_at DESC LIMIT 20
        """, (week_ago,)).fetchall()

        expired = conn.execute("""
            SELECT id, target_name, summary, created_at FROM decision_records
            WHERE status = 'proposed' AND created_at < ?
            ORDER BY created_at DESC LIMIT 10
        """, (two_weeks_ago,)).fetchall()

        total = sum(stat_map.values())
        executed_count = stat_map.get("executed", 0)

        report = {
            "week_total": total,
            "proposed": stat_map.get("proposed", 0),
            "executed": executed_count,
            "rejected": stat_map.get("rejected", 0),
            "deferred": stat_map.get("deferred", 0),
            "executed_details": [dict(r) for r in executed],
            "expired_proposals": [dict(r) for r in expired],
            "execution_rate": round(executed_count / total * 100, 1) if total > 0 else 0,
        }

        try:
            conn.execute("""
                INSERT INTO weekly_reports (report_type, report_date, content_json, created_at)
                VALUES ('decision_review', date('now','localtime'), ?, datetime('now','localtime'))
            """, (_json.dumps(report, ensure_ascii=False),))
            conn.commit()
        except Exception:
            pass

        return report
    finally:
        conn.close()


# ── 决策回测 ──────────────────────────────────────────

def update_decision_backtest(decision_id: int, days: int, result_pct: float,
                              benchmark_pct: float = None) -> bool:
    """
    更新决策回测结果。

    Args:
        decision_id: 决策记录 ID
        days: 回测周期（7=T+7, 30=T+30）
        result_pct: 决策后 N 天的实际收益率（%）
        benchmark_pct: 基准收益率（%），如沪深300同期涨幅

    Returns: True if updated successfully
    """
    conn = _get_conn()
    try:
        # 检查是否已有回测记录
        existing = conn.execute(
            """
            SELECT id FROM decision_reviews
            WHERE decision_id = ? AND outcome = 'backtest'
            """,
            (decision_id,),
        ).fetchone()

        excess_return = None
        if benchmark_pct is not None and result_pct is not None:
            excess_return = round(result_pct - benchmark_pct, 2)

        review_note = (
            f"T+{days}回测: 收益率 {result_pct:.2f}%"
            + (f"，基准 {benchmark_pct:.2f}%" if benchmark_pct is not None else "")
            + (f"，超额 {excess_return:.2f}%" if excess_return is not None else "")
        )

        if existing:
            # 更新已有回测记录
            conn.execute(
                """
                UPDATE decision_reviews
                SET result_note = ?, profit_change = ?, updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (review_note, result_pct, existing["id"]),
            )
        else:
            # 创建回测记录
            conn.execute(
                """
                INSERT INTO decision_reviews
                    (decision_id, outcome, result_note, profit_change, created_at, updated_at)
                VALUES (?, 'backtest', ?, ?, datetime('now','localtime'), datetime('now','localtime'))
                """,
                (decision_id, review_note, result_pct),
            )

        # 更新决策状态为 reviewed
        conn.execute(
            """
            UPDATE decision_records
            SET status = 'reviewed', updated_at = datetime('now','localtime')
            WHERE id = ? AND status IN ('accepted', 'executed', 'deferred')
            """,
            (decision_id,),
        )

        conn.commit()
        return True
    except Exception as e:
        logging.getLogger(__name__).error(f"更新决策回测失败: {e}")
        return False
    finally:
        conn.close()


def get_decisions_for_backtest(days: int = 7) -> list[dict]:
    """
    获取需要回测的决策列表。

    筛选条件：
    - status 为 accepted 或 executed
    - 创建时间距今正好 N 天（或超过 N 天但尚未回测）
    - 尚未有 backtest 类型的 review 记录

    Args:
        days: 回测周期（7=T+7, 30=T+30）

    Returns: 待回测决策列表
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT d.* FROM decision_records d
            WHERE d.status IN ('accepted', 'executed')
              AND date(d.created_at) <= date('now','localtime', ?)
              AND date(d.created_at) >= date('now','localtime', ?)
              AND d.id NOT IN (
                  SELECT decision_id FROM decision_reviews
                  WHERE outcome = 'backtest'
              )
            ORDER BY d.created_at ASC
            """,
            (f"-{days} days", f"-{days * 2} days"),
        ).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            # 解析 JSON 字段
            item["evidence_json"] = _json_loads(item.get("evidence_json"), {})
            item["risk_json"] = _json_loads(item.get("risk_json"), {})
            item["suitability_json"] = _json_loads(item.get("suitability_json"), {})
            results.append(item)

        return results
    finally:
        conn.close()


def auto_backtest_decisions() -> list[dict]:
    """
    自动回测 T+7 和 T+30 的决策。

    流程：
    1. 获取 T+7 和 T+30 待回测的决策
    2. 对每个决策，查找其 target_code 对应的指数/基金在决策创建后 N 天的价格变化
    3. 计算决策收益率和基准收益率
    4. 调用 update_decision_backtest 更新结果

    Returns: 回测结果列表
    """
    from datetime import datetime, timedelta

    results = []

    # 分别处理 T+7 和 T+30
    for days in [7, 30]:
        pending = get_decisions_for_backtest(days=days)
        if not pending:
            continue

        conn = _get_conn()
        try:
            for decision in pending:
                decision_id = decision["id"]
                target_code = decision.get("target_code") or ""
                created_at = decision.get("created_at") or ""
                decision_type = decision.get("decision_type") or ""

                if not target_code or not created_at:
                    continue

                # 解析创建日期
                try:
                    created_date = datetime.strptime(created_at[:10], "%Y-%m-%d")
                except Exception:
                    continue

                # 查询决策创建时的价格（基线）
                try:
                    baseline_row = conn.execute(
                        """
                        SELECT close FROM valuations
                        WHERE index_code = ?
                          AND date <= ?
                        ORDER BY date DESC LIMIT 1
                        """,
                        (target_code, created_at[:10]),
                    ).fetchone()

                    if not baseline_row or not baseline_row["close"]:
                        continue

                    baseline_price = baseline_row["close"]

                    # 查询 N 天后的价格
                    target_date = (created_date + timedelta(days=days)).strftime("%Y-%m-%d")
                    end_row = conn.execute(
                        """
                        SELECT close FROM valuations
                        WHERE index_code = ?
                          AND date <= ?
                        ORDER BY date DESC LIMIT 1
                        """,
                        (target_code, target_date),
                    ).fetchone()

                    if not end_row or not end_row["close"]:
                        continue

                    end_price = end_row["close"]

                    # 计算收益率
                    result_pct = (end_price - baseline_price) / baseline_price * 100

                    # 获取基准收益率（沪深300）
                    benchmark_pct = None
                    try:
                        bench_base = conn.execute(
                            """
                            SELECT close FROM valuations
                            WHERE index_code IN ('000300', 'hs300', '沪深300')
                              AND date <= ?
                            ORDER BY date DESC LIMIT 1
                            """,
                            (created_at[:10],),
                        ).fetchone()
                        bench_end = conn.execute(
                            """
                            SELECT close FROM valuations
                            WHERE index_code IN ('000300', 'hs300', '沪深300')
                              AND date <= ?
                            ORDER BY date DESC LIMIT 1
                            """,
                            (target_date,),
                        ).fetchone()

                        if bench_base and bench_end and bench_base["close"]:
                            benchmark_pct = (bench_end["close"] - bench_base["close"]) / bench_base["close"] * 100
                    except Exception:
                        pass

                    # 对于 sell 类型决策，收益需要取反
                    if decision_type == "sell":
                        result_pct = -result_pct

                    # 更新回测结果
                    success = update_decision_backtest(
                        decision_id, days, round(result_pct, 2),
                        round(benchmark_pct, 2) if benchmark_pct is not None else None,
                    )

                    if success:
                        results.append({
                            "decision_id": decision_id,
                            "target_code": target_code,
                            "days": days,
                            "result_pct": round(result_pct, 2),
                            "benchmark_pct": round(benchmark_pct, 2) if benchmark_pct is not None else None,
                            "decision_type": decision_type,
                        })

                except Exception as e:
                    logging.getLogger(__name__).warning(
                        f"决策 #{decision_id} 回测失败: {e}"
                    )
                    continue
        finally:
            conn.close()

    if results:
        logging.info(f"自动回测完成: {len(results)} 条决策已回测")

    return results
