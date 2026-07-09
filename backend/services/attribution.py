"""收益归因分析 — Brinson 三因素分解。

把组合收益拆解为：选股效应 + 择时效应 + 交互效应，并按品类（fund_category）归因。
纯算法层，不调用 LLM。

基准说明（无外部基准时的默认约定）：
- 基准权重 wb 默认取等权（1/N）；可通过 benchmark_weights 配置覆盖。
- 基准收益 Rb 默认取各品类收益率的等权平均（中性基准），代表"无选股能力"的基准收益；
  若 benchmark_weights 同时提供每品类 return，则使用用户指定的基准收益。
  用户也可通过 system_config 的 `attribution.benchmark_weights` 注入真实基准。
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import datetime

from db import get_config, list_holdings, list_transactions
from db.portfolio import list_snapshots

logger = logging.getLogger(__name__)


# 品类中文标签映射
_CATEGORY_LABELS = {
    "equity": "股票型",
    "bond": "债券型",
    "bond_index": "债券指数型",
    "index": "指数型",
    "hybrid": "混合型",
    "money_market": "货币型",
    "convertible_bond": "可转债型",
    "qdii": "QDII",
    "": "未分类",
}


def _safe_float(v, default: float = 0.0) -> float:
    """安全的浮点转换，None / 非数值返回默认值。"""
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _category_label(cat: str) -> str:
    """品类 key → 中文标签。"""
    return _CATEGORY_LABELS.get(cat or "", cat or "未分类")


def _parse_holdings_json(snapshot: dict) -> list[dict]:
    """从快照解析 holdings_json 为持仓列表。"""
    raw = snapshot.get("holdings_json")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _fund_category_map(holdings: list[dict]) -> dict:
    """构建 fund_code -> fund_category 映射（基于当前持仓）。"""
    m: dict[str, str] = {}
    for h in holdings:
        code = h.get("fund_code") or ""
        if code:
            m[code] = h.get("fund_category") or ""
    return m


def _category_value_map(holding_list: list[dict]) -> dict:
    """按 fund_category 聚合市值。返回 {category: total_value}。"""
    result: dict[str, float] = {}
    for h in holding_list:
        cat = h.get("fund_category") or ""
        val = _safe_float(h.get("current_value"))
        result[cat] = result.get(cat, 0.0) + val
    return result


def _net_cash_flow_by_category(transactions: list[dict], cat_map: dict) -> dict:
    """按品类汇总净现金流。买入为净投入（+），卖出为回收（-）。"""
    result: dict[str, float] = {}
    for tx in transactions:
        code = tx.get("fund_code") or ""
        cat = cat_map.get(code, "")
        ttype = (tx.get("transaction_type") or "").lower()
        amount = _safe_float(tx.get("amount"))
        if ttype == "buy":
            result[cat] = result.get(cat, 0.0) + amount
        elif ttype == "sell":
            result[cat] = result.get(cat, 0.0) - amount
    return result


def _empty_result(start_date: str, end_date: str, reason: str = "") -> dict:
    """数据不足时的空结果。"""
    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_return": 0.0,
        "selection_effect": 0.0,
        "timing_effect": 0.0,
        "interaction_effect": 0.0,
        "category_attribution": [],
        "top_contributors": [],
        "top_drags": [],
        "has_snapshots": False,
        "reason": reason,
    }


def _compute_holding_returns(
    holdings: list[dict],
    start_snap: dict | None,
    end_snap: dict | None,
    transactions: list[dict],
) -> list[dict]:
    """计算单只持仓的期间收益率。

    优先用快照期初/期末市值 + 期间净现金流计算；无快照时退化为当前盈亏率代理。
    """
    # 按 fund_code 汇总交易现金流
    tx_flow: dict[str, float] = {}
    for tx in transactions:
        code = tx.get("fund_code") or ""
        ttype = (tx.get("transaction_type") or "").lower()
        amount = _safe_float(tx.get("amount"))
        if ttype == "buy":
            tx_flow[code] = tx_flow.get(code, 0.0) + amount
        elif ttype == "sell":
            tx_flow[code] = tx_flow.get(code, 0.0) - amount

    start_map: dict[str, float] = {}
    end_map: dict[str, float] = {}
    if start_snap:
        for h in _parse_holdings_json(start_snap):
            code = h.get("fund_code") or ""
            if code:
                start_map[code] = _safe_float(h.get("current_value"))
    if end_snap:
        for h in _parse_holdings_json(end_snap):
            code = h.get("fund_code") or ""
            if code:
                end_map[code] = _safe_float(h.get("current_value"))

    total_value = sum(_safe_float(h.get("current_value")) for h in holdings) or 1.0

    results = []
    for h in holdings:
        code = h.get("fund_code") or ""
        if not code:
            continue
        if _safe_float(h.get("shares")) <= 0:
            continue
        sv = start_map.get(code, 0.0)
        ev = end_map.get(code, _safe_float(h.get("current_value")))
        cf = tx_flow.get(code, 0.0)
        if sv > 0:
            ret = (ev - sv - cf) / sv
        elif cf > 0:
            # 期初无仓位，期间新建仓
            ret = (ev - cf) / cf
        else:
            # 无期间数据，用当前盈亏率作为代理
            ret = _safe_float(h.get("profit_rate"), 0.0)
        value = ev if ev > 0 else _safe_float(h.get("current_value"))
        results.append({
            "fund_code": code,
            "fund_name": h.get("fund_name") or code,
            "category": _category_label(h.get("fund_category") or ""),
            "category_key": h.get("fund_category") or "",
            "return": ret,
            "value": value,
            "contribution": (value / total_value) * ret if total_value > 0 else 0.0,
        })
    return results


def _format_contrib(h: dict) -> dict:
    """格式化 Top 贡献/拖累条目。"""
    return {
        "fund_code": h["fund_code"],
        "fund_name": h["fund_name"],
        "category": h["category"],
        "category_key": h.get("category_key", ""),
        "return": round(h["return"], 6),
        "contribution": round(h["contribution"], 6),
        "value": round(h["value"], 2),
    }


def brinson_attribution(
    holdings: list[dict],
    transactions: list[dict],
    benchmark_weights: dict | None,
    start_date: str,
    end_date: str,
    user_id: str = "default",
) -> dict:
    """Brinson 三因素收益归因分解。

    Args:
        holdings: 期初/当前持仓列表（list_holdings 结果）
        transactions: 期间交易记录（list_transactions 结果）
        benchmark_weights: 基准权重。支持两种格式：
            - {category: weight}
            - {category: {"weight": w, "return": r}}
            None 时等权，基准收益取各品类收益等权平均。
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        user_id: 用户 ID（用于读取该用户快照）

    Returns:
        {
            "total_return": float,
            "selection_effect": float,
            "timing_effect": float,
            "interaction_effect": float,
            "category_attribution": [...],
            "top_contributors": [...],
            "top_drags": [...],
        }
    """
    # 数据不足时返回空结果而非报错
    if not holdings:
        return _empty_result(start_date, end_date, reason="无持仓数据")

    cat_map = _fund_category_map(holdings)

    # 获取期间快照（含 holdings_json），list_snapshots 按日期倒序返回
    try:
        snapshots = list_snapshots(
            start_date=start_date, end_date=end_date, user_id=user_id, limit=1000
        )
    except Exception as e:
        logger.warning(f"[attribution] 读取快照失败，退化为截面归因: {e}")
        snapshots = []

    start_snap = snapshots[-1] if snapshots else None  # 最早一条
    end_snap = snapshots[0] if snapshots else None      # 最晚一条

    # ── 1. 各品类期初/期末市值 ──
    if start_snap:
        start_holdings = _parse_holdings_json(start_snap)
        # 旧快照可能缺 fund_category，用当前持仓映射补齐
        for h in start_holdings:
            code = h.get("fund_code") or ""
            if not h.get("fund_category") and code in cat_map:
                h["fund_category"] = cat_map[code]
        start_cat_values = _category_value_map(start_holdings)
        end_holdings = _parse_holdings_json(end_snap) if end_snap else []
        for h in end_holdings:
            code = h.get("fund_code") or ""
            if not h.get("fund_category") and code in cat_map:
                h["fund_category"] = cat_map[code]
        end_cat_values = _category_value_map(end_holdings)
    else:
        # 无快照：退化为当前持仓做"截面"归因
        active = [h for h in holdings if _safe_float(h.get("shares")) > 0]
        start_cat_values = _category_value_map(active)
        end_cat_values = start_cat_values

    total_start_value = sum(start_cat_values.values())
    if total_start_value <= 0:
        return _empty_result(start_date, end_date, reason="期初市值为 0")

    # 期初权重 wp
    wp = {cat: val / total_start_value for cat, val in start_cat_values.items()}

    # ── 2. 净现金流（按品类）──
    cash_flows = _net_cash_flow_by_category(transactions, cat_map)

    # ── 3. 各品类收益率 Rp = (期末 - 期初 - 净投入) / 期初 ──
    Rp: dict[str, float] = {}
    all_categories = set(start_cat_values.keys()) | set(end_cat_values.keys())
    for cat in all_categories:
        sv = start_cat_values.get(cat, 0.0)
        ev = end_cat_values.get(cat, 0.0)
        cf = cash_flows.get(cat, 0.0)
        if sv > 0:
            Rp[cat] = (ev - sv - cf) / sv
        elif cf > 0:
            # 期初无仓位，期间新建仓
            Rp[cat] = (ev - cf) / cf
        else:
            Rp[cat] = 0.0

    # ── 4. 基准权重 wb 和基准收益 Rb ──
    categories = list(Rp.keys())
    n = len(categories)
    wb: dict[str, float] = {}
    Rb: dict[str, float] = {}

    if benchmark_weights and n > 0:
        bw_total = 0.0
        has_benchmark_return = False
        for cat in categories:
            spec = benchmark_weights.get(cat)
            if isinstance(spec, dict):
                wb[cat] = _safe_float(spec.get("weight"))
                r = spec.get("return")
                if r is not None:
                    Rb[cat] = _safe_float(r)
                    has_benchmark_return = True
                else:
                    Rb[cat] = Rp.get(cat, 0.0)
            else:
                wb[cat] = _safe_float(spec)
                Rb[cat] = Rp.get(cat, 0.0)
            bw_total += wb[cat]
        # 归一化基准权重
        if bw_total > 0:
            wb = {c: w / bw_total for c, w in wb.items()}
        else:
            wb = {c: 1.0 / n for c in categories}
        # 未提供基准收益时，用各品类收益等权平均作为中性基准
        if not has_benchmark_return:
            avg_ret = sum(Rp.values()) / n
            Rb = {c: avg_ret for c in categories}
    elif n > 0:
        # 默认：等权 + 中性基准收益（各品类收益等权平均）
        avg_ret = sum(Rp.values()) / n
        wb = {c: 1.0 / n for c in categories}
        Rb = {c: avg_ret for c in categories}

    # ── 5. Brinson 三因素 ──
    selection_effect = sum(
        wp.get(c, 0.0) * (Rp.get(c, 0.0) - Rb.get(c, 0.0)) for c in categories
    )
    timing_effect = sum(
        (wp.get(c, 0.0) - wb.get(c, 0.0)) * Rb.get(c, 0.0) for c in categories
    )
    interaction_effect = sum(
        (wp.get(c, 0.0) - wb.get(c, 0.0)) * (Rp.get(c, 0.0) - Rb.get(c, 0.0))
        for c in categories
    )

    # 组合总收益 = Σ wp × Rp
    total_return = sum(wp.get(c, 0.0) * Rp.get(c, 0.0) for c in categories)

    # ── 6. 品类归因 ──
    category_attribution = []
    for cat in categories:
        w = wp.get(cat, 0.0)
        r = Rp.get(cat, 0.0)
        contribution = w * r  # 对总收益的贡献度
        category_attribution.append({
            "category": _category_label(cat),
            "category_key": cat or "unknown",
            "return": round(r, 6),
            "contribution": round(contribution, 6),
            "weight": round(w, 6),
            "benchmark_weight": round(wb.get(cat, 0.0), 6),
            "benchmark_return": round(Rb.get(cat, 0.0), 6),
            "start_value": round(start_cat_values.get(cat, 0.0), 2),
            "end_value": round(end_cat_values.get(cat, 0.0), 2),
            "net_cash_flow": round(cash_flows.get(cat, 0.0), 2),
        })
    # 按贡献度降序
    category_attribution.sort(key=lambda x: x["contribution"], reverse=True)

    # ── 7. Top 贡献 / 拖累（按单只持仓收益排序）──
    holding_returns = _compute_holding_returns(holdings, start_snap, end_snap, transactions)
    sorted_desc = sorted(holding_returns, key=lambda x: x["return"], reverse=True)
    top_contributors = [_format_contrib(h) for h in sorted_desc if h["return"] > 0][:10]
    top_drags = [_format_contrib(h) for h in reversed(sorted_desc) if h["return"] < 0][:10]

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_return": round(total_return, 6),
        "selection_effect": round(selection_effect, 6),
        "timing_effect": round(timing_effect, 6),
        "interaction_effect": round(interaction_effect, 6),
        "category_attribution": category_attribution,
        "top_contributors": top_contributors,
        "top_drags": top_drags,
        "has_snapshots": bool(snapshots),
    }


def _load_benchmark_weights() -> dict | None:
    """从 system_config 读取基准权重配置；不存在或解析失败返回 None（等权）。"""
    try:
        raw = get_config("attribution.benchmark_weights", "")
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"[attribution] 读取基准权重配置失败: {e}")
    return None


def get_attribution_report(user_id: str, start_date: str, end_date: str) -> dict:
    """聚合调用：从 db 取数据并执行 Brinson 归因。

    Args:
        user_id: 用户 ID
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
    """
    try:
        holdings = list_holdings(user_id=user_id)
        transactions = list_transactions(
            user_id=user_id, start_date=start_date, end_date=end_date, limit=10000
        )
        benchmark_weights = _load_benchmark_weights()
        return brinson_attribution(
            holdings, transactions, benchmark_weights, start_date, end_date, user_id=user_id
        )
    except Exception as e:
        logger.exception(f"[attribution] get_attribution_report 失败: {e}")
        return _empty_result(start_date, end_date, reason=f"计算失败: {e}")


def _parse_period(period: str) -> tuple[str, str]:
    """解析期间字符串为 (start_date, end_date)。

    支持：YYYY / YYYY-MM / YYYY-H1|H2 / YYYY-Q1..Q4。
    """
    if not period:
        return "", ""
    p = period.strip().upper()
    try:
        if p.endswith("-H1"):
            year = int(p[:-3])
            return f"{year}-01-01", f"{year}-06-30"
        if p.endswith("-H2"):
            year = int(p[:-3])
            return f"{year}-07-01", f"{year}-12-31"
        if "-Q" in p:
            year_str, q_str = p.split("-Q", 1)
            year = int(year_str)
            q = int(q_str)
            if q < 1 or q > 4:
                return "", ""
            start_month = (q - 1) * 3 + 1
            end_month = q * 3
            last_day = calendar.monthrange(year, end_month)[1]
            return f"{year}-{start_month:02d}-01", f"{year}-{end_month:02d}-{last_day:02d}"
        if len(p) == 4 and p.isdigit():
            year = int(p)
            return f"{year}-01-01", f"{year}-12-31"
        if len(p) == 7 and p[4] == "-" and p[:4].isdigit() and p[5:7].isdigit():
            year = int(p[:4])
            month = int(p[5:7])
            if month < 1 or month > 12:
                return "", ""
            last_day = calendar.monthrange(year, month)[1]
            return f"{p}-01", f"{year}-{month:02d}-{last_day:02d}"
    except (ValueError, IndexError):
        return "", ""
    return "", ""


def get_category_attribution(user_id: str, period: str) -> list[dict]:
    """按品类归因。

    Args:
        user_id: 用户 ID
        period: 期间，如 '2026-H1' / '2026-Q1' / '2026' / '2026-03'

    Returns:
        品类归因列表 [{category, return, contribution, weight, ...}, ...]
    """
    start_date, end_date = _parse_period(period)
    if not start_date:
        return []
    try:
        report = get_attribution_report(user_id, start_date, end_date)
        return report.get("category_attribution", [])
    except Exception as e:
        logger.exception(f"[attribution] get_category_attribution 失败: {e}")
        return []


def get_contributors(
    user_id: str,
    limit: int = 10,
    order: str = "desc",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """获取 Top 贡献（order=desc）或 Top 拖累（order=asc）持仓。

    Args:
        user_id: 用户 ID
        limit: 返回条数
        order: 'desc' 返回贡献最大的，'asc' 返回拖累最大的
        start_date: 起始日期，默认当年 1 月 1 日
        end_date: 结束日期，默认今天
    """
    if not start_date or not end_date:
        today = datetime.now()
        start_date = start_date or f"{today.year}-01-01"
        end_date = end_date or today.strftime("%Y-%m-%d")
    report = get_attribution_report(user_id, start_date, end_date)
    if order.lower() == "asc":
        items = report.get("top_drags", [])
    else:
        items = report.get("top_contributors", [])
    return items[:limit] if limit > 0 else items
