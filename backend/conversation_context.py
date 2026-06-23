"""统一对话上下文构建器。

聚合对话摘要、最近消息、持仓、估值、决策、关注列表和 RAG，
供普通聊天、单专家和多 Agent 编排共用。
"""

from __future__ import annotations

import re
from typing import Any


def estimate_text_tokens(text: str) -> int:
    """粗略估算 token，避免上下文无限膨胀。"""
    if not text:
        return 0
    # 中文按字符近似，英文按词近似，取更保守的值。
    ascii_words = len(re.findall(r"[A-Za-z0-9_]+", text))
    non_space_chars = len(re.sub(r"\s+", "", text))
    return max(ascii_words, non_space_chars // 2)


def _clip(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return text[:max_chars]
    return text[: max_chars - 12].rstrip() + "\n...（已裁剪）"


def _format_recent_messages(messages: list[dict], max_chars: int = 3000) -> str:
    lines = []
    role_labels = {"user": "用户", "assistant": "助手", "system": "系统"}
    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        role = role_labels.get(msg.get("role"), msg.get("role", "消息"))
        lines.append(f"{role}: {_clip(content, 600)}")
    return _clip("\n".join(lines), max_chars)


def _build_decision_context(user_id: str = "default", limit: int = 6) -> str:
    try:
        from db import list_decisions, list_due_decision_reviews

        active = [
            d for d in list_decisions(limit=limit * 2)
            if d.get("status") in ("proposed", "accepted", "deferred")
        ][:limit]
        due = list_due_decision_reviews(limit=3)
    except Exception:
        active = []
        due = []

    lines = []
    if active:
        lines.append("### 近期未完成决策")
        for d in active:
            target = d.get("target_name") or d.get("target_code") or d.get("target_type") or "组合"
            lines.append(
                f"- #{d.get('id')} {target}: {d.get('summary', '')}"
                f"（{d.get('decision_type', '')}/{d.get('status', '')}）"
            )
    if due:
        lines.append("### 到期需复盘")
        for d in due:
            target = d.get("target_name") or d.get("target_code") or "组合"
            lines.append(f"- #{d.get('id')} {target}: {d.get('summary', '')}")
    return "\n".join(lines) or "暂无未完成决策。"


def _build_watchlist_context(user_id: str = "default", limit: int = 8) -> str:
    try:
        from db import list_watchlist

        items = [
            item for item in list_watchlist(user_id=user_id)
            if item.get("status") != "bought"
        ][:limit]
    except Exception:
        items = []
    if not items:
        return "暂无关注标的。"

    lines = ["### 关注列表"]
    for item in items:
        trigger_parts = []
        if item.get("target_price") is not None:
            trigger_parts.append(f"目标净值 {item.get('target_price')}")
        if item.get("target_percentile") is not None:
            trigger_parts.append(f"目标分位 {item.get('target_percentile')}%")
        trigger = "，".join(trigger_parts) if trigger_parts else "未设置触发条件"
        lines.append(f"- {item.get('fund_name') or item.get('fund_code')}（{item.get('fund_code')}）：{trigger}")
    return "\n".join(lines)


def _build_user_profile_context(user_id: str = "default") -> str:
    try:
        from db.dashboard import get_user_profile

        profile = get_user_profile(user_id) or {}
    except Exception:
        profile = {}
    if not profile:
        return "暂无完整用户画像。"

    fields = [
        ("风险偏好", "risk_preference"),
        ("备用金月数", "emergency_fund_months"),
        ("月结余", "monthly_surplus"),
        ("目标权益仓位", "target_equity_ratio"),
        ("单标的上限", "max_single_position_pct"),
        ("主要目标", "primary_goal"),
        ("资金用途", "fund_usage"),
    ]
    lines = ["### 用户画像"]
    for label, key in fields:
        value = profile.get(key)
        if value not in (None, ""):
            lines.append(f"- {label}: {value}")
    return "\n".join(lines) if len(lines) > 1 else "暂无完整用户画像。"


def _build_missing_context(scenario_type: str, sections: dict[str, str]) -> str:
    missing = []
    if scenario_type in {"buy_decision", "sell_decision", "portfolio_review"}:
        profile_text = sections.get("user_profile_context", "")
        if "资金用途" not in profile_text:
            missing.append("资金用途")
        if "目标权益仓位" not in profile_text and "单标的上限" not in profile_text:
            missing.append("目标仓位")
        if "暂无完整用户画像" in profile_text:
            missing.append("风险偏好/现金流画像")
    if "用户当前无持仓记录" in sections.get("portfolio_context", ""):
        missing.append("当前持仓")
    if not missing:
        return "暂无明显缺失。"
    return "、".join(dict.fromkeys(missing))


def _compose_prompt_context(sections: dict[str, str], current_user_message: str, token_budget: int) -> str:
    ordered = [
        ("系统原则", "你是谨慎的个人理财助手。AI 只提供建议，不直接执行交易；涉及买卖必须提醒用户确认。"),
        ("用户画像", sections.get("user_profile_context", "")),
        ("当前持仓与盈亏", sections.get("portfolio_context", "")),
        ("当前估值", sections.get("valuation_context", "")),
        ("历史摘要", sections.get("conversation_summary", "")),
        ("最近对话", sections.get("recent_messages", "")),
        ("近期决策", sections.get("decision_context", "")),
        ("关注列表", sections.get("watchlist_context", "")),
        ("知识库证据", sections.get("rag_context", "")),
        ("缺失信息", sections.get("missing_context", "")),
        ("当前问题", current_user_message or ""),
    ]

    # 预留末尾当前问题和关键上下文，按预算裁剪最长的 RAG/最近消息。
    max_chars = max(1200, token_budget * 3)
    parts = []
    for title, body in ordered:
        body = body or ""
        if title == "知识库证据":
            body = _clip(body, max(1200, max_chars // 5))
        elif title == "最近对话":
            body = _clip(body, max(1200, max_chars // 4))
        parts.append(f"## {title}\n{body}")
    return _clip("\n\n".join(parts), max_chars)


def build_conversation_context(
    conversation_id: int,
    current_user_message: str,
    scenario_type: str = "general_analysis",
    agent_id: int | None = None,
    rag_context: str = "",
    token_budget: int = 6000,
    user_id: str = "default",
    recent_limit: int = 12,
) -> dict[str, Any]:
    """构建统一对话上下文包。"""
    from db import get_conversation_summary, get_messages
    from portfolio_context import build_portfolio_context, build_valuation_summary

    summary = get_conversation_summary(conversation_id)
    summary_text = summary.get("summary", "") if summary else ""
    up_to_message_id = summary.get("up_to_message_id") if summary else 0

    messages = get_messages(conversation_id, limit=recent_limit)
    if up_to_message_id:
        messages = [m for m in messages if (m.get("id") or 0) > up_to_message_id]

    sections = {
        "conversation_summary": summary_text or "暂无历史摘要。",
        "recent_messages": _format_recent_messages(messages),
        "portfolio_context": build_portfolio_context(user_id=user_id),
        "valuation_context": build_valuation_summary() or "暂无估值摘要。",
        "decision_context": _build_decision_context(user_id=user_id),
        "watchlist_context": _build_watchlist_context(user_id=user_id),
        "user_profile_context": _build_user_profile_context(user_id=user_id),
        "rag_context": rag_context or "暂无额外知识库证据。",
    }
    sections["missing_context"] = _build_missing_context(scenario_type, sections)

    prompt_context = _compose_prompt_context(sections, current_user_message, token_budget)
    return {
        "conversation_id": conversation_id,
        "scenario_type": scenario_type,
        "agent_id": agent_id,
        "token_budget": token_budget,
        "estimated_tokens": estimate_text_tokens(prompt_context),
        "sections": sections,
        "prompt_context": prompt_context,
    }
