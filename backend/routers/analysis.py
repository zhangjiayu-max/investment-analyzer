"""AI 市场分析路由 — /api/analysis/*、/api/analysis-agents/*"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from db import (
    get_analysis_agent, list_analysis_agents, update_analysis_agent,
    create_analysis_history, list_analysis_history,
    get_analysis_history_item, get_analysis_history_status,
    update_analysis_history, delete_analysis_history,
    save_prompt_version,
    get_latest_valuation, get_valuation_history,
    list_holdings,
    create_async_task, update_async_task, get_async_task,
)
from llm_service import _call_llm, MODEL
from rag import build_rag_context_with_details, log_rag_search
from models.analysis import AnalysisRunRequest, AnalysisAgentUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])

_background_tasks = set()


@router.post("/api/analysis/run")
async def run_analysis(req: AnalysisRunRequest):
    """触发 AI 指数深度分析（后台异步执行）。"""
    agent = get_analysis_agent(req.agent_id)
    if not agent:
        raise HTTPException(404, "分析 Agent 不存在")

    history_id = create_analysis_history(
        index_code=req.index_code,
        index_name=req.index_name,
        agent_id=agent["id"],
        agent_name=agent["name"],
        prompt_used=agent.get("system_prompt", "")[:500],
        news_context="",
        valuation_context="",
        result="",
        token_usage=0,
        status="running",
    )
    # 同时创建 async_task 记录（统一异步任务跟踪）
    async_task_id = create_async_task("index_analysis", caller="index_deep_analysis")
    task = asyncio.create_task(_run_index_analysis_async(history_id, req.dict(), agent, async_task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"ok": True, "id": history_id, "task_id": async_task_id, "status": "running"}


async def _run_index_analysis_async(history_id: int, req_data: dict, agent: dict, async_task_id: int = None):
    """后台执行指数深度分析。"""
    req = AnalysisRunRequest(**req_data)
    index_label = req.index_name or req.index_code

    # 2. 获取估值数据（当前 + 近60天历史趋势）
    valuation_context = ""
    if req.index_code:
        try:
            latest = get_latest_valuation(req.index_code)
            if latest:
                valuation_context = json.dumps(latest, ensure_ascii=False, indent=2)
            # 追加近60天估值历史
            history = get_valuation_history(req.index_code, days=60)
            if history:
                valuation_context += f"\n\n近{len(history)}天估值历史趋势：\n" + json.dumps(history, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 3. RAG 知识库检索（搜索与该指数相关的文章、分析、估值记录）
    rag_context = ""
    rag_result = {}
    if req.index_name:
        try:
            rag_query = f"{req.index_name} 估值 分析 投资"
            rag_result = build_rag_context_with_details(query=rag_query, limit=5)
            rag_context = rag_result.get("context", "")
            # 记录 RAG 检索日志
            log_rag_search(
                conversation_id=0,  # 单次分析无对话 ID
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

    # 4. 用户持仓数据（与该指数相关的持仓）
    portfolio_context = ""
    try:
        all_holdings = list_holdings()
        if all_holdings and (req.index_code or req.index_name):
            # 精确匹配：index_code 相同
            matched = [h for h in all_holdings if h.get("index_code") and h["index_code"] == req.index_code]
            # 模糊匹配：基金名称包含指数关键词（如"沪深300"匹配"沪深300ETF"）
            if req.index_name:
                keywords = req.index_name.replace("指数", "").strip()
                fuzzy_matched = [h for h in all_holdings
                                 if h.get("fund_name") and keywords in h["fund_name"]
                                 and h not in matched]
                matched.extend(fuzzy_matched)
            if matched:
                portfolio_lines = []
                total_value = sum(h.get("current_value", 0) for h in all_holdings if h.get("current_value"))
                for h in matched:
                    val = h.get("current_value", 0)
                    pnl = h.get("profit_loss", 0)
                    rate = h.get("profit_rate", 0)
                    pct = round(val / total_value * 100, 1) if total_value > 0 else 0
                    status = "盈利" if pnl > 0 else ("亏损" if pnl < 0 else "持平")
                    portfolio_lines.append(
                        f"- {h['fund_name']}({h.get('fund_code','')})："
                        f"市值 ¥{val:,.0f}，占总资产 {pct}%，"
                        f"盈亏 ¥{pnl:,.0f}（{rate:+.2f}%），{status}"
                    )
                portfolio_context = (
                    f"用户当前持有 {len(matched)} 只与{index_label}相关的基金：\n"
                    + "\n".join(portfolio_lines)
                )
    except Exception as e:
        logger.warning(f"持仓数据获取失败: {e}")

    # 5. 指数专属新闻（多源 + 关联词扩展搜索）
    news_context = ""
    try:
        from tools import execute_tool
        news_query = f"{index_label} 最新消息 政策" if req.index_name else "A股 今日行情 板块 热点"
        news_result = execute_tool("web_search", {"query": news_query, "max_results": 5})
        news_context = news_result if news_result else ""
    except Exception as e:
        logger.warning(f"akshare 新闻检索失败: {e}")

    # YingMi MCP 搜索：直接关键词 + 关联关键词
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        # 构建搜索关键词列表：先搜直接名称，再搜关联词
        search_keywords = [index_label] if index_label else []
        # 补充关联搜索词（提高覆盖率，尤其是港股/跨市场指数）
        name_lower = (req.index_name or "").lower()
        if "恒生" in name_lower or "港" in name_lower:
            search_keywords.extend(["港股科技", "南向资金", "港股通 科技"])
        elif "科技" in name_lower or "信息" in name_lower or "电子" in name_lower:
            search_keywords.extend(["科技板块", "半导体 AI"])
        elif "消费" in name_lower:
            search_keywords.extend(["消费板块", "白酒 食品"])
        elif "医药" in name_lower or "生物" in name_lower:
            search_keywords.extend(["医药板块", "创新药"])
        elif "新能源" in name_lower or "光伏" in name_lower:
            search_keywords.extend(["新能源", "光伏 锂电"])
        elif "金融" in name_lower or "银行" in name_lower or "证券" in name_lower:
            search_keywords.extend(["金融板块", "银行 券商"])
        # A 股大盘相关（所有指数都可能受此影响）
        search_keywords.append("A股 市场")

        mcp_items = []
        seen_titles = set()
        for kw in search_keywords[:3]:  # 最多 3 轮搜索，避免太慢
            try:
                mcp_raw = await asyncio.to_thread(
                    lambda _kw=kw: mcp.call_tool("SearchFinancialNews", {"keyword": _kw, "pageSize": 3})
                )
                if isinstance(mcp_raw, dict):
                    for c in mcp_raw.get("content", []):
                        if c.get("type") == "text":
                            parsed = json.loads(c["text"])
                            if parsed.get("success") and parsed.get("data", {}).get("items"):
                                for item in parsed["data"]["items"]:
                                    title = item.get("title", "").strip()
                                    if title and title not in seen_titles:
                                        seen_titles.add(title)
                                        mcp_items.append(
                                            f"- {title}（{item.get('sources', '')}）: {item.get('summary', '')[:120]}"
                                        )
            except Exception:
                continue

        if mcp_items:
            mcp_news = "\n".join(mcp_items)
            news_context = (news_context + "\n\n" + mcp_news).strip() if news_context else mcp_news
    except Exception as e:
        logger.warning(f"MCP 新闻检索失败: {e}")

    # 6. 拼装 prompt（加入关联推理引导）
    full_prompt = agent["system_prompt"]
    # 引导 LLM 做跨市场关联分析
    full_prompt += """

