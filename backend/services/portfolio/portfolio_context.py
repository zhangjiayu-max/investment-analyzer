"""持仓上下文构建 — 为 AI 分析提供统一的持仓+现金+估值数据。"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def build_portfolio_context(user_id: str = "default") -> str:
    """构建持仓+现金的紧凑上下文文本，供 Orchestrator 和专家 Agent 使用。

    返回格式化的 Markdown 文本，包含：
    - 总资产概览（持仓市值 + 现金）
    - 持仓明细（基金名称、市值、占比、盈亏率）
    - 资产分布（股票型/债券型/货币型/其他）
    - 现金余额及占比
    - 集中度指标（前3/前5）
    """
    try:
        from db import list_holdings, get_cash_balance

        holdings = list_holdings(user_id)
        active = [h for h in holdings if (h.get("shares") or 0) > 0]

        if not active:
            return "用户当前无持仓记录。请勿编造任何持仓数据，仅基于市场公开信息进行分析。"

        # 现金
        cash_info = get_cash_balance(user_id)
        cash_balance = cash_info.get("balance", 0) if cash_info else 0

        # 总资产
        total_value = sum(h.get("current_value", 0) or 0 for h in active)
        total_cost = sum(h.get("total_cost", 0) or 0 for h in active)
        total_assets = total_value + cash_balance
        total_profit = total_value - total_cost
        profit_rate = total_profit / total_cost if total_cost > 0 else 0

        if total_assets <= 0:
            return ""

        # ── 持仓明细 ──
        lines = []
        lines.append(f"总资产: ¥{total_assets:,.0f}（持仓 ¥{total_value:,.0f} + 现金 ¥{cash_balance:,.0f}）")
        lines.append(f"总盈亏: ¥{total_profit:,.0f}（{profit_rate:+.2%}）")
        lines.append("")

        # 按市值排序
        sorted_holdings = sorted(active, key=lambda h: h.get("current_value", 0) or 0, reverse=True)

        lines.append("### 持仓明细")
        lines.append("| 基金名称 | 市值 | 占比 | 盈亏率 | 成本价 | 份额 | 上次买入 | 盈亏 |")
        lines.append("|---------|------|------|--------|--------|------|----------|------|")
        for h in sorted_holdings:
            name = h.get("fund_name", h.get("fund_code", "未知"))
            value = h.get("current_value", 0) or 0
            pct = value / total_assets if total_assets > 0 else 0
            pr = h.get("profit_rate", 0) or 0
            pr_pct = f"{pr:+.2%}" if isinstance(pr, (int, float)) else "N/A"
            # 成本价
            cp = h.get("cost_price")
            cp_str = f"¥{cp:,.4f}" if cp and cp > 0 else "-"
            # 持有份额
            shares = h.get("shares")
            shares_str = f"{shares:,.0f}" if shares and shares > 0 else "-"
            # 上次买入（日期/价格）
            lbd = h.get("last_buy_date", "")
            lbp = h.get("last_buy_price")
            lb_str = f"{lbd} / ¥{lbp:,.4f}" if lbd and lbp and lbp > 0 else (lbd or "-")
            # 盈亏金额
            pl = h.get("profit_loss", 0) or 0
            pl_str = f"¥{pl:+,.0f}" if isinstance(pl, (int, float)) else "-"
            lines.append(f"| {name} | ¥{value:,.0f} | {pct:.1%} | {pr_pct} | {cp_str} | {shares_str} | {lb_str} | {pl_str} |")

        # ── 资产分布 ──
        lines.append("")
        lines.append("### 资产分布")
        category_map = {}
        for h in active:
            cat = h.get("fund_category") or "equity"
            cat_label = {
                "equity": "股票型",
                "bond": "债券型",
                "money": "货币型",
                "hybrid": "混合型",
                "index": "指数型",
                "qdii": "QDII",
            }.get(cat, cat)
            category_map.setdefault(cat_label, 0)
            category_map[cat_label] += h.get("current_value", 0) or 0

        for cat_label, cat_value in sorted(category_map.items(), key=lambda x: -x[1]):
            cat_pct = cat_value / total_assets if total_assets > 0 else 0
            lines.append(f"- {cat_label}: ¥{cat_value:,.0f}（{cat_pct:.1%}）")

        # 现金占比
        cash_pct = cash_balance / total_assets if total_assets > 0 else 0
        lines.append(f"- 现金: ¥{cash_balance:,.0f}（{cash_pct:.1%}）")

        # ── 集中度 ──
        lines.append("")
        if len(sorted_holdings) >= 3:
            top3_value = sum(h.get("current_value", 0) or 0 for h in sorted_holdings[:3])
            top3_pct = top3_value / total_assets if total_assets > 0 else 0
            lines.append(f"前3集中度: {top3_pct:.1%}")
        if len(sorted_holdings) >= 5:
            top5_value = sum(h.get("current_value", 0) or 0 for h in sorted_holdings[:5])
            top5_pct = top5_value / total_assets if total_assets > 0 else 0
            lines.append(f"前5集中度: {top5_pct:.1%}")

        # 新鲜度标注
        from datetime import datetime as _dt
        lines.append("")
        lines.append(f"> 📅 持仓数据最后更新: {_dt.now().strftime('%Y-%m-%d %H:%M')}。净值可能有延迟，以基金公司公布为准。")

        # ── 最近交易记录（10条）──
        try:
            from db._conn import _get_conn as _get_conn_inner
            _conn = _get_conn_inner()
            _trades = _conn.execute("""
                SELECT transaction_date, fund_code, fund_name, transaction_type, amount, shares, status
                FROM portfolio_transactions
                WHERE user_id = ? AND status IN ('confirmed', 'pending')
                ORDER BY transaction_date DESC, created_at DESC
                LIMIT 10
            """, (user_id,)).fetchall()
            _conn.close()
            if _trades:
                lines.append("")
                lines.append("### 最近交易记录")
                lines.append("| 日期 | 基金 | 操作 | 金额 | 份额 | 状态 |")
                lines.append("|------|------|------|------|------|------|")
                for _t in _trades:
                    _td = dict(_t) if not isinstance(_t, dict) else _t
                    _type_label = {"buy": "买入", "sell": "卖出", "dividend": "分红"}.get(_td.get("transaction_type", ""), _td.get("transaction_type", ""))
                    _status_label = {"confirmed": "已确认", "pending": "待确认"}.get(_td.get("status", ""), _td.get("status", ""))
                    _name = _td.get("fund_name") or _td.get("fund_code", "未知")
                    _amt = _td.get("amount", 0) or 0
                    _shares = _td.get("shares", 0) or 0
                    lines.append(f"| {_td.get('transaction_date', '')} | {_name} | {_type_label} | ¥{_amt:,.0f} | {_shares:,.0f} | {_status_label} |")
                lines.append("")
                lines.append("> ⚠️ 请务必结合上述交易记录分析。用户近期已执行的操作（尤其是已卖出的基金）不应再建议卖出。")
        except Exception as _te:
            logger.debug(f"交易记录注入失败: {_te}")

        # ── 持仓上限与减仓约束 ──
        lines.append("")
        lines.append("### 持仓上限")
        try:
            from db.config import get_config_int
            single_pct = get_config_int("daily_advice.default_single_position_pct", 15)
        except Exception:
            single_pct = 15
        lines.append(f"- 单只基金/标的持仓上限: {single_pct}%（系统配置）")
        lines.append("- 加仓前必须检查目标基金当前占比是否已达上限，如已超限禁止加仓")

        lines.append("")
        lines.append("### 减仓约束（必须遵守）")
        lines.append("- 单基金单次减仓金额 ≤ 该基金持仓市值的 20%")
        lines.append("- 单次建议总减仓金额 ≤ 总资产的 10%")
        lines.append("- 单基金单次减仓上限: ¥50,000")
        lines.append("- 单次建议最多减仓 2 只基金")
        lines.append("- 近期（10天内）已卖出基金不得再建议卖出")
        lines.append("- 减仓仅在估值百分位 ≥ 80% 时考虑")

        # 新鲜度标注
        from datetime import datetime as _dt2
        lines.append("")
        lines.append(f"> 📅 持仓数据最后更新: {_dt2.now().strftime('%Y-%m-%d %H:%M')}。净值可能有延迟，以基金公司公布为准。")

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"构建持仓上下文失败: {e}")
        return ""


def build_bond_fund_holdings_context(user_id: str = "default") -> str:
    """构建债券/混合型基金的底层持仓上下文，供专家分析亏损归因时使用。

    仅当组合中债券占比 > 50% 时才返回有意义的内容，否则返回空字符串。
    获取每个债券/混合型基金的重仓股、可转债占比、资产配置，
    超时 5s/基金，失败跳过。
    """
    try:
        from db import list_holdings, get_cash_balance
        from db.portfolio import get_fund_holdings

        holdings = list_holdings(user_id)
        active = [h for h in holdings if (h.get("shares") or 0) > 0]
        if not active:
            return ""

        total_value = sum(h.get("current_value", 0) or 0 for h in active)
        if total_value <= 0:
            return ""

        # 计算债券+混合型占比
        bond_value = 0
        bond_funds = []
        for h in active:
            cat = h.get("fund_category") or "equity"
            value = h.get("current_value", 0) or 0
            if cat in ("bond", "hybrid"):
                bond_value += value
                bond_funds.append(h)

        if total_value <= 0 or bond_value / total_value < 0.3:
            return ""  # 债券占比 < 30%，不需要底层持仓分析

        lines = ["## 债券/混合型基金底层持仓分析"]
        lines.append("> 以下数据用于亏损归因分析，帮助判断下跌是来自权益端还是固收端。")
        lines.append("")

        for h in bond_funds[:5]:  # 最多 5 只
            fund_code = h.get("fund_code", "")
            fund_name = h.get("fund_name", fund_code)
            value = h.get("current_value", 0) or 0
            pct = value / total_value * 100
            pr = h.get("profit_rate", 0) or 0

            # 获取基金底层持仓（超时 5s）
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(get_fund_holdings, fund_code)
                    try:
                        fund_data = future.result(timeout=5)
                    except concurrent.futures.TimeoutError:
                        fund_data = None
            except Exception:
                fund_data = None

            if not fund_data:
                continue

            lines.append(f"### {fund_name}（{fund_code}）")
            lines.append(f"- 占组合: {pct:.1f}% | 盈亏率: {pr:+.2%}")

            # 股票重仓 top 3
            stocks = fund_data.get("top_stocks", [])
            if stocks:
                stock_strs = []
                for s in stocks[:3]:
                    sn = s.get("stock_name", "?")
                    sp = s.get("pct_nav", "?")
                    stock_strs.append(f"{sn}({sp}%)")
                lines.append(f"- 重仓股: {', '.join(stock_strs)}")

            # 可转债占比
            bond_summary = fund_data.get("bond_type_summary", {})
            if bond_summary:
                cb_pct = bond_summary.get("可转债", 0)
                if cb_pct and cb_pct > 1:
                    parts = [f"- 可转债占比: {cb_pct:.1f}%"]
                    if cb_pct > 5:
                        parts.append("⚠️ 偏高，股市波动时风险较大")
                    lines.append("".join(parts))

            # 资产配置
            alloc = fund_data.get("asset_allocation", [])
            if alloc:
                stock_pct = None
                bond_pct = None
                for a in alloc:
                    t = a.get("type", "")
                    try:
                        p = float(str(a.get("pct", "0")).replace("%", ""))
                    except (ValueError, TypeError):
                        p = 0
                    if "股票" in t or "权益" in t:
                        stock_pct = p
                    elif "债券" in t or "固收" in t:
                        bond_pct = p
                if stock_pct is not None:
                    lines.append(f"- 股票仓位: {stock_pct:.0f}%")
                if bond_pct is not None:
                    lines.append(f"- 债券仓位: {bond_pct:.0f}%")

            lines.append("")

        if len(lines) <= 3:
            return ""  # 没有获取到任何有效数据

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"构建债券持仓上下文失败: {e}")
        return ""


def build_portfolio_summary_line(user_id: str = "default") -> str:
    """构建单行持仓摘要，用于 clarify_requirement 等对 token 敏感的场景。

    示例输出: "持仓: 沪深300ETF(30%), 中证500ETF(20%), 恒乐债券A(40%), 现金(10%)"
    """
    try:
        from db import list_holdings, get_cash_balance

        holdings = list_holdings(user_id)
        active = [h for h in holdings if (h.get("shares") or 0) > 0]

        cash_info = get_cash_balance(user_id)
        cash_balance = cash_info.get("balance", 0) if cash_info else 0

        total_value = sum(h.get("current_value", 0) or 0 for h in active)
        total_assets = total_value + cash_balance

        if total_assets <= 0:
            return ""

        parts = []
        sorted_holdings = sorted(active, key=lambda h: h.get("current_value", 0) or 0, reverse=True)
        for h in sorted_holdings[:6]:  # 最多显示 6 只
            name = h.get("fund_name", h.get("fund_code", "未知"))
            # 缩短名称
            for suffix in ["证券投资基金A类", "证券投资基金C类", "证券投资基金", "A类", "C类"]:
                name = name.replace(suffix, "")
            value = h.get("current_value", 0) or 0
            pct = value / total_assets if total_assets > 0 else 0
            parts.append(f"{name}({pct:.0%})")

        if cash_balance > 0:
            cash_pct = cash_balance / total_assets if total_assets > 0 else 0
            parts.append(f"现金({cash_pct:.0%})")

        return "持仓: " + ", ".join(parts)

    except Exception as e:
        logger.warning(f"构建持仓摘要失败: {e}")
        return ""


def build_valuation_summary() -> str:
    """构建估值摘要上下文，供 Orchestrator 使用。

    返回格式化的估值数据，包含所有跟踪指数的 PE/PB/百分位。
    """
    try:
        from db import list_valuation_indexes

        valuations = list_valuation_indexes()
        if not valuations:
            return ""

        # 按指数聚合 PE 和 PB
        index_map = {}
        for v in valuations:
            code = v.get("index_code", "")
            name = v.get("index_name", code)
            metric = v.get("metric_type", "")
            value = v.get("current_value")
            percentile = v.get("percentile")

            if code not in index_map:
                index_map[code] = {"name": name, "pe": "-", "pb": "-", "percentile": None}

            if metric == "市盈率" and value is not None:
                index_map[code]["pe"] = f"{value:.1f}"
                if percentile is not None:
                    index_map[code]["percentile"] = percentile
            elif metric == "市净率" and value is not None:
                index_map[code]["pb"] = f"{value:.2f}"
                if percentile is not None and index_map[code]["percentile"] is None:
                    index_map[code]["percentile"] = percentile

        lines = ["### 当前市场估值"]

        # R5: 检测过期估值数据（仅查本地，不触发在线兜底，避免批量场景串行超时）
        expired_warnings = []
        for code, info in index_map.items():
            try:
                from db.valuations import get_best_valuation
                best = get_best_valuation(code, "市盈率", query_source="portfolio", enable_online=False)
                if best and best.get("days_old", 0) > 7:
                    expired_warnings.append(f"{info['name']} PE: {best['days_old']}天前 ({best.get('snapshot_date','?')})")
                elif best and best.get("days_old", 0) > 3:
                    expired_warnings.append(f"{info['name']} PE: {best['days_old']}天前")
            except Exception:
                pass

        if expired_warnings:
            lines.append("")
            lines.append("### ⚠️ 数据时效性警告")
            lines.append("以下指数估值数据超过7天，分析时请明确标注数据时效性风险：")
            for w in expired_warnings:
                lines.append(f"- {w}")

        lines.append("")
        lines.append("| 指数 | PE | PB | 百分位 | 水平 |")
        lines.append("|------|-----|-----|--------|------|")

        for code, info in index_map.items():
            percentile = info["percentile"]
            if percentile is None:
                continue

            if percentile <= 20:
                level = "极度低估"
            elif percentile <= 30:
                level = "低估"
            elif percentile <= 70:
                level = "合理"
            elif percentile <= 80:
                level = "偏高"
            else:
                level = "极度高估"

            lines.append(f"| {info['name']} | {info['pe']} | {info['pb']} | {percentile:.0f}% | {level} |")

        if len(lines) <= 3:
            return ""

        # 新鲜度标注
        lines.append("")
        lines.append("> 📅 以上估值数据基于最近一次入库快照。如需最新数据，请使用「估值更新」工具。")

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"构建估值摘要失败: {e}")
        return ""
