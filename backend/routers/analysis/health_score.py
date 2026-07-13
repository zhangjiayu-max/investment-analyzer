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
import time
from datetime import datetime, timedelta

from fastapi import APIRouter

from db._conn import _get_conn
from db.health_score import save_health_score, get_health_score, list_health_scores
from db.portfolio import list_holdings
from db.valuations import get_latest_valuation, list_valuation_indexes, get_index_info
from db.config import get_config, get_config_float
from services.llm_service import _call_llm, MODEL
from infra.utils import _safe_float

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health-score"])

_background_tasks: set = set()

# 2026-07-13 性能优化：akshare 同步调用在 async 函数中阻塞事件循环，
# 加 5 分钟内存缓存 + to_thread 线程池执行，避免每次请求都串行抓取。
_FEAR_GREED_CACHE: dict = {"data": None, "ts": 0.0}
_STOCK_BOND_RATIO_CACHE: dict = {"data": None, "ts": 0.0}
_HEALTH_SCORE_CACHE: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300  # 秒

# ============ 辅助函数 ============


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
            SELECT * FROM portfolio_transactions
            WHERE (is_system IS NULL OR is_system = 0)
            ORDER BY transaction_date DESC LIMIT 200
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # 统一字段名：transaction_type -> trade_type, transaction_date -> trade_date
            d["trade_type"] = d.get("transaction_type", "")
            d["trade_date"] = d.get("transaction_date", "")
            result.append(d)
        return result
    except Exception:
        return []
    finally:
        conn.close()


# ============ 五维度评分 ============

