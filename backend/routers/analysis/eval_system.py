"""Agent 质量评测系统 — LLM Judge + 每日评测 + A/B 测试"""
import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from db import get_config, get_config_int, get_running_async_task, create_async_task, update_async_task
from infra.schemas import PromptActivateRequest
from db.eval import (
    create_eval_case, list_eval_cases, get_eval_case, update_eval_case, delete_eval_case,
    create_eval_run, list_eval_runs, get_eval_run_detail, update_eval_run,
    get_eval_stats, get_random_active_cases,
    create_prompt_version, list_prompt_versions, get_active_prompt,
    activate_prompt_version, update_prompt_scores,
    save_eval_daily_report, get_eval_daily_report, list_eval_daily_reports, get_eval_trends,
)
from services.llm_service import _call_llm, MODEL_AUX
from services.quality.eval_execution import execute_case, production_prompt, UnsupportedEvaluation, capture_data_evidence
from db.eval import claim_governance_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eval-system", tags=["eval-system"])

_background_tasks: set = set()

# ============ LLM Judge ============

JUDGE_PROMPT = """你是投资分析质量评审专家。请严格按以下维度给这段 Agent 分析输出打分。

评分维度（每项 1-5 分，5 分最佳）：

1. **数据准确性** — 引用的数据是否真实可验证，有无编造数字或基金名称
2. **建议可操作性** — 用户看完能不能直接行动，是否有具体操作步骤和金额建议
3. **风险提示** — 是否充分提示风险，有无盲目乐观或忽略潜在风险
4. **幻觉程度** — 是否编造了不存在的数据/基金/结论（1=严重幻觉，5=完全无幻觉）
5. **格式规范** — 结构是否清晰，表格/列表是否正确，是否便于快速阅读

评测用例类型：{case_type}
期望行为说明：{expected_behavior}

--- 输入参数 ---
{input_params}

--- Agent 输出 ---
{agent_output}

请严格按以下 JSON 格式输出评分（不要输出其他内容）：
```json
{{
  "score_data_accuracy": 4,
  "score_actionability": 3,
  "score_risk_warning": 5,
  "score_hallucination": 4,
  "score_format": 4,
  "score_total": 20,
  "judge_comments": "具体评审意见，说明扣分原因",
  "issues_found": ["问题1", "问题2"],
  "improvement_suggestions": ["建议1", "建议2"]
}}
```"""


async def run_llm_judge(case_type: str, expected_behavior: str,
                        input_params: str, agent_output: str) -> dict:
    """调用 LLM-as-Judge 对 Agent 输出打分。"""
    if get_config("llm_cost.llm_judge_eval", "false") != "true":
        return {
            "score_data_accuracy": 0,
            "score_actionability": 0,
            "score_risk_warning": 0,
            "score_hallucination": 0,
            "score_format": 0,
            "score_total": 0,
            "judge_comments": "LLM Judge 已关闭",
            "status": "skipped",
            "issues_found": [],
            "improvement_suggestions": [],
        }

    prompt = JUDGE_PROMPT.format(
        case_type=case_type,
        expected_behavior=expected_behavior or "无特殊要求",
        input_params=input_params[:3000],
        agent_output=agent_output[:6000],
    )
    try:
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="eval_judge",
            model=MODEL_AUX,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=get_config_int("llm.max_tokens_eval", 2000),
        ))
        content = response.choices[0].message.content or ""
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(1))
        else:
            parsed = json.loads(content.strip())
        for key in ["score_data_accuracy", "score_actionability", "score_risk_warning",
                     "score_hallucination", "score_format"]:
            value = parsed.get(key)
            if type(value) is not int or not 1 <= value <= 5:
                raise ValueError(f'Judge缺少有效评分字段: {key}')
            parsed[key] = value
        parsed["score_total"] = (
            parsed["score_data_accuracy"] + parsed["score_actionability"] +
            parsed["score_risk_warning"] + parsed["score_hallucination"] +
            parsed["score_format"]
        )
        parsed["status"] = "scored"
        return parsed
    except Exception as e:
        logger.error(f"[eval] LLM Judge 失败: {e}")
        return {
            "score_data_accuracy": 0, "score_actionability": 0,
            "score_risk_warning": 0, "score_hallucination": 0,
            "score_format": 0, "score_total": 0,
            "judge_comments": f"评审失败: {e}", "status":"error",
            "issues_found": [], "improvement_suggestions": [],
        }


