"""全景诊断 — POST /api/portfolio/analysis/panorama"""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException

from infra.state import track_agent as _track_agent, untrack_agent as _untrack_agent
from db import (
    list_holdings, get_portfolio_diversification,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    get_analysis_agent_by_name,
    save_analysis_conclusion,
)
from db.portfolio import update_analysis_record, get_analysis_record_status
from db.agent_analysis_log import create_analysis_log, complete_analysis_log
from db.config import get_config as _get_config, get_config_int, get_config_float
from services.rag import build_rag_context_with_details  # 保留向后兼容
from models.portfolio import PanoramaAnalysisRequest
from ._shared import (
    _get_mcp_context, _get_holdings_valuation_context, _format_news_section,
    inject_rag_context,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-panorama"])

_background_tasks: set = set()


def _extract_candidates_safely(record_id: int, analysis_type: str, result_text: str):
    try:
        from db.decisions import extract_recommendation_candidates_from_analysis
        extract_recommendation_candidates_from_analysis(record_id, analysis_type, result_text)
    except Exception as e:
        logger.warning(f"建议候选抽取失败 record_id={record_id}: {e}")


@router.post("/api/portfolio/analysis/panorama")
async def panorama_analysis_api(req: PanoramaAnalysisRequest):
    """全景诊断 — 从全局视角诊断投资组合健康状况（异步执行）。"""
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

    agent = get_analysis_agent_by_name("全景诊断分析师")
    if not agent:
        raise HTTPException(404, "全景诊断分析师未配置")

    record_id = create_portfolio_analysis_record(
        analysis_type="panorama",
        summary=f"全景诊断 · {len(holdings)}只基金",
        input_data=json.dumps({"holdings_count": len(holdings)}, ensure_ascii=False),
        result_data="",
        status="running",
        agent_id=agent["id"],
    )

    task = asyncio.create_task(_run_panorama_async(record_id, agent["system_prompt"], holdings, agent["id"], agent["name"]))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"ok": True, "id": record_id, "status": "running"}


