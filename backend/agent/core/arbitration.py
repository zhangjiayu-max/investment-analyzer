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

    # 推理摘要：重构为"识别分歧+解决冲突+独立置信度评估"格式
    # conv#130 修复：原实现是拼接每个专家 analysis[:120]，导致 reasoning 是原文片段堆砌
    # 新实现：结构化输出裁决依据，让用户看到仲裁逻辑而非原文复读
    reasoning = _build_arbitration_reasoning(
        query=query,
        specialist_results=specialist_results or [],
        stances=stances,
        final_stance=final_stance,
        arbitration_mode=arbitration_mode,
        key_conflicts=key_conflicts,
        confidence=confidence,
        supporting_count=len(supporting_agents),
    )

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


def _build_arbitration_reasoning(
    query: str,
    specialist_results: list[dict],
    stances: list[str],
    final_stance: str,
    arbitration_mode: str,
    key_conflicts: list[dict],
    confidence: str,
    supporting_count: int,
) -> str:
    """构建结构化仲裁推理依据。

    conv#130 修复：替代原"拼接每个专家 analysis[:120]"的简陋实现，
    输出"裁决结论 + 分歧识别 + 解决逻辑 + 置信度依据"四段式结构化推理。

    Returns:
        结构化推理字符串（≤800 字符）
    """
    # 段1：裁决结论
    stance_label_map = {
        "buy": "看多（建议买入/加仓）",
        "sell": "看空（建议卖出/减仓）",
        "hold": "中性（建议持有/观望）",
        "watch": "观望等待",
    }
    parts = [f"【裁决】{stance_label_map.get(final_stance, final_stance)}（{arbitration_mode}模式）"]

    # 段2：分歧识别
    if key_conflicts:
        conflict_lines = []
        for c in key_conflicts:
            buy_side = "、".join(c.get("buy_side", [])) if c.get("buy_side") else ""
            sell_side = "、".join(c.get("sell_side", [])) if c.get("sell_side") else ""
            hold_side = "、".join(c.get("hold_side", [])) if c.get("hold_side") else ""
            opposing = sell_side or hold_side or "、".join(c.get("opposing_side", []))
            if buy_side and opposing:
                conflict_lines.append(f"  - {buy_side} 看多 vs {opposing} 持谨慎")
            elif sell_side and hold_side:
                conflict_lines.append(f"  - {sell_side} 看空 vs {hold_side} 倾向持有")
        if conflict_lines:
            parts.append("【分歧】\n" + "\n".join(conflict_lines))
    else:
        # 无立场分歧时，识别数据/逻辑差异
        parts.append("【分歧】专家立场一致，未检测到方向性冲突")

    # 段3：解决逻辑
    if arbitration_mode == "consensus":
        parts.append(f"【解决】{supporting_count} 位专家立场一致，采纳共识结论")
    elif arbitration_mode == "conflict":
        # 冲突模式：按风险优先原则解决（风控视角优先）
        if final_stance == "hold":
            parts.append("【解决】专家立场存在分歧，按风险优先原则采纳谨慎立场（持有/观望）")
        elif final_stance == "sell":
            parts.append("【解决】专家立场存在分歧，检测到看空信号，优先保护本金")
        else:
            parts.append("【解决】专家立场存在分歧，综合评估后给出方向性建议")
    else:
        parts.append("【解决】综合各专家观点给出裁决")

    # 段4：置信度依据
    confidence_basis_map = {
        "high": f"{supporting_count} 位专家立场一致支持，置信度高",
        "medium": "专家立场存在分歧或样本有限，置信度中等",
        "low": "未能从专家分析中识别明确立场，置信度低",
    }
    parts.append(f"【置信度依据】{confidence_basis_map.get(confidence, '置信度未知')}")

    reasoning = "\n".join(parts)
    # 截断到 800 字符（保持与原实现一致的字段长度约束）
    if len(reasoning) > 800:
        reasoning = reasoning[:797] + "..."
    return reasoning
