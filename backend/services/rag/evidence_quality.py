"""检索证据的有限校验：相关场景、原始来源和可直接复算的收益示例。

这些检查不验证书籍真实性、预测、相关系数或全部金融事实。
算术冲突仅表示需核对原始条件（例如杠杆），不自动改写原文。
"""
import json
import re

KNOWLEDGE_TYPES = {"book", "note", "concept", "strategy", "bond", "knowledge"}
_RETURN = re.compile(r"(下跌|下降|亏损|上涨|上升|增长)\s*([+-]?\d+(?:\.\d+)?)\s*%")
_COMBINED = re.compile(
    r"组合(?:回报率?|收益率?|结果)(?:约)?(?:为|是|达到|等于)?\s*"
    r"(正|负|亏损|盈利|[+-])?\s*(\d+(?:\.\d+)?)\s*%"
)


def numeric_consistency_issues(text: str) -> list[dict]:
    """只检查明确给出两类涨跌、权重与组合收益的同一局部示例。

    不推断缺失的权重，不把不同期间的历史/预测收益拼成公式。
    发现偏离简单加权时保留需原文核对状态，容忍 0.15 个百分点的舍入。
    """
    text = re.sub(r"\s+", "", str(text or "")).replace("％", "%").replace("−", "-")
    issues = []
    for combined in _COMBINED.finditer(text):
        prefix = text[max(0, combined.start() - 240):combined.start()]
        # 引用错误示例并明确纠正的段落，本身不是在主张该错误结果。
        correction_context = text[max(0, combined.start() - 160):combined.end() + 40]
        if re.search(r"错误示例|错误地认为|误称|误写|计算错误|实际应为|正确结果", correction_context):
            continue
        returns = list(_RETURN.finditer(prefix))
        if len(returns) < 2:
            continue
        first, second = returns[-2:]
        example = prefix[first.start():]
        # 跨时间的事实不构成同一情景的加权收益公式。
        if any(term in example for term in ("过去", "去年", "预测", "明年", "次年", "累计", "复利")):
            continue
        weights_text = prefix[second.end():]
        if re.search(r"(?:各占|各配置|各为|各)50%|50[/：:]50|50%配置", weights_text):
            weights = [.5, .5]
        else:
            # 非等权必须能按资产名称连接权重，不能假设后文列举顺序相同。
            weights = []
            for asset_return in (first, second):
                label = re.search(r"(传统资产|保护资产|股票|债券|权益资产|现金|资产[AB甲乙])$",
                                  prefix[:asset_return.start()])
                if label is None:
                    break
                asset = re.escape(label.group(1))
                weight = re.search(
                    rf"(?:{asset}(?:权重|配置比例|配置|占比|占|比例)?(?:为|是)?(\d+(?:\.\d+)?)%|"
                    rf"(\d+(?:\.\d+)?)%{asset})", weights_text,
                )
                if weight is None:
                    break
                weights.append(float(weight.group(1) or weight.group(2)) / 100)
            if len(weights) != 2 or abs(sum(weights) - 1) > .0001:
                continue
        values = [(-1 if item.group(1) in ("下跌", "下降", "亏损") else 1) * float(item.group(2))
                  for item in (first, second)]
        expected = sum(weight * value for weight, value in zip(weights, values))
        claimed = (-1 if combined.group(1) in ("负", "亏损", "-") else 1) * float(combined.group(2))
        if abs(expected - claimed) > .15:
            issues.append({"code": "weighted_return_inconsistency", "status": "requires_source_check",
                           "expected_simple_return_pct": round(expected, 6), "claimed_return_pct": claimed,
                           "excerpt": prefix[first.start():] + combined.group(0)})
    return issues


def allocation_scope_mismatch(query: str, result: dict) -> bool:
    """只拒绝明显的短线交易规则，避免把标题中的“组合”当成配置适用性。"""
    if not re.search(r"资产配置|均衡|均衡性|组合风险|集中度|持仓风险|分散", query):
        return False
    body = re.sub(r"\s+", "", result.get("body", "") or "")
    trading_rule = re.search(r"止损|月度交易|短线交易|交易预期|年化收益预期|可口可乐.*建仓", body)
    allocation_context = re.search(r"资产配置|再平衡|分散|相关性|集中度|风险预算|股债|资产类别|现金比例", body)
    return bool(trading_rule and not allocation_context)


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (TypeError, ValueError):
        return [str(value)] if value else []


def hydrate_evidence(results: list[dict], get_conn) -> list[dict]:
    """按类型和唯一 ID 回查知识原文；检索通道与出版来源分别保留。

    不使用标题回查。缺失原表时保留索引正文并显式记录来源状态。
    """
    conn = None
    try:
        for item in results:
            ct, ref = item.get("content_type", ""), str(item.get("reference_id", ""))
            item["reference_id"] = ref
            item["evidence_id"] = f"{ct}:{ref}"
            if item.get("source") in {"fts", "chroma", "both"}:
                item["retrieval_source"] = item.pop("source")
            item["limitations"] = _as_list(item.get("limitations"))
            item.setdefault("content_origin", "retrieval_index")
            if ct not in KNOWLEDGE_TYPES:
                continue
            if conn is None:
                conn = get_conn()
            row = conn.execute("SELECT * FROM knowledge_base WHERE id = ? AND category = ?", (ref, ct)).fetchone()
            if row is None:
                continue
            original = dict(row)
            item["body"] = original.get("content") or ""
            item["title"] = original.get("title") or item.get("title", "")
            item["source"] = original.get("source") or ""
            item["content_origin"] = "source_record"
            for field in ("importance", "atom_type", "evidence_level", "as_of_date", "valid_until"):
                if field in original:
                    item[field] = original[field]
            for field in ("limitations", "counterpoints"):
                item[field] = _as_list(original.get(field))
            item["time"] = original.get("as_of_date") or original.get("updated_at") or item.get("time", "")
    finally:
        if conn is not None:
            conn.close()
    return results