# ============ Agent 输出生成 ============

async def _generate_agent_output(case: dict, prompt_override: str = None) -> str:
    result = await execute_case(case,prompt_override=prompt_override)
    return str(result["result"])


async def _execute_single_eval(case_id: int, prompt_version: str = None,
                               run_mode: str = "manual", case_snapshot: dict = None) -> dict:
    """执行真实 Agent 并使用指定的完整 Prompt，而非仅记录版本标签。"""
    import time
    case = dict(case_snapshot or get_eval_case(case_id) or {})
    if not case:
        return {"error":f"用例 {case_id} 不存在"}
    kind = case["analysis_type"]
    version_label = "production"
    try:
        prompt = production_prompt(kind)
    except UnsupportedEvaluation as exc:
        rid = create_eval_run(case_id,kind,str(exc),score_status='skipped',case_snapshot=case)
        return {'run_id':rid,'case_id':case_id,'case_type':kind,'error':str(exc),'score_status':'skipped'}
    if prompt_version is not None:
        versions = list_prompt_versions(kind)
        selected = next((v for v in versions if v['id']==prompt_version),None) if type(prompt_version) is int else next((v for v in versions if v['version']==str(prompt_version)),None)
        if not selected:
            raise ValueError("Prompt 版本不存在或不属于该 Agent 类型")
        prompt, version_label = selected['prompt_content'],selected['version']
    else:
        active = get_active_prompt(kind)
        if active:
            version_label = active['version'] if active['prompt_content'] == prompt else 'production (unversioned)'
    started = time.monotonic()
    snapshot = {**case,"production_prompt":prompt,"prompt_version":version_label,"data_evidence":capture_data_evidence(case)}
    try:
        result = await execute_case(case,prompt_override=prompt if prompt else None)
        output = str(result['result'])
        scores = await run_llm_judge(case_type=kind,expected_behavior=case.get('expected_quality',''),input_params=case.get('input_params','{}'),agent_output=output)
        state = scores.get('status') or ('scored' if scores.get('score_total',0)>0 else 'error')
        run_id = create_eval_run(case_id,kind,scores.get('judge_comments','')[:200],
            result_data=json.dumps({'agent_output':output,'execution':result,'scores':scores,
                                     'prompt_version':version_label,'run_mode':run_mode},ensure_ascii=False),
            score=scores['score_total'] if state=='scored' else None,score_max=25,
            rubric_version='investment-five-dim-v1',score_status=state,
            duration_ms=int((time.monotonic()-started)*1000),token_usage=result.get('token_usage',0),
            case_snapshot=snapshot,prompt_version=version_label)
        response = {'run_id':run_id,'case_id':case_id,'case_type':kind,'scores':scores,'prompt_version':version_label,'score_status':state}
        if state != 'scored':
            response['error'] = scores.get('judge_comments') or '未获得有效评分'
        return response
    except Exception as exc:
        run_id = create_eval_run(case_id,kind,'评测失败',error_msg=str(exc),case_snapshot=snapshot,
                                 prompt_version=version_label,score_max=25,
                                 duration_ms=int((time.monotonic()-started)*1000))
        return {'run_id':run_id,'case_id':case_id,'case_type':kind,'error':str(exc)}


# ============ 每日评测 ============

