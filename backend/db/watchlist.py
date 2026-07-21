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
        # P0-1（2026-07-21）多维信号字段
        'signal_confidence', 'tech_signal', 'capital_signal', 'sentiment_signal',
        # P0-3（2026-07-21）信号回测闭环
        'last_signal_status',
        # P1-C（2026-07-21）退出信号动态化
        'high_water_mark',
        # P1-D（2026-07-21）信号有效期
        'signal_triggered_at',
        # P2-A（2026-07-21）回测闭环深化：自适应阈值
        'adaptive_target_pct',
        'adaptive_reason',
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


# ── O-4（2026-07-21）：watchlist current_percentile fallback ──
# 场景：无 index_code 的关注基金（如 020256/004253/021608/005661）current_percentile=None
# 修复：fallback 1 从 fund_metadata 查 tracking_index；fallback 2 用基金净值回撤估算


def refresh_watchlist_percentile(user_id: str = "default") -> dict:
    """O-4: 刷新关注列表 current_percentile，对无 index_code 的基金走 fallback。

    Returns:
        {"processed": N, "updated": M, "skipped": K, "details": [...]}
    """
    try:
        from db.config import get_config_bool
        fallback_enabled = get_config_bool("watchlist.fallback_percentile_enabled", True)
    except Exception:
        fallback_enabled = True

    items = list_watchlist(user_id)
    processed = len(items)
    updated = 0
    skipped = 0
    details = []

    for item in items:
        fund_code = item["fund_code"]
        try:
            pct = _resolve_watchlist_percentile(item, fallback_enabled)
            if pct is not None:
                update_watchlist_item(item["id"], current_percentile=pct)
                updated += 1
                details.append({"fund_code": fund_code, "percentile": pct})
            else:
                skipped += 1
                details.append({"fund_code": fund_code, "skipped": True})
        except Exception as e:
            skipped += 1
            details.append({"fund_code": fund_code, "error": str(e)})

    logger.info(f"[watchlist] refresh_percentile: processed={processed}, updated={updated}, skipped={skipped}")
    return {"processed": processed, "updated": updated, "skipped": skipped, "details": details}


def _resolve_watchlist_percentile(item: dict, fallback_enabled: bool = True) -> float | None:
    """解析单条 watchlist 项的 current_percentile，支持多级 fallback。

    Args:
        item: watchlist dict
        fallback_enabled: 是否启用 fallback（受 watchlist.fallback_percentile_enabled 开关控制）
    Returns:
        percentile [0, 100] 或 None
    """
    fund_code = item.get("fund_code")
    if not fund_code:
        return None

    # 1. 直接用 item 的 index_code 查本地估值
    index_code = item.get("index_code")
    if index_code:
        pct = _query_percentile_from_valuation(index_code)
        if pct is not None:
            return pct

    # 2. fallback 1: 从 fund_metadata 查 tracking_index
    if fallback_enabled:
        try:
            from db import lookup_fund_info
            fm = lookup_fund_info(fund_code)
            if fm:
                tracking_index = fm.get("tracking_index") or fm.get("index_code")
                if tracking_index:
                    pct = _query_percentile_from_valuation(tracking_index)
                    if pct is not None:
                        return pct
        except Exception as e:
            logger.debug(f"[watchlist] lookup_fund_info fallback 失败 {fund_code}: {e}")

        # 3. fallback 2: 用基金净值回撤估算「相对分位」
        # 简单线性映射：drawdown 0% → 100；drawdown -50% → 0
        try:
            from services.fund_data_service import get_fund_nav_history_from_cache
            navs = get_fund_nav_history_from_cache(fund_code, days=365)
            if navs and len(navs) > 30:
                valid = [n for n in navs if n.get("nav") and n["nav"] > 0]
                if len(valid) > 30:
                    current = valid[-1]["nav"]
                    max_nav = max(n["nav"] for n in valid)
                    if max_nav > 0:
                        drawdown = (current - max_nav) / max_nav  # 负数
                        # 线性映射：drawdown=0 → 100；drawdown=-0.5 → 0
                        pct = max(0.0, min(100.0, 100 + drawdown * 200))
                        return round(pct, 2)
        except Exception as e:
            logger.debug(f"[watchlist] nav_history fallback 失败 {fund_code}: {e}")

    return None


