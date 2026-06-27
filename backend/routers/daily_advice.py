"""每日持仓提示路由 — /api/daily-advice/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json
import logging

from db.daily_advice import (
    get_signal, update_signal_status, list_today_signals,
    list_runs, get_today_run,
)
from daily_position_advisor import run_daily_position_advice
from db.decisions import create_candidate_from_structured_recommendation

logger = logging.getLogger(__name__)
router = APIRouter()


class RunRequest(BaseModel):
    user_id: str = "default"
    trigger_type: str = "manual"
    force: bool = False


@router.post("/api/daily-advice/run")
async def run_advice(req: RunRequest):
    """手动触发每日持仓提示。"""
    result = run_daily_position_advice(
        user_id=req.user_id,
        trigger_type=req.trigger_type,
        force=req.force,
    )
    return result


@router.get("/api/daily-advice/today")
async def get_today(user_id: str = "default"):
    """获取今日提示聚合视图。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    run = get_today_run(user_id, today)
    signals = list_today_signals(user_id, today)

    if not run:
        return {
            "run": None,
            "headline": "今日尚未生成提示，点击「生成今日提示」开始。",
            "stats": {"actionable": 0, "watch": 0, "blocked": 0, "info": 0},
            "signals": [],
        }

    import json
    stats = json.loads(run.get("stats_json", "{}"))

    # 生成 headline
    actionable = stats.get("actionable", 0)
    if actionable > 0:
        action_signals = [s for s in signals if s.get("severity") == "actionable"]
        names = "、".join(s.get("target_name", "") for s in action_signals[:3])
        headline = f"今日 {actionable} 条可行动：{names}"
    elif stats.get("blocked", 0) > 0:
        headline = "今日有风险拦截，建议查看详情"
    elif stats.get("watch", 0) > 0:
        headline = "今日偏观察，无紧急行动"
    else:
        headline = "今日持仓稳定，继续持有"

    # 候选 ID
    candidate_ids = [s.get("candidate_id") for s in signals if s.get("candidate_id")]

    # 过滤掉 hold 类型的 info 信号（无行动价值，减少噪音）
    display_signals = [s for s in signals if s.get("signal_type") != "hold"]

    return {
        "run": run,
        "headline": headline,
        "stats": stats,
        "signals": display_signals,
        "candidate_ids": candidate_ids,
    }


@router.get("/api/daily-advice/runs")
async def list_runs_api(user_id: str = "default", limit: int = 30):
    """查看历史运行记录。"""
    return {"runs": list_runs(user_id, limit)}


@router.get("/api/daily-advice/signals")
async def list_signals_api(user_id: str = "default", signal_date: Optional[str] = None):
    """查看信号列表。"""
    from datetime import datetime
    if not signal_date:
        signal_date = datetime.now().strftime("%Y-%m-%d")
    signals = list_today_signals(user_id, signal_date)
    # 过滤掉 hold 类型的 info 信号（无行动价值，减少噪音）
    return {"signals": [s for s in signals if s.get("signal_type") != "hold"]}


@router.post("/api/daily-advice/signals/{signal_id}/read")
async def mark_read(signal_id: int):
    """标记信号已读。"""
    update_signal_status(signal_id, "read")
    return {"ok": True}


@router.post("/api/daily-advice/signals/{signal_id}/ignore")
async def mark_ignore(signal_id: int):
    """忽略信号。"""
    update_signal_status(signal_id, "ignored")
    return {"ok": True}