async def _run_panorama_async(record_id: int, system_prompt: str, holdings: list, agent_id: int = None, agent_name: str = ""):
    """后台执行全景诊断分析。"""
    uid = f"panorama_{record_id}"
    try:
        diversification = get_portfolio_diversification()
        total_value = diversification.get('total_value', 1) or 1

        holdings_lines = []
        for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
            pct = (h.get('current_value', 0) or 0) / total_value * 100
            holdings_lines.append(
                f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
                f"账户 {h.get('account') or _get_config('portfolio.default_account', '花无缺')}, "
                f"市值 {(h.get('current_value') or 0):.2f}, "
                f"盈亏 {(h.get('profit_loss') or 0):.2f} ({(h.get('profit_rate') or 0)*100:.1f}%), "
                f"占比 {pct:.1f}%"
            )

        type_dist = diversification.get('type_distribution', {})
        type_lines = [f"  - {k}: {v:.1f}%" for k, v in type_dist.items()]

        mcp_context = _get_mcp_context(holdings)
        valuation_context = _get_holdings_valuation_context(holdings)
        news_section = _format_news_section(mcp_context)

        rag_context = ""
        try:
            fund_names = " ".join([h.get("fund_name", "") for h in holdings[:5]])
            rag_context = inject_rag_context(
                base_query="投资组合 资产配置 风险分析",
                extra_keywords=fund_names,
                caller="panorama",
            )
        except Exception as e:
            logger.warning(f"RAG 检索失败: {e}")

        # 组合约束注入
        facts_block = ""
        try:
            from services.portfolio_fact_layer import build_portfolio_facts
            facts = build_portfolio_facts()
            facts_block = json.dumps(facts, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass

        user_content = (
            f"## 组合约束（系统注入，优先级最高）\n```json\n{facts_block}\n```\n\n---\n\n"
            f"## 持仓明细\n" + "\n".join(holdings_lines) +
            f"\n\n## 类型分布\n" + "\n".join(type_lines) +
            f"\n\n## 集中度\n- 前3大持仓占比: {diversification.get('top3_concentration', 0):.1f}%"
            f"\n- 前5大持仓占比: {diversification.get('top5_concentration', 0):.1f}%\n"
            f"\n## MCP 专业数据\n{json.dumps(mcp_context, ensure_ascii=False, indent=2)}\n"
            f"\n{valuation_context}"
            f"\n\n{news_section}"
            f"{rag_context if rag_context else ''}"
        )

        _track_agent(uid, "全景诊断分析师", "持仓诊断")
        import uuid
        trace_id = f"log_{uuid.uuid4().hex[:12]}"
        _start_ts = time.time()
        create_analysis_log(
            trace_id=trace_id, agent_id=agent_id, agent_name=agent_name,
            analysis_type="panorama", source_table="portfolio_analysis_records",
            source_id=record_id, query=user_content[:300],
            input_summary="全景诊断",
        )
        from services.llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_panorama",
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
        _elapsed_ms = int((time.time() - _start_ts) * 1000)
        complete_analysis_log(trace_id=trace_id, status="done", duration_ms=_elapsed_ms, token_usage=tokens)
        _extract_candidates_safely(record_id, "panorama", result_text)

        # ── 桥接 B：保存分析结论 ──
        try:
            summary = result_text[:100].replace("\n", " ").strip() if result_text else ""
            action = "hold"
            for candidate, act in [("减仓", "decrease"), ("加仓", "increase"),
                                    ("买入", "buy"), ("卖出", "sell"),
                                    ("调仓", "rebalance"), ("持有", "hold"),
                                    ("观望", "hold")]:
                if candidate in (result_text or ""):
                    action = act
                    break

            key_vars = []
            for var in ["集中度", "分散", "估值", "仓位", "风险",
                        "收益", "波动", "回撤", "费率", "行业"]:
                if var in (result_text or ""):
                    key_vars.append(var)

            save_analysis_conclusion(
                source_system="independent_analysis",
                source_type="panorama",
                source_id=record_id,
                target_subject="整体组合",
                action=action,
                summary=summary,
                reasoning=result_text[100:250].replace("\n", " ").strip() if len(result_text or "") > 100 else "",
                key_variables=key_vars[:5] if key_vars else None,
            )
        except Exception as e:
            logger.warning(f"全景诊断结论保存失败 record_id={record_id}: {e}")

        logger.info(f"全景诊断完成 record_id={record_id}")

    except Exception as e:
        logger.error(f"全景诊断失败 record_id={record_id}: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))
        _elapsed_ms = int((time.time() - _start_ts) * 1000)
        complete_analysis_log(trace_id=trace_id, status="error", duration_ms=_elapsed_ms, error_msg=str(e))
    finally:
        _untrack_agent(uid)


@router.get("/api/portfolio/analysis/panorama/{record_id}/status")
async def panorama_status_api(record_id: int):
    """查询全景诊断执行状态。"""
    record = get_analysis_record_status(record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    return {
        "id": record["id"],
        "status": record["status"],
        "result": record.get("result_data"),
        "token_usage": record.get("token_usage", 0),
        "error": record.get("error_msg"),
    }


@router.get("/api/portfolio/analysis/{record_id}/status")
async def analysis_status_api(record_id: int):
    """查询任意分析记录执行状态（通用端点）。"""
    record = get_analysis_record_status(record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    return {
        "id": record["id"],
        "status": record["status"],
        "result": record.get("result_data"),
        "token_usage": record.get("token_usage", 0),
        "error": record.get("error_msg"),
    }


@router.get("/api/portfolio/analysis/panorama/records")
async def list_panorama_records_api(limit: int = 10):
    """列出全景诊断历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="panorama", limit=limit)
    return {"records": records}
