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
    create_async_task, update_async_task, get_async_task,
    get_config_float, get_config_int,
)
from models.eval import CreateEvalCaseRequest, BadCaseToEvalRequest
from routers.analysis.panorama import panorama_analysis_api
from routers.analysis.deep_dive import fund_deep_dive_api
from routers.analysis.trade_review import trade_review_api
from routers.analysis.what_if import what_if_analysis_api
from routers.analysis.diversification import portfolio_diversification_ai_summary
from routers.analysis.portfolio_ai import portfolio_ai_analysis_api
from models.portfolio import (
    PanoramaAnalysisRequest, DeepDiveRequest,
    TradeReviewRequest, WhatIfRequest, PortfolioAiAnalysisRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["eval"])

_background_tasks = set()


async def _await_portfolio_record_result(record_id: int, timeout: int = 180) -> dict:
    """等待异步持仓分析完成，供评测内部调用。"""
    from db.portfolio import get_analysis_record_status

    deadline = time.time() + timeout
    while time.time() < deadline:
        record = get_analysis_record_status(record_id)
        if not record:
            raise HTTPException(404, "分析记录不存在")
        if record.get("status") == "done":
            return {
                "id": record_id,
                "result": record.get("result_data", ""),
                "token_usage": record.get("token_usage", 0),
            }
        if record.get("status") == "error":
            raise RuntimeError(record.get("error_msg") or "分析失败")
        await asyncio.sleep(1)
    raise TimeoutError("等待异步分析完成超时")


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


@router.put("/api/eval/cases/{case_id}")
async def update_eval_case_api(case_id: int, body: dict):
    """更新评测用例。"""
    from db import update_eval_case
    allowed = {"name", "description", "analysis_type", "input_params", "expected_quality", "is_active"}
    fields = {k: v for k, v in body.items() if k in allowed and v is not None}
    if not fields:
        raise HTTPException(400, "无有效字段")
    ok = update_eval_case(case_id, **fields)
    if not ok:
        raise HTTPException(404, "评测用例不存在")
    return {"ok": True}


@router.delete("/api/eval/cases/{case_id}")
async def delete_eval_case_api(case_id: int):
    """删除评测用例。"""
    ok = delete_eval_case(case_id)
    if not ok:
        raise HTTPException(404, "评测用例不存在")
    return {"ok": True}


