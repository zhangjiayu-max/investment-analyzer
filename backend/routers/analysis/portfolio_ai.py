"""持仓 AI 分析 — POST /api/portfolio/analysis/ai"""
import asyncio
import json
import logging

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
from rag import build_rag_context_with_details, log_rag_search
from models.portfolio import PortfolioAiAnalysisRequest, FeedbackRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-portfolio-ai"])

_background_tasks: set = set()


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

    # 3. 拼装 LLM 上下文
    holdings_summary = []
    for h in holdings:
        holdings_summary.append(
            f"- {h.get('fund_name', '')}({h.get('fund_code', '')}): "
            f"持有 {h.get('shares', 0)} 份, "
            f"成本价 {h.get('cost_price', 'N/A')}, "
            f"当前净值 {h.get('current_price', 'N/A')}, "
            f"盈亏 {(h.get('profit_loss') or 0):.2f}元"
        )

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

    user_content = f"""## 当前持仓
{chr(10).join(holdings_summary)}

## 专业分析数据
{json.dumps(mcp_context, ensure_ascii=False, indent=2)}

## 知识库参考（历史分析/文章）
{rag_context[:1500] if rag_context else '暂无相关知识库内容'}

## 用户问题
{user_question}

请给出全面的分析报告。"""

    # 4. 调用 LLM
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_analysis",
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_analysis', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8192),
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"AI 分析失败: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))
        return

    update_analysis_record(
        record_id,
        result_data=result_text,
        token_usage=tokens,
        status="done",
        error_msg="",
    )
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
