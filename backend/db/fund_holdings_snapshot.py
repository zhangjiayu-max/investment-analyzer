"""基金持仓快照 DB 层 — fund_stock_holdings 表 CRUD。

表结构：按 (fund_code, report_date, stock_code) 复合主键存储季度持仓快照。
用途：支持季度持仓变化追踪（新进/增持/减持/退出）。
"""
import json
from datetime import datetime

from db._conn import _get_conn


def init_fund_holdings_snapshot_tables(conn):
    """基金持仓快照相关表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_stock_holdings (
            fund_code    TEXT NOT NULL,
            report_date  TEXT NOT NULL,
            stock_code   TEXT NOT NULL,
            stock_name   TEXT,
            pct_nav      REAL,
            shares       REAL,
            market_value REAL,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (fund_code, report_date, stock_code)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fund_holdings_fund_date "
        "ON fund_stock_holdings(fund_code, report_date)"
    )

    # ── P0-6 综合快照表（含 top_stocks/bond_holdings/asset_allocation/industry_allocation）──
    # 用途：akshare 失效时返回最近一次成功的缓存，避免 top_stocks 永远为空
    # 与 fund_stock_holdings 共存：后者按 (fund_code, report_date, stock_code) 粒度细分，
    # 本表按 fund_code 唯一存最近一次完整数据
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_holdings_snapshots (
            fund_code                 TEXT PRIMARY KEY,
            top_stocks_json           TEXT DEFAULT '[]',
            bond_holdings_json        TEXT DEFAULT '[]',
            asset_allocation_json     TEXT DEFAULT '[]',
            industry_allocation_json  TEXT DEFAULT '[]',
            bond_type_summary_json    TEXT DEFAULT '{}',
            report_date               TEXT,
            data_source               TEXT,  -- akshare / ttfund
            updated_at                TEXT DEFAULT (datetime('now','localtime'))
        )
    """)


