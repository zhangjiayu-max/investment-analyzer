"""最终答复的证据关联检查；规则不改写原始工具证据。"""
import math
import re


def _finite(value):
    try:
        return None if isinstance(value, bool) else float(value) if math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def _limited(text):
    return bool(re.search(r"如果|假如|假设|待核实|待核验|待验证|可能|只是|仅为|不能|无法|不足以|不代表|不等于|不应|并非|不是|未执行|未成交|是否|旧报告|历史报告|误称|说法(?:不成立|错误|有误)|[？?]", text))


def market_cause_claims(sentences):
    for sentence in sentences:
        if _limited(sentence):
            continue
        if (re.search(r"资金撤离|资金流出|情绪踩踏|做空|卖空", sentence)
                and re.search(r"主要|确定|证实|更符合|更多指向|原因|导致|造成|因为|源于", sentence)):
            yield sentence


def unrequested_trade_plan(content, query, actions, sentences):
    if query is None:
        return False
    from agent.core.answer_policy import has_explicit_action_intent
    if has_explicit_action_intent(query):
        return False
    if any(isinstance(a, dict) and str(a.get("type") or a.get("action") or "").upper()
           in {"BUY", "ADD", "SELL", "REDUCE", "REDUCE_CONCENTRATION"} for a in actions or []):
        return True
    for sentence in sentences:
        if _limited(sentence) or re.search(r"不建议|不要|未建议|历史|旧报告|不宜|不必", sentence):
            continue
        if (re.search(r"建议|每月|每期|分批|立即|执行", sentence)
                and re.search(r"买入|卖出|加仓|减仓|补仓|定投", sentence)
                and re.search(r"\d+(?:\.\d+)?\s*(?:元|万元|%)", sentence)):
            return True
    return False


def evidence_direction_issues(sentences, snapshot):
    from services.portfolio.analysis_contract import build_portfolio_calculations
    if not snapshot.get("funds") or snapshot.get("target_equity_ratio") is None:
        return []
    try:
        facts = build_portfolio_calculations(snapshot)
        over = facts["category_total_assets_pct"]["equity_proxy"] > facts["target_equity_ratio"]
    except (ValueError, TypeError, KeyError):
        return []
    if not over:
        return []
    for sentence in sentences:
        if re.search(r"如果|假如|假设|旧报告|历史报告|说法(?:不成立|错误|有误)|不应|不能(?:说|认为|声称)|并非", sentence):
            continue
        target = re.search(r"降低(?:权益目标|目标权益(?:比例)?|权益(?:比例)?目标)", sentence)
        if not target:
            continue
        # “除非…否则不能…”恰恰声明降低目标是加仓前提，不能按一般否定跳过。
        prerequisite = re.search(r"除非.*降低.*(?:否则|才能|才可以).*?(?:加仓|买入|补仓)", sentence)
        if prerequisite or (not _limited(sentence) and re.search(r"(?:就|才|以便|从而|可以).{0,10}(?:加仓|买入|补仓)", sentence)):
            return ["equity_target_direction_error"]
    return []


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def has_market_cause_evidence(claim, calls, as_of, parse_date):
    """对应标的、当期、有限且方向相符的观测可支持归因；工具成功本身不算证据。"""
    if not as_of:
        return False
    requirements = []
    if re.search(r"资金撤离|资金流出", claim):
        requirements.append((("net_outflow", "资金净流出"), ("net_inflow", "net_flow", "net_flow_yi", "资金净流入")))
    if re.search(r"做空|卖空", claim):
        requirements.append((("short_selling_volume", "short_volume", "short_selling_ratio", "卖空成交额", "卖空比例"), ()))
    if "情绪踩踏" in claim:
        requirements.append((("panic_selling_volume", "情绪踩踏成交额"), ()))
    for positive, negative in requirements:
        supported = False
        for tool, data, _ in calls:
            if not re.search(r"finance|flow|capital|market|short", tool or ""):
                continue
            for row in _walk(data):
                name = row.get("index_name") or row.get("fund_name") or row.get("name") or row.get("名称")
                if not name and tool == "query_southbound_capital" and "南向" in claim:
                    name = "南向"
                if not name or re.sub(r"指数$", "", str(name)) not in claim:
                    continue
                stamp = parse_date(row.get("as_of") or row.get("timestamp") or row.get("date") or row.get("latest_date"))
                if stamp != as_of or row.get("error") or row.get("metric_status") in {"unknown", "unavailable"}:
                    continue
                if (any(_finite(row.get(key)) is not None and _finite(row[key]) > 0 for key in positive)
                        or any(_finite(row.get(key)) is not None and _finite(row[key]) < 0 for key in negative)):
                    supported = True
                    break
            if supported:
                break
        if not supported:
            return False
    return bool(requirements)


