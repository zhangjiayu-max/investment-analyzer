"""Bad Case 根因自动分析模块

使用 LLM 对 Bad Case 进行根因分类，支持两种来源：
- portfolio_analysis_records (分析记录)
- llm_feedback (对话反馈)

根因分类体系：
- data_missing: 关键数据缺失（估值、持仓、行情等）
- reasoning_error: 推理逻辑错误（自相矛盾、因果倒置等）
- knowledge_gap: 知识不足（超出模型知识范围）
- format_issue: 格式/结构问题（输出不规范、缺少关键段落）
- hallucination: 幻觉/编造数据（捏造数字、虚构事实）
- irrelevant: 答非所问（没理解用户意图）
- outdated_info: 信息过时（用旧数据给出建议）
- tone_issue: 语气/态度问题（过于武断、不够谨慎等）
- other: 其他
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 根因分类体系
ROOT_CAUSES = {
    "data_missing": {
        "label": "数据缺失",
        "description": "关键数据缺失（估值、持仓、行情等），导致分析不完整",
        "severity": "high",
    },
    "reasoning_error": {
        "label": "推理错误",
        "description": "逻辑推理有误（自相矛盾、因果倒置、过度推断等）",
        "severity": "high",
    },
    "knowledge_gap": {
        "label": "知识不足",
        "description": "超出模型知识范围，缺乏特定领域专业知识",
        "severity": "medium",
    },
    "format_issue": {
        "label": "格式问题",
        "description": "输出格式不规范、缺少关键段落、结构混乱",
        "severity": "low",
    },
    "hallucination": {
        "label": "幻觉编造",
        "description": "捏造数据、虚构事实、引用不存在的指标",
        "severity": "critical",
    },
    "irrelevant": {
        "label": "答非所问",
        "description": "没有理解用户意图，回答偏离问题",
        "severity": "medium",
    },
    "outdated_info": {
        "label": "信息过时",
        "description": "使用过时数据或已不适用的建议",
        "severity": "medium",
    },
    "tone_issue": {
        "label": "语气问题",
        "description": "过于武断、不够谨慎、缺乏风险提示",
        "severity": "low",
    },
    "other": {
        "label": "其他",
        "description": "不属于以上分类的其他问题",
        "severity": "low",
    },
}

ANALYSIS_PROMPT = """你是一个投资分析质量审核专家。请分析以下 Bad Case，找出根本原因。

## Bad Case 信息
- 来源: {source}
- 类型: {type}
- 用户反馈: {note}
- 创建时间: {created_at}

## 用户输入
{input_data}

## 模型输出（摘要）
{output_data}

## 根因分类体系
{causes_desc}

## 要求
请严格按 JSON 格式返回，包含以下字段：
```json
{{
  "root_cause": "分类代码（从上面选一个）",
  "confidence": 0.0-1.0,
  "detail": "一句话说明根因",
  "evidence": "从输入/输出中找到的关键证据",
  "suggestion": "改进建议"
}}
```

