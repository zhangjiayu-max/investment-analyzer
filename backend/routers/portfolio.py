"""持仓路由 — /api/portfolio/*

含五大板块：
  - 持仓 CRUD：持仓列表/汇总/创建、现金、基金净值历史、清空
  - 调仓管理：调仓分析、配置 CRUD（获取/更新/历史/详情/回滚）
  - 持仓分析：分散度、AI 汇总、穿透、表现、交易汇总、AI 分析、AI 记录、反馈、bad-case、全景、深度、交易复盘、情景推演、今日状态
  - 风险预警：列表、未读数、标记已读、删除、生成
  - 交易标签：添加/移除/获取交易标签
"""

import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from state import (
    track_agent as _track_agent,
    untrack_agent as _untrack_agent,
)
from db import (
    create_holding, get_holding, list_holdings, update_holding, delete_holding, get_portfolio_summary,
    create_transaction, list_transactions, confirm_transaction, settle_transaction, delete_transaction,
    refresh_holding_price, refresh_all_fund_prices, fetch_fund_nav,
    lookup_fund_info, get_fund_holdings,
    get_portfolio_diversification, get_transaction_summary, clear_all_portfolio_data,
    get_cash_balance, add_cash,
    get_fund_nav_history,
    create_alert, list_alerts, get_unread_alert_count, mark_alert_read, delete_alert,
    add_transaction_tag, remove_transaction_tag, get_transaction_tags,
    create_portfolio_analysis_record, list_portfolio_analysis_records,
    get_portfolio_analysis_record, delete_portfolio_analysis_record,
    update_analysis_feedback, list_bad_cases, list_all_bad_cases,
    get_analysis_agent,
    get_latest_valuation, get_valuation_history, list_valuation_indexes, list_index_freshness,
    search_indexes_by_keyword,
    get_active_rebalance_config, save_rebalance_config,
    list_rebalance_configs, get_rebalance_config_by_id, rollback_rebalance_config,
    set_cash_balance, get_portfolio_penetration,
)
from mcp.trading_calendar import expected_confirm_date
from rag import build_rag_context_with_details, log_rag_search
from models.portfolio import (
    CreateHoldingRequest, UpdateHoldingRequest,
    CreateTransactionRequest, ConfirmTransactionRequest,
    CreateAlertRequest, TagRequest, AdjustCashRequest,
    PortfolioAiAnalysisRequest, FeedbackRequest,
    PanoramaAnalysisRequest, DeepDiveRequest, TradeReviewRequest, WhatIfRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio"])


# ══════════════════════════════════════════════════════
# 持仓管理 API
# ══════════════════════════════════════════════════════


@router.get("/api/portfolio")
async def list_portfolio_api(account: str = None):
    """获取所有持仓。可选 ?account=花无缺 筛选。"""
    return {"holdings": list_holdings(account=account) if account else list_holdings()}


@router.get("/api/portfolio/summary")
async def portfolio_summary_api(account: str = None):
    """获取持仓汇总。可选 ?account=花无缺 筛选。"""
    if account:
        return get_portfolio_summary(account=account)
    return get_portfolio_summary()


@router.get("/api/portfolio/rebalancing")
async def portfolio_rebalancing_api():
    """获取智能调仓建议：结合持仓分布和市场估值，分析偏离度并给出建议。"""
    from rebalancer import analyze_rebalancing_need
    result = analyze_rebalancing_need()
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/api/portfolio/rebalance/config")
async def get_rebalance_config_api():
    """获取当前调仓配置（优先从数据库读取）和所有可用策略预设。"""
    from config import get_rebalance_config, list_strategy_presets, get_strategy_info

    # 优先从数据库读取活跃配置
    db_config = get_active_rebalance_config()
    if db_config:
        return {
            "config": db_config["config"],
            "presets": list_strategy_presets(),
            "current_strategy": get_strategy_info(db_config["strategy"]),
            "config_id": db_config["id"],
            "created_at": db_config["created_at"],
        }

    # 数据库无配置时，返回 env 中的默认值
    return {
        "config": get_rebalance_config(),
        "presets": list_strategy_presets(),
        "current_strategy": get_strategy_info(),
        "config_id": None,
        "created_at": None,
    }


@router.post("/api/portfolio/rebalance/config")
async def update_rebalance_config_api(req: dict):
    """保存调仓配置到数据库（创建新版本）。"""
    from config import get_rebalance_config, get_strategy_info

    # 合并：当前配置 + 本次修改
    current = get_active_rebalance_config()
    if current:
        merged = {**current["config"]}
    else:
        merged = get_rebalance_config()

    # 应用本次修改
    for key, value in req.items():
        if key in ("strategy", "base_allocation", "valuation_adjustment",
                    "valuation_percentiles", "drift_thresholds", "cash_targets",
                    "cash_triggers", "drift_ignore", "undervalue_max", "undervalue_amount"):
            merged[key] = value

    strategy = merged.get("strategy", "balanced")
    config_json = json.dumps(merged, ensure_ascii=False)

    # 生成变更摘要
    changes = [k for k in req if k in merged]
    note = f"修改: {', '.join(changes)}" if changes else None

    config_id = save_rebalance_config(strategy, config_json, note=note)

    return {
        "ok": True,
        "message": f"配置已保存（版本 #{config_id}）",
        "config_id": config_id,
    }


@router.get("/api/portfolio/rebalance/config/history")
async def get_rebalance_config_history_api(limit: int = 20):
    """获取调仓配置变更历史。"""
    return {"records": list_rebalance_configs(limit=limit)}


@router.get("/api/portfolio/rebalance/config/{config_id}")
async def get_rebalance_config_detail_api(config_id: int):
    """获取指定版本的配置详情。"""
    cfg = get_rebalance_config_by_id(config_id)
    if not cfg:
        raise HTTPException(404, "配置版本不存在")
    return cfg


@router.post("/api/portfolio/rebalance/config/{config_id}/rollback")
async def rollback_rebalance_config_api(config_id: int):
    """回滚到指定配置版本。"""
    ok = rollback_rebalance_config(config_id)
    if not ok:
        raise HTTPException(404, "配置版本不存在")
    return {"ok": True, "message": f"已回滚到版本 #{config_id}"}


@router.post("/api/portfolio/clear")
async def clear_portfolio_api():
    """清空所有持仓数据。"""
    clear_all_portfolio_data()
    return {"ok": True, "message": "所有持仓数据已清空"}


@router.get("/api/portfolio/cash")
async def get_cash_api(user_id: str = "default"):
    """获取零钱余额。"""
    return get_cash_balance(user_id)


@router.post("/api/portfolio/cash")
async def adjust_cash_api(req: AdjustCashRequest):
    """调整零钱余额。mode='add' 时 amount 正数存入/负数支出，mode='set' 时直接设置余额。"""
    uid = req.user_id or "default"
    if req.mode == "set":
        new_balance = set_cash_balance(uid, req.amount)
    else:
        new_balance = add_cash(uid, req.amount)
    return {"ok": True, "balance": new_balance}


@router.get("/api/portfolio/fund-nav-history/{fund_code}")
async def fund_nav_history_api(fund_code: str, days: int = 365):
    """获取基金净值历史 + 买卖点标记，用于交易行为图表。"""
    result = get_fund_nav_history(fund_code, days=days)
    if not result:
        raise HTTPException(404, f"获取 {fund_code} 净值数据失败")
    return result


@router.post("/api/portfolio")
async def create_holding_api(req: CreateHoldingRequest):
    """新增持仓。"""
    try:
        holding_id = create_holding(
        fund_code=req.fund_code, fund_name=req.fund_name,
        shares=req.shares, cost_price=req.cost_price,
        current_price=req.current_price,
        index_code=req.index_code, index_name=req.index_name,
        buy_date=req.buy_date, notes=req.notes,
        account=req.account,
        )
        return {"ok": True, "holding_id": holding_id}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── 持仓分析 API ──────────────────────────────────────────


@router.get("/api/portfolio/analysis/diversification")
async def portfolio_diversification_api():
    """分析持仓分散度：基金数量、指数分布、类型分布、仓位集中度。"""
    result = get_portfolio_diversification()

    # 补充 MCP 分析数据（每个 MCP 调用独立 try/except，返回状态信息）
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
                mcp_data["asset_class"] = {"status": "error", "message": str(e)}

            # 基金相关性分析（需 fundList: [{fundCode}]）
            try:
                raw = mcp.call_tool_text("GetFundsCorrelation", {
                    "fundList": [{"fundCode": c} for c in fund_codes],
                })
                mcp_data["correlation"] = {"status": "ok", "data": raw}
            except Exception as e:
                mcp_data["correlation"] = {"status": "error", "message": str(e)}

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
                mcp_data["top_holding_industry"] = {"status": "error", "message": str(e)}

            # 市场行情
            try:
                raw = mcp.get_latest_quotations()
                mcp_data["market"] = {"status": "ok", "data": raw}
            except Exception as e:
                mcp_data["market"] = {"status": "error", "message": str(e)}
    except Exception as e:
        mcp_data["error"] = f"MCP 客户端异常: {e}"

    result["mcp"] = mcp_data
    return result


# ── MCP 解析辅助函数（分散度分析使用） ──

def _parse_mcp_pct_pairs(text: str) -> list[tuple[str, float]]:
    """提取文本中的(标签, 百分比)对，如 ('股票', 85.0)。"""
    try:
        return [(m.group(1), float(m.group(2)))
                for m in re.finditer(r'([一-龥]{2,})\s*[:：]?\s*(\d+\.?\d*)%', text)]
    except Exception:
        return []


def _parse_mcp_correlation(text: str) -> list[dict]:
    """提取基金对相关系数，返回 [{"fund_a": str, "fund_b": str, "coefficient": float}]。"""
    pairs = []
    try:
        # 匹配 "A 和 B 的相关系数为 0.88" / "A vs B: 0.88" / "A, B, 0.88"
        for m in re.finditer(
            r'([一-龥a-zA-Z]{2,})[\s和vsVS、,]+([一-龥a-zA-Z]{2,})[\s\S]{0,40}?(\d+\.\d{2})',
            text,
        ):
            c = float(m.group(3))
            if 0 < c <= 1:
                pairs.append({"fund_a": m.group(1).strip(), "fund_b": m.group(2).strip(), "coefficient": c})
    except Exception:
        pass
    return pairs


@router.post("/api/portfolio/analysis/diversification/ai-summary")
async def portfolio_diversification_ai_summary(agent_id: int = 2):
    """基于 MCP + 持仓数据，生成 AI 分散度分析解读。"""
    # 1. 获取 agent 配置
    agent = get_analysis_agent(agent_id)
    if not agent:
        raise HTTPException(404, "分析 Agent 不存在")
    system_prompt = agent["system_prompt"]

    # 2. 获取持仓 + 分散度数据
    result = get_portfolio_diversification()
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

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

{f"### 资产大类穿透\n{asset_class_block}" if asset_class_block else ""}

{f"### 估值参考（跟踪指数）\n{valuation_block}" if valuation_block else ""}

## 📄 MCP 原始数据（供验证和深度参考）
{mcp_raw_block}

请对以上持仓分散度进行专业解读。"""

    uid = f"diversification_{int(time.time())}"
    _track_agent(uid, "分散度分析师", "持仓分散度解读")
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="diversification_analysis",
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        analysis = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        raise HTTPException(500, f"AI 分析失败: {e}")
    finally:
        _untrack_agent(uid)

    # 保存记录
    record_id = create_portfolio_analysis_record(
        analysis_type="diversification_ai",
        summary=f"分散度解读 · {result.get('holding_count',0)}只基金",
        input_data=json.dumps({"holdings": result}, ensure_ascii=False),
        result_data=analysis,
        token_usage=tokens,
        agent_id=agent_id,
    )

    return {"id": record_id, "result": analysis, "token_usage": tokens}


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


@router.post("/api/portfolio/analysis/ai")
async def portfolio_ai_analysis_api(req: PortfolioAiAnalysisRequest):
    """AI 持仓分析：调用 MCP 工具获取专业数据 + LLM 生成分析报告。"""
    # 1. 获取持仓数据
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

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

    user_question = req.question or "请全面分析我的持仓情况，包括资产配置合理性、风险分散度、各基金表现，以及改进建议。"

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
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"AI 分析失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    # 5. 保存记录
    record_id = create_portfolio_analysis_record(
        analysis_type="ai",
        summary=f"AI持仓分析 · {len(holdings)}只基金",
        input_data=json.dumps({
            "holdings": [{k: h.get(k) for k in ("fund_code", "fund_name", "shares", "cost_price", "current_price", "profit_loss")} for h in holdings],
            "question": user_question,
        }, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
    )

    return {
        "id": record_id,
        "result": result_text,
        "token_usage": tokens,
        "holdings_count": len(holdings),
        "mcp_used": list(mcp_context.keys()),
    }


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


# ── AI 持仓分析 4 模式 ─────────────────────────────────────


def _get_valuation_context() -> str:
    """获取当前所有持仓的估值数据摘要。"""
    try:
        indexes = list_valuation_indexes()
        lines = []

        # 新鲜度总览
        freshness = list_index_freshness()
        stale = [f for f in freshness if f.get("stale_days", 0) >= 10]
        if stale:
            lines.append("[数据新鲜度] 以下指数数据超过10天未更新: " +
                         ", ".join(f"{f['index_name']}({int(f['stale_days'])}天)" for f in stale[:5]))

        for idx in indexes[:20]:
            val = get_latest_valuation(idx.get("index_code", ""))
            if val:
                pe_percentile = val.get("pe_percentile", "N/A")
                pb_percentile = val.get("pb_percentile", "N/A")
                date_str = val.get("snapshot_date", "")
                stale_mark = ""
                if date_str:
                    from datetime import date as dt_date
                    try:
                        d = dt_date.fromisoformat(str(date_str))
                        sd = (dt_date.today() - d).days
                        if sd >= 10:
                            stale_mark = f" [数据过期{sd}天]"
                    except:
                        pass
                lines.append(f"- {idx.get('index_name','')}({idx.get('index_code','')}): PE分位 {pe_percentile}, PB分位 {pb_percentile} [{date_str}]{stale_mark}")
        if lines:
            return "## 估值参考（跟踪指数）\n" + "\n".join(lines)
        return "## 估值参考\n暂无估值数据"
    except Exception as e:
        return f"## 估值参考\n获取失败: {e}"


def _get_holdings_valuation_context(holdings: list) -> str:
    """按持仓匹配估值数据 — 让 LLM 知道每只基金对应的估值。"""
    from db.valuations import search_indexes_by_keyword, get_latest_valuation
    lines = []
    matched = 0
    for h in holdings:
        idx_name = (h.get("index_name") or "").strip()
        fund_name = h.get("fund_name", "")
        if not idx_name or idx_name == "该基金无跟踪标的":
            lines.append(f"- {fund_name}: 无跟踪指数，估值数据不可用")
            continue
        # 搜索匹配的估值数据
        found = False
        search_term = idx_name.replace("指数", "").replace("中证", "").replace("全指", "")
        try:
            matches = search_indexes_by_keyword(search_term)
            for m in matches:
                val = get_latest_valuation(m["index_code"])
                if val and val.get("percentile") is not None:
                    pct = val["percentile"]
                    current_val = val.get("current_value")
                    metric = val.get("metric_type", "PE")
                    level = "🔥高估" if pct > 80 else ("⚠️偏高" if pct > 50 else ("✅低估" if pct < 20 else "⚡适中"))
                    profit_info = ""
                    pr = h.get("profit_rate")
                    if pr is not None:
                        profit_info = f", 盈亏{pr:+.1%}"
                    lines.append(f"- {fund_name} → {m['index_name']}: {metric}={current_val}, 百分位={pct:.0f}% {level}{profit_info}")
                    matched += 1
                    found = True
                    break
        except Exception:
            pass
        if not found:
            lines.append(f"- {fund_name}（{idx_name}）: 估值数据未匹配到")

    header = f"## 持仓估值匹配（已匹配 {matched}/{len([h for h in holdings if (h.get('shares') or 0) > 0])} 只）"
    if not lines:
        return header + "\n暂无估值数据"
    return header + "\n" + "\n".join(lines)


def _format_news_section(mcp_context: dict) -> str:
    """从 mcp_context 中提取新闻数据并格式化为 Markdown 段落。"""
    parts = []

    # 市场行情
    quotations = mcp_context.get("market_quotations", "")
    if quotations and not quotations.startswith("获取失败") and not quotations.startswith("调用失败"):
        parts.append(f"### 市场行情\n{quotations[:1500]}")

    # MCP 热点新闻（带关键词搜索的）
    news_map = mcp_context.get("news", {})
    if isinstance(news_map, dict) and news_map:
        hot_news = []
        fund_news = []
        for key, val in news_map.items():
            if isinstance(val, str) and not val.startswith("获取失败"):
                if key in ("market_hot", "market_trend"):
                    hot_news.append(val[:1000])
                else:
                    fund_news.append(val[:800])
        if hot_news:
            parts.append("### 近期市场热点\n" + "\n---\n".join(hot_news))
        if fund_news:
            parts.append("### 持仓相关新闻\n" + "\n---\n".join(fund_news))

    # akshare 实时新闻
    akshare = mcp_context.get("akshare_news", [])
    if isinstance(akshare, list) and akshare:
        news_lines = []
        for item in akshare:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            time_val = item.get("time", "")
            source = item.get("source", "")
            if title:
                news_lines.append(f"- **{title}**  {snippet[:120]}")
        if news_lines:
            parts.append("### 实时财经新闻\n" + "\n".join(news_lines[:8]))

    # hot_topics（兜底）
    hot_topics = mcp_context.get("hot_topics", "")
    if hot_topics and not hot_topics.startswith("获取失败") and not hot_topics.startswith("调用失败"):
        if not news_map:  # 只有 news 搜索失败时才展示 hot_topics
            parts.append(f"### 市场热点\n{hot_topics[:1500]}")

    if not parts:
        return "## 新闻热点\n暂无最新新闻数据。"

    return "## 新闻热点\n" + "\n\n".join(parts)


def _add_akshare_news(mcp_context: dict, max_results: int = 8):
    """用 akshare 补充实时财经新闻。"""
    try:
        import akshare as ak
        news_items = []
        # 东方财富 A 股新闻
        try:
            df = ak.stock_news_em(symbol="A股")
            if df is not None and len(df) > 0:
                for _, row in df.head(max_results).iterrows():
                    news_items.append({
                        "title": str(row.get("新闻标题", "")),
                        "snippet": str(row.get("新闻内容", ""))[:200],
                        "time": str(row.get("发布时间", "")),
                        "source": "东方财富",
                    })
        except Exception:
            pass
        # 央视新闻补充
        if len(news_items) < 3:
            try:
                from datetime import datetime
                df2 = ak.news_cctv(date=datetime.now().strftime("%Y%m%d"))
                if df2 is not None and len(df2) > 0:
                    for _, row in df2.head(max_results - len(news_items)).iterrows():
                        title = str(row.get("title", ""))
                        if any(kw in title for kw in ["股", "基金", "央行", "利率", "经济", "金融", "市场", "投资", "GDP", "通胀", "行情"]):
                            news_items.append({
                                "title": title,
                                "snippet": str(row.get("content", ""))[:200],
                                "time": str(row.get("date", "")),
                                "source": "央视新闻",
                            })
            except Exception:
                pass
        if news_items:
            mcp_context["akshare_news"] = news_items
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"akshare 新闻获取异常: {e}")


def _get_mcp_context(holdings: list[dict]) -> dict:
    """获取 MCP 相关数据，包含实时市场新闻和热点搜索。"""
    mcp_context = {}
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        fund_codes = [h["fund_code"] for h in holdings if h.get("fund_code") and h["fund_code"].strip()]

        if not fund_codes:
            return {}

        holding_map = {h["fund_code"]: h.get("current_value", 0) or 0 for h in holdings if h.get("fund_code")}

        # 资产大类穿透
        try:
            raw = mcp.call_tool_text("GetFundAssetClassAnalysis", {
                "holdingList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
            })
            mcp_context["asset_class_analysis"] = raw
        except Exception as e:
            mcp_context["asset_class_analysis"] = f"调用失败: {e}"

        # 基金相关性
        try:
            raw = mcp.call_tool_text("GetFundsCorrelation", {
                "fundList": [{"fundCode": c} for c in fund_codes]
            })
            mcp_context["correlation"] = raw
        except Exception as e:
            mcp_context["correlation"] = f"调用失败: {e}"

        # 组合诊断
        try:
            raw = mcp.call_tool_text("DiagnoseFundPortfolio", {
                "fundList": [{"fundCode": c, "amount": int(holding_map.get(c, 0))} for c in fund_codes]
            })
            mcp_context["portfolio_diagnosis"] = raw
        except Exception as e:
            mcp_context["portfolio_diagnosis"] = f"调用失败: {e}"

        # 市场行情
        try:
            mcp_context["market_quotations"] = mcp.get_latest_quotations()
        except Exception as e:
            mcp_context["market_quotations"] = f"获取失败: {e}"

        # ── 实时热点新闻 — 精简版，只查市场热点 + 前3持仓 ──
        try:
            news_map = {}

            # 市场整体热点（2个关键词）
            for kw, label in [("A股 热门 板块 基金", "market_hot"), ("市场热点 行情", "market_trend")]:
                try:
                    news_map[label] = mcp.search_news(kw, 5)
                except Exception:
                    pass

            # 前3大持仓相关新闻
            top3 = sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True)[:3]
            for h in top3:
                name = h.get("fund_name", "") or ""
                kw = name.replace("ETF", "").replace("联接", "").replace("基金", "").replace("LOF", "").strip()[:10]
                if len(kw) >= 2:
                    try:
                        news_map[f"fund_{h.get('fund_code','')}"] = mcp.search_news(kw, 3)
                    except Exception:
                        pass

            if news_map:
                mcp_context["news"] = news_map

            # 热点话题
            try:
                mcp_context["hot_topics"] = mcp.call_tool_text("SearchHotTopic", {"keyword": "市场热点 热门基金"})
            except Exception:
                try:
                    mcp_context["hot_topics"] = mcp.call_tool_text("SearchHotTopic", {})
                except Exception as e:
                    mcp_context["hot_topics"] = f"获取失败: {e}"

        except Exception as e:
            mcp_context["news"] = f"获取失败: {e}"

        # 用 akshare 补充实时新闻
        _add_akshare_news(mcp_context)

        # 各基金诊断（最多3只）
        for code in fund_codes[:3]:
            try:
                mcp_context[f"fund_diagnosis_{code}"] = mcp.call_tool_text("GetFundDiagnosis", {"fundCode": code})
            except Exception:
                pass

    except ImportError:
        mcp_context["error"] = "MCP 客户端未配置"
    except Exception as e:
        mcp_context["error"] = f"MCP 调用异常: {e}"
    return mcp_context


@router.post("/api/portfolio/analysis/panorama")
async def panorama_analysis_api(req: PanoramaAnalysisRequest):
    """模式 1：全景诊断 — 从全局视角诊断投资组合健康状况。"""
    holdings = list_holdings()
    if not holdings:
        raise HTTPException(400, "暂无持仓数据")

    agent = get_analysis_agent(3)
    if not agent:
        raise HTTPException(404, "全景诊断分析师未配置")
    system_prompt = agent["system_prompt"]

    # 收集数据
    diversification = get_portfolio_diversification()
    total_value = diversification.get('total_value', 1) or 1

    # 持仓明细
    holdings_lines = []
    for h in sorted(holdings, key=lambda x: (x.get('current_value', 0) or 0), reverse=True):
        pct = (h.get('current_value', 0) or 0) / total_value * 100
        holdings_lines.append(
            f"- {h.get('fund_name','')}({h.get('fund_code','')}): "
            f"账户 {h.get('account') or '花无缺'}, "
            f"市值 {(h.get('current_value') or 0):.2f}, "
            f"盈亏 {(h.get('profit_loss') or 0):.2f} ({(h.get('profit_rate') or 0)*100:.1f}%), "
            f"占比 {pct:.1f}%"
        )

    # 类型分布
    type_dist = diversification.get('type_distribution', {})
    type_lines = [f"  - {k}: {v:.1f}%" for k, v in type_dist.items()]

    # MCP 数据
    mcp_context = _get_mcp_context(holdings)

    # 估值数据（按持仓匹配，而非通用列表）
    valuation_context = _get_holdings_valuation_context(holdings)

    # 从 mcp_context 中提取新闻数据，单独格式化
    news_section = _format_news_section(mcp_context)

    # RAG 知识库检索
    rag_context = ""
    try:
        fund_names = " ".join([h.get("fund_name", "") for h in holdings[:5]])
        rag_query = f"投资组合 资产配置 风险分析 {fund_names}"
        rag_result = build_rag_context_with_details(query=rag_query, limit=5)
        rag_context = rag_result.get("context", "")
        log_rag_search(
            conversation_id=0, message_id=0, query=rag_query,
            keywords=rag_result.get("keywords", []),
            results=rag_result.get("results", []),
            fts_count=rag_result.get("fts_count", 0),
            chroma_count=rag_result.get("chroma_count", 0),
            freshness_filtered=rag_result.get("freshness_filtered", 0),
        )
    except Exception as e:
        logger.warning(f"RAG 检索失败: {e}")

    user_content = (
        f"## 持仓明细\n" + "\n".join(holdings_lines) +
        f"\n\n## 类型分布\n" + "\n".join(type_lines) +
        f"\n\n## 集中度\n- 前3大持仓占比: {diversification.get('top3_concentration', 0):.1f}%"
        f"\n- 前5大持仓占比: {diversification.get('top5_concentration', 0):.1f}%\n"
        f"\n## MCP 专业数据\n{json.dumps(mcp_context, ensure_ascii=False, indent=2)}\n"
        f"\n{valuation_context}"
        f"\n\n{news_section}"
        f"\n\n## 知识库参考\n{rag_context[:1500] if rag_context else '暂无相关知识库内容'}"
    )

    # 调用 LLM（带 10 分钟超时）
    uid = f"panorama_{int(time.time())}"
    _track_agent(uid, "全景诊断分析师", "持仓诊断")
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.wait_for(asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_panorama",
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        )), timeout=600)
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except asyncio.TimeoutError:
        _untrack_agent(uid)
        logger.error(f"全景诊断 LLM 调用超时")
        raise HTTPException(504, "AI 分析超时，请重试")
    except Exception as e:
        _untrack_agent(uid)
        logger.error(f"全景诊断失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")
    finally:
        _untrack_agent(uid)

    # 保存记录
    record_id = create_portfolio_analysis_record(
        analysis_type="panorama",
        summary=f"全景诊断 · {len(holdings)}只基金",
        input_data=json.dumps({"holdings_count": len(holdings), "total_value": diversification.get('total_value')}, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=3,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


def _get_fund_mcp_diagnosis(fund_code: str) -> str:
    """获取单只基金的 MCP 诊断数据。"""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        # 基金诊断
        diagnosis = mcp.call_tool_text("GetFundDiagnosis", {"fundCode": fund_code})
        # 相关性（如果 MCP 支持单只查询）
        return diagnosis or "MCP 诊断不可用"
    except ImportError:
        return "MCP 客户端未配置"
    except Exception as e:
        return f"MCP 诊断获取失败: {e}"


@router.post("/api/portfolio/analysis/deep-dive/{holding_id}")
async def fund_deep_dive_api(holding_id: int, req: DeepDiveRequest):
    """模式 2：单基金深度分析 — 分析买入质量、持有收益、操作记录。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")

    agent = get_analysis_agent(4)
    if not agent:
        raise HTTPException(404, "基金深度分析师未配置")

    fund_code = holding["fund_code"]
    fund_name = holding.get("fund_name", "")

    # 1) 交易记录
    txs = list_transactions(fund_code=fund_code, limit=100)
    tx_lines = []
    for t in sorted(txs, key=lambda x: x.get("transaction_date", "")):
        tx_lines.append(
            f"- {t.get('transaction_date','')} {'买入' if t.get('transaction_type')=='buy' else '卖出'}: "
            f"金额 {(t.get('amount') or 0):.2f}, 份额 {(t.get('shares') or 0):.4f}, "
            f"价格 {(t.get('price') or 0):.4f}, 状态 {t.get('status') or 'confirmed'}"
        )

    # 2) 估值历史 — 带趋势
    valuation_section = ""
    try:
        index_code = holding.get("index_code") or ""
        if index_code:
            hist = get_valuation_history(index_code, days=365)
            if hist and len(hist) > 0:
                latest_day = hist[-1]
                # 当前值
                parts = []
                for mt in ["pe", "pb"]:
                    lv = get_latest_valuation(index_code, mt)
                    if lv:
                        pct = lv.get("percentile", "N/A")
                        val = lv.get("current_value", "N/A")
                        parts.append(f"{mt.upper()}: {val} (分位 {pct}%)")
                if parts:
                    valuation_section += "当前估值: " + ", ".join(parts) + "\n"
                # 趋势（近30天分位变化）
                recent = hist[-30:]
                if len(recent) >= 5:
                    pe_pcts = [get_latest_valuation(index_code, "pe").get("percentile") for _ in recent[:5]]
                    # 简化：月初 vs 月末
                    first = recent[0]
                    last = recent[-1]
                    valuation_section += (
                        f"估值趋势: {first.get('date','?')}→{last.get('date','?')}, "
                        f"PE {first.get('current_value','?')}→{last.get('current_value','?')}\n"
                    )
                # 买入时估值
                if txs:
                    first_buy = next((t for t in txs if t["transaction_type"] == "buy"), None)
                    if first_buy:
                        buy_date = first_buy.get("transaction_date", "")
                        if buy_date:
                            buy_val = get_latest_valuation(index_code, "pe")
                            if buy_val:
                                valuation_section += f"首次买入时估值: PE {buy_val.get('current_value','?')} (分位 {buy_val.get('percentile','?')}%)\n"
            else:
                valuation_section = "暂无估值历史数据\n"
        else:
            val = get_latest_valuation(fund_code)
            if val:
                valuation_section = f"当前PE分位: {val.get('percentile','N/A')}%\n"
            else:
                valuation_section = "该基金无跟踪指数数据\n"
    except Exception as e:
        valuation_section = f"估值获取失败: {e}\n"

    # 3) 基金基本面（重仓股、行业配置、资产配置）
    fundamentals_section = ""
    try:
        info = lookup_fund_info(fund_code)
        if info:
            cnt_lines = []
            for k in ("fund_name", "fund_type", "index_name", "management_rate", "custody_rate", "fund_scale", "establish_date"):
                v = info.get(k)
                if v is not None:
                    cnt_lines.append(f"- {k}: {v}")
            if cnt_lines:
                fundamentals_section += "### 基金基本信息\n" + "\n".join(cnt_lines) + "\n"

        holdings_data = get_fund_holdings(fund_code)
        if holdings_data:
            # 重仓股
            stocks = holdings_data.get("top_stocks", [])
            if stocks:
                stock_lines = [f"  - {s['stock_name']}({s.get('stock_code','')}): {s.get('pct_nav','?')}%" for s in stocks[:5]]
                fundamentals_section += "### 重仓股\n" + "\n".join(stock_lines) + "\n"
            # 资产配置
            alloc = holdings_data.get("asset_allocation", [])
            if alloc:
                alloc_lines = [f"  - {a['type']}: {a['pct']}%" for a in alloc]
                fundamentals_section += "### 资产配置\n" + "\n".join(alloc_lines) + "\n"
            # 债券类型
            bt = holdings_data.get("bond_type_summary", {})
            if bt:
                bt_lines = [f"  - {k}: {v}%" for k, v in bt.items() if v]
                if bt_lines:
                    fundamentals_section += "### 债券类型\n" + "\n".join(bt_lines) + "\n"
    except Exception as e:
        fundamentals_section = f"基本面获取失败: {e}\n"

    # 4) 组合上下文 — 该基金在整体组合中的位置
    portfolio_context = ""
    try:
        all_holdings = list_holdings()
        total_value = sum((h.get("current_value", 0) or 0) for h in all_holdings)
        this_value = holding.get("current_value", 0) or 0
        this_pct = this_value / total_value * 100 if total_value > 0 else 0
        # 相关性数据（从 MCP 获取）
        portfolio_context += f"该基金在组合中占比: {this_pct:.1f}%\n"
        # 与其他基金的对比
        if len(all_holdings) > 1:
            others = [(h.get("fund_name",""), h.get("current_value",0) or 0) for h in all_holdings if h["id"] != holding_id]
            others.sort(key=lambda x: x[1], reverse=True)
            portfolio_context += "组合中其他主要持仓: " + ", ".join(f"{n}(市值{v:.0f})" for n, v in others[:3]) + "\n"
    except Exception as e:
        portfolio_context = f"组合上下文获取失败: {e}\n"

    # 5) 新闻/市场上下文（简版，基于指数名或基金名搜索）
    news_context = ""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        # 用基金名或指数名搜相关新闻
        kw = (fund_name or index_code or "")[:10]
        if kw and len(kw) >= 2:
            news_text = mcp.search_news(kw, 3)
            if news_text and not news_text.startswith("获取失败"):
                news_context = f"### 相关新闻\n{news_text[:1500]}"
        # 补充大盘新闻
        try:
            import akshare as ak
            df = ak.stock_news_em(symbol="A股")
            if df is not None and len(df) > 0:
                ak_lines = []
                for _, row in df.head(3).iterrows():
                    title = str(row.get("新闻标题", ""))
                    if title:
                        ak_lines.append(f"- {title}")
                if ak_lines:
                    news_context += "\n### 实时财经新闻\n" + "\n".join(ak_lines)
        except ImportError:
            pass
    except Exception:
        pass

    # RAG 知识库检索
    rag_context = ""
    try:
        rag_query = f"{fund_name} 基金分析 投资策略"
        rag_result = build_rag_context_with_details(query=rag_query, limit=5)
        rag_context = rag_result.get("context", "")
        log_rag_search(
            conversation_id=0, message_id=0, query=rag_query,
            keywords=rag_result.get("keywords", []),
            results=rag_result.get("results", []),
            fts_count=rag_result.get("fts_count", 0),
            chroma_count=rag_result.get("chroma_count", 0),
            freshness_filtered=rag_result.get("freshness_filtered", 0),
        )
    except Exception as e:
        logger.warning(f"RAG 检索失败: {e}")

    user_content = (
        f"## 基金持仓信息\n"
        f"- 基金: {fund_name}({fund_code})\n"
        f"- 账户: {holding.get('account','花无缺')}\n"
        f"- 持有份额: {holding.get('shares',0):.4f}\n"
        f"- 成本净值: {holding.get('cost_price',0):.4f}\n"
        f"- 当前净值: {holding.get('current_price',0):.4f}\n"
        f"- 市值: {holding.get('current_value',0):.2f}\n"
        f"- 盈亏: {holding.get('profit_loss',0):.2f} ({holding.get('profit_rate',0)*100:.1f}%)\n"
        f"- 持有时间: 自首次交易起\n"
        f"\n## 交易记录\n" + ("\n".join(tx_lines) if tx_lines else "无交易记录") +
        f"\n\n## 估值数据\n{valuation_section}"
        f"\n\n{fundamentals_section}"
        f"\n\n## 组合角色上下文\n{portfolio_context}"
        f"\n\n## MCP 诊断\n{_get_fund_mcp_diagnosis(fund_code)}"
        f"\n\n{news_context}"
        f"\n\n## 知识库参考\n{rag_context[:1500] if rag_context else '暂无相关知识库内容'}"
    )

    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_deep_dive",
            model=MODEL,
            messages=[
                {"role": "system", "content": agent["system_prompt"]},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"深度分析失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    record_id = create_portfolio_analysis_record(
        analysis_type="deep_dive",
        summary=f"深度分析 · {fund_name}",
        input_data=json.dumps({"holding_id": holding_id, "fund_code": fund_code}, ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=4,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


@router.post("/api/portfolio/analysis/trade-review")
async def trade_review_api(req: TradeReviewRequest):
    """模式 3：交易复盘 — 分析交易行为模式和操作质量。"""
    txs = list_transactions(limit=500)
    if not txs:
        raise HTTPException(400, "暂无交易记录")

    agent = get_analysis_agent(5)
    if not agent:
        raise HTTPException(404, "交易复盘分析师未配置")

    # 过滤日期范围
    if req.start_date:
        txs = [t for t in txs if t.get("transaction_date", "") >= req.start_date]
    if req.end_date:
        txs = [t for t in txs if t.get("transaction_date", "") <= req.end_date]
    if not txs:
        raise HTTPException(400, "所选日期范围内无交易记录")

    # 交易记录 + 标签 + 估值快照
    tx_lines = []
    total_fee = 0
    for t in sorted(txs, key=lambda x: x.get("transaction_date", "")):
        tags = get_transaction_tags(t["id"])
        tag_str = f" [{','.join(tags)}]" if tags else ""

        # 解析估值快照
        snapshot_str = t.get("valuation_snapshot")
        valuation_str = ""
        if snapshot_str:
            try:
                snap = json.loads(snapshot_str)
                pe_pct = snap.get("pe_percentile")
                pb_pct = snap.get("pb_percentile")
                if pe_pct is not None:
                    valuation_str = f" [PE分位:{pe_pct:.1f}%"
                    if pb_pct is not None:
                        valuation_str += f", PB分位:{pb_pct:.1f}%"
                    valuation_str += "]"
            except:
                pass

        # 手续费
        fee = t.get("fee") or 0
        total_fee += fee
        fee_str = f", 手续费 {fee:.2f}" if fee > 0 else ""

        tx_lines.append(
            f"- {t['transaction_date']} {t.get('transaction_time','')} "
            f"{'买入' if t['transaction_type']=='buy' else '卖出'}"
            f"{tag_str}{valuation_str}: "
            f"{t.get('fund_name','')}({t.get('fund_code','')}), "
            f"金额 {(t.get('amount') or 0):.2f}, 价格 {(t.get('price') or 0):.4f}{fee_str}"
        )

    # 汇总统计
    buy_count = len([t for t in txs if t["transaction_type"] == "buy"])
    sell_count = len([t for t in txs if t["transaction_type"] == "sell"])
    buy_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "buy")
    sell_total = sum(t.get("amount", 0) or 0 for t in txs if t["transaction_type"] == "sell")

    # 有估值快照的交易统计
    txs_with_valuation = [t for t in txs if t.get("valuation_snapshot")]
    valuation_summary = ""
    if txs_with_valuation:
        buy_with_val = [t for t in txs_with_valuation if t["transaction_type"] == "buy"]
        sell_with_val = [t for t in txs_with_valuation if t["transaction_type"] == "sell"]
        if buy_with_val:
            avg_buy_pe = sum(json.loads(t["valuation_snapshot"]).get("pe_percentile", 50)
                           for t in buy_with_val) / len(buy_with_val)
            low_buy = len([t for t in buy_with_val
                          if json.loads(t["valuation_snapshot"]).get("pe_percentile", 50) < 30])
            valuation_summary += f"\n买入时估值分析: 平均PE分位 {avg_buy_pe:.1f}%, 低估买入(PE<30%) {low_buy}/{len(buy_with_val)} 笔"
        if sell_with_val:
            avg_sell_pe = sum(json.loads(t["valuation_snapshot"]).get("pe_percentile", 50)
                            for t in sell_with_val) / len(sell_with_val)
            high_sell = len([t for t in sell_with_val
                           if json.loads(t["valuation_snapshot"]).get("pe_percentile", 50) > 70])
            valuation_summary += f"\n卖出时估值分析: 平均PE分位 {avg_sell_pe:.1f}%, 高估卖出(PE>70%) {high_sell}/{len(sell_with_val)} 笔"

    user_content = (
        f"## 操作总览\n"
        f"- 买入 {buy_count} 笔, 共 {buy_total:.2f} 元\n"
        f"- 卖出 {sell_count} 笔, 共 {sell_total:.2f} 元\n"
        f"- 净投入: {buy_total - sell_total:.2f} 元\n"
        f"- 手续费总计: {total_fee:.2f} 元\n"
        f"{valuation_summary}\n"
        f"\n## 交易明细（含交易时点估值）\n" + "\n".join(tx_lines)
    )

    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_trade_review",
            model=MODEL,
            messages=[
                {"role": "system", "content": agent["system_prompt"]},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"交易复盘失败: {e}")
        raise HTTPException(500, f"AI 分析失败: {str(e)}")

    record_id = create_portfolio_analysis_record(
        analysis_type="trade_review",
        summary=f"交易复盘 · {buy_count}买{sell_count}卖",
        input_data=json.dumps({"start_date": req.start_date, "end_date": req.end_date, "tx_count": len(txs)},
                              ensure_ascii=False),
        result_data=result_text,
        token_usage=tokens,
        agent_id=5,
    )

    return {"id": record_id, "result": result_text, "token_usage": tokens}


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
            temperature=0.3,
            max_tokens=8192,
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


@router.get("/api/portfolio/analysis/panorama/records")
async def list_panorama_records_api(limit: int = 10):
    """列出全景诊断历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="panorama", limit=limit)
    return {"records": records}


@router.get("/api/portfolio/analysis/deep-dive/records")
async def list_deep_dive_records_api(limit: int = 10):
    """列出深度分析历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="deep_dive", limit=limit)
    return {"records": records}


@router.get("/api/portfolio/analysis/trade-review/records")
async def list_trade_review_records_api(limit: int = 10):
    """列出交易复盘历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="trade_review", limit=limit)
    return {"records": records}


@router.post("/api/portfolio/backfill-snapshots")
async def backfill_snapshots_api():
    """回填历史交易的估值快照。"""
    from db import backfill_valuation_snapshots
    updated = backfill_valuation_snapshots()
    return {"ok": True, "updated": updated}


@router.get("/api/portfolio/analysis/what-if/records")
async def list_whatif_records_api(limit: int = 10):
    """列出情景推演历史记录。"""
    records = list_portfolio_analysis_records(analysis_type="what_if", limit=limit)
    return {"records": records}


# ── 风险预警 API ──────────────────────────────────────────


@router.get("/api/portfolio/alerts")
async def list_alerts_api(unread_only: bool = False, limit: int = 50):
    """获取预警列表。"""
    return {"alerts": list_alerts(limit=limit, unread_only=unread_only)}


@router.get("/api/portfolio/alerts/unread-count")
async def unread_alert_count_api():
    """获取未读预警数量。"""
    return {"count": get_unread_alert_count()}


@router.put("/api/portfolio/alerts/{alert_id}/read")
async def mark_alert_read_api(alert_id: int):
    """标记预警为已读。"""
    if not mark_alert_read(alert_id):
        raise HTTPException(404, "预警不存在")
    return {"ok": True}


@router.delete("/api/portfolio/alerts/{alert_id}")
async def delete_alert_api(alert_id: int):
    """删除预警。"""
    if not delete_alert(alert_id):
        raise HTTPException(404, "预警不存在")
    return {"ok": True}


@router.post("/api/portfolio/alerts/generate")
async def generate_alert_api(req: CreateAlertRequest):
    """AI 主动生成预警。"""
    alert_id = create_alert(
        alert_type=req.alert_type,
        title=req.title,
        content=req.content,
        severity=req.severity,
        related_fund_code=req.related_fund_code,
        related_fund_name=req.related_fund_name,
        source=req.source or "system",
    )
    return {"ok": True, "alert_id": alert_id}


@router.post("/api/portfolio/alerts/scan")
async def scan_portfolio_alerts():
    """持仓风险巡检 — 主动扫描持仓数据生成预警。"""
    from datetime import datetime, timedelta
    from db import get_config_int

    holdings = list_holdings()
    if not holdings:
        return {"ok": True, "generated": 0, "message": "暂无持仓"}

    # 读取可配置阈值
    val_high = get_config_int('alert.valuation_high', 80)       # 高估百分位
    val_low = get_config_int('alert.valuation_low', 20)          # 低估百分位
    drawdown_threshold = get_config_int('alert.drawdown_pct', 10)  # 回撤预警(%)
    concentration_threshold = get_config_int('alert.concentration_pct', 30)  # 集中度(%)
    cash_high_pct = get_config_int('alert.cash_high_pct', 15)    # 现金闲置(%)
    stale_days = get_config_int('alert.stale_days', 5)           # 数据过期(天)

    generated = 0
    today = datetime.now().strftime("%Y-%m-%d")
    today_prefix = datetime.now().strftime("%Y-%m-%d")

    # ── 去重：同一天同一类型+同一基金不重复生成 ──
    existing = list_alerts(limit=200)
    existing_keys = set()
    for a in existing:
        if a.get("created_at", "").startswith(today_prefix):
            existing_keys.add(f"{a.get('alert_type')}:{a.get('related_fund_code', '')}")

    def should_create(alert_type, fund_code=""):
        key = f"{alert_type}:{fund_code}"
        if key in existing_keys:
            return False
        existing_keys.add(key)
        return True

    # ── 1. 估值预警 ──
    try:
        for h in holdings:
            code = h.get("fund_code", "")
            name = h.get("fund_name", code)
            if not code:
                continue
            val = get_latest_valuation(code)
            if not val:
                continue
            pct = val.get("percentile")
            metric = val.get("metric_type", "PE")
            if pct is None:
                continue
            if pct >= val_high and should_create("valuation_alert", code):
                create_alert(
                    alert_type="valuation_alert",
                    title=f"{name} 估值偏高（{metric}百分位 {pct}%）",
                    content=f"{name}（{code}）当前{metric}百分位为 {pct}%，已进入高估区间（>{val_high}%）。建议关注是否需要减仓或止盈。",
                    severity="warning",
                    related_fund_code=code,
                    related_fund_name=name,
                    source="system_scan",
                )
                generated += 1
            elif pct <= val_low and should_create("valuation_opportunity", code):
                create_alert(
                    alert_type="valuation_opportunity",
                    title=f"{name} 估值偏低（{metric}百分位 {pct}%）",
                    content=f"{name}（{code}）当前{metric}百分位为 {pct}%，处于低估区间（<{val_low}%）。可考虑逢低加仓。",
                    severity="info",
                    related_fund_code=code,
                    related_fund_name=name,
                    source="system_scan",
                )
                generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 估值预警异常: {e}")

    # ── 2. 回撤预警 ──
    try:
        for h in holdings:
            code = h.get("fund_code", "")
            name = h.get("fund_name", code)
            if not code:
                continue
            nav_data = get_fund_nav_history(code, days=60)
            if not nav_data or len(nav_data) < 10:
                continue
            navs = [d.get("nav", 0) for d in nav_data if d.get("nav")]
            if len(navs) < 10:
                continue
            peak = max(navs[-30:]) if len(navs) >= 30 else max(navs)
            current = navs[-1]
            if peak <= 0:
                continue
            drawdown_pct = (peak - current) / peak * 100
            if drawdown_pct >= drawdown_threshold and should_create("drawdown_alert", code):
                create_alert(
                    alert_type="drawdown_alert",
                    title=f"{name} 近期回撤 {drawdown_pct:.1f}%",
                    content=f"{name}（{code}）从近期高点 {peak:.4f} 回撤至 {current:.4f}，跌幅 {drawdown_pct:.1f}%。请评估是否需要止损或加仓。",
                    severity="danger" if drawdown_pct >= drawdown_threshold * 1.5 else "warning",
                    related_fund_code=code,
                    related_fund_name=name,
                    source="system_scan",
                )
                generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 回撤预警异常: {e}")

    # ── 3. 集中度预警 ──
    try:
        total_value = sum(h.get("current_value", 0) or 0 for h in holdings)
        if total_value > 0:
            for h in holdings:
                code = h.get("fund_code", "")
                name = h.get("fund_name", code)
                value = h.get("current_value", 0) or 0
                pct = value / total_value * 100
                if pct >= concentration_threshold and should_create("concentration_alert", code):
                    create_alert(
                        alert_type="concentration_alert",
                        title=f"{name} 占比过高（{pct:.1f}%）",
                        content=f"{name}（{code}）占组合总市值 {pct:.1f}%，超过集中度阈值 {concentration_threshold}%。建议适当分散配置。",
                        severity="warning",
                        related_fund_code=code,
                        related_fund_name=name,
                        source="system_scan",
                    )
                    generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 集中度预警异常: {e}")

    # ── 4. 现金闲置预警 ──
    try:
        cash_balance = get_cash_balance() or 0
        total_assets = total_value + cash_balance
        if total_assets > 0:
            cash_pct = cash_balance / total_assets * 100
            if cash_pct >= cash_high_pct and should_create("cash_idle"):
                create_alert(
                    alert_type="cash_idle",
                    title=f"现金占比偏高（{cash_pct:.1f}%）",
                    content=f"当前现金余额 ¥{cash_balance:,.0f}，占总资产 {cash_pct:.1f}%，超过 {cash_high_pct}% 阈值。资金闲置会拖低整体收益，建议逐步配置。",
                    severity="info",
                    source="system_scan",
                )
                generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 现金预警异常: {e}")

    # ── 5. 数据过期预警 ──
    try:
        stale_funds = []
        cutoff = (datetime.now() - timedelta(days=stale_days)).strftime("%Y-%m-%d")
        for h in holdings:
            updated = h.get("price_updated_at", "") or ""
            if updated < cutoff and h.get("shares", 0) > 0:
                stale_funds.append(h)
        if stale_funds and should_create("stale_data"):
            names = "、".join(h.get("fund_name", h.get("fund_code", "")) for h in stale_funds[:5])
            create_alert(
                alert_type="stale_data",
                title=f"{len(stale_funds)} 只基金数据超过 {stale_days} 天未更新",
                content=f"以下基金净值数据过期：{names}。建议刷新行情数据。",
                severity="info",
                source="system_scan",
            )
            generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 数据过期预警异常: {e}")

    return {"ok": True, "generated": generated}


# ── 交易标签 API ──────────────────────────────────────────


@router.post("/api/portfolio/transactions/{tx_id}/tags")
async def add_transaction_tag_api(tx_id: int, req: TagRequest):
    """给交易记录添加标签。"""
    tag_id = add_transaction_tag(tx_id, req.tag)
    return {"ok": True, "tag_id": tag_id}


@router.delete("/api/portfolio/transactions/{tx_id}/tags/{tag}")
async def remove_transaction_tag_api(tx_id: int, tag: str):
    """移除交易记录的标签。"""
    if not remove_transaction_tag(tx_id, tag):
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


@router.get("/api/portfolio/transactions/{tx_id}/tags")
async def get_transaction_tags_api(tx_id: int):
    """获取交易记录的所有标签。"""
    return {"tags": get_transaction_tags(tx_id)}


@router.get("/api/portfolio/pending-transactions")
async def list_pending_transactions_api():
    """获取所有待确认交易（包括没有 holding_id 的新建买入）。"""
    txs = list_transactions(status="pending", limit=200, include_system=False)
    # 为没有 holding_id 的交易补充基金名称
    for tx in txs:
        if not tx.get("holding_id") and not tx.get("_fund_name"):
            tx["_fund_name"] = tx.get("fund_name") or tx.get("fund_code", "未知基金")
            tx["_fund_code"] = tx.get("fund_code", "")
    return {"transactions": txs}


@router.get("/api/portfolio/{holding_id}")
async def get_holding_api(holding_id: int):
    """获取单个持仓详情。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    return holding


@router.put("/api/portfolio/{holding_id}")
async def update_holding_api(holding_id: int, req: UpdateHoldingRequest):
    """更新持仓。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if fields:
        update_holding(holding_id, **fields)
    return {"ok": True}


@router.delete("/api/portfolio/{holding_id}")
async def delete_holding_api(holding_id: int):
    """删除持仓。"""
    if not delete_holding(holding_id):
        raise HTTPException(404, "持仓不存在")
    return {"ok": True}


@router.get("/api/portfolio/{holding_id}/transactions")
async def list_transactions_api(holding_id: int, limit: int = 100):
    """获取持仓的交易记录。"""
    return {"transactions": list_transactions(holding_id=holding_id, limit=limit)}


@router.post("/api/portfolio/transactions")
async def create_transaction_api(req: CreateTransactionRequest):
    """新增交易记录。"""
    # 自动计算 T+1 确认日
    expected_confirm = None
    if req.status == "pending" and req.transaction_date:
        try:
            from datetime import datetime as dt
            d = dt.strptime(req.transaction_date, "%Y-%m-%d").date()
            expected_confirm = str(expected_confirm_date(d, req.transaction_time))
        except (ValueError, TypeError):
            pass

    tx_id = create_transaction(
        fund_code=req.fund_code, transaction_type=req.transaction_type,
        amount=req.amount, transaction_date=req.transaction_date,
        shares=req.shares, price=req.price,
        holding_id=req.holding_id, notes=req.notes,
        status=req.status, submitted_shares=req.submitted_shares,
        submitted_amount=req.submitted_amount,
        transaction_time=req.transaction_time,
        expected_confirm_date=expected_confirm,
    )
    return {"ok": True, "transaction_id": tx_id, "expected_confirm_date": expected_confirm}


@router.post("/api/portfolio/transactions/{tx_id}/confirm")
async def confirm_transaction_api(tx_id: int, req: ConfirmTransactionRequest):
    """确认交易：填入 T+1 实际净值，计算实际份额/金额。"""
    ok = confirm_transaction(tx_id, req.confirmed_price,
                             confirmed_shares=req.confirmed_shares,
                             confirmed_amount=req.confirmed_amount,
                             target_fund_code=req.target_fund_code,
                             target_fund_name=req.target_fund_name,
                             fee=req.fee)
    if not ok:
        raise HTTPException(404, "交易记录不存在")
    return {"ok": True}


@router.post("/api/portfolio/transactions/{tx_id}/settle")
async def settle_transaction_api(tx_id: int):
    """标记卖出交易已到账。"""
    ok = settle_transaction(tx_id)
    if not ok:
        raise HTTPException(400, "只能标记已确认的卖出交易为已到账")
    return {"ok": True}


@router.delete("/api/portfolio/transactions/{tx_id}")
async def delete_transaction_api(tx_id: int):
    """撤销 pending 状态的交易记录。"""
    ok = delete_transaction(tx_id)
    if not ok:
        raise HTTPException(400, "只能撤销待确认（pending）状态的交易")
    return {"ok": True}


@router.post("/api/portfolio/refresh")
async def refresh_all_prices_api():
    """批量刷新所有持仓的最新净值。"""
    results = refresh_all_fund_prices()
    return {"ok": True, "results": results, "total": len(results)}


@router.post("/api/portfolio/{holding_id}/refresh")
async def refresh_single_price_api(holding_id: int):
    """刷新单个持仓的最新净值。"""
    holding = get_holding(holding_id)
    if not holding:
        raise HTTPException(404, "持仓不存在")
    nav_data = refresh_holding_price(holding_id)
    if not nav_data:
        raise HTTPException(502, "净值获取失败，请稍后重试")
    return {"ok": True, "fund_code": holding["fund_code"], "nav": nav_data}
