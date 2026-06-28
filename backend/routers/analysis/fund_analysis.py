"""指定基金分析 — POST /api/portfolio/analysis/fund-analysis"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from db import (
    list_holdings, get_analysis_agent,
    lookup_fund_info, fetch_fund_nav,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
)
from db.portfolio import update_analysis_record, compare_funds
from db.config import get_config_int, get_config_float
from ._shared import _get_valuation_context, _get_valuation_context_for_fund

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-fund-analysis"])

_background_tasks: set = set()


def _extract_candidates_safely(record_id: int, analysis_type: str, result_text: str):
    try:
        from db.decisions import extract_recommendation_candidates_from_analysis
        extract_recommendation_candidates_from_analysis(record_id, analysis_type, result_text)
    except Exception as e:
        logger.warning(f"建议候选抽取失败 record_id={record_id}: {e}")


@router.post("/api/portfolio/analysis/fund-analysis")
async def fund_analysis_api(req: dict):
    """模式4：指定基金分析 — 输入任意基金代码，结合持仓和估值分析是否建仓。"""
    fund_code = req.get("fund_code", "").strip()
    if not fund_code:
        raise HTTPException(400, "请输入基金代码")

    agent = get_analysis_agent(6)
    if not agent:
        raise HTTPException(404, "AI 基金分析师未配置")

    # 确保 system_prompt 是字符串类型
    system_prompt = agent.get("system_prompt", "")
    if isinstance(system_prompt, bytes):
        system_prompt = system_prompt.decode("utf-8")

    # 获取目标基金信息
    fund_info = lookup_fund_info(fund_code)
    fund_name = fund_info.get("fund_name", "未知基金") if fund_info else "未知基金"

    # 获取目标基金净值
    nav_data = fetch_fund_nav(fund_code)
    nav_info = f"最新净值: {nav_data.get('nav', 'N/A')}，日期: {nav_data.get('date', 'N/A')}" if nav_data else "净值数据获取失败"

    # 获取目标基金估值数据
    target_valuation = _get_valuation_context_for_fund(fund_code, fund_name)

    # 获取用户持仓数据
    holdings = list_holdings()
    total_value = sum(h.get('current_value', 0) or 0 for h in holdings)
    holdings_lines = []
    for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
        pct = (h.get('current_value', 0) or 0) / total_value * 100 if total_value else 0
        holdings_lines.append(
            f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
            f"市值 {(h.get('current_value') or 0):.2f}, 占比 {pct:.1f}%, "
            f"成本 {(h.get('total_cost') or 0):.2f}"
        )

    # 获取整体估值上下文
    valuation_context = _get_valuation_context()

    user_content = (
        f"## 待分析基金\n"
        f"- 基金代码: {fund_code}\n"
        f"- 基金名称: {fund_name}\n"
        f"- {nav_info}\n"
        f"{target_valuation}\n\n"
        f"## 用户当前持仓\n" + "\n".join(holdings_lines) +
        f"\n总市值: {total_value:.2f}\n"
        f"\n{valuation_context}"
    )

    # 创建记录（status='running'）
    record_id = create_portfolio_analysis_record(
        analysis_type="what_if",
        summary=f"指定基金分析 · {fund_name}({fund_code})",
        input_data=json.dumps({"fund_code": fund_code, "fund_name": fund_name}, ensure_ascii=False),
        status="running",
        agent_id=6,
    )

    # 后台执行分析
    task = asyncio.create_task(_run_fund_analysis_async(record_id, system_prompt, user_content))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"ok": True, "id": record_id, "status": "running"}


async def _run_fund_analysis_async(record_id: int, system_prompt: str, user_content: str):
    """后台执行指定基金分析。"""
    import uuid
    trace_id = f"fund_{uuid.uuid4().hex[:12]}"
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_fund_analysis",
            trace_id=trace_id,
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
        update_analysis_record(record_id, result_data=result_text, token_usage=tokens, status="done")
        _extract_candidates_safely(record_id, "fund_analysis", result_text)
        logger.info(f"指定基金分析完成 record_id={record_id}")
    except Exception as e:
        logger.error(f"指定基金分析失败 record_id={record_id}: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))


@router.get("/api/portfolio/analysis/fund-analysis/records")
async def list_fund_analysis_records_api(limit: int = 10):
    """列出指定基金分析历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="what_if", limit=limit)
    return {"records": records}


@router.get("/api/analysis/compare-funds")
async def compare_funds_api(fund_a: str, fund_b: str):
    """基金六维对比 — 收益/回撤/波动/费率/规模/经理。"""
    if not fund_a or not fund_b:
        raise HTTPException(400, "请提供两只基金代码")
    if fund_a == fund_b:
        raise HTTPException(400, "请提供两只不同的基金代码")
    try:
        result = compare_funds(fund_a.strip(), fund_b.strip())
        return result
    except Exception as e:
        logger.error(f"基金对比失败 {fund_a} vs {fund_b}: {e}", exc_info=True)
        raise HTTPException(500, f"基金对比失败: {str(e)}")
