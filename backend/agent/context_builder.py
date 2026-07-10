"""分层上下文构建器 — 6 层架构，按层级组装专家可见的上下文。

设计要点：
- Layer 0: System Prompts（永久，~500 tokens）
- Layer 1: 用户画像（跨对话，~200 tokens）
- Layer 2: 对话记忆（本对话，动态）
- Layer 3: 共享工作上下文（本次分析，~1500 tokens）
- Layer 4: 专家私有上下文（每个专家独立）
- Layer 5: 黑板共享（专家间，动态增长）

每个专家看到的上下文 = Layer 0 + 1 + 2 + 3 + 5 + 4（自己的）
关键优化：
- 专家间不互传全文分析，只传结构化结论
- Layer 4 不跨专家共享
- Layer 5 黑板 ≤800 字
- Layer 2 滑动窗口：simple=5轮, medium=8轮, complex=12轮

与现有代码的关系：
- 替代 orchestrator.py 中 prebuilt_context 的简单拼接
- 整合 memory.py 的 compress_history_semantic / build_user_memory_context
- 整合 rag.py 的 build_rag_context_with_details
"""

import logging
from typing import Optional, Any

from db.config import get_config_int, get_config_bool

logger = logging.getLogger(__name__)


# ── 滑动窗口配置 ──────────────────────────────

_SLIDING_WINDOW_SIZES = {
    "simple": 5,
    "medium": 8,
    "complex": 12,
}


def _get_sliding_window_size(complexity: str) -> int:
    """根据复杂度获取滑动窗口大小（保留最近 N 轮原文）。"""
    default = _SLIDING_WINDOW_SIZES.get(complexity, 8)
    try:
        return get_config_int(
            f"agent.context_sliding_window_{complexity}",
            default,
        )
    except Exception:
        return default


# ── 上下文层构建函数 ──────────────────────────

def build_layer0_system(agent: dict) -> str:
    """Layer 0: 系统提示词（永久，~500 tokens）。

    Args:
        agent: 专家配置 dict，包含 system_prompt / name / description 等
    """
    parts = []
    system_prompt = agent.get("system_prompt", "") or ""
    if system_prompt:
        parts.append(system_prompt)

    # 行为约束（可配置开关）
    include_constraints = True
    try:
        include_constraints = get_config_bool("agent.context_include_constraints", True)
    except Exception:
        pass

    if include_constraints:
        parts.append(
            "\n## 行为约束\n"
            "1. 只使用上下文中明确提供的数据，禁止编造\n"
            "2. 基金代码必须通过工具验证\n"
            "3. 输出 Markdown 格式，禁止 emoji 标题\n"
            "4. 结尾包含具体操作建议\n"
        )

    return "\n".join(parts)


def build_layer1_user_profile(user_id: str = "default") -> str:
    """Layer 1: 用户画像（跨对话，~200 tokens）。

    复用 memory.py 的 build_user_memory_context。
    """
    try:
        from agent.memory import build_user_memory_context
        return build_user_memory_context(user_id) or ""
    except Exception as e:
        logger.debug(f"[context_builder] Layer 1 加载失败: {e}")
        return ""


def build_layer2_conversation_history(
    history: list,
    conversation_id: int,
    complexity: str = "medium",
    max_tokens: int = 3000,
) -> str:
    """Layer 2: 对话记忆（本对话，动态）。

    三层压缩策略：
    1. 最近 K 轮保留原文（K = 滑动窗口大小）
    2. 次近消息合并为结构化摘要
    3. 最早消息用一句话总结

    Args:
        history: 历史消息列表 [{"role": "user"/"assistant", "content": "..."}]
        conversation_id: 对话 ID（用于查摘要表）
        complexity: 复杂度，决定滑动窗口大小
        max_tokens: 最大 token 数
    """
    if not history:
        return ""

    try:
        from agent.memory import compress_history_semantic, estimate_tokens
    except ImportError:
        # 退化：简单截断
        return _simple_history_truncate(history, max_tokens)

    # 复用现有压缩函数（已经实现了"近期保留+旧消息摘要"逻辑）
    try:
        compressed = compress_history_semantic(
            history, max_tokens=max_tokens, conversation_id=conversation_id
        )
        if not compressed:
            return ""

        # 格式化为文本
        parts = []
        for msg in compressed:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content:
                continue
            if role == "user":
                parts.append(f"用户: {content}")
            elif role == "assistant":
                parts.append(f"助手: {content}")
            else:
                parts.append(content)
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"[context_builder] Layer 2 压缩失败，降级截断: {e}")
        return _simple_history_truncate(history, max_tokens)


