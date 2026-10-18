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
from models.eval import CreateEvalCaseRequest, UpdateEvalCaseRequest, BadCaseToEvalRequest
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
async def list_eval_cases_api(analysis_type: str = "", active_only: bool = True,
                              page: int = None, page_size: int = 20):
    """列出评测用例。传入 page 时返回分页结构，否则返回原格式（向后兼容）。"""
    result = list_eval_cases(
        analysis_type=analysis_type or None, active_only=active_only,
        page=page, page_size=page_size,
    )
    if page is not None:
        return {
            "cases": result["items"],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }
    return {"cases": result}


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
async def update_eval_case_api(case_id: int, body: UpdateEvalCaseRequest):
    """更新评测用例。"""
    from db import update_eval_case
    allowed = {"name", "description", "analysis_type", "input_params", "expected_quality", "is_active"}
    fields = body.model_dump(exclude_none=True)
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

    from db.eval import claim_governance_task
    submitted = claim_governance_task("eval_case_run", f"eval_case_{case_id}", {"case_id":case_id})
    if not submitted["reused"]:
        task = asyncio.create_task(_run_eval_case_async(submitted["task_id"],case_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return submitted


async def _run_eval_case_async(task_id: int, case_id: int):
    """后台执行评测用例运行。"""
    try:
        result = await _do_eval_case_run(case_id)
        update_async_task(task_id, status="done", result=result)
    except Exception as e:
        logging.error(f"评测用例运行异步任务失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _do_eval_case_run(case_id: int):
    """执行真实入口、等待最终输出、完成评分后才将评测任务终结。"""
    from services.quality.eval_execution import execute_case, production_prompt, UnsupportedEvaluation
    case = get_eval_case(case_id)
    if not case:
        raise HTTPException(404,"评测用例不存在")
    kind = case["analysis_type"]
    start = time.monotonic()
    run_id = None
    try:
        # 在执行前冻结用例和生效提示词，避免执行期间编辑改变历史依据。
        prompt = production_prompt(kind)
        snapshot = {**case,"production_prompt":prompt}
        result = await execute_case(case, prompt_override=prompt if prompt else None)
        data = json.dumps(result,ensure_ascii=False)
        run_id = create_eval_run(case_id,kind,str(result["result"])[:500],result_data=data,
                                 duration_ms=int((time.monotonic()-start)*1000),
                                 token_usage=result.get("token_usage",0),case_snapshot=snapshot)
        score_state = await _score_run_async(run_id,case,data,kind)
        run = get_eval_run_detail(run_id)
        return {"ok":score_state == "scored","skipped":score_state == "skipped","case_id":case_id,"name":case['name'],"run_id":run_id,"score_status":score_state,
                "status":score_state,"score":run.get('score'),"duration_ms":run.get('duration_ms'),
                "error":"评分失败，请重试" if score_state == "error" else ""}
    except UnsupportedEvaluation as exc:
        run_id = create_eval_run(case_id,kind,str(exc),score_status='skipped',case_snapshot=case)
        update_eval_run(run_id,score_reason=str(exc))
        return {'ok':False,'skipped':True,'case_id':case_id,'run_id':run_id,'reason':str(exc)}
    except Exception as exc:
        error = str(getattr(exc,"detail",exc))
        if run_id is None:
            run_id = create_eval_run(case_id,kind,"执行失败",error_msg=error,
                                     duration_ms=int((time.monotonic()-start)*1000),case_snapshot=case)
        else:
            update_eval_run(run_id,score_status="error",error_msg=error)
        return {"ok":False,"run_id":run_id,"error":error}


async def _score_run_async(run_id: int, case: dict, result_data: str, analysis_type: str):
    """阻塞评分调用在线程中执行；失败与跳过不作为有效0分。"""
    try:
        from agent.eval.eval_scorer import score_eval_result
        expected = case.get("expected_quality","")
        if not expected.strip():
            update_eval_run(run_id,score_status="skipped",score_reason="未设置质量标准，已跳过评分")
            return "skipped"
        score, reason = await asyncio.to_thread(lambda: asyncio.run(score_eval_result(expected,result_data,analysis_type)))
        if score <= 0 or reason.startswith("评分失败"):
            raise RuntimeError(reason)
        update_eval_run(run_id,score=score,score_reason=reason,score_status="scored")
        return "scored"
    except Exception as exc:
        update_eval_run(run_id,score_status="error",score_reason=str(exc))
        logger.error("Eval run %s 评分失败: %s",run_id,exc)
        return "error"


async def _run_case_batch(task_id: int, case_ids: list[int]):
    results = []
    try:
        for index,cid in enumerate(case_ids):
            update_async_task(task_id,progress={"completed":index,"total":len(case_ids),"case_id":cid})
            results.append({"case_id":cid,**await _do_eval_case_run(cid)})
        passed = sum(bool(r.get('ok')) and r.get('score',0)>=5 for r in results)
        failed = sum(bool(r.get('ok')) and r.get('score',0)<5 for r in results)
        skipped = sum(bool(r.get('skipped')) for r in results)
        errors = len(results)-passed-failed-skipped
        update_async_task(task_id,status="done",result={"ok":errors==0,"total":len(results),
                          "passed":passed,"failed":failed,"errors":errors,"skipped":skipped,"results":results})
    except Exception as exc:
        update_async_task(task_id,status="error",error_msg=str(exc))


def _submit_case_batch(case_ids: list[int]):
    from db.eval import claim_governance_task
    ids = sorted(set(case_ids))
    if not ids:
        raise HTTPException(400,"没有可运行用例")
    submitted = claim_governance_task("eval_case_batch","eval_case_batch",{"case_ids":ids})
    if not submitted["reused"]:
        task = asyncio.create_task(_run_case_batch(submitted["task_id"],ids))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return submitted


@router.post("/api/eval/run-batch")
async def run_cases_batch_api(body: dict):
    ids = body.get("case_ids")
    if not isinstance(ids,list) or not ids or len(ids)>200 or any(type(i) is not int or i<=0 for i in ids):
        raise HTTPException(400,"case_ids需为1-200个正整数")
    if any(not get_eval_case(cid) for cid in ids):
        raise HTTPException(404,"评测用例不存在")
    return _submit_case_batch(ids)


@router.get("/api/eval/runs")
async def list_eval_runs_api(case_id: int = 0, limit: int = 50,
                             page: int = None, page_size: int = 20):
    """列出评测运行记录。传入 page 时返回分页结构，否则返回原格式（向后兼容）。"""
    result = list_eval_runs(
        case_id=case_id or None, limit=limit,
        page=page, page_size=page_size,
    )
    if page is not None:
        return {
            "runs": result["items"],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }
    return {"runs": result}


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
    from db.eval import get_governance_bad_case, get_eval_source, create_eval_case_from_source
    bad_case = get_governance_bad_case(req.source,req.source_id)
    if not bad_case:
        raise HTTPException(404,'Bad Case 不存在')
    existing = get_eval_source('bad_case:'+req.source,req.source_id)
    if existing:
        return {'ok':True,**existing,'created':False,'reused':True}

    # 2. 确定分析类型
    from services.quality.eval_execution import resolve_case_type
    analysis_type = resolve_case_type(bad_case.get("type", "ai"))

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
    created = create_eval_case_from_source('bad_case:'+req.source,req.source_id,
        name=name,analysis_type=analysis_type,input_params=input_params,
        description=f"从 Bad Case 转化 | 来源: {req.source} | 原始ID: {req.source_id}",expected_quality=expected_quality)
    return {"ok":True,**created,"expected_quality":expected_quality}


@router.post("/api/eval/cases/batch-from-bad-cases")
async def batch_convert_bad_cases():
    """批量将所有 Bad Case 转化为 Eval Case（跳过已转化的）。"""
    all_bad = list_all_bad_cases(limit=500)
    from db.eval import get_eval_source, create_eval_case_from_source

    converted = []
    skipped = 0

    for bc in all_bad:
        source = bc.get("source", "chat")
        source_id = bc.get("id")
        desc_marker = f"来源: {source} | 原始ID: {source_id}"
        if get_eval_source('bad_case:'+source,source_id):
            skipped += 1
            continue

        # 确定分析类型
        from services.quality.eval_execution import resolve_case_type
        analysis_type = resolve_case_type(bc.get("type", "ai"))

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

        created = create_eval_case_from_source('bad_case:'+source,source_id,
            name=name,analysis_type=analysis_type,input_params=input_params,
            description=f"从 Bad Case 批量转化 | {desc_marker}",expected_quality=expected_quality)
        if created['created']:
            converted.append({"case_id":created['id'],"name":name,"source":source,"source_id":source_id})
        else:
            skipped += 1

    return {"ok": True, "converted": len(converted), "skipped": skipped, "cases": converted}


@router.post("/api/eval/cases/auto-generate")
async def auto_generate_eval_cases(min_score: float = 8.0, limit: int = 50):
    """自动生成 Eval Case（正例 + 负例）。

    正例：高分对话（overall_score >= min_score）
    负例：Bad Case（rating = unhelpful）
    """
    from infra.auto_eval_generator import auto_generate_eval_cases
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
        from services.llm_service import _call_llm, call_llm_async, MODEL_AUX
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
            model=MODEL_AUX,
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
    from db.eval import get_governance_quality
    return get_governance_quality(days)["summary"]


@router.get("/api/eval/quality-trend")
async def quality_trend_api(days: int = 30):
    from db.eval import get_governance_quality
    return {"trend":get_governance_quality(days)["trend"]}


@router.get("/api/eval/low-quality")
async def low_quality_api(limit: int = 20, days: int = 30):
    from db.eval import get_governance_quality
    return {"items":get_governance_quality(days,limit)["low_quality"]}


@router.get("/api/eval/stats-by-agent")
async def stats_by_agent_api(days: int = 30):
    from db.eval import get_governance_quality
    return {"agents":get_governance_quality(days)["agents"]}


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
async def conversation_eval_stats_api(days: int = 30):
    """获取对话评估统计"""
    from db.eval import get_conversation_eval_stats
    return {"ok": True, "stats": get_conversation_eval_stats(days)}


@router.get("/api/eval/conversation-list")
async def conversation_eval_list_api(limit: int = 50, min_score: float = None,
                                     page: int = None, page_size: int = 20):
    """列出对话评估记录。传入 page 时返回分页结构，否则返回原格式（向后兼容）。"""
    from db.eval import list_conversation_evaluations
    result = list_conversation_evaluations(
        limit=limit, min_score=min_score, page=page, page_size=page_size,
    )
    if page is not None:
        return {
            "ok": True,
            "items": result["items"],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }
    return {"ok": True, "evaluations": result}


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
    from db.eval import accept_governance_suggestion
    try:
        return accept_governance_suggestion(suggestion_id)
    except LookupError as exc:
        raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.post("/api/eval/suggestions/{suggestion_id}/reject")
async def reject_eval_suggestion_api(suggestion_id: int):
    from db.eval import reject_governance_suggestion
    if not reject_governance_suggestion(suggestion_id):
        raise HTTPException(409,"建议不存在或已处理")
    return {"ok":True}


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
    """回归和单条评测共享真实执行器；以持久批次任务跟踪终态。"""
    if limit < 1 or limit > 200:
        raise HTTPException(400,"limit需在1-200之间")
    return _submit_case_batch([case["id"] for case in list_eval_cases(active_only=True)[:limit]])


# ── 测试套件 (Eval Suite) API ──────────────────────────────────────


@router.post("/api/eval/suites")
async def create_eval_suite_api(body: dict):
    """创建测试套件。"""
    from db.eval import create_eval_suite
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name 必填")
    suite_id = create_eval_suite(
        name=name,
        description=body.get("description", ""),
        suite_type=body.get("suite_type", "regression"),
        is_active=body.get("is_active", True),
    )
    return {"ok": True, "id": suite_id}


@router.get("/api/eval/suites")
async def list_eval_suites_api(suite_type: str = "", active_only: bool = False):
    """列出测试套件。"""
    from db.eval import list_eval_suites
    return {"suites": list_eval_suites(suite_type=suite_type or None, active_only=active_only)}


@router.get("/api/eval/suites/{suite_id}")
async def get_eval_suite_api(suite_id: int):
    """获取测试套件详情（含用例列表）。"""
    from db.eval import get_eval_suite
    suite = get_eval_suite(suite_id)
    if not suite:
        raise HTTPException(404, "测试套件不存在")
    return suite


@router.put("/api/eval/suites/{suite_id}")
async def update_eval_suite_api(suite_id: int, body: dict):
    """更新测试套件。"""
    from db.eval import update_eval_suite
    allowed = {"name", "description", "suite_type", "is_active"}
    fields = {k: v for k, v in body.items() if k in allowed and v is not None}
    if not fields:
        raise HTTPException(400, "无有效字段")
    ok = update_eval_suite(suite_id, **fields)
    if not ok:
        raise HTTPException(404, "测试套件不存在")
    return {"ok": True}


@router.delete("/api/eval/suites/{suite_id}")
async def delete_eval_suite_api(suite_id: int):
    """删除测试套件。"""
    from db.eval import delete_eval_suite
    ok = delete_eval_suite(suite_id)
    if not ok:
        raise HTTPException(404, "测试套件不存在")
    return {"ok": True}


@router.post("/api/eval/suites/{suite_id}/cases")
async def add_case_to_suite_api(suite_id: int, body: dict):
    """添加用例到套件。body: {case_id, sort_order?}"""
    from db.eval import add_case_to_suite, get_eval_suite
    if not get_eval_suite(suite_id):
        raise HTTPException(404, "测试套件不存在")
    case_id = body.get("case_id")
    if not case_id:
        raise HTTPException(400, "case_id 必填")
    sort_order = body.get("sort_order", 0) or 0
    add_case_to_suite(suite_id, case_id, sort_order=sort_order)
    return {"ok": True}


@router.delete("/api/eval/suites/{suite_id}/cases/{case_id}")
async def remove_case_from_suite_api(suite_id: int, case_id: int):
    """从套件移除用例。"""
    from db.eval import remove_case_from_suite
    ok = remove_case_from_suite(suite_id, case_id)
    if not ok:
        raise HTTPException(404, "用例不在套件中")
    return {"ok": True}


@router.post("/api/eval/suites/{suite_id}/run")
async def run_eval_suite_api(suite_id: int):
    """运行测试套件：遍历套件内用例，逐个调用现有 run 逻辑。"""
    from db.eval import get_eval_suite, list_suite_cases
    suite = get_eval_suite(suite_id)
    if not suite:
        raise HTTPException(404, "测试套件不存在")

    cases = list_suite_cases(suite_id)
    if not cases:
        raise HTTPException(400, "套件内无用例")

    return _submit_case_batch([case['id'] for case in cases])


# ── 改进任务 (Improvement Task) API ──────────────────────────────────────


@router.get("/api/eval/improvement-tasks")
async def list_improvement_tasks_api(status: str = "", limit: int = 100):
    """列出改进任务（支持 status 过滤）。"""
    from db.eval import list_improvement_tasks
    return {"tasks": list_improvement_tasks(status=status or None, limit=limit)}


@router.post("/api/eval/improvement-tasks/{task_id}/apply")
async def apply_improvement_task_api(task_id: int, body: dict = None):
    """应用改进任务（状态改 applied）。可选 body: {prompt_diff}"""
    from db.eval import update_improvement_task_status
    prompt_diff = (body or {}).get("prompt_diff") if body else None
    update_improvement_task_status(task_id, "applied", prompt_diff=prompt_diff)
    return {"ok": True}


@router.post("/api/eval/improvement-tasks/{task_id}/reject")
async def reject_improvement_task_api(task_id: int):
    """拒绝改进任务（状态改 rejected）。"""
    from db.eval import update_improvement_task_status
    update_improvement_task_status(task_id, "rejected")
    return {"ok": True}
