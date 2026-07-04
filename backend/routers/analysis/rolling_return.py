from infra.utils import _safe_float
"""滚动收益分析 — 计算任意时点买入持有N年的收益分布

参考韭圈儿"组合回测"功能：
- 任意时点买入持有1/3/5年的收益分布
- 胜率（正收益概率）
- 最大收益、最小收益、中位收益
- 年化收益中位数
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from db._conn import _get_conn
from db.health_score import save_bond_yield
from db.config import get_config, get_config_int
from services.llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rolling", tags=["rolling-return"])

_background_tasks: set = set()



# ============ 核心计算 ============

def calc_rolling_returns(nav_series: list[tuple], holding_days: int) -> dict:
    """计算滚动持有收益。

    Args:
        nav_series: [(date_str, nav_float), ...] 按日期升序
        holding_days: 持有天数

    Returns:
        {win_rate, median, mean, max, min, p25, p75, count, annualized_median}
    """
    if len(nav_series) < holding_days + 1:
        return {"error": f"数据不足：需要{holding_days + 1}条，实际{len(nav_series)}条"}

    returns = []
    for i in range(len(nav_series) - holding_days):
        start_nav = nav_series[i][1]
        end_nav = nav_series[i + holding_days][1]
        if start_nav > 0:
            ret = (end_nav - start_nav) / start_nav
            returns.append(ret)

    if not returns:
        return {"error": "无有效收益数据"}

    returns.sort()
    n = len(returns)
    positive = sum(1 for r in returns if r > 0)

    median_idx = n // 2
    p25_idx = n // 4
    p75_idx = n * 3 // 4

    years = holding_days / 365
    median_return = returns[median_idx]
    annualized = (1 + median_return) ** (1 / years) - 1 if years > 0 and median_return > -1 else 0

    return {
        "holding_days": holding_days,
        "holding_years": round(years, 1),
        "sample_count": n,
        "win_rate": round(positive / n * 100, 1),
        "median_return": round(median_return * 100, 2),
        "mean_return": round(sum(returns) / n * 100, 2),
        "max_return": round(returns[-1] * 100, 2),
        "min_return": round(returns[0] * 100, 2),
        "p25_return": round(returns[p25_idx] * 100, 2),
        "p75_return": round(returns[p75_idx] * 100, 2),
        "annualized_median": round(annualized * 100, 2),
        "first_date": nav_series[0][0],
        "last_date": nav_series[-1][0],
    }


def calc_max_drawdown(nav_series: list[tuple]) -> dict:
    """计算最大回撤。"""
    if len(nav_series) < 2:
        return {"max_drawdown": 0, "peak_date": "", "trough_date": ""}

    peak = nav_series[0][1]
    peak_date = nav_series[0][0]
    max_dd = 0
    dd_peak_date = ""
    dd_trough_date = ""

    for date, nav in nav_series:
        if nav > peak:
            peak = nav
            peak_date = date
        dd = (peak - nav) / peak
        if dd > max_dd:
            max_dd = dd
            dd_peak_date = peak_date
            dd_trough_date = date

    return {
        "max_drawdown": round(max_dd * 100, 2),
        "peak_date": dd_peak_date,
        "trough_date": dd_trough_date,
    }


def calc_annual_returns(nav_series: list[tuple]) -> list[dict]:
    """计算逐年收益率。"""
    if len(nav_series) < 2:
        return []

    # 按年分组
    year_data = {}
    for date, nav in nav_series:
        year = date[:4]
        if year not in year_data:
            year_data[year] = {"first": nav, "first_date": date, "last": nav, "last_date": date}
        year_data[year]["last"] = nav
        year_data[year]["last_date"] = date

    result = []
    for year in sorted(year_data.keys()):
        d = year_data[year]
        if d["first"] > 0:
            ret = (d["last"] - d["first"]) / d["first"]
            result.append({
                "year": year,
                "return_pct": round(ret * 100, 2),
                "start_nav": round(d["first"], 4),
                "end_nav": round(d["last"], 4),
            })

    return result


# ============ 数据获取 ============

def _get_index_nav_from_akshare(index_code: str, days: int = 3650) -> list[tuple]:
    """从akshare获取指数历史净值。返回[(date, nav), ...]"""
    import akshare as ak
    try:
        # 尝试指数日线
        symbol = index_code.replace(".", "").lower()
        if symbol.startswith("sh") or symbol.startswith("sz"):
            pass
        elif symbol.startswith("000"):
            symbol = f"sh{symbol}"
        else:
            symbol = f"sz{symbol}"

        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is not None and len(df) > 0:
            df = df.tail(days)
            result = []
            for _, row in df.iterrows():
                date = str(row.get("date", row.name))[:10]
                nav = _safe_float(row.get("close"))
                if nav > 0:
                    result.append((date, nav))
            return result
    except Exception as e:
        logger.warning(f"[rolling] akshare指数数据失败 {index_code}: {e}")

    return []


def _get_fund_nav_from_akshare(fund_code: str, days: int = 3650) -> list[tuple]:
    """从akshare获取基金历史净值。返回[(date, nav), ...]"""
    import akshare as ak
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
        if df is not None and len(df) > 0:
            df = df.tail(days)
            result = []
            for _, row in df.iterrows():
                date = str(row.iloc[0])[:10]
                nav = _safe_float(row.iloc[1])
                if nav > 0:
                    result.append((date, nav))
            return result
    except Exception as e:
        logger.warning(f"[rolling] akshare基金数据失败 {fund_code}: {e}")

    return []


def _get_portfolio_nav_simulated(holdings: list, days: int = 1825) -> list[tuple]:
    """按持仓权重加权计算组合净值。"""
    if not holdings:
        return []

    # 1. 获取每只基金的净值序列
    fund_navs = {}
    for h in holdings:
        code = h.get("fund_code", "")
        if code and (h.get("shares", 0) or 0) > 0:
            nav = _get_fund_nav_from_akshare(code, days)
            if nav and len(nav) > 10:
                fund_navs[code] = nav

    if not fund_navs:
        return []

    # 只有一只基金，直接返回
    if len(fund_navs) == 1:
        return list(fund_navs.values())[0]

    # 2. 按 current_value 计算权重
    total_value = sum((h.get("current_value", 0) or 0) for h in holdings if h.get("fund_code") in fund_navs)
    if total_value <= 0:
        # fallback: 等权
        weights = {code: 1.0 / len(fund_navs) for code in fund_navs}
    else:
        weights = {}
        for h in holdings:
            code = h.get("fund_code", "")
            if code in fund_navs:
                weights[code] = (h.get("current_value", 0) or 0) / total_value

    # 3. 构建日期->净值映射
    date_nav_maps = {}
    for code, series in fund_navs.items():
        date_nav_maps[code] = {d[0]: d[1] for d in series}

    # 4. 取所有基金日期的交集（至少 80% 基金有数据的日期）
    all_dates = sorted(set().union(*[set(m.keys()) for m in date_nav_maps.values()]))
    min_coverage = max(1, len(fund_navs) * 0.8)

    # 5. 归一化：每只基金以第一天净值为基准
    base_navs = {}
    for code, m in date_nav_maps.items():
        first_date = min(m.keys())
        base_navs[code] = m[first_date]

    portfolio_nav = []
    for date in all_dates:
        weighted_nav = 0
        covered = 0
        for code, m in date_nav_maps.items():
            if date in m:
                # 归一化后加权
                normalized = m[date] / base_navs[code] if base_navs[code] > 0 else 1.0
                weighted_nav += normalized * weights.get(code, 0)
                covered += 1

        if covered >= min_coverage and weighted_nav > 0:
            portfolio_nav.append((date, weighted_nav))

    return portfolio_nav


# ============ 分析执行 ============

async def analyze_rolling_return(target: str = "portfolio", code: str = "",
                                  lookback_years: int = 5) -> dict:
    """执行滚动收益分析。

    Args:
        target: "portfolio" | "index" | "fund"
        code: 指数代码或基金代码（portfolio时可为空）
        lookback_years: 回看年数
    """
    days = lookback_years * 365

    # 获取净值数据
    if target == "portfolio":
        from db.portfolio import list_holdings
        holdings = list_holdings() or []
        active = [h for h in holdings if _safe_float(h.get("shares")) > 0]
        nav_series = _get_portfolio_nav_simulated(active, days)
        name = "我的持仓组合"
    elif target == "index":
        nav_series = _get_index_nav_from_akshare(code, days)
        name = code
    elif target == "fund":
        nav_series = _get_fund_nav_from_akshare(code, days)
        name = code
    else:
        return {"error": f"未知目标类型: {target}"}

    if not nav_series:
        return {"error": f"无法获取 {name} 的净值数据"}

    # 计算多个持有期的滚动收益
    periods = [
        (180, "半年"),
        (365, "1年"),
        (730, "2年"),
        (1095, "3年"),
        (1825, "5年"),
    ]

    rolling_results = []
    for holding_days, label in periods:
        if len(nav_series) >= holding_days + 1:
            result = calc_rolling_returns(nav_series, holding_days)
            result["label"] = label
            rolling_results.append(result)

    # 最大回撤
    drawdown = calc_max_drawdown(nav_series)

    # 逐年收益
    annual_returns = calc_annual_returns(nav_series)

    # 年化收益率（整体）
    if len(nav_series) >= 2:
        total_years = (datetime.strptime(nav_series[-1][0], "%Y-%m-%d") -
                       datetime.strptime(nav_series[0][0], "%Y-%m-%d")).days / 365
        total_return = (nav_series[-1][1] - nav_series[0][1]) / nav_series[0][1]
        cagr = (1 + total_return) ** (1 / total_years) - 1 if total_years > 0 and total_return > -1 else 0
    else:
        total_return = 0
        cagr = 0
        total_years = 0

    # LLM 总结
    summary = ""
    try:
        if get_config("llm_cost.page_llm_summary", "false") != "true":
            raise RuntimeError("页面 LLM 总结已关闭")
        rolling_text = "\n".join(
            f"- 持有{r['label']}: 胜率{r['win_rate']}%, 中位收益{r['median_return']}%, "
            f"最差{r['min_return']}%, 最好{r['max_return']}%"
            for r in rolling_results
        )
        prompt = f"""你是投资顾问。根据以下滚动收益分析数据，用3-5句话给出通俗易懂的结论。

