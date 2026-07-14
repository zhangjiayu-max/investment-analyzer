"""三层记忆治理 — 会话 → 项目 → 空间

借鉴 ContextGo 的三层治理模型：
- session_steward: 会话级，管理当前对话的工作记忆
- project_curator: 项目级，将会话洞察提升为项目知识
- space_curator: 空间级，跨项目记忆蒸馏

我们的映射：
- session_steward: 当前对话的上下文管理（已有 compress_history_semantic）
- project_curator: 用户画像 + 分析知识沉淀（新增）
- space_curator: 跨用户的行业知识 + 最佳实践（新增）
"""

import json
import logging
from datetime import datetime, timedelta

from db._conn import _get_conn

logger = logging.getLogger(__name__)


# ── 会话级治理（session_steward）──────────────────────────


class SessionSteward:
    """会话级上下文管理。

    职责：
    - 管理当前对话的工作记忆
    - 提取记忆候选
    - 检测会话信号
    """

    @staticmethod
    def extract_and_save_candidates(conversation_id: int, user_id: str = "default"):
        """从对话中提取记忆候选并保存。"""
        from agent.memory.memory_lifecycle import extract_memory_candidates, save_memory, should_promote

        candidates = extract_memory_candidates(conversation_id, user_id)

        promoted_count = 0
        for candidate in candidates:
            if should_promote(candidate):
                save_memory(
                    user_id=user_id,
                    memory_type=candidate["type"],
                    content=candidate["content"],
                    source=candidate.get("source", ""),
                    evidence_count=candidate.get("evidence_count", 1),
                    confidence=0.6,
                )
                promoted_count += 1

        if promoted_count > 0:
            logger.info(f"会话 {conversation_id}: 提取 {len(candidates)} 个候选，晋升 {promoted_count} 个")

        return promoted_count

    @staticmethod
    def get_working_context(conversation_id: int, max_tokens: int = 3000) -> str:
        """获取当前会话的工作上下文。"""
        from agent.memory.memory import compress_history_semantic, estimate_tokens
        from db import get_messages

        messages = get_messages(conversation_id)
        if not messages:
            return ""

        # 压缩历史
        compressed = compress_history_semantic(messages, max_tokens)

        # 格式化
        parts = []
        for msg in compressed:
            role = msg.get("role", "")
            content = msg.get("content", "")[:300]
            if role == "system":
                parts.append(f"[系统] {content}")
            elif role == "user":
                parts.append(f"[用户] {content}")
            elif role == "assistant":
                parts.append(f"[助手] {content}")

        return "\n".join(parts)


# ── 项目级治理（project_curator）──────────────────────────


class ProjectCurator:
    """项目级知识管理。

    职责：
    - 将会话洞察提升为项目知识
    - 管理用户画像
    - 沉淀分析最佳实践
    """

    @staticmethod
    def promote_session_insights(user_id: str = "default"):
        """将会话级洞察提升为项目级知识。"""
        from agent.memory.memory_lifecycle import get_memories, save_memory, compact_memories

        # 1. 获取所有未压缩的记忆
        memories = get_memories(user_id, limit=50)

        # 2. 按类型分组
        by_type = {}
        for m in memories:
            mt = m.get("memory_type", "")
            if "_profile" in mt:  # 已经是 profile 级别
                continue
            if mt not in by_type:
                by_type[mt] = []
            by_type[mt].append(m)

        # 3. 对每种类型进行压缩
        promoted_count = 0
        for mt, mems in by_type.items():
            if len(mems) >= 3:
                # 压缩为 profile
                compacted = compact_memories(user_id, topic=None)
                promoted_count += compacted

        logger.info(f"项目级提升: user_id={user_id}, 压缩={promoted_count} 条")
        return promoted_count

    @staticmethod
    def get_project_context(user_id: str = "default") -> str:
        """获取项目级上下文（用户画像 + 知识沉淀）。"""
        parts = []

        # 1. 用户偏好画像
        try:
            from agent.memory.feedback_learner import get_preference_context
            pref_ctx = get_preference_context(user_id)
            if pref_ctx:
                parts.append(pref_ctx)
        except Exception:
            pass

        # 2. 项目级记忆（profile 类型）
        try:
            from agent.memory.memory_lifecycle import get_memories
            profiles = get_memories(user_id, memory_type="fact_profile", limit=5)
            if profiles:
                profile_text = "\n".join(f"- {p['content'][:150]}" for p in profiles)
                parts.append(f"<project_knowledge>\n{profile_text}\n</project_knowledge>")
        except Exception:
            pass

        return "\n".join(parts) if parts else ""


