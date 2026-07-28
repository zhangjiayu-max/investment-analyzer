"""龙虎榜数据采集 — P1-8 机构席位信号补充。

akshare 接口：
- stock_lhb_detail_em：东方财富龙虎榜详情（按日期范围）
- stock_lhb_stock_statistic_em：龙虎榜个股统计（含机构席位买卖明细）

信号维度：
1. 机构净买入：机构席位净买入额 > 0 为正面信号
2. 机构活跃度：近期上榜次数 + 机构参与率
3. 游资 vs 机构：区分机构席位和游资席位（机构席位名称含"机构专用"）

缓存：模块内 dict 缓存，TTL 由 market.dragon_tiger_cache_ttl 配置（默认 3600s）。
降级：akshare 调用失败返回空数据，不影响主流程。
"""
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    import akshare as ak
    import pandas as pd
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False
    ak = None
    pd = None


# 模块级缓存（TTL 可配置，默认 1 小时）
_DRAGON_TIGER_CACHE: dict[str, tuple[object, float]] = {}
_DEFAULT_CACHE_TTL = 3600  # 1 小时
_DEFAULT_TIMEOUT = 30  # akshare 调用超时秒数


def _safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _get_cached(key: str):
    """获取缓存数据，过期或不存在返回 None。"""
    entry = _DRAGON_TIGER_CACHE.get(key)
    if entry and entry[1] > time.time():
        return entry[0]
    return None


def _set_cached(key: str, value, ttl: int = _DEFAULT_CACHE_TTL):
    """写入缓存。"""
    _DRAGON_TIGER_CACHE[key] = (value, time.time() + ttl)


def _call_akshare_with_timeout(fn, *args, timeout: int = _DEFAULT_TIMEOUT, **kwargs):
    """带超时的 akshare 调用（复用 leading_indicators.akshare_utils 模式）。

    F-akshare 修复：手动管理 executor + shutdown(wait=False, cancel_futures=True)，
    避免 zombie 线程卡死（akshare 内部 requests 无超时，网络异常会无限等待）。
    """
    import concurrent.futures
    fn_name = getattr(fn, "__name__", str(fn))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(f"[dragon_tiger] akshare 超时({timeout}s): {fn_name}")
        return None
    except Exception as e:
        logger.warning(f"[dragon_tiger] akshare 调用失败 {fn_name}: {e}")
        return None
    finally:
        # 关键：wait=False 不等待 zombie 线程，cancel_futures=True 取消未开始的 future
        executor.shutdown(wait=False, cancel_futures=True)


def _get_cache_ttl() -> int:
    """从配置读取缓存 TTL（秒）。"""
    try:
        from db.config import get_config_int
        return get_config_int("market.dragon_tiger_cache_ttl", _DEFAULT_CACHE_TTL)
    except Exception:
        return _DEFAULT_CACHE_TTL


def _is_enabled() -> bool:
    """龙虎榜开关（默认开启）。"""
    try:
        from db.config import get_config_bool
        return get_config_bool("market.dragon_tiger_enabled", True)
    except Exception:
        return True


def _pick_col(df, candidates: list[str]) -> str | None:
    """兼容不同 akshare 版本的列名差异，返回首个命中的列名。"""
    if df is None:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    return None


