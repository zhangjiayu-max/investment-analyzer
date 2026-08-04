"""短线主题机会 CRUD 与决策联动。"""

import json
from datetime import datetime, timedelta

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
    # O-3（2026-07-21）：theme_opportunities 主表新增 4 个核心字段
    # 原问题：save_opportunity 只写 verdict/score/summary，前端机会卡片缺核心数据
    # 修复：通过 _add_column_if_not_exists 兼容已存在的表
    _add_column_if_not_exists(conn, "theme_opportunities", "entry_price", "REAL")
    _add_column_if_not_exists(conn, "theme_opportunities", "entry_amount", "REAL")
    _add_column_if_not_exists(conn, "theme_opportunities", "valuation_percentile", "REAL")
    _add_column_if_not_exists(conn, "theme_opportunities", "review_status", "TEXT")
    # 2026-07-30 conv#194：新增 opportunity_type 列区分卡片类型（news/loss_recovery）
    # 原问题：loss_recovery 补仓回本卡片无法落库，因表无 opportunity_type 列
    _add_column_if_not_exists(conn, "theme_opportunities", "opportunity_type", "TEXT")
    _add_column_if_not_exists(conn, "theme_opportunities", "signal_source", "TEXT")

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

    # L3 回测基准化（2026-07-21）：新增 benchmark_pct/excess_return 字段
    # 用 ALTER TABLE 兼容已有数据
    _ensure_column(conn, "theme_opportunity_backtests", "benchmark_pct", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "excess_return", "REAL")

    # LI-6（2026-07-22）：回测增强 — 信号来源标记 + miss 原因
    _ensure_column(conn, "theme_opportunity_backtests", "signal_source", "TEXT DEFAULT 'news'")
    _ensure_column(conn, "theme_opportunity_backtests", "miss_reason", "TEXT")

    # F-4（2026-07-23）：入场估值分位（用于 miss_reason 拼接和反哺分析）
    _ensure_column(conn, "theme_opportunity_backtests", "entry_percentile", "REAL")

    # Accuracy-Fix（2026-07-27）：入场时的资金面/量能信号，用于分维度命中率分析
    # capital_signal: inflow/outflow/neutral  volume_signal: expand/shrink/neutral
    _ensure_column(conn, "theme_opportunity_backtests", "capital_signal", "TEXT")
    _ensure_column(conn, "theme_opportunity_backtests", "volume_signal", "TEXT")

    # P1-R6（2026-08-01）：含成本 walk-forward 回测 — 扩展回测表字段
    # dim_scores_json: 14 维分项分快照（供 R4 算 IC）
    # entry_amount: 决策金额（算 Sharpe 用）
    # buy_fee / sell_fee / slippage_cost: 实际扣费与滑点成本
    # net_return: 扣成本后净收益（百分比）
    # max_drawdown / sharpe / calmar: 持有期内风险指标
    _ensure_column(conn, "theme_opportunity_backtests", "dim_scores_json", "TEXT")
    _ensure_column(conn, "theme_opportunity_backtests", "entry_amount", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "buy_fee", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "sell_fee", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "slippage_cost", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "net_return", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "max_drawdown", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "sharpe", "REAL")
    _ensure_column(conn, "theme_opportunity_backtests", "calmar", "REAL")

    # Accuracy-Boost（2026-07-30）：降权/恢复日志表 — 记录权重变更历史
    conn.execute("""
        CREATE TABLE IF NOT EXISTS opportunity_weight_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            scope_value TEXT NOT NULL,
            old_weight REAL NOT NULL,
            new_weight REAL NOT NULL,
            reason TEXT DEFAULT '',
            consecutive_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_weight_log_scope ON opportunity_weight_log(scope, scope_value)")

    # P1-R6（2026-08-01）：walk-forward 滚动回测聚合表（per theme + per window）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS theme_backtest_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme TEXT,
            window_start TEXT,
            window_end TEXT,
            windows_count INTEGER,
            hit_rate REAL,
            avg_net_return REAL,
            avg_sharpe REAL,
            avg_max_drawdown REAL,
            avg_calmar REAL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_backtest_metrics_theme ON theme_backtest_metrics(theme, window_start)")


def _ensure_column(conn, table: str, column: str, col_type: str):
    """安全添加列（如果不存在）。"""
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            conn.commit()
    except Exception:
        pass


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
             risk_note, evidence_json, status, updated_at,
             entry_price, entry_amount, valuation_percentile, review_status,
             opportunity_type, signal_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'),
                ?, ?, ?, ?, ?, ?)
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
            entry_price = COALESCE(excluded.entry_price, theme_opportunities.entry_price),
            entry_amount = COALESCE(excluded.entry_amount, theme_opportunities.entry_amount),
            valuation_percentile = COALESCE(excluded.valuation_percentile, theme_opportunities.valuation_percentile),
            review_status = COALESCE(excluded.review_status, theme_opportunities.review_status),
            opportunity_type = COALESCE(excluded.opportunity_type, theme_opportunities.opportunity_type),
            signal_source = COALESCE(excluded.signal_source, theme_opportunities.signal_source),
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
        # O-3 新增 4 个核心字段
        item.get("entry_price"),
        item.get("entry_amount"),
        item.get("valuation_percentile"),
        item.get("review_status", "pending"),
        # 2026-07-30 conv#194 新增：卡片类型与信号来源
        item.get("opportunity_type", "news"),
        item.get("signal_source", "news"),
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
        data: {opportunity_id, theme, entry_date, review_date, entry_price,
               signal_source?, capital_signal?, volume_signal?, entry_percentile?,
               entry_amount?, dim_scores_json?}

    Returns:
        backtest_id
    """
    conn = _get_conn()
    try:
        # LI-6（2026-07-22）：新增 signal_source 字段（默认 'news'）
        # Accuracy-Fix（2026-07-27）：新增 capital_signal/volume_signal 字段
        # Accuracy-Boost（2026-07-30）：新增 entry_percentile 字段（用于 miss_reason 拼接和反哺分析）
        # P1-R6（2026-08-01）：新增 entry_amount / dim_scores_json 字段（含成本回测 + R4 IC 计算）
        cur = conn.execute("""
            INSERT INTO theme_opportunity_backtests (
                opportunity_id, theme, entry_date, review_date, entry_price,
                signal_source, capital_signal, volume_signal, entry_percentile,
                entry_amount, dim_scores_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("opportunity_id"),
            data.get("theme", ""),
            data.get("entry_date", ""),
            data.get("review_date", ""),
            data.get("entry_price"),
            data.get("signal_source", "news"),
            data.get("capital_signal"),
            data.get("volume_signal"),
            data.get("entry_percentile"),
            data.get("entry_amount"),
            data.get("dim_scores_json"),
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def save_backtest_metrics(metrics: dict) -> int:
    """P1-R6（2026-08-01）：保存 walk-forward 回测聚合指标。

    Args:
        metrics: {theme, window_start, window_end, windows_count, hit_rate,
                  avg_net_return, avg_sharpe, avg_max_drawdown, avg_calmar}

    Returns:
        metrics_id
    """
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO theme_backtest_metrics (
                theme, window_start, window_end, windows_count,
                hit_rate, avg_net_return, avg_sharpe, avg_max_drawdown, avg_calmar
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metrics.get("theme"),
            metrics.get("window_start"),
            metrics.get("window_end"),
            metrics.get("windows_count"),
            metrics.get("hit_rate"),
            metrics.get("avg_net_return"),
            metrics.get("avg_sharpe"),
            metrics.get("avg_max_drawdown"),
            metrics.get("avg_calmar"),
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_backtest_metrics(theme: str | None = None, limit: int = 50) -> list[dict]:
    """P1-R6：列出 walk-forward 回测聚合指标。"""
    conn = _get_conn()
    try:
        if theme:
            rows = conn.execute(
                "SELECT * FROM theme_backtest_metrics WHERE theme = ? ORDER BY created_at DESC LIMIT ?",
                (theme, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM theme_backtest_metrics ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_reviewed_backtests_with_dims(theme: str | None = None, days: int = 90) -> list[dict]:
    """P1-R4（2026-08-01）：列出已回测且含 dim_scores_json 的记录，供 IC 计算。

    Args:
        theme: 主题过滤（可选）
        days: 最近多少天

    Returns:
        [{id, theme, dim_scores_json, net_return, excess_return, hit, entry_date, review_date}, ...]
    """
    conn = _get_conn()
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        if theme:
            rows = conn.execute(
                """SELECT id, theme, dim_scores_json, net_return, excess_return, hit,
                          entry_date, review_date, signal_source
                   FROM theme_opportunity_backtests
                   WHERE hit IS NOT NULL
                     AND dim_scores_json IS NOT NULL
                     AND entry_date >= ?
                     AND theme = ?
                   ORDER BY entry_date DESC""",
                (start_date, theme),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, theme, dim_scores_json, net_return, excess_return, hit,
                          entry_date, review_date, signal_source
                   FROM theme_opportunity_backtests
                   WHERE hit IS NOT NULL
                     AND dim_scores_json IS NOT NULL
                     AND entry_date >= ?
                   ORDER BY entry_date DESC""",
                (start_date,),
            ).fetchall()
        return [dict(r) for r in rows]
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


# ── P1-R4（2026-08-01）：信号 IC 计算 ──────────────────────────

# IC 进程级缓存：(dim_name, window_days) → (ic_value, cached_ts)
_ic_cache: dict[tuple, tuple] = {}
_IC_CACHE_TTL = 3600.0  # 1 小时


def _spearman_rank_correlation(x: list[float], y: list[float]) -> float | None:
    """纯 Python 实现 Spearman 等级相关系数（无 scipy 依赖）。

    Returns:
        [-1, 1] 区间相关系数；样本不足返回 None
    """
    n = len(x)
    if n < 3 or n != len(y):
        return None

    def rank(values: list[float]) -> list[float]:
        """返回 values 的秩（平均秩处理并列）。"""
        indexed = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            # 找到所有与 values[indexed[i]] 相等的元素
            while j + 1 < n and values[indexed[j + 1]] == values[indexed[i]]:
                j += 1
            # 平均秩（1-based）
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg_rank
            i = j + 1
        return ranks

    rx = rank(x)
    ry = rank(y)
    # Pearson 相关系数 on ranks = Spearman
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = (sum((r - mean_rx) ** 2 for r in rx)) ** 0.5
    den_y = (sum((r - mean_ry) ** 2 for r in ry)) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def calc_signal_ic(dim_name: str, window_days: int = 90) -> float | None:
    """P1-R4：计算某维度信号与净超额收益的 Spearman IC。

    逻辑：
    - 取最近 window_days 内已 review 且含 dim_scores_json 的回测记录
    - 对 (dim_score, net_excess_return) 算 Spearman rank correlation
    - 缓存 1 小时（_ic_cache）

    Args:
        dim_name: 维度名（如 'news'/'policy'/'valuation'...）
        window_days: 回看窗口天数

    Returns:
        IC 值 [-1, 1]；样本不足返回 None
    """
    import time as _time
    cache_key = (dim_name, window_days)
    cached = _ic_cache.get(cache_key)
    if cached and (_time.time() - cached[1]) < _IC_CACHE_TTL:
        return cached[0]

    records = list_reviewed_backtests_with_dims(days=window_days)
    if len(records) < 10:  # 样本不足（配置 min_samples 默认 30，但底层至少要 10 才能算相关性）
        _ic_cache[cache_key] = (None, _time.time())
        return None

    x_scores = []
    y_returns = []
    for r in records:
        dims_json = r.get("dim_scores_json")
        if not dims_json:
            continue
        try:
            dims = json.loads(dims_json) if isinstance(dims_json, str) else dims_json
        except (TypeError, json.JSONDecodeError):
            continue
        dim_score = dims.get(dim_name)
        if dim_score is None:
            continue
        # 用 net_return（扣成本后净收益）作为信号预测目标
        net_return = r.get("net_return")
        if net_return is None:
            # 回退到 excess_return
            net_return = r.get("excess_return")
        if net_return is None:
            continue
        x_scores.append(float(dim_score))
        y_returns.append(float(net_return))

    if len(x_scores) < 10:
        _ic_cache[cache_key] = (None, _time.time())
        return None

    ic = _spearman_rank_correlation(x_scores, y_returns)
    _ic_cache[cache_key] = (ic, _time.time())
    return ic


def get_all_dim_ic(window_days: int = 90, min_samples: int = 30) -> dict[str, float | None]:
    """P1-R4：批量计算所有维度的 IC 值。

    Returns:
        {dim_name: ic_value or None}
    """
    dims = ["news", "policy", "basic", "valuation", "holding", "tradability",
            "tech", "capital", "sentiment", "leading", "volume", "research",
            "margin", "etf", "regime"]
    result = {}
    for d in dims:
        result[d] = calc_signal_ic(d, window_days)
    return result


def update_opportunity_backtest(backtest_id: int, fields: dict) -> bool:
    """更新回测记录（回测后填充 review_price/hit/change_pct）。"""
    if not fields:
        return False
    conn = _get_conn()
    try:
        allowed = {"review_price", "hit", "change_pct", "reviewed_at", "benchmark_pct", "excess_return", "miss_reason", "entry_percentile", "capital_signal", "volume_signal",
                   # P1-R6（2026-08-01）：含成本 walk-forward 回测扩展字段
                   "dim_scores_json", "entry_amount", "buy_fee", "sell_fee", "slippage_cost", "net_return", "max_drawdown", "sharpe", "calmar"}
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
    """获取原始与持有期去重后的回测统计。"""
    conn = _get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            """SELECT b.*, COALESCE(o.verdict, 'unknown') AS verdict
               FROM theme_opportunity_backtests b
               LEFT JOIN theme_opportunities o ON o.id = b.opportunity_id
               ORDER BY b.theme, b.entry_date, b.id"""
        ).fetchall()]

        reviewed_rows = [r for r in rows if r.get("hit") is not None]

        def _aggregate(items: list[dict]) -> dict:
            reviewed = len(items)
            hits = sum(1 for item in items if item.get("hit") == 1)
            net_values = [float(item["net_return"]) for item in items if item.get("net_return") is not None]
            excess_values = [float(item["excess_return"]) for item in items if item.get("excess_return") is not None]
            return {
                "reviewed": reviewed,
                "hits": hits,
                "misses": reviewed - hits,
                "hit_rate": round(hits / reviewed * 100, 1) if reviewed else None,
                "avg_net_return": round(sum(net_values) / len(net_values), 2) if net_values else None,
                "avg_excess_return": round(sum(excess_values) / len(excess_values), 2) if excess_values else None,
            }

        def _group(items: list[dict], key: str) -> dict:
            grouped: dict[str, list[dict]] = {}
            for item in items:
                value = str(item.get(key) or "unknown")
                grouped.setdefault(value, []).append(item)
            return {value: _aggregate(group) for value, group in grouped.items()}

        # 同主题持有窗口重叠的连续信号只保留第一条，避免伪造独立样本量。
        independent_rows = []
        last_review_by_theme: dict[str, str] = {}
        for row in reviewed_rows:
            theme = row.get("theme") or "unknown"
            entry_date = str(row.get("entry_date") or "")
            last_review = last_review_by_theme.get(theme)
            if last_review and entry_date <= last_review:
                continue
            independent_rows.append(row)
            last_review_by_theme[theme] = str(row.get("review_date") or entry_date)

        raw_stats = _aggregate(reviewed_rows)
        independent_stats = _aggregate(independent_rows)
        by_source = _group(independent_rows, "signal_source")
        by_theme = _group(independent_rows, "theme")
        by_verdict = _group(independent_rows, "verdict")
        theme_stats = [
            {"theme": theme, "hit": values["hits"], **values}
            for theme, values in by_theme.items()
        ]
        source_count = len({str(r.get("signal_source") or "news") for r in independent_rows})
        market_months = len({str(r.get("entry_date") or "")[:7] for r in independent_rows})
        independent_count = len(independent_rows)
        dimension_counts: dict[str, int] = {}
        for row in independent_rows:
            try:
                dimensions = json.loads(row.get("dim_scores_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                dimensions = {}
            for name, value in dimensions.items():
                if value is not None:
                    dimension_counts[name] = dimension_counts.get(name, 0) + 1
        expected_dimensions = {
            "news", "policy", "basic", "valuation", "holding",
            "tradability", "tech", "capital", "sentiment", "leading",
            "volume", "research", "margin", "etf", "regime",
        }
        dimension_samples = {
            name: dimension_counts.get(name, 0) for name in sorted(expected_dimensions)
        }
        dimensions_ready = all(count >= 30 for count in dimension_samples.values())

        return {
            "total": len(rows),
            "reviewed": independent_stats["reviewed"],
            "hit": independent_stats["hits"],
            "miss": independent_stats["misses"],
            "hit_rate": independent_stats["hit_rate"],
            "raw_reviewed": raw_stats["reviewed"],
            "raw_hit": raw_stats["hits"],
            "raw_miss": raw_stats["misses"],
            "raw_hit_rate": raw_stats["hit_rate"],
            "independent_sample_count": independent_count,
            "independent_stats": independent_stats,
            "theme_stats": theme_stats,
            "by_source": by_source,
            "by_theme": by_theme,
            "by_verdict": by_verdict,
            "raw_by_source": _group(reviewed_rows, "signal_source"),
            "raw_by_theme": _group(reviewed_rows, "theme"),
            "raw_by_verdict": _group(reviewed_rows, "verdict"),
            "learning_readiness": {
                "ready": (
                    independent_count >= 100
                    and source_count >= 2
                    and market_months >= 3
                    and dimensions_ready
                ),
                "independent_samples": independent_count,
                "source_count": source_count,
                "market_months": market_months,
                "dimension_samples": dimension_samples,
                "dimensions_ready": dimensions_ready,
                "minimum_required": 100,
            },
        }
    finally:
        conn.close()


def get_backtest_stats_by_source() -> dict:
    """LI-6（2026-07-22）：按信号来源分组统计命中率。

    Returns:
        {
            "news": {"total": N, "hits": H, "hit_rate": R},
            "leading_strong": {...},
            "leading_medium": {...},
        }
    """
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT signal_source,
                   COUNT(*) as total,
                   SUM(CASE WHEN hit=1 THEN 1 ELSE 0 END) as hits,
                   SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) as reviewed
            FROM theme_opportunity_backtests
            GROUP BY signal_source
        """).fetchall()
        result = {}
        for r in rows:
            source = r["signal_source"] or "news"
            reviewed = r["reviewed"] or 0
            result[source] = {
                "total": r["total"],
                "hits": r["hits"] or 0,
                "reviewed": reviewed,
                "hit_rate": round((r["hits"] or 0) / reviewed * 100, 1) if reviewed else None,
            }
        return result
    finally:
        conn.close()


def update_backtest_miss_reason(backtest_id: int, miss_reason: str) -> bool:
    """LI-6（2026-07-22）：更新回测 miss 原因。"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE theme_opportunity_backtests SET miss_reason = ? WHERE id = ?",
            (miss_reason, backtest_id),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_consecutive_misses_by_source() -> dict:
    """LI-6（2026-07-22）：查询各信号来源的连续 miss 次数（用于命中率反哺）。

    Returns:
        {signal_source: consecutive_miss_count}
    """
    conn = _get_conn()
    try:
        # 取每个 signal_source 最近的记录，从后往前数连续 miss
        rows = conn.execute("""
            SELECT signal_source, hit
            FROM theme_opportunity_backtests
            WHERE hit IS NOT NULL
            ORDER BY signal_source, reviewed_at DESC
        """).fetchall()

        result = {}
        current_source = None
        miss_streak = 0
        for r in rows:
            source = r["signal_source"] or "news"
            if source != current_source:
                if current_source and miss_streak >= 3:
                    result[current_source] = miss_streak
                current_source = source
                miss_streak = 0
            if r["hit"] == 0:
                miss_streak += 1
            else:
                miss_streak = 0
        # 最后一个
        if current_source and miss_streak >= 3:
            result[current_source] = miss_streak
        return result
    finally:
        conn.close()


def get_consecutive_misses_by_theme() -> dict:
    """F-4+（2026-07-23）：查询各主题的连续 miss 次数（用于 per-theme 降权反哺）。

    解决 per-source 统计中不同主题 hit/miss 交错导致连续 miss 计数被打断的问题。
    按 theme 分组独立统计，半导体 6 连 miss 即使与其他主题 hit 交错也能被检测到。

    Returns:
        {theme: consecutive_miss_count}
    """
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT theme, hit
            FROM theme_opportunity_backtests
            WHERE hit IS NOT NULL
            ORDER BY theme, reviewed_at DESC
        """).fetchall()

        result = {}
        current_theme = None
        miss_streak = 0
        for r in rows:
            theme = r["theme"] or ""
            if theme != current_theme:
                if current_theme and miss_streak >= 3:
                    result[current_theme] = miss_streak
                current_theme = theme
                miss_streak = 0
            if r["hit"] == 0:
                miss_streak += 1
            else:
                miss_streak = 0
        if current_theme and miss_streak >= 3:
            result[current_theme] = miss_streak
        return result
    finally:
        conn.close()


def get_consecutive_hits_by_theme() -> dict:
    """Accuracy-Boost（2026-07-30）：查询各主题的连续 hit 次数（用于权重恢复）。

    连续 2 次 hit → 权重恢复到 1.0（从降权状态恢复）。

    Returns:
        {theme: consecutive_hit_count}（仅返回 ≥2 的主题）
    """
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT theme, hit
            FROM theme_opportunity_backtests
            WHERE hit IS NOT NULL
            ORDER BY theme, reviewed_at DESC
        """).fetchall()

        result = {}
        current_theme = None
        hit_streak = 0
        for r in rows:
            theme = r["theme"] or ""
            if theme != current_theme:
                if current_theme and hit_streak >= 2:
                    result[current_theme] = hit_streak
                current_theme = theme
                hit_streak = 0
            if r["hit"] == 1:
                hit_streak += 1
            else:
                hit_streak = 0
        if current_theme and hit_streak >= 2:
            result[current_theme] = hit_streak
        return result
    finally:
        conn.close()


