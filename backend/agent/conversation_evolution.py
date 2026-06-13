"""对话评估与进化系统集成 — 自动触发反馈学习、Bad Case 标记、专家调优"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 阈值配置
LOW_SCORE_THRESHOLD = 60   # 低于60分触发负面反馈
HIGH_SCORE_THRESHOLD = 85  # 高于85分建议转为 Eval 基准


async def process_conversation_evaluation(conversation_id: int, evaluation: dict):
    """
    处理对话评估结果，触发进化机制。

    自动执行：
    1. 低分对话 → 反馈学习（更新用户画像）
    2. 低分对话 → 自动标记 Bad Case
    3. 高分对话 → 建议转化为 Eval 基准
    4. 专家表现分析 → 识别需要调优的专家
    """
    auto_score = evaluation.get("auto_score", 0)
    breakdown = evaluation.get("auto_score_breakdown", {})
    suggestions = evaluation.get("suggestions", [])

    results = {
        "feedback_triggered": False,
        "bad_case_marked": False,
        "eval_suggested": False,
        "expert_alerts": [],
    }

    # 1. 低分对话 → 反馈学习
    if auto_score < LOW_SCORE_THRESHOLD:
        try:
            await _trigger_feedback_learning(conversation_id, evaluation)
            results["feedback_triggered"] = True
        except Exception as e:
            logger.error(f"触发反馈学习失败: {e}")

    # 2. 低分对话 → 自动标记 Bad Case
    if auto_score < LOW_SCORE_THRESHOLD:
        try:
            await _auto_mark_bad_case(conversation_id, evaluation)
            results["bad_case_marked"] = True
        except Exception as e:
            logger.error(f"标记 Bad Case 失败: {e}")

    # 3. 高分对话 → 建议转化为 Eval 基准
    if auto_score >= HIGH_SCORE_THRESHOLD:
        try:
            await _suggest_as_eval_case(conversation_id, evaluation)
            results["eval_suggested"] = True
        except Exception as e:
            logger.error(f"生成 Eval 建议失败: {e}")

    # 4. 专家表现分析
    try:
        alerts = await _analyze_expert_performance(conversation_id, evaluation)
        results["expert_alerts"] = alerts
    except Exception as e:
        logger.error(f"分析专家表现失败: {e}")

    logger.info(
        f"对话 {conversation_id} 进化处理完成: "
        f"分数={auto_score:.0f}, "
        f"反馈={results['feedback_triggered']}, "
        f"BadCase={results['bad_case_marked']}, "
        f"Eval建议={results['eval_suggested']}, "
        f"专家告警={len(results['expert_alerts'])}"
    )

    return results


async def _trigger_feedback_learning(conversation_id: int, evaluation: dict):
    """低分对话触发反馈学习，更新用户画像"""
    from agent.feedback_learner import update_user_profile_from_feedback
    from db.conversations import get_messages

    messages = get_messages(conversation_id)
    if not messages:
        return

    # 提取用户问题
    user_query = ""
    for msg in messages:
        if msg["role"] == "user":
            user_query = msg["content"][:200]
            break

    # 构建反馈信号
    breakdown = evaluation.get("auto_score_breakdown", {})
    weak_dimensions = [k for k, v in breakdown.items() if v < 60]

    dim_names = {
        "execution": "执行效率",
        "data": "数据利用",
        "collaboration": "专家协作",
        "response": "响应质量",
    }

    note_parts = [f"对话质量评分: {evaluation['auto_score']:.0f}/100"]
    if weak_dimensions:
        note_parts.append(
            f"薄弱维度: {', '.join(dim_names.get(d, d) for d in weak_dimensions)}"
        )

    # 添加具体的优化建议
    optimization_hints = _generate_optimization_hints(breakdown, weak_dimensions)
    if optimization_hints:
        note_parts.append(f"优化方向: {'; '.join(optimization_hints[:2])}")

    note = "。".join(note_parts) + "。"

    # 触发反馈学习
    update_user_profile_from_feedback(
        user_id="default",
        feedback_type="unhelpful",
        note=note,
        input_summary=user_query,
    )

    logger.info(f"对话 {conversation_id} 低分触发反馈学习: {evaluation['auto_score']:.0f}分")


async def _auto_mark_bad_case(conversation_id: int, evaluation: dict):
    """低分对话自动标记为 Bad Case"""
    from db import save_llm_feedback
    from db.conversations import get_messages

    breakdown = evaluation.get("auto_score_breakdown", {})

    # 计算三维度分数（映射到 1-10 分制）
    data_score = min(10, max(1, breakdown.get("data", 50) / 10))
    logic_score = min(10, max(1, breakdown.get("collaboration", 50) / 10))
    action_score = min(10, max(1, breakdown.get("response", 50) / 10))
    overall_score = min(10, max(1, evaluation.get("auto_score", 50) / 10))

    # 提取输入摘要
    messages = get_messages(conversation_id)
    input_summary = ""
    for msg in messages:
        if msg["role"] == "user":
            input_summary = msg["content"][:200]
            break

    # 构建标签
    suggestions = evaluation.get("suggestions", [])
    tags = ",".join(suggestions[:3]) if suggestions else "自动标记"

    # 保存到 llm_feedback 表
    save_llm_feedback(
        caller="conversation_eval",
        input_summary=input_summary,
        output_summary=f"对话质量评分: {evaluation['auto_score']:.0f}/100",
        rating="unhelpful",
        tags=tags,
        comment=f"自动标记: 低分对话 ({evaluation['auto_score']:.0f}分)",
        score_data_accuracy=data_score,
        score_logic=logic_score,
        score_actionability=action_score,
        overall_score=overall_score,
        target_type="conversation",
        target_id=conversation_id,
    )

    logger.info(f"对话 {conversation_id} 自动标记为 Bad Case")


async def _suggest_as_eval_case(conversation_id: int, evaluation: dict):
    """高分对话建议转化为 Eval 用例"""
    from db.conversations import get_messages, get_conversation
    from db._conn import _get_conn

    conv = get_conversation(conversation_id)
    messages = get_messages(conversation_id)

    # 提取用户问题
    user_query = ""
    for msg in messages:
        if msg["role"] == "user":
            user_query = msg["content"]
            break

    if not user_query:
        return

    # 生成期望质量标准
    expected_quality = _generate_expected_quality(evaluation)

    # 保存建议到数据库
    conn = _get_conn()
    conn.execute("""
        INSERT INTO eval_suggestions (
            conversation_id, name, analysis_type, input_params,
            expected_quality, auto_score, status
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (
        conversation_id,
        f"高质量对话-{conv.get('title', '未命名')[:20]}",
        "orchestrator",
        json.dumps({"question": user_query}, ensure_ascii=False),
        expected_quality,
        evaluation.get("auto_score", 0),
    ))
    conn.commit()
    conn.close()

    logger.info(f"对话 {conversation_id} 建议转化为 Eval 用例: {evaluation['auto_score']:.0f}分")


