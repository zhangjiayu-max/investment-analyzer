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
        lines.append("| 基金名称 | 市值 | 占比 | 盈亏率 |")
        lines.append("|---------|------|------|--------|")
        for h in sorted_holdings:
            name = h.get("fund_name", h.get("fund_code", "未知"))
            value = h.get("current_value", 0) or 0
            pct = value / total_assets if total_assets > 0 else 0
            pr = h.get("profit_rate", 0) or 0
            pr_pct = f"{pr:+.2%}" if isinstance(pr, (int, float)) else "N/A"
            lines.append(f"| {name} | ¥{value:,.0f} | {pct:.1%} | {pr_pct} |")

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

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"构建持仓上下文失败: {e}")
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

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"构建估值摘要失败: {e}")
        return ""
