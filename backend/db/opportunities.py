"""短线主题机会 CRUD 与决策联动。"""

import json
from datetime import datetime

from db._conn import _get_conn
from db._utils import _add_column_if_not_exists


def init_opportunity_tables(conn):
    """初始化主题机会相关表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS theme_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            trade_date TEXT NOT NULL,
            theme TEXT NOT NULL,
            verdict TEXT NOT NULL,
            opportunity_score REAL DEFAULT 0,
            summary TEXT DEFAULT '',
            policy_signal TEXT DEFAULT '',
            future_direction TEXT DEFAULT '',
            market_signal TEXT DEFAULT '',
            valuation_role TEXT DEFAULT '',
            portfolio_fit_json TEXT DEFAULT '{}',
            matched_funds_json TEXT DEFAULT '[]',
            entry_plan_json TEXT DEFAULT '{}',
            exit_plan_json TEXT DEFAULT '{}',
            risk_note TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, trade_date, theme)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_theme_opp_user_date ON theme_opportunities(user_id, trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_theme_opp_status ON theme_opportunities(status)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS theme_opportunity_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER REFERENCES theme_opportunities(id) ON DELETE CASCADE,
            fund_code TEXT NOT NULL,
            decision_id INTEGER,
            transaction_id INTEGER,
            entry_date TEXT,
            entry_price REAL,
            entry_amount REAL,
            entry_shares REAL,
            current_price REAL,
            current_return_pct REAL,
            max_return_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            exit_triggered INTEGER DEFAULT 0,
            exit_reason TEXT DEFAULT '',
            review_due_date TEXT,
            last_checked_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "theme_opportunity_tracks", "transaction_id", "INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_theme_track_opp ON theme_opportunity_tracks(opportunity_id)")


def _json_dumps(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _json_loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _row_to_opportunity(row) -> dict:
    item = dict(row)
    item["portfolio_fit"] = _json_loads(item.pop("portfolio_fit_json", ""), {})
    item["matched_funds"] = _json_loads(item.pop("matched_funds_json", ""), [])
    item["entry_plan"] = _json_loads(item.pop("entry_plan_json", ""), {})
    item["exit_plan"] = _json_loads(item.pop("exit_plan_json", ""), {})
    item["evidence"] = _json_loads(item.pop("evidence_json", ""), [])
    return item


def save_opportunity(item: dict, user_id: str = "default") -> int:
    """保存或更新每日主题机会，返回 id。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO theme_opportunities
            (user_id, trade_date, theme, verdict, opportunity_score, summary,
             policy_signal, future_direction, market_signal, valuation_role,
             portfolio_fit_json, matched_funds_json, entry_plan_json, exit_plan_json,
             risk_note, evidence_json, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(user_id, trade_date, theme) DO UPDATE SET
            verdict = excluded.verdict,
            opportunity_score = excluded.opportunity_score,
            summary = excluded.summary,
            policy_signal = excluded.policy_signal,
            future_direction = excluded.future_direction,
            market_signal = excluded.market_signal,
            valuation_role = excluded.valuation_role,
            portfolio_fit_json = excluded.portfolio_fit_json,
            matched_funds_json = excluded.matched_funds_json,
            entry_plan_json = excluded.entry_plan_json,
            exit_plan_json = excluded.exit_plan_json,
            risk_note = excluded.risk_note,
            evidence_json = excluded.evidence_json,
            status = excluded.status,
            updated_at = datetime('now','localtime')
    """, (
        user_id,
        item["trade_date"],
        item["theme"],
        item["verdict"],
        item.get("opportunity_score", 0),
        item.get("summary", ""),
        item.get("policy_signal", ""),
        item.get("future_direction", ""),
        item.get("market_signal", ""),
        item.get("valuation_role", ""),
        _json_dumps(item.get("portfolio_fit", {})),
        _json_dumps(item.get("matched_funds", [])),
        _json_dumps(item.get("entry_plan", {})),
        _json_dumps(item.get("exit_plan", {})),
        item.get("risk_note", ""),
        _json_dumps(item.get("evidence", [])),
        item.get("status", "active"),
    ))
    conn.commit()
    if cur.lastrowid:
        opportunity_id = cur.lastrowid
    else:
        row = conn.execute(
            "SELECT id FROM theme_opportunities WHERE user_id = ? AND trade_date = ? AND theme = ?",
            (user_id, item["trade_date"], item["theme"]),
        ).fetchone()
        opportunity_id = row["id"]
    conn.close()
    return opportunity_id


def get_opportunity(opportunity_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM theme_opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    conn.close()
    return _row_to_opportunity(row) if row else None


def list_opportunities(trade_date: str = None, user_id: str = "default", limit: int = 20) -> list[dict]:
    conn = _get_conn()
    if trade_date:
        rows = conn.execute("""
            SELECT * FROM theme_opportunities
            WHERE user_id = ? AND trade_date = ?
            ORDER BY opportunity_score DESC, id DESC
            LIMIT ?
        """, (user_id, trade_date, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM theme_opportunities
            WHERE user_id = ?
            ORDER BY trade_date DESC, opportunity_score DESC, id DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
    conn.close()
    return [_row_to_opportunity(r) for r in rows]


def update_opportunity_status(opportunity_id: int, status: str) -> bool:
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE theme_opportunities SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, opportunity_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def create_decision_from_opportunity(opportunity_id: int, user_id: str = "default") -> int:
    """把机会卡保存为理财决策草案。"""
    item = get_opportunity(opportunity_id)
    if not item:
        raise ValueError("机会不存在")

    from db.decisions import create_decision

    fund = (item.get("matched_funds") or [{}])[0]
    entry_plan = item.get("entry_plan") or {}
    exit_plan = item.get("exit_plan") or {}
    review_at = exit_plan.get("review_date")
    decision_type = "add" if item.get("verdict") == "can_buy" else "watch"
    target_code = fund.get("fund_code") or item.get("theme", "")
    target_name = fund.get("fund_name") or item.get("theme", "")

    decision_id = create_decision(
        user_id=user_id,
        source_type="opportunity",
        source_id=opportunity_id,
        decision_type=decision_type,
        target_type="fund" if fund else "theme",
        target_code=target_code,
        target_name=target_name,
        summary=f"{item['theme']}：{item.get('summary', '')}",
        rationale=item.get("policy_signal", ""),
        evidence={
            "theme": item.get("theme"),
            "score": item.get("opportunity_score"),
            "evidence": item.get("evidence", []),
            "portfolio_fit": item.get("portfolio_fit", {}),
            "entry_plan": entry_plan,
            "exit_plan": exit_plan,
        },
        risk={
            "risk_note": item.get("risk_note", ""),
            "valuation_role": item.get("valuation_role", ""),
        },
        suitability={
            "checklist": [
                "确认资金来自机会资金或长期权益资金",
                "确认场外基金持有期和赎回费",
                "确认单主题仓位不超过计划上限",
                "确认退出条件和复盘日期",
            ],
        },
        confidence="medium" if item.get("opportunity_score", 0) < 75 else "high",
        status="proposed",
        review_at=review_at,
        actions=[
            {
                "action_type": "pre_trade_check",
                "title": f"执行前检查 {item['theme']} 的资金、费率和仓位约束",
                "params": {"opportunity_id": opportunity_id, "entry_plan": entry_plan},
            },
            {
                "action_type": "schedule_review",
                "title": f"{review_at} 复盘 {item['theme']} 机会是否兑现" if review_at else f"复盘 {item['theme']} 机会是否兑现",
                "scheduled_at": review_at,
            },
        ],
    )
    update_opportunity_status(opportunity_id, "watching")
    return decision_id


def mark_opportunity_bought(opportunity_id: int, fund_code: str, amount: float = 0,
                            transaction_id: int | None = None, user_id: str = "default") -> int:
    """标记机会已买入并创建跟踪记录。"""
    item = get_opportunity(opportunity_id)
    if not item:
        raise ValueError("机会不存在")
    review_date = (item.get("exit_plan") or {}).get("review_date")
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO theme_opportunity_tracks
            (opportunity_id, fund_code, transaction_id, entry_date, entry_amount, review_due_date, last_checked_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
    """, (
        opportunity_id,
        fund_code,
        transaction_id,
        datetime.now().strftime("%Y-%m-%d"),
        amount,
        review_date,
    ))
    conn.commit()
    track_id = cur.lastrowid
    conn.close()
    update_opportunity_status(opportunity_id, "bought")
    return track_id
