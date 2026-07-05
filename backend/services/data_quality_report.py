"""
数据缺口分析服务 — 纯 SQL 查询，零 LLM 调用。

检查数据库中各核心表的字段完整性和时效性，找出"数据缺失导致 AI 分析不准"的场景。

核心函数:
  - data_gap_analysis()        → 各表字段 NULL 率 + 数据缺口
  - data_freshness_report()    → 各数据源新鲜度状态
  - data_consistency_check()   → 跨表一致性校验
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent.parent.parent / "data" / "valuations.db"

# 新鲜度阈值（小时）
FRESH_OK_HOURS = 6       # < 6h → ok
FRESH_STALE_HOURS = 24   # 6-24h → stale, >24h → expired

# 估值数据缺口阈值（天）
VALUATION_GAP_DAYS = 5

# 不存在的表跳过列表（运行时动态检测）
_WARNINGS: list[str] = []


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """获取 SQLite 连接（row_factory 启用）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """检查表是否存在"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row is not None


def _parse_dt(date_str: str | None) -> datetime | None:
    """解析各种日期格式"""
    if not date_str:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _hours_since(date_str: str | None) -> float | None:
    """计算距今多少小时"""
    dt = _parse_dt(date_str)
    if dt is None:
        return None
    delta = datetime.now() - dt
    return round(delta.total_seconds() / 3600, 1)


def _days_since(date_str: str | None) -> float | None:
    """计算距今多少天"""
    hrs = _hours_since(date_str)
    return round(hrs / 24, 1) if hrs is not None else None


def _is_trading_day(date_str: str | None) -> bool:
    """判断给定日期是否为交易日（周一至周五，排除周末）。
    简单版：不包含节假日判断，仅排除周末。
    """
    if not date_str:
        return False
    dt = _parse_dt(date_str)
    if dt is None:
        return False
    # weekday(): 0=Mon, 5=Sat, 6=Sun
    return dt.weekday() < 5


def _trading_hours_since(date_str: str | None, is_market_data: bool = False) -> float | None:
    """计算距今多少小时。如果是市场数据且最新日期是周五/周末，
    扣除周末非交易时间，避免误报 expired。
    """
    raw_hours = _hours_since(date_str)
    if raw_hours is None:
        return None
    if not is_market_data:
        return raw_hours

    dt = _parse_dt(date_str)
    if dt is None:
        return raw_hours

    now = datetime.now()
    # 如果最新数据是周五，且现在还是周末，只算到周一开盘（9:30）
    if dt.weekday() == 4:  # Friday
        # 下一个交易日是周一
        next_trading = dt + timedelta(days=3)  # Monday
        next_trading = next_trading.replace(hour=9, minute=30, second=0, microsecond=0)
        if now > next_trading:
            # 周一开盘后了，从周一开始算
            return round((now - next_trading).total_seconds() / 3600, 1)
        else:
            # 还在周末，数据是新鲜的
            return 0.0
    elif dt.weekday() >= 5:  # Saturday or Sunday (shouldn't happen for market data)
        next_trading = dt + timedelta(days=(7 - dt.weekday()))  # Next Monday
        next_trading = next_trading.replace(hour=9, minute=30, second=0, microsecond=0)
        if now > next_trading:
            return round((now - next_trading).total_seconds() / 3600, 1)
        else:
            return 0.0

    return raw_hours


def _freshness_status(age_hours: float | None) -> str:
    """根据 age_hours 返回状态标签"""
    if age_hours is None:
        return "unknown"
    if age_hours < FRESH_OK_HOURS:
        return "ok"
    if age_hours < FRESH_STALE_HOURS:
        return "stale"
    return "expired"


# ---------------------------------------------------------------------------
# 1. 数据缺口分析
# ---------------------------------------------------------------------------

def data_gap_analysis() -> list[dict]:
    """
    检查各核心表的字段 NULL 率和数据缺口问题。

    返回 list[dict]，每个 dict 描述一个数据缺口问题：
      - table: 表名
      - field: 字段名（或 "overall"）
      - issue: 问题描述
      - null_count: NULL 数量
      - total_count: 总记录数
      - null_rate: NULL 率 (0-1)
      - severity: critical / warning / info
    """
    global _WARNINGS
    _WARNINGS = []
    conn = _get_conn()
    results: list[dict] = []

    # ---- 1.1 portfolio_holdings: 关键字段 NULL 率 ----
    if _table_exists(conn, "portfolio_holdings"):
        total = conn.execute("SELECT COUNT(*) FROM portfolio_holdings").fetchone()[0]
        if total > 0:
            # 检查每个关键字段
            key_fields = [
                "fund_category",
                "index_name",
                "index_code",
                "cost_price",
                "current_price",
                "current_value",
                "profit_loss",
                "profit_rate",
                "price_updated_at",
                "buy_date",
                "fund_name",
                "shares",
                "manager_name",
            ]
            for field in key_fields:
                null_count = conn.execute(
                    f"SELECT COUNT(*) FROM portfolio_holdings WHERE `{field}` IS NULL OR `{field}` = ''"
                ).fetchone()[0]
                if null_count > 0:
                    null_rate = round(null_count / total, 3)
                    severity = (
                        "critical" if null_rate > 0.5
                        else "warning" if null_rate > 0.2
                        else "info"
                    )
                    results.append({
                        "table": "portfolio_holdings",
                        "field": field,
                        "issue": f"字段 {field} 有 {null_count}/{total} 条 NULL 或空值",
                        "null_count": null_count,
                        "total_count": total,
                        "null_rate": null_rate,
                        "severity": severity,
                    })

            # 检查 cost_price = 0 的记录（可能未录入成本）
            zero_cost = conn.execute(
                "SELECT COUNT(*) FROM portfolio_holdings WHERE cost_price = 0 OR cost_price IS NULL"
            ).fetchone()[0]
            if zero_cost > 0:
                results.append({
                    "table": "portfolio_holdings",
                    "field": "cost_price",
                    "issue": f"有 {zero_cost} 条记录 cost_price 为 0 或 NULL，无法计算盈亏",
                    "null_count": zero_cost,
                    "total_count": total,
                    "null_rate": round(zero_cost / total, 3),
                    "severity": "critical" if zero_cost / total > 0.3 else "warning",
                })

            # 检查 current_price 为 NULL 但持仓存在的记录
            no_price = conn.execute(
                "SELECT COUNT(*) FROM portfolio_holdings WHERE (current_price IS NULL OR current_price = 0) AND shares > 0"
            ).fetchone()[0]
            if no_price > 0:
                results.append({
                    "table": "portfolio_holdings",
                    "field": "current_price",
                    "issue": f"有 {no_price} 条持仓记录无当前价格，无法计算市值",
                    "null_count": no_price,
                    "total_count": total,
                    "null_rate": round(no_price / total, 3),
                    "severity": "critical",
                })

            # 检查 index_code 为空但 fund_category 需要关联指数的
            no_index = conn.execute(
                "SELECT COUNT(*) FROM portfolio_holdings WHERE (index_code IS NULL OR index_code = '') AND fund_category != '货币基金' AND fund_category != '债券基金'"
            ).fetchone()[0]
            if no_index > 0:
                results.append({
                    "table": "portfolio_holdings",
                    "field": "index_code",
                    "issue": f"有 {no_index} 条非货币/债券基金记录缺少关联指数，影响估值分析",
                    "null_count": no_index,
                    "total_count": total,
                    "null_rate": round(no_index / total, 3),
                    "severity": "warning",
                })
        else:
            results.append({
                "table": "portfolio_holdings",
                "field": "overall",
                "issue": "表为空，无持仓数据",
                "null_count": 0,
                "total_count": 0,
                "null_rate": 1.0,
                "severity": "critical",
            })
    else:
        _WARNINGS.append("portfolio_holdings 表不存在，跳过检查")

    # ---- 1.2 index_valuations: 估值数据时效性与缺口 ----
    if _table_exists(conn, "index_valuations"):
        total = conn.execute("SELECT COUNT(*) FROM index_valuations").fetchone()[0]
        if total > 0:
            latest = conn.execute(
                "SELECT MAX(snapshot_date) FROM index_valuations"
            ).fetchone()[0]
            days = _days_since(latest)
            if days is not None and days > VALUATION_GAP_DAYS:
                results.append({
                    "table": "index_valuations",
                    "field": "snapshot_date",
                    "issue": f"最新估值日期为 {latest}，距今 {days} 天，超过 {VALUATION_GAP_DAYS} 天阈值",
                    "null_count": 0,
                    "total_count": total,
                    "null_rate": 0.0,
                    "severity": "critical" if days > 10 else "warning",
                })

            # 检查各指数的最新估值日期是否一致
            index_dates = conn.execute(
                """SELECT index_code, index_name, MAX(snapshot_date) as latest_date,
                          COUNT(*) as cnt
                   FROM index_valuations
                   GROUP BY index_code, index_name
                   ORDER BY latest_date ASC"""
            ).fetchall()
            if index_dates:
                newest_date = index_dates[-1]["latest_date"]
                stale_indexes = [
                    r for r in index_dates
                    if r["latest_date"] != newest_date
                ]
                if stale_indexes:
                    stale_list = [
                        f"{r['index_name'] or r['index_code']}({r['latest_date']})"
                        for r in stale_indexes
                    ]
                    results.append({
                        "table": "index_valuations",
                        "field": "snapshot_date",
                        "issue": f"有 {len(stale_indexes)} 个指数的估值日期落后于最新日期 {newest_date}：{', '.join(stale_list[:5])}",
                        "null_count": len(stale_indexes),
                        "total_count": len(index_dates),
                        "null_rate": round(len(stale_indexes) / len(index_dates), 3),
                        "severity": "warning",
                    })

            # 检查 percentile 为 NULL 的记录
            null_pct = conn.execute(
                "SELECT COUNT(*) FROM index_valuations WHERE percentile IS NULL"
            ).fetchone()[0]
            if null_pct > 0:
                results.append({
                    "table": "index_valuations",
                    "field": "percentile",
                    "issue": f"有 {null_pct} 条估值记录缺少百分位数据",
                    "null_count": null_pct,
                    "total_count": total,
                    "null_rate": round(null_pct / total, 3),
                    "severity": "warning",
                })
        else:
            results.append({
                "table": "index_valuations",
                "field": "overall",
                "issue": "表为空，无估值数据",
                "null_count": 0,
                "total_count": 0,
                "null_rate": 1.0,
                "severity": "critical",
            })
    else:
        _WARNINGS.append("index_valuations 表不存在，跳过检查")

    # ---- 1.3 fund_nav_history: 各基金最新净值日期 ----
    if _table_exists(conn, "fund_nav_history"):
        total = conn.execute("SELECT COUNT(*) FROM fund_nav_history").fetchone()[0]
        if total > 0:
            # 各基金最新净值日期
            fund_nav = conn.execute(
                """SELECT fund_code, MAX(nav_date) as latest_date, COUNT(*) as cnt
                   FROM fund_nav_history
                   GROUP BY fund_code
                   ORDER BY latest_date ASC"""
            ).fetchall()

            # 找出净值数据过期的基金（>5天）
            stale_funds = []
            for r in fund_nav:
                days = _days_since(r["latest_date"])
                if days is not None and days > VALUATION_GAP_DAYS:
                    stale_funds.append({
                        "fund_code": r["fund_code"],
                        "latest_date": r["latest_date"],
                        "days_behind": days,
                        "record_count": r["cnt"],
                    })

            if stale_funds:
                results.append({
                    "table": "fund_nav_history",
                    "field": "nav_date",
                    "issue": f"有 {len(stale_funds)} 只基金净值数据过期（>{VALUATION_GAP_DAYS}天）",
                    "null_count": len(stale_funds),
                    "total_count": len(fund_nav),
                    "null_rate": round(len(stale_funds) / max(len(fund_nav), 1), 3),
                    "severity": "critical" if len(stale_funds) > len(fund_nav) * 0.3 else "warning",
                    "details": stale_funds[:10],
                })

            # 检查 nav 为 NULL 的记录
            null_nav = conn.execute(
                "SELECT COUNT(*) FROM fund_nav_history WHERE nav IS NULL OR nav = 0"
            ).fetchone()[0]
            if null_nav > 0:
                results.append({
                    "table": "fund_nav_history",
                    "field": "nav",
                    "issue": f"有 {null_nav} 条净值记录的 nav 为 NULL 或 0",
                    "null_count": null_nav,
                    "total_count": total,
                    "null_rate": round(null_nav / total, 3),
                    "severity": "warning",
                })
        else:
            results.append({
                "table": "fund_nav_history",
                "field": "overall",
                "issue": "表为空，无净值历史数据",
                "null_count": 0,
                "total_count": 0,
                "null_rate": 1.0,
                "severity": "critical",
            })
    else:
        _WARNINGS.append("fund_nav_history 表不存在，跳过检查")

    # ---- 1.4 portfolio_transactions: 缺少 price 或 shares ----
    if _table_exists(conn, "portfolio_transactions"):
        total = conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0]
        if total > 0:
            null_price = conn.execute(
                "SELECT COUNT(*) FROM portfolio_transactions WHERE price IS NULL OR price = 0"
            ).fetchone()[0]
            if null_price > 0:
                results.append({
                    "table": "portfolio_transactions",
                    "field": "price",
                    "issue": f"有 {null_price} 条交易记录缺少价格",
                    "null_count": null_price,
                    "total_count": total,
                    "null_rate": round(null_price / total, 3),
                    "severity": "warning",
                })

            null_shares = conn.execute(
                "SELECT COUNT(*) FROM portfolio_transactions WHERE shares IS NULL OR shares = 0"
            ).fetchone()[0]
            if null_shares > 0:
                results.append({
                    "table": "portfolio_transactions",
                    "field": "shares",
                    "issue": f"有 {null_shares} 条交易记录缺少份额",
                    "null_count": null_shares,
                    "total_count": total,
                    "null_rate": round(null_shares / total, 3),
                    "severity": "warning",
                })

            # 检查 amount 但无 price 和 shares（无法反推）
            missing_both = conn.execute(
                "SELECT COUNT(*) FROM portfolio_transactions WHERE (price IS NULL OR price = 0) AND (shares IS NULL OR shares = 0) AND amount > 0"
            ).fetchone()[0]
            if missing_both > 0:
                results.append({
                    "table": "portfolio_transactions",
                    "field": "price+shares",
                    "issue": f"有 {missing_both} 条记录同时缺少 price 和 shares，仅有金额",
                    "null_count": missing_both,
                    "total_count": total,
                    "null_rate": round(missing_both / total, 3),
                    "severity": "warning",
                })
        else:
            results.append({
                "table": "portfolio_transactions",
                "field": "overall",
                "issue": "表为空，无交易记录",
                "null_count": 0,
                "total_count": 0,
                "null_rate": 1.0,
                "severity": "info",
            })
    else:
        _WARNINGS.append("portfolio_transactions 表不存在，跳过检查")

    # ---- 1.5 articles / author_articles: 新闻数据时效性 ----
    if _table_exists(conn, "articles"):
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        latest = conn.execute(
            "SELECT MAX(created_at) FROM articles"
        ).fetchone()[0]
        days = _days_since(latest)
        if days is not None and days > 7:
            results.append({
                "table": "articles",
                "field": "created_at",
                "issue": f"最新文章创建于 {latest}，距今 {days} 天，新闻数据可能过期",
                "null_count": 0,
                "total_count": total,
                "null_rate": 0.0,
                "severity": "warning" if days < 14 else "critical",
            })
        # 检查 status 为 pending 的积压
        pending = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE status = 'pending'"
        ).fetchone()[0]
        if pending > 10:
            results.append({
                "table": "articles",
                "field": "status",
                "issue": f"有 {pending} 篇文章处于 pending 状态，可能未处理",
                "null_count": pending,
                "total_count": total,
                "null_rate": round(pending / max(total, 1), 3),
                "severity": "info",
            })
    else:
        _WARNINGS.append("articles 表不存在，跳过检查")

    # ---- 1.6 fund_metadata: 基金元数据缺失 ----
    if _table_exists(conn, "fund_metadata"):
        total = conn.execute("SELECT COUNT(*) FROM fund_metadata").fetchone()[0]
        if total == 0:
            results.append({
                "table": "fund_metadata",
                "field": "overall",
                "issue": "fund_metadata 表为空，缺少基金分类、基准等元数据",
                "null_count": 0,
                "total_count": 0,
                "null_rate": 1.0,
                "severity": "warning",
            })
    else:
        _WARNINGS.append("fund_metadata 表不存在，跳过检查")
        # 即使表不存在也报一个问题
        results.append({
            "table": "fund_metadata",
            "field": "overall",
            "issue": "fund_metadata 表不存在，缺少基金分类、基准等元数据，影响基金分析质量",
            "null_count": 0,
            "total_count": 0,
            "null_rate": 1.0,
            "severity": "warning",
        })

    # ---- 1.7 fund_trade_profiles: 交易画像缺失 ----
    if _table_exists(conn, "fund_trade_profiles"):
        total = conn.execute("SELECT COUNT(*) FROM fund_trade_profiles").fetchone()[0]
        if total == 0:
            results.append({
                "table": "fund_trade_profiles",
                "field": "overall",
                "issue": "fund_trade_profiles 表为空，缺少基金短期/长期适配性、赎回费等信息",
                "null_count": 0,
                "total_count": 0,
                "null_rate": 1.0,
                "severity": "warning",
            })
    else:
        _WARNINGS.append("fund_trade_profiles 表不存在，跳过检查")

    conn.close()
    return results


# ---------------------------------------------------------------------------
# 2. 数据新鲜度报告
# ---------------------------------------------------------------------------

def data_freshness_report() -> dict:
    """
    各数据源的最新更新时间和新鲜度状态。

    返回 dict:
      - sources: {source_name: {label, latest, age_hours, status, count}}
      - summary: {ok, stale, expired, unknown}
    """
    conn = _get_conn()
    sources: dict[str, Any] = {}

    def _add_source(key: str, label: str, latest: str | None, count: int, is_market: bool = False):
        age = _trading_hours_since(latest, is_market_data=is_market)
        sources[key] = {
            "label": label,
            "latest": latest,
            "age_hours": age if age is not None else 0,
            "status": _freshness_status(age),
            "count": count,
        }

    # 持仓价格更新 (市场数据，周末不算过期)
    if _table_exists(conn, "portfolio_holdings"):
        row = conn.execute(
            "SELECT MAX(price_updated_at) as latest, COUNT(*) as cnt FROM portfolio_holdings"
        ).fetchone()
        _add_source("holdings_price", "持仓价格", row["latest"], row["cnt"], is_market=True)
    else:
        _add_source("holdings_price", "持仓价格", None, 0, is_market=True)

    # 估值数据 (市场数据)
    if _table_exists(conn, "index_valuations"):
        row = conn.execute(
            "SELECT MAX(snapshot_date) as latest, COUNT(*) as cnt FROM index_valuations"
        ).fetchone()
        latest_dt = row["latest"] + " 00:00:00" if row["latest"] else None
        _add_source("valuations", "指数估值", latest_dt, row["cnt"], is_market=True)
    else:
        _add_source("valuations", "指数估值", None, 0, is_market=True)

    # 基金净值 (市场数据)
    if _table_exists(conn, "fund_nav_history"):
        row = conn.execute(
            "SELECT MAX(nav_date) as latest, COUNT(*) as cnt FROM fund_nav_history"
        ).fetchone()
        latest_dt = row["latest"] + " 00:00:00" if row["latest"] else None
        _add_source("fund_nav", "基金净值", latest_dt, row["cnt"], is_market=True)
    else:
        _add_source("fund_nav", "基金净值", None, 0, is_market=True)

    # 交易记录
    if _table_exists(conn, "portfolio_transactions"):
        row = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM portfolio_transactions"
        ).fetchone()
        _add_source("transactions", "交易记录", row["latest"], row["cnt"])
    else:
        _add_source("transactions", "交易记录", None, 0)

    # 文章/新闻
    if _table_exists(conn, "articles"):
        row = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM articles"
        ).fetchone()
        _add_source("articles", "新闻文章", row["latest"], row["cnt"])
    else:
        _add_source("articles", "新闻文章", None, 0)

    # 作者文章
    if _table_exists(conn, "author_articles"):
        row = conn.execute(
            "SELECT MAX(created_at) as latest, COUNT(*) as cnt FROM author_articles"
        ).fetchone()
        _add_source("author_articles", "研究员文章", row["latest"], row["cnt"])
    else:
        _add_source("author_articles", "研究员文章", None, 0)

    # 快照
    if _table_exists(conn, "portfolio_snapshots"):
        row = conn.execute(
            "SELECT MAX(snapshot_date) as latest, COUNT(*) as cnt FROM portfolio_snapshots"
        ).fetchone()
        latest_dt = row["latest"] + " 00:00:00" if row["latest"] else None
        age = _hours_since(latest_dt)
        sources["snapshots"] = {
            "label": "持仓快照",
            "latest": row["latest"],
            "age_hours": age,
            "status": _freshness_status(age) if age else ("unknown" if age is None else "expired"),
            "count": row["cnt"],
        }
    else:
        _add_source("snapshots", "持仓快照", None, 0)

    # 健康评分
    if _table_exists(conn, "health_scores"):
        row = conn.execute(
            "SELECT MAX(score_date) as latest, COUNT(*) as cnt FROM health_scores"
        ).fetchone()
        latest_dt = row["latest"] + " 00:00:00" if row["latest"] else None
        age = _hours_since(latest_dt)
        sources["health_scores"] = {
            "label": "健康评分",
            "latest": row["latest"],
            "age_hours": age,
            "status": _freshness_status(age) if age else ("unknown" if age is None else "expired"),
            "count": row["cnt"],
        }
    else:
        _add_source("health_scores", "健康评分", None, 0)

    # 汇总
    summary = {"ok": 0, "stale": 0, "expired": 0, "unknown": 0}
    for s in sources.values():
        summary[s["status"]] = summary.get(s["status"], 0) + 1

    conn.close()
    return {"sources": sources, "summary": summary}


# ---------------------------------------------------------------------------
# 3. 数据一致性检查
# ---------------------------------------------------------------------------

def data_consistency_check() -> list[dict]:
    """
    跨表数据一致性校验。

    返回 list[dict]，每个 dict 描述一个不一致问题：
      - check: 检查名称
      - expected: 期望值/状态
      - actual: 实际值/状态
      - severity: critical / warning / info
      - detail: 详细描述
    """
    conn = _get_conn()
    results: list[dict] = []

    # ---- 3.1 持仓总市值 vs 各基金市值之和 ----
    if _table_exists(conn, "portfolio_holdings"):
        rows = conn.execute(
            """SELECT
                  SUM(current_value) as total_mv,
                  SUM(total_cost) as total_cost,
                  SUM(profit_loss) as total_pl,
                  COUNT(*) as cnt
               FROM portfolio_holdings WHERE shares > 0"""
        ).fetchone()

        if rows and rows["cnt"] > 0:
            total_mv = rows["total_mv"] or 0
            total_cost = rows["total_cost"] or 0
            total_pl = rows["total_pl"] or 0

            # 计算 profit_loss 是否等于 current_value - total_cost
            expected_pl = round(total_mv - total_cost, 2)
            actual_pl = round(total_pl, 2)
            if abs(expected_pl - actual_pl) > 1.0:
                results.append({
                    "check": "持仓盈亏一致性",
                    "expected": f"profit_loss 应为 {expected_pl} (市值-成本)",
                    "actual": f"实际 profit_loss 合计为 {actual_pl}",
                    "severity": "warning",
                    "detail": f"差异 {round(abs(expected_pl - actual_pl), 2)} 元，可能部分持仓未更新盈亏",
                })

            # 检查有市值但无成本的持仓
            mv_no_cost = conn.execute(
                "SELECT COUNT(*) FROM portfolio_holdings WHERE current_value > 0 AND (total_cost IS NULL OR total_cost = 0)"
            ).fetchone()[0]
            if mv_no_cost > 0:
                results.append({
                    "check": "持仓成本缺失",
                    "expected": "有市值的持仓应有成本数据",
                    "actual": f"{mv_no_cost} 条持仓有市值但无成本",
                    "severity": "warning",
                    "detail": "无法计算盈亏率",
                })

            # 检查有份额但无市值的持仓
            shares_no_mv = conn.execute(
                "SELECT COUNT(*) FROM portfolio_holdings WHERE shares > 0 AND (current_value IS NULL OR current_value = 0)"
            ).fetchone()[0]
            if shares_no_mv > 0:
                results.append({
                    "check": "持仓市值缺失",
                    "expected": "有份额的持仓应有市值",
                    "actual": f"{shares_no_mv} 条持仓有份额但无市值",
                    "severity": "critical",
                    "detail": "可能未更新当前价格",
                })

    # ---- 3.2 交易记录中的基金代码 vs 持仓表中的基金代码 ----
    if _table_exists(conn, "portfolio_transactions") and _table_exists(conn, "portfolio_holdings"):
        orphan_tx = conn.execute(
            """SELECT DISTINCT t.fund_code, t.fund_name
               FROM portfolio_transactions t
               LEFT JOIN portfolio_holdings h ON t.fund_code = h.fund_code
               WHERE h.fund_code IS NULL"""
        ).fetchall()
        if orphan_tx:
            orphan_list = [f"{r['fund_code']}({r['fund_name'] or '未知'})" for r in orphan_tx]
            results.append({
                "check": "交易记录孤儿基金",
                "expected": "交易记录的基金代码应在持仓表中存在",
                "actual": f"{len(orphan_tx)} 个基金在交易记录中但不在持仓表中",
                "severity": "info",
                "detail": f"可能已清仓: {', '.join(orphan_list[:5])}",
            })

        # 反向：持仓表中有但交易记录中没有的基金
        orphan_holdings = conn.execute(
            """SELECT DISTINCT h.fund_code, h.fund_name
               FROM portfolio_holdings h
               LEFT JOIN portfolio_transactions t ON h.fund_code = t.fund_code
               WHERE t.fund_code IS NULL"""
        ).fetchall()
        if orphan_holdings:
            orphan_list = [f"{r['fund_code']}({r['fund_name'] or '未知'})" for r in orphan_holdings]
            results.append({
                "check": "持仓无交易记录",
                "expected": "持仓中的基金应有对应交易记录",
                "actual": f"{len(orphan_holdings)} 个基金在持仓表中但无交易记录",
                "severity": "warning",
                "detail": f"可能通过导入添加，无交易历史: {', '.join(orphan_list[:5])}",
            })

    # ---- 3.3 估值数据中的指数代码 vs 持仓关联指数 ----
    if _table_exists(conn, "index_valuations") and _table_exists(conn, "portfolio_holdings"):
        # 持仓中关联了指数但没有估值数据的
        missing_val = conn.execute(
            """SELECT DISTINCT h.index_code, h.index_name, h.fund_code, h.fund_name
               FROM portfolio_holdings h
               LEFT JOIN index_valuations v ON h.index_code = v.index_code
               WHERE h.index_code IS NOT NULL AND h.index_code != '' AND v.index_code IS NULL"""
        ).fetchall()
        if missing_val:
            missing_list = [
                f"{r['fund_name']}→{r['index_name'] or r['index_code']}"
                for r in missing_val
            ]
            results.append({
                "check": "持仓指数缺少估值数据",
                "expected": "持仓关联的指数应在估值表中存在",
                "actual": f"{len(missing_val)} 个持仓关联的指数无估值数据",
                "severity": "warning",
                "detail": f"影响估值分析: {', '.join(missing_list[:5])}",
            })

        # 持仓中未关联指数的基金（非货币/债券）
        no_index = conn.execute(
            """SELECT fund_code, fund_name, fund_category
               FROM portfolio_holdings
               WHERE (index_code IS NULL OR index_code = '')
               AND fund_category NOT IN ('货币基金', '债券基金', '')
               """
        ).fetchall()
        if no_index:
            no_index_list = [f"{r['fund_code']}({r['fund_name']})" for r in no_index]
            results.append({
                "check": "持仓未关联指数",
                "expected": "非货币/债券基金应关联跟踪指数",
                "actual": f"{len(no_index)} 个非货币/债券基金未关联指数",
                "severity": "warning",
                "detail": f"无法进行估值百分位分析: {', '.join(no_index_list[:5])}",
            })

    # ---- 3.4 持仓快照 vs 持仓表总市值 ----
    if _table_exists(conn, "portfolio_snapshots") and _table_exists(conn, "portfolio_holdings"):
        latest_snap = conn.execute(
            "SELECT snapshot_date, total_value, total_cost FROM portfolio_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        holdings_sum = conn.execute(
            "SELECT SUM(current_value) as mv, SUM(total_cost) as cost FROM portfolio_holdings WHERE shares > 0"
        ).fetchone()

        if latest_snap and holdings_sum and holdings_sum["mv"]:
            snap_mv = latest_snap["total_value"] or 0
            hold_mv = holdings_sum["mv"] or 0
            diff = abs(snap_mv - hold_mv)
            if diff > 1.0:
                pct_diff = round(diff / max(hold_mv, 1) * 100, 2)
                results.append({
                    "check": "快照市值 vs 持仓市值",
                    "expected": f"快照市值应接近持仓合计: {hold_mv:.2f}",
                    "actual": f"快照市值: {snap_mv:.2f}，差异 {diff:.2f} ({pct_diff}%)",
                    "severity": "warning" if pct_diff < 5 else "critical",
                    "detail": f"快照日期 {latest_snap['snapshot_date']}，可能持仓已更新但快照未刷新",
                })

    # ---- 3.5 fund_nav_history 覆盖持仓基金 ----
    if _table_exists(conn, "fund_nav_history") and _table_exists(conn, "portfolio_holdings"):
        holdings_funds = conn.execute(
            "SELECT DISTINCT fund_code FROM portfolio_holdings WHERE shares > 0"
        ).fetchall()
        nav_funds = set(
            r["fund_code"] for r in conn.execute(
                "SELECT DISTINCT fund_code FROM fund_nav_history"
            ).fetchall()
        )
        missing_nav = [
            r["fund_code"] for r in holdings_funds if r["fund_code"] not in nav_funds
        ]
        if missing_nav:
            results.append({
                "check": "持仓基金缺少净值历史",
                "expected": "持仓中的基金应有净值历史数据",
                "actual": f"{len(missing_nav)} 个持仓基金无净值历史",
                "severity": "warning",
                "detail": f"影响趋势分析: {', '.join(missing_nav[:5])}",
            })

    conn.close()
    return results


# ---------------------------------------------------------------------------
# 综合报告
# ---------------------------------------------------------------------------

def generate_full_report() -> dict:
    """生成完整的数据质量报告"""
    gaps = data_gap_analysis()
    freshness = data_freshness_report()
    consistency = data_consistency_check()

    # 统计严重程度
    severity_count = {"critical": 0, "warning": 0, "info": 0}
    for g in gaps:
        severity_count[g.get("severity", "info")] = \
            severity_count.get(g.get("severity", "info"), 0) + 1
    for c in consistency:
        severity_count[c.get("severity", "info")] = \
            severity_count.get(c.get("severity", "info"), 0) + 1

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "warnings": list(_WARNINGS),
        "data_gaps": gaps,
        "freshness": freshness,
        "consistency": consistency,
        "severity_summary": severity_count,
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    report = generate_full_report()

    print("=" * 70)
    print("  数据缺口分析报告")
    print(f"  生成时间: {report['generated_at']}")
    print("=" * 70)

    # Warnings
    if report["warnings"]:
        print("\n⚠️  跳过的表（不存在）:")
        for w in report["warnings"]:
            print(f"   - {w}")

    # Data Gaps
    print(f"\n{'─' * 70}")
    print("  一、数据缺口分析")
    print(f"{'─' * 70}")
    if report["data_gaps"]:
        for g in report["data_gaps"]:
            icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(g["severity"], "⚪")
            print(f"\n  {icon} [{g['severity'].upper()}] {g['table']}.{g['field']}")
            print(f"     {g['issue']}")
            if g.get("details"):
                for d in g["details"][:3]:
                    print(f"     - {d}")
    else:
        print("  ✅ 未发现数据缺口问题")

    # Freshness
    print(f"\n{'─' * 70}")
    print("  二、数据新鲜度报告")
    print(f"{'─' * 70}")
    src = report["freshness"]["sources"]
    for key, s in src.items():
        icon = {"ok": "✅", "stale": "🟡", "expired": "🔴", "unknown": "❓"}.get(s["status"], "❓")
        age = f"{s['age_hours']}h" if s["age_hours"] is not None else "N/A"
        print(f"  {icon} {s['label']:12s} | 最新: {str(s['latest']):20s} | 年龄: {age:>8s} | 记录: {s['count']}")
    print(f"\n  汇总: {report['freshness']['summary']}")

    # Consistency
    print(f"\n{'─' * 70}")
    print("  三、数据一致性检查")
    print(f"{'─' * 70}")
    if report["consistency"]:
        for c in report["consistency"]:
            icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(c["severity"], "⚪")
            print(f"\n  {icon} [{c['severity'].upper()}] {c['check']}")
            print(f"     期望: {c['expected']}")
            print(f"     实际: {c['actual']}")
            print(f"     详情: {c['detail']}")
    else:
        print("  ✅ 未发现一致性问题")

    # Summary
    print(f"\n{'═' * 70}")
    print("  严重程度汇总:")
    for k, v in report["severity_summary"].items():
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(k, "⚪")
        print(f"    {icon} {k}: {v}")
    print(f"{'═' * 70}\n")

    # JSON 输出（可选）
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
