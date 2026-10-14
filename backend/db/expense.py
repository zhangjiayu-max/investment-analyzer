"""每月支出记录数据层。

设计稿：doc/plans/2026-10-14-financial-decision-enhancement.md

- expense_records 表：月份/分类/金额/固定项
- 写操作后自动回填 user_profiles.monthly_expense（近 3 个月实际记录均值）
  与 monthly_surplus（income - expense），受 system_config 'expense.auto_fill_profile' 开关控制
"""

from __future__ import annotations

from datetime import datetime

from db._conn import _get_conn

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


def _validate_month(month: str) -> str:
    """校验并规范化月份格式 YYYY-MM。"""
    value = (month or "").strip()
    try:
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
    return datetime.now().strftime("%Y-%m")


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
    if amount <= 0:
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
    conn.close()
    return [_row_to_dict(r) for r in rows]


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
        if fields["amount"] <= 0:
            raise ValueError("金额必须为正数")
    if "is_fixed" in fields:
        fields["is_fixed"] = 1 if fields["is_fixed"] else 0
    conn = _get_conn()
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


def get_expense_summary(month: str, user_id: str = "default") -> dict:
    """月度汇总：总额/固定/浮动/分类占比/贷款还款额。"""
    month = _validate_month(month)
    conn = _get_conn()
    rows = conn.execute(
        "SELECT category, is_fixed, SUM(amount) AS total FROM expense_records "
        "WHERE user_id = ? AND month = ? GROUP BY category, is_fixed",
        (user_id, month),
    ).fetchall()
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
        "count": sum(1 for _ in rows),
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
    """把 from_month 的固定支出复制到 to_month。返回复制条数（幂等：同月同类同额不重复）。"""
    from_month = _validate_month(from_month)
    to_month = _validate_month(to_month)
    conn = _get_conn()
    rows = conn.execute(
        "SELECT category, sub_category, amount, note FROM expense_records "
        "WHERE user_id = ? AND month = ? AND is_fixed = 1",
        (user_id, from_month),
    ).fetchall()
    copied = 0
    for r in rows:
        exists = conn.execute(
            "SELECT 1 FROM expense_records WHERE user_id = ? AND month = ? AND category = ? "
            "AND sub_category = ? AND amount = ? AND is_fixed = 1",
            (user_id, to_month, r["category"], r["sub_category"], r["amount"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO expense_records (user_id, month, category, sub_category, amount, is_fixed, note) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (user_id, to_month, r["category"], r["sub_category"], r["amount"], r["note"]),
        )
        copied += 1
    conn.commit()
    conn.close()
    if copied:
        _auto_fill_profile(user_id)
    return copied


def get_cashflow(user_id: str = "default", month: str | None = None) -> dict:
    """收支视图：月收入 - 支出 = 可投资结余（供 wealth_advisor / allocation_advisor 消费）。"""
    from db.dashboard import get_user_profile

    profile = get_user_profile(user_id) or {}
    month = _validate_month(month) if month else _current_month()
    summary = get_expense_summary(month, user_id)
    income = float(profile.get("monthly_income") or 0)

    # 支出优先用当月实际记录；当月无记录时回退画像静态值
    if summary["total"] > 0:
        expense = summary["total"]
        expense_source = "records"
    else:
        expense = float(profile.get("monthly_expense") or 0)
        expense_source = "profile_static"

    surplus = income - expense
    return {
        "month": month,
        "monthly_income": round(income, 2),
        "monthly_expense": round(expense, 2),
        "expense_source": expense_source,
        "fixed_total": summary["fixed_total"],
        "debt_payment": summary["debt_payment"],
        "monthly_surplus": round(surplus, 2),
        "surplus_rate": round(surplus / income, 4) if income > 0 else None,
    }


# ── 回填钩子 ──────────────────────────────────────────


def _auto_fill_profile(user_id: str = "default"):
    """写操作后自动回填 user_profiles.monthly_expense / monthly_surplus。

    规则：monthly_expense = 近 3 个有记录月份的支出均值（不足 3 个月按实际月份数）。
    开关：system_config 'expense.auto_fill_profile'（默认 true）。
    失败不阻塞主流程。
    """
    try:
        from db.config import get_config
        if get_config("expense.auto_fill_profile", "true").lower() != "true":
            return

        conn = _get_conn()
        rows = conn.execute(
            "SELECT month, SUM(amount) AS total FROM expense_records "
            "WHERE user_id = ? GROUP BY month ORDER BY month DESC LIMIT 3",
            (user_id,),
        ).fetchall()
        conn.close()
        if not rows:
            return
        avg_expense = round(sum(float(r["total"]) for r in rows) / len(rows), 2)

        from db.dashboard import get_user_profile, update_user_profile
        profile = get_user_profile(user_id) or {}
        income = float(profile.get("monthly_income") or 0)
        fields: dict = {"monthly_expense": avg_expense}
        if income > 0:
            fields["monthly_surplus"] = round(income - avg_expense, 2)
        update_user_profile(user_id, **fields)
    except Exception:
        # 回填失败不影响主流程
        pass
