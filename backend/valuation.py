"""估值分析模块 — PE/PB 百分位法 + 综合投资建议"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from market_data import get_stock_history, get_stock_info, get_fund_nav, get_fund_info


def analyze_stock(symbol: str, user_valuation: dict = None, enable_fusion: bool = False) -> dict:
    """
    综合分析一只股票。

    参数:
        symbol: 股票代码，如 "600519"
        user_valuation: 用户提供的参考估值，如 {"pe": 11.5, "pb": 1.3, "pe_percentile": 0.25}

    返回:
        {
            "name": str,
            "code": str,
            "basic_info": dict,
            "price_stats": dict,
            "valuation": dict,
            "recommendation": str,   # "低估" / "合理偏低估" / "合理" / "合理偏高估" / "高估"
            "reason": str,
            "score": int,            # 0-100, 越低越值得买
        }
    """
    info = get_stock_info(symbol)
    name = info.get("name", symbol)

    # 获取历史行情计算价格统计
    try:
        df = get_stock_history(symbol, days=365)
        price_stats = _calc_price_stats(df)
    except Exception:
        price_stats = {}

    # 估值分析
    pe = info.get("pe")
    pb = info.get("pb")
    valuation = {"pe": pe, "pb": pb}

    # 综合评分
    score, recommendation, reason = _score_stock(
        pe, pb, price_stats, user_valuation
    )

    result = {
        "name": name,
        "code": symbol,
        "basic_info": info,
        "price_stats": price_stats,
        "valuation": valuation,
        "recommendation": recommendation,
        "reason": reason,
        "score": score,
    }

    # 估值融合层（可选）：LLM 结合基本面/情绪/画像做二次判断 + 可解释溯源
    if enable_fusion:
        try:
            from valuation_fusion import fusion_score
            from agent.kyc import get_kyc_profile
            result["fusion"] = fusion_score(result, get_kyc_profile("default"))
        except Exception as e:
            import logging
            logging.warning(f"估值融合附加失败: {e}")

    return result


def analyze_fund(fund_code: str, user_valuation: dict = None) -> dict:
    """
    综合分析一只基金。

    参数:
        fund_code: 基金代码，如 "110011"
        user_valuation: 参考估值

    返回:
        同 analyze_stock 结构
    """
    info = get_fund_info(fund_code)
    name = info.get("name", fund_code)

    try:
        df = get_fund_nav(fund_code)
        price_stats = _calc_fund_stats(df)
    except Exception:
        price_stats = {}

    # ── 估值：通过跟踪指数查找 PE/PB 百分位 ──
    valuation = {}
    index_valuation = None
    try:
        from db.portfolio import lookup_fund_info
        from db.valuations import search_indexes_by_keyword, get_best_valuation

        fund_detail = lookup_fund_info(fund_code)
        tracking_index = (fund_detail or {}).get("tracking_index", "")

        if tracking_index:
            search_term = tracking_index.replace("指数", "").replace("中证", "").replace("全指", "")
            matches = search_indexes_by_keyword(search_term)

            pe_val = pb_val = pe_pct = pb_pct = None
            index_code = ""
            index_name = ""

            for m in matches:
                code = m["index_code"]
                pe_data = get_best_valuation(code, "市盈率")
                if pe_data and pe_data.get("current_value") is not None:
                    pe_val = pe_data["current_value"]
                    pe_pct = pe_data.get("percentile")
                    index_code = code
                    index_name = pe_data.get("index_name", m.get("index_name", ""))
                pb_data = get_best_valuation(code, "市净率")
                if pb_data and pb_data.get("current_value") is not None:
                    pb_val = pb_data["current_value"]
                    pb_pct = pb_data.get("percentile")
                    if not index_name:
                        index_name = pb_data.get("index_name", m.get("index_name", ""))
                if pe_val is not None or pb_val is not None:
                    break

            valuation = {
                "pe": pe_val,
                "pb": pb_val,
                "pe_percentile": pe_pct,
                "pb_percentile": pb_pct,
                "index_code": index_code,
                "index_name": index_name,
                "tracking_index": tracking_index,
            }
            index_valuation = valuation
    except Exception as e:
        import logging
        logging.warning(f"基金估值查询失败 ({fund_code}): {e}")

    score, recommendation, reason = _score_fund(price_stats, user_valuation, index_valuation)

    return {
        "name": name,
        "code": fund_code,
        "basic_info": info,
        "price_stats": price_stats,
        "valuation": valuation,
        "recommendation": recommendation,
        "reason": reason,
        "score": score,
    }


def compare_valuation(target_pe: float, target_pb: float,
                      index_pe: float, index_pb: float,
                      index_pe_pct: float = None,
                      index_pb_pct: float = None) -> dict:
    """
    对比目标标的和指数估值。

    返回:
        {
            "pe_ratio": float,          # 目标PE / 指数PE
            "pb_ratio": float,          # 目标PB / 指数PB
            "valuation_level": str,     # "低估" / "合理" / "高估"
            "reason": str,
        }
    """
    result = {"pe_ratio": None, "pb_ratio": None, "valuation_level": "未知", "reason": ""}

    reasons = []

    if target_pe and index_pe and target_pe > 0 and index_pe > 0:
        ratio = target_pe / index_pe
        result["pe_ratio"] = round(ratio, 2)
        if ratio < 0.8:
            reasons.append(f"PE 相对指数偏低 ({ratio:.1%})")
        elif ratio > 1.2:
            reasons.append(f"PE 相对指数偏高 ({ratio:.1%})")
        else:
            reasons.append(f"PE 与指数接近 ({ratio:.1%})")

    if target_pb and index_pb and target_pb > 0 and index_pb > 0:
        ratio = target_pb / index_pb
        result["pb_ratio"] = round(ratio, 2)
        if ratio < 0.8:
            reasons.append(f"PB 相对指数偏低 ({ratio:.1%})")
        elif ratio > 1.2:
            reasons.append(f"PB 相对指数偏高 ({ratio:.1%})")

    # 综合判断
    if index_pe_pct is not None:
        if index_pe_pct < 0.3:
            reasons.append(f"指数 PE 百分位 {index_pe_pct:.0%}，整体低估区间")
        elif index_pe_pct > 0.7:
            reasons.append(f"指数 PE 百分位 {index_pe_pct:.0%}，整体高估区间")

    result["reason"] = "；".join(reasons) if reasons else "数据不足，无法判断"

    # 简单判断
    pe_ok = result["pe_ratio"] is not None and result["pe_ratio"] < 1.0
    pb_ok = result["pb_ratio"] is not None and result["pb_ratio"] < 1.0
    if pe_ok and pb_ok:
        result["valuation_level"] = "低估"
    elif pe_ok or pb_ok:
        result["valuation_level"] = "合理偏低"
    else:
        pe_high = result["pe_ratio"] is not None and result["pe_ratio"] > 1.2
        pb_high = result["pb_ratio"] is not None and result["pb_ratio"] > 1.2
        if pe_high and pb_high:
            result["valuation_level"] = "高估"
        else:
            result["valuation_level"] = "合理"

    return result


def generate_report(analyses: list, index_valuation: dict = None) -> str:
    """
    生成 Markdown 格式的投资分析报告。

    参数:
        analyses: analyze_stock / analyze_fund 返回结果的列表
        index_valuation: 指数估值参考
    """
    lines = ["# 投资分析报告\n"]
    lines.append(f"生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")

    if index_valuation:
        lines.append("## 参考指数估值\n")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 指数 | {index_valuation.get('index_name', '-')} |")
        pe = index_valuation.get("pe")
        lines.append(f"| PE | {pe:.2f if pe else '-'} |")
        pe_pct = index_valuation.get("pe_percentile")
        lines.append(f"| PE 百分位 | {pe_pct:.0% if pe_pct else '-'} |")
        pb = index_valuation.get("pb")
        lines.append(f"| PB | {pb:.2f if pb else '-'} |")
        pb_pct = index_valuation.get("pb_percentile")
        lines.append(f"| PB 百分位 | {pb_pct:.0% if pb_pct else '-'} |")
        lines.append("")

    lines.append("## 分析结果\n")
    lines.append("| 标的 | 代码 | PE | PB | 建议 | 评分 | 理由 |")
    lines.append("|------|------|----|----|------|------|------|")

    for a in analyses:
        v = a.get("valuation", {})
        pe_str = f"{v['pe']:.1f}" if v.get("pe") else "-"
        pb_str = f"{v['pb']:.1f}" if v.get("pb") else "-"
        lines.append(
            f"| {a['name']} | {a['code']} | {pe_str} | {pb_str} "
            f"| {a['recommendation']} | {a['score']} | {a['reason']} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("*评分说明：0-30 低估（可考虑买入）、30-70 合理（持有/观望）、70-100 高估（谨慎）*")

    return "\n".join(lines)


def plot_stock_chart(symbol: str, days: int = 180) -> go.Figure:
    """绘制股票 K 线图 + 成交量。"""
    df = get_stock_history(symbol, days=days)
    info = get_stock_info(symbol)
    name = info.get("name", symbol)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03, row_heights=[0.7, 0.3],
    )

    fig.add_trace(
        go.Candlestick(
            x=df["日期"], open=df["开盘"], high=df["最高"],
            low=df["最低"], close=df["收盘"], name="K线",
        ),
        row=1, col=1,
    )

    # 均线
    if len(df) >= 20:
        df["MA20"] = df["收盘"].rolling(20).mean()
        fig.add_trace(
            go.Scatter(x=df["日期"], y=df["MA20"], name="MA20",
                       line=dict(color="orange", width=1)),
            row=1, col=1,
        )
    if len(df) >= 60:
        df["MA60"] = df["收盘"].rolling(60).mean()
        fig.add_trace(
            go.Scatter(x=df["日期"], y=df["MA60"], name="MA60",
                       line=dict(color="purple", width=1)),
            row=1, col=1,
        )

    # 成交量
    colors = ["red" if c >= o else "green"
              for c, o in zip(df["收盘"], df["开盘"])]
    fig.add_trace(
        go.Bar(x=df["日期"], y=df["成交量"], name="成交量",
               marker_color=colors, opacity=0.5),
        row=2, col=1,
    )

    fig.update_layout(
        title=f"{name} ({symbol})",
        xaxis_rangeslider_visible=False,
        height=600,
        template="plotly_white",
    )
    return fig


def plot_fund_chart(fund_code: str) -> go.Figure:
    """绘制基金净值曲线。"""
    df = get_fund_nav(fund_code)
    info = get_fund_info(fund_code)
    name = info.get("name", fund_code)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["净值日期"], y=df["累计净值"],
            mode="lines", name="累计净值",
            line=dict(color="royalblue", width=2),
        )
    )
    fig.update_layout(
        title=f"{name} ({fund_code}) 累计净值",
        height=400,
        template="plotly_white",
        xaxis_title="日期",
        yaxis_title="累计净值",
    )
    return fig


# ── 内部辅助函数 ──────────────────────────────────────────────


def _calc_price_stats(df: pd.DataFrame) -> dict:
    """从行情 DataFrame 计算价格统计。"""
    if df.empty:
        return {}
    close = df["收盘"]
    latest = close.iloc[-1]
    return {
        "latest_price": round(latest, 2),
        "high_52w": round(close.max(), 2),
        "low_52w": round(close.min(), 2),
        "price_vs_high": round((latest / close.max() - 1) * 100, 1),  # 距高点跌幅 %
        "price_vs_low": round((latest / close.min() - 1) * 100, 1),   # 距低点涨幅 %
        "avg_volume": round(df["成交量"].mean(), 0),
        "change_pct_20d": round(
            (latest / close.iloc[max(0, len(close) - 20)] - 1) * 100, 1
        ),
        "data_days": len(df),
    }


def _calc_fund_stats(df: pd.DataFrame) -> dict:
    """从净值 DataFrame 计算基金统计。"""
    if df.empty:
        return {}
    nav = df["累计净值"]
    latest = nav.iloc[-1]
    return {
        "latest_nav": round(latest, 4),
        "high": round(nav.max(), 4),
        "low": round(nav.min(), 4),
        "nav_vs_high": round((latest / nav.max() - 1) * 100, 1),
        "nav_vs_low": round((latest / nav.min() - 1) * 100, 1),
        "data_days": len(df),
    }


def _score_stock(pe, pb, price_stats, user_val) -> tuple:
    """
    综合评分。

    返回 (score, recommendation, reason)
    score: 0-100, 越低越值得买
    """
    score = 50  # 中性起点
    reasons = []

    # PE 评分
    if pe is not None and pe > 0:
        if pe < 15:
            score -= 15
            reasons.append(f"PE={pe:.1f} 较低")
        elif pe < 25:
            score -= 5
            reasons.append(f"PE={pe:.1f} 合理")
        elif pe < 50:
            score += 10
            reasons.append(f"PE={pe:.1f} 偏高")
        else:
            score += 25
            reasons.append(f"PE={pe:.1f} 很高")

    # PB 评分
    if pb is not None and pb > 0:
        if pb < 1.0:
            score -= 10
            reasons.append(f"PB={pb:.1f} 低于净资产")
        elif pb < 2.0:
            score -= 3
        elif pb > 5.0:
            score += 15
            reasons.append(f"PB={pb:.1f} 较高")

    # 价格位置
    if price_stats:
        vs_high = price_stats.get("price_vs_high", 0)
        if vs_high < -30:
            score -= 10
            reasons.append(f"距52周高点跌 {abs(vs_high):.0f}%")
        elif vs_high > -5:
            score += 5
            reasons.append("接近52周高点")

    # 用户提供的指数估值参考
    if user_val:
        pe_pct = user_val.get("pe_percentile")
        if pe_pct is not None:
            if pe_pct < 0.3:
                score -= 10
                reasons.append(f"指数PE百分位 {pe_pct:.0%}，低估区间")
            elif pe_pct > 0.7:
                score += 10
                reasons.append(f"指数PE百分位 {pe_pct:.0%}，高估区间")

    # 限制在 0-100
    score = max(0, min(100, score))

    # 建议
    if score <= 30:
        rec = "低估"
    elif score <= 45:
        rec = "合理偏低"
    elif score <= 60:
        rec = "合理"
    elif score <= 75:
        rec = "合理偏高"
    else:
        rec = "高估"

    return score, rec, "；".join(reasons) if reasons else "数据不足"


def _score_fund(price_stats, user_val, index_val=None) -> tuple:
    """基金评分。"""
    score = 50
    reasons = []

    if price_stats:
        vs_high = price_stats.get("nav_vs_high", 0)
        if vs_high < -20:
            score -= 15
            reasons.append(f"净值距高点跌 {abs(vs_high):.0f}%，处于低位")
        elif vs_high < -10:
            score -= 5
            reasons.append(f"净值距高点跌 {abs(vs_high):.0f}%")
        elif vs_high > -3:
            score += 10
            reasons.append("净值接近历史高点")

    # 跟踪指数估值
    if index_val:
        pe_pct = index_val.get("pe_percentile")
        pb_pct = index_val.get("pb_percentile")
        idx_name = index_val.get("index_name", "")

        if pe_pct is not None:
            if pe_pct < 20:
                score -= 15
                reasons.append(f"{idx_name} PE百分位 {pe_pct:.0f}%，深度低估")
            elif pe_pct < 30:
                score -= 10
                reasons.append(f"{idx_name} PE百分位 {pe_pct:.0f}%，低估区间")
            elif pe_pct < 50:
                score -= 5
                reasons.append(f"{idx_name} PE百分位 {pe_pct:.0f}%，合理偏低")
            elif pe_pct > 80:
                score += 15
                reasons.append(f"{idx_name} PE百分位 {pe_pct:.0f}%，高估区间")
            elif pe_pct > 70:
                score += 10
                reasons.append(f"{idx_name} PE百分位 {pe_pct:.0f}%，偏高")
            else:
                reasons.append(f"{idx_name} PE百分位 {pe_pct:.0f}%")

        if pb_pct is not None and pe_pct is not None:
            diverge = abs(pe_pct - pb_pct)
            if diverge > 20:
                better = "PB" if pb_pct < pe_pct else "PE"
                reasons.append(f"PE/PB百分位背离{diverge:.0f}%，建议参考{better}")

    if user_val:
        pe_pct = user_val.get("pe_percentile")
        if pe_pct is not None:
            if pe_pct < 0.3:
                score -= 10
                reasons.append(f"参考指数PE百分位 {pe_pct:.0%}")
            elif pe_pct > 0.7:
                score += 10

    score = max(0, min(100, score))

    if score <= 30:
        rec = "低估"
    elif score <= 45:
        rec = "合理偏低"
    elif score <= 60:
        rec = "合理"
    elif score <= 75:
        rec = "合理偏高"
    else:
        rec = "高估"

    return score, rec, "；".join(reasons) if reasons else "数据不足"


def check_pe_pb_divergence(index_code: str) -> dict | None:
    """检查同一指数的 PE 和 PB 百分位背离情况。

    返回 warning dict 或 None：
    {
        "type": "pe_pb_diverge",
        "level": "danger" | "warning" | "info",
        "message": str,
        "pe_percentile": float,
        "pb_percentile": float,
        "diverge_pct": float,
        "recommended_metric": "PB" | "PE",
    }
    """
    from db.valuations import get_latest_valuation

    pe_row = get_latest_valuation(index_code, metric_type="市盈率", max_days=30)
    pb_row = get_latest_valuation(index_code, metric_type="市净率", max_days=30)

    if not pe_row or not pb_row:
        return None

    pe_pct = pe_row.get("percentile")
    pb_pct = pb_row.get("percentile")

    if pe_pct is None or pb_pct is None:
        return None

    diverge = abs(pe_pct - pb_pct)

    if diverge < 10:
        return None

    # 谁更低就推荐谁（更稳定的指标）
    if pb_pct < pe_pct:
        recommended = "PB"
        stable_pct = pb_pct
    else:
        recommended = "PE"
        stable_pct = pe_pct

    if diverge > 30:
        level = "danger"
        msg = f"PE百分位({pe_pct:.1f}%)与PB百分位({pb_pct:.1f}%)严重背离({diverge:.0f}%)，盈利波动导致PE失真，强烈建议参考{recommended}百分位({stable_pct:.1f}%)"
    elif diverge > 20:
        level = "warning"
        msg = f"PE百分位({pe_pct:.1f}%)与PB百分位({pb_pct:.1f}%)差异较大({diverge:.0f}%)，建议参考{recommended}百分位({stable_pct:.1f}%)"
    else:
        level = "info"
        msg = f"PE百分位({pe_pct:.1f}%)与PB百分位({pb_pct:.1f}%)存在差异({diverge:.0f}%)，可结合参考"

    return {
        "type": "pe_pb_diverge",
        "level": level,
        "message": msg,
        "pe_percentile": pe_pct,
        "pb_percentile": pb_pct,
        "diverge_pct": diverge,
        "recommended_metric": recommended,
    }


def get_index_history_years(index_code: str) -> float:
    """获取指数数据覆盖年限（年）。"""
    from db._conn import _get_conn

    conn = _get_conn()
    row = conn.execute("""
        SELECT MIN(snapshot_date) as earliest, MAX(snapshot_date) as latest
        FROM index_valuations
        WHERE index_code = ?
    """, (index_code,)).fetchone()
    conn.close()

    if not row or not row["earliest"] or not row["latest"]:
        return 0

    from datetime import datetime
    try:
        d1 = datetime.strptime(row["earliest"], "%Y-%m-%d")
        d2 = datetime.strptime(row["latest"], "%Y-%m-%d")
        years = (d2 - d1).days / 365.25
        return round(years, 1)
    except Exception:
        return 0


def get_history_years_warning(years: float) -> dict | None:
    """根据覆盖年限返回警告信息。"""
    if years <= 0:
        return None
    if years < 3:
        return {"level": "danger", "message": f"数据仅覆盖{years}年，不足3年，百分位参考价值很低"}
    if years < 5:
        return {"level": "warning", "message": f"数据覆盖{years}年，未经历完整牛熊周期，百分位需谨慎参考"}
    if years < 7:
        return {"level": "info", "message": f"数据覆盖{years}年，基本可信"}
    return None
