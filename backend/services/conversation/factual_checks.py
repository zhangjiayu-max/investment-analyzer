"""最终正文的保守事实检查：只报告可由完整原始证据证伪的陈述。"""
from datetime import date, timedelta
import json
import math
import re

from services.conversation.quality_contract import tool_evidence


def _sentences(content):
    return [part.strip().replace("**", "") for part in re.split(r"[。！？；;\n]", content or "") if part.strip()]


def _qualified(text):
    """疑问、假设、历史引述及否定不当成当前肯定事实验收。"""
    return bool(re.search(r"如果|假如|假设|(?:^|[，,：:（(])若|待核实|待核验|不能(?:声称|认为|说)|"
                          r"不得(?:声称|认为)|误称|误报|旧报告|历史报告|并非|不是|不代表|不等于|不应(?:再)?(?:称|说)|"
                          r"说法(?:不成立|错误|有误)|错误的说法|"
                          r"是否|[？?]", text))


def _calls(tool_calls):
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        evidence = tool_evidence([call])
        if not evidence:
            continue
        args = call.get("arguments", call.get("args", {})) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = {"_unknown": True}
        if not isinstance(args, dict):
            args = {"_unknown": True}
        yield evidence[0]["tool"], evidence[0]["data"], args


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _market_reasoning_issues(content, calls):
    """保守拦截已明确矛盾的归因逻辑，不把规则匹配当成事实核验。"""
    issues = []
    missing_flow = re.search(r"(?:没有|缺少|缺乏|未取得|未获取)[^。\n]{0,16}(?:资金流|卖空|做空)[^。\n]{0,12}(?:数据|证据)", content)
    for sentence in _sentences(content):
        limited = re.search(r"不能|无法|不足以|不代表|不等于|待验证|待核验|待核实|仅为假设|只是.*假设|是否|[？?]", sentence)
        if not limited:
            if missing_flow and re.search(r"(?:更符合|更多指向|主要(?:由|是)|原因(?:是|在于))[^。]{0,30}(?:资金撤离|情绪踩踏|做空)", sentence):
                issues.append("unsupported_market_causation")
            if re.search(r"低估值|低分位|底部分位", sentence) and re.search(r"(?:而非|排除|不是|并非)基本面(?:恶化|问题)", sentence):
                issues.append("unsupported_market_causation")
            if (re.search(r"一年|近一年|年度|全年", sentence) and re.search(r"一月|一个月|月度|最近月", sentence)
                    and re.search(r"所以|因此|说明|表明", sentence) and re.search(r"企稳|跌速放缓|跌幅.*收窄", sentence)
                    and not re.search(r"折算|年化|同口径|等长窗口", sentence)):
                issues.append("incomparable_return_windows")
        if (re.search(r"假设|情景|再跌", sentence) and re.search(r"最大(?:可能)?(?:损失|亏损|回撤)", sentence)
                and not re.search(r"不(?:是|代表|等于)|不能|并非|不应", sentence)):
            issues.append("scenario_is_not_maximum_loss")

    def quotes(value, inherited_name=""):
        if isinstance(value, list):
            for row in value:
                yield from quotes(row, inherited_name)
        elif isinstance(value, dict):
            name = value.get("index_name") or value.get("名称") or value.get("name") or inherited_name
            point = next((value[k] for k in ("current_point", "最新价", "最新点位", "最新", "last_price") if k in value), None)
            stamp = value.get("as_of") or value.get("timestamp") or value.get("更新时间") or value.get("时间") or value.get("date")
            try:
                valid = not isinstance(point, bool) and math.isfinite(float(point)) and float(point) > 0
            except (ValueError, TypeError):
                valid = False
            if name and valid and _date(stamp) == date.today():
                yield re.sub(r"指数$", "", str(name))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    yield from quotes(child, name)
    names = set()
    for name, data, _ in calls:
        if name in {"eastmoney_finance_data", "yingmi_latest_quotations"}:
            names.update(quotes(data))
    for sentence in _sentences(content):
        if _qualified(sentence):
            continue
        if (any(name in sentence for name in names) and re.search(r"点位|行情", sentence)
                and re.search(r"未取得|未获取|未返回|无数据|没有(?:取得|获取|返回)", sentence)):
            issues.append("market_quote_falsely_missing")
    return issues


