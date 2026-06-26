"""Agent 质量评测系统 — LLM Judge + 每日评测 + A/B 测试"""
import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from db import get_config, get_config_int
from schemas import PromptActivateRequest
from db.eval import (
    create_eval_case, list_eval_cases, get_eval_case, update_eval_case, delete_eval_case,
    create_eval_run, list_eval_runs, get_eval_run_detail, update_eval_run,
    get_eval_stats, get_random_active_cases,
    create_prompt_version, list_prompt_versions, get_active_prompt,
    activate_prompt_version, update_prompt_scores,
    save_eval_daily_report, get_eval_daily_report, list_eval_daily_reports, get_eval_trends,
)
from llm_service import _call_llm, MODEL

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
            model=MODEL,
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
            parsed[key] = max(1, min(5, int(parsed.get(key, 3))))
        parsed["score_total"] = (
            parsed["score_data_accuracy"] + parsed["score_actionability"] +
            parsed["score_risk_warning"] + parsed["score_hallucination"] +
            parsed["score_format"]
        )
        return parsed
    except Exception as e:
        logger.error(f"[eval] LLM Judge 失败: {e}")
        return {
            "score_data_accuracy": 0, "score_actionability": 0,
            "score_risk_warning": 0, "score_hallucination": 0,
            "score_format": 0, "score_total": 0,
            "judge_comments": f"评审失败: {e}",
            "issues_found": [], "improvement_suggestions": [],
        }


# ============ Agent 输出生成 ============

async def _generate_agent_output(case: dict) -> str:
    """根据用例类型生成 Agent 输出（用于评测）。"""
    case_type = case.get("analysis_type", "panorama")
    input_params = case.get("input_params", "{}")

    type_prompts = {
        "panorama": "请对以下投资组合进行全面诊断分析，包括集中度风险、估值水位、分散化程度，并给出加减仓建议。",
        "deepdive": "请对持仓中最主要的基金进行深度分析，包括基金经理、历史业绩、持仓风格、估值水平。",
        "fee": "请分析持仓基金的真实费率成本，计算年化费率和10年复利侵蚀，给出降费建议。",
        "correlation": "请计算持仓基金之间的相关性，识别高相关对，计算有效持仓数，给出分散度优化建议。",
        "trade-review": "请复盘最近的交易记录，分析买卖时机、仓位管理、盈亏原因。",
    }
    prompt = type_prompts.get(case_type, "请分析以下投资组合。")
    full_prompt = f"""{prompt}

输入参数：
{input_params[:3000]}

请按照标准分析格式输出。"""
    try:
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="eval_agent_gen",
            model=MODEL,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3,
            max_tokens=get_config_int("llm.max_tokens_analysis", 8000),
        ))
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"[eval] 生成 Agent 输出失败: {e}")
        return f"生成失败: {e}"


# ============ 单条评测 ============

async def _execute_single_eval(case_id: int, prompt_version: str = None,
                               run_mode: str = "manual") -> dict:
    """执行单条评测。"""
    case = get_eval_case(case_id)
    if not case:
        return {"error": f"用例 {case_id} 不存在"}

    case_type = case.get("analysis_type", "panorama")
    agent_output = await _generate_agent_output(case)

    scores = await run_llm_judge(
        case_type=case_type,
        expected_behavior=case.get("expected_quality", ""),
        input_params=case.get("input_params", "{}"),
        agent_output=agent_output,
    )

    if not prompt_version:
        active = get_active_prompt(case_type)
        prompt_version = active["version"] if active else "default"

    # 用现有 eval_runs 表存储
    run_id = create_eval_run(
        case_id=case_id,
        analysis_type=case_type,
        result_summary=scores.get("judge_comments", "")[:200],
        result_data=json.dumps({
            "agent_output": agent_output[:2000],
            "scores": scores,
            "prompt_version": prompt_version,
            "run_mode": run_mode,
        }, ensure_ascii=False),
        score=scores["score_total"],
    )

    return {
        "run_id": run_id,
        "case_id": case_id,
        "case_type": case_type,
        "scores": scores,
        "prompt_version": prompt_version,
    }


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
    result = await _execute_single_eval(data.case_id, run_mode="manual")
    return {"status": "ok", "result": result}


