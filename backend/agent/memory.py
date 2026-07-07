"""上下文与记忆管理 — 语义压缩 + 跨对话用户记忆"""

import json
import logging

from services.llm_service import _call_llm, MODEL
from db.config import get_config_int, get_config_float, get_config

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


def _fallback_summary(messages: list) -> str:
    """不调 LLM 的回退摘要：简单截取最近 5 条，保留最小上下文。"""
    parts = []
    for msg in messages[-5:]:
        if msg["role"] == "user":
            parts.append(f"用户问: {msg['content'][:50]}")
        elif msg["role"] == "assistant":
            parts.append(f"助手答: {msg['content'][:30]}")
    return "\n".join(parts) if parts else "对话摘要不可用"


def _generate_summary(messages: list) -> str:
    """调用 LLM 将消息列表压缩为摘要。
    受 `llm_cost.memory_summarize` 开关控制，默认关闭走回退。
    """
    if get_config("llm_cost.memory_summarize", "false") != "true":
        return _fallback_summary(messages)

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
            temperature=get_config_float('llm.temperature_eval', 0.2),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 500),
        )
        return response.choices[0].message.content or "对话摘要生成失败"
    except Exception as e:
        logger.error(f"生成对话摘要失败: {e}")
        return _fallback_summary(messages)