def _simple_history_truncate(history: list, max_tokens: int) -> str:
    """简单截断历史（降级方案）。"""
    try:
        from agent.memory import estimate_tokens
    except ImportError:
        estimate_tokens = lambda x: len(x) // 3  # 粗略估算

    parts = []
    used = 0
    # 从最近的消息往前取
    for msg in reversed(history[-10:]):  # 最多看最近 10 条
        content = msg.get("content", "")
        if not content:
            continue
        tokens = estimate_tokens(content)
        if used + tokens > max_tokens:
            break
        role = msg.get("role", "")
        prefix = "用户: " if role == "user" else "助手: " if role == "assistant" else ""
        parts.insert(0, f"{prefix}{content}")
        used += tokens
    return "\n".join(parts) if parts else ""


def build_layer3_shared_work(
    rag_context: str = "",
    portfolio_summary: str = "",
    valuation_data: dict = None,
    market_context: str = "",
    max_tokens: int = 1500,
    rag_low_quality: bool = False,
) -> str:
    """Layer 3: 共享工作上下文（本次分析，~1500 tokens）。

    所有专家共享，包含 RAG 检索结果、持仓摘要、预取估值数据。

    Phase C: rag_low_quality=True 时在 RAG 段前加显眼警示标签，
    程序化提示专家优先依赖工具数据（而非只在末尾追加文本）。
    """
    parts = []

    # RAG 检索结果（压缩）
    if rag_context:
        try:
            from agent.memory import compress_rag_token_aware
            rag_compressed = compress_rag_token_aware(rag_context, max_tokens=800)
        except ImportError:
            rag_compressed = rag_context[:1500]
        if rag_compressed:
            # Phase C: 低质命中时加显眼警示标签（前置，而非末尾追加）
            if rag_low_quality:
                parts.append(
                    "## 知识库检索结果\n"
                    "⚠️ 知识库命中质量低（最高分 < 0.05），以下内容仅供参考，"
                    "请以工具数据（持仓/行情/估值工具）为准\n\n"
                    f"{rag_compressed}"
                )
            else:
                parts.append(f"## 知识库检索结果\n{rag_compressed}")

    # 持仓摘要
    if portfolio_summary:
        parts.append(f"## 当前持仓\n{portfolio_summary}")

    # 预取估值数据
    if valuation_data:
        val_lines = []
        for target, data in valuation_data.items():
            if isinstance(data, dict):
                pe = data.get("pe") or data.get("PE")
                pb = data.get("pb") or data.get("PB")
                pct = data.get("percentile") or data.get("分位")
                line = f"- {target}"
                if pe:
                    line += f" PE={pe}"
                if pb:
                    line += f" PB={pb}"
                if pct:
                    line += f" 分位={pct}"
                val_lines.append(line)
        if val_lines:
            parts.append("## 预取估值数据\n" + "\n".join(val_lines))

    # 市场背景
    if market_context:
        parts.append(f"## 市场背景\n{market_context}")

    result = "\n\n".join(parts)

    # 总长度控制
    if len(result) > max_tokens * 3:  # 粗略估算 1 token ≈ 3 字符
        result = result[:max_tokens * 3] + "\n...(共享上下文已截断)"
    return result


def build_layer4_private(
    agent: dict,
    tool_results: list = None,
    max_tokens: int = 2500,
) -> str:
    """Layer 4: 专家私有上下文（每个专家独立）。

    包含该专家调用的工具结果。不跨专家共享。

    Args:
        agent: 专家配置
        tool_results: 该专家的工具调用结果列表 [{"name": "...", "result": "..."}]
    """
    if not tool_results:
        return ""

    parts = ["## 你的工具调用结果（私有，其他专家看不到）"]
    used = len(parts[0])
    max_chars = max_tokens * 3  # 粗略估算

    for tr in tool_results[-10:]:  # 最多保留最近 10 个工具结果
        name = tr.get("name", "unknown")
        result = tr.get("result", "") or tr.get("result_preview", "")
        if not result:
            continue
        line = f"\n### {name}\n{result}"
        if used + len(line) > max_chars:
            # 截断当前结果
            remaining = max_chars - used
            if remaining > 100:
                line = line[:remaining] + "..."
            else:
                break
        parts.append(line)
        used += len(line)

    return "\n".join(parts) if len(parts) > 1 else ""


def build_layer5_blackboard(
    blackboard,
    exclude_agent: Optional[str] = None,
    max_chars: int = 800,
) -> str:
    """Layer 5: 黑板共享（专家间，动态增长）。

    Args:
        blackboard: Blackboard 实例
        exclude_agent: 排除的 agent_key（避免专家看到自己的结论）
        max_chars: 最大字符数
    """
    if blackboard is None:
        return ""
    try:
        return blackboard.to_context_text(max_chars=max_chars, exclude_agent=exclude_agent)
    except Exception as e:
        logger.debug(f"[context_builder] Layer 5 黑板构建失败: {e}")
        return ""


