"""对话最终化契约：原始证据优先，动作必须与标的及来源一一对应。"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re
import hashlib


def tool_evidence(tool_calls: list[dict]) -> list[dict]:
    """仅将完整 JSON 或显式结构化对象视为可验证工具事实。"""
    evidence = []
    for i, call in enumerate(tool_calls or []):
        if not isinstance(call, dict):
            continue
        value = call.get("result")
        if value is None:
            value = call.get("result_preview")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
        if not isinstance(value, (dict, list)) or not value or (isinstance(value, dict) and value.get("error")):
            continue
        identity = json.dumps([call.get("name") or call.get("tool"),
                               call.get("arguments", call.get("args", {})), value],
                              sort_keys=True, ensure_ascii=False, default=str)
        stable_id = "tool:" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        evidence.append({"evidence_id": call.get("evidence_id") or stable_id,
                         "tool": call.get("name") or call.get("tool"), "data": value})
    return evidence


def compact_tool_evidence(tool_calls: list[dict], max_chars: int = 32000) -> list[dict]:
    """按工具压缩且保留 JSON 结构；不整体截断而丢失靠后的工具事实。"""
    from services.llm.context_budget import summarize_evidence
    unique = {}
    for entry in tool_evidence(tool_calls):
        # 以工具+原始数据去重；不得把相同指标但数值/日期不同的证据合并。
        key = json.dumps([entry["tool"], entry["data"]], sort_keys=True, ensure_ascii=False, default=str)
        unique.setdefault(key, entry)
    entries = list(unique.values())
    allowance = max(100, min(6000, max_chars // max(1, len(entries)) - 160))
    return [{**entry, 'data': summarize_evidence(entry['data'], allowance)} for entry in entries]


def reconcile_reflection_evidence(reflection: dict, tool_calls: list[dict]) -> dict:
    """纠正能由原始记录直接证伪的“工具无数据”，保留其他审查意见。"""
    result = deepcopy(reflection or {})
    corrections = []
    for evidence in tool_evidence(tool_calls):
        data = evidence["data"]
        if evidence["tool"] != "get_bond_temperature" or not isinstance(data, dict):
            continue
        value = data.get("temperature")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        corrections.append({"tool": evidence["tool"], "evidence_id": evidence["evidence_id"],
                            "value": value, "date": data.get("date"), "status": "available"})
        def disproved(text):
            return ("get_bond_temperature" in text or "债市温度" in text) and any(
                word in text for word in ("空数据", "无数据", "没有返回", "缺乏可验证数据", "无法验证", "无法核验"))
        for key in ("quality_issues", "missing_perspectives"):
            result[key] = [text for text in result.get(key, []) if not disproved(str(text))]
    result["evidence_corrections"] = corrections
    return result


def merge_specialist_results(events: list[dict], final: list[dict]) -> list[dict]:
    """最终对象字段优先，事件预览仅补齐缺失专家。"""
    merged = {}
    for item in [*(events or []), *(final or [])]:
        if not isinstance(item, dict) or not item.get("agent_key"):
            continue
        key = (item["agent_key"], bool(item.get("is_cross_review")))
        merged[key] = {**merged.get(key, {}), **item}
    return list(merged.values())


def confidence_label(value: float) -> str:
    return "高" if value >= .75 else "中" if value >= .5 else "低"


def calibrate_confidence_text(content: str, confidence: float) -> str:
    """总体置信度由统一数值生成，避免正文自称高置信。"""
    label = confidence_label(confidence)
    pattern = r"(\*{0,2}总体置信度[：:]\*{0,2}\s*)(?:中高|中低|高|中|低)(?:置信度)?(?:\s*[（(]\d+(?:\.\d+)?%[）)])?"
    replacement = rf"\g<1>{label}（{confidence:.0%}）"
    if re.search(pattern, content):
        return re.sub(pattern, replacement, content)
    return content + f"\n\n总体置信度：{label}（{confidence:.0%}）；以证据完整性和审查结果为准。"


def finalize_answer_event(event: dict) -> dict:
    """流式正常/超时收尾共用质量门禁；旧引擎缺少验收证据时不授权候选。"""
    from agent.infra.output_quality import is_valid_analysis, validate_specialist_output
    from services.conversation.delivery_status import delivery_metadata, has_delivery_content
    result = dict(event)
    if not has_delivery_content(result.get("content", "")):
        result.update(completion_status="failed", quality_status="degraded", confidence=0.0)
        return delivery_metadata(result.get("content", ""), result)
    status = result.get("completion_status", "completed")
    quality = result.get("quality_status", "unverified")
    experts = result.get("specialist_results") or []
    reflection = result.get("reflection") or {}
    invalid = any(not validate_specialist_output(s)["valid"] for s in experts)
    if (invalid or reflection.get("status") in {"failed", "budget_exhausted"}
            or quality == "degraded" or not is_valid_analysis(result.get("content", ""))):
        status, quality = "degraded", "degraded"
    if status != "completed":
        quality = "degraded"
        result["confidence"] = min(result.get("confidence", 0.0), .45)
        result["content"] = calibrate_confidence_text(result.get("content", ""), result["confidence"])
        if result.get("arbitration"):
            result["arbitration"] = {**result["arbitration"], "confidence": "low",
                                     "confidence_score": result["confidence"]}
    result.update(completion_status=status, quality_status=quality)
    return delivery_metadata(result.get('content', ''), result)


def _fund_codes(value) -> set[str]:
    if isinstance(value, dict):
        result = set()
        for key, child in value.items():
            if key in {"fund_code", "code", "target_code"} and re.fullmatch(r"\d{6}", str(child)):
                result.add(str(child))
            elif isinstance(child, (list, dict)):
                result.update(_fund_codes(child))
        return result
    if isinstance(value, list):
        return set().union(*(_fund_codes(item) for item in value)) if value else set()
    return set()


def selected_asset_actions(arbitration: dict | None, specialists: list[dict]) -> list[dict]:
    """候选与推荐使用同一动作来源；显式空仲裁动作具有权威性。"""
    if arbitration is not None and "asset_actions" in arbitration:
        return arbitration["asset_actions"] or []
    return [action for specialist in specialists or [] if not specialist.get("is_cross_review")
            for action in specialist.get("asset_actions", [])]


def _within_snapshot_limits(direction, code, amount, evidence):
    """已知现金、持有额及用户上限形成硬边界，不允许候选越界。"""
    snapshots = [entry["data"] for entry in evidence if isinstance(entry["data"], dict)
                 and (entry["evidence_id"] == "portfolio_snapshot" or entry["tool"] == "get_portfolio_snapshot")]
    for snapshot in snapshots:
        funds = snapshot.get("funds") or []
        fund = next((f for f in funds if str(f.get("fund_code")) == code), {})
        value = float(fund.get("current_value") or 0)
        if not math.isfinite(value):
            return False
        if direction == "sell":
            if amount is not None and amount > value:
                return False
            continue
        cash = float(snapshot.get("cash_balance") or 0)
        assets = float(snapshot.get("invested_value", snapshot.get("total_value", 0)) or 0) + cash
        if not math.isfinite(cash) or not math.isfinite(assets):
            return False
        if amount is not None and amount > cash:
            return False
        limit = (snapshot.get("concentration_policy") or {}).get("max_single_position_pct")
        if limit is not None and assets > 0 and (value + (amount or 0)) / assets * 100 > float(limit) + .00001:
            return False
        # 分类代理足以证伪明确越过权益预算的买入；混合基金不假定100%权益。
        category = str(fund.get("fund_category") or "").lower()
        if category in {"index", "equity", "stock"} and "黄金" not in str(fund.get("fund_name", "")):
            from services.portfolio.analysis_contract import build_portfolio_calculations
            calculations = build_portfolio_calculations(snapshot)
            target = calculations.get("target_equity_ratio")
            equity = calculations["equity_proxy_value"]
            if target is not None and assets > 0 and (equity + (amount or 0)) / assets * 100 > float(target) + .00001:
                return False
    return True


def action_constraint_issues(actions, tool_calls):
    """让报告验收和候选共用交易金额边界；未知金额不推断可执行规模。"""
    evidence = tool_evidence(tool_calls)
    buys = {}
    directions = {}
    for action in actions or []:
        if not isinstance(action, dict) or action.get("status", "current") != "current":
            continue
        if action.get("negated") or action.get("historical") or action.get("conditions") or action.get("condition"):
            continue
        direction = str(action.get("type") or action.get("action") or "").upper()
        if direction not in {"BUY", "ADD", "SELL", "REDUCE", "REDUCE_CONCENTRATION"}:
            continue
        code = str(action.get("target_code") or action.get("target") or "")
        if not re.fullmatch(r"\d{6}", code):
            continue
        side = "buy" if direction in {"BUY", "ADD"} else "sell"
        directions.setdefault(code, set()).add(side)
        if len(directions[code]) > 1:
            return ["conflicting_asset_actions"]
        try:
            amount = action.get("amount", action.get("suggested_amount"))
            amount = None if amount is None else float(amount)
            if amount is not None and (not math.isfinite(amount) or amount <= 0):
                return ["asset_action_limit_violation"]
            if not _within_snapshot_limits("buy" if direction in {"BUY", "ADD"} else "sell", code, amount, evidence):
                return ["asset_action_limit_violation"]
            if direction in {"BUY", "ADD"}:
                buys[code] = max(buys.get(code, 0), amount or 0)
        except (ValueError, TypeError, KeyError):
            return ["asset_action_limit_violation"]
    if not _batch_buy_limits(buys, evidence):
        return ["asset_action_limit_violation"]
    return []


def _batch_buy_limits(buys, evidence):
    """累计买入不能越过现金或组合权益目标，不假设尚未完成的卖出到账。"""
    from services.portfolio.analysis_contract import build_portfolio_calculations
    for entry in evidence:
        snapshot = entry["data"]
        if not isinstance(snapshot, dict) or (entry["evidence_id"] != "portfolio_snapshot" and entry["tool"] != "get_portfolio_snapshot"):
            continue
        cash = float(snapshot.get("cash_balance") or 0)
        if sum(buys.values()) > cash + .00001:
            return False
        calculations = build_portfolio_calculations(snapshot)
        target = calculations.get("target_equity_ratio")
        equity_buys = sum(buys.get(str(f.get("fund_code")), 0) for f in snapshot.get("funds", [])
                          if str(f.get("fund_category") or "").lower() in {"index", "equity", "stock"}
                          and "黄金" not in str(f.get("fund_name", "")))
        assets = float(snapshot.get("invested_value", snapshot.get("total_value", 0)) or 0) + cash
        if equity_buys and target is not None and assets > 0 and (calculations["equity_proxy_value"] + equity_buys) / assets * 100 > float(target) + .00001:
            return False
    return True


def build_chat_candidates(content: str, actions: list[dict], tool_calls: list[dict], *,
                          conversation_id: int = 0, message_id: int = 0,
                          completion_status: str = "completed", quality_status: str = "validated") -> list[dict]:
    """接受有来源且与最终文本同向的结构化动作；不从散文生成动作。"""
    if completion_status != "completed" or quality_status != "validated":
        return []
    evidence = tool_evidence(tool_calls)
    refs = {}
    for entry in evidence:
        for ref in (entry["tool"], entry["evidence_id"]):
            refs.setdefault(ref, set()).update(_fund_codes(entry["data"]))
    aliases = {"BUY": "buy", "ADD": "buy", "SELL": "sell", "REDUCE": "sell", "REDUCE_CONCENTRATION": "sell"}
    groups = {}
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        code = str(action.get("target_code") or action.get("target") or "").strip()
        direction = aliases.get(str(action.get("type") or action.get("action") or "").upper())
        if re.fullmatch(r"\d{6}", code) and direction:
            groups.setdefault(code, []).append((direction, action))
    candidates = []
    for code, proposed in groups.items():
        if len({direction for direction, _ in proposed}) != 1:
            continue
        for direction, action in proposed:
            if action.get("status", "current") != "current" or action.get("negated") or action.get("historical"):
                continue
            if action.get("conditions") or action.get("condition") or action.get("source") == "text_fallback":
                continue
            sources = action.get("evidence_refs") or []
            if not isinstance(sources, list) or not any(isinstance(ref, str) and code in refs.get(ref, set()) for ref in sources):
                continue
            try:
                confidence = float(action.get("confidence", 0))
            except (ValueError, TypeError):
                continue
            if not .5 <= confidence <= 1 or not action.get("reason"):
                continue
            lines = [line for line in re.split(r"[。！？\n]", content) if re.search(rf"(?<!\d){code}(?!\d)", line)]
            expected = r"(?:建议|条件性)?(?:减持|减仓|卖出|赎回)" if direction == "sell" else r"(?:建议|开始|继续)?(?:买入|加仓|定投|建仓)"
            opposite = r"买入|加仓|定投|建仓" if direction == "sell" else r"减持|减仓|卖出|赎回"
            denied = r"禁止|不建议|不应|不宜|不再|不买|不卖|不减|不加|不定投|不建仓|避免|勿|无需|不要|尚未|历史|已经|若|如果|条件性|待核验"
            if any(re.search(opposite, line) or re.search(denied, line) for line in lines):
                continue
            if not any(re.search(expected, line)
                       and len(set(re.findall(r"(?<!\d)\d{6}(?!\d)", line))) == 1 for line in lines):
                continue
            amount = action.get("amount", action.get("suggested_amount"))
            if amount is not None:
                try:
                    amount = float(amount)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(amount) or amount <= 0:
                    continue
            try:
                if not _within_snapshot_limits(direction, code, amount, evidence):
                    continue
            except (TypeError, ValueError, KeyError):
                continue
            candidates.append({"source_type": "ai_chat", "source_id": conversation_id,
                               "scenario_type": "validated_asset_action", "action_type": direction,
                               "target_type": "fund", "target_code": code, "target_name": action.get("target_name", ""),
                               "summary": f"{code}：{action['reason']}", "rationale": action["reason"],
                               "suggested_amount": amount, "confidence": "medium" if confidence < .75 else "high",
                               "evidence": {"conversation_id": conversation_id, "message_id": message_id,
                                            "evidence_refs": sources, "validation_status": "validated"},
                               "dedupe_key": f"chat:{conversation_id}:{message_id}:{code}:{direction}",
                               "status": "new", "priority": 4})
            break
    # 组合级现金预算：多个标的同时买入不能分别通过后再合计超出现金。
    buys = {c["target_code"]: float(c.get("suggested_amount") or 0) for c in candidates if c["action_type"] == "buy"}
    try:
        if not _batch_buy_limits(buys, evidence):
            candidates = [c for c in candidates if c["action_type"] != "buy"]
    except (TypeError, ValueError, KeyError):
        return []
    return candidates
