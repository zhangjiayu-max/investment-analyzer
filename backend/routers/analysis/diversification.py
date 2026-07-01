"""分散度分析 + AI 摘要 + 穿透 + 表现 + 交易汇总"""
import asyncio
import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, HTTPException

from state import track_agent as _track_agent, untrack_agent as _untrack_agent
from db import (
    list_holdings, get_holding, list_transactions,
    get_portfolio_diversification, get_portfolio_penetration,
    get_transaction_summary,
    get_analysis_agent,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    create_async_task, update_async_task,
    search_indexes_by_keyword, get_latest_valuation,
)
from db.portfolio import update_analysis_record
from db.config import get_config as _get_config, get_config_int, get_config_float
from rag import build_rag_context_with_details
from ._shared import _parse_mcp_pct_pairs, _parse_mcp_correlation

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-diversification"])

_background_tasks: set = set()


_DIVERSIFICATION_CACHE_KEY = "diversification_api_default"
_DIVERSIFICATION_CACHE_TTL_SECONDS = 3600  # 1 小时


@router.get("/api/portfolio/analysis/diversification")
async def portfolio_diversification_api(force_refresh: bool = False):
    """分析持仓分散度：基金数量、指数分布、类型分布、仓位集中度。

    默认使用 1 小时本地缓存，避免重复调用 MCP/akshare；force_refresh=true 可强制刷新。
    MCP 调用失败时返回缓存/本地数据 + 友好 warning，不阻塞主流程。
    """
    from db.portfolio import save_analysis_cache, get_analysis_cache

    # 尝试读取缓存
    if not force_refresh:
        cached = get_analysis_cache(_DIVERSIFICATION_CACHE_KEY)
        if cached:
            cached_at = cached.get("cached_at")
            try:
                if cached_at and (time.time() - cached_at) < _DIVERSIFICATION_CACHE_TTL_SECONDS:
                    return cached
            except Exception:
                pass

    result = get_portfolio_diversification()
    warning = None

    # 补充 MCP 分析数据（每个 MCP 调用独立 try/except，失败时记录 warning）
    mcp_data = {}
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        holdings = list_holdings()
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code") and h["fund_code"].strip()]

        if not fund_codes:
            mcp_data["error"] = "持仓中没有有效的基金代码"
        else:
            # 构建持仓映射 {fund_code: current_value} 用于 MCP 参数
            holding_map = {h["fund_code"]: h.get("current_value", 0) or 0 for h in holdings if h.get("fund_code")}

            # 资产大类穿透分析（需 holdingList: [{fundCode, amount}]）
            try:
                raw = mcp.call_tool_text("GetFundAssetClassAnalysis", {
                    "holdingList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes],
                })
                mcp_data["asset_class"] = {"status": "ok", "data": raw}
            except Exception as e:
                logger.warning(f"[diversification] MCP 资产大类分析失败: {e}")
                mcp_data["asset_class"] = {"status": "degraded", "message": "资产大类分析暂不可用"}

            # 基金相关性分析（需 fundList: [{fundCode}]）
            try:
                raw = mcp.call_tool_text("GetFundsCorrelation", {
                    "fundList": [{"fundCode": c} for c in fund_codes],
                })
                mcp_data["correlation"] = {"status": "ok", "data": raw}
            except Exception as e:
                logger.warning(f"[diversification] MCP 相关性分析失败: {e}")
                mcp_data["correlation"] = {"status": "degraded", "message": "相关性分析暂不可用"}

            # 持仓最大基金的行业配置
            try:
                top = max(holdings, key=lambda h: (h.get("current_value", 0) or 0))
                raw = mcp.call_tool_text("getFundIndustryAllocation", {
                    "fundCode": top["fund_code"],
                })
                mcp_data["top_holding_industry"] = {
                    "status": "ok",
                    "fund_name": top.get("fund_name", ""),
                    "fund_code": top["fund_code"],
                    "data": raw,
                }
            except Exception as e:
                logger.warning(f"[diversification] MCP 行业配置分析失败: {e}")
                mcp_data["top_holding_industry"] = {"status": "degraded", "message": "行业配置分析暂不可用"}

            # 市场行情
            try:
                raw = mcp.get_latest_quotations()
                mcp_data["market"] = {"status": "ok", "data": raw}
            except Exception as e:
                logger.warning(f"[diversification] MCP 市场行情获取失败: {e}")
                mcp_data["market"] = {"status": "degraded", "message": "市场行情暂不可用"}
    except Exception as e:
        logger.warning(f"[diversification] MCP 客户端异常: {e}")
        mcp_data["error"] = "MCP 服务暂不可用，已返回本地持仓数据"
        warning = "MCP 服务暂不可用，部分分析基于本地缓存数据"

    result["mcp"] = mcp_data
    result["warning"] = warning
    result["cached_at"] = time.time()
    save_analysis_cache(_DIVERSIFICATION_CACHE_KEY, result)
    return result


