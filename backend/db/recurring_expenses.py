"""周期固定账单：模板生成待付投影，付款与实际支出在同一事务落库。"""

from __future__ import annotations

import calendar
from contextlib import contextmanager
import logging
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from db._conn import _get_conn
from db.expense import DEBT_CATEGORIES, FIXED_CATEGORIES

logger = logging.getLogger(__name__)
INTERVALS = {"monthly": 1, "quarterly": 3, "yearly": 12}
TEMPLATE_FIELDS = {"name", "category", "amount", "frequency", "start_month",
                   "end_month", "due_day", "active", "note"}


def init_recurring_expense_tables(conn):
    """初始化周期模板与已付账单快照表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recurring_expense_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount > 0),
            frequency TEXT NOT NULL,
            start_month TEXT NOT NULL,
            end_month TEXT,
            due_day INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recurring_expense_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            template_id INTEGER NOT NULL REFERENCES recurring_expense_templates(id),
            month TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            due_date TEXT NOT NULL,
            expense_id INTEGER NOT NULL UNIQUE REFERENCES expense_records(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(template_id, month)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_template_user "
                 "ON recurring_expense_templates(user_id, active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recurring_payment_month "
                 "ON recurring_expense_payments(user_id, month)")


def _today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _month(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("月份格式错误（应为 YYYY-MM）")
    if int(value[:4]) < 1:
        raise ValueError("月份年份必须大于零")
    return value


def _text(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name}须为不超过 {maximum} 字的文本")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name}不能为空")
    return value


def _validate(fields):
    item = {key: fields[key] for key in TEMPLATE_FIELDS}
    item["name"] = _text(item["name"], "名称", 200, required=True)
    item["note"] = _text(item["note"], "备注", 2000)
    if item["category"] not in FIXED_CATEGORIES:
        raise ValueError("周期账单仅支持固定支出分类")
    if item["frequency"] not in INTERVALS:
        raise ValueError("周期须为 monthly、quarterly 或 yearly")
    if isinstance(item["amount"], bool):
        raise ValueError("金额必须为有限正数")
    try:
        item["amount"] = float(item["amount"])
    except (ValueError, TypeError, OverflowError):
        raise ValueError("金额必须为有限正数") from None
    if not math.isfinite(item["amount"]) or item["amount"] <= 0:
        raise ValueError("金额必须为有限正数")
    item["start_month"] = _month(item["start_month"])
    if item["end_month"] is not None:
        item["end_month"] = _month(item["end_month"])
        if item["end_month"] < item["start_month"]:
            raise ValueError("结束月份不能早于开始月份")
    if type(item["due_day"]) is not int or not 1 <= item["due_day"] <= 31:
        raise ValueError("扣款日须为 1 至 31 的整数")
    if type(item["active"]) is not bool:
        raise ValueError("启用状态须为布尔值")
    return item


def _template(row):
    item = dict(row)
    item["active"] = bool(item["active"])
    item["amount"] = float(item["amount"])
    return item


def create_recurring_template(name, category, amount, frequency, start_month,
                              end_month=None, due_day=1, active=True, note="",
                              user_id="default") -> int:
    fields = _validate(locals())
    conn = _get_conn()
    try:
        keys = list(fields)
        cursor = conn.execute(
            f"INSERT INTO recurring_expense_templates (user_id, {', '.join(keys)}) "
            f"VALUES ({', '.join('?' for _ in range(len(keys) + 1))})",
            [user_id] + [fields[key] for key in keys],
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_recurring_template(template_id, user_id="default") -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM recurring_expense_templates WHERE id=? AND user_id=?",
                           (template_id, user_id)).fetchone()
        return _template(row) if row else None
    finally:
        conn.close()


@contextmanager
def _read_snapshot(conn=None):
    """允许汇总复用同一读事务，付款不能在实际与待付之间凭空消失。"""
    owned = conn is None
    conn = conn if conn is not None else _get_conn()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        yield conn
    finally:
        if owned:
            conn.close()


def list_recurring_templates(user_id="default", *, conn=None) -> list[dict]:
    with _read_snapshot(conn) as conn:
        return [_template(row) for row in conn.execute(
            "SELECT * FROM recurring_expense_templates WHERE user_id=? ORDER BY active DESC, id",
            (user_id,)).fetchall()]


def update_recurring_template(template_id, user_id="default", **fields) -> bool:
    fields = {key: value for key, value in fields.items() if key in TEMPLATE_FIELDS}
    if not fields:
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM recurring_expense_templates WHERE id=? AND user_id=?",
                           (template_id, user_id)).fetchone()
        if row is None:
            return False
        merged = _template(row)
        merged.update(fields)
        valid = _validate(merged)
        conn.execute(
            "UPDATE recurring_expense_templates SET " + ", ".join(f"{key}=?" for key in fields)
            + ", updated_at=datetime('now','localtime') WHERE id=? AND user_id=?",
            [valid[key] for key in fields] + [template_id, user_id],
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_recurring_template(template_id, user_id="default") -> bool:
    """停用模板，保留已付历史。"""
    return update_recurring_template(template_id, user_id=user_id, active=False)


def _in_range(template, month):
    return (template["active"] and template["start_month"] <= month
            and (template["end_month"] is None or month <= template["end_month"]))


def _is_due(template, month):
    if not _in_range(template, month):
        return False
    year, number = map(int, month.split("-"))
    start_year, start_number = map(int, template["start_month"].split("-"))
    return ((year - start_year) * 12 + number - start_number) % INTERVALS[template["frequency"]] == 0


def _pending_bill(template, month):
    year, number = map(int, month.split("-"))
    day = min(template["due_day"], calendar.monthrange(year, number)[1])
    return dict(template_id=template["id"], month=month, name=template["name"],
                category=template["category"], amount=template["amount"],
                due_date=f"{month}-{day:02d}", status="pending", expense_id=None)


def list_recurring_bills(month, user_id="default", *, conn=None) -> list[dict]:
    month = _month(month)
    with _read_snapshot(conn) as conn:
        templates = [_template(row) for row in conn.execute(
            "SELECT * FROM recurring_expense_templates WHERE user_id=?", (user_id,)).fetchall()]
        paid = [dict(row) for row in conn.execute(
            "SELECT template_id, month, name, category, amount, due_date, expense_id "
            "FROM recurring_expense_payments WHERE user_id=? AND month=?", (user_id, month)).fetchall()]
    template_by_id = {item["id"]: item for item in templates}
    by_id = {}
    for row in paid:
        bill = dict(row, status="paid")
        current = template_by_id.get(row["template_id"])
        if current:
            bill["current_template_amount"] = current["amount"]
            bill["current_template_frequency"] = current["frequency"]
        by_id[row["template_id"]] = bill
    for item in templates:
        if item["id"] not in by_id and _is_due(item, month):
            by_id[item["id"]] = _pending_bill(item, month)
    return sorted(by_id.values(), key=lambda row: (row["due_date"], row["template_id"]))


def pay_recurring_bill(template_id, month, user_id="default") -> int:
    """付款幂等；实际记录与账单快照同时提交，失败同时回滚。"""
    month = _month(month)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        paid = conn.execute(
            "SELECT expense_id FROM recurring_expense_payments "
            "WHERE template_id=? AND month=? AND user_id=?", (template_id, month, user_id)).fetchone()
        if paid:
            return paid["expense_id"]
        row = conn.execute("SELECT * FROM recurring_expense_templates WHERE id=? AND user_id=?",
                           (template_id, user_id)).fetchone()
        if row is None:
            raise ValueError("周期账单模板不存在")
        item = _template(row)
        if not _is_due(item, month):
            raise ValueError("该模板在所选月份没有待付账单")
        bill = _pending_bill(item, month)
        expense_id = conn.execute(
            "INSERT INTO expense_records (user_id, month, category, sub_category, amount, is_fixed, note) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (user_id, month, item["category"], item["name"], item["amount"], item["note"]),
        ).lastrowid
        conn.execute(
            "INSERT INTO recurring_expense_payments "
            "(user_id, template_id, month, name, category, amount, due_date, expense_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, template_id, month, item["name"], item["category"], item["amount"],
             bill["due_date"], expense_id),
        )
        conn.commit()
    finally:
        conn.close()
    # 衍生画像的失败不应使已成功付款呈现为失败。
    try:
        from db.expense import _auto_fill_profile
        _auto_fill_profile(user_id)
    except Exception:
        logger.exception("账单已支付，回填用户画像失败")
    return expense_id


def get_recurring_summary(month, user_id="default", *, conn=None) -> dict:
    month = _month(month)
    with _read_snapshot(conn) as conn:
        bills = list_recurring_bills(month, user_id=user_id, conn=conn)
        templates = [item for item in list_recurring_templates(user_id, conn=conn) if _in_range(item, month)]
    paid_template_ids = {item["template_id"] for item in bills if item["status"] == "paid"}
    pending = [bill for bill in bills if bill["status"] == "pending"]
    paid = [bill for bill in bills if bill["status"] == "paid"]
    by_category = {}
    for bill in bills:
        bucket = by_category.setdefault(bill["category"], {"category": bill["category"], "paid": 0.0, "pending": 0.0, "total": 0.0})
        bucket[bill["status"]] += bill["amount"]
        bucket["total"] += bill["amount"]
    today = _today().isoformat()
    return {
        "month": month,
        "pending_total": round(sum(item["amount"] for item in pending), 2),
        "paid_total": round(sum(item["amount"] for item in bills if item["status"] == "paid"), 2),
        "recurring_paid": round(sum(item["amount"] for item in paid), 2),
        "recurring_pending": round(sum(item["amount"] for item in pending), 2),
        "recurring_due_total": round(sum(item["amount"] for item in bills), 2),
        # 生效中的模板数与本月到期账单数不同：季度/年度账单在非到期月
        # 仍属于家庭固定负担，但不会生成本月应付项。
        "recurring_count": len(templates),
        "recurring_bills": bills,
        "recurring_by_category": [{key: round(value, 2) if key != "category" else value for key, value in item.items()} for item in by_category.values()],
        "monthly_provision": round(sum(item["amount"] / INTERVALS[item["frequency"]]
                                       for item in templates), 2),
        "future_provision": round(sum(item["amount"] / INTERVALS[item["frequency"]]
                                      for item in templates if not _is_due(item, month)
                                      and item["id"] not in paid_template_ids), 2),
        "pending_debt_payment": round(sum(item["amount"] for item in pending
                                           if item["category"] in DEBT_CATEGORIES), 2),
        "debt_payment": round(sum(item["amount"] for item in bills
                                  if item["category"] in DEBT_CATEGORIES), 2),
        "overdue_count": sum(item["due_date"] < today for item in pending),
        "upcoming_count": sum(item["due_date"] >= today for item in pending),
    }