async def run_daily_eval() -> dict:
    """每日自动评测 Pipeline。"""
    today = datetime.now().strftime("%Y-%m-%d")
    sampled = get_random_active_cases(5)
    if not sampled:
        return {"message": "无活跃用例，跳过评测"}

    results = []
    for case in sampled:
        result = await _execute_single_eval(case["id"], run_mode="daily")
        results.append(result)

    valid_results = [r for r in results if "error" not in r]
    if not valid_results:
        return {"message": "评测全部失败", "results": results}

    scores_by_type = {}
    all_totals = []
    for r in valid_results:
        t = r["case_type"]
        s = r["scores"]["score_total"]
        all_totals.append(s)
        scores_by_type.setdefault(t, []).append(s)

    avg_score = sum(all_totals) / len(all_totals)
    avg_by_type = {t: sum(s) / len(s) for t, s in scores_by_type.items()}

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_report = get_eval_daily_report(yesterday)
    score_trend = "stable"
    alerts = []
    if prev_report:
        prev_avg = prev_report.get("avg_score", 0)
        if prev_avg > 0:
            diff = avg_score - prev_avg
            if diff < -0.5:
                score_trend = "down"
                alerts.append(f"质量下降：平均分 {avg_score:.1f}（前日 {prev_avg:.1f}，降 {abs(diff):.1f}）")
            elif diff > 0.5:
                score_trend = "up"
                alerts.append(f"质量提升：平均分 {avg_score:.1f}（前日 {prev_avg:.1f}，升 {diff:.1f}）")

    recommendations = []
    for r in valid_results:
        issues = r["scores"].get("issues_found", [])
        recommendations.extend(issues)

    save_eval_daily_report(
        report_date=today,
        total_cases=len(valid_results),
        avg_score=round(avg_score, 2),
        scores_by_type=avg_by_type,
        score_trend=score_trend,
        alerts=alerts,
        recommendations=list(set(recommendations))[:10],
    )

    return {
        "date": today,
        "total_cases": len(valid_results),
        "avg_score": round(avg_score, 2),
        "scores_by_type": {t: round(s, 2) for t, s in avg_by_type.items()},
        "trend": score_trend,
        "alerts": alerts,
        "results": results,
    }


# ============ API 端点 ============

from pydantic import BaseModel, Field
from typing import Optional


class EvalCaseCreate(BaseModel):
    case_name: str = Field(..., min_length=1, max_length=200)
    case_type: str = Field(..., max_length=50)
    portfolio_context: Optional[dict] = Field(None)
    input_params: Optional[dict] = Field(None)
    expected_behavior: Optional[str] = Field(None, max_length=2000)
    expected_quality: Optional[str] = Field(None, max_length=2000)


class EvalRunRequest(BaseModel):
    case_id: int = Field(..., gt=0)


class EvalBatchRequest(BaseModel):
    case_type: Optional[str] = Field(None, max_length=50)
    version_a: Optional[int] = Field(None, gt=0)
    version_b: Optional[int] = Field(None, gt=0)


@router.post("/cases")
async def create_case_api(data: EvalCaseCreate):
    ctx = data.portfolio_context or data.input_params or {}
    cid = create_eval_case(
        name=data.case_name,
        analysis_type=data.case_type,
        input_params=json.dumps(ctx, ensure_ascii=False) if isinstance(ctx, dict) else str(ctx),
        expected_quality=data.expected_behavior or data.expected_quality or "",
    )
    return {"status": "ok", "id": cid}


@router.get("/cases")
async def list_cases_api(case_type: str = None, limit: int = 100):
    cases = list_eval_cases(analysis_type=case_type)
    return {"status": "ok", "cases": cases[:limit]}


@router.delete("/cases/{case_id}")
async def delete_case_api(case_id: int):
    ok = delete_eval_case(case_id)
    return {"status": "ok" if ok else "error"}