## 重要提示：关联性分析
- 如果没有直接关于该指数的新闻，**不要说"没有相关新闻"就结束**
- 分析 A 股大盘、相关板块、宏观政策对该指数的**间接影响**
- 对于港股指数：A 股科技板块动态、南向资金流向、中美关系、美联储政策都会影响
- 对于行业指数：上下游产业链新闻、政策风向、资金轮动都是重要信号
- **从已有新闻中提炼与该指数相关的信息**，而不是只看标题是否包含指数名称"""
    if valuation_context:
        full_prompt += f"\n\n<valuation_data>\n指数估值数据（{index_label}）：\n{valuation_context}\n</valuation_data>"
    if rag_context:
        full_prompt += f"\n\n<knowledge_base>\n知识库检索结果（与{index_label}相关的历史分析和文章）：\n{rag_context}\n</knowledge_base>"
    if portfolio_context:
        full_prompt += f"\n\n<user_portfolio>\n用户持仓情况（与{index_label}相关）：\n{portfolio_context}\n</user_portfolio>"
    if news_context:
        full_prompt += f"\n\n<latest_news>\n{index_label}相关新闻与政策：\n{news_context}\n</latest_news>"

    try:
        response = await asyncio.to_thread(lambda: _call_llm(
            caller="index_deep_analysis",
            model=MODEL,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": f"请对 {index_label} 进行深度分析。"},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"AI 分析失败: {e}")
        update_analysis_history(history_id, status="error", error_msg=str(e))
        if async_task_id:
            update_async_task(async_task_id, status="error", error_msg=str(e))
        return

    update_analysis_history(
        history_id,
        prompt_used=full_prompt,
        news_context=news_context,
        valuation_context=valuation_context,
        result=result_text,
        token_usage=token_usage,
        status="done",
        error_msg="",
    )
    if async_task_id:
        update_async_task(async_task_id, status="done", result={"history_id": history_id, "result": result_text, "token_usage": token_usage}, token_usage=token_usage)

    async def _auto_evaluate():
        try:
            from agent.eval_scorer import evaluate_llm_output
            await evaluate_llm_output(
                query=f"分析 {req.index_name}({req.index_code}) 的估值",
                output=result_text,
                context=f"新闻: {news_context[:300]}\n估值: {valuation_context[:300]}",
                target_type="analysis",
                target_id=history_id,
            )
        except Exception as e:
            logger.warning(f"自动质量评估失败: {e}")
    asyncio.create_task(_auto_evaluate())


@router.get("/api/analysis/history")
async def get_analysis_history_api(index_code: str = "", limit: int = 50):
    """获取分析历史列表。"""
    return {"history": list_analysis_history(index_code or None, limit)}


@router.get("/api/analysis/history/{history_id}")
async def get_analysis_history_detail_api(history_id: int):
    """获取单条分析历史详情。"""
    item = get_analysis_history_item(history_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    return item


@router.get("/api/analysis/history/{history_id}/status")
async def get_analysis_history_status_api(history_id: int):
    """查询指数深度分析执行状态。"""
    item = get_analysis_history_status(history_id)
    if not item:
        raise HTTPException(404, "记录不存在")
    return {
        "id": item["id"],
        "status": item.get("status", "done"),
        "result": item.get("result", ""),
        "token_usage": item.get("token_usage", 0),
        "error": item.get("error_msg", ""),
    }


@router.delete("/api/analysis/history/{history_id}")
async def delete_analysis_history_api(history_id: int):
    """删除分析历史。"""
    delete_analysis_history(history_id)
    return {"ok": True}


@router.get("/api/analysis-agents")
async def list_analysis_agents_api():
    """获取分析 Agent 列表。"""
    return {"agents": list_analysis_agents()}


@router.put("/api/analysis-agents/{agent_id}")
async def update_analysis_agent_api(agent_id: int, req: AnalysisAgentUpdateRequest):
    """更新分析 Agent 配置。修改提示词时自动保存版本历史，并触发回归测试。"""
    kwargs = {k: v for k, v in req.dict().items() if v is not None}
    if not kwargs:
        raise HTTPException(400, "无更新内容")
    # 提示词变更前，保存当前版本
    prompt_changed = False
    if 'system_prompt' in kwargs:
        current = get_analysis_agent(agent_id)
        if current and kwargs['system_prompt'] != current.get('system_prompt'):
            save_prompt_version(agent_id, 'analysis', current['system_prompt'])
            prompt_changed = True
    update_analysis_agent(agent_id, **kwargs)
    # prompt 变更后触发回归测试
    if prompt_changed:
        try:
            from agent.regression import run_regression_tests
            asyncio.create_task(run_regression_tests(agent_id, "analysis"))
        except Exception as e:
            logger.warning(f"触发回归测试失败: {e}")
    return {"ok": True}
