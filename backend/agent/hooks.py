"""Hooks 系统 — 双向钩子机制

借鉴 ContextGo 的 Hooks 设计：
- prompt-transform: 修改发送给 Agent 的 prompt（如注入用户偏好）
- native-projection: 生成输出产物（如分析摘要存入知识库）

Hook 类型：
1. before_prompt: 在 prompt 发送给 LLM 前执行
2. after_response: 在 LLM 响应后执行
3. on_error: 在错误发生时执行
"""

import json
import logging
from datetime import datetime

from db._conn import _get_conn

logger = logging.getLogger(__name__)


# ── Hook 注册表 ──────────────────────────────────────────

_hooks = {
    "before_prompt": [],
    "after_response": [],
    "on_error": [],
}


def register_hook(hook_type: str, name: str, handler, priority: int = 50):
    """注册一个 Hook。

    参数:
        hook_type: before_prompt | after_response | on_error
        name: Hook 名称
        handler: 处理函数 (context: dict) -> dict
        priority: 优先级（越小越先执行）
    """
    if hook_type not in _hooks:
        logger.warning(f"未知 Hook 类型: {hook_type}")
        return

    _hooks[hook_type].append({
        "name": name,
        "handler": handler,
        "priority": priority,
    })
    _hooks[hook_type].sort(key=lambda h: h["priority"])


def run_hooks(hook_type: str, context: dict) -> dict:
    """执行指定类型的所有 Hook。

    参数:
        hook_type: before_prompt | after_response | on_error
        context: 上下文数据

    返回:
        处理后的上下文数据
    """
    if hook_type not in _hooks:
        return context

    for hook in _hooks[hook_type]:
        try:
            result = hook["handler"](context)
            if result:
                context.update(result)
        except Exception as e:
            logger.warning(f"Hook {hook['name']} 执行失败: {e}")

    return context


# ── 内置 Hooks ──────────────────────────────────────────


def hook_inject_user_preferences(context: dict) -> dict:
    """Hook: 注入用户偏好到 system prompt。"""
    user_id = context.get("user_id", "default")
    try:
        from agent.feedback_learner import get_preference_context
        pref_ctx = get_preference_context(user_id)
        if pref_ctx:
            existing_prompt = context.get("system_prompt", "")
            context["system_prompt"] = existing_prompt + "\n\n" + pref_ctx
    except Exception:
        pass
    return context


def hook_inject_memory_context(context: dict) -> dict:
    """Hook: 注入用户记忆到 system prompt。"""
    user_id = context.get("user_id", "default")
    try:
        from agent.memory_lifecycle import get_memories
        memories = get_memories(user_id, limit=5)
        if memories:
            memory_text = "\n".join(f"- {m['content'][:100]}" for m in memories[:5])
            existing_prompt = context.get("system_prompt", "")
            context["system_prompt"] = existing_prompt + f"\n\n<user_memories>\n{memory_text}\n</user_memories>"
    except Exception:
        pass
    return context


def hook_inject_risk_disclaimer(context: dict) -> dict:
    """Hook: 在分析结果后注入风险提示。"""
    response = context.get("response", "")
    analysis_type = context.get("analysis_type", "")

    # 只对投资分析类响应注入风险提示
    investment_keywords = ["买入", "卖出", "加仓", "减仓", "建议", "推荐"]
    if any(kw in response for kw in investment_keywords):
        disclaimer = "\n\n⚠️ 风险提示：以上分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。"
        context["response"] = response + disclaimer

    return context


def hook_save_analysis_summary(context: dict) -> dict:
    """Hook: 保存分析摘要到知识库（native-projection）。"""
    response = context.get("response", "")
    analysis_type = context.get("analysis_type", "")
    user_id = context.get("user_id", "default")

    # 只保存有价值的分析结果
    if len(response) > 200 and analysis_type in ("valuation", "portfolio", "market"):
        try:
            from agent.memory_lifecycle import save_memory
            save_memory(
                user_id=user_id,
                memory_type="fact",
                content=response[:500],
                source=f"analysis:{analysis_type}",
                evidence_count=1,
                confidence=0.7,
                metadata={"analysis_type": analysis_type}
            )
        except Exception:
            pass

    return context


def hook_detect_repeated_question(context: dict) -> dict:
    """Hook: 检测重复提问，提醒用户。"""
    conversation_id = context.get("conversation_id")
    current_query = context.get("query", "")

    if not conversation_id or not current_query:
        return context

    try:
        from db import get_messages
        messages = get_messages(conversation_id)
        user_messages = [m for m in messages if m.get("role") == "user"]

        # 检查最近 5 条用户消息是否有相似内容
        recent_queries = [m.get("content", "") for m in user_messages[-5:]]
        for prev_query in recent_queries[:-1]:
            if _is_similar_query(current_query, prev_query):
                context["repeated_query_detected"] = True
                context["repeated_query_hint"] = "检测到您可能在重复提问，是否需要我换一种方式回答？"
                break
    except Exception:
        pass

    return context


def _is_similar_query(q1: str, q2: str) -> bool:
    """简单判断两个查询是否相似。"""
    # 去除标点和空格
    import re
    q1_clean = re.sub(r'[^\w]', '', q1)
    q2_clean = re.sub(r'[^\w]', '', q2)

    if not q1_clean or not q2_clean:
        return False

    # 如果完全相同
    if q1_clean == q2_clean:
        return True

    # 如果长度相近且有大量重叠
    if abs(len(q1_clean) - len(q2_clean)) < 5:
        # 计算共同字符比例
        common = sum(1 for c in q1_clean if c in q2_clean)
        if common / max(len(q1_clean), len(q2_clean)) > 0.8:
            return True

    return False


# ── 注册内置 Hooks ──────────────────────────────────────


def register_builtin_hooks():
    """注册所有内置 Hooks。"""
    # before_prompt 类型
    register_hook("before_prompt", "inject_preferences", hook_inject_user_preferences, priority=10)
    register_hook("before_prompt", "inject_memory", hook_inject_memory_context, priority=20)
    register_hook("before_prompt", "detect_repeated", hook_detect_repeated_question, priority=30)

    # after_response 类型
    register_hook("after_response", "risk_disclaimer", hook_inject_risk_disclaimer, priority=10)
    register_hook("after_response", "save_summary", hook_save_analysis_summary, priority=50)

    logger.info("内置 Hooks 已注册")


# 自动注册
register_builtin_hooks()
