"""行情数据获取模块 — 基于 akshare 获取股票、基金、指数数据

增强特性：
- akshare 失败时自动降级到东方财富 HTTP API
- 统一错误处理和日志
- 5 分钟 TTL 缓存
"""

import json
import logging
import ssl
import time
from datetime import datetime, timedelta
from functools import wraps

import requests

logger = logging.getLogger(__name__)

# ── akshare 可选导入（降级时不依赖） ──
try:
    import akshare as ak
    _HAS_AKSHARE = True
except ImportError:
    _HAS_AKSHARE = False
    logger.warning("akshare 未安装，将仅使用东方财富 HTTP API")

try:
    import pandas as pd
except ImportError:
    pd = None

# ── 行情数据 TTL 缓存（akshare 调用慢且不稳定，5 分钟内复用）──
_market_cache = {}
_MARKET_CACHE_TTL = 300  # 5 分钟


def _get_cached(key):
    """获取缓存数据，过期或不存在返回 None。"""
    entry = _market_cache.get(key)
    if entry and entry[1] > time.time():
        logger.debug(f"行情缓存命中: {key}")
        return entry[0]
    return None


def _set_cached(key, data):
    """写入缓存。"""
    _market_cache[key] = (data, time.time() + _MARKET_CACHE_TTL)