def log_weight_change(scope: str, scope_value: str, old_weight: float,
                       new_weight: float, reason: str, consecutive_count: int = 0) -> None:
    """Accuracy-Boost（2026-07-30）：记录权重变更日志到 opportunity_weight_log 表。"""
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT INTO opportunity_weight_log (scope, scope_value, old_weight, new_weight, reason, consecutive_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (scope, scope_value, old_weight, new_weight, reason, consecutive_count))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def delete_avoid_verdict_backtests() -> dict:
    """Accuracy-Fix（2026-07-27）：清理 verdict=avoid 机会对应的回测记录。

    场景：历史 10 条 avoid 机会仍创建了 backtest 记录，污染命中率统计。
    策略：删除 theme_opportunity_backtests 中 opportunity_id 对应的 theme_opportunities.verdict=avoid 的记录，
    但保留已回测（hit IS NOT NULL）的 avoid 记录 —— 它们已贡献到历史统计，删除会破坏历史数据完整性。

    Returns:
        {"scanned": N, "deleted": M, "kept_reviewed": K}
    """
    conn = _get_conn()
    try:
        # 找到 avoid 机会对应的 backtest 记录
        rows = conn.execute("""
            SELECT b.id, b.hit
            FROM theme_opportunity_backtests b
            JOIN theme_opportunities o ON o.id = b.opportunity_id
            WHERE o.verdict = 'avoid'
        """).fetchall()
        scanned = len(rows)
        deleted = 0
        kept_reviewed = 0
        for r in rows:
            # 已回测的记录保留（历史数据不破坏），仅删除未回测的
            if r["hit"] is None:
                conn.execute("DELETE FROM theme_opportunity_backtests WHERE id = ?", (r["id"],))
                deleted += 1
            else:
                kept_reviewed += 1
        conn.commit()
        return {"scanned": scanned, "deleted": deleted, "kept_reviewed": kept_reviewed}
    finally:
        conn.close()