def _generate_expected_quality(evaluation: dict) -> str:
    """从评估结果生成期望质量标准"""
    breakdown = evaluation.get("auto_score_breakdown", {})
    parts = []

    if breakdown.get("data", 0) >= 80:
        parts.append("引用具体数据和估值指标")
    if breakdown.get("collaboration", 0) >= 80:
        parts.append("多专家协作分析，有交叉审阅或仲裁")
    if breakdown.get("response", 0) >= 80:
        parts.append("结构清晰、有风险提示、可操作性强")

    return "；".join(parts) if parts else "专业、准确、可操作的投资分析"


def _generate_optimization_hints(breakdown: dict, weak_dimensions: list) -> list:
    """根据评估维度生成具体的优化建议"""
    hints = []

    # 执行效率优化建议
    if "execution" in weak_dimensions:
        execution_score = breakdown.get("execution", 0)
        if execution_score < 40:
            hints.append("减少重复的专家调用，优化编排逻辑")
        elif execution_score < 60:
            hints.append("优化执行耗时，考虑减少专家数量或简化分析")

    # 数据利用优化建议
    if "data" in weak_dimensions:
        data_score = breakdown.get("data", 0)
        if data_score < 40:
            hints.append("增加知识库引用，提升分析的数据支撑")
        elif data_score < 60:
            hints.append("结合用户持仓数据进行个性化分析")

    # 专家协作优化建议
    if "collaboration" in weak_dimensions:
        collab_score = breakdown.get("collaboration", 0)
        if collab_score < 40:
            hints.append("对于复杂任务，触发交叉审阅以验证分析结果")
        elif collab_score < 60:
            hints.append("增加专家覆盖度，调用更多相关专家")

    # 响应质量优化建议
    if "response" in weak_dimensions:
        response_score = breakdown.get("response", 0)
        if response_score < 40:
            hints.append("添加风险提示，提升建议的完整性")
        elif response_score < 60:
            hints.append("提供更具体、可操作的建议")

    return hints


