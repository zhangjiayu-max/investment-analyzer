"""持仓 AI 分析 — POST /api/portfolio/analysis/ai"""
import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException

from db import (
    list_holdings, get_analysis_agent,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    get_portfolio_analysis_record, delete_portfolio_analysis_record,
    update_analysis_feedback, list_all_bad_cases,
    create_async_task, update_async_task,
)
from db.portfolio import update_analysis_record
from db.config import get_config_int, get_config_float
from db.agent_analysis_log import create_analysis_log, complete_analysis_log
from services.rag import build_rag_context_with_details, log_rag_search
from models.portfolio import PortfolioAiAnalysisRequest, FeedbackRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-portfolio-ai"])

_background_tasks: set = set()


def _extract_candidates_safely(record_id: int, analysis_type: str, result_text: str):
    try:
        from db.decisions import extract_recommendation_candidates_from_analysis
        extract_recommendation_candidates_from_analysis(record_id, analysis_type, result_text)
    except Exception as e:
        logger.warning(f"建议候选抽取失败 record_id={record_id}: {e}")


@router.post("/api/portfolio/analysis/ai")
async def portfolio_ai_analysis_api(req: PortfolioAiAnalysisRequest):
    """AI 持仓分析：后台调用 MCP 工具 + LLM 生成分析报告（异步模式）。"""
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")
    user_question = req.question or "请全面分析我的持仓情况，包括资产配置合理性、风险分散度、各基金表现，以及改进建议。"
    record_id = create_portfolio_analysis_record(
        analysis_type="ai",
        summary=f"AI持仓分析 · {len(holdings)}只基金",
        input_data=json.dumps({
            "holdings": [{k: h.get(k) for k in ("fund_code", "fund_name", "shares", "cost_price", "current_price", "profit_loss")} for h in holdings],
            "question": user_question,
        }, ensure_ascii=False),
        result_data="",
        token_usage=0,
        status="running",
    )
    # 创建 async_task 记录用于通用状态查询
    task_id = create_async_task("portfolio_ai", caller="portfolio_ai")
    task = asyncio.create_task(_run_portfolio_ai_async(task_id, record_id, user_question))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "record_id": record_id, "status": "running", "holdings_count": len(holdings)}