def calc_quality_score(holdings_with_val: list) -> tuple[int, dict]:
    """选品质量（200分）：同类排名、费率水平、估值合理性。"""
    if not holdings_with_val:
        return 100, {"reason": "无持仓数据，给予中性分"}

    import akshare as ak
    total = 0
    count = 0
    good_count = 0
    bad_count = 0
    details = []

    for h in holdings_with_val:
        fund_code = h.get("fund_code", "")
        fund_name = h.get("fund_name", "")
        fund_score = 0
        fund_reasons = []

        # 1. 同类排名（80分）
        try:
            # 获取同类基金排名
            rank_data = ak.fund_open_fund_info_em(symbol=fund_code, indicator="同类排名")
            if rank_data is not None and len(rank_data) > 0:
                # 取最近一期排名百分位
                latest = rank_data.iloc[-1]
                rank_pct = _safe_float(latest.iloc[1]) if len(latest) > 1 else 50
                if rank_pct <= 10:
                    fund_score += 80
                    fund_reasons.append(f"排名前10%")
                elif rank_pct <= 25:
                    fund_score += 65
                    fund_reasons.append(f"排名前25%")
                elif rank_pct <= 50:
                    fund_score += 50
                    fund_reasons.append(f"排名中等")
                elif rank_pct <= 75:
                    fund_score += 30
                    fund_reasons.append(f"排名后25%")
                else:
                    fund_score += 15
                    fund_reasons.append(f"排名后10%")
            else:
                fund_score += 40  # 无数据给中性分
        except Exception:
            fund_score += 40

        # 2. 费率水平（60分）
        try:
            fee_data = ak.fund_open_fund_info_em(symbol=fund_code, indicator="费率")
            if fee_data is not None and len(fee_data) > 0:
                # 找管理费率
                mgmt_fee = 0
                for _, row in fee_data.iterrows():
                    for val in row:
                        if "管理" in str(val):
                            # 下一个值是费率
                            idx = list(row).index(val)
                            if idx + 1 < len(row):
                                fee_str = str(row.iloc[idx + 1]).replace("%", "").strip()
                                try:
                                    mgmt_fee = float(fee_str)
                                except Exception:
                                    pass
                if mgmt_fee > 0:
                    if mgmt_fee <= 0.5:
                        fund_score += 60
                        fund_reasons.append(f"低费率{mgmt_fee}%")
                    elif mgmt_fee <= 1.0:
                        fund_score += 45
                        fund_reasons.append(f"费率适中{mgmt_fee}%")
                    elif mgmt_fee <= 1.5:
                        fund_score += 30
                        fund_reasons.append(f"费率偏高{mgmt_fee}%")
                    else:
                        fund_score += 15
                        fund_reasons.append(f"高费率{mgmt_fee}%")
                else:
                    fund_score += 35
            else:
                fund_score += 35
        except Exception:
            fund_score += 35

        # 3. 估值合理性（60分）— 保留原逻辑但降低权重
        val = h.get("valuation")
        if val:
            pe_pct = _safe_float(val.get("pe_percentile"))
            pb_pct = _safe_float(val.get("pb_percentile"))
            avg_pct = (pe_pct + pb_pct) / 2 if pe_pct and pb_pct else pe_pct or pb_pct or 50
            if avg_pct < 30:
                fund_score += 60
                fund_reasons.append(f"估值低{avg_pct:.0f}%")
            elif avg_pct < 50:
                fund_score += 50
                fund_reasons.append(f"估值偏低{avg_pct:.0f}%")
            elif avg_pct < 70:
                fund_score += 35
            elif avg_pct < 90:
                fund_score += 20
                fund_reasons.append(f"估值偏高{avg_pct:.0f}%")
            else:
                fund_score += 10
                fund_reasons.append(f"估值过高{avg_pct:.0f}%")
        else:
            fund_score += 30

        fund_score = min(200, fund_score)
        total += fund_score
        count += 1
        if fund_score >= 140:
            good_count += 1
        elif fund_score <= 80:
            bad_count += 1
        details.append({"fund_name": fund_name, "score": fund_score, "reasons": fund_reasons})

    if count == 0:
        return 100, {"reason": "无持仓数据"}

    avg_score = total // count
    avg_score = max(0, min(200, avg_score))

    detail = {
        "matched_count": count,
        "good_count": good_count,
        "bad_count": bad_count,
        "avg_score": avg_score,
        "top_holdings": details[:5],
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
    """持有行为（200分）：交易频率、追涨杀跌、持有时长。"""
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
    # 用买入日期反查当时的指数估值百分位
    from db.valuations import get_valuation_history
    buy_trades = [t for t in trades if t.get("trade_type") == "buy"]
    chase_ratio = 0.0
    if buy_trades:
        chases = 0
        for t in buy_trades:
            trade_date = t.get("trade_date", "")
            fund_code = t.get("fund_code", "")
            # 查该基金对应的指数估值
            holding = _find_holding_by_code(fund_code)
            index_code = holding.get("index_code", "") if holding else ""
            if index_code and trade_date:
                # 取交易日期前后的估值
                try:
                    val_hist = get_valuation_history(index_code, days=5)
                    # 找最接近交易日的估值
                    closest_pct = None
                    for v in val_hist:
                        if v.get("snapshot_date", "") <= trade_date:
                            closest_pct = _safe_float(v.get("percentile"))
                            break
                    if closest_pct is None and val_hist:
                        closest_pct = _safe_float(val_hist[0].get("percentile"))
                    if closest_pct and closest_pct > 70:
                        chases += 1
                except Exception:
                    pass
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
    # 从持仓的 buy_date 推算真实持有时长
    avg_days = 180  # 默认值
    try:
        holdings = list_holdings() or []
        active = [h for h in holdings if _safe_float(h.get("shares")) > 0]
        holding_days_list = []
        for h in active:
            buy_date_str = h.get("buy_date", "")
            if not buy_date_str:
                # 从交易记录推算最早买入日期
                fund_code = h.get("fund_code", "")
                if fund_code:
                    try:
                        conn = _get_conn()
                        try:
                            earliest = conn.execute(
                                "SELECT MIN(transaction_date) FROM portfolio_transactions WHERE fund_code = ? AND transaction_type = 'buy'",
                                (fund_code,)
                            ).fetchone()
                            if earliest and earliest[0]:
                                buy_date_str = earliest[0]
                        finally:
                            conn.close()
                    except Exception:
                        pass
            if buy_date_str:
                try:
                    bd = datetime.strptime(buy_date_str[:10], "%Y-%m-%d")
                    days_held = (now - bd).days
                    if days_held > 0:
                        holding_days_list.append(days_held)
                except Exception:
                    pass
        if holding_days_list:
            avg_days = int(sum(holding_days_list) / len(holding_days_list))
    except Exception:
        pass

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


def _find_holding_by_code(fund_code: str) -> dict | None:
    """根据基金代码查找持仓记录。"""
    try:
        holdings = list_holdings() or []
        for h in holdings:
            if h.get("fund_code") == fund_code:
                return h
    except Exception:
        pass
    return None


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

def _calc_health_score_sync() -> dict:
    """计算综合理财健康分（同步实现，运行在线程池中）。"""
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

    # LLM 总结（已在线程池中，直接同步调用即可，不再嵌套 to_thread）
    summary = ""
    try:
        if get_config("llm_cost.page_llm_summary", "false") != "true":
            raise RuntimeError("页面 LLM 总结已关闭")
        prompt = f"""你是理财顾问。根据以下健康分数据，用3-5句话给出通俗易懂的总结和建议。

总分：{total}/1000
选品质量：{sq}/200 | 分散配置：{sd}/200 | 估值合理性：{sv}/200
持有行为：{sb}/200 | 风控纪律：{sr}/200

建议：{'; '.join(advice[:5])}

请用温和专业的语气，不超过150字。"""
        import uuid
        trace_id = f"hlth_{uuid.uuid4().hex[:12]}"
        resp = _call_llm(
            caller="health_score", trace_id=trace_id, model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=500,
        )
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

    # 提取可执行行动
    try:
        from analysis.action_extractor import extract_actions, format_actions_for_response
        result["actions"] = format_actions_for_response(extract_actions("health_score", result))
    except Exception as e:
        logger.warning(f"[health] 行动提取失败: {e}")
        result["actions"] = []

    return result


async def calc_health_score() -> dict:
    """计算综合理财健康分（带 5 分钟内存缓存，akshare 调用在线程池执行）。"""
    now = time.time()
    cached = _HEALTH_SCORE_CACHE.get("data")
    if cached is not None and (now - _HEALTH_SCORE_CACHE["ts"]) < _CACHE_TTL:
        return cached
    try:
        result = await asyncio.to_thread(_calc_health_score_sync)
        _HEALTH_SCORE_CACHE["data"] = result
        _HEALTH_SCORE_CACHE["ts"] = now
        return result
    except Exception as e:
        logger.warning(f"[health] 计算失败，降级返回: {e}")
        return cached or {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_score": 0,
            "scores": {},
            "details": {},
            "advice": [],
            "summary": "",
            "actions": [],
        }


# ============ 股债性价比（FED模型） ============

def _calc_stock_bond_ratio_sync() -> dict:
    """FED模型同步实现（运行在线程池中）。"""
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

    result = {
        "hs300_pe": round(pe, 2),
        "earnings_yield": round(earnings_yield * 100, 2),
        "bond_yield_10y": round(bond_yield, 2) if bond_yield > 1 else round(bond_yield * 100, 2),
        "spread": spread_pct,
        "signal": signal,
        "advice": advice,
    }

    # === 股债配比增强：结合估值给调仓建议 ===
    try:
        # 计算当前股债比例
        holdings = list_holdings() or []
        active = [h for h in holdings if (h.get("shares") or 0) > 0]
        equity_types = ["股票型", "混合型", "指数型", "ETF", "QDII"]
        bond_types = ["债券型", "货币型"]
        equity_value = sum(
            (h.get("current_value") or 0) for h in active
            if any(t in (h.get("fund_type") or h.get("fund_category") or "") for t in equity_types)
        )
        bond_value = sum(
            (h.get("current_value") or 0) for h in active
            if any(t in (h.get("fund_type") or h.get("fund_category") or "") for t in bond_types)
        )
        total_v = equity_value + bond_value
        stock_pct = equity_value / total_v * 100 if total_v > 0 else 50
        bond_pct = 100 - stock_pct

        # 用沪深300估值判断建议股债比
        val = get_latest_valuation("399300.SZ")
        percentile = val.get("percentile", 50) if val else 50

        if percentile < 20:
            suggested_stock = 70
            reason = "估值极低，建议超配股票"
        elif percentile < 40:
            suggested_stock = 60
            reason = "估值偏低，建议略超配股票"
        elif percentile > 80:
            suggested_stock = 40
            reason = "估值极高，建议超配债券"
        elif percentile > 60:
            suggested_stock = 50
            reason = "估值偏高，建议股债均衡"
        else:
            suggested_stock = 50
            reason = "估值中性，建议股债均衡"

        suggested_bond = 100 - suggested_stock
        deviation = stock_pct - suggested_stock

        result["stock_pct"] = round(stock_pct, 1)
        result["bond_pct"] = round(bond_pct, 1)
        result["suggestion"] = {
            "suggested_stock_pct": suggested_stock,
            "suggested_bond_pct": suggested_bond,
            "deviation": round(deviation, 1),
            "reason": reason,
            "action": f"股票{stock_pct:.0f}%→建议{suggested_stock}%，偏差{deviation:+.0f}%",
        }
    except Exception as e:
        logger.warning(f"[stock_bond] 生成调仓建议失败: {e}")
        result["suggestion"] = None

    return result


async def calc_stock_bond_ratio() -> dict:
    """FED模型：股票盈利收益率 vs 国债收益率（带 5 分钟内存缓存，akshare 调用在线程池执行）。"""
    now = time.time()
    cached = _STOCK_BOND_RATIO_CACHE.get("data")
    if cached is not None and (now - _STOCK_BOND_RATIO_CACHE["ts"]) < _CACHE_TTL:
        return cached
    try:
        result = await asyncio.to_thread(_calc_stock_bond_ratio_sync)
        _STOCK_BOND_RATIO_CACHE["data"] = result
        _STOCK_BOND_RATIO_CACHE["ts"] = now
        return result
    except Exception as e:
        logger.warning(f"[stock_bond] 计算失败，降级返回: {e}")
        return cached or {"error": "数据暂时不可用"}


# ============ 恐贪指数 ============

def _calc_fear_greed_index_sync() -> dict:
    """恐贪指数同步实现（运行在线程池中）。"""
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
        if df is not None and len(df) >= 20:
            # 用标准差归一化，比简单除以100更敏感
            flows = [_safe_float(r.get("净流入", 0)) for _, r in df.iterrows()]
            recent_60 = flows[-60:] if len(flows) >= 60 else flows
            mean_flow = sum(recent_60) / len(recent_60)
            std_flow = (sum((f - mean_flow) ** 2 for f in recent_60) / len(recent_60)) ** 0.5
            recent_5 = sum(flows[-5:])
            # z-score: 近5日总量 vs 60日均值
            if std_flow > 0:
                z_score = (recent_5 - mean_flow * 5) / (std_flow * 5 ** 0.5)
            else:
                z_score = 0
            # z-score 映射到 0-100: -2→0, 0→50, +2→100
            factors["north_flow"] = max(0, min(100, (z_score / 4 + 0.5) * 100))
        else:
            factors["north_flow"] = 50
    except Exception:
        factors["north_flow"] = 50

    # 4. 换手率（权重15%）
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and len(df) > 0:
            # 用换手率列计算市场平均换手率
            turnover_col = None
            for col in df.columns:
                if "换手" in str(col):
                    turnover_col = col
                    break
            if turnover_col:
                avg_turnover = df[turnover_col].apply(lambda x: _safe_float(x)).mean()
                # 映射：换手率 < 1% → 恐惧(20), 1-3% → 中性(50), >5% → 贪婪(80)
                if avg_turnover < 0.5:
                    factors["turnover"] = 10
                elif avg_turnover < 1.0:
                    factors["turnover"] = 30
                elif avg_turnover < 3.0:
                    factors["turnover"] = 50
                elif avg_turnover < 5.0:
                    factors["turnover"] = 70
                else:
                    factors["turnover"] = 90
            else:
                factors["turnover"] = 50
        else:
            factors["turnover"] = 50
    except Exception:
        factors["turnover"] = 50

    # 5. 融资余额变化（权重10%）
    try:
        df = ak.stock_margin_sz()
        if df is not None and len(df) >= 5:
            recent = df.tail(5)
            # 找融资余额列
            margin_col = None
            for col in df.columns:
                if "融资余额" in str(col):
                    margin_col = col
                    break
            if margin_col:
                margin_now = _safe_float(recent.iloc[-1][margin_col])
                margin_5d = _safe_float(recent.iloc[0][margin_col])
                if margin_5d > 0:
                    change_pct = (margin_now - margin_5d) / margin_5d * 100
                    # 融资增加→贪婪，减少→恐惧
                    factors["margin"] = max(0, min(100, 50 + change_pct * 10))
                else:
                    factors["margin"] = 50
            else:
                factors["margin"] = 50
        else:
            factors["margin"] = 50
    except Exception:
        factors["margin"] = 50

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

    result = {
        "score": total_score,
        "zone": zone,
        "advice": advice,
        "factors": {k: round(v, 1) for k, v in factors.items()},
    }

    # === 恐贪指数增强：结合持仓给建议 ===
    try:
        holdings = list_holdings()
        score = total_score

        advice_actions = []
        for h in (holdings or [])[:10]:
            if (h.get("shares") or 0) <= 0:
                continue
            fund_name = h.get("fund_name", "")
            index_code = h.get("index_code", "")

            val = None
            if index_code:
                val = get_latest_valuation(index_code)

            percentile = val.get("percentile") if val else None

            if score < 30:  # 极度恐惧
                if percentile is not None and percentile < 30:
                    action = "可加仓"
                    reason = f"极度恐惧+低估值({percentile:.0f}%)，逆向买入机会"
                elif percentile is not None and percentile > 70:
                    action = "观望"
                    reason = f"虽恐惧但估值偏高({percentile:.0f}%)，不建议追"
                else:
                    action = "可小幅加仓"
                    reason = "极度恐惧时可逆向操作"
            elif score > 70:  # 极度贪婪
                if percentile is not None and percentile > 70:
                    action = "考虑减仓"
                    reason = f"极度贪婪+高估值({percentile:.0f}%)，风险较高"
                elif percentile is not None and percentile < 30:
                    action = "持有"
                    reason = "虽贪婪但估值低，安全边际足"
                else:
                    action = "谨慎持有"
                    reason = "极度贪婪时注意风险"
            else:  # 中性
                if percentile is not None and percentile < 30:
                    action = "可定投"
                    reason = f"估值低({percentile:.0f}%)，适合定投"
                elif percentile is not None and percentile > 70:
                    action = "观望"
                    reason = f"估值偏高({percentile:.0f}%)，等待回调"
                else:
                    action = "持有"
                    reason = "情绪中性+估值正常，保持配置"

            advice_actions.append({
                "fund_name": fund_name,
                "action": action,
                "reason": reason,
            })

        # 现金建议
        total_value = sum((h.get("current_value") or 0) for h in (holdings or []))
        cash_balance = 0
        try:
            cash_balance = get_total_cash_balance()
        except Exception:
            pass
        cash_pct = cash_balance / total_value * 100 if total_value > 0 else 0

        if score < 30:
            cash_advice = f"现金{cash_pct:.0f}%，恐惧时可用部分现金逆向布局"
        elif score > 70:
            cash_advice = f"现金{cash_pct:.0f}%，贪婪时保持充足现金应对回调"
        else:
            cash_advice = f"现金{cash_pct:.0f}%，配置适中"

        suggestion_map = {
            (0, 20): "极度恐惧！历史最佳买入窗口，建议分批加仓",
            (20, 40): "市场偏恐惧，可小幅定投优质标的",
            (40, 60): "情绪中性，保持现有配置，观察等待",
            (60, 80): "市场偏贪婪，注意控制仓位",
            (80, 100): "极度贪婪！建议逐步减仓锁定利润",
        }
        suggestion = ""
        for (low, high), text in suggestion_map.items():
            if low <= score < high:
                suggestion = text
                break

        result["portfolio_advice"] = {
            "suggestion": suggestion,
            "actions": advice_actions[:5],
            "cash_advice": cash_advice,
        }
    except Exception as e:
        logger.warning(f"[fear_greed] 生成持仓建议失败: {e}")
        result["portfolio_advice"] = None

    return result


async def calc_fear_greed_index() -> dict:
    """恐贪指数（0-100）：基于市场情绪指标（带 5 分钟内存缓存，akshare 调用在线程池执行）。"""
    now = time.time()
    cached = _FEAR_GREED_CACHE.get("data")
    if cached is not None and (now - _FEAR_GREED_CACHE["ts"]) < _CACHE_TTL:
        return cached
    try:
        result = await asyncio.to_thread(_calc_fear_greed_index_sync)
        _FEAR_GREED_CACHE["data"] = result
        _FEAR_GREED_CACHE["ts"] = now
        return result
    except Exception as e:
        logger.warning(f"[fear_greed] 计算失败，降级返回: {e}")
        return cached or {
            "score": 50,
            "zone": "中性",
            "advice": "数据暂时不可用",
            "factors": {},
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