def _recent_counts(data, as_of):
    """仅完整窗口、完整结果或窗口内确定无成交可支持准确笔数。"""
    if not isinstance(data, dict) or not as_of:
        return None
    cutoff = as_of - timedelta(days=30)
    scope = data.get("date_scope") or {}
    if not isinstance(scope, dict):
        return None
    complete = data.get("transactions_complete") is True
    end = _date(scope.get("end")) if scope else as_of if complete else None
    start = _date(scope.get("start"))
    if not end or end < as_of or (start and start > cutoff):
        return None
    last = _date(scope.get("last_confirmed_date"))
    count = data.get("confirmed_count")
    if (isinstance(count, int) and not isinstance(count, bool) and count == 0) or (last and last < cutoff):
        return {"buy": 0, "sell": 0, "total": 0}
    transactions = data.get("transactions")
    if not isinstance(transactions, list):
        return None
    if not complete and not (isinstance(count, int) and not isinstance(count, bool) and count == len(transactions)):
        return None
    counts = {"buy": 0, "sell": 0, "total": 0}
    for tx in transactions:
        if not isinstance(tx, dict):
            return None
        tx_date = _date(tx.get("transaction_date"))
        if not tx_date:
            return None
        if tx.get("status") not in {"confirmed", "settled"} or tx.get("is_system") or tx.get("is_hypothetical"):
            continue
        if cutoff <= tx_date <= as_of and tx.get("transaction_type") in {"buy", "sell"}:
            counts[tx["transaction_type"]] += 1
            counts["total"] += 1
    return counts


def check_final_facts(content: str, snapshot: dict | None = None,
                      tool_calls: list | None = None) -> list[str]:
    """返回稳定 issue code 列表；不修改正文、快照或工具记录，不访问外部状态。

    交易窗口参考 exact snapshot.as_of。支持 snapshot.transaction_evidence
    （query_trade_evidence 格式或 transactions_complete=True 的完整记录）及
    query_transaction_history 原始结果；单标的/操作过滤、最近 N 条截断不支持全局推断。
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    sentences = _sentences(content)
    issues = []
    calls = list(_calls(tool_calls))
    issues.extend(_market_reasoning_issues(content or "", calls))
    available_temperature = any(
        name == "get_bond_temperature" and isinstance(data, dict)
        and isinstance(data.get("temperature"), (int, float))
        and not isinstance(data["temperature"], bool) and math.isfinite(data["temperature"])
        for name, data, _ in calls
    )
    if available_temperature:
        for sentence in sentences:
            if _qualified(sentence):
                continue
            for clause in re.split(r"[，,]", sentence):
                if re.search(r"(?:债市温度|get_bond_temperature)[^，,]{0,18}"
                             r"(?:空数据|无数据|未返回(?:有效)?数据|没有返回(?:有效)?数据|缺乏可验证数据)", clause):
                    issues.append("bond_temperature_falsely_missing")

    policy = snapshot.get("concentration_policy") or {}
    threshold = policy.get("max_single_position_pct") if isinstance(policy, dict) else None
    if isinstance(threshold, (float, int)) and not isinstance(threshold, bool) and math.isfinite(threshold):
        single = r"(?:单只基金|单基金|单标的|单只标的)"
        for sentence in sentences:
            if _qualified(sentence) or not re.search(r"用户|KYC|你的|您的", sentence, re.I):
                continue
            if not re.search(single, sentence) or not re.search(r"上限|红线", sentence):
                continue
            value = re.search(single + r"[^%\d]{0,12}(?:上限|红线)[^%\d]{0,14}(\d+(?:\.\d+)?)%", sentence)
            if not value:
                value = re.search(r"(\d+(?:\.\d+)?)%[^。；]{0,12}(?:用户|KYC)[^。；]{0,8}(?:红线|上限)", sentence, re.I)
            if value and abs(float(value.group(1)) - threshold) > .01:
                issues.append("kyc_single_limit_value_mismatch")
            if "总资产" not in sentence and re.search(
                r"(?:分母(?:是|为|按)?|上限(?:为|是)|按|占|以)(?:不含现金的?)?(?:持仓市值|剩余持仓|持仓总额|持仓)|(?:持仓市值|持仓)(?:口径|占比)", sentence
            ):
                issues.append("kyc_single_limit_denominator_mismatch")

    as_of = _date(snapshot.get("as_of"))
    counts = []
    embedded = _recent_counts(snapshot.get("transaction_evidence"), as_of)
    if embedded is not None:
        counts.append(embedded)
    for name, data, args in calls:
        if name != "query_transaction_history" or any(args.get(k) for k in ("fund_code", "fund_name", "transaction_type", "_unknown")):
            continue
        if args.get("user_id", "default") != snapshot.get("user_id", "default"):
            continue
        known = _recent_counts(data, as_of)
        if known is not None:
            counts.append(known)
    # 原始证据之间冲突时不擅自选取一个作事实裁决。
    if counts and all(count == counts[0] for count in counts):
        known = counts[0]
        for sentence in sentences:
            if _qualified(sentence):
                continue
            window = re.search(r"(?:最近|近|过去)\s*30\s*(?:天|日)", sentence)
            if not window:
                continue
            tail = sentence[window.end():]
            claims = re.findall(r"(\d+)\s*(?:笔|次)\s*(买入|卖出|交易|成交)", tail)
            claims += [(number, kind) for kind, number in re.findall(r"(买入|卖出|交易|成交)\s*(\d+)\s*(?:笔|次)", tail)]
            for number, kind in claims:
                key = {"买入": "buy", "卖出": "sell", "交易": "total", "成交": "total"}[kind]
                if int(number) != known[key]:
                    issues.append("recent_30d_trade_count_mismatch")
    return list(dict.fromkeys(issues))
