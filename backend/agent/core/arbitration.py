"""仲裁 Agent 的轻量裁决工具。"""

from __future__ import annotations

from collections import Counter


def _detect_stance(text: str) -> str:
    text = (text or "").lower()
    if any(kw in text for kw in ["不建议", "观望", "持有", "风险", "谨慎", "先别", "暂停"]):
        return "hold"
    if any(kw in text for kw in ["建议买", "买入", "加仓", "建仓", "上车", "分批买"]):
        return "buy"
    if any(kw in text for kw in ["卖出", "减仓", "止盈", "止损", "退出"]):
        return "sell"
    return "unknown"


def arbitrate_results(query: str, specialist_results: list[dict], blackboard=None, plan=None) -> dict:
    """基于专家结果做轻量仲裁，输出可解释裁决。"""
    stances = []
    reasons = []
    supporting_agents = []

    for result in specialist_results or []:
        agent_key = result.get("agent_key", "unknown")
        agent_name = result.get("agent", agent_key)
        analysis = result.get("analysis", "")
        stance = result.get("stance") or _detect_stance(analysis)
        stances.append(stance)
        if stance != "unknown":
            supporting_agents.append(agent_key)
        if analysis:
            reasons.append(f"{agent_name}: {analysis[:120]}")

    counts = Counter(s for s in stances if s != "unknown")
    if counts["hold"] > 0:
        final_stance = "hold"
        arbitration_mode = "conflict" if counts["buy"] or counts["sell"] else "consensus"
    elif counts["sell"] > counts["buy"]:
        final_stance = "sell"
        arbitration_mode = "consensus" if counts["sell"] > 1 else "conflict"
    elif counts["buy"] > 0:
        final_stance = "buy"
        arbitration_mode = "consensus" if counts["buy"] > 1 else "conflict"
    else:
        final_stance = "watch"
        arbitration_mode = "consensus"

    shared_evidence = {}
    if plan and getattr(plan, "shared_evidence_keys", None):
        shared_evidence["keys"] = list(plan.shared_evidence_keys)
    if blackboard:
        try:
            shared_evidence["key_data"] = blackboard.get_key_data()
        except Exception:
            shared_evidence["key_data"] = {}

    disagreements = [
        {
            "agent_key": result.get("agent_key", "unknown"),
            "agent": result.get("agent", result.get("agent_key", "unknown")),
            "stance": result.get("stance") or _detect_stance(result.get("analysis", "")),
        }
        for result in specialist_results or []
        if (result.get("stance") or _detect_stance(result.get("analysis", ""))) in ("buy", "sell", "hold")
    ]

    # ── 兼容字段：供 _save_final / pipeline 日志直接使用 ──
    # 修复 conv 125：原返回字段 final_stance/arbitration_mode/disagreements
    # 与 _save_final 期望的 verdict/confidence/key_conflicts/reasoning 不匹配，
    # 导致写入 messages.metadata 的 arbitration 字段值全为空字符串。
    _stance_verdict_map = {
        "buy": "建议买入/加仓",
        "sell": "建议卖出/减仓",
        "hold": "建议持有/观望",
        "watch": "建议观望等待",
    }
    verdict = _stance_verdict_map.get(final_stance, "建议观望")

    # 置信度推断：consensus + 多专家支持 = high；conflict = medium；无 stance = low
    supporting_count = len(supporting_agents)
    if not supporting_count:
        confidence = "low"
    elif arbitration_mode == "conflict":
        confidence = "medium"
    elif supporting_count >= 2:
        confidence = "high"
    else:
        confidence = "medium"

    # 关键冲突：按 stance 分组提取对立观点（buy vs sell/hold 等）
    stance_groups: dict[str, list[str]] = {"buy": [], "sell": [], "hold": []}
    for d in disagreements:
        s = d.get("stance", "unknown")
        if s in stance_groups:
            agent_label = d.get("agent") or d.get("agent_key") or "unknown"
            stance_groups[s].append(agent_label)
    key_conflicts = []
    if stance_groups["buy"] and (stance_groups["sell"] or stance_groups["hold"]):
        opposing = stance_groups["sell"] + stance_groups["hold"]
        key_conflicts.append({
            "type": "buy_vs_sell_hold",
            "buy_side": stance_groups["buy"],
            "opposing_side": opposing,
            "note": "看多与看空/持有观点存在分歧",
        })
    if stance_groups["sell"] and stance_groups["hold"]:
        key_conflicts.append({
            "type": "sell_vs_hold",
            "sell_side": stance_groups["sell"],
            "hold_side": stance_groups["hold"],
            "note": "看空与持有观点存在分歧",
        })

    # 推理摘要：拼接各专家理由，截断到 800 字符
    reasoning = " | ".join(reasons)[:800] if reasons else ""

    summary = {
        # 原字段（向后兼容）
        "query": query,
        "final_stance": final_stance,
        "arbitration_mode": arbitration_mode,
        "reasons": reasons,
        "supporting_agents": supporting_agents,
        "shared_evidence": shared_evidence,
        "disagreements": disagreements,
        # 兼容字段：供 _save_final / pipeline 日志使用
        "verdict": verdict,
        "confidence": confidence,
        "key_conflicts": key_conflicts,
        "reasoning": reasoning,
    }
    return summary
