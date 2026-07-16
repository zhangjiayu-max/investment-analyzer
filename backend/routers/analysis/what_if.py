"""情景推演 — POST /api/portfolio/analysis/what-if

独立情景引擎：支持市场下跌/估值修复/降息/加息/政策刺激/流动性收紧/自定义等情景。
政策类情景路由到 macro_strategist + risk_assessor 双专家协作；
市场类情景路由到 risk_assessor 单专家。
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

# 情景 → 主专家映射（政策类需宏观专家，市场类用风险专家）
_SCENARIO_EXPERT = {
    "market_drop": "risk_assessor",
    "repair_to_median": "risk_assessor",
    "repair_to_opportunity": "risk_assessor",
    "rate_cut": "macro_strategist",
    "rate_hike": "macro_strategist",
    "policy_stimulus": "macro_strategist",
    "liquidity_tighten": "macro_strategist",
    "custom": "macro_strategist",
}

# 政策类情景需要双专家协作（宏观分析 + 风险评估）
_POLICY_SCENARIOS = {"rate_cut", "rate_hike", "policy_stimulus", "liquidity_tighten", "custom"}


@router.post("/api/portfolio/analysis/what-if")
async def what_if_analysis_api(req: WhatIfRequest):
    """模式 4：情景推演 — 独立情景引擎。"""
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

    # 情景描述构建
    scenario_desc = {
        "market_drop": f"市场整体下跌 {req.parameter or 10}%",
        "repair_to_median": "估值修复到历史中位数",
        "repair_to_opportunity": "估值修复到机会值（20%分位）",
        "rate_cut": f"央行降息 {req.parameter or 25}个基点",
        "rate_hike": f"央行加息 {req.parameter or 25}个基点",
        "policy_stimulus": "出台重大经济刺激政策（如大规模基建/消费补贴）",
        "liquidity_tighten": "流动性收紧（如央行收紧MLF/提高准备金率）",
        "custom": req.custom_scenario or "自定义情景",
    }.get(req.scenario, req.scenario)

    user_content = (
        f"## 用户选择的情景\n{scenario_desc}"
        f"{'(参数: ' + str(req.parameter) + ')' if req.parameter and req.scenario not in ('repair_to_median', 'repair_to_opportunity', 'policy_stimulus') else ''}"
        f"\n\n## 当前持仓\n" + "\n".join(holdings_lines) +
        f"\n总市值: {total_value:.2f}\n"
        f"\n{valuation_context}"
        f"\n\n## 情景推演要求"
        f"\n- 分析该情景发生时的传导路径（宏观→行业→持仓标的）"
        f"\n- 评估对用户当前持仓的冲击（哪些受益/受损，幅度多大）"
        f"\n- 给出应对建议（是否调整仓位，如何调整）"
        f"\n- 标注情景发生概率（基于当前数据推断）"
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

    # 根据情景类型选择专家
    primary_expert = _SCENARIO_EXPERT.get(req.scenario, "risk_assessor")

    try:
        from agent.multi_agent import run_specialist
        # 政策类情景：宏观专家 + 风险专家双专家协作
        if req.scenario in _POLICY_SCENARIOS:
            macro_result = await asyncio.to_thread(
                run_specialist, "macro_strategist", user_content
            )
            macro_text = macro_result.get("analysis", "") or ""
            # 将宏观分析注入风险专家上下文
            risk_content = user_content + f"\n\n## 宏观策略师的分析（参考）\n{macro_text}"
            risk_result = await asyncio.to_thread(
                run_specialist, "risk_assessor", risk_content
            )
            risk_text = risk_result.get("analysis", "") or ""
            # 合并结果
            result_text = f"## 宏观策略师分析\n{macro_text}\n\n## 风险评估师分析\n{risk_text}"
            tokens = (macro_result.get("tokens_used", 0) or 0) + (risk_result.get("tokens_used", 0) or 0)
        else:
            # 市场类情景：单专家
            specialist_result = await asyncio.to_thread(
                run_specialist, primary_expert, user_content
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
        input_data=json.dumps({"scenario": req.scenario, "parameter": req.parameter, "custom_scenario": req.custom_scenario}, ensure_ascii=False),
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

    return {"id": record_id, "result": result_text, "token_usage": tokens, "expert": primary_expert}


@router.get("/api/portfolio/analysis/what-if/records")
async def list_whatif_records_api(limit: int = 10):
    """列出情景推演历史记录。"""
    from db import list_portfolio_analysis_records
    records = list_portfolio_analysis_records(analysis_type="what_if", limit=limit)
    return {"records": records}