@router.post("/api/daily-advice/signals/{signal_id}/create-candidate")
async def create_candidate(signal_id: int):
    """将信号转为建议候选。"""
    signal = get_signal(signal_id)
    if not signal:
        raise HTTPException(404, "信号不存在")

    if signal.get("candidate_id"):
        return {"ok": True, "candidate_id": signal["candidate_id"], "message": "已有候选"}

    candidate_id = create_candidate_from_structured_recommendation({
        "source_type": "daily_advice",
        "source_id": str(signal.get("run_id")),
        "scenario_type": signal.get("signal_type", ""),
        "action_type": signal.get("action_type", "watch"),
        "target_type": "fund",
        "target_code": signal.get("target_code", ""),
        "target_name": signal.get("target_name", ""),
        "summary": signal.get("summary", ""),
        "rationale": signal.get("rationale", ""),
        "suggested_amount": signal.get("suggested_amount"),
        "confidence": "medium",
        "evidence": signal.get("evidence_json", {}),
        "risk": signal.get("risk_json", {}),
        "source_snapshot": signal.get("source_snapshot_json", {}),
        "dedupe_key": signal.get("dedupe_key", ""),
        "priority": 2,
        "status": "new",
    })

    update_signal_status(signal_id, "promoted", candidate_id=candidate_id)
    return {"ok": True, "candidate_id": candidate_id}


@router.post("/api/daily-advice/signals/{signal_id}/ask-ai")
async def ask_ai_about_signal(signal_id: int):
    """根据信号上下文调用 LLM 生成解释。"""
    signal = get_signal(signal_id)
    if not signal:
        raise HTTPException(404, "信号不存在")

    # 拼接信号上下文
    target_name = signal.get("target_name", "")
    target_code = signal.get("target_code", "")
    signal_type = signal.get("signal_type", "")
    action_type = signal.get("action_type", "")
    severity = signal.get("severity", "")
    summary = signal.get("summary", "")
    rationale = signal.get("rationale", "")
    score = signal.get("score", 0)
    suggested_amount = signal.get("suggested_amount")

    try:
        evidence = json.loads(signal.get("evidence_json", "{}"))
    except Exception:
        evidence = {}

    try:
        risks = json.loads(signal.get("risk_json", "{}"))
    except Exception:
        risks = {}

    # 构建 prompt
    evidence_text = "\n".join(f"  - {k}: {v}" for k, v in evidence.items()) if evidence else "  无"
    risk_notes = risks.get("notes", []) if isinstance(risks, dict) else []
    risk_text = "\n".join(f"  - {r}" for r in risk_notes) if risk_notes else "  无"
    amount_text = f"\n  建议金额: ¥{suggested_amount:.0f}" if suggested_amount else ""

    prompt = f"""你是用户的投资持仓顾问。请根据以下每日持仓信号，用通俗易懂的语言解释：
1. 这个信号意味着什么？
2. 为什么系统会生成这个建议？
3. 用户应该怎么做？
4. 有哪些风险需要注意？

请用简洁的中文回答，不要泛泛而谈，要基于具体数据。

## 信号详情
- 基金名称: {target_name}（{target_code}）
- 信号类型: {signal_type}
- 建议动作: {action_type}
- 信号等级: {severity}
- 评分: {score}/100{amount_text}

## 摘要
{summary}

## 分析依据
{rationale}

## 证据
{evidence_text}

## 风险提示
{risk_text}

请给出你的解释和建议："""

    try:
        from llm_service import _call_llm, MODEL
        from db.config import get_config_float, get_config_int

        response = _call_llm(
            caller="daily_advice_ask_ai",
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一位专业的投资持仓顾问。请根据数据给出客观、具体的分析和建议，最后加一句风险提示。"},
                {"role": "user", "content": prompt},
            ],
            temperature=get_config_float("llm.temperature_analysis", 0.3),
            max_tokens=get_config_int("llm.max_tokens_analysis", 8000),
        )
        ai_text = response.choices[0].message.content or ""

        return {
            "ok": True,
            "signal_id": signal_id,
            "ai_explanation": ai_text,
        }
    except Exception as e:
        logger.error(f"ask-ai 失败: {e}", exc_info=True)
        raise HTTPException(500, f"AI 解释生成失败: {e}")


# ── 周报生成器 ──────────────────────────────────────────