@router.post("/run")
async def run_eval_api(data: EvalRunRequest):
    """运行单条评测用例（异步执行）。"""
    if not get_eval_case(data.case_id):
        raise HTTPException(404,'用例不存在')
    submitted = claim_governance_task('eval_system_run','eval_system',{'case_id':data.case_id})
    if submitted['reused']:
        return submitted
    task_id,case_id = submitted['task_id'],data.case_id

    async def _run():
        try:
            result = await _execute_single_eval(case_id, run_mode="manual")
            update_async_task(task_id, status="done", result={"status": "ok", "result": result})
        except Exception as e:
            logger.error(f"任务 {task_id} 失败: {e}", exc_info=True)
            update_async_task(task_id, status="error", error_msg=str(e))

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_batch_eval(active_cases: list, version_a, version_b) -> dict:
    """旧批量 API 也使用相同的配对、输入证据和真实提示词执行流程。"""
    versions = {v['id']:v for v in list_prompt_versions()}
    active, target = versions.get(version_a),versions.get(version_b)
    if not active or not target or active['agent_type'] != target['agent_type'] or active['id'] == target['id']:
        raise ValueError('请选择同一Agent的两个不同Prompt版本')
    return await _compare_prompt_versions(target,active,active_cases[:5])


@router.post("/run-batch")
async def run_batch_eval_api(data: EvalBatchRequest):
    """批量 A/B 评测；按版本和不可变用例内容进行任务去重。"""
    versions = {v['id']:v for v in list_prompt_versions()}
    active,target = versions.get(data.version_a),versions.get(data.version_b)
    if not active or not target:
        raise HTTPException(400,'请指定两个有效Prompt版本')
    kind = target['agent_type']
    if active['agent_type'] != kind or active['id'] == target['id'] or (data.case_type and data.case_type != kind):
        raise HTTPException(400,'用例与两个Prompt版本必须属于同一Agent，且版本不同')
    cases = list_eval_cases(analysis_type=kind,active_only=True)[:5]
    if not cases:
        raise HTTPException(400,'无可用评测用例')
    submitted = claim_governance_task('eval_system_batch','eval_system',{'active':active['id'],'target':target['id'],'cases':cases})
    if not submitted['reused']:
        async def run():
            try:
                result = await _compare_prompt_versions(target,active,cases)
                update_async_task(submitted['task_id'],status='done',result=result)
            except Exception as exc:
                update_async_task(submitted['task_id'],status='error',error_msg=str(exc))
        task = asyncio.create_task(run())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return submitted


@router.post("/daily")
async def trigger_daily_eval_api():
    """触发每日评测（异步执行）。"""
    submitted = claim_governance_task('eval_system_daily','eval_system',{'date':datetime.now().strftime('%Y-%m-%d')})
    if submitted['reused']:
        return submitted
    task_id = submitted['task_id']

    async def _run():
        try:
            result = await run_daily_eval()
            update_async_task(task_id, status="done", result={"status": "ok", "report": result})
        except Exception as e:
            logger.error(f"任务 {task_id} 失败: {e}", exc_info=True)
            update_async_task(task_id, status="error", error_msg=str(e))

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


@router.get("/results")
async def list_results_api(case_id: int = None, limit: int = 50):
    runs = list_eval_runs(case_id=case_id, limit=limit)
    return {"status": "ok", "results": runs}


@router.get("/results/{run_id}")
async def get_result_api(run_id: int):
    result = get_eval_run_detail(run_id)
    if not result:
        raise HTTPException(404, "结果不存在")
    return {"status": "ok", "result": result}


@router.get("/stats")
async def get_stats_api():
    stats = get_eval_stats()
    return {"status": "ok", "stats": stats}


# --- 提示词版本管理 ---


class PromptVersionCreate(BaseModel):
    agent_type: str = Field(..., max_length=50)
    version: str = Field(..., max_length=20)
    prompt_content: str = Field(..., min_length=10, max_length=50000)
    changelog: str = Field("", max_length=1000)


@router.post("/prompts")
async def create_prompt_api(data: PromptVersionCreate):
    vid = create_prompt_version(
        agent_type=data.agent_type,
        version=data.version,
        prompt_content=data.prompt_content,
        changelog=data.changelog,
    )
    return {"status": "ok", "id": vid}


