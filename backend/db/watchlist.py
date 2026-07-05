"""关注列表 CRUD — 看好但未持有的基金，方便择机买入。"""

import json
import logging
from datetime import datetime

from db._conn import _get_conn, _row_to_dict

logger = logging.getLogger(__name__)


def add_to_watchlist(fund_code: str, fund_name: str,
                      fund_category: str = None, index_code: str = None,
                      index_name: str = None, target_price: float = None,
                      target_percentile: float = None, notes: str = None,
                      priority: int = 0, user_id: str = "default") -> int:
    """添加基金到关注列表。返回 id。"""
    if fund_category is None:
        from db.portfolio import classify_fund_category
        fund_category = classify_fund_category(fund_name, fund_code=fund_code)

    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO watchlist
                (user_id, fund_code, fund_name, fund_category, index_code, index_name,
                 target_price, target_percentile, notes, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, fund_code, fund_name, fund_category, index_code, index_name,
              target_price, target_percentile, notes, priority))
        watchlist_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        logger.error(f"添加关注列表失败: {e}")
        raise
    finally:
        conn.close()

    return watchlist_id


def get_watchlist_item(item_id: int) -> dict | None:
    try:
        """获取单条关注记录。"""
        conn = _get_conn()
        row = conn.execute("SELECT * FROM watchlist WHERE id = ?", (item_id,)).fetchone()
        conn.close()
        return dict(row) if row else None
    finally:
        conn.close()


def get_watchlist_by_fund(fund_code: str, user_id: str = "default") -> dict | None:
    try:
        """根据基金代码查询关注记录。"""
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM watchlist WHERE fund_code = ? AND user_id = ?",
            (fund_code, user_id),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    finally:
        conn.close()


def list_watchlist(user_id: str = "default", status: str = None,
                   category: str = None) -> list[dict]:
    try:
        """获取关注列表。可选按 status/category 筛选。"""
        conn = _get_conn()
        conditions = ["user_id = ?"]
        params: list = [user_id]
        if status:
            conditions.append("status = ?")
            params.append(status)
        if category:
            conditions.append("fund_category = ?")
            params.append(category)

        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT * FROM watchlist WHERE {where} ORDER BY priority DESC, created_at DESC",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_watchlist_item(item_id: int, **fields) -> bool:
    try:
        """更新关注记录字段。"""
        allowed = {
            'fund_name', 'fund_category', 'index_code', 'index_name',
            'target_price', 'target_percentile', 'notes', 'priority', 'status',
            'current_nav', 'current_percentile', 'nav_updated_at',
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"非法字段名: {invalid}")
        if not fields:
            return False

        fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [item_id]

        conn = _get_conn()
        cur = conn.execute(f"UPDATE watchlist SET {set_clause} WHERE id = ?", values)
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok
    finally:
        conn.close()


def remove_from_watchlist(item_id: int) -> bool:
    try:
        """从关注列表移除。"""
        conn = _get_conn()
        cur = conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok
    finally:
        conn.close()


def batch_add_to_watchlist(items: list[dict], user_id: str = "default") -> dict:
    try:
        """批量添加基金到关注列表。返回成功/失败统计。"""
        from db.portfolio import classify_fund_category
        conn = _get_conn()
        added = 0
        skipped = 0
        errors = []

        for item in items:
            fund_code = item.get("fund_code", "").strip()
            fund_name = item.get("fund_name", "").strip()
            if not fund_code or not fund_name:
                errors.append(f"缺少基金代码或名称: {item}")
                continue

            # 查重：已在关注列表或已持仓
            existing = conn.execute(
                "SELECT id FROM watchlist WHERE user_id = ? AND fund_code = ?",
                (user_id, fund_code),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            # 查重：已在持仓中
            held = conn.execute(
                "SELECT id FROM portfolio_holdings WHERE user_id = ? AND fund_code = ?",
                (user_id, fund_code),
            ).fetchone()
            if held:
                skipped += 1
                errors.append(f"{fund_name}({fund_code}) 已在持仓中")
                continue

            try:
                cat = item.get("fund_category") or classify_fund_category(fund_name, fund_code=fund_code)
                conn.execute("""
                    INSERT INTO watchlist
                        (user_id, fund_code, fund_name, fund_category, index_code, index_name,
                         target_price, target_percentile, notes, priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id, fund_code, fund_name, cat,
                    item.get("index_code"), item.get("index_name"),
                    item.get("target_price"), item.get("target_percentile"),
                    item.get("notes"), item.get("priority", 0),
                ))
                added += 1
            except Exception as e:
                errors.append(f"{fund_name}({fund_code}): {e}")

        conn.commit()
        conn.close()
        return {"added": added, "skipped": skipped, "errors": errors}
    finally:
        conn.close()


def refresh_watchlist_navs(user_id: str = "default") -> list[dict]:
    """批量刷新关注列表基金净值。返回每只基金的更新结果。"""
    from db.portfolio import fetch_fund_nav
    items = list_watchlist(user_id)
    results = []

    for item in items:
        fund_code = item["fund_code"]
        try:
            nav_data = fetch_fund_nav(fund_code)
            if nav_data:
                nav = nav_data.get("nav")
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                update_watchlist_item(
                    item["id"],
                    current_nav=nav,
                    nav_updated_at=now,
                )
                results.append({
                    "fund_code": fund_code,
                    "fund_name": item.get("fund_name", ""),
                    "nav": nav,
                    "date": nav_data.get("date", ""),
                    "change_pct": nav_data.get("change_pct"),
                })
            else:
                results.append({
                    "fund_code": fund_code,
                    "fund_name": item.get("fund_name", ""),
                    "error": "净值获取失败",
                })
        except Exception as e:
            results.append({
                "fund_code": fund_code,
                "fund_name": item.get("fund_name", ""),
                "error": str(e),
            })

    return results


def get_watchlist_summary(user_id: str = "default") -> dict:
    """获取关注列表统计。"""
    items = list_watchlist(user_id)
    watching = [i for i in items if i.get("status") != "bought"]
    bought = [i for i in items if i.get("status") == "bought"]
    category_count = {}
    for i in items:
        cat = i.get("fund_category") or "other"
        category_count[cat] = category_count.get(cat, 0) + 1
    return {
        "total": len(items),
        "watching": len(watching),
        "bought": len(bought),
        "category_count": category_count,
    }
