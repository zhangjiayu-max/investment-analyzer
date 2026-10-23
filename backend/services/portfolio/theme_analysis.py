"""主题聚合分析 — 跨账户去重、同指数合并、主题集中度诊断。

解决"持仓数量的分散 ≠ 风险的分散"问题：
  - 同一基金在多个账户重复持有 → cross_account
  - 同一指数由不同基金持有 → same_index
  - 按主题关键词聚合权益持仓 → themes（集中度 HHI / 有效主题数 / Top3 权重）
  - 碎片仓位识别（现值 < 1 万）→ fragments
"""

from __future__ import annotations

from datetime import datetime

# 主题识别规则（顺序敏感：先具体后宽泛，命中即停）
# (主题名, 关键词列表, 适用类别集合 None=全部)
THEME_RULES: list[tuple[str, list[str], set[str] | None]] = [
    ("债券", ["债"], {"bond", "bond_pure", "bond_index"}),
    ("黄金/商品", ["黄金", "原油", "商品", "白银", "豆粕"], None),
    ("港股科技互联网", ["恒生科技", "港股通互联网", "港股互联网", "恒生互联网", "港股通科技"], None),
    ("医疗医药", ["医疗", "医药", "生物", "创新药", "健康"], None),
    ("固收+", ["价值回报", "稳健回报", "均衡回报", "回报混合", "双利", "安盈", "添利", "聚利", "增利", "丰利"], {"hybrid"}),
    ("大消费", ["白酒", "消费", "食品", "饮料", "酒"], None),
    ("畜牧农业", ["畜牧", "养殖", "农业", "猪", "饲料"], None),
    ("新能源", ["光伏", "新能源", "电池", "碳中和", "风电", "储能"], None),
    ("金融地产", ["证券", "券商", "银行", "保险", "房地产", "地产", "中证金融", "金融地产"], None),
    ("红利风格", ["红利", "股息"], None),
    ("科技制造", ["机器人", "半导体", "芯片", "人工智能", "AI", "科技", "电子", "云计算", "5G"], None),
    ("宽基", ["沪深300", "中证500", "中证800", "中证A500", "A500", "上证50", "创业板", "科创50", "中证1000", "中证全指"], None),
]

BOND_CATEGORIES = {"bond", "bond_pure", "bond_index", "money", "money_market"}
EQUITY_CATEGORIES = {"index", "hybrid", "equity", "qdii", "stock"}
# 防守盘主题：不参与权益集中度（HHI）评估
DEFENSIVE_THEMES = {"债券", "固收+"}
FRAGMENT_THRESHOLD = 10_000.0
# HHI 集中度分级阈值（前后端共用一套口径）
HHI_MODERATE = 0.12
HHI_HIGH = 0.18


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _match_theme(h: dict) -> str:
    category = (h.get("fund_category") or "").strip()
    name = f"{h.get('fund_name') or ''} {h.get('index_name') or ''}"
    if category in BOND_CATEGORIES:
        return "债券"
    if category in ("gold", "commodity") or "黄金" in name:
        return "黄金/商品"
    for theme, keywords, cats in THEME_RULES[1:]:
        if cats is not None and category not in cats:
            continue
        for kw in keywords:
            if kw in name:
                return theme
    return "其他"


def _pnl_rate(h: dict) -> float:
    cost = _num(h.get("total_cost"))
    if cost <= 0:
        return 0.0
    return _num(h.get("profit_loss")) / cost


def _fund_brief(h: dict) -> dict:
    return {
        "fund_code": h.get("fund_code"),
        "fund_name": h.get("fund_name"),
        "account": h.get("account") or "默认账户",
        "theme": h.get("_theme") or "",
        "index_code": h.get("index_code") or "",
        "index_name": h.get("index_name") or "",
        "value": round(_num(h.get("current_value")), 2),
        "cost": round(_num(h.get("total_cost")), 2),
        "pnl": round(_num(h.get("profit_loss")), 2),
        "pnl_rate": round(_pnl_rate(h), 4),
    }


