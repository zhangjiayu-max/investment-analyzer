"""每月支出记录数据层。

设计稿：doc/plans/2026-10-14-financial-decision-enhancement.md

- expense_records 表：月份/分类/金额/固定项
- 实际支出、月度预算与周期账单分离；未结账月份采用保守预算
- 写操作仅更新画像结余，不用不完整记账覆盖用户设置的月预算
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import math
import re

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# ── 分类常量（代码管理，不做 DB 配置表）──
FIXED_CATEGORIES = [
    "房贷", "车贷", "保险", "水电物业", "话费宽带", "订阅服务", "子女教育", "赡养费",
]
VARIABLE_CATEGORIES = [
    "餐饮", "交通", "购物", "娱乐", "医疗", "教育提升", "人情往来", "旅游", "其他",
]
ALL_CATEGORIES = FIXED_CATEGORIES + VARIABLE_CATEGORIES

# 贷款类分类（用于汇总负债还款额）
DEBT_CATEGORIES = {"房贷", "车贷"}


def get_expense_categories() -> dict:
    """返回分类常量，供前端下拉框使用。"""
    return {
        "fixed": FIXED_CATEGORIES,
        "variable": VARIABLE_CATEGORIES,
    }


def init_expense_tables(conn):
    """初始化支出记录表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expense_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            month TEXT NOT NULL,
            category TEXT NOT NULL,
            sub_category TEXT,
            amount REAL NOT NULL,
            is_fixed INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expense_user_month ON expense_records(user_id, month)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expense_fixed ON expense_records(user_id, is_fixed)"
    )

    columns = {row[1] for row in conn.execute("PRAGMA table_info(expense_records)")}
    if "copied_from_id" not in columns:
        conn.execute("ALTER TABLE expense_records ADD COLUMN copied_from_id INTEGER")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_expense_copy_identity "
                 "ON expense_records(user_id, month, copied_from_id) WHERE copied_from_id IS NOT NULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS expense_month_status (
        user_id TEXT NOT NULL, month TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id, month)
    )""")


def _validate_month(month: str) -> str:
    """校验并规范化月份格式 YYYY-MM。"""
    value = (month or "").strip()
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
            raise ValueError("noncanonical month")
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise ValueError(f"月份格式错误（应为 YYYY-MM）: {month}")
    return value


def _validate_category(category: str) -> str:
    value = (category or "").strip()
    if value not in ALL_CATEGORIES:
        raise ValueError(f"无效支出分类: {category}（可选: {', '.join(ALL_CATEGORIES)}）")
    return value


def _row_to_dict(row) -> dict:
    item = dict(row)
    item["amount"] = float(item["amount"])
    item["is_fixed"] = bool(item["is_fixed"])
    return item


def _current_month() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")


# ── CRUD ──────────────────────────────────────────────


def create_expense(
    month: str,
    category: str,
    amount: float,
    user_id: str = "default",
    sub_category: str = "",
    is_fixed: bool = False,
    note: str = "",
) -> int:
    """新增支出记录，返回 id。"""
    month = _validate_month(month)
    category = _validate_category(category)
    amount = float(amount)
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("金额必须为正数")
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO expense_records (user_id, month, category, sub_category, amount, is_fixed, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, month, category, sub_category or "", amount, 1 if is_fixed else 0, note or ""),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    _auto_fill_profile(user_id)
    return new_id


def get_expense(expense_id: int, user_id: str = "default") -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM expense_records WHERE id = ? AND user_id = ?",
        (expense_id, user_id),
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def list_expenses(month: str | None = None, user_id: str = "default") -> list[dict]:
    """按月查询支出明细；month 为空则返回全部（按月份倒序）。"""
    conn = _get_conn()
    if month:
        month = _validate_month(month)
        rows = conn.execute(
            "SELECT * FROM expense_records WHERE user_id = ? AND month = ? "
            "ORDER BY is_fixed DESC, category, id",
            (user_id, month),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM expense_records WHERE user_id = ? "
            "ORDER BY month DESC, is_fixed DESC, category, id",
            (user_id,),
        ).fetchall()
    linked = {r["expense_id"]: r["template_id"] for r in conn.execute(
        "SELECT expense_id, template_id FROM recurring_expense_payments WHERE user_id=?", (user_id,)
    )}
    conn.close()
    return [dict(_row_to_dict(r), recurring_template_id=linked.get(r["id"])) for r in rows]


def update_expense(expense_id: int, user_id: str = "default", **fields) -> bool:
    """更新支出记录。"""
    allowed = {"month", "category", "sub_category", "amount", "is_fixed", "note"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    if "month" in fields:
        fields["month"] = _validate_month(fields["month"])
    if "category" in fields:
        fields["category"] = _validate_category(fields["category"])
    if "amount" in fields:
        fields["amount"] = float(fields["amount"])
        if not math.isfinite(fields["amount"]) or fields["amount"] <= 0:
            raise ValueError("金额必须为正数")
    if "is_fixed" in fields:
        fields["is_fixed"] = 1 if fields["is_fixed"] else 0
    conn = _get_conn()
    linked = conn.execute("SELECT 1 FROM recurring_expense_payments WHERE expense_id=? AND user_id=?",
                          (expense_id, user_id)).fetchone()
    if linked:
        conn.close()
        raise ValueError("周期账单的已支付记录不能直接修改；请删除支付记录后重新确认")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE expense_records SET {set_clause}, updated_at = datetime('now','localtime') "
        "WHERE id = ? AND user_id = ?",
        list(fields.values()) + [expense_id, user_id],
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    if ok:
        _auto_fill_profile(user_id)
    return ok


def delete_expense(expense_id: int, user_id: str = "default") -> bool:
    conn = _get_conn()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM recurring_expense_payments WHERE expense_id=? AND user_id=?",
                 (expense_id, user_id))
    cur = conn.execute(
        "DELETE FROM expense_records WHERE id = ? AND user_id = ?",
        (expense_id, user_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    if ok:
        _auto_fill_profile(user_id)
    return ok


# ── 汇总 ──────────────────────────────────────────────


def get_expense_summary(month: str, user_id: str = "default", *, conn=None) -> dict:
    """月度汇总：总额/固定/浮动/分类占比/贷款还款额。"""
    month = _validate_month(month)
    owned = conn is None
    conn = conn if conn is not None else _get_conn()
    rows = conn.execute(
        "SELECT category, is_fixed, COUNT(*) AS record_count, SUM(amount) AS total FROM expense_records "
        "WHERE user_id = ? AND month = ? GROUP BY category, is_fixed",
        (user_id, month),
    ).fetchall()
    if owned:
        conn.close()

    total = 0.0
    fixed_total = 0.0
    debt_total = 0.0
    by_category: dict[str, float] = {}
    for r in rows:
        amount = float(r["total"])
        total += amount
        if r["is_fixed"]:
            fixed_total += amount
        if r["category"] in DEBT_CATEGORIES:
            debt_total += amount
        by_category[r["category"]] = round(by_category.get(r["category"], 0.0) + amount, 2)

    return {
        "month": month,
        "total": round(total, 2),
        "fixed_total": round(fixed_total, 2),
        "variable_total": round(total - fixed_total, 2),
        "debt_payment": round(debt_total, 2),
        "by_category": by_category,
        "count": sum(r["record_count"] for r in rows),
    }


def get_expense_trend(months: int = 6, user_id: str = "default") -> list[dict]:
    """近 N 个月支出趋势（含无记录的月份补 0）。"""
    months = max(1, min(int(months), 24))
    conn = _get_conn()
    rows = conn.execute(
        "SELECT month, SUM(amount) AS total, "
        "SUM(CASE WHEN is_fixed = 1 THEN amount ELSE 0 END) AS fixed_total "
        "FROM expense_records WHERE user_id = ? GROUP BY month ORDER BY month DESC LIMIT ?",
        (user_id, months),
    ).fetchall()
    conn.close()

    by_month = {
        r["month"]: {"total": float(r["total"]), "fixed_total": float(r["fixed_total"])}
        for r in rows
    }

    # 补齐最近 N 个月（含无记录月份）
    now = datetime.now()
    result = []
    for i in range(months - 1, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            m += 12
            y -= 1
        key = f"{y:04d}-{m:02d}"
        item = by_month.get(key, {"total": 0.0, "fixed_total": 0.0})
        result.append({
            "month": key,
            "total": round(item["total"], 2),
            "fixed_total": round(item["fixed_total"], 2),
        })
    return result


def copy_fixed_expenses(from_month: str, to_month: str, user_id: str = "default") -> int:
    """按原始记录身份复制固定项；同额同备注也保留，重复操作不重复入账。"""
    from_month = _validate_month(from_month)
    to_month = _validate_month(to_month)
    if from_month == to_month:
        return 0
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT e.*, COALESCE(e.copied_from_id,e.id) AS origin_id FROM expense_records e "
            "WHERE e.user_id=? AND e.month=? AND e.is_fixed=1 "
            "AND NOT EXISTS (SELECT 1 FROM recurring_expense_payments p WHERE p.expense_id=e.id)",
            (user_id, from_month),
        ).fetchall()
        copied = 0
        for r in rows:
            exists = conn.execute(
                "SELECT 1 FROM expense_records WHERE user_id=? AND month=? "
                "AND COALESCE(copied_from_id,id)=?", (user_id,to_month,r["origin_id"])
            ).fetchone()
            if exists:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO expense_records "
                "(user_id,month,category,sub_category,amount,is_fixed,note,copied_from_id) "
                "VALUES (?,?,?,?,?,1,?,?)",
                (user_id,to_month,r["category"],r["sub_category"] or "",r["amount"],r["note"] or "",r["origin_id"]),
            )
            copied += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if copied:
        _auto_fill_profile(user_id)
    return copied


def set_expense_month_complete(month: str, complete: bool, user_id: str = "default") -> None:
    """用户明确确认当月实际支出已录完；可撤销，不改写月预算。"""
    month = _validate_month(month)
    conn = _get_conn()
    try:
        conn.execute("INSERT INTO expense_month_status(user_id,month,complete) VALUES (?,?,?) "
                     "ON CONFLICT(user_id,month) DO UPDATE SET complete=excluded.complete",
                     (user_id, month, int(complete)))
        conn.commit()
    finally:
        conn.close()
    _auto_fill_profile(user_id)


def get_cashflow(user_id: str = "default", month: str | None = None) -> dict:
    """统一家庭现金流：实际/待付/月均预留、月预算和应急储备分别计算。"""
    from db.recurring_expenses import get_recurring_summary

    month = _validate_month(month) if month else _current_month()
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        profile_row = conn.execute("SELECT * FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        profile = dict(profile_row) if profile_row else {}
        summary = get_expense_summary(month, user_id, conn=conn)
        bills = get_recurring_summary(month, user_id, conn=conn)
        row = conn.execute("SELECT complete FROM expense_month_status WHERE user_id=? AND month=?",
                           (user_id, month)).fetchone()
        investment_row = conn.execute("SELECT emergency_months FROM user_investment_profile WHERE user_id=?",
                                      (user_id,)).fetchone()
        buckets = conn.execute("SELECT * FROM goal_buckets WHERE user_id=? AND status='active'", (user_id,)).fetchall()
        cash_row = conn.execute("SELECT balance FROM portfolio_cash WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    income = max(0.0, float(profile.get("monthly_income") or 0))
    budget = max(0.0, float(profile.get("monthly_expense") or 0))
    complete = bool(row and row["complete"])
    recorded = summary["total"]
    pending = bills["pending_total"]
    provision = bills["future_provision"]
    known_expense = recorded + pending + provision
    expense = known_expense if complete else max(budget, known_expense)
    source = "records" if complete else "budget_and_records" if recorded or pending or provision else "profile_static"
    surplus = income - expense
    target_months = int(investment_row["emergency_months"] or 6) if investment_row else 6
    emergency = [dict(b) for b in buckets if b["bucket_type"] == "emergency"]
    cash = max(0.0, float(cash_row["balance"] or 0)) if cash_row else 0.0
    emergency_target = max(sum(float(b.get("target_amount") or 0) for b in emergency), expense * target_months)
    emergency_available = min(cash, sum(max(0.0, float(b.get("current_amount") or 0)) for b in emergency)) if emergency else cash
    emergency_gap = max(0.0, emergency_target - emergency_available)
    reserve = min(max(0.0, surplus), emergency_gap)
    data_complete = income > 0 and (budget > 0 or complete)
    investable = max(0.0, surplus - reserve) if data_complete else 0.0
    debt_payment = summary["debt_payment"] + bills["pending_debt_payment"]
    return {
        "month": month,
        "monthly_income": round(income, 2),
        "monthly_expense": round(expense, 2),
        "expense_source": source,
        "expense_complete": complete,
        "recorded_expense": round(recorded, 2),
        "budget_expense": round(budget, 2),
        "pending_bills": round(pending, 2),
        "future_provision": round(provision, 2),
        "monthly_provision": bills["monthly_provision"],
        "fixed_total": summary["fixed_total"],
        "debt_payment": round(debt_payment, 2),
        "monthly_surplus": round(surplus, 2),
        "surplus_rate": round(surplus / income, 4) if income > 0 else None,
        "investable_surplus": round(investable, 2),
        "bucket_reserve": round(reserve, 2),
        "emergency_target": round(emergency_target, 2),
        "emergency_available": round(emergency_available, 2),
        "emergency_gap": round(emergency_gap, 2),
        "emergency_months": round(emergency_available / expense, 1) if expense > 0 else None,
        "emergency_target_months": target_months,
        "protected_cash": round(min(cash, emergency_target + max(expense - recorded, pending + provision)), 2),
        "cash_balance": round(cash, 2),
        "data_available": data_complete,
        "overdue_bills": bills["overdue_count"],
        "upcoming_bills": bills["upcoming_count"],
    }


def _auto_fill_profile(user_id: str = "default"):
    """仅同步预计月结余；月预算始终由用户显式维护，避免少记一笔就覆盖预算。"""
    try:
        from db.config import get_config
        from db.dashboard import update_user_profile
        if get_config("expense.auto_fill_profile", "true").lower() != "true":
            return
        cashflow = get_cashflow(user_id)
        update_user_profile(user_id, monthly_surplus=cashflow["monthly_surplus"])
    except Exception:
        logger.warning("同步家庭现金流结余失败 user_id=%s", user_id, exc_info=True)