def dated_quote_issues(sentences, calls, as_of, parse_date):
    if not as_of:
        return []
    observations = []
    for tool, data, _ in calls:
        if tool not in {"eastmoney_finance_data", "yingmi_latest_quotations"}:
            continue
        for row in _walk(data):
            name = row.get("index_name") or row.get("名称") or row.get("name")
            stamp = parse_date(row.get("as_of") or row.get("timestamp") or row.get("date"))
            change = next((_finite(row[key]) for key in ("change_pct", "涨跌幅", "change_percent") if key in row), None)
            if name and stamp and change is not None:
                observations.append((re.sub(r"指数$", "", name), stamp, change))
    issues = []
    for sentence in sentences:
        if _limited(sentence):
            continue
        for name, stamp, change in observations:
            if name not in sentence or stamp >= as_of:
                continue
            if any(n == name and d == as_of for n, d, _ in observations):
                continue
            values = [_finite(value) for value in re.findall(r"([+-]?\d+(?:\.\d+)?)\s*%", sentence)]
            same_change = any(value is not None and abs(value - change) < .005 for value in values)
            current_claim = re.search(r"(?:今日|今天|当天|上午|盘中).{0,8}(?:反弹|上涨|下跌|涨|跌)", sentence)
            dated_rebuttal = (re.search(r"昨日|上一交易日|昨收", sentence)
                             and re.search(r"所以|因此|说明|可见", sentence)
                             and re.search(r"今天|今日|上午|盘中", sentence)
                             and re.search(r"没有大跌|并未大跌|并没有大跌|大跌不成立|不算大跌", sentence))
            if same_change and (current_claim or dated_rebuttal):
                issues.append("stale_quote_as_current")
    return issues


def trade_execution_status(sentences, snapshot, calls):
    rows = []
    embedded = snapshot.get("transaction_evidence")
    if isinstance(embedded, dict):
        rows.extend(embedded.get("transactions") or [])
    for name, data, args in calls:
        if name == "query_transaction_history" and isinstance(data, dict) and args.get("user_id", "default") == snapshot.get("user_id", "default"):
            rows.extend(data.get("transactions") or [])
    issues, unverified = [], []
    for sentence in sentences:
        if _limited(sentence) or re.search(r"不计入|不构成|不触发|尚未|没有.{0,6}(?:买入|成交|执行)", sentence):
            continue
        if not re.search(r"已(?:确认|买入|成交|执行|真实执行)|触发.{0,5}冷却期|计入.{0,5}冷却期", sentence):
            continue
        matched = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            txid = row.get("id")
            by_id = txid is not None and re.search(rf"(?:交易|流水|记录)\s*(?:id\s*[:：#]?|编号\s*[:：#]?|#)?\s*{re.escape(str(txid))}(?!\d)", sentence, re.I)
            code, name = row.get("fund_code"), row.get("fund_name")
            amount = _finite(row.get("amount"))
            amounts = [_finite(value.replace(",", "")) for value in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*元", sentence)]
            by_detail = ((code and code in sentence) or (name and name in sentence)) and str(row.get("transaction_date") or "_missing")[:10] in sentence and amount is not None and any(v is not None and abs(v - amount) <= .51 for v in amounts)
            if by_id or by_detail:
                matched.append(row)
        # 同日同额存在真实成交时不可仅凭假设记录判错。
        if matched and all(r.get("is_hypothetical") or r.get("is_system") or r.get("status") not in {"confirmed", "settled"} for r in matched):
            issues.append("nonreal_trade_as_executed")
        elif not matched and (re.search(r"(?:交易|流水|记录)\s*(?:id\s*[:：#]?|编号\s*[:：#]?|#)?\s*\d+", sentence, re.I)
                              or re.search(r"\d{4}-\d{2}-\d{2}.*(?:买入|卖出|成交|执行)", sentence)):
            unverified.append("trade_execution_unverified")
    return {"issues": issues, "unverified": unverified}


def nonreal_trade_issues(sentences, snapshot, calls):
    return trade_execution_status(sentences, snapshot, calls)["issues"]