def analyze_theme_concentration(user_id: str = "default") -> dict:
    """主题聚合分析入口。

    返回:
    {
        "summary": {总览指标: 权重集中度 HHI、有效主题数、Top3 权重、重复/碎片计数},
        "themes": [{theme, value, weight, pnl, pnl_rate, funds: [...]}],
        "duplicates": {"cross_account": [...], "same_index": [...]},
        "fragments": [...],
        "advice": [诊断提示],
    }
    """
    from db import list_holdings, get_cash_balance

    holdings = list_holdings(user_id) or []
    active = [h for h in holdings if _num(h.get("current_value")) > 0]

    for h in active:
        h["_theme"] = _match_theme(h)

    equity = [h for h in active if h["_theme"] not in DEFENSIVE_THEMES]
    equity_value = sum(_num(h.get("current_value")) for h in equity)
    total_value = sum(_num(h.get("current_value")) for h in active)
    total_cost = sum(_num(h.get("total_cost")) for h in active)
    total_pnl = sum(_num(h.get("profit_loss")) for h in active)

    # ── 主题聚合 ──
    themes: dict[str, dict] = {}
    for h in active:
        t = themes.setdefault(h["_theme"], {
            "theme": h["_theme"], "value": 0.0, "cost": 0.0, "pnl": 0.0, "funds": [],
        })
        t["value"] += _num(h.get("current_value"))
        t["cost"] += _num(h.get("total_cost"))
        t["pnl"] += _num(h.get("profit_loss"))
        t["funds"].append(_fund_brief(h))

    theme_list = []
    for t in themes.values():
        is_defensive = t["theme"] in DEFENSIVE_THEMES
        # 权益主题占权益盘、防守盘占总持仓
        base = total_value if is_defensive else equity_value
        weight = t["value"] / base if base > 0 else 0.0
        t["weight"] = round(weight, 4)
        t["is_defensive"] = is_defensive
        t["pnl_rate"] = round(t["pnl"] / t["cost"], 4) if t["cost"] > 0 else 0.0
        t["value"] = round(t["value"], 2)
        t["cost"] = round(t["cost"], 2)
        t["pnl"] = round(t["pnl"], 2)
        t["funds"].sort(key=lambda f: -f["value"])
        theme_list.append(t)
    theme_list.sort(key=lambda t: -t["value"])
    # HHI 只对权益主题计算（债券/固收+ 是防守盘，不参与风险集中度评估）
    equity_themes = [t for t in theme_list if not t["is_defensive"]]
    eq_total = sum(t["value"] for t in equity_themes) or 1.0
    hhi = sum((t["value"] / eq_total) ** 2 for t in equity_themes)
    top3_weight = sum(t["weight"] for t in equity_themes[:3])
    equity_pnl = round(sum(t["pnl"] for t in equity_themes), 2)
    if hhi >= HHI_HIGH:
        hhi_level = "high"
    elif hhi >= HHI_MODERATE:
        hhi_level = "moderate"
    else:
        hhi_level = "low"

    # ── 跨账户重复 ──
    by_code: dict[str, list] = {}
    for h in active:
        by_code.setdefault(h.get("fund_code"), []).append(h)
    cross_account = []
    for code, hs in by_code.items():
        accounts = {h.get("account") for h in hs}
        if len(accounts) > 1:
            positions = [_fund_brief(h) for h in sorted(hs, key=lambda x: -_num(x.get("current_value")))]
            keep = positions[0]
            cross_account.append({
                "fund_code": code,
                "fund_name": hs[0].get("fund_name"),
                "theme": hs[0]["_theme"],
                "total_value": round(sum(_num(h.get("current_value")) for h in hs), 2),
                "total_pnl": round(sum(_num(h.get("profit_loss")) for h in hs), 2),
                "positions": positions,
                "keep_hint": f"建议合并至「{keep['account']}」（现值 ¥{keep['value']:,.0f} 最大），另一账户卖出后归拢",
            })
    cross_account.sort(key=lambda d: -d["total_value"])

    # ── 同指数不同基金 ──
    by_index: dict[str, list] = {}
    for h in equity:
        idx = (h.get("index_code") or "").strip()
        if not idx or idx in ("该基金无跟踪标的",):
            continue
        by_index.setdefault(idx, []).append(h)
    same_index = []
    for idx, hs in by_index.items():
        codes = {h.get("fund_code") for h in hs}
        if len(codes) > 1:
            funds = [_fund_brief(h) for h in sorted(hs, key=lambda x: -_num(x.get("current_value")))]
            keep = funds[0]
            same_index.append({
                "index_code": idx,
                "index_name": next((h.get("index_name") for h in hs if h.get("index_name")), idx),
                "theme": hs[0]["_theme"],
                "total_value": round(sum(_num(h.get("current_value")) for h in hs), 2),
                "funds": funds,
                "keep_hint": f"建议保留「{keep['fund_name']}」（现值 ¥{keep['value']:,.0f} 规模最大），合并前比较各基金管理费率",
            })
    same_index.sort(key=lambda d: -d["total_value"])

    # ── 碎片仓位 ──
    fragments = [
        _fund_brief(h) for h in sorted(active, key=lambda x: _num(x.get("current_value")))
        if _num(h.get("current_value")) < FRAGMENT_THRESHOLD
    ]
    fragment_total = round(sum(f["value"] for f in fragments), 2)
    try:
        cash = _num(get_cash_balance(user_id).get("balance"))
    except Exception:
        cash = 0.0
    total_assets = total_value + cash
    # 单只碎片涨跌 20% 对组合的影响（取最大的一只）
    fragment_max_impact = round(
        max((f["value"] for f in fragments), default=0.0) / total_assets * 0.2 * 100, 3
    ) if total_assets > 0 else 0.0

    # ── 诊断提示 ──
    advice = []
    if cross_account:
        names = "、".join(d["fund_name"] or d["fund_code"] for d in cross_account[:3])
        advice.append(f"跨账户重复持仓 {len(cross_account)} 只（{names} 等），建议合并到单一账户统一管理")
    if same_index:
        advice.append(
            f"同指数多基金 {len(same_index)} 组，实际敞口比表面持仓更集中，建议每只指数只保留一只基金"
        )
    if equity_themes and top3_weight > 0.5:
        top3_names = "、".join(t["theme"] for t in equity_themes[:3])
        advice.append(f"前三大主题（{top3_names}）占权益 {top3_weight:.0%}，超过 50% 预警线，新增资金应避开这些主题")
    if len(fragments) >= 8:
        advice.append(
            f"碎片仓位 {len(fragments)} 只（现值 <{FRAGMENT_THRESHOLD / 10000:g} 万），"
            f"对组合影响可忽略却增加管理负担，建议设定『只出不进』规则"
        )
    if hhi_level == "high":
        advice.append(
            f"权益主题 HHI={hhi:.3f}（有效主题数 {1 / hhi:.1f} 个），"
            f"≥{HHI_HIGH} 属高度集中，新增资金应优先补足弱势主题而非加仓前三大"
        )
    elif hhi_level == "moderate":
        advice.append(
            f"权益主题 HHI={hhi:.3f}（有效主题数 {1 / hhi:.1f} 个），"
            f"≥{HHI_MODERATE} 属中度集中，留意主题进一步集中"
        )
    if not advice:
        advice.append("持仓结构健康：无明显重复持仓，主题集中度在合理范围内")

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "holding_count": len(active),
            "total_value": round(total_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_rate": round(total_pnl / total_cost, 4) if total_cost > 0 else 0.0,
            "cash_balance": round(cash, 2),
            "equity_value": round(equity_value, 2),
            "equity_pnl": equity_pnl,
            "theme_count": len(equity_themes),
            "effective_theme_count": round(1 / hhi, 1) if hhi > 0 else 0,
            "hhi": round(hhi, 4),
            "hhi_level": hhi_level,
            "hhi_thresholds": {"moderate": HHI_MODERATE, "high": HHI_HIGH},
            "top3_weight": round(top3_weight, 4),
            "fragment_count": len(fragments),
            "fragment_threshold": FRAGMENT_THRESHOLD,
            "fragment_total_value": fragment_total,
            "fragment_max_impact_pct": fragment_max_impact,
            "duplicate_fund_count": len(cross_account),
            "duplicate_index_count": len(same_index),
        },
        "themes": theme_list,
        "duplicates": {"cross_account": cross_account, "same_index": same_index},
        "fragments": fragments,
        "advice": advice,
    }