# ── 主入口：组装专家上下文 ──────────────────────

def build_specialist_context(
    agent: dict,
    history: list,
    conversation_id: int,
    user_id: str = "default",
    rag_context: str = "",
    portfolio_summary: str = "",
    valuation_data: Optional[dict] = None,
    market_context: str = "",
    blackboard=None,
    tool_results: list = None,
    complexity: str = "medium",
    max_total_tokens: int = 8000,
    rag_low_quality: bool = False,
) -> str:
    """组装专家可见的完整上下文（Layer 0+1+2+3+5+4）。

    Args:
        agent: 专家配置
        history: 对话历史
        conversation_id: 对话 ID
        user_id: 用户 ID
        rag_context: RAG 检索结果
        portfolio_summary: 持仓摘要
        valuation_data: 预取的估值数据 {target: data}
        market_context: 市场背景
        blackboard: 共享黑板实例
        tool_results: 该专家的工具调用结果（私有）
        complexity: 复杂度
        max_total_tokens: 总 token 预算

    Returns:
        组装好的上下文文本
    """
    parts = []

    # Layer 0: System（~500 tokens）
    layer0 = build_layer0_system(agent)
    if layer0:
        parts.append(layer0)

    # Layer 1: 用户画像（~200 tokens）
    layer1 = build_layer1_user_profile(user_id)
    if layer1:
        parts.append(layer1)

    # Layer 2: 对话历史（动态，按复杂度分配预算）
    history_budget = {
        "simple": 1500,
        "medium": 2500,
        "complex": 3500,
    }.get(complexity, 2500)
    layer2 = build_layer2_conversation_history(
        history, conversation_id, complexity, max_tokens=history_budget
    )
    if layer2:
        parts.append(f"## 对话历史\n{layer2}")

    # Layer 3: 共享工作上下文（~1500 tokens）
    layer3 = build_layer3_shared_work(
        rag_context=rag_context,
        portfolio_summary=portfolio_summary,
        valuation_data=valuation_data,
        market_context=market_context,
        max_tokens=1500,
        rag_low_quality=rag_low_quality,
    )
    if layer3:
        parts.append(layer3)

    # Layer 5: 黑板（专家间，≤800 字）
    # 注意：Layer 5 在 Layer 4 之前，因为黑板是其他专家的结论，比自己的工具结果更重要
    layer5 = build_layer5_blackboard(
        blackboard, exclude_agent=agent.get("agent_key"), max_chars=800
    )
    if layer5:
        parts.append(layer5)

    # Layer 4: 专家私有上下文（~2500 tokens）
    layer4 = build_layer4_private(agent, tool_results, max_tokens=2500)
    if layer4:
        parts.append(layer4)

    result = "\n\n---\n\n".join(parts)

    # 总长度控制（粗略估算）
    max_chars = max_total_tokens * 3
    if len(result) > max_chars:
        result = result[:max_chars] + "\n...(上下文已截断)"
        logger.warning(
            f"[context_builder] 上下文超长，已截断: {len(result)}/{max_chars}"
        )

    return result


# ── 快速路径上下文（简单闲聊） ──────────────────

def build_simple_chat_context(query: str) -> str:
    """简单闲聊的上下文（极简，~200 tokens）。"""
    return (
        "你是一个友好的投资助手。简短回答用户的问候或感谢，不超过2句话。"
        "如果用户询问能力，简要介绍你可以帮助进行投资分析、估值查询、持仓管理。"
    )


# ── 上下文审计 ──────────────────────────────

def audit_context_layers(
    agent: dict,
    history: list,
    conversation_id: int,
    rag_context: str = "",
    portfolio_summary: str = "",
    blackboard=None,
    tool_results: list = None,
    complexity: str = "medium",
) -> dict:
    """审计各层上下文的 token 估算（用于调试和优化）。

    返回各层 token 估算值，便于发现哪层占用过多。
    """
    try:
        from agent.memory import estimate_tokens
    except ImportError:
        estimate_tokens = lambda x: len(x) // 3

    audit = {
        "layer0_system": estimate_tokens(build_layer0_system(agent)),
        "layer1_profile": estimate_tokens(build_layer1_user_profile()),
        "layer2_history": estimate_tokens(
            build_layer2_conversation_history(history, conversation_id, complexity)
        ),
        "layer3_shared": estimate_tokens(
            build_layer3_shared_work(rag_context, portfolio_summary)
        ),
        "layer5_blackboard": estimate_tokens(
            build_layer5_blackboard(blackboard) if blackboard else ""
        ),
        "layer4_private": estimate_tokens(
            build_layer4_private(agent, tool_results) if tool_results else 0
        ),
    }
    audit["total"] = sum(audit.values())
    return audit
