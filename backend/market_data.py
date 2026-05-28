"""行情数据获取模块 — 基于 akshare 获取股票、基金、指数数据"""

from datetime import datetime, timedelta

import akshare as ak
import pandas as pd


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
    获取开放式基金净值数据。

    参数:
        fund_code: 基金代码，如 "110011"

    返回:
        DataFrame，列: 净值日期/单位净值/累计净值/日增长率
    """
    df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
    df.columns = ["净值日期", "累计净值"]
    return df


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
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        spot_df = ak.stock_zh_index_spot_sina()
        base = index_code.replace(".SZ", "").replace(".SH", "").replace(".CSI", "")
        for prefix in ["sh", "sz"]:
            sina_code = f"{prefix}{base}"
            match = spot_df[spot_df["代码"] == sina_code]
            if not match.empty:
                return {
                    "price": float(match.iloc[0]["最新价"]),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                }
        return {"price": None}
    except Exception:
        return {"price": None}
