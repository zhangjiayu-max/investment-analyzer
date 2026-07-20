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

    # ── P1-N: 机会回测命中率跟踪表 ──
    # 用途：每次 save_opportunity 时插入模拟跟踪记录，15 个交易日后自动回测
    # 解决问题：原 theme_opportunity_tracks 表是"用户已买入后跟踪"，0 条记录导致命中率统计永远为 None
    conn.execute("""
        CREATE TABLE IF NOT EXISTS theme_opportunity_backtests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER REFERENCES theme_opportunities(id) ON DELETE CASCADE,
            theme TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            review_date TEXT NOT NULL,
            entry_price REAL,
            review_price REAL,
            change_pct REAL,
            hit INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            reviewed_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_theme_backtest_review ON theme_opportunity_backtests(review_date, hit)")


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
    entry_price = None
    entry_shares = None
    if transaction_id:
        try:
            from db.portfolio import get_transaction, get_holding_by_fund

            tx = get_transaction(transaction_id)
            if tx:
                entry_price = tx.get("price")
                entry_shares = tx.get("shares")
                if not amount:
                    amount = tx.get("amount") or 0
                if not review_date and tx.get("transaction_date"):
                    review_date = tx.get("transaction_date")
            holding = get_holding_by_fund(fund_code, user_id=user_id)
            current_price = (holding or {}).get("current_price")
        except Exception:
            current_price = None
    else:
        current_price = None
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO theme_opportunity_tracks
            (opportunity_id, fund_code, transaction_id, entry_date, entry_amount, entry_price, entry_shares,
             current_price, review_due_date, last_checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
    """, (
        opportunity_id,
        fund_code,
        transaction_id,
        datetime.now().strftime("%Y-%m-%d"),
        amount,
        entry_price,
        entry_shares,
        current_price,
        review_date,
    ))
    conn.commit()
    track_id = cur.lastrowid
    conn.close()
    update_opportunity_status(opportunity_id, "bought")
    return track_id


def _refresh_track_metrics(track: dict) -> dict:
    """基于交易与当前净值刷新机会跟踪收益率。"""
    track = dict(track)
    performance = _calculate_track_performance(track)
    if performance.get("current_price") is not None:
        track.update(performance)
    if not track.get("transaction_id"):
        return track
    try:
        fields = {k: v for k, v in performance.items() if v is not None}
        if "current_price" not in fields and performance.get("current_price") is None:
            fields["current_price"] = track.get("current_price")
        if not fields:
            return track
        conn = _get_conn()
        set_sql = ", ".join([f"{key} = ?" for key in fields.keys()])
        conn.execute(
            f"UPDATE theme_opportunity_tracks SET {set_sql}, last_checked_at = datetime('now','localtime') WHERE id = ?",
            (*fields.values(), track["id"]),
        )
        conn.commit()
        conn.close()
        track.update(fields)
        return track
    except Exception:
        return track


def _calculate_track_performance(track: dict) -> dict:
    """计算机会跟踪的当前收益，不负责落库。"""
    if not track.get("transaction_id"):
        return {}
    try:
        from db.portfolio import get_transaction, get_holding_by_fund

        tx = get_transaction(track["transaction_id"])
        if not tx:
            return {}
        entry_price = tx.get("price") or track.get("entry_price")
        entry_amount = tx.get("amount") or track.get("entry_amount") or 0
        entry_shares = tx.get("shares") or track.get("entry_shares") or 0
        current_price = track.get("current_price")
        holding = get_holding_by_fund(track.get("fund_code") or "", user_id=track.get("user_id") or "default")
        if holding and holding.get("current_price") is not None:
            current_price = holding.get("current_price")

        current_return_pct = None
        if entry_price and current_price and entry_price > 0 and current_price > 0:
            current_return_pct = round(((current_price - entry_price) / entry_price) * 100, 2)
        elif entry_amount and entry_shares and current_price and current_price > 0:
            current_value = entry_shares * current_price
            current_return_pct = round(((current_value - entry_amount) / entry_amount) * 100, 2)
        fields = {"current_price": current_price}
        if current_return_pct is not None:
            fields["current_return_pct"] = current_return_pct
            if current_return_pct > (track.get("max_return_pct") or 0):
                fields["max_return_pct"] = current_return_pct
            if current_return_pct < 0:
                existing_drawdown = abs(track.get("max_drawdown_pct") or 0)
                fields["max_drawdown_pct"] = max(existing_drawdown, abs(current_return_pct))
        return fields
    except Exception:
        return {}


