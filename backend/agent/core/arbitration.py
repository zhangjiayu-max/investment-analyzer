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

    summary = {
        "query": query,
        "final_stance": final_stance,
        "arbitration_mode": arbitration_mode,
        "reasons": reasons,
        "supporting_agents": supporting_agents,
        "shared_evidence": shared_evidence,
        "disagreements": [
            {
                "agent_key": result.get("agent_key", "unknown"),
                "agent": result.get("agent", result.get("agent_key", "unknown")),
                "stance": result.get("stance") or _detect_stance(result.get("analysis", "")),
            }
            for result in specialist_results or []
            if (result.get("stance") or _detect_stance(result.get("analysis", ""))) in ("buy", "sell", "hold")
        ],
    }
    return summary
