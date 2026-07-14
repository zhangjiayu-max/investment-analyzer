"""分析记录统一查询 API — /api/analysis/log/*"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException

from db.agent_analysis_log import (
    list_analysis_logs, get_analysis_log, count_analysis_logs,
    get_analysis_stats, fetch_source_result, update_eval_result,
)
from db.config import get_config_bool

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-log"])


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


@router.get("/api/analysis/log/{log_id}")
async def get_analysis_log_detail_api(log_id: int):
    """获取单条分析记录详情，含原始记录的完整 result。"""
    log = get_analysis_log(log_id)
    if not log:
        raise HTTPException(404, "记录不存在")
    # 按 source_table + source_id 查回原始结果
    source_result = ""
    if log.get("source_id"):
        source_result = fetch_source_result(log["source_table"], log["source_id"])
    return {"log": log, "source_result": source_result}


@router.post("/api/analysis/log/{log_id}/evaluate")
async def evaluate_analysis_log_api(log_id: int):
    """手动触发质量评估（LLM Judge）。"""
    log = get_analysis_log(log_id)
    if not log:
        raise HTTPException(404, "记录不存在")
    if log.get("status") != "done":
        raise HTTPException(400, "仅完成状态的分析可评估")
    # 查回原始结果
    output = ""
    if log.get("source_id"):
        output = fetch_source_result(log["source_table"], log["source_id"])
    if not output or len(output.strip()) < 50:
        raise HTTPException(400, "分析结果为空或过短，无法评估")

    async def _run_eval():
        try:
            from agent.eval.eval_scorer import evaluate_llm_output
            result = await evaluate_llm_output(
                query=log.get("query") or log.get("input_summary") or "",
                output=output,
                context=log.get("input_summary") or "",
                target_type="analysis_log",
                target_id=log_id,
            )
            overall = result.get("overall_score", 0)
            update_eval_result(log_id, float(overall))
            logger.info(f"分析记录评估完成 log_id={log_id} score={overall}")
        except Exception as e:
            logger.warning(f"分析记录评估失败 log_id={log_id}: {e}")

    asyncio.create_task(_run_eval())
    return {"ok": True, "message": "评估已提交，稍后刷新查看结果"}
