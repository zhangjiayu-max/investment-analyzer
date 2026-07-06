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

    # 4. 低分自动触发根因→修复→Shadow 链路
    if auto_score < LOW_SCORE_THRESHOLD:
        try:
            fix_results = await _auto_root_cause_to_fix(conversation_id, evaluation)
            if fix_results:
                results["auto_fixes"] = fix_results
                logger.info(f"自动修复链路: {len(fix_results)} 个根因已创建 Shadow")
        except Exception as e:
            logger.error(f"自动根因修复链路失败: {e}")

    # 5. 专家表现分析
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


# ═══════════════════════════════════════════════════════════════
# 胶水3: 自动根因→修复→Shadow
# ═══════════════════════════════════════════════════════════════


async def _auto_root_cause_to_fix(conversation_id: int, evaluation: dict):
    """低分 → 根因 → 自动生成 prompt 修复 → 进入 Shadow。"""
    from infra.root_cause_analyzer import batch_analyze
    from db.eval import create_prompt_version, get_active_prompt
    from infra.shadow_mode import create_shadow_config
    from db.portfolio import list_all_bad_cases

    # 1. 收集最近的 Bad Case
    bad_cases = list_all_bad_cases(limit=20)
    if not bad_cases:
        return None

    # 2. 批量根因分析
    try:
        analysis = batch_analyze(limit=20, force=False)
    except Exception as e:
        logger.error(f"批量根因分析失败: {e}")
        return None

    # 3. 找出最频繁的根因（至少出现 3 次）
    freq = {}
    for item in analysis.get("results", []):
        rc = item.get("root_cause", "")
        if rc and rc != "other":
            freq.setdefault(rc, []).append(item)

    top_causes = sorted(freq.items(), key=lambda x: -len(x[1]))
    fixes_applied = []

    for root_cause, results in top_causes:
        if len(results) < 3:
            continue  # 至少 3 个 case 指向同一根因才自动修复

        # 4. 推断受影响的 agent_type
        agent_type = _infer_agent_type_from_bad_cases(results)
        if not agent_type:
            continue

        # 5. 获取当前 active prompt
        active = get_active_prompt(agent_type)
        if not active:
            continue

        # 6. 合并多个 case 的建议，生成改进 prompt
        suggestions = list(set(
            r.get("suggestion", "") or r.get("detail", "")
            for r in results
            if r.get("suggestion") or r.get("detail")
        ))
        try:
            improved_prompt = _generate_improved_prompt(
                active["prompt_content"],
                root_cause,
                suggestions,
                results,
            )
        except Exception as e:
            logger.error(f"生成改进 prompt 失败: {e}")
            continue

        # 7. 创建新 prompt 版本（draft，不激活）
        from datetime import datetime as _dt
        version_id = create_prompt_version(
            agent_type=agent_type,
            version=f"auto-fix-{_dt.now():%Y%m%d%H%M%S}",
            prompt_content=improved_prompt,
            changelog=f"自动修复: {root_cause} ({len(results)}个Bad Case) — "
                      f"{'; '.join(s[:50] for s in suggestions[:3])}",
        )

        # 8. 创建 Shadow config（自动对比新旧版本）
        shadow_id = create_shadow_config(
            name=f"auto-fix: {agent_type} {root_cause}",
            agent_type=agent_type,
            current_prompt=active["prompt_content"],
            candidate_prompt=improved_prompt,
            traffic_pct=0.1,
            prompt_version_id=version_id,
        )

        fixes_applied.append({
            "root_cause": root_cause,
            "case_count": len(results),
            "version_id": version_id,
            "shadow_id": shadow_id,
        })

        logger.info(
            f"自动修复: {agent_type} {root_cause} → "
            f"v{version_id} → shadow#{shadow_id}"
        )

    return fixes_applied


def _generate_improved_prompt(
    current_prompt: str,
    root_cause: str,
    suggestions: list,
    bad_cases: list,
) -> str:
    """让 LLM 基于根因和建议，生成改进版的 prompt。"""
    from services.llm_service import _call_llm, MODEL

    cases_text = "\n\n".join(
        f"Bad Case #{i+1}: {c.get('detail', '') or c.get('summary', '')}\n"
        f"证据: {c.get('root_cause_detail', '') or c.get('note', '')}"
        for i, c in enumerate(bad_cases[:5])
    )

    prompt = f"""你是一个 Prompt 工程专家。以下是当前 Agent prompt 和它的 Bad Cases。
请基于根因分析结果，改进 prompt 以避免同类问题。

## 当前 Prompt
{current_prompt[:3000]}

## 根因
{root_cause}

## 改进建议
{chr(10).join(f'- {s}' for s in suggestions[:5])}

## Bad Cases
{cases_text[:2000]}

## 要求
1. 保持原有 prompt 的结构和角色定义
2. 在关键位置增加约束或示例，针对性解决上述问题
3. 不要删除原有内容，只增加和修改
4. 直接输出改进后的完整 prompt
"""

    response = _call_llm(
        caller="prompt_improver",
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=8000,
    )

    return response.choices[0].message.content or current_prompt


