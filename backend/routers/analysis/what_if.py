"""情景推演 — POST /api/portfolio/analysis/what-if

已合并到 risk_assessor 专家。保留路由用于前端/历史兼容，内部委托 run_specialist("risk_assessor", ...)。
"""
import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException

from db import (
    list_holdings,
    create_portfolio_analysis_record,
    get_analysis_agent_by_name,
)
from db.agent_analysis_log import create_analysis_log, complete_analysis_log
from models.portfolio import WhatIfRequest
from ._shared import _get_valuation_context

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-what-if"])


@router.post("/api/portfolio/analysis/what-if")
async def what_if_analysis_api(req: WhatIfRequest):
    """模式 4：情景推演 — 已合并到 risk_assessor 专家。"""
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

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

    # 动态查找情景推演分析师（避免硬编码 id 错位）
    _whatif_agent = get_analysis_agent_by_name("情景推演分析师")
    _whatif_agent_id = _whatif_agent.get("id") if _whatif_agent else None
    _whatif_agent_name = _whatif_agent.get("name", "情景推演分析师") if _whatif_agent else "情景推演分析师"

    trace_id = f"log_{uuid.uuid4().hex[:12]}"
    _start_ts = time.time()
    # 先创建 running 记录（source_id 暂为 None，成功后补更新）
    try:
        create_analysis_log(
            trace_id=trace_id, agent_id=_whatif_agent_id, agent_name=_whatif_agent_name,
            analysis_type="what_if", source_table="portfolio_analysis_records",
            source_id=None, query=user_content[:300],
            input_summary=f"情景:{scenario_desc}",
        )
    except Exception as _e:
        logger.warning(f"create_analysis_log(running) 失败: {_e}")

    try:
        from agent.multi_agent import run_specialist
        specialist_result = await asyncio.to_thread(
            run_specialist, "risk_assessor", user_content
        )
        result_text = specialist_result.get("analysis", "") or ""
        tokens = specialist_result.get("tokens_used", 0) or 0
    except Exception as e:
        logger.error(f"情景推演失败: {e}")
        _elapsed_ms = int((time.time() - _start_ts) * 1000)
        complete_analysis_log(trace_id=trace_id, status="error", duration_ms=_elapsed_ms, error_msg=str(e))
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    record_id = create_portfolio_analysis_record(
        analysis_type="what_if",
        summary=f"情景推演 · {scenario_desc}",
        input_data=json.dumps({"scenario": req.scenario, "parameter": req.parameter}, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=_whatif_agent_id,
    )
    _elapsed_ms = int((time.time() - _start_ts) * 1000)
    # 更新 source_id 并标记完成（避免重复 INSERT 触发 UNIQUE 冲突）
    try:
        from db.agent_analysis_log import update_analysis_log_source
        update_analysis_log_source(trace_id=trace_id, source_id=record_id)
    except Exception:
        pass  # 容错：即使更新失败也不影响主流程
    complete_analysis_log(trace_id=trace_id, status="done", duration_ms=_elapsed_ms, token_usage=tokens)

    return {"id": record_id, "result": result_text, "token_usage": tokens, "merged_into": "risk_assessor"}


@router.get("/api/portfolio/analysis/what-if/records")
async def list_whatif_records_api(limit: int = 10):
    """列出情景推演历史记录。"""
    from db import list_portfolio_analysis_records
    records = list_portfolio_analysis_records(analysis_type="what_if", limit=limit)
    return {"records": records}
