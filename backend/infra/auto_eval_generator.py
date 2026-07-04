"""自动生成 Eval Case 模块

两种来源：
1. 正例：高分对话（overall_score >= 8 或 rating = 'helpful'）→ 正向评测用例
2. 负例：Bad Case（rating = 'unhelpful'）→ 回归测试用例

去重策略：
- 用 source + source_id 标记已转化的记录，避免重复创建
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def auto_generate_eval_cases(min_score: float = 8.0, limit: int = 50) -> dict:
    """自动生成 Eval Case（正例 + 负例）。

    参数:
        min_score: 高分阈值（overall_score >= 此值视为正例）
        limit: 每种来源的最大处理条数

    返回:
        {
            "positive": {"total": int, "created": int, "skipped": int},
            "negative": {"total": int, "created": int, "skipped": int},
        }
    """
    from db._conn import _get_conn
    from db.eval import create_eval_case, list_eval_cases

    # 获取已转化的标记
    existing = list_eval_cases(active_only=False)
    existing_descs = {c.get("description", "") for c in existing}

    conn = _get_conn()

    # ── 正例：高分 LLM 反馈 ──
    pos_total = 0
    pos_created = 0
    pos_skipped = 0

    rows = conn.execute("""
        SELECT id, caller, input_summary, output_summary, rating,
               overall_score, score_data_accuracy, score_logic, score_actionability,
               tags, created_at
        FROM llm_feedback
        WHERE (overall_score >= ? OR rating = 'helpful')
          AND overall_score IS NOT NULL
        ORDER BY overall_score DESC, created_at DESC
        LIMIT ?
    """, (min_score, limit)).fetchall()

    for r in rows:
        d = dict(r)
        pos_total += 1
        desc_marker = f"来源: chat | 原始ID: {d['id']} | 类型: positive"
        if desc_marker in existing_descs:
            pos_skipped += 1
            continue

        # 构建 input_params
        input_params = json.dumps({
            "question": d.get("input_summary", "")[:500],
            "caller": d.get("caller", ""),
        }, ensure_ascii=False)

        # 生成名称
        caller_label = {
            "chat": "自由对话", "agent_chat": "Agent对话",
            "agent_tools": "工具对话", "article_analysis": "文章分析",
            "daily_report": "每日报告",
        }.get(d.get("caller", ""), d.get("caller", "未知"))
        score = d.get("overall_score", 0)
        name = f"✅ {caller_label}-高分({score:.1f})"

        # 用 LLM 生成期望质量标准（简化版，用输出摘要作为基准）
        expected_quality = _generate_quality_from_high_score(d)

        create_eval_case(
            name=name,
            analysis_type=d.get("caller", "ai"),
            input_params=input_params,
            description=f"自动正例 | {desc_marker}",
            expected_quality=expected_quality,
        )
        pos_created += 1

    # ── 负例：Bad Case ──
    neg_total = 0
    neg_created = 0
    neg_skipped = 0

    rows = conn.execute("""
        SELECT id, 'analysis' as source, analysis_type as type,
               summary, input_data, result_data, feedback_note, created_at
        FROM portfolio_analysis_records
        WHERE feedback = 'unhelpful'
        ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()

    for r in rows:
        d = dict(r)
        neg_total += 1
        desc_marker = f"来源: analysis | 原始ID: {d['id']}"
        if desc_marker in existing_descs:
            neg_skipped += 1
            continue

        input_params = json.dumps({
            "raw_input": (d.get("input_data") or "")[:500],
        }, ensure_ascii=False)

        analysis_type = d.get("type", "ai")
        note_preview = (d.get("feedback_note") or "")[:20]
        name = f"❌ BadCase-{analysis_type}-{note_preview or '无备注'}"

        create_eval_case(
            name=name,
            analysis_type=analysis_type,
            input_params=input_params,
            description=f"自动负例 | {desc_marker}",
            expected_quality="",
        )
        neg_created += 1

    # 也处理 llm_feedback 中的 unhelpful
    rows = conn.execute("""
        SELECT id, caller, input_summary, output_summary, comment, created_at
        FROM llm_feedback
        WHERE rating = 'unhelpful'
        ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()

    for r in rows:
        d = dict(r)
        neg_total += 1
        desc_marker = f"来源: chat | 原始ID: {d['id']} | 类型: negative"
        if desc_marker in existing_descs:
            neg_skipped += 1
            continue

        input_params = json.dumps({
            "question": d.get("input_summary", "")[:500],
            "caller": d.get("caller", ""),
        }, ensure_ascii=False)

        caller = d.get("caller", "ai")
        comment_preview = (d.get("comment") or "")[:20]
        name = f"❌ BadCase-{caller}-{comment_preview or '无备注'}"

        create_eval_case(
            name=name,
            analysis_type=caller,
            input_params=input_params,
            description=f"自动负例 | {desc_marker}",
            expected_quality="",
        )
        neg_created += 1

    conn.close()

    return {
        "positive": {"total": pos_total, "created": pos_created, "skipped": pos_skipped},
        "negative": {"total": neg_total, "created": neg_created, "skipped": neg_skipped},
    }


def _generate_quality_from_high_score(feedback: dict) -> str:
    """从高分反馈中提取质量标准。

    用输出摘要作为参考基准，后续可升级为 LLM 生成。
    """
    output = feedback.get("output_summary", "")
    if not output:
        return "参考正例输出，确保回答准确、有条理、可操作"

    # 取输出的前 300 字作为质量参考
    return f"参考标准（来自高分对话）：\n{output[:300]}"