# ── 空间级治理（space_curator）────────────────────────────


class SpaceCurator:
    """空间级知识蒸馏。

    职责：
    - 跨用户的行业知识沉淀
    - 最佳实践提取
    - 系统级知识管理
    """

    @staticmethod
    def distill_industry_knowledge():
        """蒸馏行业知识（从所有用户的分析结果中提取）。"""
        conn = _get_conn()
        try:
            # 获取最近的分析结果
            rows = conn.execute(
                """SELECT result_data, agent_name, created_at
                   FROM portfolio_analysis_records
                   WHERE created_at >= datetime('now', '-7 days')
                   ORDER BY created_at DESC
                   LIMIT 50"""
            ).fetchall()

            if not rows:
                return 0

            # 提取行业关键词和结论
            industry_insights = {}
            for r in rows:
                r = dict(r)
                result = r.get("result_data", "")
                agent = r.get("agent_name", "")

                # 简单关键词提取
                sectors = ["半导体", "新能源", "消费", "医药", "金融", "科技", "军工", "白酒"]
                for sector in sectors:
                    if sector in result:
                        if sector not in industry_insights:
                            industry_insights[sector] = []
                        industry_insights[sector].append({
                            "source": agent,
                            "snippet": result[:200],
                            "date": r.get("created_at", ""),
                        })

            # 保存到系统级知识库
            from agent.memory.memory_lifecycle import save_memory
            saved_count = 0
            for sector, insights in industry_insights.items():
                if len(insights) >= 2:
                    # 合并多条洞察
                    combined = "；".join(set(i["snippet"][:100] for i in insights[:3]))
                    save_memory(
                        user_id="system",
                        memory_type="industry_knowledge",
                        content=f"{sector}: {combined}",
                        source="distillation",
                        evidence_count=len(insights),
                        confidence=0.8,
                    )
                    saved_count += 1

            logger.info(f"空间级蒸馏: 保存 {saved_count} 条行业知识")
            return saved_count

        except Exception as e:
            logger.error(f"空间级蒸馏失败: {e}")
            return 0
        finally:
            conn.close()

    @staticmethod
    def get_space_context() -> str:
        """获取空间级上下文（系统级知识）。"""
        try:
            from agent.memory.memory_lifecycle import get_memories
            knowledge = get_memories("system", memory_type="industry_knowledge", limit=5)
            if knowledge:
                knowledge_text = "\n".join(f"- {k['content'][:150]}" for k in knowledge)
                return f"<industry_knowledge>\n{knowledge_text}\n</industry_knowledge>"
        except Exception:
            pass
        return ""


# ── 统一上下文组装 ──────────────────────────────────────


def assemble_full_context(conversation_id: int, user_id: str = "default",
                          complexity: str = "medium") -> str:
    """组装完整的三层上下文。

    根据复杂度分配 token 预算：
    - simple: 主要用会话级上下文
    - medium: 会话级 + 项目级
    - complex: 会话级 + 项目级 + 空间级
    """
    from agent.memory.memory import get_token_budget

    budget = get_token_budget(complexity)
    parts = []

    # 1. 会话级上下文（总是包含）
    session_ctx = SessionSteward.get_working_context(
        conversation_id,
        max_tokens=int(budget["total_context"] * budget["history_pct"])
    )
    if session_ctx:
        parts.append(f"<session_context>\n{session_ctx}\n</session_context>")

    # 2. 项目级上下文（medium 及以上）
    if complexity in ("medium", "complex"):
        project_ctx = ProjectCurator.get_project_context(user_id)
        if project_ctx:
            parts.append(project_ctx)

    # 3. 空间级上下文（complex）
    if complexity == "complex":
        space_ctx = SpaceCurator.get_space_context()
        if space_ctx:
            parts.append(space_ctx)

    return "\n\n".join(parts) if parts else ""


# ── 定期维护任务 ──────────────────────────────────────


def run_governance_maintenance():
    """运行三层治理的维护任务（建议每天执行一次）。"""
    logger.info("开始三层治理维护...")

    # 1. 项目级提升
    ProjectCurator.promote_session_insights()

    # 2. 空间级蒸馏
    SpaceCurator.distill_industry_knowledge()

    # 3. 记忆维护（压缩 + 遗忘）
    from agent.memory.memory_lifecycle import run_memory_maintenance
    run_memory_maintenance()

    logger.info("三层治理维护完成")