def build_user_memory_context(user_id: str = "default") -> str:
    """
    构建跨对话的用户记忆上下文。

    来源：
    - user_profiles 表的偏好画像
    - 用户持仓信息
    - 近期对话主题
    - user_memories 表的跨会话持久记忆（按 token 预算裁剪）

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

    # 4. 用户记忆（跨对话持久记忆，从 user_memories 表读取）
    #    修复 F1：此前该表只写不读，记忆完全不生效
    try:
        from agent.memory_lifecycle import get_memories
        memories = get_memories(user_id, limit=10)
        if memories:
            mem_lines = []
            mem_budget = 300  # 记忆段 token 预算
            used = 0
            for m in memories:
                # 跳过已归档 / 已压缩的历史记忆
                if m.get("is_archived") or m.get("is_compacted"):
                    continue
                content = (m.get("content") or "").strip()
                if not content:
                    continue
                line = f"- {content[:120]}"
                t = estimate_tokens(line)
                if used + t > mem_budget:
                    break
                mem_lines.append(line)
                used += t
            if mem_lines:
                parts.append("<user_memories>\n" + "\n".join(mem_lines) + "\n</user_memories>")
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


def get_conversation_summary(conversation_id: int) -> str:
    """
    获取对话摘要文本，用于跨轮次上下文连续性。

    优先从 conversation_summaries 表读取最近摘要；
    如果没有摘要，从最近5条消息生成简要摘要。

    返回摘要文本字符串（非 dict）。
    """
    if not conversation_id:
        return ""

    # 1. 尝试从数据库读取已有摘要
    try:
        from db.conversations import get_conversation_summary as _get_summary
        cached = _get_summary(conversation_id)
        if cached and cached.get("summary"):
            return cached["summary"]
    except Exception:
        pass

    # 2. 没有摘要，从最近5条消息生成简要摘要
    try:
        from db.conversations import get_messages
        messages = get_messages(conversation_id, limit=5)
        if not messages:
            return ""

        parts = []
        for msg in messages[-5:]:
            role = msg.get("role", "")
            content = (msg.get("content") or "")[:120]
            if not content:
                continue
            if role == "user":
                parts.append(f"用户问: {content}")
            elif role == "assistant":
                parts.append(f"助手答: {content[:80]}")

        if not parts:
            return ""

        summary = "\n".join(parts)
        # 尝试保存摘要供后续使用
        try:
            from db.conversations import save_conversation_summary
            save_conversation_summary(conversation_id, len(messages), summary)
        except Exception:
            pass

        return summary
    except Exception:
        return ""


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


# ── 三层压缩策略（Pipeline Phase C 增强） ──────────


def build_conversation_context(
    history: list,
    conversation_id: int,
    max_tokens: int = 3000,
    complexity: str = "medium",
) -> list:
    """三层压缩策略：最近原文 + 次近结构化摘要 + 最早一句话总结。

    替代 compress_history_semantic 的增强版，支持三层压缩：
    1. recent（占预算60%）：最近 K 轮保留原文
    2. mid（占预算30%）：次近消息合并为结构化摘要（含意图+结论+关键数据）
    3. early（占预算10%）：最早消息用一句话总结

    Args:
        history: 历史消息列表
        conversation_id: 对话 ID
        max_tokens: 总 token 预算
        complexity: 复杂度，影响滑动窗口大小

    Returns:
        压缩后的消息列表
    """
    if not history:
        return []

    # 策略1：不超限直接返回
    total_tokens = sum(estimate_tokens(msg.get("content", "")) for msg in history)
    if total_tokens <= max_tokens:
        return history

    # 策略2：三层压缩
    recent_budget = int(max_tokens * 0.6)
    mid_budget = int(max_tokens * 0.3)
    early_budget = max(max_tokens - recent_budget - mid_budget, 100)

    # 滑动窗口大小（按复杂度）
    sliding_window = {"simple": 5, "medium": 8, "complex": 12}.get(complexity, 8)

    # 从后往前切分
    recent_msgs = []
    recent_used = 0
    split_recent = len(history)
    for i in range(len(history) - 1, -1, -1):
        msg_tokens = estimate_tokens(history[i].get("content", ""))
        if recent_used + msg_tokens > recent_budget or len(recent_msgs) >= sliding_window * 2:
            split_recent = i + 1
            break
        recent_used += msg_tokens
        recent_msgs.insert(0, history[i])

    # 至少保留最近 2 条
    split_recent = min(split_recent, len(history) - 2) if len(history) > 2 else len(history)

    early_and_mid = history[:split_recent]
    if not early_and_mid:
        return recent_msgs

    # 切分 early 和 mid
    mid_tokens = 0
    split_mid = len(early_and_mid)
    for i in range(len(early_and_mid) - 1, -1, -1):
        msg_tokens = estimate_tokens(early_and_mid[i].get("content", ""))
        if mid_tokens + msg_tokens > mid_budget:
            split_mid = i + 1
            break
        mid_tokens += msg_tokens

    mid_msgs = early_and_mid[split_mid:] if split_mid < len(early_and_mid) else []
    early_msgs = early_and_mid[:split_mid] if split_mid > 0 else []

    result = []

    # 最早消息：一句话总结
    if early_msgs:
        early_summary = _build_early_summary(early_msgs, conversation_id)
        if early_summary:
            result.append({
                "role": "system",
                "content": f"## 早期对话概要（{len(early_msgs)}条消息）\n{early_summary}",
            })

    # 次近消息：结构化摘要
    if mid_msgs:
        mid_summary = _build_structured_summary(mid_msgs, conversation_id)
        if mid_summary:
            result.append({
                "role": "system",
                "content": f"## 中期对话摘要（{len(mid_msgs)}条消息）\n{mid_summary}",
            })

    # 最近消息：原文
    result.extend(recent_msgs)

    return result


def _build_early_summary(messages: list, conversation_id: int) -> str:
    """最早消息的一句话总结（极简，节省 token）。"""
    if not messages:
        return ""

    # 取第一条用户消息的核心内容
    first_user = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = (msg.get("content", "") or "").strip()
            if content:
                first_user = content[:60]
                break

    # 取最后一条助手消息的核心结论
    last_assistant = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = (msg.get("content", "") or "").strip()
            if content:
                # 提取第一句作为结论
                last_assistant = content.split("。")[0][:60]
                break

    parts = []
    if first_user:
        parts.append(f"用户最初问：{first_user}")
    if last_assistant:
        parts.append(f"结论：{last_assistant}")
    return "；".join(parts) if parts else f"共{len(messages)}条早期对话"


def _build_structured_summary(messages: list, conversation_id: int) -> str:
    """次近消息的结构化摘要（含意图+结论+关键数据）。

    优先复用缓存的 conversation_summaries；缓存不可用时降级为规则提取。
    """
    if not messages:
        return ""

    # 1. 尝试从缓存读取
    if conversation_id:
        try:
            from db.conversations import get_conversation_summary as _get_summary
            cached = _get_summary(conversation_id)
            if cached and cached.get("summary"):
                return cached["summary"][:500]
        except Exception:
            pass

    # 2. 规则提取（不调 LLM，零成本）
    parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = (msg.get("content", "") or "").strip()
        if not content:
            continue
        if role == "user":
            # 用户问题：保留前 80 字
            parts.append(f"用户问：{content[:80]}")
        elif role == "assistant":
            # 助手回答：提取第一段或第一句
            first_para = content.split("\n\n")[0][:100]
            parts.append(f"助手答：{first_para}")

    if not parts:
        return ""

    # 限制总长度
    result = "\n".join(parts[:8])  # 最多 8 条
    if len(result) > 500:
        result = result[:497] + "..."
    return result


def update_conversation_summary(conversation_id: int) -> bool:
    """更新对话摘要（Phase 5 记忆持久化调用）。

    复用 compress_history_semantic 的摘要生成逻辑，写入 conversation_summaries 表。

    Args:
        conversation_id: 对话 ID

    Returns:
        是否更新成功
    """
    if not conversation_id:
        return False

    try:
        from db.conversations import get_messages, save_conversation_summary
        messages = get_messages(conversation_id, limit=20)
        if not messages or len(messages) < 4:
            return False  # 消息太少不需要摘要

        # 使用结构化摘要生成
        summary = _build_structured_summary(messages, conversation_id)
        if not summary:
            return False

        save_conversation_summary(conversation_id, len(messages), summary)
        logger.info(f"[memory] 对话 {conversation_id} 摘要已更新")
        return True
    except Exception as e:
        logger.debug(f"[memory] 摘要更新跳过: {e}")
        return False