@router.get("/prompts")
async def list_prompts_api(agent_type: str = None):
    versions = list_prompt_versions(agent_type=agent_type)
    for version in versions:
        try:
            version['is_current'] = bool(version['is_active'] and version['prompt_content'] == production_prompt(version['agent_type']))
        except (ValueError,KeyError):
            version['is_current'] = False
        version['publication_status'] = 'current' if version['is_current'] else 'drifted' if version['is_active'] else 'draft'
    return {"status": "ok", "versions": versions}


@router.put("/prompts/{version_id}/activate")
async def activate_prompt_api(version_id: int, data: PromptActivateRequest):
    agent_type = data.agent_type
    if not agent_type:
        raise HTTPException(400, "缺少 agent_type")
    if not activate_prompt_version(version_id, agent_type):
        raise HTTPException(404,"版本不存在、类型不匹配或生产Agent不可用")
    return {"status": "ok"}


async def _compare_prompt_versions(target: dict, active: dict, cases: list[dict]) -> dict:
    results_active,results_target = [],[]
    evidence_pairs = []
    for case in cases:
        before = capture_data_evidence(case)
        results_active.append(await _execute_single_eval(case['id'],active['id'],'ab_test',case_snapshot=case))
        results_target.append(await _execute_single_eval(case['id'],target['id'],'ab_test',case_snapshot=case))
        after = capture_data_evidence(case)
        evidence_pairs.append({'case_id':case['id'],'before':before,'after':after})
        if before['hash'] != after['hash']:
            results_target[-1]['error'] = '本地输入在A/B执行间发生变化，样本不可比较'
    paired = [(a,b) for a,b in zip(results_active,results_target) if 'error' not in a and 'error' not in b]
    if not paired:
        raise RuntimeError('没有成对有效评分，无法比较版本')
    return dict(status='ok',active_version=active['version'],target_version=target['version'],
                avg_active=round(sum(a['scores']['score_total'] for a,b in paired)/len(paired),2),
                avg_target=round(sum(b['scores']['score_total'] for a,b in paired)/len(paired),2),
                paired_count=len(paired),failed_pairs=len(cases)-len(paired),data_evidence=evidence_pairs,external_data_frozen=False,
                results_active=results_active,results_target=results_target)


@router.post("/prompts/{version_id}/compare")
async def compare_prompt_api(version_id: int, case_type: str = None):
    target = next((v for v in list_prompt_versions() if v['id']==version_id),None)
    if not target:
        raise HTTPException(404,'版本不存在')
    if case_type and case_type != target['agent_type']:
        raise HTTPException(400,'用例类型与Prompt不匹配')
    active = get_active_prompt(target['agent_type'])
    if active and active['prompt_content'] != production_prompt(target['agent_type']):
        raise HTTPException(400,'历史活跃版本与当前生产Prompt不同，请先重新显式激活版本')
    if not active or active['id']==target['id']:
        raise HTTPException(400,'请选择不同于当前活跃版本的候选版本')
    cases = list_eval_cases(analysis_type=target['agent_type'],active_only=True)[:5]
    if not cases:
        raise HTTPException(400,'该类型无可用评测用例')
    submitted = claim_governance_task('eval_system_compare','eval_system',{'target':version_id,'active':active['id'],'cases':cases})
    if not submitted['reused']:
        async def run():
            try:
                result = await _compare_prompt_versions(target,active,cases)
                update_async_task(submitted['task_id'],status='done',result=result)
            except Exception as exc:
                update_async_task(submitted['task_id'],status='error',error_msg=str(exc))
        task = asyncio.create_task(run())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return submitted


# --- 质量日报 ---

@router.get("/daily-reports")
async def list_daily_reports_api(limit: int = 30):
    reports = list_eval_daily_reports(limit=limit)
    return {"status": "ok", "reports": reports}


@router.get("/daily-reports/{date}")
async def get_daily_report_api(date: str):
    report = get_eval_daily_report(date)
    if not report:
        raise HTTPException(404, f"{date} 无日报")
    return {"status": "ok", "report": report}


@router.get("/trends")
async def get_trends_api(days: int = 30):
    trends = get_eval_trends(days=days)
    return {"status": "ok", "trends": trends}
