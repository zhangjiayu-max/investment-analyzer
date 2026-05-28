"""上下文与记忆管理 — 语义压缩 + 跨对话用户记忆"""

import json
import logging

from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数。
    中文约 1.5 字符/token，英文约 4 字符/token。
    混合文本取折中值。
    """
    if not text:
        return 0
    # 计算中文字符比例
    cn_chars = sum(1 for c in text if '一' <= c <= '鿿')
    total = len(text)
    if total == 0:
        return 0
    cn_ratio = cn_chars / total
    # 中文: 字符/1.5, 英文: 字符/4, 按比例混合
    if cn_ratio > 0.5:
        return int(total / 1.5)
    else:
        return int(total / 3.0)


def compress_history_semantic(history: list, max_tokens: int = 3000, conversation_id: int = None) -> list:
    """
    语义化压缩对话历史。

    - token 不超限则原文返回
    - 超限则对旧消息调用 LLM 做摘要
    - 返回 [摘要 system msg] + 近期原文消息

    参数:
        history: [{"role": "user"|"assistant"|"system", "content": "..."}]
        max_tokens: 历史消息的 token 预算
        conversation_id: 对话 ID（用于缓存摘要）
    """
    if not history:
        return []

    # 估算当前 token
    total_tokens = sum(estimate_tokens(msg.get("content", "")) for msg in history)

    if total_tokens <= max_tokens:
        return history

    # 需要压缩：保留近期消息原文，旧消息做摘要
    # 从后往前保留，直到 token 不超限
    recent_tokens = 0
    split_idx = len(history)
    for i in range(len(history) - 1, -1, -1):
        msg_tokens = estimate_tokens(history[i].get("content", ""))
        if recent_tokens + msg_tokens > max_tokens * 0.7:
            split_idx = i + 1
            break
        recent_tokens += msg_tokens

    # 至少保留最近 2 条消息
    split_idx = max(split_idx, len(history) - 2)

    old_messages = history[:split_idx]
    recent_messages = history[split_idx:]

    if not old_messages:
        return recent_messages

    # 检查是否有缓存的摘要
    cached_summary = None
    if conversation_id:
        try:
            from db import get_conversation_summary
            cached = get_conversation_summary(conversation_id)
            if cached and cached.get("up_to_message_id", 0) >= len(old_messages):
                cached_summary = cached["summary"]
        except Exception:
            pass

    if cached_summary:
        summary = cached_summary
    else:
        # 调用 LLM 生成摘要
        summary = _generate_summary(old_messages)

        # 缓存摘要
        if conversation_id:
            try:
                from db import save_conversation_summary
                save_conversation_summary(conversation_id, len(old_messages), summary)
            except Exception:
                pass

    summary_msg = {"role": "system", "content": f"以下是早期对话摘要（共{len(old_messages)}条消息）：\n{summary}"}
    return [summary_msg] + recent_messages


def _generate_summary(messages: list) -> str:
    """调用 LLM 将消息列表压缩为摘要。"""
    # 格式化消息
    formatted = []
    for msg in messages:
        role = "用户" if msg["role"] == "user" else "助手" if msg["role"] == "assistant" else "系统"
        content = msg.get("content", "")[:500]
        formatted.append(f"{role}: {content}")

    history_text = "\n".join(formatted)

    prompt = f"""请将以下对话历史压缩为简洁的摘要，保留关键信息：
- 用户的核心问题和关注点
- 已得出的结论和数据（具体数字）
- 未完成的讨论或待办事项
- 用户表达的偏好或特殊要求

对话历史：
{history_text}

要求：
- 用简洁的中文，不超过 300 字
- 保留具体的数字、名称、结论
- 不要丢失重要的上下文信息"""

    try:
        response = _call_llm(
            caller="memory:summarize",
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个精确的对话摘要助手。只输出摘要内容。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=500,
        )
        return response.choices[0].message.content or "对话摘要生成失败"
    except Exception as e:
        logger.error(f"生成对话摘要失败: {e}")
        # 回退：简单截取
        parts = []
        for msg in messages[-5:]:
            if msg["role"] == "user":
                parts.append(f"用户问: {msg['content'][:50]}")
            elif msg["role"] == "assistant":
                parts.append(f"助手答: {msg['content'][:30]}")
        return "\n".join(parts) if parts else "对话摘要不可用"


def build_user_memory_context(user_id: str = "default") -> str:
    """
    构建跨对话的用户记忆上下文。

    来源：
    - user_profiles 表的偏好画像
    - 用户持仓信息
    - 近期对话主题

    返回约 300 token 的字符串。
    """
    parts = []

    # 1. 用户画像（来自反馈学习）
    try:
        from agent.feedback_learner import get_preference_context
        pref_ctx = get_preference_context(user_id)
        if pref_ctx:
            parts.append(pref_ctx)
    except Exception:
        pass

    # 2. 持仓概况
    try:
        from db import list_portfolio_holdings
        holdings = list_portfolio_holdings()
        if holdings:
            holding_names = [h.get("fund_name", h.get("index_name", "")) for h in holdings[:5]]
            parts.append(f"<portfolio>用户当前持有: {'、'.join(holding_names)}</portfolio>")
    except Exception:
        pass

    # 3. 近期对话主题
    try:
        from db import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT title FROM conversations ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        conn.close()
        if rows:
            titles = [r["title"] for r in rows if r["title"] != "新对话"]
            if titles:
                parts.append(f"<recent_topics>近期讨论: {'、'.join(titles[:3])}</recent_topics>")
    except Exception:
        pass

    return "\n".join(parts) if parts else ""


def get_token_budget(complexity: str) -> dict:
    """
    根据复杂度返回 token 预算分配。

    返回:
        {
            "total_context": 总 token 预算,
            "history_pct": 历史消息占比,
            "memory_pct": 用户记忆占比,
            "rag_pct": RAG 上下文占比,
        }
    """
    if complexity == "simple":
        return {"total_context": 4000, "history_pct": 0.7, "memory_pct": 0.1, "rag_pct": 0.2}
    elif complexity == "medium":
        return {"total_context": 6000, "history_pct": 0.6, "memory_pct": 0.15, "rag_pct": 0.25}
    else:  # complex
        return {"total_context": 8000, "history_pct": 0.5, "memory_pct": 0.2, "rag_pct": 0.3}


def compress_rag_token_aware(rag_context: str, max_tokens: int = 1000) -> str:
    """
    Token 感知的 RAG 上下文截断。
    """
    if not rag_context:
        return ""

    if estimate_tokens(rag_context) <= max_tokens:
        return rag_context

    # 按 token 预算截断
    max_chars = int(max_tokens * 2.5)  # 粗略换算
    truncated = rag_context[:max_chars]

    # 找到最后一个完整段落
    last_break = truncated.rfind("\n\n")
    if last_break > max_chars * 0.7:
        truncated = truncated[:last_break]

    return truncated + "\n...(已截断)"