def _query_percentile_from_valuation(index_code: str) -> float | None:
    """从本地估值表查 percentile，尝试带/不带 .CSI 后缀。"""
    if not index_code:
        return None
    try:
        from db.valuations import get_latest_valuation
        candidates = [index_code]
        if "." not in index_code:
            candidates.append(f"{index_code}.CSI")
        else:
            # 同时尝试去后缀形式
            candidates.append(index_code.split(".")[0])
        for code in candidates:
            try:
                v = get_latest_valuation(code)
                if v and v.get("percentile") is not None:
                    return float(v["percentile"])
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"[watchlist] _query_percentile_from_valuation 失败 {index_code}: {e}")
    return None


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


# ── P0-3（2026-07-21）关注列表信号回测 CRUD ─────────────────────────────────


def create_signal_backtest(item: dict) -> int:
    """插入一条 watchlist 信号回测记录，返回 id。

    必填字段：watchlist_id, fund_code, signal_date, signal_status, review_date
    可选字段：entry_nav, entry_percentile, signal_confidence, multidim_snapshot, fund_name
    """
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO watchlist_signal_backtests
                (watchlist_id, fund_code, fund_name, signal_date, signal_status,
                 entry_nav, entry_percentile, review_date, signal_confidence, multidim_snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["watchlist_id"],
            item["fund_code"],
            item.get("fund_name", ""),
            item["signal_date"],
            item["signal_status"],
            item.get("entry_nav"),
            item.get("entry_percentile"),
            item["review_date"],
            item.get("signal_confidence"),
            item.get("multidim_snapshot"),
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def has_signal_backtest_on_date(watchlist_id: int, signal_date: str) -> bool:
    """判断某 watchlist 项在某日是否已插入过回测记录（避免重复插入）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM watchlist_signal_backtests WHERE watchlist_id = ? AND signal_date = ? LIMIT 1",
            (watchlist_id, signal_date),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_pending_signal_backtests() -> list[dict]:
    """列出所有已到期但未回测的记录（review_date <= today AND hit IS NULL）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM watchlist_signal_backtests
               WHERE hit IS NULL AND review_date <= date('now','localtime')
               ORDER BY review_date ASC LIMIT 200""",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_signal_backtest(bt_id: int, **fields) -> bool:
    """更新回测记录字段。允许字段：review_nav, change_pct, hit, reviewed_at。"""
    allowed = {"review_nav", "change_pct", "hit", "reviewed_at"}
    invalid = set(fields.keys()) - allowed
    if invalid:
        raise ValueError(f"非法字段名: {invalid}")
    if not fields:
        return False
    sets = [f"{k} = ?" for k in fields]
    params = list(fields.values()) + [bt_id]
    conn = _get_conn()
    try:
        conn.execute(
            f"UPDATE watchlist_signal_backtests SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_signal_backtest_stats(fund_code: str = None) -> dict:
    """获取信号回测命中率统计。

    Args:
        fund_code: 指定基金代码则返回该基金的统计，None 则返回全局统计

    Returns:
        {
            "total": int,         # 总记录数
            "reviewed": int,      # 已回测数
            "pending": int,       # 待回测数
            "hit": int,           # 命中数
            "miss": int,          # 未命中数
            "hit_rate": float,    # 命中率（0-100），已回测数为 0 时返回 None
            # P1-B（2026-07-21）分桶统计
            "by_confidence": {    # 按 signal_confidence 分桶
                "high": {"total": int, "hit": int, "hit_rate": float},  # >= 70
                "mid": {"total": int, "hit": int, "hit_rate": float},   # 50-70
                "low": {"total": int, "hit": int, "hit_rate": float},   # < 50
            },
            "by_tech": {          # 按 tech_signal 分桶(从 multidim_snapshot 解析)
                "bull": {...}, "neutral": {...}, "bear": {...},
            },
            "by_capital": {       # 按 capital_signal 分桶
                "inflow": {...}, "neutral": {...}, "outflow": {...},
            },
            "recent_30d": {       # 近 30 天统计
                "total": int, "reviewed": int, "hit": int, "hit_rate": float,
            },
        }
    """
    conn = _get_conn()
    try:
        where = "WHERE fund_code = ?" if fund_code else ""
        params = [fund_code] if fund_code else []
        row = conn.execute(
            f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) AS reviewed,
                SUM(CASE WHEN hit IS NULL THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) AS hit,
                SUM(CASE WHEN hit = 0 THEN 1 ELSE 0 END) AS miss
                FROM watchlist_signal_backtests {where}""",
            params,
        ).fetchone()
        if not row:
            return _empty_stats()
        total = int(row["total"] or 0)
        reviewed = int(row["reviewed"] or 0)
        pending = int(row["pending"] or 0)
        hit = int(row["hit"] or 0)
        miss = int(row["miss"] or 0)
        hit_rate = round(hit / reviewed * 100, 1) if reviewed > 0 else None

        # ── P1-B 分桶统计 ───────────────────────────────────────────
        import json as _json

        # 按 confidence 分桶
        by_confidence = {"high": {"total": 0, "hit": 0, "hit_rate": None},
                         "mid": {"total": 0, "hit": 0, "hit_rate": None},
                         "low": {"total": 0, "hit": 0, "hit_rate": None}}
        # 按 multidim_snapshot.tech / capital 分桶
        by_tech = {k: {"total": 0, "hit": 0, "hit_rate": None} for k in ("bull", "neutral", "bear")}
        by_capital = {k: {"total": 0, "hit": 0, "hit_rate": None} for k in ("inflow", "neutral", "outflow")}

        rows = conn.execute(
            f"""SELECT signal_confidence, multidim_snapshot, hit
                FROM watchlist_signal_backtests {where}""",
            params,
        ).fetchall()
        for r in rows:
            conf = r["signal_confidence"]
            hit_val = r["hit"]
            # confidence 桶
            if conf is not None:
                if conf >= 70:
                    bucket = "high"
                elif conf >= 50:
                    bucket = "mid"
                else:
                    bucket = "low"
                by_confidence[bucket]["total"] += 1
                if hit_val == 1:
                    by_confidence[bucket]["hit"] += 1
            # multidim_snapshot 桶
            snap = r["multidim_snapshot"]
            if snap:
                try:
                    snap_data = _json.loads(snap) if isinstance(snap, str) else snap
                    tech_v = snap_data.get("tech", "neutral")
                    cap_v = snap_data.get("capital", "neutral")
                    if tech_v in by_tech:
                        by_tech[tech_v]["total"] += 1
                        if hit_val == 1:
                            by_tech[tech_v]["hit"] += 1
                    if cap_v in by_capital:
                        by_capital[cap_v]["total"] += 1
                        if hit_val == 1:
                            by_capital[cap_v]["hit"] += 1
                except Exception:
                    pass

        # 计算各桶命中率
        for bucket_dict in (by_confidence, by_tech, by_capital):
            for k, v in bucket_dict.items():
                if v["total"] > 0:
                    v["hit_rate"] = round(v["hit"] / v["total"] * 100, 1)

        # 近 30 天统计
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        if where:
            recent_sql = f"SELECT COUNT(*) AS total, SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) AS reviewed, SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) AS hit FROM watchlist_signal_backtests {where} AND signal_date >= ?"
            recent_params = params + [cutoff]
        else:
            recent_sql = "SELECT COUNT(*) AS total, SUM(CASE WHEN hit IS NOT NULL THEN 1 ELSE 0 END) AS reviewed, SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) AS hit FROM watchlist_signal_backtests WHERE signal_date >= ?"
            recent_params = [cutoff]
        recent_row = conn.execute(recent_sql, recent_params).fetchone()
        recent_total = int(recent_row["total"] or 0) if recent_row else 0
        recent_reviewed = int(recent_row["reviewed"] or 0) if recent_row else 0
        recent_hit = int(recent_row["hit"] or 0) if recent_row else 0
        recent_30d = {
            "total": recent_total,
            "reviewed": recent_reviewed,
            "hit": recent_hit,
            "hit_rate": round(recent_hit / recent_reviewed * 100, 1) if recent_reviewed > 0 else None,
        }

        return {
            "total": total,
            "reviewed": reviewed,
            "pending": pending,
            "hit": hit,
            "miss": miss,
            "hit_rate": hit_rate,
            "by_confidence": by_confidence,
            "by_tech": by_tech,
            "by_capital": by_capital,
            "recent_30d": recent_30d,
        }
    finally:
        conn.close()


def _empty_stats() -> dict:
    """空统计结构(P1-B)。"""
    empty_bucket = lambda: {"total": 0, "hit": 0, "hit_rate": None}
    return {
        "total": 0, "reviewed": 0, "pending": 0, "hit": 0, "miss": 0, "hit_rate": None,
        "by_confidence": {k: empty_bucket() for k in ("high", "mid", "low")},
        "by_tech": {k: empty_bucket() for k in ("bull", "neutral", "bear")},
        "by_capital": {k: empty_bucket() for k in ("inflow", "neutral", "outflow")},
        "recent_30d": {"total": 0, "reviewed": 0, "hit": 0, "hit_rate": None},
    }


def get_fund_signal_backtest_history(fund_code: str, limit: int = 20) -> list[dict]:
    """获取某基金的信号回测历史（按 signal_date 降序）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM watchlist_signal_backtests
               WHERE fund_code = ?
               ORDER BY signal_date DESC LIMIT ?""",
            (fund_code, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