def list_opportunity_tracks(user_id: str = "default", limit: int = 20) -> list[dict]:
    """列出机会跟踪记录，带上机会卡摘要。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            t.*,
            o.user_id AS opportunity_user_id,
            o.trade_date,
            o.theme,
            o.verdict,
            o.opportunity_score,
            o.summary,
            o.policy_signal,
            o.future_direction,
            o.exit_plan_json,
            o.status AS opportunity_status
        FROM theme_opportunity_tracks t
        JOIN theme_opportunities o ON o.id = t.opportunity_id
        WHERE o.user_id = ?
        ORDER BY COALESCE(t.last_checked_at, t.created_at) DESC, t.id DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()

    items = []
    for row in rows:
        item = dict(row)
        item["exit_plan"] = _json_loads(item.pop("exit_plan_json", ""), {})
        items.append(item)
    return items


def get_opportunity_track_stats(user_id: str = "default", limit: int = 10) -> dict:
    """汇总机会跟踪状态，用于共享证据和复盘提示。"""
    conn = _get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    joined = conn.execute("""
        SELECT
            t.id,
            t.fund_code,
            t.transaction_id,
            t.entry_price,
            t.entry_amount,
            t.entry_shares,
            t.current_price,
            t.review_due_date,
            t.exit_triggered,
            t.current_return_pct,
            t.max_return_pct,
            t.max_drawdown_pct,
            t.last_checked_at,
            o.status AS opportunity_status,
            o.user_id AS user_id,
            o.theme,
            o.opportunity_score
        FROM theme_opportunity_tracks t
        JOIN theme_opportunities o ON o.id = t.opportunity_id
        WHERE o.user_id = ?
    """, (user_id,)).fetchall()
    conn.close()

    total = len(joined)
    due_reviews = 0
    open_tracks = 0
    bought_tracks = 0
    exited_tracks = 0
    returns = []
    evaluated_tracks = 0
    positive_tracks = 0
    recent_items = []

    for row in joined:
        track = dict(row)
        track.update(_calculate_track_performance(track))
        track = _refresh_track_metrics(track)
        review_due_date = track["review_due_date"]
        current_return_pct = track.get("current_return_pct")
        if row["exit_triggered"]:
            exited_tracks += 1
        if row["opportunity_status"] == "bought":
            bought_tracks += 1
        if row["opportunity_status"] in ("watching", "bought"):
            open_tracks += 1
        if review_due_date and str(review_due_date) <= today and not row["exit_triggered"]:
            due_reviews += 1
        if current_return_pct is not None:
            returns.append(float(current_return_pct))
            evaluated_tracks += 1
            if current_return_pct > 0:
                positive_tracks += 1
        if len(recent_items) < limit:
            recent_items.append({
                "track_id": track["id"],
                "theme": track["theme"],
                "opportunity_score": track["opportunity_score"],
                "review_due_date": review_due_date,
                "exit_triggered": bool(track["exit_triggered"]),
                "current_return_pct": track.get("current_return_pct"),
                "max_return_pct": track.get("max_return_pct"),
                "max_drawdown_pct": track.get("max_drawdown_pct"),
                "last_checked_at": track.get("last_checked_at"),
                "opportunity_status": track["opportunity_status"],
            })

    average_return = round(sum(returns) / len(returns), 2) if returns else None
    return {
        "total": total,
        "open_tracks": open_tracks,
        "bought_tracks": bought_tracks,
        "exited_tracks": exited_tracks,
        "due_reviews": due_reviews,
        "evaluated_tracks": evaluated_tracks,
        "positive_tracks": positive_tracks,
        "hit_rate": round((positive_tracks / evaluated_tracks) * 100, 1) if evaluated_tracks else None,
        "average_return_pct": average_return,
        "recent_items": recent_items,
    }


# ── P1-N: 机会回测命中率跟踪 ──────────────────────────────────


def create_opportunity_backtest(data: dict) -> int:
    """创建机会回测记录（每次 save_opportunity 时插入）。

    Args:
        data: {opportunity_id, theme, entry_date, review_date, entry_price}

    Returns:
        backtest_id
    """
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO theme_opportunity_backtests (
                opportunity_id, theme, entry_date, review_date, entry_price
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            data.get("opportunity_id"),
            data.get("theme", ""),
            data.get("entry_date", ""),
            data.get("review_date", ""),
            data.get("entry_price"),
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_pending_backtests() -> list[dict]:
    """列出已到期但未回测的记录（review_date <= today AND hit IS NULL）。"""
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT * FROM theme_opportunity_backtests "
            "WHERE review_date <= ? AND hit IS NULL "
            "ORDER BY review_date ASC",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_opportunity_backtest(backtest_id: int, fields: dict) -> bool:
    """更新回测记录（回测后填充 review_price/hit/change_pct）。"""
    if not fields:
        return False
    conn = _get_conn()
    try:
        allowed = {"review_price", "hit", "change_pct", "reviewed_at"}
        sets = []
        values = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                values.append(v)
        if not sets:
            return False
        values.append(backtest_id)
        cur = conn.execute(
            f"UPDATE theme_opportunity_backtests SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_backtest_stats() -> dict:
    """获取回测命中率统计（用于前端展示）。"""
    conn = _get_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM theme_opportunity_backtests"
        ).fetchone()["n"]
        reviewed = conn.execute(
            "SELECT COUNT(*) AS n FROM theme_opportunity_backtests WHERE hit IS NOT NULL"
        ).fetchone()["n"]
        hit = conn.execute(
            "SELECT COUNT(*) AS n FROM theme_opportunity_backtests WHERE hit = 1"
        ).fetchone()["n"]
        miss = conn.execute(
            "SELECT COUNT(*) AS n FROM theme_opportunity_backtests WHERE hit = 0"
        ).fetchone()["n"]

        # 按主题分组命中率
        theme_rows = conn.execute(
            "SELECT theme, "
            "SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) AS reviewed, "
            "SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) AS hit "
            "FROM theme_opportunity_backtests GROUP BY theme"
        ).fetchall()
        theme_stats = [
            {
                "theme": r["theme"],
                "reviewed": r["reviewed"],
                "hit": r["hit"],
                "hit_rate": round(r["hit"] / r["reviewed"] * 100, 1) if r["reviewed"] else None,
            }
            for r in theme_rows
        ]

        return {
            "total": total,
            "reviewed": reviewed,
            "hit": hit,
            "miss": miss,
            "hit_rate": round(hit / reviewed * 100, 1) if reviewed else None,
            "theme_stats": theme_stats,
        }
    finally:
        conn.close()