def save_fund_holdings_snapshot(
    fund_code: str, report_date: str, holdings: list[dict]
) -> int:
    """批量写入基金某季度的持仓快照（INSERT OR REPLACE）。

    Args:
        fund_code: 基金代码
        report_date: 季报日期，如 "2025-09-30"
        holdings: 持仓列表 [{stock_code, stock_name, pct_nav, shares, market_value}, ...]

    Returns:
        写入条数。
    """
    if not holdings:
        return 0
    conn = _get_conn()
    try:
        rows = [
            (
                fund_code,
                report_date,
                h.get("stock_code", ""),
                h.get("stock_name", ""),
                float(h.get("pct_nav", 0) or 0),
                float(h.get("shares", 0) or 0),
                float(h.get("market_value", 0) or 0),
            )
            for h in holdings
            if h.get("stock_code")
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO fund_stock_holdings
                (fund_code, report_date, stock_code, stock_name, pct_nav, shares, market_value)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def list_fund_holdings_snapshots(fund_code: str, limit: int = 4) -> list[dict]:
    """查询某基金最近 N 个季度的持仓快照（按 report_date 倒序）。

    Returns:
        [{report_date, holdings: [{stock_code, stock_name, pct_nav, ...}]}, ...]
        按季度分组，最近季度在前。
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT report_date, stock_code, stock_name, pct_nav, shares, market_value
            FROM fund_stock_holdings
            WHERE fund_code = ?
            ORDER BY report_date DESC
            """,
            (fund_code,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    # 按 report_date 分组
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        rd = d["report_date"]
        by_date.setdefault(rd, []).append({
            "stock_code": d["stock_code"],
            "stock_name": d["stock_name"],
            "pct_nav": d["pct_nav"],
            "shares": d["shares"],
            "market_value": d["market_value"],
        })

    return [{"report_date": rd, "holdings": stocks} for rd, stocks in by_date.items()][:limit]


def get_fund_holdings_snapshot(fund_code: str, report_date: str) -> dict | None:
    """查询某基金指定季度的持仓快照。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT stock_code, stock_name, pct_nav, shares, market_value
            FROM fund_stock_holdings
            WHERE fund_code = ? AND report_date = ?
            """,
            (fund_code, report_date),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None
    return {
        "fund_code": fund_code,
        "report_date": report_date,
        "holdings": [dict(r) for r in rows],
    }


def compare_fund_holdings(fund_code: str) -> dict:
    """对比某基金最近两个季度的持仓，返回调仓动作列表。

    动作判定阈值 0.5%：
    - new: 上季度无，本季度有
    - increase: 占比上升 > 0.5%
    - decrease: 占比下降 > 0.5%
    - exit: 上季度有，本季度无

    Returns:
        {has_history, current_quarter, prev_quarter, changes: [...], summary}
    """
    snapshots = list_fund_holdings_snapshots(fund_code, limit=2)
    if len(snapshots) < 2:
        return {
            "has_history": False,
            "current_quarter": snapshots[0]["report_date"] if snapshots else None,
            "prev_quarter": None,
            "changes": [],
            "summary": "无历史快照，无法对比",
        }

    current_q = snapshots[0]
    prev_q = snapshots[1]
    current_map = {s["stock_code"]: s for s in current_q["holdings"]}
    prev_map = {s["stock_code"]: s for s in prev_q["holdings"]}

    changes = []
    THRESHOLD = 0.5

    # 新进 + 增持/减持
    for code, cur in current_map.items():
        prev = prev_map.get(code)
        cur_name = cur.get("stock_name", "")
        if prev is None:
            changes.append({
                "stock_code": code,
                "stock_name": cur_name,
                "action": "new",
                "delta_pct": round(cur["pct_nav"], 2),
            })
        else:
            delta = cur["pct_nav"] - prev["pct_nav"]
            if delta > THRESHOLD:
                changes.append({
                    "stock_code": code,
                    "stock_name": cur_name,
                    "action": "increase",
                    "delta_pct": round(delta, 2),
                })
            elif delta < -THRESHOLD:
                changes.append({
                    "stock_code": code,
                    "stock_name": cur_name,
                    "action": "decrease",
                    "delta_pct": round(delta, 2),
                })

    # 退出
    for code, prev in prev_map.items():
        if code not in current_map:
            changes.append({
                "stock_code": code,
                "stock_name": prev.get("stock_name", ""),
                "action": "exit",
                "delta_pct": round(-prev["pct_nav"], 2),
            })

    # 按变化幅度排序
    changes.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)

    return {
        "has_history": True,
        "current_quarter": current_q["report_date"],
        "prev_quarter": prev_q["report_date"],
        "changes": changes,
        "summary": _summarize_changes(changes),
    }


def _summarize_changes(changes: list[dict]) -> str:
    """生成调仓摘要文本。"""
    if not changes:
        return "本季度持仓无显著变化"
    counts = {"new": 0, "increase": 0, "decrease": 0, "exit": 0}
    for c in changes:
        counts[c["action"]] = counts.get(c["action"], 0) + 1

    parts = []
    if counts["new"]:
        parts.append(f"新进{counts['new']}只")
    if counts["increase"]:
        parts.append(f"增持{counts['increase']}只")
    if counts["decrease"]:
        parts.append(f"减持{counts['decrease']}只")
    if counts["exit"]:
        parts.append(f"退出{counts['exit']}只")

    # 调仓力度判定
    max_delta = max((abs(c["delta_pct"]) for c in changes), default=0)
    if max_delta >= 3:
        force = "力度较大"
    elif max_delta >= 1:
        force = "力度温和"
    else:
        force = "力度轻微"

    return f"本季度{'，'.join(parts)}，调仓{force}"


def list_fund_codes_with_snapshots() -> list[str]:
    """查询所有有持仓快照的基金代码（用于批量刷新）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT fund_code FROM fund_stock_holdings"
        ).fetchall()
        return [r["fund_code"] for r in rows]
    finally:
        conn.close()


# ── P0-6 综合快照表 CRUD ──────────────────────────────────────


def save_full_fund_holdings_snapshot(fund_code: str, data: dict, data_source: str = "akshare") -> bool:
    """写入/更新基金完整持仓快照（top_stocks + bond_holdings + asset_allocation + industry_allocation）。

    用途：akshare 失效时返回最近一次成功的缓存，避免 top_stocks 永远为空。
    何时调用：get_fund_holdings 成功获取数据后；ttfund 兜底成功后。

    Args:
        fund_code: 基金代码
        data: 完整持仓数据，结构同 get_fund_holdings 的返回值
        data_source: "akshare" / "ttfund"
    Returns:
        True 表示写入成功。
    """
    if not fund_code or not data:
        return False
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO fund_holdings_snapshots
                (fund_code, top_stocks_json, bond_holdings_json, asset_allocation_json,
                 industry_allocation_json, bond_type_summary_json, report_date, data_source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
        """, (
            fund_code,
            json.dumps(data.get("top_stocks", []), ensure_ascii=False),
            json.dumps(data.get("bond_holdings", []), ensure_ascii=False),
            json.dumps(data.get("asset_allocation", []), ensure_ascii=False),
            json.dumps(data.get("industry_allocation", []), ensure_ascii=False),
            json.dumps(data.get("bond_type_summary", {}), ensure_ascii=False),
            data.get("report_date"),
            data_source,
        ))
        conn.commit()
        return True
    finally:
        conn.close()


def get_latest_full_fund_holdings_snapshot(fund_code: str) -> dict | None:
    """读取基金最近一次完整持仓快照（akshare/ttfund 数据失效时兜底）。

    Returns:
        与 get_fund_holdings 相同结构的 dict；额外字段 _data_source / _snapshot_updated_at
        用于标记数据来源和陈旧度；无快照返回 None。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM fund_holdings_snapshots WHERE fund_code = ?",
            (fund_code,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None

    d = dict(row)
    return {
        "fund_code": fund_code,
        "top_stocks": json.loads(d.get("top_stocks_json") or "[]"),
        "bond_holdings": json.loads(d.get("bond_holdings_json") or "[]"),
        "asset_allocation": json.loads(d.get("asset_allocation_json") or "[]"),
        "industry_allocation": json.loads(d.get("industry_allocation_json") or "[]"),
        "bond_type_summary": json.loads(d.get("bond_type_summary_json") or "{}"),
        "report_date": d.get("report_date"),
        "_data_source": d.get("data_source") or "local_snapshot",
        "_snapshot_updated_at": d.get("updated_at"),
        "_stale": True,  # 标记为陈旧数据，调用方可据此降低置信度
    }
