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
    # 中文约1.5字符/token，英文约4字符/token
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en_chars = len(text) - cn_chars
    return int(cn_chars / 1.5 + en_chars / 4)


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


def _detect_context_conflicts(sections: dict[str, str]) -> list[str]:
    """检测上下文中的矛盾指令，按优先级排序解决。"""
    conflicts = []
    prefs = sections.get("user_profile_context", "")
    kyc = sections.get("kyc", "")
    strategy = sections.get("strategy", "")
    decision = sections.get("decision_context", "")

    # 优先级：KYC > 策略 > 偏好
    if "详细数据支撑" in prefs and ("新手" in kyc or "保守" in kyc):
        conflicts.append("[KYC覆盖] 新手用户偏好详细数据 → 提供数据但用通俗语言解释")
    if "激进" in kyc and "保守" in strategy:
        conflicts.append("[策略冲突] 激进画像 vs 保守策略 → 提醒风险，让用户选择")
    if "长期" in kyc and "短期" in decision:
        conflicts.append("[期限冲突] 长期目标 vs 短期操作 → 标记为需确认")
    return conflicts


def _build_change_context(user_id: str = "default") -> str:
    """构建近期变化上下文：对比最近两次持仓快照。"""
    try:
        from db import list_portfolio_snapshots
        snapshots = list_portfolio_snapshots(user_id=user_id, limit=2)
        if len(snapshots) < 2:
            return "暂无历史快照对比。"

        latest = snapshots[0]
        prev = snapshots[1]

        changes = []
        # 总资产变化
        val_diff = (latest.get("total_value", 0) or 0) - (prev.get("total_value", 0) or 0)
        if abs(val_diff) > 100:
            pct = val_diff / (prev.get("total_value", 1) or 1) * 100
            changes.append(f"总资产变化：{val_diff:+,.0f}元（{pct:+.1f}%）")

        # 持仓数量变化
        cnt_diff = (latest.get("holding_count", 0) or 0) - (prev.get("holding_count", 0) or 0)
        if cnt_diff != 0:
            changes.append(f"持仓数量变化：{cnt_diff:+d}只")

        # 现金变化
        cash_diff = (latest.get("cash", 0) or 0) - (prev.get("cash", 0) or 0)
        if abs(cash_diff) > 100:
            changes.append(f"现金变化：{cash_diff:+,.0f}元")

        if not changes:
            return "近期持仓无显著变化。"

        date_a = prev.get("snapshot_date", "")[:10]
        date_b = latest.get("snapshot_date", "")[:10]
        header = f"{date_a} → {date_b} 变化："
        return header + "；".join(changes)
    except Exception:
        return "暂无变化追踪数据。"


def _build_entity_memory_context(limit: int = 10) -> str:
    """增强4: 获取近期实体属性变化，注入到上下文中。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        rows = conn.execute("""
            SELECT entity_name, entity_type, attribute, old_value, new_value, snapshot_date
            FROM entity_memory
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        if not rows:
            return ""

        # 按实体聚合
        entities = {}
        for r in rows:
            name = r["entity_name"]
            entities.setdefault(name, []).append(dict(r))

        lines = ["### 近期标的属性变化"]
        for name, attr_changes in entities.items():
            latest = attr_changes[0]
            old_val = latest.get("old_value", "")
            new_val = latest.get("new_value", "")
            if old_val and old_val != new_val:
                lines.append(f"- **{name}**：{latest['attribute']} {old_val} → {new_val}（{latest['snapshot_date']}）")
            else:
                lines.append(f"- **{name}**：{latest['attribute']} = {new_val}（{latest['snapshot_date']}）")

        return "\n".join(lines)
    except Exception:
        return ""


