"""基金元信息与净值历史缓存服务

目标：降低对外部数据源（akshare / 盈米 MCP）的实时依赖，提升持仓管理模块的数据稳定性。

职责：
- 从 akshare / 东方财富获取基金元信息并缓存到 fund_metadata 表
- 从 akshare / 东方财富获取基金净值历史并缓存到 fund_nav_history 表
- 提供本地优先（cache-first）的查询接口
- 对外屏蔽数据源细节，失败时返回已缓存数据或 None
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# ── akshare 可选导入 ──
try:
    import akshare as ak

    _HAS_AKSHARE = True
except ImportError:
    ak = None
    _HAS_AKSHARE = False
    logger.warning("akshare 未安装，fund_data_service 将仅使用 HTTP 降级")

try:
    import pandas as pd
except ImportError:
    pd = None

# ── 内存缓存（用于短期高频复用，TTL 5 分钟）──
_MEMORY_CACHE = {}
_MEMORY_CACHE_TTL = 300


def _mc_get(key: str):
    entry = _MEMORY_CACHE.get(key)
    if entry and entry[1] > time.time():
        return entry[0]
    return None


def _mc_set(key: str, value):
    _MEMORY_CACHE[key] = (value, time.time() + _MEMORY_CACHE_TTL)


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_str(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 基金元信息 ──────────────────────────────────────


def _fetch_fund_metadata_from_akshare(fund_code: str) -> dict | None:
    """通过 akshare.fund_overview_em 获取基金元信息，并验证 fund_code 匹配。"""
    if not _HAS_AKSHARE or ak is None:
        return None
    try:
        df = ak.fund_overview_em(symbol=fund_code)
        if df is None or len(df) == 0:
            return None

        # 优先在返回结果中查找与 fund_code 完全匹配的行
        row = None
        code_col = "基金代码" if "基金代码" in df.columns else None
        if code_col:
            for _, r in df.iterrows():
                code_raw = _safe_str(r.get(code_col, ""))
                code_clean = re.sub(r"（.*?）", "", code_raw).strip()
                if code_clean == fund_code:
                    row = r
                    break
        # 找不到匹配行再回退到第一行
        if row is None:
            row = df.iloc[0]
            code_raw = _safe_str(row.get(code_col, fund_code)) if code_col else fund_code
            code_clean = re.sub(r"（.*?）", "", code_raw).strip()
            if code_clean != fund_code:
                logger.warning(f"akshare 返回基金代码 {code_clean} 与查询 {fund_code} 不一致，使用第一行")

        return {
            "fund_code": re.sub(r"（.*?）", "", _safe_str(row.get("基金代码", fund_code))).strip(),
            "fund_name": _safe_str(row.get("基金简称", "")),
            "fund_full_name": _safe_str(row.get("基金全称", "")),
            "fund_type": _safe_str(row.get("基金类型", "")),
            "tracking_index": _safe_str(row.get("跟踪标的", "")),
            "fund_manager": _safe_str(row.get("基金经理人", "")),
            "scale": _safe_str(row.get("净资产规模", "")),
            "established": _safe_str(row.get("成立日期/规模", "")),
            "benchmark": _safe_str(row.get("业绩比较基准", "")),
        }
    except Exception as e:
        logger.warning(f"akshare 获取基金元信息失败 {fund_code}: {e}")
        return None


def _metadata_from_akshare_dict(raw: dict) -> dict:
    """把 akshare 原始字典规整为 fund_metadata 表字段。"""
    fund_code = _safe_str(raw.get("fund_code", ""))
    fund_name = _safe_str(raw.get("fund_name", ""))
    fund_type = _safe_str(raw.get("fund_type", ""))
    # 复用 db.portfolio 的统一分类逻辑，避免两份实现漂移
    from db.portfolio import classify_fund_category

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "fund_type": fund_type,
        "fund_category": classify_fund_category(fund_name, fund_type, fund_code),
        "benchmark": _safe_str(raw.get("benchmark", "")),
        "establish_date": _safe_str(raw.get("established", "")),
        "management_company": "",
        "management_fee": None,
        "custody_fee": None,
        "subscription_fee": None,
        "source": "akshare",
        "updated_at": _now_str(),
    }


def get_fund_metadata(fund_code: str) -> dict | None:
    """本地优先获取基金元信息。"""
    if not fund_code:
        return None

    cache_key = f"meta:{fund_code}"
    cached = _mc_get(cache_key)
    if cached:
        return cached

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM fund_metadata WHERE fund_code = ?", (fund_code,)
        ).fetchone()
        if row:
            result = dict(row)
            _mc_set(cache_key, result)
            return result
    finally:
        conn.close()
    return None


def refresh_fund_metadata(fund_code: str) -> dict | None:
    """强制刷新基金元信息，写入本地缓存。"""
    if not fund_code:
        return None

    raw = _fetch_fund_metadata_from_akshare(fund_code)
    if not raw:
        return None

    meta = _metadata_from_akshare_dict(raw)
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO fund_metadata (
                fund_code, fund_name, fund_type, fund_category, benchmark,
                establish_date, management_company, management_fee, custody_fee,
                subscription_fee, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fund_code) DO UPDATE SET
                fund_name = excluded.fund_name,
                fund_type = excluded.fund_type,
                fund_category = excluded.fund_category,
                benchmark = excluded.benchmark,
                establish_date = excluded.establish_date,
                management_company = excluded.management_company,
                management_fee = excluded.management_fee,
                custody_fee = excluded.custody_fee,
                subscription_fee = excluded.subscription_fee,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                meta["fund_code"], meta["fund_name"], meta["fund_type"],
                meta["fund_category"], meta["benchmark"], meta["establish_date"],
                meta["management_company"], meta["management_fee"],
                meta["custody_fee"], meta["subscription_fee"],
                meta["source"], meta["updated_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _mc_set(f"meta:{fund_code}", meta)
    return meta


def get_or_refresh_fund_metadata(fund_code: str, max_age_days: int = 30) -> dict | None:
    """获取元信息，本地无缓存或缓存过期时自动刷新。"""
    meta = get_fund_metadata(fund_code)
    if meta:
        updated_at = meta.get("updated_at", "")
        try:
            updated = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - updated).days <= max_age_days:
                return meta
        except Exception:
            pass
    return refresh_fund_metadata(fund_code)


def batch_refresh_fund_metadata(fund_codes: list[str]) -> dict[str, dict | None]:
    """批量刷新基金元信息，返回每个基金 code 的结果。"""
    results = {}
    for code in set(fund_codes):
        if not code:
            continue
        try:
            results[code] = get_or_refresh_fund_metadata(code)
        except Exception as e:
            logger.warning(f"批量刷新基金元信息失败 {code}: {e}")
            results[code] = None
    return results


# ── 基金净值历史 ──────────────────────────────────────


def _fetch_nav_history_from_akshare(fund_code: str) -> list[dict] | None:
    """通过 akshare 获取基金净值历史。"""
    if not _HAS_AKSHARE or ak is None:
        return None
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is None or len(df) == 0:
            return None
        records = []
        for _, row in df.iterrows():
            records.append({
                "fund_code": fund_code,
                "nav_date": _safe_str(row.get("净值日期", "")),
                "nav": _safe_float(row.get("单位净值")),
                "acc_nav": _safe_float(row.get("累计净值")),
                "change_pct": _safe_float(row.get("日增长率")),
                "source": "akshare",
            })
        # 按日期升序排列
        records.sort(key=lambda x: x["nav_date"])
        return records
    except Exception as e:
        logger.warning(f"akshare 获取基金净值历史失败 {fund_code}: {e}")
        return None


def _fetch_nav_history_from_eastmoney(fund_code: str) -> list[dict] | None:
    """东方财富基金净值 HTTP API 降级。"""
    try:
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        params = {
            "fundCode": fund_code,
            "pageIndex": 1,
            "pageSize": 365,
            "startDate": "",
            "endDate": "",
        }
        headers = {"Referer": "https://fundf10.eastmoney.com/"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        rows = data.get("Data", {}).get("LSJZList", []) or []
        if not rows:
            return None
        records = []
        for r in rows:
            nav = _safe_float(r.get("DWJZ"))
            if nav is None:
                continue
            records.append({
                "fund_code": fund_code,
                "nav_date": _safe_str(r.get("FSRQ", "")),
                "nav": nav,
                "acc_nav": _safe_float(r.get("LJJZ")),
                "change_pct": _safe_float(r.get("JZZZL")),
                "source": "eastmoney",
            })
        records.sort(key=lambda x: x["nav_date"])
        return records
    except Exception as e:
        logger.warning(f"东方财富获取基金净值历史失败 {fund_code}: {e}")
        return None


def _save_nav_history(records: list[dict]) -> None:
    """批量写入净值历史，忽略冲突。"""
    if not records:
        return
    conn = _get_conn()
    try:
        for r in records:
            conn.execute(
                """
                INSERT OR IGNORE INTO fund_nav_history
                (fund_code, nav_date, nav, acc_nav, change_pct, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["fund_code"], r["nav_date"], r["nav"],
                    r.get("acc_nav"), r.get("change_pct"),
                    r.get("source", "akshare"), _now_str(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def refresh_fund_nav_history(fund_code: str) -> list[dict] | None:
    """强制刷新基金净值历史，优先 akshare，失败降级东方财富。"""
    if not fund_code:
        return None

    records = _fetch_nav_history_from_akshare(fund_code)
    source = "akshare"
    if not records:
        records = _fetch_nav_history_from_eastmoney(fund_code)
        source = "eastmoney"
    if not records:
        return None

    _save_nav_history(records)
    _mc_set(f"nav:{fund_code}", records)
    logger.info(f"已缓存 {fund_code} 净值历史 {len(records)} 条，来源 {source}")
    return records


def get_fund_nav_history_from_cache(
    fund_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
) -> list[dict]:
    """从本地缓存查询净值历史。"""
    conn = _get_conn()
    try:
        sql = "SELECT * FROM fund_nav_history WHERE fund_code = ?"
        params = [fund_code]
        if start_date:
            sql += " AND nav_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND nav_date <= ?"
            params.append(end_date)
        sql += " ORDER BY nav_date ASC"
        rows = conn.execute(sql, params).fetchall()
        records = [dict(r) for r in rows]
    finally:
        conn.close()

    if days and len(records) > days:
        records = records[-days:]
    return records


def get_or_refresh_fund_nav_history(
    fund_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
    min_local_days: int = 30,
) -> list[dict]:
    """获取净值历史，本地数据不足时自动刷新。

    策略：
    - 如果本地最新记录与今天差距 > 2 个交易日，或总记录数 < min_local_days，则触发刷新
    - 否则直接返回本地缓存
    """
    if not fund_code:
        return []

    cache_key = f"nav:{fund_code}"
    cached = _mc_get(cache_key)
    if cached is not None:
        records = cached
    else:
        records = get_fund_nav_history_from_cache(fund_code, start_date, end_date)

    need_refresh = False
    if not records:
        need_refresh = True
    elif len(records) < min_local_days:
        need_refresh = True
    else:
        latest_date = records[-1].get("nav_date", "")
        try:
            latest = datetime.strptime(latest_date, "%Y-%m-%d")
            # 简单规则：超过 3 天未更新就刷新
            if (datetime.now() - latest).days > 3:
                need_refresh = True
        except Exception:
            need_refresh = True

    if need_refresh:
        fresh = refresh_fund_nav_history(fund_code)
        if fresh:
            records = get_fund_nav_history_from_cache(fund_code, start_date, end_date, days)
        elif not records:
            return []

    _mc_set(cache_key, records)
    if days and len(records) > days:
        records = records[-days:]
    return records


def batch_refresh_fund_nav_history(fund_codes: list[str]) -> dict[str, list[dict] | None]:
    """批量刷新基金净值历史。"""
    results = {}
    for code in set(fund_codes):
        if not code:
            continue
        try:
            results[code] = refresh_fund_nav_history(code)
        except Exception as e:
            logger.warning(f"批量刷新基金净值历史失败 {code}: {e}")
            results[code] = None
    return results


def save_latest_nav(fund_code: str, nav: float, nav_date: str, change_pct: float | None = None) -> None:
    """保存单条最新净值到本地缓存（用于刷新净值时增量更新）。"""
    if not fund_code or nav is None or not nav_date:
        return
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO fund_nav_history (fund_code, nav_date, nav, change_pct, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(fund_code, nav_date) DO UPDATE SET
                nav = excluded.nav,
                change_pct = excluded.change_pct,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (fund_code, nav_date, nav, change_pct, "refresh", _now_str()),
        )
        conn.commit()
    finally:
        conn.close()
    # 清除内存缓存，避免脏数据
    _MEMORY_CACHE.pop(f"nav:{fund_code}", None)


# ── 统一入口 ──────────────────────────────────────


def warm_cache_for_portfolio(user_id: str = "default") -> dict:
    """为某个用户的全部持仓预热元信息和净值历史缓存。"""
    from db.portfolio import list_holdings

    holdings = list_holdings(user_id)
    fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code")]
    meta_results = batch_refresh_fund_metadata(fund_codes)
    nav_results = batch_refresh_fund_nav_history(fund_codes)
    return {
        "fund_codes": fund_codes,
        "metadata_success": sum(1 for v in meta_results.values() if v is not None),
        "nav_success": sum(1 for v in nav_results.values() if v is not None),
    }
