"""统一投资决策账本 CRUD — investment_decisions 表操作。

三模块联动优化 P0（2026-08-01）：见 doc/plans/2026-08-01-三模块联动优化.md

定位：决策闭环的核心账本。串联「机会雷达发现 → 专家评估 → 智能补仓 sizing →
组合风控 → 执行 → 回测归因」全链路，是系统"越来越准"的数据基础。

设计要点：
- 每条决策记录决策时快照（信号/置信度/证据），保证回测可归因到具体信号
- status 状态机：suggested → confirmed → executed → closed / expired
- exit_plan_json + exit_state_json 承载补仓-止盈闭环（S2）的分批止盈状态机
- review_result_json + is_hit + excess_return_pct 承载回测归因，反哺信号权重（A3）

CRUD 约定：create_xxx()->int / get_xxx(id)->dict|None / list_xxx()->list[dict] /
update_xxx(id, **fields)->bool。所有 SQL 参数化，连接走 _get_conn（WAL 主库）。
"""
import json
import logging
from datetime import datetime, timedelta

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# 合法状态集合（状态机约束）
VALID_STATUS = {"suggested", "confirmed", "executed", "closed", "expired"}


def init_investment_decisions_table(conn):
    """初始化 investment_decisions 统一决策账本表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS investment_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            decision_type TEXT NOT NULL,
            source_module TEXT NOT NULL,
            fund_code TEXT NOT NULL,
            fund_name TEXT,
            index_code TEXT,
            theme TEXT,
            signal_snapshot_json TEXT DEFAULT '{}',
            confidence REAL,
            verdict TEXT,
            suggested_amount REAL DEFAULT 0,
            actual_amount REAL,
            actual_price REAL,
            status TEXT DEFAULT 'suggested',
            risk_check_json TEXT DEFAULT '{}',
            exit_plan_json TEXT DEFAULT '{}',
            exit_state_json TEXT DEFAULT '{}',
            review_at TEXT,
            reviewed_at TEXT,
            review_result_json TEXT DEFAULT '{}',
            is_hit INTEGER,
            excess_return_pct REAL,
            source_ref_id INTEGER,
            trace_id TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inv_dec_user_status "
        "ON investment_decisions(user_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inv_dec_fund "
        "ON investment_decisions(fund_code, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inv_dec_review "
        "ON investment_decisions(review_at, reviewed_at)"
    )


def _row_to_decision(row) -> dict | None:
    """数据库行 → 决策 dict（解析 JSON 字段）。"""
    if row is None:
        return None
    item = dict(row)
    for json_field, obj_field, default in (
        ("signal_snapshot_json", "signal_snapshot", {}),
        ("risk_check_json", "risk_check", {}),
        ("exit_plan_json", "exit_plan", {}),
        ("exit_state_json", "exit_state", {}),
        ("review_result_json", "review_result", {}),
    ):
        if json_field in item:
            try:
                item[obj_field] = json.loads(item.pop(json_field) or json.dumps(default))
            except (json.JSONDecodeError, TypeError):
                item[obj_field] = default
    # is_hit: 1/0/None → True/False/None（None 表示未回测）
    if item.get("is_hit") is not None:
        item["is_hit"] = bool(item["is_hit"])
    return item


def create_decision(
    decision_type: str,
    source_module: str,
    fund_code: str,
    user_id: str = "default",
    fund_name: str | None = None,
    index_code: str | None = None,
    theme: str | None = None,
    signal_snapshot: dict | None = None,
    confidence: float | None = None,
    verdict: str | None = None,
    suggested_amount: float = 0,
    status: str = "suggested",
    risk_check: dict | None = None,
    exit_plan: dict | None = None,
    review_days: int | None = None,
    source_ref_id: int | None = None,
    trace_id: str | None = None,
    notes: str | None = None,
) -> int:
    """创建一条投资决策记录，返回 id。

    Args:
        decision_type: opportunity_buy / smart_add / loss_recovery / valuation_channel
        source_module: opportunity_radar / smart_add / agent_pipeline
        signal_snapshot: 决策时的信号/证据/置信度快照（用于回测归因）
        confidence: 决策置信度 0-1
        verdict: can_buy / watch / avoid / reduce
        risk_check: 组合风控检查结果（相关性/集中度/β）
        exit_plan: 止盈止损计划（S2 状态机初始态）
        review_days: 回测窗口天数（None 则不设回测时点）
    """
    if status not in VALID_STATUS:
        status = "suggested"
    review_at = None
    if review_days:
        review_at = (datetime.now() + timedelta(days=review_days)).strftime("%Y-%m-%d")

    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO investment_decisions
                (user_id, decision_type, source_module, fund_code, fund_name,
                 index_code, theme, signal_snapshot_json, confidence, verdict,
                 suggested_amount, status, risk_check_json, exit_plan_json,
                 exit_state_json, review_at, source_ref_id, trace_id, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, decision_type, source_module, fund_code, fund_name,
            index_code, theme,
            json.dumps(signal_snapshot or {}, ensure_ascii=False),
            confidence, verdict, suggested_amount, status,
            json.dumps(risk_check or {}, ensure_ascii=False),
            json.dumps(exit_plan or {}, ensure_ascii=False),
            json.dumps({}, ensure_ascii=False),
            review_at, source_ref_id, trace_id, notes,
        ))
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def get_decision(decision_id: int) -> dict | None:
    """获取单条决策完整详情。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM investment_decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        return _row_to_decision(row) if row else None
    finally:
        conn.close()


