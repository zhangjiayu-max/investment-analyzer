"""组合分析证据契约：稳定快照指纹、历史适用性和确定性金额验算。"""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP


def _money(value) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _totals(snapshot):
    invested = float(snapshot.get("invested_value", snapshot.get("total_value", 0)) or 0)
    cash = float(snapshot.get("cash_balance") or 0)
    return invested, cash, invested + cash


def portfolio_fingerprint(snapshot: dict) -> str:
    """仅取经济事实与用户约束，忽略读取时间、数据库记录顺序等噪声。"""
    funds = [{
        "fund_code": f.get("fund_code"), "fund_name": f.get("fund_name"),
        "fund_category": f.get("fund_category"), "shares": round(float(f.get("shares") or 0), 8),
        "current_value": _money(f.get("current_value")), "total_cost": _money(f.get("total_cost")),
    } for f in snapshot.get("funds", [])]
    invested, cash, _ = _totals(snapshot)
    basis = {
        "user_id": snapshot.get("user_id", "default"), "account": snapshot.get("account"),
        "funds": sorted(funds, key=lambda f: json.dumps(f, sort_keys=True)),
        "invested_value": _money(invested), "cash_balance": _money(cash),
        "concentration_policy": snapshot.get("concentration_policy", {}),
        "target_equity_ratio": snapshot.get("target_equity_ratio"),
        "kyc_context": snapshot.get("kyc_context", {}),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def portfolio_analysis_basis(snapshot: dict, validation_status: str = "unverified") -> dict:
    """存入现有 data_basis JSON；绑定快照并显式记录结果是否通过质量验收。"""
    return {"kind": "portfolio_snapshot", "fingerprint": portfolio_fingerprint(snapshot),
            "validation_status": validation_status, "version": 1}


def filter_portfolio_conclusions(conclusions: list[dict], snapshot: dict) -> list[dict]:
    """无绑定、旧快照或未验证的结论均不能作为当前组合事实注入。"""
    fingerprint = portfolio_fingerprint(snapshot)
    result = []
    for conclusion in conclusions:
        basis = conclusion.get("data_basis") or []
        if isinstance(basis, str):
            try:
                basis = json.loads(basis)
            except (ValueError, TypeError):
                continue
        if isinstance(basis, dict):
            basis = [basis]
        if not isinstance(basis, list):
            continue
        if any(isinstance(b, dict) and b.get("kind") == "portfolio_snapshot"
               and b.get("fingerprint") == fingerprint and b.get("validation_status") == "validated"
               for b in basis):
            result.append(conclusion)
    return result


def simulate_sale(snapshot: dict, fund_code: str, amount: float, proceeds: str = "cash") -> dict:
    """忽略费用/价格变动的卖出验算；赎回留现金时持仓分母同步下降。"""
    if proceeds not in {"cash", "reinvest"}:
        raise ValueError("proceeds must be cash or reinvest")
    fund = next(f for f in snapshot.get("funds", []) if f.get("fund_code") == fund_code)
    value = float(fund.get("current_value") or 0)
    if not 0 <= amount <= value:
        raise ValueError("sale amount must be between zero and fund value")
    invested, cash, assets = _totals(snapshot)
    remaining = value - amount
    invested_after = invested - amount if proceeds == "cash" else invested
    return {"sale_amount": _money(amount), "proceeds": proceeds,
            "invested_value": _money(invested_after), "cash_balance": _money(cash + amount if proceeds == "cash" else cash),
            "total_assets": _money(assets), "fund_value": _money(remaining),
            "invested_pct": remaining / invested_after * 100 if invested_after else 0,
            "total_assets_pct": remaining / assets * 100 if assets else 0}


def sale_to_target(snapshot: dict, fund_code: str, target_pct: float,
                   denominator: str = "total_assets", proceeds: str = "cash") -> float:
    """达到指定上限所需的最小卖出额，资金去向决定持仓分母。"""
    if denominator not in {"total_assets", "invested_value"} or proceeds not in {"cash", "reinvest"}:
        raise ValueError("unknown denominator or proceeds")
    if not 0 <= target_pct <= 100:
        raise ValueError("target_pct must be in [0, 100]")
    value = float(next(f for f in snapshot.get("funds", []) if f.get("fund_code") == fund_code).get("current_value") or 0)
    invested, _, assets = _totals(snapshot)
    ratio = target_pct / 100
    amount = value - ratio * (assets if denominator == "total_assets" else invested)
    if denominator == "invested_value" and proceeds == "cash" and ratio < 1:
        amount /= 1 - ratio
    return _money(max(0, min(value, amount)))


def build_portfolio_calculations(snapshot: dict) -> dict:
    """类别代理并非底层穿透；黄金单列，混合基金仅给出保守的分类上界。"""
    invested, cash, assets = _totals(snapshot)
    values = {"cash": cash, "bond": 0, "equity_proxy": 0, "hybrid": 0, "gold": 0, "other": 0}
    limits = {}
    threshold = snapshot.get("concentration_policy", {}).get("max_single_position_pct")
    for fund in snapshot.get("funds", []):
        name = fund.get("fund_name") or ""
        category = (fund.get("fund_category") or "unknown").lower()
        if category == "gold" or "黄金" in name or "gold" in name.lower():
            bucket = "gold"
        elif category.startswith("bond") or category == "convertible_bond" or (category == "index" and "债" in name):
            bucket = "bond"
        elif category in {"equity", "stock", "index"}:
            bucket = "equity_proxy"
        elif category in {"hybrid", "mixed"}:
            bucket = "hybrid"
        else:
            bucket = "other"
        values[bucket] += float(fund.get("current_value") or 0)
        code = fund.get("fund_code")
        if code:
            limits[code] = {
                "kyc_limit_pct": threshold, "denominator": "total_assets",
                "sale_to_kyc_limit": sale_to_target(snapshot, code, threshold) if threshold is not None else None,
                "sale_to_20pct_invested_cash": sale_to_target(snapshot, code, 20, "invested_value"),
            }
    scenarios = {}
    for amount in (0, 120000, 150000, 250000):
        if amount <= cash and assets > 0:
            scenarios[amount] = {
                "equity_proxy_pct": (values["equity_proxy"] + amount) / assets * 100,
                "with_hybrid_upper_pct": (values["equity_proxy"] + values["hybrid"] + amount) / assets * 100,
                "cash_after": _money(cash - amount),
            }
    target = snapshot.get("target_equity_ratio")
    if target is not None:
        target = float(target) * 100 if 0 < float(target) <= 1 else float(target)
    return {"fingerprint": portfolio_fingerprint(snapshot), "category_values": {k: _money(v) for k, v in values.items()},
            "category_total_assets_pct": {k: v / assets * 100 if assets else 0 for k, v in values.items()},
            "equity_proxy_value": _money(values["equity_proxy"]), "target_equity_ratio": target,
            "equity_cash_scenarios": scenarios, "single_position_limits": limits,
            "assumptions": "价格不变、忽略费用、无外部出入金；类别代理不等于底层股票/债券穿透敞口"}


def format_portfolio_calculations(snapshot: dict) -> str:
    calculations = build_portfolio_calculations(snapshot)
    lines = ["### 程序验算事实（情景演示，不构成交易建议）", f"组合事实指纹: {calculations['fingerprint']}",
             f"- {calculations['assumptions']}"]
    labels = {"cash": "现金", "bond": "债券", "equity_proxy": "非黄金权益类别代理", "hybrid": "混合基金", "gold": "黄金", "other": "其他/未穿透"}
    for key, value in calculations["category_values"].items():
        lines.append(f"- {labels[key]}: ¥{value:,.2f} / 总资产 {calculations['category_total_assets_pct'][key]:.2f}%")
    if calculations["target_equity_ratio"] is not None:
        lines.append(f"- KYC 目标权益比例: {calculations['target_equity_ratio']:g}%（总资产口径）；提高目标属于风险预算变更，不能称符合原目标。")
    kyc = snapshot.get("kyc_context") or {}
    if kyc.get("fund_usage"):
        lines.append(f"- KYC 资金用途: {kyc['fund_usage']}")
    if kyc.get("liquidity_needs"):
        lines.append(f"- KYC 流动性需求: {kyc['liquidity_needs']}")
    monthly_expense = float(kyc.get("monthly_expense") or 0)
    if monthly_expense > 0:
        lines.append(f"- KYC 月支出 ¥{monthly_expense:,.2f}；备用金月份必须用备用金额÷该月支出验算，不可凭空设定。")
    for code, limit in calculations["single_position_limits"].items():
        if limit["sale_to_kyc_limit"]:
            lines.append(f"- {code}: 仅回到 KYC {limit['kyc_limit_pct']:g}%/总资产上限需卖出 ¥{limit['sale_to_kyc_limit']:,.2f}（赎回留现金）；另若以20%/剩余持仓为假设目标，需 ¥{limit['sale_to_20pct_invested_cash']:,.2f}，该20%不是用户约定。")
    for amount, scenario in calculations["equity_cash_scenarios"].items():
        lines.append(f"- 现金转权益 ¥{amount:,.0f} 情景: 非黄金权益类别代理 {scenario['equity_proxy_pct']:.2f}%/总资产；混合基金全计权益的分类上界 {scenario['with_hybrid_upper_pct']:.2f}%。")
    return "\n".join(lines)
