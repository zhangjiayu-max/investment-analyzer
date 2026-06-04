"""记忆生命周期管理 — 候选评分 → 晋升 → 压缩 → 遗忘

借鉴 ContextGo 的三层记忆治理模型：
- working: 当前会话的工作记忆
- experiential: 跨会话的经验记忆（从反馈中学习）
- factual: 事实性知识（从分析结果中提取）
- source: 原始数据源

记忆生命周期：
1. Candidate: 从对话/反馈中提取候选记忆
2. Promotion: 评分决定是否晋升为持久记忆
3. Compaction: 多条相关记忆合并为 ProfileSegment
4. Forgetting: 老化 → 降级 → 归档 → 删除
"""

import json
import logging
import time
from datetime import datetime, timedelta

from db._conn import _get_conn

logger = logging.getLogger(__name__)


# ── 记忆候选评分 ──────────────────────────────────────────


def score_memory_candidate(candidate: dict) -> float:
    """评分记忆候选，决定是否晋升为持久记忆。

    评分维度（0-100）：
    - 证据数（出现次数）：30分
    - 跨源重复（多个来源确认）：25分
    - 用户确认（点赞/明确表达）：20分
    - 执行验证（工具调用成功）：15分
    - 矛盾检测（与其他记忆冲突）：-20分
    """
    score = 0.0

    # 1. 证据数（出现次数）
    evidence_count = candidate.get("evidence_count", 1)
    if evidence_count >= 5:
        score += 30
    elif evidence_count >= 3:
        score += 22
    elif evidence_count >= 2:
        score += 15
    else:
        score += 8

    # 2. 跨源重复
    source_count = candidate.get("source_count", 1)
    if source_count >= 3:
        score += 25
    elif source_count >= 2:
        score += 18
    else:
        score += 5

    # 3. 用户确认
    user_confirmed = candidate.get("user_confirmed", False)
    feedback_type = candidate.get("feedback_type", "")
    if user_confirmed or feedback_type == "helpful":
        score += 20
    elif feedback_type == "unhelpful":
        score -= 10

    # 4. 执行验证
    execution_verified = candidate.get("execution_verified", False)
    if execution_verified:
        score += 15

    # 5. 矛盾检测
    contradiction_count = candidate.get("contradiction_count", 0)
    if contradiction_count > 0:
        score -= min(20, contradiction_count * 10)

    return max(0, min(100, score))


def should_promote(candidate: dict, threshold: float = 60.0) -> bool:
    """判断记忆候选是否应该晋升。"""
    score = score_memory_candidate(candidate)
    return score >= threshold


# ── 记忆存储 ──────────────────────────────────────────


def save_memory(user_id: str, memory_type: str, content: str,
                source: str = "", evidence_count: int = 1,
                confidence: float = 0.5, metadata: dict = None) -> int:
    """保存一条记忆到数据库。

    参数:
        user_id: 用户ID
        memory_type: 记忆类型 (preference|pattern|fact|feedback)
        content: 记忆内容
        source: 来源（feedback|analysis|conversation）
        evidence_count: 证据数
        confidence: 置信度 (0-1)
        metadata: 附加元数据
    """
    conn = _get_conn()
    try:
        # 检查是否已有相同内容的记忆
        existing = conn.execute(
            "SELECT id, evidence_count, confidence FROM user_memories WHERE user_id = ? AND content = ?",
            (user_id, content)
        ).fetchone()

        if existing:
            # 更新证据数和置信度
            new_evidence = existing["evidence_count"] + evidence_count
            new_confidence = min(1.0, existing["confidence"] + 0.1)
            conn.execute(
                "UPDATE user_memories SET evidence_count = ?, confidence = ?, last_accessed = datetime('now','localtime') WHERE id = ?",
                (new_evidence, new_confidence, existing["id"])
            )
            conn.commit()
            return existing["id"]
        else:
            # 插入新记忆
            cur = conn.execute(
                """INSERT INTO user_memories
                   (user_id, memory_type, content, source, evidence_count, confidence, metadata, created_at, last_accessed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))""",
                (user_id, memory_type, content, source, evidence_count, confidence,
                 json.dumps(metadata or {}, ensure_ascii=False))
            )
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        logger.error(f"保存记忆失败: {e}")
        return -1
    finally:
        conn.close()


