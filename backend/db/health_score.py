"""综合理财健康分 — DB 层"""
import json
from datetime import datetime
from db._conn import _get_conn


def init_health_score_tables(conn):
    """健康分相关表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score_date TEXT NOT NULL,
            total_score INTEGER DEFAULT 0,
            score_quality INTEGER DEFAULT 0,
            score_diversification INTEGER DEFAULT 0,
            score_valuation INTEGER DEFAULT 0,
            score_behavior INTEGER DEFAULT 0,
            score_risk INTEGER DEFAULT 0,
            advice_json TEXT DEFAULT '[]',
            detail_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_date ON health_scores(score_date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bond_yield_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            yield_10y REAL,
            yield_3y REAL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(trade_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bond_date ON bond_yield_history(trade_date)")


def init_health_score_v2_tables(conn):
    """全账户资产健康度诊断 2.0 相关表。"""
    # 用户投资画像：风险偏好 + 四笔钱目标
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_investment_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            risk_level TEXT NOT NULL DEFAULT 'steady',
            target_date TEXT,
            target_pots TEXT NOT NULL DEFAULT '{"cash":10,"steady":35,"long_term":50,"insurance":5}',
            monthly_investable REAL DEFAULT 0,
            emergency_months INTEGER DEFAULT 6,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id)
        )
    """)

    # 健康分 2.0 快照
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_score_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score_date TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            total_score INTEGER DEFAULT 0,
            score_quality INTEGER DEFAULT 0,
            score_diversification INTEGER DEFAULT 0,
            score_valuation INTEGER DEFAULT 0,
            score_behavior INTEGER DEFAULT 0,
            score_risk INTEGER DEFAULT 0,
            asset_snapshot TEXT DEFAULT '{}',
            dimension_details TEXT DEFAULT '{}',
            actions_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, score_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_v2_date ON health_score_v2(score_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_v2_user_date ON health_score_v2(user_id, score_date)")

    # 行动项追踪（效果闭环）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_action_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            candidate_id INTEGER,
            title TEXT,
            category TEXT,
            status TEXT DEFAULT 'pending',
            accepted_at TEXT,
            executed_at TEXT,
            impact_estimate REAL,
            actual_return REAL,
            feedback TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hact_action ON health_action_tracking(action_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hact_user_status ON health_action_tracking(user_id, status)")


def save_health_score(score_date: str, total_score: int,
                      score_quality: int, score_diversification: int,
                      score_valuation: int, score_behavior: int,
                      score_risk: int, advice: list = None,
                      detail: dict = None) -> int:
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO health_scores
        (score_date, total_score, score_quality, score_diversification,
         score_valuation, score_behavior, score_risk, advice_json, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        score_date, total_score, score_quality, score_diversification,
        score_valuation, score_behavior, score_risk,
        json.dumps(advice or [], ensure_ascii=False),
        json.dumps(detail or {}, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()
    return 0


def get_health_score(score_date: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM health_scores WHERE score_date = ?", (score_date,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_health_scores(limit: int = 30) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM health_scores ORDER BY score_date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_bond_yield(trade_date: str, yield_10y: float, yield_3y: float = None) -> int:
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO bond_yield_history (trade_date, yield_10y, yield_3y)
        VALUES (?, ?, ?)
    """, (trade_date, yield_10y, yield_3y))
    conn.commit()
    conn.close()
    return 0


def get_latest_bond_yield() -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM bond_yield_history ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_bond_yield_history(days: int = 365) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bond_yield_history ORDER BY trade_date DESC LIMIT ?", (days,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
# 全账户资产健康度诊断 2.0 CRUD
# ════════════════════════════════════════════════════════════

def get_user_investment_profile(user_id: str = "default") -> dict | None:
    """获取用户投资画像。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM user_investment_profile WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["target_pots"] = json.loads(d.get("target_pots") or '{}')
    except Exception:
        d["target_pots"] = {"cash": 10, "steady": 35, "long_term": 50, "insurance": 5}
    return d


def save_user_investment_profile(user_id: str = "default",
                                 risk_level: str = "steady",
                                 target_date: str = None,
                                 target_pots: dict = None,
                                 monthly_investable: float = 0,
                                 emergency_months: int = 6) -> bool:
    """保存或更新用户投资画像。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO user_investment_profile
            (user_id, risk_level, target_date, target_pots,
             monthly_investable, emergency_months, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            risk_level=excluded.risk_level,
            target_date=excluded.target_date,
            target_pots=excluded.target_pots,
            monthly_investable=excluded.monthly_investable,
            emergency_months=excluded.emergency_months,
            updated_at=excluded.updated_at
    """, (
        user_id, risk_level, target_date,
        json.dumps(target_pots or {"cash": 10, "steady": 35, "long_term": 50, "insurance": 5}, ensure_ascii=False),
        monthly_investable, emergency_months,
    ))
    conn.commit()
    conn.close()
    return True


def save_health_score_v2(score_date: str, user_id: str = "default",
                         total_score: int = 0,
                         score_quality: int = 0, score_diversification: int = 0,
                         score_valuation: int = 0, score_behavior: int = 0,
                         score_risk: int = 0,
                         asset_snapshot: dict = None,
                         dimension_details: dict = None,
                         actions: list = None) -> int:
    """保存健康分 2.0 快照。"""
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO health_score_v2
            (score_date, user_id, total_score, score_quality, score_diversification,
             score_valuation, score_behavior, score_risk,
             asset_snapshot, dimension_details, actions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        score_date, user_id, total_score, score_quality, score_diversification,
        score_valuation, score_behavior, score_risk,
        json.dumps(asset_snapshot or {}, ensure_ascii=False),
        json.dumps(dimension_details or {}, ensure_ascii=False),
        json.dumps(actions or [], ensure_ascii=False),
    ))
    conn.commit()
    conn.close()
    return 0


def get_health_score_v2(score_date: str, user_id: str = "default") -> dict | None:
    """获取某天的健康分 2.0 快照。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM health_score_v2 WHERE score_date = ? AND user_id = ?",
        (score_date, user_id)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _parse_health_score_v2_row(row)


def list_health_scores_v2(user_id: str = "default", limit: int = 30) -> list[dict]:
    """列出健康分 2.0 历史。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM health_score_v2 WHERE user_id = ? ORDER BY score_date DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [_parse_health_score_v2_row(r) for r in rows]


def _parse_health_score_v2_row(row) -> dict:
    """解析健康分 2.0 行数据。"""
    d = dict(row)
    try:
        d["asset_snapshot"] = json.loads(d.get("asset_snapshot") or '{}')
    except Exception:
        d["asset_snapshot"] = {}
    try:
        d["dimension_details"] = json.loads(d.get("dimension_details") or '{}')
    except Exception:
        d["dimension_details"] = {}
    try:
        d["actions_json"] = json.loads(d.get("actions_json") or '[]')
    except Exception:
        d["actions_json"] = []
    return d


def track_health_action(action_id: str, user_id: str = "default",
                        candidate_id: int = None, title: str = "",
                        category: str = "", impact_estimate: float = None) -> int:
    """记录一条健康诊断行动项。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT OR IGNORE INTO health_action_tracking
            (action_id, user_id, candidate_id, title, category, impact_estimate, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (action_id, user_id, candidate_id, title, category, impact_estimate))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id or 0


def update_health_action_status(action_id: str, user_id: str = "default",
                                status: str = "pending", feedback: str = None,
                                actual_return: float = None) -> bool:
    """更新行动项状态。"""
    if status not in ("pending", "accepted", "rejected", "executed"):
        return False
    conn = _get_conn()
    accepted_at = datetime.now().isoformat() if status == "accepted" else None
    executed_at = datetime.now().isoformat() if status == "executed" else None
    conn.execute("""
        UPDATE health_action_tracking
        SET status = ?, feedback = ?, actual_return = ?,
            accepted_at = COALESCE(?, accepted_at),
            executed_at = COALESCE(?, executed_at),
            updated_at = datetime('now','localtime')
        WHERE action_id = ? AND user_id = ?
    """, (status, feedback, actual_return, accepted_at, executed_at, action_id, user_id))
    conn.commit()
    conn.close()
    return True


def list_health_action_tracking(user_id: str = "default", status: str = None,
                                limit: int = 100) -> list[dict]:
    """列出行动项追踪记录。"""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM health_action_tracking WHERE user_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM health_action_tracking WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