def fetch_dragon_tiger(days: int = 5) -> list[dict]:
    """获取近N天龙虎榜数据。

    流程：
    1. 调 stock_lhb_detail_em 拿近 N 天上榜个股列表 + 总净买额
    2. 调 stock_lhb_stock_statistic_em 拿机构席位买卖明细（近一月统计窗口）
    3. 按 code 合并，回填 institutional_net_buy / 机构席位计数 / 游资净买入

    Returns:
        [
            {
                "code": "600000",
                "name": "浦发银行",
                "date": "2026-07-28",
                "reason": "日跌幅偏离值达7%",
                "institutional_net_buy": 12345678.0,  # 机构净买入额
                "institutional_buy_count": 3,          # 机构买入席位数
                "institutional_sell_count": 1,         # 机构卖出席位数
                "hot_money_net_buy": -5000000.0,       # 游资净买入额
                "total_net_buy": 7345678.0,            # 总净买入额
            },
            ...
        ]
    """
    if not _HAS_AKSHARE or pd is None:
        return []

    cache_key = f"dragon_tiger_detail:{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        # 东方财富龙虎榜详情（按日期范围）
        df = _call_akshare_with_timeout(
            ak.stock_lhb_detail_em,
            start_date=start_date,
            end_date=end_date,
            timeout=_DEFAULT_TIMEOUT,
        )
        if df is None or len(df) == 0:
            result: list[dict] = []
            _set_cached(cache_key, result, ttl=_get_cache_ttl())
            return result

        code_col = _pick_col(df, ["代码", "股票代码"])
        if code_col is None:
            logger.warning("[dragon_tiger] 详情接口未找到代码列")
            return []
        name_col = _pick_col(df, ["名称", "股票名称"])
        date_col = _pick_col(df, ["上榜日", "上榜日期"])
        reason_col = _pick_col(df, ["解读", "上榜原因"])
        total_buy_col = _pick_col(df, ["龙虎榜买入额", "买入额"])
        total_sell_col = _pick_col(df, ["龙虎榜卖出额", "卖出额"])
        net_buy_col = _pick_col(df, ["龙虎榜净买额", "净买额"])

        result = []
        for _, r in df.iterrows():
            code = str(r.get(code_col, "")).strip()
            if not code:
                continue
            item = {
                "code": code,
                "name": str(r.get(name_col, "")).strip() if name_col else "",
                "date": str(r.get(date_col, "")).strip() if date_col else "",
                "reason": str(r.get(reason_col, "")).strip() if reason_col else "",
                "institutional_net_buy": 0.0,  # 详情接口不直接提供，由统计接口补充
                "institutional_buy_count": 0,
                "institutional_sell_count": 0,
                "hot_money_net_buy": 0.0,
                "total_net_buy": _safe_float(r.get(net_buy_col, 0)) if net_buy_col else 0.0,
                "total_buy": _safe_float(r.get(total_buy_col, 0)) if total_buy_col else 0.0,
                "total_sell": _safe_float(r.get(total_sell_col, 0)) if total_sell_col else 0.0,
            }
            result.append(item)

        # 用 stock_lhb_stock_statistic_em 补充机构席位信息
        result = _enrich_with_institutional_stats(result)

        _set_cached(cache_key, result, ttl=_get_cache_ttl())
        return result
    except Exception as e:
        logger.warning(f"[dragon_tiger] 获取龙虎榜详情失败: {e}")
        return []


def _enrich_with_institutional_stats(items: list[dict]) -> list[dict]:
    """用 stock_lhb_stock_statistic_em 补充机构席位信息。

    统计接口（symbol="近一月"）返回字段（不同 akshare 版本可能略有差异）：
    - 机构买入净额 / 机构买入总额 / 机构卖出总额
    - 机构买入席位 / 机构卖出席位
    - 龙虎榜净买额

    游资净买入 = 龙虎榜净买额 - 机构买入净额
    """
    if not items or not _HAS_AKSHARE:
        return items

    try:
        # symbol: "近一月" / "近三月" / "近六月" / "近一年"
        # 取近一月作为统计窗口，覆盖大多数近期上榜个股
        df = _call_akshare_with_timeout(
            ak.stock_lhb_stock_statistic_em,
            symbol="近一月",
            timeout=_DEFAULT_TIMEOUT,
        )
        if df is None or len(df) == 0:
            return items

        code_col = _pick_col(df, ["代码", "股票代码"])
        if code_col is None:
            return items

        inst_net_col = _pick_col(df, ["机构买入净额", "机构净买额"])
        inst_buy_col = _pick_col(df, ["机构买入总额", "机构买入额"])
        inst_sell_col = _pick_col(df, ["机构卖出总额", "机构卖出额"])
        inst_buy_seat_col = _pick_col(df, ["机构买入席位", "机构买入次数"])
        inst_sell_seat_col = _pick_col(df, ["机构卖出席位", "机构卖出次数"])
        total_net_col = _pick_col(df, ["龙虎榜净买额", "净买额"])

        # 按 code 构建索引
        stat_map: dict[str, dict] = {}
        for _, r in df.iterrows():
            code = str(r.get(code_col, "")).strip()
            if not code:
                continue
            inst_net = _safe_float(r.get(inst_net_col, 0)) if inst_net_col else 0.0
            total_net = _safe_float(r.get(total_net_col, 0)) if total_net_col else 0.0
            stat_map[code] = {
                "institutional_net_buy": inst_net,
                "institutional_buy_count": int(_safe_float(r.get(inst_buy_seat_col, 0))) if inst_buy_seat_col else 0,
                "institutional_sell_count": int(_safe_float(r.get(inst_sell_seat_col, 0))) if inst_sell_seat_col else 0,
                "total_net_buy": total_net,
                "hot_money_net_buy": total_net - inst_net,  # 游资净买入 = 总净买入 - 机构净买入
            }

        # 回填到 items
        for item in items:
            stat = stat_map.get(item["code"])
            if not stat:
                continue
            item["institutional_net_buy"] = stat["institutional_net_buy"]
            item["institutional_buy_count"] = stat["institutional_buy_count"]
            item["institutional_sell_count"] = stat["institutional_sell_count"]
            item["hot_money_net_buy"] = stat["hot_money_net_buy"]
            # 详情接口未拿到 total_net_buy 时，用统计接口的值兜底
            if item["total_net_buy"] == 0.0:
                item["total_net_buy"] = stat["total_net_buy"]
        return items
    except Exception as e:
        logger.warning(f"[dragon_tiger] 补充机构席位信息失败: {e}")
        return items


