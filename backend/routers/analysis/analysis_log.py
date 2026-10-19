"""分析记录统一查询 API — /api/analysis/log/*"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException

from db.agent_analysis_log import (
    list_analysis_logs, get_analysis_log, count_analysis_logs,
    get_analysis_stats, fetch_source_result, update_eval_result,
)
from db.agents import (
    list_agent_runs_with_filter, get_agent_run_stats, get_agent_run_detail,
)
from db.config import get_config_bool

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-log"])
_eval_tasks = set()


@router.get("/api/analysis/log/list")
async def list_analysis_log_api(
    agent_id: int = None,
    analysis_type: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """统一查询分析记录列表，支持多维度过滤 + 分页。"""
    logs = list_analysis_logs(
        agent_id=agent_id,
        analysis_type=analysis_type or None,
        status=status or None,
        date_from=date_from or None,
        date_to=date_to or None,
        limit=limit,
        offset=offset,
    )
    total = count_analysis_logs(
        agent_id=agent_id,
        analysis_type=analysis_type or None,
        status=status or None,
        date_from=date_from or None,
        date_to=date_to or None,
    )
    stats = get_analysis_stats()
    return {"logs": logs, "total": total, "stats": stats}


# ── 对话协作 Tab：查询 agent_runs 表（对话流程的专家产出）──
# 注意：list_runs 和 runs/{run_id} 必须在 {log_id} 之前注册，否则会被吞掉


@router.get("/api/analysis/log/list_runs")
async def list_agent_runs_api(
    conversation_id: int = None,
    agent_key: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """查询对话协作的 agent 执行记录（agent_runs 表）。

    与 /api/analysis/log/list（独立分析接口）互补：
    - list：查 agent_analysis_log 表，记录独立分析接口（日报/分散度/全景等）
    - list_runs：查 agent_runs 表，记录对话流程的多专家协作产出

    支持按对话ID/专家/状态/日期过滤，返回分页列表 + 总数 + 统计。
    """
    runs, total = list_agent_runs_with_filter(
        conversation_id=conversation_id,
        agent_key=agent_key or None,
        status=status or None,
        date_from=date_from or None,
        date_to=date_to or None,
        limit=limit,
        offset=offset,
    )
    stats = get_agent_run_stats()
    return {"runs": runs, "total": total, "stats": stats}


@router.get("/api/analysis/log/runs/{run_id}")
async def get_agent_run_detail_api(run_id: int):
    """获取单条 agent_run 详情（含完整 result 和 tool_calls）。

    用于对话协作 Tab 的详情弹窗，查看专家完整分析内容。
    """
    run = get_agent_run_detail(run_id)
    if not run:
        raise HTTPException(404, "记录不存在")
    return {"run": run}


@router.get("/api/analysis/log/{log_id}")
async def get_analysis_log_detail_api(log_id: int):
    """获取单条分析记录详情，含原始记录的完整 result。"""
    log = get_analysis_log(log_id)
    if not log:
        raise HTTPException(404, "记录不存在")
    # 按 source_table + source_id 查回原始结果
    # 传入 trace_id 用于 fallback：当 source_id 为 None 时从 agent_runs 查
    source_result = fetch_source_result(
        log.get("source_table", ""), log.get("source_id"),
        trace_id=log.get("trace_id"),
    )
    # 查询最新一条质量评估反馈（含各维度评语）
    eval_feedback = None
    try:
        from db import get_llm_feedback_by_target
        fb = get_llm_feedback_by_target("analysis_log", log_id)
        if fb:
            comment = fb.get("comment") or ""
            reasons = {}
            if comment:
                import json as _json
                try:
                    reasons = _json.loads(comment)
                except (ValueError, TypeError):
                    reasons = {}
            eval_feedback = {
                "overall_score": fb.get("overall_score"),
                "score_data_accuracy": fb.get("score_data_accuracy"),
                "score_logic": fb.get("score_logic"),
                "score_actionability": fb.get("score_actionability"),
                "analysis_type": reasons.get("analysis_type", ""),
                "data_accuracy_reason": reasons.get("data_accuracy_reason", ""),
                "logic_reason": reasons.get("logic_reason", ""),
                "actionability_reason": reasons.get("actionability_reason", ""),
                "overall_reason": reasons.get("overall_reason", ""),
            }
    except Exception as e:
        logger.warning(f"查询质量评估反馈失败 log_id={log_id}: {e}")
    diagnostic_note = ''
    if log.get('status') == 'error' and not log.get('error_details'):
        diagnostic_note = '此历史记录未保存底层异常，无法还原具体失败原因。后续失败会记录异常链和请求信息。'
    return {"log": log, "source_result": source_result, "eval_feedback": eval_feedback,
            "diagnostic_note": diagnostic_note}


@router.post("/api/analysis/log/{log_id}/evaluate")
async def evaluate_analysis_log_api(log_id: int):
    """手动触发质量评估（LLM Judge）。"""
    log = get_analysis_log(log_id)
    if not log:
        raise HTTPException(404, "记录不存在")
    if log.get("status") != "done":
        raise HTTPException(400, "仅完成状态的分析可评估")
    # 查回原始结果（传入 trace_id 用于 fallback：当 source_id 为 None 时从 agent_runs 查）
    output = fetch_source_result(
        log.get("source_table", ""), log.get("source_id"),
        trace_id=log.get("trace_id"),
    )
    if not output or len(output.strip()) < 50:
        raise HTTPException(400, "分析结果为空或过短，无法评估")

    from db.eval import claim_governance_task
    from db.agent_analysis_log import set_analysis_eval_state
    from db.async_tasks import update_async_task
    submitted = claim_governance_task('analysis_log_eval','analysis_log',{'log_id':log_id})
    if submitted['reused']:
        return submitted
    task_id = submitted['task_id']
    set_analysis_eval_state(log_id,'running',task_id=task_id)
    async def _run_eval():
        try:
            from agent.eval.eval_scorer import evaluate_llm_output
            result = await asyncio.to_thread(lambda: asyncio.run(evaluate_llm_output(
                query=log.get('query') or log.get('input_summary') or '',output=output,
                context=log.get('input_summary') or '',target_type='analysis_log',target_id=log_id,
                analysis_type=log.get('analysis_type',''))))
            score = result.get('overall_score',0)
            if score <= 0:
                raise RuntimeError(result.get('overall_reason') or '评估失败')
            update_eval_result(log_id,float(score))
            update_async_task(task_id,status='done',result={'ok':True,'log_id':log_id,'score':score})
        except Exception as exc:
            set_analysis_eval_state(log_id,'error',str(exc),task_id)
            update_async_task(task_id,status='error',error_msg=str(exc))
            logger.warning('分析记录评估失败 log_id=%s: %s',log_id,exc)
    task = asyncio.create_task(_run_eval())
    _eval_tasks.add(task)
    task.add_done_callback(_eval_tasks.discard)
    return submitted