def _safe_float(val) -> float | None:
    """安全转换为 float。"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ── 东方财富 HTTP 降级 API ──────────────────────────────

def _eastmoney_index_spot() -> list[dict] | None:
    """东方财富指数实时行情 HTTP API（akshare 降级）。"""
    try:
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "fltt": 2,
            "fields": "f2,f3,f4,f12,f14,f6",
            "secids": "1.000001,0.399001,0.399006,1.000300,1.000905,1.000852,0.899050,1.000016,0.399303",
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        results = []
        for item in (data.get("data", {}).get("diff", []) or []):
            results.append({
                "name": item.get("f14", ""),
                "code": item.get("f12", ""),
                "price": _safe_float(item.get("f2")),
                "change_pct": _safe_float(item.get("f3")),
                "volume_yi": round(_safe_float(item.get("f6", 0)) / 1e8, 0) if item.get("f6") else 0,
            })
        return results if results else None
    except Exception as e:
        logger.warning(f"东方财富指数行情降级失败: {e}")
        return None


def _eastmoney_fund_nav(fund_code: str) -> pd.DataFrame | None:
    """东方财富基金净值 HTTP API（akshare 降级）。"""
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
        if not rows or pd is None:
            return None
        records = []
        for r in rows:
            records.append({
                "净值日期": r.get("FSRQ", ""),
                "累计净值": _safe_float(r.get("LJJZ")),
            })
        return pd.DataFrame(records)
    except Exception as e:
        logger.warning(f"东方财富基金净值降级失败 ({fund_code}): {e}")
        return None


def _eastmoney_stock_info(symbol: str) -> dict:
    """东方财富个股信息 HTTP API（akshare 降级）。"""
    result = {"code": symbol, "name": ""}
    try:
        # 个股基本面
        url = f"https://push2.eastmoney.com/api/qt/stock/get"
        market = "1" if symbol.startswith(("6", "5")) else "0"
        params = {
            "secid": f"{market}.{symbol}",
            "fields": "f57,f58,f9,f23,f116,f117,f127",
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json().get("data", {})
        if data:
            result["name"] = data.get("f58", "")
            result["pe"] = _safe_float(data.get("f9"))
            result["pb"] = _safe_float(data.get("f23"))
            result["market_cap"] = round(_safe_float(data.get("f116", 0)) / 1e8, 2) if data.get("f116") else None
            result["circulating_cap"] = round(_safe_float(data.get("f117", 0)) / 1e8, 2) if data.get("f117") else None
    except Exception as e:
        logger.warning(f"东方财富个股信息降级失败 ({symbol}): {e}")
    return result



def get_stock_history(symbol: str, days: int = 365) -> pd.DataFrame:
    """
    获取 A 股历史行情。

    参数:
        symbol: 股票代码，如 "600519"
        days: 获取最近多少天的数据

    返回:
        DataFrame，列: 日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
    """
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily", start_date=start, end_date=end, adjust="qfq"
    )
    return df


def get_fund_nav(fund_code: str) -> pd.DataFrame:
    """
    获取开放式基金净值数据（akshare 优先，东方财富降级）。

    参数:
        fund_code: 基金代码，如 "110011"

    返回:
        DataFrame，列: 净值日期/单位净值/累计净值/日增长率
    """
    if not _HAS_AKSHARE or pd is None:
        df = _eastmoney_fund_nav(fund_code)
        if df is not None:
            return df
        return pd.DataFrame(columns=["净值日期", "累计净值"]) if pd else pd.DataFrame()

    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
        df.columns = ["净值日期", "累计净值"]
        return df
    except Exception as e:
        logger.warning(f"akshare 基金净值失败 ({fund_code})，降级到东方财富: {e}")
        df = _eastmoney_fund_nav(fund_code)
        if df is not None:
            return df
        return pd.DataFrame(columns=["净值日期", "累计净值"])


def get_stock_info(symbol: str) -> dict:
    """
    获取个股基本信息和基本面。

    返回:
        {
            "name": str,           # 股票名称
            "code": str,
            "pe": float,           # 市盈率
            "pb": float,           # 市净率
            "market_cap": float,   # 总市值（亿）
            "circulating_cap": float,  # 流通市值（亿）
            "roe": float,          # ROE
            "industry": str,       # 所属行业
        }
    """
    try:
        info_df = ak.stock_individual_info_em(symbol=symbol)
        info_dict = dict(zip(info_df["item"], info_df["value"]))
    except Exception:
        info_dict = {}

    try:
        spot_df = ak.stock_zh_a_spot_em()
        row = spot_df[spot_df["代码"] == symbol]
        if not row.empty:
            row = row.iloc[0]
            info_dict.update({
                "name": row.get("名称", ""),
                "code": symbol,
                "pe": _to_float(row.get("市盈率-动态", None)),
                "pb": _to_float(row.get("市净率", None)),
                "market_cap": _to_float(row.get("总市值", None)),
                "circulating_cap": _to_float(row.get("流通市值", None)),
            })
    except Exception:
        pass

    # akshare 全部失败时降级到东方财富
    if not info_dict.get("name"):
        fallback = _eastmoney_stock_info(symbol)
        if fallback.get("name"):
            info_dict.update(fallback)

    info_dict.setdefault("code", symbol)
    info_dict.setdefault("name", info_dict.get("股票简称", ""))
    return info_dict


def get_index_valuation(index_code: str = "000300") -> dict:
    """
    获取指数估值数据（PE/PB/百分位）。

    参数:
        index_code: 指数代码，如 "000300"(沪深300)、"000905"(中证500)、"399006"(创业板指)

    返回:
        {
            "index_name": str,
            "pe": float,
            "pb": float,
            "pe_percentile": float,  # PE 百分位 (0-1)
            "pb_percentile": float,  # PB 百分位 (0-1)
            "dividend_yield": float, # 股息率
        }
    """
    result = {
        "index_name": index_code,
        "pe": None,
        "pb": None,
        "pe_percentile": None,
        "pb_percentile": None,
        "dividend_yield": None,
    }

    try:
        # 尝试从乐咕乐股获取指数估值
        df = ak.index_value_hist_funddb(
            symbol=_index_name_map(index_code), indicator="市盈率"
        )
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            result["pe"] = _to_float(latest.get("市盈率"))
            result["pe_percentile"] = _to_float(latest.get("百分位"))
            result["index_name"] = _index_name_map(index_code)
    except Exception:
        pass

    try:
        df_pb = ak.index_value_hist_funddb(
            symbol=_index_name_map(index_code), indicator="市净率"
        )
        if df_pb is not None and not df_pb.empty:
            latest = df_pb.iloc[-1]
            result["pb"] = _to_float(latest.get("市净率"))
            result["pb_percentile"] = _to_float(latest.get("百分位"))
    except Exception:
        pass

    return result


def get_index_constituents(index_code: str = "000300") -> list:
    """获取指数成分股列表，返回股票代码列表。"""
    try:
        df = ak.index_stock_cons(symbol=index_code)
        return df["品种代码"].tolist()
    except Exception:
        return []


def get_fund_info(fund_code: str) -> dict:
    """
    获取基金基本信息。

    返回:
        {
            "name": str,           # 基金名称
            "code": str,
            "fund_type": str,      # 基金类型
            "manager": str,        # 基金经理
            "company": str,        # 基金公司
            "establish_date": str, # 成立日期
            "nav": float,          # 最新净值
            "nav_date": str,       # 净值日期
        }
    """
    result = {"code": fund_code, "name": ""}
    try:
        info_df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if info_df is not None and not info_df.empty:
            latest = info_df.iloc[-1]
            result["nav"] = _to_float(latest.iloc[1]) if len(latest) > 1 else None
            result["nav_date"] = str(latest.iloc[0]) if len(latest) > 0 else ""
    except Exception:
        pass

    try:
        # 获取基金名称
        name_df = ak.fund_name_em()
        row = name_df[name_df["基金代码"] == fund_code]
        if not row.empty:
            result["name"] = row.iloc[0].get("基金简称", "")
            result["fund_type"] = row.iloc[0].get("基金类型", "")
    except Exception:
        pass

    return result


def _index_name_map(code: str) -> str:
    """指数代码到名称的映射。"""
    mapping = {
        "000300": "沪深300",
        "000905": "中证500",
        "000852": "中证1000",
        "399006": "创业板指",
        "000016": "上证50",
        "399303": "国证2000",
    }
    return mapping.get(code, code)


def _to_float(val) -> float | None:
    """安全转换为 float。"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def get_index_current_price(index_code: str) -> dict:
    """获取指数实时点位（Sina 行情）。

    返回: {"price": float, "date": str} 或 {"price": None}
    """
    cache_key = f"index_price:{index_code}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    result = {"price": None}
    try:
        spot_df = ak.stock_zh_index_spot_sina()
        base = index_code.replace(".SZ", "").replace(".SH", "").replace(".CSI", "")
        for prefix in ["sh", "sz"]:
            sina_code = f"{prefix}{base}"
            match = spot_df[spot_df["代码"] == sina_code]
            if not match.empty:
                result = {
                    "price": float(match.iloc[0]["最新价"]),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                }
                break
    except Exception as e:
        logger.warning(f"获取指数 {index_code} 实时点位失败: {e}")

    # 只缓存有效结果
    if result.get("price") is not None:
        _set_cached(cache_key, result)
    return result


