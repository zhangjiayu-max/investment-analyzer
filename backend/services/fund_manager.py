"""基金经理信息服务 — 查询、缓存、变更检测。

数据源：
- akshare fund_individual_basic_info_xq（雪球基金详情，含基金经理）
- akshare fund_manager_em（全量基金经理列表，含从业时间/管理规模/最佳回报）
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# 内存缓存（基金代码 → 经理信息，24 小时有效）
_manager_cache: dict[str, dict] = {}
_cache_ts: dict[str, float] = {}
_CACHE_TTL = 86400  # 24 小时

# 全量经理列表缓存
_all_managers_cache = None
_all_managers_ts = 0
_ALL_CACHE_TTL = 86400


def get_fund_manager(fund_code: str) -> dict | None:
    """获取基金的基金经理信息。

    返回：
        {
            "manager_name": "张坤",
            "company": "易方达基金",
            "fund_type": "QDII-混合",
            "establish_date": "2008-06-19",
            "scale": "95.44亿",
            "source": "xq",
        }
    """
    now = time.time()
    if fund_code in _manager_cache and now - _cache_ts.get(fund_code, 0) < _CACHE_TTL:
        return _manager_cache[fund_code]

    try:
        import akshare as ak
        df = ak.fund_individual_basic_info_xq(symbol=fund_code)
        if df is None or len(df) == 0:
            return None

        info = dict(zip(df["item"], df["value"]))
        result = {
            "manager_name": str(info.get("基金经理", "") or "").strip(),
            "company": str(info.get("基金公司", "") or "").strip(),
            "fund_type": str(info.get("基金类型", "") or "").strip(),
            "establish_date": str(info.get("成立时间", "") or "").strip(),
            "scale": str(info.get("最新规模", "") or "").strip(),
            "benchmark": str(info.get("业绩比较基准", "") or "").strip(),
            "source": "xq",
        }

        # 如果有全量经理列表，补充从业信息
        _ensure_all_managers()
        if _all_managers_cache is not None and result["manager_name"]:
            matches = _all_managers_cache[
                _all_managers_cache["姓名"] == result["manager_name"]
            ]
            if len(matches) > 0:
                row = matches.iloc[0]
                result["career_days"] = int(row.get("累计从业时间", 0) or 0)
                result["career_years"] = round(result["career_days"] / 365, 1)
                result["total_scale"] = float(row.get("现任基金资产总规模", 0) or 0)
                result["best_return"] = float(row.get("现任基金最佳回报", 0) or 0)

        _manager_cache[fund_code] = result
        _cache_ts[fund_code] = now
        return result

    except Exception as e:
        logger.warning(f"获取基金 {fund_code} 经理信息失败: {e}")
        return None


def _ensure_all_managers():
    """确保全量经理列表已加载。"""
    global _all_managers_cache, _all_managers_ts
    now = time.time()
    if _all_managers_cache is not None and now - _all_managers_ts < _ALL_CACHE_TTL:
        return
    try:
        import akshare as ak
        _all_managers_cache = ak.fund_manager_em()
        _all_managers_ts = now
        logger.info(f"加载全量基金经理列表: {len(_all_managers_cache)} 条")
    except Exception as e:
        logger.warning(f"加载全量基金经理列表失败: {e}")


def check_manager_change(fund_code: str, stored_manager: str) -> dict | None:
    """检查基金经理是否变更。

    Args:
        fund_code: 基金代码
        stored_manager: 数据库中存储的经理姓名

    Returns:
        None if 未变更，否则 {"old_manager": ..., "new_manager": ..., "fund_code": ...}
    """
    if not stored_manager:
        return None

    info = get_fund_manager(fund_code)
    if not info or not info.get("manager_name"):
        return None

    current = info["manager_name"].strip()
    stored = stored_manager.strip()

    if current and stored and current != stored:
        return {
            "fund_code": fund_code,
            "old_manager": stored,
            "new_manager": current,
            "company": info.get("company", ""),
        }
    return None


def batch_get_managers(fund_codes: list[str]) -> dict[str, dict]:
    """批量获取基金经理信息。"""
    results = {}
    for code in fund_codes:
        info = get_fund_manager(code)
        if info:
            results[code] = info
    return results
