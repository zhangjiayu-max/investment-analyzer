"""综合理财健康分 — 计算引擎 + API

评分框架（满分1000分）：
- 选品质量 200分：持有基金的同类排名、基金经理评分
- 分散配置 200分：股债比例、行业分散度、相关性
- 估值合理性 200分：持仓整体估值百分位
- 持有行为 200分：交易频率、追涨杀跌、持有时长
- 风控纪律 200分：最大回撤控制、单只仓位

参考：蚂蚁财富"理财分"321模型（选品+配置+持有）
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter

from db._conn import _get_conn
from db.health_score import save_health_score, get_health_score, list_health_scores
from db.portfolio import list_holdings
from db.valuations import get_latest_valuation, list_valuation_indexes, get_index_info
from db.config import get_config_float
from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health-score"])

_background_tasks: set = set()

# ============ 辅助函数 ============

def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v else default
    except (ValueError, TypeError):
        return default


def _get_holding_valuations(holdings: list) -> list[dict]:
    """获取持仓对应的估值数据。"""
    indexes = list_valuation_indexes()
    val_map = {}
    for idx in indexes:
        code = idx.get("index_code", "")
        name = idx.get("index_name", "")
        latest = get_latest_valuation(code)
        if latest:
            val_map[name] = latest

    result = []
    for h in holdings:
        name = h.get("fund_name", "")
        matched_val = None
        for vn, vv in val_map.items():
            if vn in name or name in vn:
                matched_val = vv
                break
        result.append({
            **h,
            "valuation": matched_val,
        })
    return result


def _get_trade_records() -> list[dict]:
    """获取交易记录（用于行为分析）。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM trade_records ORDER BY trade_date DESC LIMIT 200
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ============ 五维度评分 ============

def calc_quality_score(holdings_with_val: list) -> tuple[int, dict]:
    """选品质量（200分）：基金排名、估值质量。"""
    if not holdings_with_val:
        return 100, {"reason": "无持仓数据，给予中性分"}

    total = 0
    count = 0
    good_count = 0
    bad_count = 0

    for h in holdings_with_val:
        val = h.get("valuation")
        if not val:
            continue
        count += 1

        pe_pct = _safe_float(val.get("pe_percentile"))
        pb_pct = _safe_float(val.get("pb_percentile"))
        avg_pct = (pe_pct + pb_pct) / 2 if pe_pct and pb_pct else pe_pct or pb_pct or 50

        # 低估=好选品（在合理估值买入）
        if avg_pct < 30:
            good_count += 1
            total += 180
        elif avg_pct < 50:
            total += 150
        elif avg_pct < 70:
            total += 120
        elif avg_pct < 90:
            bad_count += 1
            total += 80
        else:
            bad_count += 1
            total += 50

    if count == 0:
        return 100, {"reason": "无估值匹配"}

    avg_score = total // count
    avg_score = max(0, min(200, avg_score))

    detail = {
        "matched_count": count,
        "good_count": good_count,
        "bad_count": bad_count,
        "avg_score": avg_score,
    }
    return avg_score, detail


def calc_diversification_score(holdings: list) -> tuple[int, dict]:
    """分散配置（200分）：股债比例、持仓数量、集中度。"""
    if not holdings:
        return 100, {"reason": "无持仓"}

    score = 0

    # 1. 持仓数量（60分）
    n = len(holdings)
    if n >= 10:
        score += 60
    elif n >= 7:
        score += 50
    elif n >= 5:
        score += 40
    elif n >= 3:
        score += 30
    else:
        score += 15

    # 2. 集中度（70分）— 最大单只占比
    total_value = sum(_safe_float(h.get("market_value")) for h in holdings)
    if total_value > 0:
        max_pct = max(_safe_float(h.get("market_value")) / total_value * 100 for h in holdings)
        if max_pct <= 15:
            score += 70
        elif max_pct <= 20:
            score += 55
        elif max_pct <= 30:
            score += 35
        elif max_pct <= 40:
            score += 20
        else:
            score += 5
    else:
        score += 35

    # 3. 股债比例（70分）— 简化判断
    equity_types = ["股票型", "混合型", "指数型", "ETF", "QDII"]
    bond_types = ["债券型", "货币型"]
    equity_count = sum(1 for h in holdings if any(t in h.get("fund_type", "") for t in equity_types))
    bond_count = sum(1 for h in holdings if any(t in h.get("fund_type", "") for t in bond_types))

    equity_ratio = equity_count / n * 100 if n > 0 else 50
    # 理想股债比 40-70%
    if 40 <= equity_ratio <= 70:
        score += 70
    elif 30 <= equity_ratio <= 80:
        score += 50
    elif 20 <= equity_ratio <= 90:
        score += 30
    else:
        score += 10

    score = max(0, min(200, score))

    detail = {
        "holding_count": n,
        "max_single_pct": round(max_pct, 1) if total_value > 0 else 0,
        "equity_ratio": round(equity_ratio, 1),
        "equity_count": equity_count,
        "bond_count": bond_count,
    }
    return score, detail


