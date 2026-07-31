"""估值状态筛选器 — 为受益标的附加估值百分位和状态。

阈值：
- undervalued: percentile <= 30
- fair: 30 < percentile <= 60
- overvalued: 60 < percentile <= 80
- expensive: percentile > 80
- unknown: 无数据

估值数据时效检查：snapshot_date 超过 3 天标记 valuation_stale=True（附加字段，不阻塞）。

总开关：alerts.valuation_filter_enabled（默认 false）
"""
import logging
from datetime import datetime, timedelta

from db.config import get_config_bool
from db import list_valuation_indexes

logger = logging.getLogger(__name__)

_VALUATION_FILTER_SWITCH = "alerts.valuation_filter_enabled"

# 估值数据时效阈值（天）
_STALE_DAYS = 3


def apply_valuation_filter(beneficiaries: list[dict]) -> list[dict]:
    """为受益标的附加估值状态。

    阈值：
    - undervalued: percentile <= 30
    - fair: 30 < percentile <= 60
    - overvalued: 60 < percentile <= 80
    - expensive: percentile > 80
    - unknown: 无数据

    总开关：alerts.valuation_filter_enabled（默认 false）
    """
    try:
        if not get_config_bool(_VALUATION_FILTER_SWITCH, False):
            return beneficiaries
    except Exception:
        return beneficiaries

    if not beneficiaries:
        return beneficiaries

    # 构建 index_code → 估值信息映射
    val_map = _build_valuation_map()

    today = datetime.now()
    for b in beneficiaries:
        idx = b.get("index_code", "") or ""
        if not idx:
            b["valuation_status"] = "unknown"
            continue
        info = _lookup(val_map, idx)
        if not info:
            b["valuation_status"] = "unknown"
            continue
        percentile = info.get("percentile")
        snapshot_date = info.get("snapshot_date", "") or ""
        if percentile is None:
            b["valuation_status"] = "unknown"
            continue
        try:
            percentile_val = float(percentile)
        except (TypeError, ValueError):
            b["valuation_status"] = "unknown"
            continue
        b["valuation_percentile"] = percentile_val
        b["valuation_status"] = _classify_percentile(percentile_val)
        # 时效检查（附加字段，不阻塞）
        b["valuation_stale"] = _is_stale(snapshot_date, today)

    return beneficiaries


def _build_valuation_map() -> dict:
    """构建 index_code → 估值信息映射（最新快照）。"""
    val_map: dict[str, dict] = {}
    try:
        rows = list_valuation_indexes()
    except Exception as e:
        logger.warning(f"[valuation_filter] 加载估值数据失败: {e}")
        return val_map

    for r in rows:
        code = r.get("index_code", "") or ""
        if not code:
            continue
        # list_valuation_indexes 返回 latest_date（snapshot_date 别名）
        val_map[code] = {
            "percentile": r.get("percentile"),
            "snapshot_date": r.get("latest_date", "") or "",
            "index_name": r.get("index_name", "") or "",
        }
    return val_map


def _lookup(val_map: dict, index_code: str) -> dict | None:
    """按 index_code 查询估值，尝试带/不带 .CSI 后缀。"""
    if not index_code:
        return None
    if index_code in val_map:
        return val_map[index_code]
    # 尝试去掉 .CSI 后缀
    if index_code.endswith(".CSI"):
        bare = index_code[:-4]
        if bare in val_map:
            return val_map[bare]
    # 尝试加上 .CSI 后缀
    if not index_code.endswith(".CSI"):
        with_csi = f"{index_code}.CSI"
        if with_csi in val_map:
            return val_map[with_csi]
    return None


def _classify_percentile(percentile: float) -> str:
    """按百分位阈值分类估值状态。"""
    if percentile <= 30:
        return "undervalued"
    if percentile <= 60:
        return "fair"
    if percentile <= 80:
        return "overvalued"
    return "expensive"


def _is_stale(snapshot_date: str, today: datetime) -> bool:
    """检查估值数据是否过期（超过 _STALE_DAYS 天）。"""
    if not snapshot_date:
        return True
    try:
        snap = datetime.strptime(snapshot_date[:10], "%Y-%m-%d")
        return (today - snap).days > _STALE_DAYS
    except Exception:
        return True
