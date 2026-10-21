"""持仓上下文构建 — 为 AI 分析提供统一的持仓+现金+估值数据。"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _format_holdings_source(data_source: str, report_date: str,
                            snapshot_updated: str, is_stale: bool) -> str:
    """P1-3: 格式化基金持仓数据来源标注行。

    Returns:
        格式化后的标注行（如 "- 数据来源: akshare（截至 2025-09-30）"）。
        若无法识别来源，返回空字符串。
    """
    ds = (data_source or "").strip()
    rd = (report_date or "").strip()
    su = (snapshot_updated or "").strip()

    if ds == "akshare":
        date_part = f"（截至 {rd}）" if rd else ""
        return f"- 数据来源: akshare{date_part}"
    if ds == "ttfund_fallback":
        date_part = f"（截至 {rd}）" if rd else ""
        return f"- 数据来源: ttfund 兜底{date_part} ⚠️ akshare 主源失效"
    if ds == "local_snapshot_stale":
        date_part = f"（快照更新于 {su}）" if su else ""
        return (
            f"- 数据来源: 本地快照{date_part} ⚠️ 数据可能过期，置信度降低"
            if is_stale else f"- 数据来源: 本地快照{date_part}"
        )
    if ds == "akshare_partial_failure":
        return "- 数据来源: akshare 部分失效 ⚠️ 数据不完整，建议人工核实"
    if ds:
        return f"- 数据来源: {ds}"
    return ""


def build_portfolio_context(user_id: str = "default", snapshot: dict | None = None) -> str:
    """构建持仓+现金的紧凑上下文文本，供 Orchestrator 和专家 Agent 使用。

    返回格式化的 Markdown 文本，包含：
    - 总资产概览（持仓市值 + 现金）
    - 持仓明细（基金名称、市值、占比、盈亏率）
    - 资产分布（股票型/债券型/货币型/其他）
    - 现金余额及占比
    - 集中度指标（前3/前5）
    """
    try:
        from db import get_portfolio_snapshot

        snapshot = snapshot or get_portfolio_snapshot(user_id)
        active = snapshot.get("funds", [])

        if not active:
            return "用户当前无持仓记录。请勿编造任何持仓数据，仅基于市场公开信息进行分析。"

        # 现金
        cash_balance = snapshot.get("cash_balance", 0) or 0

        # 总资产
        total_value = snapshot.get("invested_value", snapshot.get("total_value", 0)) or 0
        total_cost = snapshot.get("total_cost", 0) or 0
        total_assets = total_value + cash_balance
        total_profit = total_value - total_cost
        profit_rate = total_profit / total_cost if total_cost > 0 else 0

        if total_assets <= 0:
            return ""

        # ── 持仓明细 ──
        lines = []
        lines.append(f"总资产: ¥{total_assets:,.0f}（持仓 ¥{total_value:,.0f} + 现金 ¥{cash_balance:,.0f}）")
        lines.append(f"快照: {snapshot.get('active_count', snapshot.get('holding_count', len(active)))} 条有效持仓 / {snapshot.get('fund_count', len(active))} 只基金；总资产占比含现金，持仓市值占比不含现金")
        lines.append(f"总盈亏: ¥{total_profit:,.0f}（{profit_rate:+.2%}）")
        lines.append("")

        # 按市值排序
        sorted_holdings = sorted(active, key=lambda h: h.get("current_value", 0) or 0, reverse=True)

        lines.append("### 持仓明细")
        lines.append("| 基金名称 | 市值 | 总资产占比 / 持仓市值占比 | 盈亏率 | 今日涨幅 | 成本价 | 份额 | 上次买入 | 盈亏 |")
        lines.append("|---------|------|------|--------|---------|--------|------|----------|------|")
        for h in sorted_holdings:
            name = h.get("fund_name", h.get("fund_code", "未知"))
            value = h.get("current_value", 0) or 0
            pct = value / total_assets if total_assets > 0 else 0
            invested_pct = value / total_value if total_value > 0 else 0
            pr = h.get("profit_rate", 0) or 0
            pr_pct = f"{pr:+.2%}" if isinstance(pr, (int, float)) else "N/A"
            # 今日涨幅（2026-07-30 conv#194 修复：注入今日涨幅让专家判断"最近涨了多少"）
            tcp = h.get("today_change_pct")
            tcp_str = f"{tcp:+.2f}%" if isinstance(tcp, (int, float)) and tcp != 0 else "-"
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
            lines.append(f"| {name}（{h.get('fund_code','')}） | ¥{value:,.0f} | {pct:.1%}总资产 / {invested_pct:.1%}持仓 | {pr_pct} | {tcp_str} | {cp_str} | {shares_str} | {lb_str} | {pl_str} |")

        # ── 资产分布 ──
        lines.append("")
        lines.append("### 资产分布")
        category_map = {}
        for h in active:
            cat = h.get("fund_category") or "equity"
            cat_label = {
                "equity": "股票型",
                "bond": "债券型",
                "bond_index": "债券指数型",
                "bond_pure": "纯债型",
                "bond_short": "短债型",
                "bond_hybrid": "混合债券型",
                "bond_convertible": "可转债型",
                "convertible_bond": "可转债型",
                "money": "货币型",
                "money_market": "货币型",
                "hybrid": "混合型",
                "index": "指数型",
                "qdii": "QDII",
            }.get(cat, cat)
            category_map.setdefault(cat_label, 0)
            category_map[cat_label] += h.get("current_value", 0) or 0

        for cat_label, cat_value in sorted(category_map.items(), key=lambda x: -x[1]):
            cat_pct = cat_value / total_value if total_value > 0 else 0
            lines.append(f"- {cat_label}: ¥{cat_value:,.0f}（{cat_pct:.1%}持仓市值）")

        # 现金占比
        cash_pct = cash_balance / total_assets if total_assets > 0 else 0
        lines.append(f"- 现金: ¥{cash_balance:,.0f}（{cash_pct:.1%}）")

        # ── 近期涨幅（2026-07-30 conv#194 修复）──
        # 从 fund_nav_history 表计算近1周/1月/3月涨幅，让专家判断"最近涨了多少"
        # 解决 conv#194 中专家说"缺少近期涨幅数据"的问题
        try:
            from db._conn import _get_conn as _get_conn_nav
            _nav_conn = _get_conn_nav()
            lines.append("")
            lines.append("### 近期涨幅")
            lines.append("| 基金名称 | 今日 | 近1周 | 近1月 | 近3月 | 近6月 |")
            lines.append("|---------|------|-------|-------|-------|-------|")
            for h in sorted_holdings:
                _name = h.get("fund_name", h.get("fund_code", "未知"))
                _code = h.get("fund_code", "")
                _today = h.get("today_change_pct")
                _today_str = f"{_today:+.2f}%" if isinstance(_today, (int, float)) and _today != 0 else "-"
                # 从净值历史计算近期涨幅
                _nav_rows = _nav_conn.execute(
                    "SELECT nav_date, nav FROM fund_nav_history WHERE fund_code = ? ORDER BY nav_date DESC LIMIT 130",
                    (_code,),
                ).fetchall()
                if len(_nav_rows) >= 2:
                    _latest_nav = _nav_rows[0]["nav"]
                    _periods = {"近1周": 5, "近1月": 22, "近3月": 66, "近6月": 132}
                    _ret_strs = []
                    for _label, _days in _periods.items():
                        if len(_nav_rows) > _days and _nav_rows[_days]["nav"] and _latest_nav:
                            _old_nav = _nav_rows[_days]["nav"]
                            _ret = (_latest_nav - _old_nav) / _old_nav * 100 if _old_nav else None
                            _ret_strs.append(f"{_ret:+.2f}%" if _ret is not None else "-")
                        else:
                            _ret_strs.append("-")
                    lines.append(f"| {_name} | {_today_str} | {_ret_strs[0]} | {_ret_strs[1]} | {_ret_strs[2]} | {_ret_strs[3]} |")
                else:
                    lines.append(f"| {_name} | {_today_str} | - | - | - | - |")
            _nav_conn.close()
        except Exception as _ne:
            logger.debug(f"近期涨幅注入失败: {_ne}")

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
        lines.append(f"> 📅 分析快照读取时间: {snapshot.get('as_of', '')}。持仓净值日期以原始记录为准，读取时间不代表净值更新时间。")

        # ── 最近交易记录（10条）──
        try:
            from db.portfolio import list_transactions
            # 按状态分别限量，避免待确认记录挤掉真实成交；公共查询排除系统/假设。
            completed = sorted(
                list_transactions(user_id=user_id, status="confirmed", limit=10)
                + list_transactions(user_id=user_id, status="settled", limit=10),
                key=lambda row: (row.get("transaction_date") or "", row.get("id") or 0),
                reverse=True,
            )[:10]
            pending = sorted(
                list_transactions(user_id=user_id, status="pending", limit=10)
                + list_transactions(user_id=user_id, status="submitted", limit=10),
                key=lambda row: (row.get("transaction_date") or "", row.get("id") or 0),
                reverse=True,
            )[:10]
            for title, _trades in (("最近真实成交", completed), ("待确认交易（不计入成交或冷却期）", pending)):
                if not _trades:
                    continue
                lines.append("")
                lines.append(f"### {title}")
                lines.append("| 日期 | 基金 | 操作 | 金额 | 份额 | 状态 |")
                lines.append("|------|------|------|------|------|------|")
                for _t in _trades:
                    _td = dict(_t) if not isinstance(_t, dict) else _t
                    _type_label = {"buy": "买入", "sell": "卖出", "dividend": "分红"}.get(_td.get("transaction_type", ""), _td.get("transaction_type", ""))
                    _status_label = {"confirmed": "已确认", "settled": "已结算", "pending": "待确认", "submitted": "已提交待确认"}.get(_td.get("status", ""), _td.get("status", ""))
                    _name = _td.get("fund_name") or _td.get("fund_code", "未知")
                    _amt = _td.get("amount", 0) or 0
                    _shares = _td.get("shares", 0) or 0
                    lines.append(f"| {_td.get('transaction_date', '')} | {_name} | {_type_label} | ¥{_amt:,.0f} | {_shares:,.0f} | {_status_label} |")
                lines.append("")
            if completed or pending:
                lines.append("> 仅真实已确认/已结算记录可用于执行、冷却期或交易频率判断；待确认记录仅表示待处理，不能描述为已执行。")
        except Exception as _te:
            logger.debug(f"交易记录注入失败: {_te}")

        # ── 持仓上限与减仓约束 ──
        lines.append("")
        lines.append("### 持仓上限")
        policy = snapshot.get("concentration_policy", {})
        single_pct = policy.get("max_single_position_pct")
        if single_pct is not None:
            lines.append(f"- 单只基金/标的持仓上限: {single_pct:g}%（KYC 用户画像；分母为含现金总资产，跨账户同基金合并计算）")
            lines.append("- 加仓前必须检查目标基金当前占比是否已达上限，如已超限禁止加仓")
        else:
            lines.append("- 尚未设置单标的上限；不能把默认阈值描述为用户约定，需在给出交易建议前核实。")

        from services.portfolio.analysis_contract import format_portfolio_calculations
        lines.append("")
        lines.append(format_portfolio_calculations(snapshot))

        lines.append("")
        lines.append("### 减仓约束（必须遵守）")
        lines.append("- 单基金单次减仓金额 ≤ 该基金持仓市值的 20%")
        lines.append("- 单次建议总减仓金额 ≤ 总资产的 10%")
        lines.append("- 单基金单次减仓上限: ¥50,000")
        lines.append("- 单次建议最多减仓 2 只基金")
        lines.append("- 最近已成交交易需计入剩余持仓；待确认交易应单列，不能视为已成交。")
        lines.append("- 风险再平衡、流动性需求和基本面变化可构成减仓理由，不能仅因账面亏损或估值低而禁止减仓。")

        # 新鲜度标注
        from datetime import datetime as _dt2
        lines.append("")
        lines.append(f"> 📅 快照范围: {snapshot.get('cash_scope', '')}；净值可能有延迟，以基金公司公布为准。")

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
            # 不使用 with 语句：with 退出时 shutdown(wait=True) 会无限等待 akshare zombie 线程
            # 案例：conv#132/133 因 akshare HTTP 挂起导致 with 退出阻塞 40min
            try:
                import concurrent.futures
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = executor.submit(get_fund_holdings, fund_code)
                try:
                    fund_data = future.result(timeout=5)
                except concurrent.futures.TimeoutError:
                    fund_data = None
                    logger.warning(f"[bond_holdings] {fund_code} 底层持仓获取超时 5s，跳过")
                except Exception as e:
                    fund_data = None
                    logger.warning(f"[bond_holdings] {fund_code} 底层持仓获取失败: {e}")
                finally:
                    # shutdown(wait=False) 不等待线程完成，立即返回
                    # cancel_futures=True 尝试取消尚未开始的任务（Python 3.9+）
                    executor.shutdown(wait=False, cancel_futures=True)
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

            # P1-3: 穿透数据来源标注（akshare / ttfund 兜底 / 本地快照 / 部分失效）
            data_source = fund_data.get("_data_source", "")
            report_date = fund_data.get("report_date", "")
            snapshot_updated = fund_data.get("_snapshot_updated_at", "")
            is_stale = fund_data.get("_stale", False)
            source_line = _format_holdings_source(data_source, report_date, snapshot_updated, is_stale)
            if source_line:
                lines.append(source_line)

            lines.append("")

        if len(lines) <= 3:
            return ""  # 没有获取到任何有效数据

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"构建债券持仓上下文失败: {e}")
        return ""


def build_portfolio_summary_line(user_id: str = "default", snapshot: dict | None = None) -> str:
    """构建单行持仓摘要，用于 clarify_requirement 等对 token 敏感的场景。

    示例输出: "持仓: 沪深300ETF(30%), 中证500ETF(20%), 恒乐债券A(40%), 现金(10%)"
    """
    try:
        from db import get_portfolio_snapshot

        snapshot = snapshot or get_portfolio_snapshot(user_id)
        active = snapshot.get("funds", [])
        cash_balance = snapshot.get("cash_balance", 0) or 0
        total_value = snapshot.get("invested_value", snapshot.get("total_value", 0)) or 0
        total_assets = snapshot.get("total_assets", total_value + cash_balance)

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
            parts.append(f"{name}[{h.get('fund_code','')}]({pct:.0%}总资产/{(value/total_value if total_value else 0):.0%}持仓)")

        if cash_balance > 0:
            cash_pct = cash_balance / total_assets if total_assets > 0 else 0
            parts.append(f"现金({cash_pct:.0%})")

        return "持仓: " + ", ".join(parts)

    except Exception as e:
        logger.warning(f"构建持仓摘要失败: {e}")
        return ""


def build_valuation_summary(conv_id: int = None, message_id: int = None,
                           trace_id: str = None, user_query: str = None) -> str:
    """构建逐指标、逐来源日期的本地估值摘要。"""
    from services.valuation.context import build_market_valuation_context
    try:
        return build_market_valuation_context()
    except Exception as error:
        logger.warning('构建估值摘要失败: %s', error)
        return ''