def _infer_agent_type_from_bad_cases(results: list) -> str | None:
    """从 Bad Case 中推断哪个 agent 受此根因影响最大。"""
    # 统计各 agent_type 的 Bad Case 数量
    agent_counts = {}
    for r in results:
        agent_type = r.get("agent_type", "") or ""
        if not agent_type:
            # 尝试从 detail/summary 推断
            detail = r.get("detail", "") or r.get("summary", "") or ""
            if "估值" in detail:
                agent_type = "valuation_expert"
            elif "风险" in detail:
                agent_type = "risk_assessor"
            elif "配置" in detail:
                agent_type = "allocation_advisor"
            elif "市场" in detail:
                agent_type = "market_analyst"
            elif "行为" in detail or "情绪" in detail:
                agent_type = "behavioral_coach"
            else:
                agent_type = "general"

        agent_counts[agent_type] = agent_counts.get(agent_type, 0) + 1

    if not agent_counts:
        return None

    return max(agent_counts, key=agent_counts.get)


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


# ═══════════════════════════════════════════════════════════════
# Agent 性能监控 Dashboard
# ═══════════════════════════════════════════════════════════════

def get_agent_performance_dashboard(days: int = 30) -> dict:
    """获取所有 Agent 的性能仪表盘数据。

    返回每个 Agent 的：
    - 成功率（按状态分组）
    - 平均耗时
    - 平均输出长度
    - Token 效率（输出 token / 总 token）
    - 最近 7 天趋势

    Returns:
        {
            "agents": [
                {
                    "agent_key": "valuation_expert",
                    "agent_name": "估值专家",
                    "total_runs": 100,
                    "success_rate": 0.85,
                    "status_breakdown": {"completed": 85, "failed": 15},
                    "avg_duration_ms": 12000,
                    "avg_output_length": 2500,
                    "token_efficiency": 0.65,
                    "trend_7d": [{"date": "2026-07-01", "runs": 5, "avg_score": 75}, ...]
                }
            ],
            "summary": {
                "total_agents": 5,
                "total_runs": 500,
                "overall_success_rate": 0.82,
                "overall_avg_duration_ms": 15000
            }
        }
    """
    from db._conn import _get_conn
    from datetime import datetime, timedelta

    conn = _get_conn()

    # 查询指定天数内的 agent_runs 数据
    rows = conn.execute("""
        SELECT
            agent_key,
            agent_name,
            status,
            duration_ms,
            output_length,
            token_usage_input,
            token_usage_output,
            created_at
        FROM agent_runs
        WHERE created_at >= datetime('now', ?)
        ORDER BY created_at DESC
    """, (f"-{days} days",)).fetchall()

    if not rows:
        conn.close()
        return {"agents": [], "summary": {"total_agents": 0, "total_runs": 0,
                                           "overall_success_rate": 0, "overall_avg_duration_ms": 0}}

    # 按 agent_key 聚合
    agent_stats: dict[str, dict] = {}
    for row in rows:
        row = dict(row)
        key = row.get("agent_key", "unknown")
        if key not in agent_stats:
            agent_stats[key] = {
                "agent_key": key,
                "agent_name": row.get("agent_name", key),
                "total_runs": 0,
                "status_counts": {},
                "total_duration_ms": 0,
                "total_output_length": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "daily_stats": {},  # date -> {runs, scores}
            }

        stats = agent_stats[key]
        stats["total_runs"] += 1

        status = row.get("status", "unknown") or "unknown"
        stats["status_counts"][status] = stats["status_counts"].get(status, 0) + 1

        stats["total_duration_ms"] += row.get("duration_ms", 0) or 0
        stats["total_output_length"] += row.get("output_length", 0) or 0
        stats["total_input_tokens"] += row.get("token_usage_input", 0) or 0
        stats["total_output_tokens"] += row.get("token_usage_output", 0) or 0

        # 按天统计趋势
        created = row.get("created_at", "")
        if created:
            date_str = created[:10]  # YYYY-MM-DD
            if date_str not in stats["daily_stats"]:
                stats["daily_stats"][date_str] = {"runs": 0, "total_duration": 0}
            stats["daily_stats"][date_str]["runs"] += 1
            stats["daily_stats"][date_str]["total_duration"] += row.get("duration_ms", 0) or 0

    # 计算最终指标
    agents_list = []
    total_runs_all = 0
    total_success_all = 0
    total_duration_all = 0

    for key, stats in agent_stats.items():
        total = stats["total_runs"]
        success = stats["status_counts"].get("completed", 0) + stats["status_counts"].get("success", 0)
        success_rate = success / total if total > 0 else 0

        avg_duration = stats["total_duration_ms"] / total if total > 0 else 0
        avg_output = stats["total_output_length"] / total if total > 0 else 0

        total_tokens = stats["total_input_tokens"] + stats["total_output_tokens"]
        token_efficiency = stats["total_output_tokens"] / total_tokens if total_tokens > 0 else 0

        # 7 天趋势
        trend_7d = []
        now = datetime.now()
        for i in range(6, -1, -1):
            date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day_data = stats["daily_stats"].get(date, {"runs": 0, "total_duration": 0})
            avg_dur = day_data["total_duration"] / day_data["runs"] if day_data["runs"] > 0 else 0
            trend_7d.append({"date": date, "runs": day_data["runs"], "avg_duration_ms": avg_dur})

        agents_list.append({
            "agent_key": key,
            "agent_name": stats["agent_name"],
            "total_runs": total,
            "success_rate": round(success_rate, 4),
            "status_breakdown": stats["status_counts"],
            "avg_duration_ms": round(avg_duration),
            "avg_output_length": round(avg_output),
            "token_efficiency": round(token_efficiency, 4),
            "trend_7d": trend_7d,
        })

        total_runs_all += total
        total_success_all += success
        total_duration_all += stats["total_duration_ms"]

    conn.close()

    overall_success = total_success_all / total_runs_all if total_runs_all > 0 else 0
    overall_duration = total_duration_all / total_runs_all if total_runs_all > 0 else 0

    return {
        "agents": agents_list,
        "summary": {
            "total_agents": len(agents_list),
            "total_runs": total_runs_all,
            "overall_success_rate": round(overall_success, 4),
            "overall_avg_duration_ms": round(overall_duration),
        },
    }