def get_dragon_tiger_signals(days: int = 5) -> dict:
    """生成龙虎榜信号汇总。

    Returns:
        {
            "top_institutional_buys": [...],   # 机构净买入TOP10
            "top_institutional_sells": [...],  # 机构净卖出TOP10
            "active_stocks": [...],            # 近期活跃个股（多次上榜）
            "institutional_active_rate": float, # 机构参与率
            "signal_date": str,
            "summary": str,                    # 文字摘要
            "total_count": int,                # 上榜记录总数
        }
    """
    cache_key = f"dragon_tiger_signals:{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    items = fetch_dragon_tiger(days=days)
    signal_date = datetime.now().strftime("%Y-%m-%d")

    if not items:
        result = {
            "top_institutional_buys": [],
            "top_institutional_sells": [],
            "active_stocks": [],
            "institutional_active_rate": 0.0,
            "signal_date": signal_date,
            "summary": "近期无龙虎榜数据",
            "total_count": 0,
        }
        _set_cached(cache_key, result, ttl=_get_cache_ttl())
        return result

    # 按 code 聚合（同一股多次上榜合并机构净买额）
    code_agg: dict[str, dict] = {}
    for it in items:
        code = it.get("code", "")
        if not code:
            continue
        if code not in code_agg:
            code_agg[code] = {
                "code": code,
                "name": it.get("name", ""),
                "appear_count": 0,
                "total_net_buy": 0.0,
                "institutional_net_buy": 0.0,
                "institutional_buy_count": 0,
                "institutional_sell_count": 0,
                "hot_money_net_buy": 0.0,
            }
        agg = code_agg[code]
        agg["appear_count"] += 1
        agg["total_net_buy"] += it.get("total_net_buy", 0.0)
        agg["institutional_net_buy"] += it.get("institutional_net_buy", 0.0)
        agg["institutional_buy_count"] += it.get("institutional_buy_count", 0)
        agg["institutional_sell_count"] += it.get("institutional_sell_count", 0)
        agg["hot_money_net_buy"] += it.get("hot_money_net_buy", 0.0)

    aggregated = list(code_agg.values())

    # 机构净买入 TOP10（按 institutional_net_buy 降序，仅正值）
    sorted_buys = sorted(aggregated, key=lambda x: x["institutional_net_buy"], reverse=True)
    top_buys = [x for x in sorted_buys if x["institutional_net_buy"] > 0][:10]

    # 机构净卖出 TOP10（按 institutional_net_buy 升序，仅负值）
    top_sells = [x for x in sorted_buys if x["institutional_net_buy"] < 0][:10]

    # 活跃个股：按上榜次数降序
    active_stocks = sorted(aggregated, key=lambda x: x["appear_count"], reverse=True)[:10]

    # 机构参与率：有机构席位活动的个股数 / 总个股数
    inst_involved = sum(
        1 for x in aggregated
        if x["institutional_buy_count"] > 0 or x["institutional_sell_count"] > 0
    )
    total_unique = len(aggregated)
    institutional_active_rate = round(inst_involved / total_unique, 4) if total_unique > 0 else 0.0

    # 文字摘要
    summary_parts = []
    if top_buys:
        top_buy_names = [
            f"{x['name']}({_format_amount(x['institutional_net_buy'])})"
            for x in top_buys[:3]
        ]
        summary_parts.append(f"机构净买入TOP3: {', '.join(top_buy_names)}")
    if top_sells:
        top_sell_names = [
            f"{x['name']}({_format_amount(x['institutional_net_buy'])})"
            for x in top_sells[:3]
        ]
        summary_parts.append(f"机构净卖出TOP3: {', '.join(top_sell_names)}")
    summary_parts.append(f"机构参与率 {institutional_active_rate:.0%}")
    summary = " | ".join(summary_parts) if summary_parts else "无显著机构信号"

    result = {
        "top_institutional_buys": top_buys,
        "top_institutional_sells": top_sells,
        "active_stocks": active_stocks,
        "institutional_active_rate": institutional_active_rate,
        "signal_date": signal_date,
        "summary": summary,
        "total_count": len(items),
    }
    _set_cached(cache_key, result, ttl=_get_cache_ttl())
    return result