@router.post("/api/eval/cases/{case_id}/run")
async def run_eval_case_api(case_id: int):
    """运行单个评测用例（异步）。立即返回 task_id，后台执行。"""
    case = get_eval_case(case_id)
    if not case:
        raise HTTPException(404, "评测用例不存在")

    task_id = create_async_task("eval_case_run", caller=f"eval_case_{case_id}")
    task = asyncio.create_task(_run_eval_case_async(task_id, case_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_eval_case_async(task_id: int, case_id: int):
    """后台执行评测用例运行。"""
    try:
        result = await _do_eval_case_run(case_id)
        update_async_task(task_id, status="done", result=result)
    except Exception as e:
        logging.error(f"评测用例运行异步任务失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _do_eval_case_run(case_id: int):
    """评测用例运行业务逻辑。"""
    case = get_eval_case(case_id)
    if not case:
        raise HTTPException(404, "评测用例不存在")

    input_params = json.loads(case["input_params"] or "{}")
    analysis_type = case["analysis_type"]
    start = time.time()

    try:
        # 专家 Agent 类型：直接调用对应的 specialist
        specialist_types = {"valuation_expert", "market_analyst", "risk_assessor",
                            "allocation_advisor", "fund_analyst", "orchestrator"}

        if analysis_type in specialist_types:
            question = input_params.get("question", "")
            if not question:
                raise HTTPException(400, f"{analysis_type} 需要 question 参数")

            from agent.multi_agent import run_specialist
            from db.agents import load_specialist_agents

            specialists = load_specialist_agents()
            if analysis_type == "orchestrator":
                # orchestrator 模式：调用完整编排流程
                from agent.orchestrator import orchestrate
                result = await asyncio.to_thread(lambda: orchestrate(question, []))
                result_summary = result.get("answer", "")[:500]
                result_data = json.dumps(result, ensure_ascii=False)
            elif analysis_type in specialists:
                # 单个专家 Agent
                result = await asyncio.to_thread(
                    lambda: run_specialist(analysis_type, question)
                )
                result_summary = result.get("analysis", "")[:500]
                result_data = json.dumps(result, ensure_ascii=False)
            else:
                raise HTTPException(400, f"未找到专家: {analysis_type}")

        elif analysis_type == "panorama":
            result = await panorama_analysis_api(PanoramaAnalysisRequest())
            if isinstance(result, StreamingResponse):
                result_summary = "流式输出（已在后台执行）"
                result_data = "{}"
            else:
                result_summary = json.dumps(result, ensure_ascii=False)[:500]
                result_data = json.dumps(result, ensure_ascii=False)
        elif analysis_type == "deep_dive":
            holding_id = input_params.get("holding_id")
            if not holding_id:
                raise HTTPException(400, "deep_dive 需要 holding_id 参数")
            result = await fund_deep_dive_api(holding_id, DeepDiveRequest())
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)
        elif analysis_type == "trade_review":
            result = await trade_review_api(TradeReviewRequest(
                start_date=input_params.get("start_date", ""),
                end_date=input_params.get("end_date", ""),
            ))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)
        elif analysis_type == "what_if":
            result = await what_if_analysis_api(WhatIfRequest(
                scenario=input_params.get("scenario", ""),
                parameter=input_params.get("parameter", ""),
            ))
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)
        elif analysis_type == "diversification_ai":
            result = await portfolio_diversification_ai_summary()
            if isinstance(result, dict) and result.get("status") == "running":
                record_id = result.get("record_id") or result.get("id")
                if record_id:
                    result = await _await_portfolio_record_result(record_id)
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)
        elif analysis_type == "ai":
            question = input_params.get("question", "")
            result = await portfolio_ai_analysis_api(PortfolioAiAnalysisRequest(question=question))
            if isinstance(result, dict) and result.get("status") == "running":
                record_id = result.get("record_id") or result.get("id")
                if record_id:
                    result = await _await_portfolio_record_result(record_id)
            result_summary = json.dumps(result, ensure_ascii=False)[:500]
            result_data = json.dumps(result, ensure_ascii=False)
        else:
            raise HTTPException(400, f"不支持的分析类型: {analysis_type}")

        duration_ms = int((time.time() - start) * 1000)
        # 提取 token_usage
        tokens = 0
        if isinstance(result, dict):
            tokens = result.get("token_usage", 0)
        run_id = create_eval_run(
            case_id=case_id, analysis_type=analysis_type,
            result_summary=result_summary,
            result_data=result_data,
            duration_ms=duration_ms,
            token_usage=tokens,
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


@router.post("/api/eval/cases/batch-from-bad-cases")
async def batch_convert_bad_cases():
    """批量将所有 Bad Case 转化为 Eval Case（跳过已转化的）。"""
    all_bad = list_all_bad_cases(limit=500)
    existing = list_eval_cases(active_only=False)
    # 用描述中的 source:id 去重
    existing_descs = {c.get("description", "") for c in existing}

    converted = []
    skipped = 0

    for bc in all_bad:
        source = bc.get("source", "chat")
        source_id = bc.get("id")
        desc_marker = f"来源: {source} | 原始ID: {source_id}"
        if desc_marker in existing_descs:
            skipped += 1
            continue

        # 确定分析类型
        analysis_type = bc.get("type", "ai")
        if analysis_type not in ("panorama", "deep_dive", "trade_review", "what_if",
                                  "diversification_ai", "ai", "orchestrator"):
            analysis_type = "ai"

        # 构建 input_params
        input_data = bc.get("input", "")
        if source == "analysis":
            try:
                parsed = json.loads(input_data) if input_data else {}
                input_params = json.dumps(parsed, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                input_params = json.dumps({"raw_input": str(input_data)[:500]}, ensure_ascii=False)
        else:
            input_params = json.dumps({"question": str(input_data)[:500]}, ensure_ascii=False)

        # 生成期望质量标准（用简单规则，避免大量 LLM 调用）
        note = bc.get("note", "")
        if "无用" in note or "没用" in note:
            expected_quality = "1. 分析必须结合具体数据，不能泛泛而谈\n2. 必须给出可操作的建议\n3. 必须包含风险提示"
        elif "错误" in note or "不准" in note:
            expected_quality = "1. 数据必须准确，引用最新估值/净值\n2. 计算必须正确\n3. 结论必须有数据支撑"
        else:
            expected_quality = "1. 分析必须专业、准确、有数据支撑\n2. 必须给出可操作建议\n3. 必须包含风险提示"

        # 生成名称
        type_label = {
            "panorama": "全景诊断", "deep_dive": "基金深度",
            "trade_review": "交易复盘", "what_if": "情景推演",
            "ai": "AI分析", "diversification_ai": "分散度",
            "orchestrator": "对话分析",
        }.get(analysis_type, analysis_type)
        input_preview = str(input_data)[:30].replace("\n", " ")
        name = f"BC-{type_label}-{input_preview}"

        case_id = create_eval_case(
            name=name,
            analysis_type=analysis_type,
            input_params=input_params,
            description=f"从 Bad Case 批量转化 | {desc_marker}",
            expected_quality=expected_quality,
        )
        converted.append({"case_id": case_id, "name": name, "source": source, "source_id": source_id})

    return {"ok": True, "converted": len(converted), "skipped": skipped, "cases": converted}


@router.post("/api/eval/cases/auto-generate")
async def auto_generate_eval_cases(min_score: float = 8.0, limit: int = 50):
    """自动生成 Eval Case（正例 + 负例）。

    正例：高分对话（overall_score >= min_score）
    负例：Bad Case（rating = unhelpful）
    """
    from auto_eval_generator import auto_generate_eval_cases
    result = auto_generate_eval_cases(min_score=min_score, limit=limit)
    return result


@router.post("/api/eval/cases/from-conversations")
async def extract_eval_from_conversations(limit: int = 20):
    """从高价值对话中自动提取 Eval Case。

    选取标准：对话有明确投资主题 + AI 回复较完整 + 用户有后续反馈。
    """
    from db._conn import _get_conn

    conn = _get_conn()
    # 选取有投资决策关键词的对话
    rows = conn.execute("""
        SELECT c.id as conv_id, c.title, m.content, m.role
        FROM conversations c
        JOIN messages m ON m.conversation_id = c.id
        WHERE m.role = 'user'
        AND (m.content LIKE '%买%' OR m.content LIKE '%卖%' OR m.content LIKE '%加仓%'
             OR m.content LIKE '%减仓%' OR m.content LIKE '%定投%' OR m.content LIKE '%配置%'
             OR m.content LIKE '%估值%' OR m.content LIKE '%风险%' OR m.content LIKE '%基金%')
        ORDER BY c.updated_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    existing = list_eval_cases(active_only=False)
    existing_inputs = set()
    for c in existing:
        try:
            params = json.loads(c.get("input_params", "{}"))
            q = params.get("question", "")
            if q:
                existing_inputs.add(q[:50])
        except Exception:
            pass

    converted = []
    for row in rows:
        question = (row["content"] or "").strip()
        if not question or len(question) < 10:
            continue
        # 去重
        if question[:50] in existing_inputs:
            continue

        input_params = json.dumps({
            "question": question[:500],
            "conversation_id": row["conv_id"],
        }, ensure_ascii=False)

        # 用 LLM 生成期望质量
        bad_case_stub = {
            "type": "orchestrator",
            "input": question,
            "output": "",
            "note": "从真实对话提取",
        }
        expected_quality = await _generate_expected_quality(bad_case_stub)

        title_preview = (row["title"] or question)[:30].replace("\n", " ")
        name = f"对话-{title_preview}"

        case_id = create_eval_case(
            name=name,
            analysis_type="orchestrator",
            input_params=input_params,
            description=f"从真实对话提取 | conv_id={row['conv_id']}",
            expected_quality=expected_quality,
        )
        converted.append({"case_id": case_id, "name": name, "conv_id": row["conv_id"]})

    return {"ok": True, "converted": len(converted), "cases": converted}


async def _generate_expected_quality(bad_case: dict) -> str:
    """用 LLM 从 bad case 的反馈中生成期望质量标准。"""
    feedback_note = bad_case.get("note", "")
    output_preview = (bad_case.get("output") or "")[:1000]
    analysis_type = bad_case.get("type", "")

    if not feedback_note and not output_preview:
        return "专业、准确、可操作的投资分析"

    try:
        from llm_service import _call_llm, call_llm_async, MODEL
        prompt = f"""你是投资分析质量标准制定专家。根据以下 Bad Case 信息，生成一条期望质量标准（1-2句话）。

分析类型：{analysis_type}
用户反馈的问题：{feedback_note or '未说明'}
原输出摘要：{output_preview[:500]}

要求：
1. 描述用户期望的输出质量（而非批评现有输出）
2. 具体、可衡量（如"必须包含风险提示"而非"要更好"）
3. 只输出质量标准文本，不要其他内容"""

        resp = await call_llm_async(
            caller="eval_scorer",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=get_config_float('llm.temperature_eval', 0.3),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 200),
        )
        msg = resp.choices[0].message
        result = (msg.content or "").strip()
        if not result and hasattr(msg, "reasoning_content") and msg.reasoning_content:
            result = msg.reasoning_content.strip()
        return result if result else "专业、准确、可操作的投资分析"
    except Exception as e:
        logger.warning(f"生成期望质量标准失败: {e}")
        return "专业、准确、可操作的投资分析"


# ── 质量评估 API ──────────────────────────────────────

@router.post("/api/eval/run-suite")
async def run_eval_suite_api(limit_per_category: int = None):
    """运行分类评测套件（60+ 用例，四大类）。

    参数:
        limit_per_category: 每个类别最多运行的用例数，None 则全部运行
    """
    from scripts.rag_eval_suite import run_eval_suite_by_category

    try:
        results = await run_eval_suite_by_category(
            limit=limit_per_category, verbose=False
        )
        return {"ok": True, "results": results}
    except Exception as e:
        logger.error(f"评测套件运行失败: {e}", exc_info=True)
        raise HTTPException(500, f"评测套件运行失败: {str(e)}")


@router.get("/api/eval/quality-summary")
async def quality_summary_api(days: int = 30):
    """获取质量评分概览（合并 llm_feedback + eval_runs）。"""
    from db import get_quality_summary, _get_conn

    # llm_feedback 数据
    fb = get_quality_summary(days)

    # eval_runs 数据
    conn = _get_conn()
    eval_row = conn.execute("""
        SELECT
            COUNT(*) as total_runs,
            COUNT(CASE WHEN score > 0 THEN 1 END) as scored_runs,
            COALESCE(AVG(CASE WHEN score > 0 THEN score END), 0) as avg_score,
            COUNT(CASE WHEN score >= 7 THEN 1 END) as good_count,
            COUNT(CASE WHEN score > 0 AND score < 5 THEN 1 END) as bad_count
        FROM eval_runs
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    conn.close()

    eval_data = dict(eval_row) if eval_row else {}

    return {
        "total_feedback": fb.get("total_feedback", 0) + eval_data.get("total_runs", 0),
        "scored_count": fb.get("scored_count", 0) + eval_data.get("scored_runs", 0),
        "avg_overall": eval_data.get("avg_score", 0) or fb.get("avg_overall", 0),
        "avg_data_accuracy": fb.get("avg_data_accuracy", 0),
        "avg_logic": fb.get("avg_logic", 0),
        "avg_actionability": fb.get("avg_actionability", 0),
        "low_quality_count": fb.get("low_quality_count", 0) + eval_data.get("bad_count", 0),
        "eval_avg_score": eval_data.get("avg_score", 0),
        "eval_good_count": eval_data.get("good_count", 0),
        "eval_bad_count": eval_data.get("bad_count", 0),
    }


@router.get("/api/eval/quality-trend")
async def quality_trend_api(days: int = 30):
    """获取按天的质量评分趋势。"""
    from db import get_quality_trend
    return {"trend": get_quality_trend(days)}


@router.get("/api/eval/low-quality")
async def low_quality_api(limit: int = 20):
    """获取低分产出列表（bad cases）。"""
    from db import get_low_quality_items
    return {"items": get_low_quality_items(limit)}


@router.get("/api/eval/stats-by-agent")
async def stats_by_agent_api():
    """按 Agent 类型分组的评测统计。"""
    from db import _get_conn
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            c.analysis_type,
            COUNT(DISTINCT c.id) as case_count,
            COUNT(r.id) as run_count,
            COALESCE(AVG(r.score), 0) as avg_score,
            COALESCE(MIN(r.score), 0) as min_score,
            COALESCE(MAX(r.score), 0) as max_score,
            COUNT(CASE WHEN r.score >= 7 THEN 1 END) as good_count,
            COUNT(CASE WHEN r.score < 5 THEN 1 END) as bad_count
        FROM eval_cases c
        LEFT JOIN eval_runs r ON c.id = r.case_id
        GROUP BY c.analysis_type
        ORDER BY avg_score DESC
    """).fetchall()
    conn.close()

    # Agent 名称映射
    agent_names = {
        "valuation_expert": "估值分析师",
        "market_analyst": "择时分析师",
        "risk_assessor": "风险管理师",
        "allocation_advisor": "资产配置师",
        "fund_analyst": "基金分析师",
        "orchestrator": "编排器（多Agent）",
        "ai": "AI 分析",
        "panorama": "全景诊断",
        "deep_dive": "单基金深度",
        "trade_review": "交易复盘",
        "what_if": "情景推演",
        "diversification_ai": "分散度分析",
    }

    result = []
    for row in rows:
        r = dict(row)
        r["agent_name"] = agent_names.get(r["analysis_type"], r["analysis_type"])
        result.append(r)

    return {"agents": result}


# ── 对话质量评估 API ──────────────────────────────────────

@router.post("/api/eval/conversation/{conversation_id}")
async def evaluate_conversation_api(conversation_id: int):
    """自动评估对话质量"""
    from agent.conversation_evaluator import get_evaluator
    from db.eval import (
        create_conversation_evaluation, save_eval_details,
        get_conversation_evaluation,
    )

    # 检查是否已有评估
    existing = get_conversation_evaluation(conversation_id)
    if existing and existing.get("auto_score"):
        return {"ok": True, "evaluation": existing, "cached": True}

    try:
        evaluator = get_evaluator()
        result = evaluator.evaluate(conversation_id)

        # 保存评估结果
        eval_id = create_conversation_evaluation(
            conversation_id=conversation_id,
            message_id=result.message_id,
            auto_score=result.auto_score,
            auto_score_breakdown=json.dumps(result.auto_score_breakdown, ensure_ascii=False),
            complexity=result.metadata.get("complexity", "medium"),
            specialist_count=result.metadata.get("specialist_count", 0),
            duration_ms=result.metadata.get("duration_ms", 0),
            has_cross_review=result.metadata.get("has_cross_review", False),
            has_arbitration=result.metadata.get("has_arbitration", False),
            duplicate_calls=result.metadata.get("duplicate_calls", 0),
            suggestions=json.dumps(result.suggestions, ensure_ascii=False),
        )

        # 保存评估详情
        details_to_save = []
        for dim in result.dimensions:
            for metric, value in dim.get("metrics", {}).items():
                if isinstance(value, (int, float)):
                    details_to_save.append({
                        "dimension": dim["name"],
                        "metric": metric,
                        "value": value,
                    })

        if details_to_save:
            save_eval_details(eval_id, details_to_save)

        # 等待进化处理完成（最多等待2秒）
        evolution_result = None
        try:
            import asyncio
            from agent.conversation_evolution import process_conversation_evaluation

            evolution_data = {
                "auto_score": result.auto_score,
                "auto_score_breakdown": result.auto_score_breakdown,
                "suggestions": result.suggestions,
            }

            # 直接调用并等待结果
            evolution_result = await asyncio.wait_for(
                process_conversation_evaluation(conversation_id, evolution_data),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            logger.info("进化处理超时，已在后台继续执行")
        except Exception as e:
            logger.warning(f"进化处理失败: {e}")

        return {
            "ok": True,
            "evaluation": {
                "id": eval_id,
                "auto_score": result.auto_score,
                "auto_score_breakdown": result.auto_score_breakdown,
                "dimensions": result.dimensions,
                "metadata": result.metadata,
                "suggestions": result.suggestions,
            },
            "evolution": evolution_result,
        }

    except Exception as e:
        logger.error(f"对话评估失败: {e}", exc_info=True)
        raise HTTPException(500, f"评估失败: {str(e)}")


@router.get("/api/eval/conversation/{conversation_id}")
async def get_conversation_evaluation_api(conversation_id: int, message_id: int = None):
    """获取对话评估结果

    参数:
        conversation_id: 对话 ID
        message_id: 消息 ID（可选，如果指定则返回该消息的评估）
    """
    from db.eval import get_conversation_evaluation

    evaluation = get_conversation_evaluation(conversation_id, message_id)
    if not evaluation:
        return {"ok": True, "evaluation": None}

    return {"ok": True, "evaluation": evaluation}


@router.post("/api/eval/conversation/{conversation_id}/llm")
async def evaluate_conversation_with_llm_api(conversation_id: int, message_id: int = None):
    """使用 LLM 进行智能评估"""
    from agent.conversation_evaluator import evaluate_with_llm

    try:
        result = await evaluate_with_llm(conversation_id, message_id)
        if "error" in result:
            raise HTTPException(400, result["error"])
        return {"ok": True, "evaluation": result}
    except Exception as e:
        logger.error(f"LLM 评估失败: {e}", exc_info=True)
        raise HTTPException(500, f"评估失败: {str(e)}")

    evaluation = get_conversation_evaluation(conversation_id, message_id)
    if not evaluation:
        return {"ok": True, "evaluation": None}

    return {"ok": True, "evaluation": evaluation}


@router.post("/api/eval/conversation/{conversation_id}/user-score")
async def submit_conversation_user_score_api(conversation_id: int, body: dict):
    """提交用户对对话的评分"""
    from db.eval import (
        get_conversation_evaluation, update_conversation_evaluation_user_score,
    )

    evaluation = get_conversation_evaluation(conversation_id)
    if not evaluation:
        raise HTTPException(404, "请先运行自动评估")

    user_score = body.get("score")
    if user_score is None or not (0 <= user_score <= 5):
        raise HTTPException(400, "评分必须在 0-5 之间")

    breakdown = body.get("breakdown", {})
    comment = body.get("comment", "")

    update_conversation_evaluation_user_score(
        eval_id=evaluation["id"],
        user_score=user_score,
        user_score_breakdown=json.dumps(breakdown, ensure_ascii=False),
        user_comment=comment,
    )

    return {"ok": True}


@router.get("/api/eval/conversation-stats")
async def conversation_eval_stats_api():
    """获取对话评估统计"""
    from db.eval import get_conversation_eval_stats
    return {"ok": True, "stats": get_conversation_eval_stats()}


@router.get("/api/eval/conversation-list")
async def conversation_eval_list_api(limit: int = 50, min_score: float = None):
    """列出对话评估记录"""
    from db.eval import list_conversation_evaluations
    evaluations = list_conversation_evaluations(limit=limit, min_score=min_score)
    return {"ok": True, "evaluations": evaluations}


# ── 进化系统 API ──────────────────────────────────────

@router.get("/api/eval/evolution-stats")
async def evolution_stats_api(days: int = 30):
    """获取进化效果统计"""
    from agent.conversation_evolution import get_evolution_stats
    return {"ok": True, "stats": get_evolution_stats(days)}


@router.get("/api/eval/suggestions")
async def list_eval_suggestions_api(status: str = None, limit: int = 50):
    """获取评估建议（高分对话转化为 Eval 用例）"""
    from agent.conversation_evolution import get_eval_suggestions
    return {"ok": True, "suggestions": get_eval_suggestions(status=status, limit=limit)}


@router.post("/api/eval/suggestions/{suggestion_id}/accept")
async def accept_eval_suggestion_api(suggestion_id: int):
    """接受评估建议，转化为 Eval 用例"""
    from agent.conversation_evolution import get_eval_suggestions, update_eval_suggestion_status
    from db.eval import create_eval_case

    # 获取建议
    suggestions = get_eval_suggestions()
    suggestion = None
    for s in suggestions:
        if s["id"] == suggestion_id:
            suggestion = s
            break

    if not suggestion:
        raise HTTPException(404, "建议不存在")

    # 创建 Eval 用例
    case_id = create_eval_case(
        name=suggestion["name"],
        analysis_type=suggestion["analysis_type"],
        input_params=suggestion["input_params"],
        description=f"从对话 {suggestion['conversation_id']} 转化（评分: {suggestion['auto_score']:.0f}）",
        expected_quality=suggestion["expected_quality"] or "",
    )

    # 更新建议状态
    update_eval_suggestion_status(suggestion_id, "accepted")

    return {"ok": True, "case_id": case_id}


@router.post("/api/eval/suggestions/{suggestion_id}/reject")
async def reject_eval_suggestion_api(suggestion_id: int):
    """拒绝评估建议"""
    from agent.conversation_evolution import update_eval_suggestion_status
    update_eval_suggestion_status(suggestion_id, "rejected")
    return {"ok": True}


@router.get("/api/eval/expert-alerts")
async def expert_alerts_api(days: int = 7, limit: int = 50):
    """获取专家表现告警"""
    from agent.conversation_evolution import get_expert_alerts
    return {"ok": True, "alerts": get_expert_alerts(days=days, limit=limit)}


# ── LLM 评估 Agent API ──────────────────────────────────────

@router.post("/api/eval/llm")
async def evaluate_with_llm_agent_api(
    target_type: str,
    target_id: int,
    message_id: int = None,
):
    """使用 LLM 评估 Agent 进行智能评估

    参数:
        target_type: 评估场景（conversation/daily_report/hot_topics/portfolio）
        target_id: 目标 ID
        message_id: 消息 ID（对话场景可选）
    """
    import time
    from db.eval import save_llm_evaluation, get_llm_evaluation

    # 检查是否已有评估
    existing = get_llm_evaluation(target_type, target_id, message_id)
    if existing and existing.get("total_score"):
        return {"ok": True, "evaluation": existing, "cached": True}

    start_time = time.time()

    try:
        if target_type == "conversation":
            from agent.llm_evaluator_agent import evaluate_conversation_output
            result = evaluate_conversation_output(target_id, message_id)
        elif target_type == "daily_report":
            from agent.llm_evaluator_agent import evaluate_daily_report_output
            # 从数据库获取日报内容
            from db.dashboard import get_daily_report
            report = get_daily_report(target_id)
            if not report:
                raise HTTPException(404, "日报不存在")
            result = evaluate_daily_report_output(report.get("content", ""))
        elif target_type == "hot_topics":
            from agent.llm_evaluator_agent import evaluate_hot_topics_output
            # 从数据库获取热点内容
            from db.dashboard import get_hot_topics
            topics = get_hot_topics(target_id)
            if not topics:
                raise HTTPException(404, "热点不存在")
            result = evaluate_hot_topics_output(topics.get("content", ""))
        elif target_type == "portfolio":
            from agent.llm_evaluator_agent import evaluate_portfolio_output
            # 从数据库获取持仓分析
            from db.portfolio import get_portfolio_analysis
            analysis = get_portfolio_analysis(target_id)
            if not analysis:
                raise HTTPException(404, "持仓分析不存在")
            result = evaluate_portfolio_output(analysis.get("content", ""))
        else:
            raise HTTPException(400, f"不支持的评估类型: {target_type}")

        if "error" in result:
            raise HTTPException(400, result["error"])

        duration_ms = int((time.time() - start_time) * 1000)

        # 保存评估结果
        eval_id = save_llm_evaluation(
            target_type=target_type,
            target_id=target_id,
            message_id=message_id,
            total_score=result.get("total_score", 0),
            dimensions=result.get("dimensions", {}),
            strengths=result.get("strengths", []),
            weaknesses=result.get("weaknesses", []),
            suggestions=result.get("suggestions", []),
            user_preference_hints=result.get("user_preference_hints", []),
            duration_ms=duration_ms,
        )

        return {
            "ok": True,
            "evaluation": {
                "id": eval_id,
                **result,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LLM 评估失败: {e}", exc_info=True)
        raise HTTPException(500, f"评估失败: {str(e)}")


@router.get("/api/eval/llm/{target_type}/{target_id}")
async def get_llm_evaluation_api(target_type: str, target_id: int, message_id: int = None):
    """获取 LLM 评估结果"""
    from db.eval import get_llm_evaluation

    evaluation = get_llm_evaluation(target_type, target_id, message_id)
    if not evaluation:
        return {"ok": True, "evaluation": None}

    return {"ok": True, "evaluation": evaluation}


@router.get("/api/eval/llm-stats")
async def llm_eval_stats_api(days: int = 30):
    """获取 LLM 评估统计"""
    from db.eval import get_llm_eval_stats
    return {"ok": True, "stats": get_llm_eval_stats(days)}


@router.get("/api/eval/user-insights/{user_id}")
async def user_insights_api(user_id: str = "default"):
    """获取用户偏好洞察"""
    from db.eval import get_user_preferences, get_failure_patterns

    preferences = get_user_preferences(user_id)
    failure_patterns = get_failure_patterns(limit=10)

    return {
        "ok": True,
        "insights": {
            "preferences": preferences,
            "failure_patterns": failure_patterns,
        },
    }


@router.post("/api/eval/auto-regression")
async def auto_regression_api(limit: int = 10):
    """自动回归测试：对每个用例实际运行 Agent 回答，然后用 LLM 评分。

    每条用例约 10-30 秒（含 Agent 回答 + LLM 评分），limit 默认 10 条控制总耗时。
    """
    from db import list_eval_cases, get_eval_case, create_eval_run
    from db._conn import _get_conn
    import json
    import time

    cases = list_eval_cases(active_only=True)
    if limit:
        cases = cases[:limit]

    results = []
    passed = 0
    failed = 0
    degraded = []

    for case in cases:
        case_id = case["id"]
        analysis_type = case.get("analysis_type", "ai")
        start = time.time()

        try:
            detail = get_eval_case(case_id)
            if not detail:
                results.append({"case_id": case_id, "status": "skip", "reason": "用例不存在"})
                continue

            input_params = detail.get("input_params", "{}")
            expected_quality = detail.get("expected_quality", "")

            try:
                params = json.loads(input_params) if isinstance(input_params, str) else input_params
            except (json.JSONDecodeError, TypeError):
                params = {}

            question = params.get("question", "")
            if not question:
                duration_ms = int((time.time() - start) * 1000)
                create_eval_run(case_id=case_id, analysis_type=analysis_type,
                    result_summary="无问题参数", score=0, duration_ms=duration_ms)
                results.append({"case_id": case_id, "status": "skip", "reason": "无问题"})
                continue

            # 1. 实际运行 Agent 获取回答
            answer = await _run_agent_for_eval(question)
            duration_ms = int((time.time() - start) * 1000)

            if not answer:
                create_eval_run(case_id=case_id, analysis_type=analysis_type,
                    result_summary="Agent 无回答", score=0, duration_ms=duration_ms)
                results.append({"case_id": case_id, "status": "fail", "score": 0, "reason": "Agent 无回答"})
                failed += 1
                continue

            # 2. 用 LLM 评分回答质量
            dimensions = await _score_answer_quality(question, answer[:2000], expected_quality)
            total_score = sum(dimensions.values()) / len(dimensions) if dimensions else 0

            # 保存运行结果（含完整回答）
            answer_summary = answer[:300].replace("\n", " ")
            create_eval_run(
                case_id=case_id, analysis_type=analysis_type,
                result_summary=answer_summary,
                result_data=json.dumps({"answer": answer, "dimensions": dimensions}, ensure_ascii=False),
                score=round(total_score, 1), duration_ms=duration_ms,
            )

            # 3. 退化检测
            conn = _get_conn()
            recent_runs = conn.execute("""
                SELECT score FROM eval_runs WHERE case_id = ? ORDER BY created_at DESC LIMIT 3
            """, (case_id,)).fetchall()
            conn.close()
            recent_scores = [r["score"] for r in recent_runs if r["score"] is not None]
            is_degraded = len(recent_scores) >= 3 and all(s < 4 for s in recent_scores)
            status = "degraded" if is_degraded else ("pass" if total_score >= 5 else "fail")

            if is_degraded:
                degraded.append({"case_id": case_id, "name": case["name"], "scores": recent_scores})

            results.append({
                "case_id": case_id, "status": status, "score": round(total_score, 1),
                "dimensions": dimensions, "duration_ms": duration_ms,
                "answer_preview": answer[:100],
            })
            if total_score >= 5:
                passed += 1
            else:
                failed += 1

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            create_eval_run(case_id=case_id, analysis_type=analysis_type,
                result_summary=f"异常: {str(e)[:200]}", score=0,
                duration_ms=duration_ms, error_msg=str(e)[:500])
            results.append({"case_id": case_id, "status": "error", "error": str(e)[:200]})
            failed += 1

    if degraded:
        logging.warning(f"检测到 {len(degraded)} 个退化用例: {[d['name'] for d in degraded]}")

    return {
        "ok": True, "total": len(cases), "passed": passed, "failed": failed,
        "degraded_count": len(degraded), "degraded": degraded, "results": results,
    }


async def _run_agent_for_eval(question: str) -> str:
    """运行 Agent 回答评测问题（简化版，直接调 LLM + RAG）。"""
    try:
        from llm_service import _call_llm, call_llm_async, MODEL
        from rag import build_rag_context_with_details

        # 获取 RAG 上下文
        rag_result = build_rag_context_with_details(query=question, limit=5)
        rag_context = rag_result.get("context", "")

        # 构建 prompt
        system = "你是一位专业的投资分析助手。请根据提供的知识库资料，给出专业、准确、可操作的投资分析。必须引用具体数据，包含风险提示。"
        user_msg = question
        if rag_context:
            user_msg = f"参考资料：\n{rag_context[:2000]}\n\n用户问题：{question}"

        resp = await call_llm_async(
            caller="eval_runner", model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.3), max_tokens=get_config_int('llm.max_tokens_eval', 2000),
        )

        msg = resp.choices[0].message
        content = (msg.content or "").strip()
        if not content and hasattr(msg, "reasoning_content"):
            content = (msg.reasoning_content or "").strip()
        return content

    except Exception as e:
        logging.error(f"Agent 评测运行失败: {e}")
        return ""


async def _score_answer_quality(question: str, answer: str, expected_quality: str) -> dict:
    """用 LLM 对 Agent 回答质量做多维度评分。"""
    try:
        from llm_service import _call_llm, call_llm_async, MODEL

        prompt = f"""请对以下投资分析回答评分，返回JSON格式。

用户问题：{question[:300]}
AI回答：{answer[:1000]}
质量标准：{expected_quality[:300]}

评分（0-10整数）：data_accuracy(数据准确性), logic(逻辑性), actionability(可操作性), risk_awareness(风险意识)

直接返回JSON，如 {{"data_accuracy":8,"logic":7,"actionability":6,"risk_awareness":5}}"""

        resp = await call_llm_async(
            caller="eval_scorer", model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=get_config_float('llm.temperature_eval', 0.2), max_tokens=get_config_int('llm.max_tokens_eval_score', 500),
        )

        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        if not text and hasattr(msg, "reasoning_content"):
            text = (msg.reasoning_content or "").strip()

        import re
        # 匹配包含实际数字的 JSON（不是"分数"这种模板文字）
        match = re.search(r'\{[^{}]*\d+[^{}]*\}', text)
        if match:
            try:
                parsed = json.loads(match.group())
                return {
                    "data_accuracy": min(10, max(0, int(parsed.get("data_accuracy", 5)))),
                    "logic": min(10, max(0, int(parsed.get("logic", 5)))),
                    "actionability": min(10, max(0, int(parsed.get("actionability", 5)))),
                    "risk_awareness": min(10, max(0, int(parsed.get("risk_awareness", 5)))),
                }
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: 从 reasoning 中提取数字模式
        scores = re.findall(r'(?:data_accuracy|logic|actionability|risk_awareness)[：:]\s*(\d+)', text)
        if len(scores) >= 4:
            return {
                "data_accuracy": min(10, int(scores[0])),
                "logic": min(10, int(scores[1])),
                "actionability": min(10, int(scores[2])),
                "risk_awareness": min(10, int(scores[3])),
            }

        # Fallback: 从回答内容做规则评分
        return _rule_score_answer(answer, expected_quality)

    except Exception as e:
        logging.warning(f"回答质量评分失败: {e}")
        return _rule_score_answer(answer, expected_quality)


def _rule_score_answer(answer: str, expected_quality: str) -> dict:
    """基于规则的回答质量评分（LLM 失败时的 fallback）。"""
    a = answer.lower()
    eq = expected_quality.lower()

    # 数据准确性
    data_hits = sum(1 for kw in ["pe", "pb", "净值", "收益率", "百分位", "亿", "元", "%", "点"] if kw in a)
    data_score = min(10, 3 + data_hits * 2)

    # 逻辑性
    logic_hits = sum(1 for kw in ["因为", "所以", "首先", "其次", "因此", "基于", "分析", "对比"] if kw in a)
    logic_score = min(10, 3 + logic_hits * 2)

    # 可操作性
    action_hits = sum(1 for kw in ["建议", "可以", "买入", "卖出", "持有", "配置", "比例", "仓位", "分批"] if kw in a)
    action_score = min(10, 3 + action_hits * 2)

    # 风险意识
    risk_hits = sum(1 for kw in ["风险", "注意", "谨慎", "回撤", "波动", "不确定", "止损", "控制"] if kw in a)
    risk_score = min(10, 3 + risk_hits * 2)

    return {
        "data_accuracy": data_score,
        "logic": logic_score,
        "actionability": action_score,
        "risk_awareness": risk_score,
    }