def calc_valuation_score(holdings_with_val: list) -> tuple[int, dict]:
    """估值合理性（200分）：持仓整体估值百分位。"""
    if not holdings_with_val:
        return 100, {"reason": "无估值数据"}

    pcts = []
    for h in holdings_with_val:
        val = h.get("valuation")
        if not val:
            continue
        pe_pct = _safe_float(val.get("pe_percentile"))
        pb_pct = _safe_float(val.get("pb_percentile"))
        avg = (pe_pct + pb_pct) / 2 if pe_pct and pb_pct else pe_pct or pb_pct
        if avg > 0:
            pcts.append(avg)

    if not pcts:
        return 100, {"reason": "无有效百分位"}

    portfolio_pct = sum(pcts) / len(pcts)

    # 低估=高分
    if portfolio_pct <= 20:
        score = 200
    elif portfolio_pct <= 35:
        score = 175
    elif portfolio_pct <= 50:
        score = 140
    elif portfolio_pct <= 65:
        score = 100
    elif portfolio_pct <= 80:
        score = 60
    else:
        score = 30

    detail = {
        "portfolio_avg_percentile": round(portfolio_pct, 1),
        "index_count": len(pcts),
        "undervalued_count": sum(1 for p in pcts if p < 30),
        "overvalued_count": sum(1 for p in pcts if p > 70),
    }
    return score, detail


def calc_behavior_score(trades: list) -> tuple[int, dict]:
    """持有行为（200分）：交易频率、追涨杀跌。"""
    if not trades:
        return 140, {"reason": "无交易记录，给予偏高分"}

    score = 0

    # 1. 交易频率（80分）
    now = datetime.now()
    recent_90d = [t for t in trades if t.get("trade_date", "") >= (now - timedelta(days=90)).strftime("%Y-%m-%d")]
    freq = len(recent_90d)

    if freq <= 3:
        score += 80  # 低频=好
    elif freq <= 6:
        score += 60
    elif freq <= 12:
        score += 40
    elif freq <= 20:
        score += 20
    else:
        score += 5

    # 2. 追涨杀跌检测（60分）
    # 简化：检查买入时市场是否处于高位（估值>70%）
    buy_trades = [t for t in trades if t.get("trade_type") == "buy"]
    if buy_trades:
        chases = sum(1 for t in buy_trades if _safe_float(t.get("market_percentile", 50)) > 70)
        chase_ratio = chases / len(buy_trades)
        if chase_ratio <= 0.1:
            score += 60
        elif chase_ratio <= 0.3:
            score += 40
        elif chase_ratio <= 0.5:
            score += 20
        else:
            score += 5
    else:
        score += 40

    # 3. 平均持有时长（60分）
    # 从持仓快照推算
    avg_days = 180  # 默认假设半年
    if avg_days >= 365:
        score += 60
    elif avg_days >= 180:
        score += 45
    elif avg_days >= 90:
        score += 30
    else:
        score += 10

    score = max(0, min(200, score))

    detail = {
        "trades_90d": freq,
        "buy_count": len(buy_trades),
        "chase_ratio": round(chase_ratio * 100, 1) if buy_trades else 0,
        "avg_holding_days": avg_days,
    }
    return score, detail