def get_market_overview() -> dict:
    """
    获取市场全景数据，用于每日简报。

    返回:
        {
            "indices": [{"name", "price", "change_pct", "volume_yi"}],   # 主要指数
            "sectors_top": [{"name", "change_pct", "lead_stock", "lead_change"}],  # 领涨板块
            "sectors_bottom": [{"name", "change_pct", "lead_stock", "lead_change"}],  # 领跌板块
            "breadth": {"up", "down", "limit_up", "limit_down", "total_volume_yi"},  # 涨跌家数
        }
    """
    # 缓存命中则直接返回（行情数据 5 分钟内复用）
    cached = _get_cached("market_overview")
    if cached is not None:
        return cached

    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    result = {
        "indices": [],
        "sectors_top": [],
        "sectors_bottom": [],
        "breadth": {},
    }

    # 1. 主要指数行情（akshare 优先，东方财富降级）
    try:
        df = ak.stock_zh_index_spot_sina()
        target_names = {
            "上证指数", "深证成指", "创业板指", "科创50",
            "沪深300", "中证500", "中证1000", "北证50",
        }
        seen = set()
        for _, row in df.iterrows():
            name = str(row.get("名称", ""))
            if name in target_names and name not in seen:
                seen.add(name)
                result["indices"].append({
                    "name": name,
                    "price": float(row.get("最新价", 0)),
                    "change_pct": round(float(row.get("涨跌幅", 0)), 2),
                    "volume_yi": round(float(row.get("成交额", 0)) / 1e8, 0),
                })
    except Exception as e:
        logger.warning(f"akshare 指数行情失败，降级到东方财富: {e}")
        fallback_indices = _eastmoney_index_spot()
        if fallback_indices:
            result["indices"] = fallback_indices

    # 2. 行业板块涨跌幅（同花顺）
    try:
        df = ak.stock_board_industry_summary_ths()
        df = df.sort_values("涨跌幅", ascending=False)
        for _, row in df.head(5).iterrows():
            result["sectors_top"].append({
                "name": str(row.get("板块", "")),
                "change_pct": round(float(row.get("涨跌幅", 0)), 2),
                "lead_stock": str(row.get("领涨股", "")),
                "lead_change": round(float(row.get("领涨股-涨跌幅", 0)), 2),
            })
        for _, row in df.tail(5).iterrows():
            result["sectors_bottom"].append({
                "name": str(row.get("板块", "")),
                "change_pct": round(float(row.get("涨跌幅", 0)), 2),
                "lead_stock": str(row.get("领涨股", "")),
                "lead_change": round(float(row.get("领涨股-涨跌幅", 0)), 2),
            })
    except Exception as e:
        logger.warning(f"行业板块获取失败: {e}")

    # 3. 涨跌家数（多接口降级尝试）
    # 优先用东财个股接口，失败则从板块数据估算
    try:
        df = ak.stock_zh_a_spot_em()
        up_count = int((df["涨跌幅"] > 0).sum())
        down_count = int((df["涨跌幅"] < 0).sum())
        limit_up = int((df["涨跌幅"] >= 9.9).sum())
        limit_down = int((df["涨跌幅"] <= -9.9).sum())
        total_vol = round(df["成交额"].sum() / 1e8, 0)
        result["breadth"] = {
            "up": up_count,
            "down": down_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total_volume_yi": total_vol,
        }
    except Exception:
        # 降级：从板块数据统计涨跌家数
        try:
            df = ak.stock_board_industry_summary_ths()
            up_count = int(df["上涨家数"].sum())
            down_count = int(df["下跌家数"].sum())
            total_vol = round(float(df["总成交额"].sum()), 0)
            result["breadth"] = {
                "up": up_count,
                "down": down_count,
                "limit_up": 0,
                "limit_down": 0,
                "total_volume_yi": total_vol,
            }
        except Exception as e:
            logger.warning(f"涨跌家数降级估算失败: {e}")
            total_vol = sum(idx.get("volume_yi", 0) for idx in result["indices"])
            result["breadth"] = {
                "up": 0, "down": 0, "limit_up": 0, "limit_down": 0,
                "total_volume_yi": total_vol,
            }

    _set_cached("market_overview", result)
    return result