def get_backtest_stats_by_signal() -> dict:
    """Accuracy-Fix（2026-07-27）：按资金面/量能信号分组统计命中率。

    用于分析"资金流入 + 放量"信号的命中率是否高于"资金流出 + 缩量"信号，
    验证资金面和量能维度对准确率的贡献。

    Returns:
        {
            "capital_signal": {"inflow": {...}, "outflow": {...}, "neutral": {...}},
            "volume_signal": {"expand": {...}, "shrink": {...}, "neutral": {...}}
        }
    """
    conn = _get_conn()
    try:
        result = {"capital_signal": {}, "volume_signal": {}}
        for signal_col in ("capital_signal", "volume_signal"):
            rows = conn.execute(f"""
                SELECT {signal_col} as sig,
                   COUNT(*) as total,
                   SUM(CASE WHEN hit=1 THEN 1 ELSE 0 END) as hits,
                   SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) as reviewed
                FROM theme_opportunity_backtests
                WHERE {signal_col} IS NOT NULL
                GROUP BY {signal_col}
            """).fetchall()
            for r in rows:
                sig = r["sig"] or "unknown"
                reviewed = r["reviewed"] or 0
                result[signal_col][sig] = {
                    "total": r["total"],
                    "hits": r["hits"] or 0,
                    "reviewed": reviewed,
                    "hit_rate": round((r["hits"] or 0) / reviewed * 100, 1) if reviewed else None,
                }
        return result
    finally:
        conn.close()