def list_decisions(
    user_id: str = "default",
    status: str | None = None,
    fund_code: str | None = None,
    decision_type: str | None = None,
    source_module: str | None = None,
    include_reviewed: bool = True,
    limit: int = 100,
) -> list[dict]:
    """查询决策列表（按创建时间倒序）。"""
    conn = _get_conn()
    try:
        conditions = ["user_id = ?"]
        params: list = [user_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        if fund_code:
            conditions.append("fund_code = ?")
            params.append(fund_code)
        if decision_type:
            conditions.append("decision_type = ?")
            params.append(decision_type)
        if source_module:
            conditions.append("source_module = ?")
            params.append(source_module)
        if not include_reviewed:
            conditions.append("reviewed_at IS NULL")
        where = " AND ".join(conditions)
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM investment_decisions WHERE {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_decision(r) for r in rows]
    finally:
        conn.close()


def update_decision_status(
    decision_id: int,
    status: str,
    actual_amount: float | None = None,
    actual_price: float | None = None,
) -> bool:
    """更新决策状态（状态机流转），可选填实际成交金额/价格。"""
    if status not in VALID_STATUS:
        logger.warning(f"非法决策状态: {status}，忽略")
        return False
    conn = _get_conn()
    try:
        fields = ["status = ?", "updated_at = datetime('now','localtime')"]
        params: list = [status]
        if actual_amount is not None:
            fields.append("actual_amount = ?")
            params.append(actual_amount)
        if actual_price is not None:
            fields.append("actual_price = ?")
            params.append(actual_price)
        params.append(decision_id)
        cur = conn.execute(
            f"UPDATE investment_decisions SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_exit_state(decision_id: int, exit_state: dict) -> bool:
    """更新止盈闭环执行状态（S2：已减仓批次、回本提醒触发等）。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE investment_decisions SET exit_state_json = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (json.dumps(exit_state or {}, ensure_ascii=False), decision_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_exit_plan(decision_id: int, exit_plan: dict) -> bool:
    """更新止盈计划（S2：补仓后生成/调整分批止盈计划）。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE investment_decisions SET exit_plan_json = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (json.dumps(exit_plan or {}, ensure_ascii=False), decision_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_review(
    decision_id: int,
    is_hit: bool,
    excess_return_pct: float | None = None,
    review_result: dict | None = None,
    status_after: str | None = "closed",
) -> bool:
    """记录回测归因结果（A3：反哺信号权重的数据源）。

    Args:
        is_hit: 是否命中（超额收益达标）
        excess_return_pct: 超额收益%（涨幅 - 基准）
        review_result: 归因详情（各信号贡献、基准、价格路径等）
        status_after: 回测后状态（默认 closed）
    """
    conn = _get_conn()
    try:
        fields = [
            "reviewed_at = datetime('now','localtime')",
            "is_hit = ?",
            "excess_return_pct = ?",
            "review_result_json = ?",
            "updated_at = datetime('now','localtime')",
        ]
        params: list = [int(is_hit), excess_return_pct,
                        json.dumps(review_result or {}, ensure_ascii=False)]
        if status_after and status_after in VALID_STATUS:
            fields.append("status = ?")
            params.append(status_after)
        params.append(decision_id)
        cur = conn.execute(
            f"UPDATE investment_decisions SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_decisions_due_for_review(
    user_id: str = "default",
    as_of_date: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """获取已到回测时点且尚未回测的决策（闭环定时任务用）。"""
    if as_of_date is None:
        as_of_date = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM investment_decisions
               WHERE user_id = ? AND reviewed_at IS NULL
                 AND review_at IS NOT NULL AND review_at <= ?
                 AND status IN ('confirmed', 'executed')
               ORDER BY review_at ASC LIMIT ?""",
            (user_id, as_of_date, limit),
        ).fetchall()
        return [_row_to_decision(r) for r in rows]
    finally:
        conn.close()


def get_decision_accuracy_stats(
    user_id: str = "default",
    days: int = 90,
) -> dict:
    """决策准确率统计（命中率/平均超额收益，按来源模块分组）。

    用于前端展示与 A3 信号权重反哺。
    """
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        # 总体命中率
        overall = conn.execute(
            """SELECT COUNT(*) AS reviewed,
                      SUM(CASE WHEN is_hit = 1 THEN 1 ELSE 0 END) AS hits,
                      AVG(excess_return_pct) AS avg_excess
               FROM investment_decisions
               WHERE user_id = ? AND reviewed_at IS NOT NULL AND created_at >= ?""",
            (user_id, start_date),
        ).fetchone()
        # 按来源模块分组
        by_module = conn.execute(
            """SELECT source_module,
                      COUNT(*) AS reviewed,
                      SUM(CASE WHEN is_hit = 1 THEN 1 ELSE 0 END) AS hits,
                      AVG(excess_return_pct) AS avg_excess
               FROM investment_decisions
               WHERE user_id = ? AND reviewed_at IS NOT NULL AND created_at >= ?
               GROUP BY source_module""",
            (user_id, start_date),
        ).fetchall()
        # 按主题分组（机会雷达来源）
        by_theme = conn.execute(
            """SELECT theme,
                      COUNT(*) AS reviewed,
                      SUM(CASE WHEN is_hit = 1 THEN 1 ELSE 0 END) AS hits
               FROM investment_decisions
               WHERE user_id = ? AND reviewed_at IS NOT NULL AND created_at >= ?
                 AND theme IS NOT NULL AND theme != ''
               GROUP BY theme""",
            (user_id, start_date),
        ).fetchall()
    finally:
        conn.close()

    def _stats(row) -> dict:
        reviewed = row["reviewed"] or 0
        hits = row["hits"] or 0
        return {
            "reviewed": reviewed,
            "hits": hits,
            "hit_rate_pct": round(hits / reviewed * 100, 1) if reviewed else 0.0,
            "avg_excess_pct": round(row["avg_excess"], 2) if row["avg_excess"] is not None else None,
        }

    return {
        "overall": _stats(overall),
        "by_module": {r["source_module"]: _stats(r) for r in by_module},
        "by_theme": {
            r["theme"]: {
                "reviewed": r["reviewed"] or 0,
                "hits": r["hits"] or 0,
                "hit_rate_pct": round((r["hits"] or 0) / (r["reviewed"] or 1) * 100, 1),
            }
            for r in by_theme
        },
        "window_days": days,
    }