基金/指数：{name}
数据区间：{nav_series[0][0]} ~ {nav_series[-1][0]}（{total_years:.1f}年）
总收益：{total_return*100:.1f}% | 年化：{cagr*100:.1f}%
最大回撤：{drawdown['max_drawdown']}%

滚动收益：
{rolling_text}

请给出持有建议和风险提示。不超过200字。"""
        resp = await asyncio.to_thread(lambda: _call_llm(
            caller="page_summary_rolling_return", model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=500,
        ))
        summary = resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"[rolling] LLM总结失败: {e}")

    # 保存到DB
    cache_key = f"rolling_{target}_{code or 'portfolio'}_{lookback_years}y"
    result = {
        "target": target,
        "code": code,
        "name": name,
        "data_range": f"{nav_series[0][0]} ~ {nav_series[-1][0]}",
        "total_years": round(total_years, 1),
        "total_return": round(total_return * 100, 2),
        "cagr": round(cagr * 100, 2),
        "rolling_periods": rolling_results,
        "max_drawdown": drawdown,
        "annual_returns": annual_returns,
        "summary": summary,
    }

    # 存入analysis_cache
    conn = None
    try:
        conn = _get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO analysis_cache (cache_key, data, created_at)
            VALUES (?, ?, datetime('now','localtime'))
        """, (cache_key, json.dumps(result, ensure_ascii=False)))
        conn.commit()
    except Exception as e:
        logger.warning(f"[rolling] 缓存保存失败: {e}")
    finally:
        if conn:
            conn.close()

    return result


