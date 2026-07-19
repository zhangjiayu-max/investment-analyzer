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
    """获取单条关注记录。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM watchlist WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_watchlist_by_fund(fund_code: str, user_id: str = "default") -> dict | None:
    """根据基金代码查询关注记录。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM watchlist WHERE fund_code = ? AND user_id = ?",
        (fund_code, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_watchlist(user_id: str = "default", status: str = None,
                   category: str = None) -> list[dict]:
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


def update_watchlist_item(item_id: int, **fields) -> bool:
    """更新关注记录字段。"""
    allowed = {
        'fund_name', 'fund_category', 'index_code', 'index_name',
        'target_price', 'target_percentile', 'notes', 'priority', 'status',
        'current_nav', 'current_percentile', 'nav_updated_at',
        'suggested_buy_price', 'buy_price_source',
        'analysis_method', 'drawdown_percentile', 'nav_percentile',
        # Batch1 增强点 1：退出机制字段
        'target_profit_pct', 'stop_loss_pct', 'entry_price', 'entry_date',
        'exit_signal', 'exit_signal_reason',
        # Batch1 增强点 2：异常波动预警字段
        'daily_change_pct', 'weekly_change_pct',
        'volatility_alert', 'volatility_alert_reason', 'volatility_updated_at',
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


def remove_from_watchlist(item_id: int) -> bool:
    """从关注列表移除。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def batch_add_to_watchlist(items: list[dict], user_id: str = "default") -> dict:
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


# ── Batch1 增强点 1：退出机制（止盈/止损）─────────────────────────────────

def update_entry_info(item_id: int, entry_price: float = None, entry_date: str = None,
                     target_profit_pct: float = None, stop_loss_pct: float = None) -> bool:
    """用户上车后更新买入信息（买入价/日期/止盈/止损）。

    所有参数可选，只更新传入的字段。同时把 status 改为 'bought'。
    """
    fields = {"status": "bought"}
    if entry_price is not None:
        fields["entry_price"] = float(entry_price)
    if entry_date is not None:
        fields["entry_date"] = entry_date
    if target_profit_pct is not None:
        fields["target_profit_pct"] = float(target_profit_pct)
    if stop_loss_pct is not None:
        fields["stop_loss_pct"] = float(stop_loss_pct)
    # 上车时清空旧的退出信号
    fields["exit_signal"] = "none"
    fields["exit_signal_reason"] = ""
    return update_watchlist_item(item_id, **fields)


def get_watchlist_with_exit_status(item_id: int) -> dict | None:
    """查询单条关注记录，附带当前盈亏百分比和退出信号状态。

    Returns:
        {
            ...原 watchlist 字段,
            pnl_pct: 当前盈亏百分比（current_nav 相对 entry_price）,
            exit_signal: profit_target / stop_loss / none,
            exit_signal_reason: 触发原因,
        }
    """
    item = get_watchlist_item(item_id)
    if not item:
        return None

    entry_price = item.get("entry_price")
    current_nav = item.get("current_nav")
    if entry_price and current_nav and entry_price > 0:
        pnl_pct = (current_nav - entry_price) / entry_price * 100
        item["pnl_pct"] = round(pnl_pct, 2)

        target_profit = item.get("target_profit_pct")
        stop_loss = item.get("stop_loss_pct")
        exit_signal = "none"
        exit_reason = ""
        if target_profit and pnl_pct >= target_profit:
            exit_signal = "profit_target"
            exit_reason = f"已涨 {pnl_pct:.1f}%，达到止盈目标 {target_profit}%"
        elif stop_loss and pnl_pct <= -stop_loss:
            exit_signal = "stop_loss"
            exit_reason = f"已跌 {abs(pnl_pct):.1f}%，触发止损 -{stop_loss}%"
        item["exit_signal"] = exit_signal
        item["exit_signal_reason"] = exit_reason
    else:
        item["pnl_pct"] = None
        item["exit_signal"] = item.get("exit_signal") or "none"
        item["exit_signal_reason"] = item.get("exit_signal_reason") or ""

    return item


# ── Batch2 增强点 1：关注计划自动剔除已上车 ─────────────────────────────────

def auto_mark_bought_on_trade(fund_code: str, entry_price: float,
                              entry_date: str, user_id: str = "default") -> int:
    """当 portfolio 中买入某基金时，自动把 watchlist 中同 fund_code 的 watching 项标为 bought。

    用于解决用户在持仓中买入关注基金后，watchlist 仍停留 watching 状态导致巡检继续生成 green 信号的问题。

    Args:
        fund_code: 基金代码
        entry_price: 买入价
        entry_date: 买入日期（YYYY-MM-DD）
        user_id: 用户 ID，默认 'default'

    Returns:
        受影响的行数（0 表示无匹配项）
    """
    conn = _get_conn()
    try:
        cursor = conn.execute("""
            UPDATE watchlist
            SET status = 'bought',
                entry_price = ?,
                entry_date = ?,
                exit_signal = 'none',
                exit_signal_reason = '',
                updated_at = datetime('now','localtime')
            WHERE fund_code = ? AND user_id = ? AND status = 'watching'
        """, (float(entry_price) if entry_price else None, entry_date, fund_code, user_id))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