async def _run_portfolio_ai_async(task_id: int, record_id: int, user_question: str):
    """后台执行通用 AI 持仓分析（包裹 async_task 状态跟踪）。"""
    try:
        await _run_portfolio_ai_analysis_async(record_id, user_question)
        # 读取 record 结果
        record = get_portfolio_analysis_record(record_id)
        if record and record.get("status") == "done":
            update_async_task(task_id, status="done", result={"record_id": record_id, "status": "done"})
        else:
            error_msg = record.get("error_msg", "未知错误") if record else "记录不存在"
            update_async_task(task_id, status="error", error_msg=error_msg)
    except Exception as e:
        logger.error(f"AI 持仓分析失败 task_id={task_id}, record_id={record_id}: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _run_portfolio_ai_analysis_async(record_id: int, user_question: str):
    """后台执行通用 AI 持仓分析。"""
    # 分析记录埋点（running）
    trace_id = f"log_{uuid.uuid4().hex[:12]}"
    _start_ts = time.time()
    try:
        create_analysis_log(
            trace_id=trace_id, agent_id=None, agent_name="AI持仓分析",
            analysis_type="portfolio_ai", source_table="portfolio_analysis_records",
            source_id=record_id, query=user_question[:200],
            input_summary="AI持仓分析",
        )
    except Exception as _e:
        logger.warning(f"create_analysis_log 失败: {_e}")

    # 1. 获取持仓数据
    holdings = list_holdings()

    # 2. 调用 MCP 工具
    mcp_context = {}
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()

        # 并行调用多个 MCP 工具
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code")]

        # 组合诊断（所有基金）
        if fund_codes:
            try:
                mcp_context["portfolio_diagnosis"] = mcp.diagnose_portfolio(fund_codes)
            except Exception as e:
                mcp_context["portfolio_diagnosis"] = f"诊断失败: {e}"

        # 市场行情
        try:
            mcp_context["market_quotations"] = mcp.get_latest_quotations()
        except Exception as e:
            mcp_context["market_quotations"] = f"行情获取失败: {e}"

        # 热点话题
        try:
            mcp_context["hot_topics"] = mcp.get_hot_topics()
        except Exception as e:
            mcp_context["hot_topics"] = f"热点获取失败: {e}"

        # 各基金诊断（最多3只，避免 token 过多）
        fund_diagnoses = {}
        for code in fund_codes[:3]:
            try:
                fund_diagnoses[code] = mcp.get_fund_diagnosis(code)
            except Exception as e:
                fund_diagnoses[code] = f"诊断失败: {e}"
        mcp_context["fund_diagnoses"] = fund_diagnoses

    except ImportError:
        mcp_context["error"] = "MCP 客户端未配置"
    except Exception as e:
        mcp_context["error"] = f"MCP 调用异常: {e}"

    # 3. 拼装 LLM 上下文 — 复用统一组合事实层 + 估值摘要 + 完整持仓字段
    holdings_summary = []
    for h in holdings:
        profit_rate_pct = ((h.get("profit_rate") or 0) * 100)
        current_value = h.get("current_value") or 0
        total_cost = h.get("total_cost") or 0
        holdings_summary.append(
            f"- {h.get('fund_name', '')}({h.get('fund_code', '')}): "
            f"持有 {h.get('shares', 0):.2f} 份, "
            f"成本价 {h.get('cost_price', 'N/A')}, "
            f"当前净值 {h.get('current_price', 'N/A')}, "
            f"市值 ¥{current_value:,.0f}, "
            f"成本 ¥{total_cost:,.0f}, "
            f"盈亏 ¥{(h.get('profit_loss') or 0):,.0f} ({profit_rate_pct:+.1f}%), "
            f"跟踪指数 {h.get('index_name', 'N/A')}"
        )

    # 组合事实层（snapshot+constraints+market+recent_analyses+market_state+recent_decisions）
    portfolio_facts_text = ""
    try:
        from services.portfolio.portfolio_fact_layer import build_portfolio_facts
        facts = build_portfolio_facts()
        portfolio_facts_text = json.dumps(facts, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning(f"build_portfolio_facts 失败: {e}")

    # 估值摘要
    valuation_text = ""
    try:
        from services.portfolio_context import build_valuation_summary
        valuation_text = build_valuation_summary() or ""
    except Exception as e:
        logger.warning(f"build_valuation_summary 失败: {e}")

    # RAG 知识库检索（搜索与持仓相关的文章、分析）
    rag_context = ""
    try:
        fund_names = " ".join([h.get("fund_name", "") for h in holdings[:5]])
        rag_query = f"持仓分析 基金投资 {fund_names}"
        rag_result = build_rag_context_with_details(query=rag_query, limit=5)
        rag_context = rag_result.get("context", "")
        # 记录 RAG 检索日志
        log_rag_search(
            conversation_id=0,
            message_id=0,
            query=rag_query,
            keywords=rag_result.get("keywords", []),
            results=rag_result.get("results", []),
            fts_count=rag_result.get("fts_count", 0),
            chroma_count=rag_result.get("chroma_count", 0),
            freshness_filtered=rag_result.get("freshness_filtered", 0),
        )
    except Exception as e:
        logger.warning(f"RAG 检索失败: {e}")

    system_prompt = """你是一位专业的投资组合分析师。请根据以下持仓数据和专业分析工具的输出，给出全面的投资组合分析报告。

分析要求：
1. **资产配置分析** — 持仓的基金类型分布、行业分布是否合理
2. **风险评价** — 组合的集中度风险、相关性风险、回撤风险
3. **各基金表现** — 逐个评价每只基金的表现（收益、波动、性价比）
4. **改进建议** — 具体的调仓建议、定投策略、风险控制措施
5. **市场环境** — 结合当前市场行情和热点，给出背景判断

输出格式：使用 Markdown 标题层级，结论先行，数据支撑，内容专业易懂。"""

    user_content = f"""## 当前持仓（完整字段）
{chr(10).join(holdings_summary)}

## 组合事实层（snapshot+constraints+market+recent_analyses+market_state+recent_decisions）
{portfolio_facts_text[:3000] if portfolio_facts_text else '暂无'}

## 当前市场估值数据
{valuation_text or '暂无估值数据'}

## 专业分析数据（MCP 诊断）
{json.dumps(mcp_context, ensure_ascii=False, indent=2)[:3000]}

## 知识库参考（历史分析/文章）
{rag_context[:1500] if rag_context else '暂无相关知识库内容'}

## 用户问题
{user_question}

请给出全面的分析报告。"""

    # 4. 调用 LLM
    try:
        from services.llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_analysis",
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_analysis', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8192),
            trace_id=trace_id,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"AI 分析失败: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))
        try:
            complete_analysis_log(
                trace_id=trace_id, status="error",
                duration_ms=int((time.time() - _start_ts) * 1000),
                error_msg=str(e),
            )
        except Exception as _e:
            logger.warning(f"complete_analysis_log 失败: {_e}")
        return

    update_analysis_record(
        record_id,
        result_data=result_text,
        token_usage=tokens,
        status="done",
        error_msg="",
    )
    try:
        complete_analysis_log(
            trace_id=trace_id, status="done",
            duration_ms=int((time.time() - _start_ts) * 1000),
            token_usage=tokens,
        )
    except Exception as _e:
        logger.warning(f"complete_analysis_log 失败: {_e}")
    _extract_candidates_safely(record_id, "ai", result_text)
    logger.info(f"AI 持仓分析完成 record_id={record_id}, mcp={list(mcp_context.keys())}")


@router.get("/api/portfolio/analysis/ai-records")
async def list_ai_analysis_records_api(limit: int = 20):
    """列出 AI 持仓分析记录。"""
    records = list_portfolio_analysis_records(analysis_type="ai", limit=limit)
    return {"records": records}


@router.get("/api/portfolio/analysis/ai-records/{record_id}")
async def get_ai_analysis_record_api(record_id: int):
    """获取单条 AI 持仓分析记录详情。"""
    record = get_portfolio_analysis_record(record_id)
    if not record:
        raise HTTPException(404, "分析记录不存在")
    return record


@router.delete("/api/portfolio/analysis/ai-records/{record_id}")
async def delete_ai_analysis_record_api(record_id: int):
    """删除 AI 持仓分析记录。"""
    if not delete_portfolio_analysis_record(record_id):
        raise HTTPException(404, "分析记录不存在")
    return {"ok": True}


@router.post("/api/portfolio/analysis/feedback/{record_id}")
async def submit_analysis_feedback_api(record_id: int, req: FeedbackRequest):
    """提交对分析结果的反馈。"""
    if req.feedback not in ("helpful", "unhelpful"):
        raise HTTPException(400, "feedback 必须为 helpful 或 unhelpful")
    if not update_analysis_feedback(record_id, req.feedback, req.note):
        raise HTTPException(404, "分析记录不存在")
    # 触发反馈学习，更新用户画像
    try:
        from agent.feedback_learner import update_user_profile_from_feedback
        update_user_profile_from_feedback("default", req.feedback, req.note)
    except Exception as e:
        logger.warning(f"反馈学习更新失败: {e}")
    return {"ok": True}


@router.get("/api/portfolio/analysis/bad-cases")
async def list_bad_cases_api(source: str = None, limit: int = 100):
    """列出所有 Bad Case（分析记录 + LLM 反馈）。"""
    cases = list_all_bad_cases(source=source, limit=limit)
    return {"cases": cases, "count": len(cases)}


@router.post("/api/portfolio/analysis/root-cause/batch")
async def batch_analyze_root_cause(limit: int = 50, force: bool = False):
    """批量分析 Bad Case 根因。"""
    from infra.root_cause_analyzer import batch_analyze
    result = batch_analyze(limit=limit, force=force)
    return result


@router.get("/api/portfolio/analysis/root-cause/stats")
async def get_root_cause_stats_api():
    """获取根因统计信息。"""
    from infra.root_cause_analyzer import get_root_cause_stats
    return get_root_cause_stats()


@router.post("/api/portfolio/analysis/root-cause/{source}/{case_id}")
async def analyze_single_root_cause(source: str, case_id: int):
    """分析单个 Bad Case 的根因。"""
    from infra.root_cause_analyzer import analyze_root_cause, _save_root_cause
    from db.portfolio import list_all_bad_cases

    cases = list_all_bad_cases(limit=500)
    target = None
    for c in cases:
        if c["source"] == source and c["id"] == case_id:
            target = c
            break

    if not target:
        raise HTTPException(status_code=404, detail="Bad Case 未找到")

    result = analyze_root_cause(target)
    if not result:
        raise HTTPException(status_code=500, detail="根因分析失败")

    _save_root_cause(source, case_id, result)
    return {"ok": True, "result": result}