# ============ API 端点 ============

from pydantic import BaseModel, Field
from typing import Optional


class RollingAnalyzeRequest(BaseModel):
    target: str = Field("portfolio", pattern=r"^(portfolio|index|fund)$")
    code: str = Field("", max_length=20)
    lookback_years: int = Field(5, ge=1, le=20)


class RollingPortfolioRequest(BaseModel):
    lookback_years: int = Field(5, ge=1, le=20)


class RollingCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    lookback_years: int = Field(5, ge=1, le=20)


@router.post("/analyze")
async def analyze_api(data: RollingAnalyzeRequest):
    """执行滚动收益分析。"""
    target = data.target
    code = data.code
    lookback_years = data.lookback_years
    result = await analyze_rolling_return(target, code, lookback_years)
    # 提取可执行行动
    try:
        from analysis.action_extractor import extract_actions, format_actions_for_response
        # 从 windows 中提取 stats
        windows = result.get("windows", [])
        avg_win_rate = sum(w.get("win_rate", 50) for w in windows) / len(windows) if windows else 50
        result["actions"] = format_actions_for_response(extract_actions("rolling", {"stats": {"win_rate": avg_win_rate}}))
    except Exception as e:
        logger.warning(f"[rolling] 行动提取失败: {e}")
        result["actions"] = []
    return {"status": "ok", "result": result}


@router.post("/portfolio")
async def portfolio_api(data: RollingPortfolioRequest):
    """分析持仓组合的滚动收益。"""
    lookback_years = data.lookback_years
    result = await analyze_rolling_return("portfolio", "", lookback_years)
    return {"status": "ok", "result": result}


@router.post("/index")
async def index_api(data: RollingCodeRequest):
    """分析指数的滚动收益。"""
    code = data.code
    lookback_years = data.lookback_years
    result = await analyze_rolling_return("index", code, lookback_years)
    return {"status": "ok", "result": result}


@router.post("/fund")
async def fund_api(data: RollingCodeRequest):
    """分析基金的滚动收益。"""
    code = data.code
    lookback_years = data.lookback_years
    result = await analyze_rolling_return("fund", code, lookback_years)
    return {"status": "ok", "result": result}