def _format_amount(v: float) -> str:
    """金额格式化（亿/万）。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "0"
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v/1e4:.2f}万"
    return f"{v:.0f}"


def get_dragon_tiger_for_stock(stock_code: str, days: int = 30) -> dict | None:
    """查询单只个股的龙虎榜历史。

    Args:
        stock_code: 股票代码（如 "600000"）
        days: 回看天数（默认 30）

    Returns:
        {
            "code": str,
            "name": str,
            "records": [{code, name, date, reason, total_net_buy}, ...],
            "appear_count": int,
            "total_net_buy_sum": float,
        }
        无数据返回 None。
    """
    if not stock_code or not _HAS_AKSHARE:
        return None

    cache_key = f"dragon_tiger_stock:{stock_code}:{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = _call_akshare_with_timeout(
            ak.stock_lhb_detail_em,
            start_date=start_date,
            end_date=end_date,
            timeout=_DEFAULT_TIMEOUT,
        )
        if df is None or len(df) == 0:
            return None

        code_col = _pick_col(df, ["代码", "股票代码"])
        if code_col is None:
            return None

        # 筛选目标个股
        df = df[df[code_col].astype(str).str.strip() == stock_code.strip()]
        if len(df) == 0:
            return None

        name_col = _pick_col(df, ["名称", "股票名称"])
        date_col = _pick_col(df, ["上榜日", "上榜日期"])
        reason_col = _pick_col(df, ["解读", "上榜原因"])
        net_buy_col = _pick_col(df, ["龙虎榜净买额", "净买额"])

        records = []
        for _, r in df.iterrows():
            records.append({
                "code": stock_code,
                "name": str(r.get(name_col, "")).strip() if name_col else "",
                "date": str(r.get(date_col, "")).strip() if date_col else "",
                "reason": str(r.get(reason_col, "")).strip() if reason_col else "",
                "total_net_buy": _safe_float(r.get(net_buy_col, 0)) if net_buy_col else 0.0,
            })

        # 按日期降序
        records.sort(key=lambda x: x.get("date", ""), reverse=True)

        result = {
            "code": stock_code,
            "name": records[0].get("name", "") if records else "",
            "records": records,
            "appear_count": len(records),
            "total_net_buy_sum": round(sum(r.get("total_net_buy", 0.0) for r in records), 2),
        }
        _set_cached(cache_key, result, ttl=_get_cache_ttl())
        return result
    except Exception as e:
        logger.warning(f"[dragon_tiger] 查询个股 {stock_code} 龙虎榜失败: {e}")
        return None
