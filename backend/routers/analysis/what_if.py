"""情景推演 — POST /api/portfolio/analysis/what-if"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from db import (
    list_holdings, get_analysis_agent,
    create_portfolio_analysis_record,
)
from db.config import get_config_int, get_config_float
from models.portfolio import WhatIfRequest
from ._shared import _get_valuation_context

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-what-if"])

_background_tasks: set = set()


@router.post("/api/portfolio/analysis/what-if")
async def what_if_analysis_api(req: WhatIfRequest):
    """模式 4：情景推演 — 模拟不同市场情景下的组合变化。"""
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

    agent = get_analysis_agent(6)
    if not agent:
        raise HTTPException(404, "情景推演分析师未配置")

    # 持仓数据
    total_value = sum(h.get('current_value', 0) or 0 for h in holdings)
    holdings_lines = []
    for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
        pct = (h.get('current_value', 0) or 0) / total_value * 100 if total_value else 0
        holdings_lines.append(
            f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
            f"市值 {(h.get('current_value') or 0):.2f}, 占比 {pct:.1f}%, "
            f"成本 {(h.get('total_cost') or 0):.2f}"
        )

    # 估值数据
    valuation_context = _get_valuation_context()

    scenario_desc = {
        "market_drop": f"市场整体下跌 {req.parameter or 10}%",
        "repair_to_median": "估值修复到历史中位数",
        "repair_to_opportunity": "估值修复到机会值（20%分位）",
    }.get(req.scenario, req.scenario)

    user_content = (
        f"## 用户选择的情景\n{scenario_desc}"
        f"{'(跌幅: ' + str(req.parameter) + '%)' if req.scenario == 'market_drop' and req.parameter else ''}"
        f"\n\n## 当前持仓\n" + "\n".join(holdings_lines) +
        f"\n总市值: {total_value:.2f}\n"
        f"\n{valuation_context}"
    )

    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_whatif",
            model=MODEL,
            messages=[
                {"role": "system", "content": agent["system_prompt"]},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_analysis', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8192),
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"情景推演失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    record_id = create_portfolio_analysis_record(
        analysis_type="what_if",
        summary=f"情景推演 · {scenario_desc}",
        input_data=json.dumps({"scenario": req.scenario, "parameter": req.parameter}, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=6,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


@router.get("/api/portfolio/analysis/what-if/records")
async def list_whatif_records_api(limit: int = 10):
    """列出情景推演历史记录。"""
    from db import list_portfolio_analysis_records
    records = list_portfolio_analysis_records(analysis_type="what_if", limit=limit)
    return {"records": records}