def get_memories(user_id: str, memory_type: str = None, limit: int = 20) -> list:
    """获取用户记忆。"""
    conn = _get_conn()
    try:
        if memory_type:
            rows = conn.execute(
                "SELECT * FROM user_memories WHERE user_id = ? AND memory_type = ? ORDER BY confidence DESC, last_accessed DESC LIMIT ?",
                (user_id, memory_type, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_memories WHERE user_id = ? ORDER BY confidence DESC, last_accessed DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"获取记忆失败: {e}")
        return []
    finally:
        conn.close()


# ── 记忆压缩（合并为 ProfileSegment）──────────────────


def compact_memories(user_id: str, topic: str = None) -> int:
    """将多条相关记忆压缩为一条 ProfileSegment。

    压缩策略：
    - 同一主题的多条记忆合并为一条
    - 保留最高置信度的内容
    - 合并证据数
    - 标记原始记忆为已压缩
    """
    conn = _get_conn()
    try:
        # 找到可以压缩的记忆组
        if topic:
            rows = conn.execute(
                """SELECT * FROM user_memories
                   WHERE user_id = ? AND content LIKE ? AND is_compacted = 0
                   ORDER BY confidence DESC""",
                (user_id, f"%{topic}%")
            ).fetchall()
        else:
            # 按 memory_type 分组压缩
            rows = conn.execute(
                """SELECT * FROM user_memories
                   WHERE user_id = ? AND is_compacted = 0
                   ORDER BY memory_type, confidence DESC""",
                (user_id,)
            ).fetchall()

        if len(rows) < 2:
            return 0

        # 按类型分组
        groups = {}
        for r in rows:
            r = dict(r)
            mt = r.get("memory_type", "unknown")
            if mt not in groups:
                groups[mt] = []
            groups[mt].append(r)

        compacted_count = 0
        for mt, memories in groups.items():
            if len(memories) < 2:
                continue

            # 合并策略：保留最高置信度的内容，合并证据数
            best = memories[0]
            total_evidence = sum(m.get("evidence_count", 1) for m in memories)
            combined_content = best["content"]

            # 如果有多条不同内容，合并摘要
            if len(set(m["content"] for m in memories)) > 1:
                contents = list(set(m["content"] for m in memories))[:5]
                combined_content = "；".join(contents)

            # 创建压缩后的记忆
            save_memory(
                user_id=user_id,
                memory_type=f"{mt}_profile",
                content=combined_content,
                source="compaction",
                evidence_count=total_evidence,
                confidence=min(1.0, best.get("confidence", 0.5) + 0.1),
                metadata={"compacted_from": [m["id"] for m in memories]}
            )

            # 标记原始记忆为已压缩
            for m in memories:
                conn.execute(
                    "UPDATE user_memories SET is_compacted = 1 WHERE id = ?",
                    (m["id"],)
                )
            compacted_count += len(memories)

        conn.commit()
        return compacted_count
    except Exception as e:
        logger.error(f"压缩记忆失败: {e}")
        return 0
    finally:
        conn.close()


# ── 记忆遗忘（衰减机制）──────────────────────────────


def forget_stale_memories(user_id: str, days_threshold: int = 30,
                          min_confidence: float = 0.3) -> int:
    """遗忘过时的记忆。

    遗忘策略（四阶段）：
    1. Retain: 最近访问且高置信度 → 保留
    2. Deprioritize: 较久未访问 → 降低优先级
    3. Archive: 超过阈值且低置信度 → 归档
    4. Delete: 归档超过 90 天 → 删除

    参数:
        user_id: 用户ID
        days_threshold: 多少天未访问算过时
        min_confidence: 最低置信度阈值
    """
    conn = _get_conn()
    try:
        cutoff_date = (datetime.now() - timedelta(days=days_threshold)).strftime("%Y-%m-%d %H:%M:%S")
        archive_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. 找到需要遗忘的记忆
        stale = conn.execute(
            """SELECT id, confidence, last_accessed, is_pinned
               FROM user_memories
               WHERE user_id = ? AND last_accessed < ? AND is_pinned = 0 AND is_compacted = 0""",
            (user_id, cutoff_date)
        ).fetchall()

        forgotten_count = 0
        for row in stale:
            row = dict(row)
            mem_id = row["id"]
            confidence = row.get("confidence", 0.5)

            if confidence < min_confidence:
                # 低置信度 → 归档
                conn.execute(
                    "UPDATE user_memories SET is_archived = 1 WHERE id = ?",
                    (mem_id,)
                )
                forgotten_count += 1
            else:
                # 降低置信度
                new_confidence = max(0.1, confidence - 0.1)
                conn.execute(
                    "UPDATE user_memories SET confidence = ? WHERE id = ?",
                    (new_confidence, mem_id)
                )

        # 2. 删除归档超过 90 天的记忆
        deleted = conn.execute(
            "DELETE FROM user_memories WHERE user_id = ? AND is_archived = 1 AND last_accessed < ?",
            (user_id, archive_date)
        ).rowcount

        conn.commit()
        logger.info(f"遗忘记忆: user_id={user_id}, 归档={forgotten_count}, 删除={deleted}")
        return forgotten_count + deleted
    except Exception as e:
        logger.error(f"遗忘记忆失败: {e}")
        return 0
    finally:
        conn.close()


# ── 从对话中提取记忆候选 ───────────────────────────────


def extract_memory_candidates(conversation_id: int, user_id: str = "default") -> list:
    """从对话中提取记忆候选。

    提取策略：
    - 用户表达的偏好（"我喜欢..."、"我不喜欢..."）
    - 分析结论（估值判断、操作建议）
    - 反馈信号（点赞/点踩的具体内容）
    """
    from db import get_messages

    messages = get_messages(conversation_id)
    candidates = []

    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "")

        # 用户表达的偏好
        if role == "user":
            preference_keywords = ["喜欢", "不喜欢", "偏好", "希望", "不要", "需要"]
            for kw in preference_keywords:
                if kw in content:
                    candidates.append({
                        "type": "preference",
                        "content": content[:200],
                        "source": "conversation",
                        "evidence_count": 1,
                        "source_count": 1,
                        "user_confirmed": True,
                    })
                    break

        # 分析结论（assistant 的分析结果）
        if role == "assistant" and len(content) > 100:
            # 提取关键结论
            if any(kw in content for kw in ["建议", "结论", "判断", "低估", "高估"]):
                candidates.append({
                    "type": "fact",
                    "content": content[:300],
                    "source": "analysis",
                    "evidence_count": 1,
                    "source_count": 1,
                    "execution_verified": True,
                })

    return candidates


# ── 初始化记忆表 ──────────────────────────────────────


def init_memory_tables(conn=None):
    """初始化记忆相关的数据库表。"""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT DEFAULT '',
            evidence_count INTEGER DEFAULT 1,
            confidence REAL DEFAULT 0.5,
            is_pinned INTEGER DEFAULT 0,
            is_compacted INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            last_accessed TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_memories_type ON user_memories(memory_type)")

    if own_conn:
        conn.commit()
        conn.close()


# ── 定期维护 ──────────────────────────────────────────


def run_memory_maintenance(user_id: str = "default"):
    """运行记忆维护任务（建议每天执行一次）。

    1. 压缩同主题记忆
    2. 遗忘过时记忆
    """
    logger.info(f"开始记忆维护: user_id={user_id}")

    # 1. 压缩
    compacted = compact_memories(user_id)
    logger.info(f"压缩记忆: {compacted} 条")

    # 2. 遗忘
    forgotten = forget_stale_memories(user_id)
    logger.info(f"遗忘记忆: {forgotten} 条")

    return {"compacted": compacted, "forgotten": forgotten}