async def _analyze_expert_performance(conversation_id: int, evaluation: dict) -> list:
    """分析专家表现，识别需要调优的专家"""
    from db.agents import get_agent_runs
    from db._conn import _get_conn

    runs = get_agent_runs(conversation_id)
    if not runs:
        return []

    # 统计每个专家的表现
    expert_stats = {}
    for run in runs:
        agent_key = run.get("agent_key", "")
        if not agent_key:
            continue

        if agent_key not in expert_stats:
            expert_stats[agent_key] = {
                "name": run.get("agent_name", ""),
                "total": 0,
                "success": 0,
                "total_duration": 0,
            }

        expert_stats[agent_key]["total"] += 1
        if run.get("status") in ("completed", "success"):
            expert_stats[agent_key]["success"] += 1
        expert_stats[agent_key]["total_duration"] += run.get("duration_ms", 0)

    # 识别表现差的专家
    alerts = []
    for agent_key, stats in expert_stats.items():
        if stats["total"] == 0:
            continue

        success_rate = stats["success"] / stats["total"]
        avg_duration = stats["total_duration"] / stats["total"]

        alert_type = None
        if success_rate < 0.8:
            alert_type = "low_success"
        elif avg_duration > 60000:
            alert_type = "slow_response"

        if alert_type:
            alerts.append({
                "agent_key": agent_key,
                "name": stats["name"],
                "success_rate": success_rate,
                "avg_duration_ms": avg_duration,
                "alert_type": alert_type,
            })

    # 保存告警到数据库
    if alerts:
        conn = _get_conn()
        for alert in alerts:
            conn.execute("""
                INSERT INTO expert_performance_alerts (
                    conversation_id, agent_key, agent_name,
                    success_rate, avg_duration_ms, alert_type
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                conversation_id,
                alert["agent_key"],
                alert["name"],
                alert["success_rate"],
                alert["avg_duration_ms"],
                alert["alert_type"],
            ))
        conn.commit()
        conn.close()

    return alerts


def get_evolution_stats(days: int = 30) -> dict:
    """获取进化效果统计"""
    from db._conn import _get_conn

    conn = _get_conn()

    # 统计低分对话数量
    low_score_count = conn.execute("""
        SELECT COUNT(*) as count
        FROM conversation_evaluations
        WHERE auto_score < ? AND created_at >= datetime('now', ?)
    """, (LOW_SCORE_THRESHOLD, f"-{days} days")).fetchone()["count"]

    # 统计自动触发的反馈学习
    feedback_count = conn.execute("""
        SELECT COUNT(*) as count
        FROM llm_feedback
        WHERE caller = 'conversation_eval'
        AND created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["count"]

    # 统计高分建议
    suggestion_count = conn.execute("""
        SELECT COUNT(*) as count
        FROM eval_suggestions
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["count"]

    # 统计专家告警
    alert_count = conn.execute("""
        SELECT COUNT(*) as count
        FROM expert_performance_alerts
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()["count"]

    # 质量趋势（按天）
    trend = conn.execute("""
        SELECT
            DATE(created_at) as date,
            AVG(auto_score) as avg_score,
            COUNT(*) as count
        FROM conversation_evaluations
        WHERE created_at >= datetime('now', ?)
        GROUP BY DATE(created_at)
        ORDER BY date
    """, (f"-{days} days",)).fetchall()

    # 专家表现统计
    expert_stats = conn.execute("""
        SELECT
            agent_key,
            agent_name,
            AVG(success_rate) as avg_success_rate,
            COUNT(*) as alert_count
        FROM expert_performance_alerts
        WHERE created_at >= datetime('now', ?)
        GROUP BY agent_key
        ORDER BY avg_success_rate ASC
    """, (f"-{days} days",)).fetchall()

    conn.close()

    return {
        "low_score_count": low_score_count,
        "feedback_count": feedback_count,
        "suggestion_count": suggestion_count,
        "alert_count": alert_count,
        "trend": [dict(r) for r in trend],
        "expert_stats": [dict(r) for r in expert_stats],
    }


def get_eval_suggestions(status: str = None, limit: int = 50) -> list:
    """获取评估建议"""
    from db._conn import _get_conn

    conn = _get_conn()

    if status:
        rows = conn.execute("""
            SELECT es.*, c.title as conversation_title
            FROM eval_suggestions es
            LEFT JOIN conversations c ON es.conversation_id = c.id
            WHERE es.status = ?
            ORDER BY es.id DESC
            LIMIT ?
        """, (status, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT es.*, c.title as conversation_title
            FROM eval_suggestions es
            LEFT JOIN conversations c ON es.conversation_id = c.id
            ORDER BY es.id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def update_eval_suggestion_status(suggestion_id: int, status: str) -> bool:
    """更新评估建议状态"""
    from db._conn import _get_conn

    conn = _get_conn()
    conn.execute("""
        UPDATE eval_suggestions SET status = ? WHERE id = ?
    """, (status, suggestion_id))
    conn.commit()
    conn.close()
    return True


def get_expert_alerts(days: int = 7, limit: int = 50) -> list:
    """获取专家表现告警"""
    from db._conn import _get_conn

    conn = _get_conn()
    rows = conn.execute("""
        SELECT *
        FROM expert_performance_alerts
        WHERE created_at >= datetime('now', ?)
        ORDER BY created_at DESC
        LIMIT ?
    """, (f"-{days} days", limit)).fetchall()
    conn.close()

    return [dict(r) for r in rows]