@router.post("/api/daily-advice/weekly-report")
async def generate_weekly_report(user_id: str = "default"):
    """Generate weekly investment report from signals, decisions, and thread summaries."""
    from datetime import datetime, timedelta
    from db.thread_summaries import list_thread_summaries
    from db._conn import _get_conn

    week_ago = (datetime.now() - timedelta(days=7)).isoformat()

    conn = _get_conn()
    try:
        # Get signals from past 7 days
        signals = conn.execute(
            "SELECT * FROM daily_position_signals WHERE user_id=? AND created_at >= ? ORDER BY created_at DESC",
            (user_id, week_ago)
        ).fetchall()
        signals_list = [dict(s) for s in signals]

        # Get decisions from past 7 days
        decisions = conn.execute(
            "SELECT * FROM decision_records WHERE user_id=? AND created_at >= ? ORDER BY created_at DESC",
            (user_id, week_ago)
        ).fetchall()
        decisions_list = [dict(d) for d in decisions]
    finally:
        conn.close()

    # Get thread summaries
    summaries = list_thread_summaries(user_id, limit=50)
    summaries_week = [s for s in summaries if s.get("created_at", "") >= week_ago]

    # Count signal types
    actionable_count = len([s for s in signals_list if s.get("severity") == "actionable"])
    watch_count = len([s for s in signals_list if s.get("severity") == "watch"])

    # Build LLM prompt
    prompt = f"""基于以下投资数据，生成一份简洁的周报：

信号统计：{len(signals_list)}条信号（{actionable_count}条可行动，{watch_count}条观察）
关键信号：{json.dumps([s.get('summary','') for s in signals_list[:5]], ensure_ascii=False)}
决策记录：{json.dumps([d.get('title','') for d in decisions_list[:5]], ensure_ascii=False)}
对话摘要：{json.dumps([s.get('summary','') for s in summaries_week[:3]], ensure_ascii=False)}

请生成一份200-400字的周报，包含：
1. 本周市场/持仓概况
2. 主要操作与决策
3. 下周关注点
"""

    try:
        from llm_service import _call_llm, MODEL
        from db.config import get_config_float, get_config_int

        response = _call_llm(
            caller="weekly_report",
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个专业的投资周报撰写助手。"},
                {"role": "user", "content": prompt},
            ],
            temperature=get_config_float("llm.temperature_analysis", 0.3),
            max_tokens=get_config_int("llm.max_tokens_analysis", 4096),
        )
        report_text = response.choices[0].message.content or ""

        return {
            "period": {"from": week_ago, "to": datetime.now().isoformat()},
            "stats": {
                "total_signals": len(signals_list),
                "actionable": actionable_count,
                "watch": watch_count,
                "decisions": len(decisions_list),
                "conversations": len(summaries_week)
            },
            "report": report_text,
            "signals": signals_list[:10],
            "decisions": decisions_list[:5]
        }
    except Exception as e:
        logger.error(f"周报生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── 决策标签管理 ──────────────────────────────────────────

class TagRequest(BaseModel):
    tag: str


@router.post("/api/daily-advice/decisions/{decision_id}/tags")
async def add_tag(decision_id: int, req: TagRequest):
    from db.daily_advice import add_decision_tag
    ok = add_decision_tag(decision_id, req.tag)
    if not ok:
        raise HTTPException(404, "Decision not found")
    return {"status": "ok", "tag": req.tag}


@router.delete("/api/daily-advice/decisions/{decision_id}/tags/{tag}")
async def delete_tag(decision_id: int, tag: str):
    from db.daily_advice import remove_decision_tag
    ok = remove_decision_tag(decision_id, tag)
    if not ok:
        raise HTTPException(404, "Decision not found")
    return {"status": "ok", "tag": tag}


@router.get("/api/daily-advice/decisions/{decision_id}/tags")
async def get_tags(decision_id: int):
    from db.daily_advice import list_decision_tags
    return {"tags": list_decision_tags(decision_id)}