@router.post("/run-batch")
async def run_batch_eval_api(data: EvalBatchRequest):
    case_type = data.get("case_type")
    version_a = data.get("version_a")
    version_b = data.get("version_b")

    cases = list_eval_cases(analysis_type=case_type)
    active_cases = [c for c in cases if c.get("is_active")]
    if not active_cases:
        raise HTTPException(400, "无可用评测用例")

    results_a, results_b = [], []
    for case in active_cases[:5]:
        ra = await _execute_single_eval(case["id"], prompt_version=version_a, run_mode="ab_test")
        rb = await _execute_single_eval(case["id"], prompt_version=version_b, run_mode="ab_test")
        results_a.append(ra)
        results_b.append(rb)

    scores_a = [r["scores"]["score_total"] for r in results_a if "error" not in r]
    scores_b = [r["scores"]["score_total"] for r in results_b if "error" not in r]
    avg_a = sum(scores_a) / len(scores_a) if scores_a else 0
    avg_b = sum(scores_b) / len(scores_b) if scores_b else 0
    wins_a = sum(1 for a, b in zip(scores_a, scores_b) if a > b)
    wins_b = sum(1 for a, b in zip(scores_a, scores_b) if b > a)
    ties = sum(1 for a, b in zip(scores_a, scores_b) if a == b)
    total = len(scores_a)
    win_rate_b = wins_b / total * 100 if total > 0 else 0

    if win_rate_b > 60:
        verdict = "新版胜出，建议上线"
    elif win_rate_b < 40:
        verdict = "旧版更优，建议保留"
    else:
        verdict = "差异不大，建议继续迭代"

    return {
        "status": "ok",
        "comparison": {
            "version_a": version_a, "version_b": version_b,
            "avg_score_a": round(avg_a, 2), "avg_score_b": round(avg_b, 2),
            "wins_a": wins_a, "wins_b": wins_b, "ties": ties,
            "win_rate_b": round(win_rate_b, 1), "verdict": verdict,
        },
        "results_a": results_a, "results_b": results_b,
    }


@router.post("/daily")
async def trigger_daily_eval_api():
    result = await run_daily_eval()
    return {"status": "ok", "report": result}


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
    return {"status": "ok", "versions": versions}


@router.put("/prompts/{version_id}/activate")
async def activate_prompt_api(version_id: int, data: PromptActivateRequest):
    agent_type = data.agent_type
    if not agent_type:
        raise HTTPException(400, "缺少 agent_type")
    activate_prompt_version(version_id, agent_type)
    return {"status": "ok"}


@router.get("/prompts/{version_id}/compare")
async def compare_prompt_api(version_id: int, case_type: str = None):
    versions = list_prompt_versions()
    target = next((v for v in versions if v["id"] == version_id), None)
    if not target:
        raise HTTPException(404, "版本不存在")
    active = get_active_prompt(target["agent_type"])
    if not active:
        raise HTTPException(400, "该类型无活跃版本，无法对比")

    cases = get_random_active_cases(5)
    if not cases:
        raise HTTPException(400, "无可用评测用例")

    results_active, results_target = [], []
    for case in cases:
        ra = await _execute_single_eval(case["id"], prompt_version=active["version"], run_mode="ab_test")
        rt = await _execute_single_eval(case["id"], prompt_version=target["version"], run_mode="ab_test")
        results_active.append(ra)
        results_target.append(rt)

    scores_active = [r["scores"]["score_total"] for r in results_active if "error" not in r]
    scores_target = [r["scores"]["score_total"] for r in results_target if "error" not in r]

    return {
        "status": "ok",
        "active_version": active["version"],
        "target_version": target["version"],
        "avg_active": round(sum(scores_active) / len(scores_active), 2) if scores_active else 0,
        "avg_target": round(sum(scores_target) / len(scores_target), 2) if scores_target else 0,
        "results_active": results_active,
        "results_target": results_target,
    }


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
