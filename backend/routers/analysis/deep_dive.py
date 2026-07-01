"""深度分析 — POST /api/portfolio/analysis/deep-dive/{holding_id}"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from state import track_agent as _track_agent, untrack_agent as _untrack_agent
from db import (
    list_holdings, get_holding, list_transactions, get_analysis_agent,
    lookup_fund_info, get_fund_holdings, fetch_fund_nav,
    get_valuation_history, get_latest_valuation,
    create_portfolio_analysis_record,
    save_analysis_conclusion,
    get_related_orchestrator_decisions,
)
from db.portfolio import update_analysis_record
from db.config import get_config as _get_config, get_config_int, get_config_float
from rag import build_rag_context_with_details, log_rag_search
from models.portfolio import DeepDiveRequest
from ._shared import (
    _get_fund_mcp_diagnosis, _fetch_valuation_fallback,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-deep-dive"])

_background_tasks: set = set()


def _extract_candidates_safely(record_id: int, analysis_type: str, result_text: str):
    try:
        from db.decisions import extract_recommendation_candidates_from_analysis
        extract_recommendation_candidates_from_analysis(record_id, analysis_type, result_text)
    except Exception as e:
        logger.warning(f"建议候选抽取失败 record_id={record_id}: {e}")


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
                # 本地有 index_code 但无估值历史，尝试兜底
                idx_name = holding.get("index_name", "")
                fallback = _fetch_valuation_fallback(index_name=idx_name, fund_code=fund_code)
                if fallback:
                    valuation_section = f"{fallback}\n"
                else:
                    valuation_section = "暂无估值历史数据\n"
        else:
            val = get_latest_valuation(fund_code)
            if val:
                valuation_section = f"当前PE分位: {val.get('percentile','N/A')}%\n"
            else:
                # 兜底：尝试天天基金 / 盈米 MCP
                idx_name = holding.get("index_name", "")
                fallback = _fetch_valuation_fallback(index_name=idx_name, fund_code=fund_code)
                if fallback:
                    valuation_section = f"{fallback}\n"
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

    # 4) 组合事实层 — 统一的组合上下文（替代 portfolio_context + bond_context）
    facts_context = ""
    try:
        from portfolio_fact_layer import build_portfolio_facts
        facts = build_portfolio_facts()
        facts_context = json.dumps(facts, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        facts_context = f"组合事实获取失败: {e}"

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
        f"- 账户: {holding.get('account') or _get_config('portfolio.default_account', '花无缺')}\n"
        f"- 持有份额: {holding.get('shares',0):.4f}\n"
        f"- 成本净值: {holding.get('cost_price',0):.4f}\n"
        f"- 当前净值: {holding.get('current_price',0):.4f}\n"
        f"- 市值: {holding.get('current_value',0):.2f}\n"
        f"- 盈亏: {holding.get('profit_loss',0):.2f} ({holding.get('profit_rate',0)*100:.1f}%)\n"
        f"- 持有时间: 自首次交易起\n"
        f"\n## 交易记录\n" + ("\n".join(tx_lines) if tx_lines else "无交易记录") +
        f"\n\n## 估值数据\n{valuation_section}"
        f"\n\n{fundamentals_section}"
        f"\n\n## 组合约束（系统注入，优先级最高）\n"
        f"```json\n{facts_context}\n```"
        f"\n\n## MCP 诊断\n{_get_fund_mcp_diagnosis(fund_code)}"
        f"\n\n{news_context}"
        f"\n\n## 知识库参考\n{rag_context[:1500] if rag_context else '暂无相关知识库内容'}"
    )

    # 创建记录（status='running'）
    record_id = create_portfolio_analysis_record(
        analysis_type="deep_dive",
        summary=f"深度分析 · {fund_name}",
        input_data=json.dumps({"holding_id": holding_id, "fund_code": fund_code}, ensure_ascii=False),
        status="running",
        agent_id=4,
    )

    # 后台执行分析
    task = asyncio.create_task(_run_deep_dive_async(record_id, agent["system_prompt"], user_content))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"ok": True, "id": record_id, "status": "running"}


async def _run_deep_dive_async(record_id: int, system_prompt: str, user_content: str):
    """后台执行单基金深度分析。"""
    import uuid
    trace_id = f"deep_{uuid.uuid4().hex[:12]}"
    try:
        from llm_service import _call_llm, MODEL
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="portfolio_deep_dive",
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
        _extract_candidates_safely(record_id, "deep_dive", result_text)

        # ── 桥接 B：保存分析结论 ──
        try:
            # 从 input 中解析基金代码（user_content 在外部作用域不可直接访问）
            from db import get_portfolio_analysis_record
            rec = get_portfolio_analysis_record(record_id)
            input_data = rec.get("input_data", "{}") if rec else "{}"
            try:
                inp = json.loads(input_data) if isinstance(input_data, str) else input_data
                fund_code = inp.get("fund_code", "")
            except Exception:
                fund_code = ""

            summary = result_text[:100].replace("\n", " ").strip() if result_text else ""
            action = "hold"
            for candidate, act in [("减仓", "decrease"), ("加仓", "increase"),
                                    ("买入", "buy"), ("卖出", "sell"),
                                    ("持有", "hold"), ("观望", "hold")]:
                if candidate in (result_text or ""):
                    action = act
                    break

            key_vars = []
            for var in ["估值", "PE", "PB", "百分位", "收益率", "持仓",
                        "回撤", "波动", "规模", "费率", "行业"]:
                if var in (result_text or ""):
                    key_vars.append(var)

            save_analysis_conclusion(
                source_system="independent_analysis",
                source_type="deep_dive",
                source_id=record_id,
                target_subject=fund_code or "未知基金",
                action=action,
                summary=summary,
                reasoning=result_text[100:250].replace("\n", " ").strip() if len(result_text or "") > 100 else "",
                key_variables=key_vars[:5] if key_vars else None,
            )
            # 关联对话上下文（调用 daily_report 的函数）
            try:
                from routers.analysis.daily_report import _attach_chat_context
                _attach_chat_context(result_text)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"深度分析结论保存失败 record_id={record_id}: {e}")

        logger.info(f"深度分析完成 record_id={record_id}")
    except Exception as e:
        logger.error(f"深度分析失败 record_id={record_id}: {e}")
        update_analysis_record(record_id, status="error", error_msg=str(e))


@router.get("/api/portfolio/analysis/deep-dive/records")
async def list_deep_dive_records_api(limit: int = 10):
    """列出深度分析历史记录。"""
    from db import list_portfolio_analysis_records
    records = list_portfolio_analysis_records(analysis_type="deep_dive", limit=limit)
    return {"records": records}