def check_agent_health(days: int = 7) -> list[dict]:
    """检查所有 Agent 的健康状态。

    判定规则：
    - 成功率 < 50% → critical
    - 成功率 50-80% → warning
    - 成功率 > 80% → healthy

    Returns:
        [
            {
                "agent_key": "valuation_expert",
                "agent_name": "估值专家",
                "status": "healthy" | "warning" | "critical",
                "success_rate": 0.85,
                "total_runs": 50,
                "message": "运行正常"
            }
        ]
    """
    dashboard = get_agent_performance_dashboard(days=days)

    health_list = []
    for agent in dashboard.get("agents", []):
        success_rate = agent.get("success_rate", 0)
        total_runs = agent.get("total_runs", 0)

        if total_runs == 0:
            status = "healthy"
            message = "无运行记录"
        elif success_rate < 0.5:
            status = "critical"
            message = f"成功率仅 {success_rate:.0%}，需要立即排查"
        elif success_rate < 0.8:
            status = "warning"
            message = f"成功率 {success_rate:.0%}，低于预期"
        else:
            status = "healthy"
            message = f"运行正常（成功率 {success_rate:.0%}）"

        health_list.append({
            "agent_key": agent["agent_key"],
            "agent_name": agent["agent_name"],
            "status": status,
            "success_rate": success_rate,
            "total_runs": total_runs,
            "message": message,
        })

    return health_list


def get_low_performing_agents(limit: int = 3) -> list[dict]:
    """获取成功率最低的 N 个 Agent。

    Args:
        limit: 返回数量

    Returns:
        [{"agent_key", "agent_name", "success_rate", "total_runs"}]
    """
    dashboard = get_agent_performance_dashboard(days=30)

    agents = dashboard.get("agents", [])
    # 过滤掉无运行记录的
    agents_with_runs = [a for a in agents if a.get("total_runs", 0) > 0]
    # 按成功率升序排序
    agents_sorted = sorted(agents_with_runs, key=lambda x: x.get("success_rate", 1.0))

    return [
        {
            "agent_key": a["agent_key"],
            "agent_name": a["agent_name"],
            "success_rate": a["success_rate"],
            "total_runs": a["total_runs"],
        }
        for a in agents_sorted[:limit]
    ]