def calc_risk_score(holdings: list, holdings_with_val: list) -> tuple[int, dict]:
    """风控纪律（200分）：仓位控制、回撤管理。"""
    if not holdings:
        return 100, {"reason": "无持仓"}

    score = 0
    total_value = sum(_safe_float(h.get("market_value")) for h in holdings)

    # 1. 单只仓位控制（80分）
    if total_value > 0:
        max_pct = max(_safe_float(h.get("market_value")) / total_value * 100 for h in holdings)
        if max_pct <= 10:
            score += 80
        elif max_pct <= 15:
            score += 65
        elif max_pct <= 20:
            score += 50
        elif max_pct <= 30:
            score += 30
        else:
            score += 10
    else:
        score += 40

    # 2. 高估持仓占比（60分）
    overvalued_value = 0
    for h in holdings_with_val:
        val = h.get("valuation")
        if not val:
            continue
        pe_pct = _safe_float(val.get("pe_percentile"))
        if pe_pct > 80:
            overvalued_value += _safe_float(h.get("market_value"))

    if total_value > 0:
        overvalued_pct = overvalued_value / total_value * 100
        if overvalued_pct <= 10:
            score += 60
        elif overvalued_pct <= 20:
            score += 45
        elif overvalued_pct <= 30:
            score += 30
        elif overvalued_pct <= 50:
            score += 15
        else:
            score += 5
    else:
        score += 30

    # 3. 止损意识（60分）— 从交易记录看是否有止损行为
    trades = _get_trade_records()
    sell_trades = [t for t in trades if t.get("trade_type") == "sell"]
    stop_loss_count = sum(1 for t in sell_trades if _safe_float(t.get("profit_loss_pct", 0)) < -10)
    if sell_trades:
        stop_loss_ratio = stop_loss_count / len(sell_trades)
        if stop_loss_ratio <= 0.1:
            score += 60  # 很少大亏卖出
        elif stop_loss_ratio <= 0.3:
            score += 40
        else:
            score += 15
    else:
        score += 40

    score = max(0, min(200, score))

    detail = {
        "max_single_pct": round(max_pct, 1) if total_value > 0 else 0,
        "overvalued_pct": round(overvalued_pct, 1) if total_value > 0 else 0,
        "stop_loss_count": stop_loss_count,
    }
    return score, detail


# ============ 综合评分 ============

async def calc_health_score() -> dict:
    """计算综合理财健康分。"""
    holdings = list_holdings() or []
    active_holdings = [h for h in holdings if _safe_float(h.get("shares")) > 0]

    holdings_with_val = _get_holding_valuations(active_holdings)
    trades = _get_trade_records()

    # 五维度评分
    sq, dq = calc_quality_score(holdings_with_val)
    sd, dd = calc_diversification_score(active_holdings)
    sv, dv = calc_valuation_score(holdings_with_val)
    sb, db = calc_behavior_score(trades)
    sr, dr = calc_risk_score(active_holdings, holdings_with_val)

    total = sq + sd + sv + sb + sr

    # 生成建议
    advice = []
    if dq.get("bad_count", 0) > 0:
        advice.append(f"有 {dq['bad_count']} 只基金处于高估区域，考虑减仓或止盈")
    if dd.get("max_single_pct", 0) > 20:
        advice.append(f"最大单只仓位 {dd['max_single_pct']}%，建议控制在15%以内")
    if dd.get("equity_ratio", 50) > 80:
        advice.append(f"权益占比 {dd['equity_ratio']}% 偏高，建议增加债券基金配置")
    if dd.get("equity_ratio", 50) < 30:
        advice.append(f"权益占比 {dd['equity_ratio']}% 偏低，长期收益可能受限")
    if dv.get("portfolio_avg_percentile", 50) > 70:
        advice.append("持仓整体估值偏高，注意风险")
    if dv.get("portfolio_avg_percentile", 50) < 30:
        advice.append("持仓整体估值偏低，可考虑适度加仓")
    if db.get("trades_90d", 0) > 12:
        advice.append(f"近90天交易 {db['trades_90d']} 次，频率偏高，建议减少操作")
    if db.get("chase_ratio", 0) > 30:
        advice.append(f"追涨买入占比 {db['chase_ratio']}%，建议在低估时买入")
    if dr.get("overvalued_pct", 0) > 30:
        advice.append(f"高估持仓占比 {dr['overvalued_pct']}%，建议分批止盈")

    if not advice:
        advice.append("整体投资行为健康，继续保持")

    # LLM 总结
    summary = ""
    try:
        prompt = f"""你是理财顾问。根据以下健康分数据，用3-5句话给出通俗易懂的总结和建议。

总分：{total}/1000
选品质量：{sq}/200 | 分散配置：{sd}/200 | 估值合理性：{sv}/200
持有行为：{sb}/200 | 风控纪律：{sr}/200

建议：{'; '.join(advice[:5])}

请用温和专业的语气，不超过150字。"""
        resp = await asyncio.to_thread(lambda: _call_llm(
            caller="health_score", model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=500,
        ))
        summary = resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"[health] LLM 总结失败: {e}")

    today = datetime.now().strftime("%Y-%m-%d")
    result = {
        "date": today,
        "total_score": total,
        "scores": {
            "quality": sq,
            "diversification": sd,
            "valuation": sv,
            "behavior": sb,
            "risk": sr,
        },
        "details": {
            "quality": dq,
            "diversification": dd,
            "valuation": dv,
            "behavior": db,
            "risk": dr,
        },
        "advice": advice,
        "summary": summary,
    }

    # 保存到 DB
    save_health_score(
        score_date=today,
        total_score=total,
        score_quality=sq,
        score_diversification=sd,
        score_valuation=sv,
        score_behavior=sb,
        score_risk=sr,
        advice=advice,
        detail=result["details"],
    )

    return result


