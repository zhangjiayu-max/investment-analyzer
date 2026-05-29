"""评测集路由 — /api/eval/*"""

import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from db import (
    create_eval_case, list_eval_cases, get_eval_case, delete_eval_case,
    create_eval_run, list_eval_runs, get_eval_run_detail, get_eval_stats,
)
from models.eval import CreateEvalCaseRequest
from routers.portfolio import (
    panorama_analysis_api, fund_deep_dive_api,
    trade_review_api, what_if_analysis_api,
    portfolio_diversification_ai_summary, portfolio_ai_analysis_api,
)
from models.portfolio import (
    PanoramaAnalysisRequest, DeepDiveRequest,
    TradeReviewRequest, WhatIfRequest, PortfolioAiAnalysisRequest,
)

router = APIRouter(tags=["eval"])


@router.get("/api/eval/cases")
async def list_eval_cases_api(analysis_type: str = "", active_only: bool = True):
    """列出评测用例。"""
    return {"cases": list_eval_cases(analysis_type=analysis_type or None, active_only=active_only)}


@router.post("/api/eval/cases")
async def create_eval_case_api(req: CreateEvalCaseRequest):
    """创建评测用例。"""
    case_id = create_eval_case(
        name=req.name, analysis_type=req.analysis_type,
        input_params=req.input_params, description=req.description,
        expected_quality=req.expected_quality,
    )
    return {"ok": True, "id": case_id}


@router.delete("/api/eval/cases/{case_id}")
async def delete_eval_case_api(case_id: int):
    """删除评测用例。"""
    ok = delete_eval_case(case_id)
    if not ok:
        raise HTTPException(404, "评测用例不存在")
    return {"ok": True}


@router.post("/api/eval/cases/{case_id}/run")
async def run_eval_case_api(case_id: int):
    """运行单个评测用例：调用对应的分析模式，记录结果。"""
    case = get_eval_case(case_id)
    if not case:
        raise HTTPException(404, "评测用例不存在")

    input_params = json.loads(case["input_params"] or "{}")
    analysis_type = case["analysis_type"]
    start = time.time()

    try:
        if analysis_type == "panorama":
            result = await panorama_analysis_api(PanoramaAnalysisRequest())
            # 如果返回的是 StreamingResponse 或 dict
            if isinstance(result, StreamingResponse):
                result_summary = "流式输出（已在后台执行）"
                result_data = "{}"
            else:
                result_summary = json.dumps(result, ensure_ascii=False)[:500]
                result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "deep_dive":
            holding_id = input_params.get("holding_id")
            if not holding_id:
                raise HTTPException(400, "deep_dive 需要 holding_id 参数")
            result = await fund_deep_dive_api(holding_id, DeepDiveRequest())
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "trade_review":
            result = await trade_review_api(TradeReviewRequest(
                start_date=input_params.get("start_date", ""),
                end_date=input_params.get("end_date", ""),
            ))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "what_if":
            result = await what_if_analysis_api(WhatIfRequest(
                scenario=input_params.get("scenario", ""),
                parameter=input_params.get("parameter", ""),
            ))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "diversification_ai":
            result = await portfolio_diversification_ai_summary()
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        elif analysis_type == "ai":
            question = input_params.get("question", "")
            result = await portfolio_ai_analysis_api(PortfolioAiAnalysisRequest(question=question))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)[:5000]
        else:
            raise HTTPException(400, f"不支持的分析类型: {analysis_type}")

        duration_ms = int((time.time() - start) * 1000)
        run_id = create_eval_run(
            case_id=case_id, analysis_type=analysis_type,
            result_summary=result_summary,
            result_data=result_data,
            duration_ms=duration_ms,
        )
        return {"ok": True, "run_id": run_id, "duration_ms": duration_ms}
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        run_id = create_eval_run(
            case_id=case_id, analysis_type=analysis_type,
            result_summary=f"错误: {str(e)[:200]}",
            error_msg=str(e)[:1000],
            duration_ms=duration_ms,
        )
        return {"ok": False, "run_id": run_id, "error": str(e)}


@router.get("/api/eval/runs")
async def list_eval_runs_api(case_id: int = 0, limit: int = 50):
    """列出评测运行记录。"""
    return {"runs": list_eval_runs(case_id=case_id or None, limit=limit)}


@router.get("/api/eval/runs/{run_id}")
async def get_eval_run_detail_api(run_id: int):
    """获取单条运行记录详情。"""
    run = get_eval_run_detail(run_id)
    if not run:
        raise HTTPException(404, "运行记录不存在")
    return run


@router.get("/api/eval/stats")
async def get_eval_stats_api():
    """获取评测统计概览。"""
    return get_eval_stats()
