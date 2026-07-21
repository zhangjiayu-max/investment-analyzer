"""仲裁 Agent 的轻量裁决工具。"""

from __future__ import annotations

from collections import Counter


def _detect_stance(text: str) -> str:
    """从专家分析文本中检测操作立场。

    conv#131 修复：原优先级 hold > buy > sell 且 hold 关键词含"风险""谨慎"，
    导致几乎所有专家都被识别为 hold，key_conflicts 恒空。

    新优先级：sell > buy > hold（先检测明确操作意图，最后 fallback 到 hold）。
    否定形式（"不建议X"/"先别X"/"暂停X"/"不X"）优先识别为 hold 或 unknown。
    """
    text = (text or "").lower()
    # 否定形式优先：不建议/先别/暂停 + 任何操作 → hold
    # 避免"不建议加仓""不建议买入"被 buy 关键词截获
    if any(neg in text for neg in ["不建议", "先别", "暂停"]):
        return "hold"
    # sell 优先级最高：明确卖出/减仓意图
    # "减仓/清仓/赎回/退出/止损"直接判定 sell（这些词很少用于否定语境）
    # "卖出"需要排除"不卖出/没有卖出"等否定形式
    if any(kw in text for kw in ["减仓", "清仓", "赎回", "退出", "止损"]):
        return "sell"
    if "卖出" in text:
        # 检查"卖出"是否在否定语境中
        sell_negated = any(neg in text for neg in ["不卖出", "没有卖出", "无需卖出", "暂不卖出", "不急着卖出"])
        if not sell_negated:
            return "sell"
    # buy 次高：明确买入/加仓意图
    # 但排除"不买入""没有买入""而非...买入/建仓"等否定形式
    if any(kw in text for kw in ["建议买", "买入", "加仓", "建仓", "上车", "分批买", "定投", "补仓"]):
        neg_contexts = ["不买入", "没有买入", "无需买入", "暂不买入", "不急买入",
                        "而非长期价值资金的稳步建仓", "而非.*建仓"]
        # 用正则检查"而非...建仓/买入"模式（10 字符内）
        is_negated = any(neg in text for neg in neg_contexts)
        if not is_negated:
            import re
            if re.search(r"而非[^。]{0,15}(建仓|买入|加仓)", text):
                is_negated = True
        if not is_negated:
            return "buy"
    # hold 最低：被动立场，仅匹配明确的不操作意图
    # 移除"风险""谨慎"（风险描述不是立场），移除"止盈"（盈利操作不是 sell）
    if any(kw in text for kw in ["观望", "持有"]):
        return "hold"
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
    # conv#131 修复：原逻辑"1 个 hold 就压倒一切"违反多数决原则
    # 新逻辑：hold 需过半才生效，否则 buy/sell 的明确意图优先
    total_stanced = sum(counts.values())
    half = total_stanced / 2
    if counts["hold"] > half:
        final_stance = "hold"
        arbitration_mode = "conflict" if (counts["buy"] or counts["sell"]) else "consensus"
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

    # conv#131 修复：disagreements 只提取明确操作意图（buy/sell），不含 hold
    # 原逻辑含 hold 导致大量"伪 hold"污染分歧列表
    disagreements = [
        {
            "agent_key": result.get("agent_key", "unknown"),
            "agent": result.get("agent", result.get("agent_key", "unknown")),
            "stance": result.get("stance") or _detect_stance(result.get("analysis", "")),
        }
        for result in specialist_results or []
        if (result.get("stance") or _detect_stance(result.get("analysis", ""))) in ("buy", "sell")
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

    # conv#131 修复：key_conflicts 只检测 buy vs sell 的真实分歧
    # 原逻辑含 buy_vs_hold/sell_vs_hold，但 hold 是被动立场不是真实分歧方，
    # 且"伪 hold"误判严重导致大量假冲突或假共识
    stance_groups: dict[str, list[str]] = {"buy": [], "sell": []}
    for d in disagreements:
        s = d.get("stance", "unknown")
        if s in stance_groups:
            agent_label = d.get("agent") or d.get("agent_key") or "unknown"
            stance_groups[s].append(agent_label)
    key_conflicts = []
    if stance_groups["buy"] and stance_groups["sell"]:
        key_conflicts.append({
            "type": "buy_vs_sell",
            "buy_side": stance_groups["buy"],
            "sell_side": stance_groups["sell"],
            "note": "看多与看空观点存在分歧",
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
            if buy_side and sell_side:
                conflict_lines.append(f"  - {buy_side} 看多 vs {sell_side} 看空")
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