def _build_trade_pattern_context(user_id: str = "default") -> str:
    """构建交易行为模式上下文，注入到对话中供行为教练引用。"""
    try:
        from db.portfolio import analyze_trade_patterns
        data = analyze_trade_patterns(user_id)
        if data.get("error"):
            return ""
        lines = ["### 交易行为数据"]
        if data.get("chase_pe_median") is not None:
            lines.append(f"- 追涨倾向：买入时PE分位中位数 {data['chase_pe_median']}%" +
                         ("（偏高）" if data['chase_pe_median'] > 60 else "（合理）"))
        if data.get("panic_sell_median") is not None:
            lines.append(f"- 杀跌倾向：亏损卖出幅度中位数 {data['panic_sell_median']}%")
        if data.get("avg_holding_days") is not None:
            lines.append(f"- 持有耐心：平均持仓 {data['avg_holding_days']} 天")
        if data.get("monthly_avg_trades") is not None:
            lines.append(f"- 频繁交易度：月均交易 {data['monthly_avg_trades']} 次")
        if data.get("win_rate") is not None:
            lines.append(f"- 胜率：{data['win_rate']}%（{data.get('total_sells', 0)}次卖出）")
        if data.get("max_single_loss"):
            lines.append(f"- 最大单笔亏损：{data['max_single_loss']} 元")
        if data.get("trade_style"):
            lines.append(f"- 交易风格：{data['trade_style']}")
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:
        return ""


def record_entity_snapshots(analysis_text: str, source: str = "analysis", source_id: int = 0):
    """增强4: 从分析文本中提取实体属性变化并记录（仅在值变化时记录）。"""
    try:
        from db._conn import _get_conn
        from datetime import datetime

        # 提取实体属性（关键词匹配模式）
        entity_patterns = [
            # 指数估值
            (r'(沪深300|中证500|创业板|恒生科技|纳斯达克|标普500|中证红利|红利指数)\s*(?:PE|pe|市盈率)\s*(?:分位|百分位)?\s*[：:为是]?\s*(\d+\.?\d*%?)', 'index', 'pe_percentile'),
            (r'(沪深300|中证500|创业板|恒生科技|纳斯达克|标普500|中证红利|红利指数)\s*(?:PB|pb|市净率)\s*(?:分位|百分位)?\s*[：:为是]?\s*(\d+\.?\d*%?)', 'index', 'pb_percentile'),
            # 估值水平
            (r'(沪深300|中证500|创业板|恒生科技|纳斯达克|标普500)\s*(?:估值|水平)\s*[：:为是]?\s*(低估|合理|高估|极度低估|极度高估)', 'index', 'valuation_level'),
            # 债市温度
            (r'债市温度\s*[：:为是]?\s*(\d+\.?\d*)', 'market', 'bond_temperature'),
        ]

        conn = _get_conn()
        snapshot_date = datetime.now().strftime("%Y-%m-%d")

        for pattern, entity_type, attribute in entity_patterns:
            matches = re.findall(pattern, analysis_text)
            for match in matches:
                if isinstance(match, tuple):
                    entity_name = match[0]
                    new_value = match[1]
                else:
                    entity_name = "债市"
                    new_value = match

                # 查询上次记录
                last = conn.execute("""
                    SELECT new_value FROM entity_memory
                    WHERE entity_name = ? AND attribute = ?
                    ORDER BY id DESC LIMIT 1
                """, (entity_name, attribute)).fetchone()

                old_value = last["new_value"] if last else ""

                # 属性变化了才记录
                if new_value != old_value:
                    conn.execute("""
                        INSERT INTO entity_memory
                        (entity_name, entity_type, attribute, old_value, new_value, source, source_id, snapshot_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (entity_name, entity_type, attribute, old_value, new_value, source, source_id, snapshot_date))

        conn.commit()
        conn.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"记录实体记忆失败: {e}")


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
        ("近期标的变化", sections.get("entity_memory", "")),  # 增强4: 实体记忆
        ("交易行为数据", sections.get("trade_pattern_context", "")),
        ("知识库证据", sections.get("rag_context", "")),
        ("缺失信息", sections.get("missing_context", "")),
        ("上下文冲突", sections.get("conflicts", "")),
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
        "change_context": _build_change_context(user_id=user_id),
        "entity_memory": _build_entity_memory_context(),  # 增强4: 实体记忆
        "trade_pattern_context": _build_trade_pattern_context(user_id=user_id),
    }
    sections["missing_context"] = _build_missing_context(scenario_type, sections)

    # 冲突检测：发现上下文中的矛盾指令
    conflicts = _detect_context_conflicts(sections)
    if conflicts:
        sections["conflicts"] = "⚠️ 检测到上下文冲突：\n" + "\n".join(f"- {c}" for c in conflicts)

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
