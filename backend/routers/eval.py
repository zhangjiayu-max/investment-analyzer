"""评测集路由 — /api/eval/*"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from db import (
    create_eval_case, list_eval_cases, get_eval_case, delete_eval_case,
    create_eval_run, update_eval_run, list_eval_runs, get_eval_run_detail,
    get_eval_stats, list_all_bad_cases,
)
from models.eval import CreateEvalCaseRequest, BadCaseToEvalRequest
from routers.portfolio import (
    panorama_analysis_api, fund_deep_dive_api,
    trade_review_api, what_if_analysis_api,
    portfolio_diversification_ai_summary, portfolio_ai_analysis_api,
)
from models.portfolio import (
    PanoramaAnalysisRequest, DeepDiveRequest,
    TradeReviewRequest, WhatIfRequest, PortfolioAiAnalysisRequest,
)

logger = logging.getLogger(__name__)

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
    """运行单个评测用例：调用对应的分析模式，记录结果，自动评分。"""
    case = get_eval_case(case_id)
    if not case:
        raise HTTPException(404, "评测用例不存在")

    input_params = json.loads(case["input_params"] or "{}")
    analysis_type = case["analysis_type"]
    start = time.time()

    try:
        if analysis_type == "panorama":
            result = await panorama_analysis_api(PanoramaAnalysisRequest())
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

        # 异步触发自动评分（不阻塞返回）
        asyncio.create_task(_score_run_async(run_id, case, result_data, analysis_type))

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


async def _score_run_async(run_id: int, case: dict, result_data: str, analysis_type: str):
    """异步评分：用 LLM-as-Judge 对运行结果打分。"""
    try:
        from agent.eval_scorer import score_eval_result
        expected_quality = case.get("expected_quality", "")
        score, reason = await score_eval_result(expected_quality, result_data, analysis_type)
        update_eval_run(run_id, score=score, score_reason=reason)
        logger.info(f"Eval run {run_id} 评分完成: {score}分 — {reason}")
    except Exception as e:
        logger.error(f"Eval run {run_id} 评分失败: {e}")


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


@router.post("/api/eval/cases/from-bad-case")
async def create_eval_from_bad_case(req: BadCaseToEvalRequest):
    """从 Bad Case 转化为 Eval Case：自动提取输入/输出，用 LLM 生成期望质量标准。"""
    # 1. 查询原始 bad case 数据
    all_cases = list_all_bad_cases(limit=500)
    bad_case = None
    for c in all_cases:
        if c["source"] == req.source and c["id"] == req.source_id:
            bad_case = c
            break

    if not bad_case:
        raise HTTPException(404, "Bad Case 不存在")

    # 2. 确定分析类型
    analysis_type = bad_case.get("type", "ai")
    if analysis_type not in ("panorama", "deep_dive", "trade_review", "what_if",
                              "diversification_ai", "ai"):
        analysis_type = "ai"

    # 3. 构建 input_params
    input_data = bad_case.get("input", "")
    if req.source == "analysis":
        # 分析记录：尝试从 input_data 解析 JSON
        try:
            parsed = json.loads(input_data) if input_data else {}
            input_params = json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            input_params = json.dumps({"raw_input": str(input_data)[:500]}, ensure_ascii=False)
    else:
        # 对话反馈：用 question 字段
        input_params = json.dumps({"question": str(input_data)[:500]}, ensure_ascii=False)

    # 4. 用 LLM 生成期望质量标准
    expected_quality = await _generate_expected_quality(bad_case)

    # 5. 生成用例名称
    name = req.name.strip()
    if not name:
        type_label = {
            "panorama": "全景诊断", "deep_dive": "基金深度",
            "trade_review": "交易复盘", "what_if": "情景推演",
            "ai": "AI分析", "diversification_ai": "分散度",
        }.get(analysis_type, analysis_type)
        note_preview = (bad_case.get("note") or "")[:20]
        name = f"BadCase-{type_label}-{note_preview or '无备注'}"

    # 6. 创建 eval case
    case_id = create_eval_case(
        name=name,
        analysis_type=analysis_type,
        input_params=input_params,
        description=f"从 Bad Case 转化 | 来源: {req.source} | 原始ID: {req.source_id}",
        expected_quality=expected_quality,
    )

    return {"ok": True, "id": case_id, "name": name, "expected_quality": expected_quality}


async def _generate_expected_quality(bad_case: dict) -> str:
    """用 LLM 从 bad case 的反馈中生成期望质量标准。"""
    feedback_note = bad_case.get("note", "")
    output_preview = (bad_case.get("output") or "")[:1000]
    analysis_type = bad_case.get("type", "")

    if not feedback_note and not output_preview:
        return "专业、准确、可操作的投资分析"

    try:
        from llm_service import _call_llm, MODEL
        prompt = f"""你是投资分析质量标准制定专家。根据以下 Bad Case 信息，生成一条期望质量标准（1-2句话）。

分析类型：{analysis_type}
用户反馈的问题：{feedback_note or '未说明'}
原输出摘要：{output_preview[:500]}

要求：
1. 描述用户期望的输出质量（而非批评现有输出）
2. 具体、可衡量（如"必须包含风险提示"而非"要更好"）
3. 只输出质量标准文本，不要其他内容"""

        resp = _call_llm(
            caller="eval_scorer",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
        )
        result = (resp.choices[0].message.content or "").strip()
        return result if result else "专业、准确、可操作的投资分析"
    except Exception as e:
        logger.warning(f"生成期望质量标准失败: {e}")
        return "专业、准确、可操作的投资分析"