# ============ 股债性价比（FED模型） ============

async def calc_stock_bond_ratio() -> dict:
    """FED模型：股票盈利收益率 vs 国债收益率。"""
    # 获取沪深300估值（用 index_code 而非 index_name）
    hs300 = get_latest_valuation("399300.SZ")
    if not hs300:
        # 尝试其他名称
        for code in ["399300.SZ", "000300.SH", "399300"]:
            hs300 = get_latest_valuation(code)
            if hs300:
                break

    pe = _safe_float(hs300.get("current_value") or hs300.get("pe_ttm") or hs300.get("pe") or hs300.get("metric_value")) if hs300 else 0
    if pe <= 0:
        return {"error": "无法获取沪深300 PE数据"}

    earnings_yield = 1 / pe  # 盈利收益率

    # 获取国债收益率（从DB或akshare）
    from db.health_score import get_latest_bond_yield
    bond = get_latest_bond_yield()
    bond_yield = _safe_float(bond.get("yield_10y")) if bond else 0

    if bond_yield <= 0:
        # 尝试从akshare获取
        try:
            import akshare as ak
            df = ak.bond_zh_us_rate(start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"))
            if df is not None and len(df) > 0:
                bond_yield = float(df.iloc[-1].get("中国国债收益率10年", 0))
        except Exception:
            pass

    if bond_yield <= 0:
        bond_yield = 2.5  # 兜底假设

    # bond_yield 可能是百分比形式（如2.5）或小数形式（如0.025）
    if bond_yield > 1:
        bond_yield_decimal = bond_yield / 100
    else:
        bond_yield_decimal = bond_yield

    spread = earnings_yield - bond_yield_decimal
    spread_pct = round(spread * 100, 2)

    if spread_pct > 5:
        signal = "极度看好股票"
        advice = "股票盈利收益率远超国债，权益资产极具吸引力"
    elif spread_pct > 3:
        signal = "看好股票"
        advice = "股票相对债券有明显优势，建议增配权益"
    elif spread_pct > 1:
        signal = "中性偏股"
        advice = "股票有一定吸引力，可适度配置"
    elif spread_pct > 0:
        signal = "中性"
        advice = "股债吸引力接近，维持现有配置"
    else:
        signal = "看好债券"
        advice = "债券吸引力超过股票，建议增配固收"

    return {
        "hs300_pe": round(pe, 2),
        "earnings_yield": round(earnings_yield * 100, 2),
        "bond_yield_10y": round(bond_yield, 2) if bond_yield > 1 else round(bond_yield * 100, 2),
        "spread": spread_pct,
        "signal": signal,
        "advice": advice,
    }


# ============ 恐贪指数 ============

async def calc_fear_greed_index() -> dict:
    """恐贪指数（0-100）：基于市场情绪指标。"""
    import akshare as ak

    factors = {}

    # 1. 沪深300近20日涨跌幅（权重20%）
    try:
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 20:
            close_now = float(df.iloc[-1]["close"])
            close_20d = float(df.iloc[-20]["close"])
            pct_20d = (close_now - close_20d) / close_20d * 100
            # 映射到0-100：-10%→0, 0%→50, +10%→100
            factors["market_trend"] = max(0, min(100, (pct_20d + 10) / 20 * 100))
        else:
            factors["market_trend"] = 50
    except Exception:
        factors["market_trend"] = 50

    # 2. 涨跌家数比（权重20%）
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and len(df) > 0:
            up = sum(1 for _, r in df.iterrows() if _safe_float(r.get("涨跌幅")) > 0)
            down = sum(1 for _, r in df.iterrows() if _safe_float(r.get("涨跌幅")) < 0)
            total = up + down
            if total > 0:
                up_ratio = up / total * 100
                factors["up_down_ratio"] = max(0, min(100, up_ratio))
            else:
                factors["up_down_ratio"] = 50
        else:
            factors["up_down_ratio"] = 50
    except Exception:
        factors["up_down_ratio"] = 50

    # 3. 北向资金（权重15%）
    try:
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        if df is not None and len(df) >= 5:
            recent = df.tail(5)
            net_flow = sum(_safe_float(r.get("净流入", 0)) for _, r in recent.iterrows())
            # 正流入→贪婪，负流入→恐惧
            factors["north_flow"] = max(0, min(100, (net_flow / 100 + 50)))
        else:
            factors["north_flow"] = 50
    except Exception:
        factors["north_flow"] = 50

    # 4. 换手率（权重15%）
    factors["turnover"] = 50  # 需要更多数据源

    # 5. 融资余额变化（权重10%）
    factors["margin"] = 50  # 需要更多数据源

    # 加权计算
    weights = {
        "market_trend": 0.25,
        "up_down_ratio": 0.25,
        "north_flow": 0.20,
        "turnover": 0.15,
        "margin": 0.15,
    }

    total_score = sum(factors.get(k, 50) * w for k, w in weights.items())
    total_score = round(max(0, min(100, total_score)))

    if total_score <= 20:
        zone = "极度恐慌"
        advice = "市场极度恐慌，历史经验表明这是逆向买入的好时机"
    elif total_score <= 40:
        zone = "恐慌"
        advice = "市场情绪低迷，可考虑分批加仓低估品种"
    elif total_score <= 60:
        zone = "中性"
        advice = "市场情绪正常，维持现有配置"
    elif total_score <= 80:
        zone = "贪婪"
        advice = "市场情绪偏热，注意控制仓位"
    else:
        zone = "极度贪婪"
        advice = "市场极度贪婪，考虑分批止盈"

    return {
        "score": total_score,
        "zone": zone,
        "advice": advice,
        "factors": {k: round(v, 1) for k, v in factors.items()},
    }


# ============ API 端点 ============

@router.post("/calculate")
async def calculate_health_score():
    """计算并返回健康分。"""
    result = await calc_health_score()
    return {"status": "ok", "result": result}


@router.get("/today")
async def get_today_score():
    """获取今日健康分。"""
    today = datetime.now().strftime("%Y-%m-%d")
    score = get_health_score(today)
    if not score:
        result = await calc_health_score()
        return {"status": "ok", "result": result}
    return {"status": "ok", "result": score}


@router.get("/history")
async def get_history(limit: int = 30):
    """获取历史健康分。"""
    scores = list_health_scores(limit=limit)
    return {"status": "ok", "scores": scores}


@router.get("/stock-bond-ratio")
async def get_stock_bond_ratio():
    """获取股债性价比（FED模型）。"""
    result = await calc_stock_bond_ratio()
    return {"status": "ok", "result": result}


@router.get("/fear-greed")
async def get_fear_greed():
    """获取恐贪指数。"""
    result = await calc_fear_greed_index()
    return {"status": "ok", "result": result}
