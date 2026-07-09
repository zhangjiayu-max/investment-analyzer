"""指数历史数据获取器 — 从 akshare 拉取指数历史（点位+PE），落库 index_price_history。

数据源：ak.stock_zh_index_hist_csindex（中证指数公司官方，已实测可用）
- 白酒 399997 返回 2799 行，2015-01-01 至今（11.5 年）
- 同时返回收盘点位（L4用）+ 滚动市盈率（L5用）

注意：需 ssl._create_default_https_context = ssl._create_unverified_context 绕过证书验证
"""

import logging
import ssl
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 绕过 SSL 证书验证（akshare 中证指数接口需要）
ssl._create_default_https_context = ssl._create_unverified_context


def fetch_index_history(index_code: str, years: int = 10) -> dict:
    """从 akshare 拉取指数历史，落库 index_price_history。

    Args:
        index_code: 指数代码（如 '399997'，不带后缀）
        years: 拉取年数

    Returns:
        {"fetched": int, "saved": int, "date_range": "start~end"}
    """
    import akshare as ak
    from db._conn import _get_conn

    # 归一化代码（剥离后缀）
    code = _normalize_code(index_code)

    start_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")

    try:
        # ak.stock_zh_index_hist_csindex 需要纯数字代码
        df = ak.stock_zh_index_hist_csindex(symbol=code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning(f"拉取指数 {code} 历史失败: {e}")
        # 降级：尝试 stock_zh_index_daily
        try:
            df = ak.stock_zh_index_daily(symbol=f"sh{code}" if code.startswith("000") else f"sz{code}")
        except Exception as e2:
            logger.error(f"降级拉取也失败: {e2}")
            return {"fetched": 0, "saved": 0, "error": str(e2)}

    if df is None or len(df) == 0:
        return {"fetched": 0, "saved": 0, "error": "空数据"}

    # 标准化列名（不同接口列名不同）
    df = _normalize_columns(df)

    saved = 0
    conn = _get_conn()
    try:
        for _, row in df.iterrows():
            trade_date = str(row.get("trade_date", "")).split(" ")[0]
            if not trade_date:
                continue
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO index_price_history
                    (index_code, trade_date, close, pe_ttm, pb, dividend_yield, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    code, trade_date,
                    float(row.get("close", 0)) if row.get("close") else None,
                    float(row.get("pe_ttm", 0)) if row.get("pe_ttm") else None,
                    float(row.get("pb", 0)) if row.get("pb") else None,
                    float(row.get("dividend_yield", 0)) if row.get("dividend_yield") else None,
                    "csindex",
                    datetime.now().isoformat(),
                ))
                saved += 1
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()

    date_range = ""
    if len(df) > 0:
        dates = df["trade_date"].astype(str).tolist()
        date_range = f"{dates[0]}~{dates[-1]}"

    logger.info(f"指数 {code} 拉取 {len(df)} 行，落库 {saved} 条，范围 {date_range}")
    return {"fetched": len(df), "saved": saved, "date_range": date_range}


def _normalize_code(index_code: str) -> str:
    """归一化指数代码，剥离后缀。"""
    if not index_code:
        return ""
    code = index_code.strip().upper()
    for suffix in (".SZ", ".SH", ".CSI", ".HI", ".GI"):
        if code.endswith(suffix):
            return code[: -len(suffix)]
    return code


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名（不同 akshare 接口列名不同）。"""
    col_map = {
        "日期": "trade_date",
        "收盘价": "close",
        "收盘": "close",
        "市盈率1": "pe_ttm",
        "市盈率2": "pe_ttm",
        "滚动市盈率": "pe_ttm",
        "市净率1": "pb",
        "市净率2": "pb",
        "滚动市净率": "pb",
        "股息率1": "dividend_yield",
        "股息率2": "dividend_yield",
        "滚动股息率": "dividend_yield",
        "date": "trade_date",
        "close": "close",
        "pe": "pe_ttm",
        "pb": "pb",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    return df


def get_index_price_history(index_code: str, days: int = 3650) -> list[dict]:
    """读取本地 index_price_history 表。

    Args:
        index_code: 指数代码
        days: 返回最近多少天

    Returns:
        [{trade_date, close, pe_ttm, pb, dividend_yield}, ...]
    """
    from db._conn import _get_conn
    code = _normalize_code(index_code)
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT trade_date, close, pe_ttm, pb, dividend_yield
            FROM index_price_history
            WHERE index_code = ? AND trade_date >= ?
            ORDER BY trade_date ASC
        """, (code, start_date)).fetchall()
    finally:
        conn.close()

    return [{"trade_date": r[0], "close": r[1], "pe_ttm": r[2], "pb": r[3], "dividend_yield": r[4]} for r in rows]


def ensure_index_history(index_code: str, min_years: int = 3) -> bool:
    """确保指数有足够历史数据，不足则自动拉取。

    Args:
        index_code: 指数代码
        min_years: 最少年数

    Returns:
        True 如果数据足够
    """
    from db._conn import _get_conn
    code = _normalize_code(index_code)

    conn = _get_conn()
    try:
        row = conn.execute("""
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date)
            FROM index_price_history WHERE index_code = ?
        """, (code,)).fetchone()
    finally:
        conn.close()

    count, min_date, max_date = row[0], row[1], row[2]

    # 检查数据量和时间跨度
    if count > 0 and min_date and max_date:
        try:
            span_days = (datetime.fromisoformat(max_date) - datetime.fromisoformat(min_date)).days
            if span_days >= min_years * 365:
                return True
        except Exception:
            pass

    # 数据不足，触发拉取
    logger.info(f"指数 {code} 历史数据不足（{count}条），触发拉取")
    result = fetch_index_history(code, years=max(min_years, 10))
    return result.get("saved", 0) > 0


def fetch_holdings_index_history(user_id: str = "default") -> dict:
    """批量回填用户持仓涉及的指数历史（启动时调用）。

    Returns:
        {"total": int, "success": int, "failed": int, "details": [...]}
    """
    from db.portfolio import list_holdings

    holdings = list_holdings(user_id=user_id)
    index_codes = set()
    for h in holdings:
        ic = h.get("index_code")
        if ic:
            index_codes.add(ic)

    total = len(index_codes)
    success = 0
    failed = 0
    details = []

    for code in index_codes:
        try:
            result = fetch_index_history(code, years=10)
            if result.get("saved", 0) > 0:
                success += 1
                details.append({"index_code": code, "status": "ok", "saved": result["saved"]})
            else:
                failed += 1
                details.append({"index_code": code, "status": "empty", "error": result.get("error", "")})
        except Exception as e:
            failed += 1
            details.append({"index_code": code, "status": "error", "error": str(e)})

    logger.info(f"批量回填完成：{total}个指数，成功{success}，失败{failed}")
    return {"total": total, "success": success, "failed": failed, "details": details}