@router.post("/api/portfolio/analysis/diversification/ai-summary")
async def portfolio_diversification_ai_summary(agent_id: int = 2):
    """基于 MCP + 持仓数据，后台生成 AI 分散度分析解读（异步模式）。"""
    agent = get_analysis_agent(agent_id)
    if not agent:
        raise HTTPException(404, "分析 Agent 不存在")
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")
    result = get_portfolio_diversification()
    record_id = create_portfolio_analysis_record(
        analysis_type="diversification_ai",
        summary=f"分散度解读 · {result.get('holding_count', len(holdings))}只基金",
        input_data=json.dumps({"holdings": result}, ensure_ascii=False),
        result_data="",
        token_usage=0,
        agent_id=agent_id,
        status="running",
    )
    # 创建 async_task 记录用于通用状态查询
    task_id = create_async_task("diversification_ai", caller="diversification_ai")
    task = asyncio.create_task(_run_diversification_ai_async(task_id, record_id, agent_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "record_id": record_id, "status": "running"}


async def _run_diversification_ai_async(task_id: int, record_id: int, agent_id: int = 2):
    """后台执行 AI 分散度分析解读（包裹 async_task 状态跟踪）。"""
    try:
        await _run_diversification_ai_summary_async(record_id, agent_id)
        # 读取 record 结果
        from db import get_portfolio_analysis_record
        record = get_portfolio_analysis_record(record_id)
        if record and record.get("status") == "done":
            update_async_task(task_id, status="done", result={"record_id": record_id, "status": "done"})
        else:
            error_msg = record.get("error_msg", "未知错误") if record else "记录不存在"
            update_async_task(task_id, status="error", error_msg=error_msg)
    except Exception as e:
        logger.error(f"分散度 AI 分析失败 task_id={task_id}, record_id={record_id}: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _run_diversification_ai_summary_async(record_id: int, agent_id: int = 2):
    """后台执行 AI 分散度分析解读。"""
    # 1. 获取 agent 配置
    agent = get_analysis_agent(agent_id)
    system_prompt = agent["system_prompt"]

    # 2. 获取持仓 + 分散度数据
    result = get_portfolio_diversification()
    holdings = list_holdings()

    total_value = result.get('total_value', 1) or 1

    # 3. 预计算：基金集中度检验（本地数据，高置信度）
    concentration_items = []
    for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
        pct = (h.get('current_value', 0) or 0) / total_value * 100
        if pct > 25:
            level = "⚠️ 高度集中"
        elif pct > 15:
            level = "⚡ 适度集中"
        else:
            level = "✅ 合理"
        concentration_items.append(f"- {level} {h.get('fund_name','')}({h.get('fund_code','')}): {pct:.1f}%（阈值: >25%高度集中, >15%适度集中）")

    # 4. 获取 MCP 数据 + 结构化提取
    mcp_raw_sections = []
    mcp_parsed = {"correlation": [], "asset_class_pcts": [], "industry_pcts": {}}

    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code") and h["fund_code"].strip()]

        if fund_codes:
            holding_map = {h["fund_code"]: h.get("current_value", 0) or 0 for h in holdings if h.get("fund_code")}

            # 4a. 资产大类穿透分析
            try:
                raw = mcp.call_tool_text("GetFundAssetClassAnalysis", {
                    "holdingList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
                })
                mcp_raw_sections.append(f"【资产大类穿透分析】\n{raw}")
                mcp_parsed["asset_class_pcts"] = _parse_mcp_pct_pairs(raw)
            except Exception as e:
                mcp_raw_sections.append(f"【资产大类穿透分析】调用失败: {e}")

            # 4b. 基金相关性分析
            try:
                raw = mcp.call_tool_text("GetFundsCorrelation", {
                    "fundList": [{"fundCode": c} for c in fund_codes]
                })
                mcp_raw_sections.append(f"【基金相关性分析】\n{raw}")
                mcp_parsed["correlation"] = _parse_mcp_correlation(raw)
            except Exception as e:
                mcp_raw_sections.append(f"【基金相关性分析】调用失败: {e}")

            # 4c. 行业配置（top 5 基金，原来是 top 1）
            sorted_holdings = sorted(holdings, key=lambda h: (h.get("current_value", 0) or 0), reverse=True)
            for h in sorted_holdings[:5]:
                fc = h.get("fund_code", "")
                if fc and fc.strip():
                    try:
                        raw = mcp.call_tool_text("getFundIndustryAllocation", {"fundCode": fc})
                        label = f"{h.get('fund_name','')}行业配置"
                        mcp_raw_sections.append(f"【{label}】\n{raw}")
                        mcp_parsed["industry_pcts"][fc] = {"name": h.get("fund_name", ""), "pcts": _parse_mcp_pct_pairs(raw)}
                    except Exception:
                        pass

            # 4d. 组合诊断（新增：MCP 第三方综合诊断作为参考）
            try:
                raw = mcp.call_tool_text("DiagnoseFundPortfolio", {
                    "fundList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
                })
                mcp_raw_sections.append(f"【组合诊断】\n{raw}")
            except Exception:
                pass

            # 4e. 市场行情
            try:
                raw = mcp.get_latest_quotations()
                mcp_raw_sections.append(f"【市场行情】\n{raw}")
            except Exception:
                pass

            # 4f. 政策热点新闻
            try:
                hot_raw = mcp.call_tool_text("SearchHotTopic", {})
                mcp_raw_sections.append(f"【市场热点】\n{hot_raw}")
            except Exception:
                pass

            # 4g. 对主要持仓行业搜索相关新闻
            industry_keywords = set()
            for h in sorted_holdings[:3]:
                idx = (h.get("index_name") or "").strip()
                if idx and idx != "该基金无跟踪标的":
                    kw = idx.replace("指数", "").replace("中证", "").replace("全指", "").strip()
                    if kw:
                        industry_keywords.add(kw)
            for kw in industry_keywords:
                try:
                    news_raw = mcp.call_tool_text("SearchFinancialNews", {"keyword": kw, "pageSize": 3})
                    mcp_raw_sections.append(f"【{kw}相关新闻】\n{news_raw}")
                except Exception:
                    pass
    except Exception as e:
        mcp_raw_sections.append(f"MCP 数据获取异常: {e}")

    # 5. 预计算：相关性阈值检验
    correlation_lines = []
    if mcp_parsed["correlation"]:
        for cp in mcp_parsed["correlation"]:
            c = cp["coefficient"]
            if c >= 0.85:
                correlation_lines.append(f"- ⚠️ {cp['fund_a']} vs {cp['fund_b']}: {c:.2f}（强同向波动 ≥0.85）")
            elif c >= 0.7:
                correlation_lines.append(f"- ⚡ {cp['fund_a']} vs {cp['fund_b']}: {c:.2f}（中等相关 0.7-0.85）")
            else:
                correlation_lines.append(f"- ✅ {cp['fund_a']} vs {cp['fund_b']}: {c:.2f}（分散有效 <0.7）")
    if not correlation_lines:
        correlation_lines.append("（相关性数据暂缺，无法检验）")

    correlation_block = "\n".join(correlation_lines)

    # 6. 预计算：行业集中度汇总（从解析的 MCP 行业数据中提取）
    industry_summary_lines = []
    total_industry_pcts = {}
    for fc, indata in mcp_parsed["industry_pcts"].items():
        for label, pct in indata["pcts"]:
            total_industry_pcts[label] = total_industry_pcts.get(label, 0) + pct
    if total_industry_pcts:
        sorted_industries = sorted(total_industry_pcts.items(), key=lambda x: -x[1])
        industry_summary_lines.append(f"累计行业暴露（top {len(mcp_parsed['industry_pcts'])} 只基金）:")
        for label, pct in sorted_industries[:5]:
            flag = "⚠️" if pct > 35 else ("⚡" if pct > 20 else "✅")
            industry_summary_lines.append(f"- {flag} {label}: {pct:.0f}%（阈值: >35%过高, >20%偏高）")
    industry_block = "\n".join(industry_summary_lines) if industry_summary_lines else "（行业数据暂缺）"

    # 7. 资产大类汇总
    asset_class_block = ""
    if mcp_parsed["asset_class_pcts"]:
        asset_lines = [f"- {label}: {pct:.0f}%" for label, pct in mcp_parsed["asset_class_pcts"]]
        asset_class_block = "底层资产穿透:\n" + "\n".join(asset_lines)

    # 7.5 估值参考（查询持仓基金跟踪指数的估值数据）
    valuation_ref_lines = []
    seen_indexes = set()
    for h in holdings:
        idx_name = (h.get("index_name") or "").strip()
        if not idx_name or idx_name == "该基金无跟踪标的" or idx_name in seen_indexes:
            continue
        seen_indexes.add(idx_name)
        try:
            matches = search_indexes_by_keyword(idx_name.replace("指数", ""))
            for m in matches:
                val = get_latest_valuation(m["index_code"])
                if val and val.get("percentile") is not None:
                    pct = val["percentile"]
                    z = val.get("zscore")
                    metric = val.get("metric_type", "")
                    current_val = val.get("current_value")
                    level = "🔥 高估" if pct > 80 else ("⚠️ 偏高" if pct > 50 else "✅ 低估" if pct < 20 else "⚡ 适中")
                    z_note = f"z-score={z:+.2f}" if z is not None else ""
                    valuation_ref_lines.append(
                        f"- {m['index_name']}({metric}={current_val}, "
                        f"历史分位={pct:.1f}% {level} {z_note})"
                    )
                    break
        except Exception:
            pass
    valuation_block = ""
    if valuation_ref_lines:
        valuation_block = "跟踪指数估值参考:\n" + "\n".join(valuation_ref_lines)
        valuation_block += "\n\n💡 估值分位<20%为低估区域，可适度容忍集中；>80%为高估区域，宜警惕集中风险。"

    # 8. 拼装 LLM prompt（预计算分析 + 原始 MCP 数据）
    holdings_text = "\n".join(
        f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
        f"持仓占比 {(h.get('current_value',0) or 0) / total_value * 100:.1f}%, "
        f"盈亏 {h.get('profit_loss',0):+.2f}元"
        for h in holdings
    )

    mcp_raw_block = "\n\n".join(mcp_raw_sections) if mcp_raw_sections else "（无 MCP 数据）"

    asset_class_section = f"### 资产大类穿透\n{asset_class_block}" if asset_class_block else ""
    valuation_section = f"### 估值参考（跟踪指数）\n{valuation_block}" if valuation_block else ""

    user_content = f"""## 持仓概览
持有基金 {result.get('holding_count',0)} 只 | 总投资 {result.get('total_cost',0):.0f}元 | 总市值 {result.get('total_value',0):.0f}元

## 持仓明细
{holdings_text}

## 类型分布
{json.dumps(result.get('type_distribution',{}), ensure_ascii=False)}

## 📊 预计算分析（系统已自动计算以下指标供你参考）

### 基金集中度检验
{chr(10).join(concentration_items)}

### 相关性检验
{correlation_block}

### 行业集中度（基于 MCP 行业配置数据）
{industry_block}

{asset_class_section}

{valuation_section}

## 📄 MCP 原始数据（供验证和深度参考）
{mcp_raw_block}

请对以上持仓分散度进行专业解读。"""

    uid = f"diversification_{int(time.time())}"
    trace_id = f"divr_{uuid.uuid4().hex[:12]}"
    _track_agent(uid, "分散度分析师", "持仓分散度解读")
    logger.info(f"[trace:{trace_id}] 分散度分析师开始 record_id={record_id}")
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="diversification_analysis",
            trace_id=trace_id,
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_analysis', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8192),
        ))
        analysis = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
        logger.info(f"[trace:{trace_id}] 分散度分析师完成 record_id={record_id} tokens={tokens}")
    except Exception as e:
        logger.error(f"[trace:{trace_id}] 分散度 AI 分析失败 record_id={record_id}: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))
        return
    finally:
        _untrack_agent(uid)

    update_analysis_record(
        record_id,
        result_data=analysis,
        token_usage=tokens,
        status="done",
        error_msg="",
    )
    logger.info(f"分散度 AI 分析完成 record_id={record_id}")


@router.get("/api/portfolio/analysis/ai-summary/today-status")
async def portfolio_ai_summary_today_status():
    """查询今天是否已有 AI 分散度分析结果。"""
    records = list_portfolio_analysis_records(analysis_type="diversification_ai", limit=5)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    for r in records:
        if r.get("created_at", "").startswith(today):
            return {"analyzed_today": True, "record_id": r["id"]}
    return {"analyzed_today": False, "record_id": None}


@router.get("/api/portfolio/analysis/penetration")
async def portfolio_penetration_api():
    """跨基金持仓穿透分析：加权聚合底层股票持仓。"""
    result = get_portfolio_penetration()
    return result


@router.get("/api/portfolio/analysis/{holding_id}/performance")
async def holding_performance_api(holding_id: int):
    """分析单只持仓基金的投资表现。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    fund_code = holding["fund_code"]
    txs = list_transactions(fund_code=fund_code, limit=100)
    buy_txs = [t for t in txs if t["transaction_type"] == "buy"]
    sell_txs = [t for t in txs if t["transaction_type"] == "sell"]
    buy_total = sum(t.get("amount", 0) or 0 for t in buy_txs)
    sell_total = sum(t.get("amount", 0) or 0 for t in sell_txs)
    return {
        "fund_code": fund_code,
        "fund_name": holding.get("fund_name", ""),
        "shares": holding.get("shares", 0),
        "cost_price": holding.get("cost_price", 0),
        "current_price": holding.get("current_price", 0),
        "total_cost": holding.get("total_cost", 0),
        "current_value": holding.get("current_value", 0),
        "profit_loss": round(holding.get("profit_loss", 0) or 0, 2),
        "profit_rate": round((holding.get("profit_rate", 0) or 0) * 100, 2),
        "buy_count": len(buy_txs),
        "sell_count": len(sell_txs),
        "buy_total": round(buy_total, 2),
        "sell_total": round(sell_total, 2),
    }


@router.get("/api/portfolio/analysis/transactions-summary")
async def transactions_summary_api():
    """交易行为汇总分析。"""
    return get_transaction_summary()