只返回 JSON，不要其他内容。"""


def _build_prompt(bad_case: dict) -> str:
    """构建分析 Prompt。"""
    causes_desc = "\n".join(
        f"- `{code}`: {info['label']} — {info['description']}"
        for code, info in ROOT_CAUSES.items()
    )

    return ANALYSIS_PROMPT.format(
        source=bad_case.get("source", "unknown"),
        type=bad_case.get("type", "unknown"),
        note=bad_case.get("note") or "用户未填写原因",
        created_at=bad_case.get("created_at", ""),
        input_data=(bad_case.get("input") or "")[:1500],
        output_data=(bad_case.get("output") or "")[:2000],
        causes_desc=causes_desc,
    )


def _parse_response(text: str) -> dict | None:
    """解析 LLM 返回的 JSON。"""
    try:
        # 尝试直接解析
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 块
    import re
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试找第一个 { 到最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def analyze_root_cause(bad_case: dict) -> dict | None:
    """分析单个 Bad Case 的根因。

    参数:
        bad_case: 统一格式的 bad case 字典（来自 list_all_bad_cases）

    返回:
        {
            "root_cause": str,      # 分类代码
            "confidence": float,    # 置信度 0-1
            "detail": str,          # 一句话根因
            "evidence": str,        # 关键证据
            "suggestion": str,      # 改进建议
        }
        或 None（分析失败时）
    """
    from services.llm_service import _call_llm

    prompt = _build_prompt(bad_case)

    try:
        response = _call_llm(
            caller="root_cause_analyzer",
            model=None,  # 使用默认模型
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
        )

        text = response.choices[0].message.content if hasattr(response, "choices") else str(response)
        result = _parse_response(text or "")
        if not result:
            logger.warning(f"根因分析返回非 JSON: {(text or '')[:200]}")
            return None

        # 验证 root_cause 是否在分类体系内
        rc = result.get("root_cause", "")
        if rc not in ROOT_CAUSES:
            logger.warning(f"未知根因分类: {rc}")
            result["root_cause"] = "other"

        return result

    except Exception as e:
        logger.error(f"根因分析失败: {e}")
        return None


def batch_analyze(limit: int = 50, force: bool = False) -> dict:
    """批量分析未标记根因的 Bad Case。

    参数:
        limit: 最大分析条数
        force: 是否强制重新分析已有根因的记录

    返回:
        {
            "total": int,
            "analyzed": int,
            "failed": int,
            "skipped": int,
            "results": list,
        }
    """
    from db.config import get_config

    if get_config("llm_cost.root_cause_analyzer", "false") != "true":
        return {
            "total": 0,
            "analyzed": 0,
            "failed": 0,
            "skipped": 0,
            "results": [],
            "message": "自动根因分析已关闭",
        }

    from db.portfolio import list_all_bad_cases
    from db._conn import _get_conn

    cases = list_all_bad_cases(limit=200)  # 多拿一些，后面过滤

    # 过滤掉已有根因的（除非 force=True）
    if not force:
        conn = _get_conn()
        analyzed_ids = set()

        # 查已有根因的 analysis 记录
        rows = conn.execute(
            "SELECT id FROM portfolio_analysis_records WHERE root_cause != '' AND root_cause IS NOT NULL"
        ).fetchall()
        analyzed_ids.update(f"analysis_{r['id']}" for r in rows)

        # 查已有根因的 feedback 记录
        rows = conn.execute(
            "SELECT id FROM llm_feedback WHERE root_cause != '' AND root_cause IS NOT NULL"
        ).fetchall()
        analyzed_ids.update(f"chat_{r['id']}" for r in rows)
        conn.close()

        cases = [c for c in cases if f"{c['source']}_{c['id']}" not in analyzed_ids]

    cases = cases[:limit]

    total = len(cases)
    analyzed = 0
    failed = 0
    results = []

    for case in cases:
        result = analyze_root_cause(case)
        if result:
            # 写入数据库
            _save_root_cause(case["source"], case["id"], result)
            analyzed += 1
            results.append({
                "source": case["source"],
                "id": case["id"],
                "root_cause": result["root_cause"],
                "detail": result.get("detail", ""),
            })
        else:
            failed += 1

    return {
        "total": total,
        "analyzed": analyzed,
        "failed": failed,
        "skipped": 0,
        "results": results,
    }


def _save_root_cause(source: str, case_id: int, result: dict):
    """将根因分析结果写入数据库，并自动写入 improvement_tasks 形成根因闭环。"""
    from db._conn import _get_conn

    conn = _get_conn()
    root_cause = result.get("root_cause", "other")
    detail = json.dumps(result, ensure_ascii=False)

    if source == "analysis":
        conn.execute(
            "UPDATE portfolio_analysis_records SET root_cause = ?, root_cause_detail = ? WHERE id = ?",
            (root_cause, detail, case_id),
        )
    else:
        conn.execute(
            "UPDATE llm_feedback SET root_cause = ?, root_cause_detail = ? WHERE id = ?",
            (root_cause, detail, case_id),
        )
    conn.commit()
    conn.close()

    # 自动写入 improvement_tasks（根因闭环：分析结果 → 可应用的改进项）
    suggestion = result.get("suggestion") or ""
    if suggestion:
        try:
            from db.eval import create_improvement_task
            create_improvement_task(
                source_type="bad_case",
                source_id=case_id,
                root_cause=root_cause,
                suggestion=suggestion,
                status="pending",
            )
        except Exception as e:
            logger.warning(f"写入 improvement_task 失败: {e}")


def get_root_cause_stats() -> dict:
    """获取根因统计信息。

    返回:
        {
            "total_analyzed": int,
            "by_cause": [{"root_cause": str, "label": str, "count": int, "pct": float}],
            "by_severity": {"critical": int, "high": int, "medium": int, "low": int},
            "by_source": {"analysis": dict, "chat": dict},
            "recent": list,  # 最近 10 条分析结果
        }
    """
    from db._conn import _get_conn

    conn = _get_conn()

    # 统计 portfolio_analysis_records 的根因
    analysis_counts = {}
    rows = conn.execute("""
        SELECT root_cause, COUNT(*) as cnt
        FROM portfolio_analysis_records
        WHERE root_cause != '' AND root_cause IS NOT NULL
        GROUP BY root_cause
    """).fetchall()
    for r in rows:
        analysis_counts[r["root_cause"]] = r["cnt"]

    # 统计 llm_feedback 的根因
    chat_counts = {}
    rows = conn.execute("""
        SELECT root_cause, COUNT(*) as cnt
        FROM llm_feedback
        WHERE root_cause != '' AND root_cause IS NOT NULL
        GROUP BY root_cause
    """).fetchall()
    for r in rows:
        chat_counts[r["root_cause"]] = r["cnt"]

    # 合并统计
    all_counts = {}
    for code in ROOT_CAUSES:
        a = analysis_counts.get(code, 0)
        c = chat_counts.get(code, 0)
        all_counts[code] = a + c

    total = sum(all_counts.values())

    by_cause = []
    for code, count in sorted(all_counts.items(), key=lambda x: -x[1]):
        if count > 0:
            by_cause.append({
                "root_cause": code,
                "label": ROOT_CAUSES[code]["label"],
                "severity": ROOT_CAUSES[code]["severity"],
                "count": count,
                "pct": round(count / total * 100, 1) if total > 0 else 0,
            })

    # 按严重程度统计
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for item in by_cause:
        sev = item["severity"]
        severity_counts[sev] = severity_counts.get(sev, 0) + item["count"]

    # 最近 10 条
    recent = []
    rows = conn.execute("""
        SELECT 'analysis' as source, id, root_cause, root_cause_detail, created_at
        FROM portfolio_analysis_records
        WHERE root_cause != '' AND root_cause IS NOT NULL
        UNION ALL
        SELECT 'chat' as source, id, root_cause, root_cause_detail, created_at
        FROM llm_feedback
        WHERE root_cause != '' AND root_cause IS NOT NULL
        ORDER BY created_at DESC LIMIT 10
    """).fetchall()
    for r in rows:
        d = dict(r)
        detail = {}
        if d.get("root_cause_detail"):
            try:
                detail = json.loads(d["root_cause_detail"])
            except Exception:
                pass
        recent.append({
            "source": d["source"],
            "id": d["id"],
            "root_cause": d["root_cause"],
            "label": ROOT_CAUSES.get(d["root_cause"], {}).get("label", d["root_cause"]),
            "detail": detail.get("detail", ""),
            "created_at": d["created_at"],
        })

    conn.close()

    return {
        "total_analyzed": total,
        "by_cause": by_cause,
        "by_severity": severity_counts,
        "by_source": {
            "analysis": analysis_counts,
            "chat": chat_counts,
        },
        "recent": recent,
    }
