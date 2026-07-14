"""每日简报 — 从 dashboard.py 提取"""
import asyncio
import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, HTTPException

from db import (
    list_valuation_indexes, list_holdings, get_portfolio_diversification,
    get_total_cash_balance, get_analysis_agent,
    create_analysis_history,
    create_async_task, update_async_task, get_async_task, get_latest_async_task,
    get_config_int, get_config_float,
    save_analysis_conclusion,
    get_related_orchestrator_decisions,
)
from db._conn import _get_conn
from db.agent_analysis_log import create_analysis_log, complete_analysis_log
from services.llm_service import _call_llm, MODEL
from infra.state import track_agent as _track_agent, untrack_agent as _untrack_agent

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-daily-report"])

_background_tasks = set()


# ── 桥接 B：对话→分析关联 ──


def _extract_report_keywords(report_text: str) -> list[str]:
    """从分析报告文本中提取关键词，用于匹配对话。"""
    keywords = set()

    # 基金代码模式（6位数字）
    for m in re.finditer(r'\b(\d{6})\b', report_text or ""):
        keywords.add(m.group(1))

    # 话题标签
    for kw in ["债市", "股市", "定投", "减仓", "加仓", "止盈", "止损", "调仓",
               "利率", "估值", "分散", "集中", "现金", "备用金",
               "债券", "股票", "指数", "基金", "组合"]:
        if kw in (report_text or ""):
            keywords.add(kw)

    return list(keywords)[:10]


def _attach_chat_context(report: dict | str, hours: int = 48) -> dict:
    """
    分析报告生成完后，检查最近对话中有没有相关的结论，
    如果有，在 report 末尾加一个"💬 AI对话相关内容"区域。

    Returns:
        如果有关联，在 report dict 中添加 chat_context 字段
    """
    if isinstance(report, str):
        report_text = report
        report = {"result": report_text}
    else:
        report_text = report.get("result", "") or ""

    if not report_text:
        return report

    try:
        topics = _extract_report_keywords(report_text)
        if not topics:
            return report

        decisions = get_related_orchestrator_decisions(
            keywords=topics,
            hours=hours,
            limit=3,
        )

        if not decisions:
            return report

        report["chat_context"] = {
            "title": "💬 AI对话相关内容",
            "note": "以下观点来自近期AI对话，供交叉参考：",
            "items": [
                {
                    "time": d.get("created_at", ""),
                    "content": (d.get("summary", "") or "")[:200],
                }
                for d in decisions
            ],
        }
    except Exception as e:
        logger.warning(f"_attach_chat_context 失败: {e}")

    return report


@router.get("/api/dashboard/daily-report")
async def get_daily_report():
    """获取今日自动生成的日报。如果没有今日的，返回最近一条。"""
    import logging
    logging.warning("=== get_daily_report called (NEW VERSION) ===")
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM analysis_history WHERE agent_id = 1 AND date(created_at) = ? ORDER BY created_at DESC LIMIT 1",
        (today,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM analysis_history WHERE agent_id = 1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    conn.close()
    if row:
        r = dict(row)
        is_today = r.get("created_at", "").startswith(today)
        return {"has_report": True, "report": r, "is_today": is_today}
    return {"has_report": False, "report": None, "is_today": False}


@router.get("/api/dashboard/daily-report/task")
async def get_daily_report_task():
    """查询最近一次每日简报异步任务状态。"""
    task = get_latest_async_task("daily_report")
    if not task:
        return {"has_task": False, "status": None}
    return {
        "has_task": True,
        "task_id": task["id"],
        "status": task["status"],
        "error": task.get("error_msg", ""),
        "created_at": task.get("created_at", ""),
    }


@router.post("/api/dashboard/daily-report/regenerate")
async def regenerate_daily_report():
    """重新生成今日市场简报（异步）。立即返回 task_id，后台执行。"""
    agent = get_analysis_agent(1)
    if not agent:
        raise HTTPException(400, "市场日报分析师未配置")
    task_id = create_async_task("daily_report", caller="daily_report")
    task = asyncio.create_task(_run_regenerate_daily_report_async(task_id, agent))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_regenerate_daily_report_async(task_id: int, agent: dict):
    """后台执行重新生成今日市场简报。"""
    trace_id = f"log_{uuid.uuid4().hex[:12]}"
    _start_ts = time.time()
    try:
        # 删除今日旧报告
        today = time.strftime("%Y-%m-%d")
        conn = _get_conn()
        conn.execute(
            "DELETE FROM analysis_history WHERE agent_id = 1 AND date(created_at) = ?",
            (today,)
        )
        conn.commit()
        conn.close()

        # 复用 _auto_daily_report 的数据收集逻辑
        from routers.dashboard.dashboard import get_hot_topics
        news_context = ""
        try:
            news_data = await get_hot_topics()
            news_list = news_data.get("news", [])[:8]
            news_context = "\n".join(
                f"- {n.get('title','')}（{n.get('source','')}）"
                for n in news_list if n.get('title')
            ) if news_list else "暂无新闻"
        except Exception as e:
            logging.warning(f"简报重新生成新闻检索失败: {e}")
            news_context = "暂无新闻"

        # 盈米 MCP 数据
        yingmi_context = ""
        try:
            from mcp.yingmi_client import get_yingmi_client
            ym = get_yingmi_client()
            quotations = ym.call_tool_text("GetLatestQuotations")
            if quotations:
                yingmi_context = f"【盈米市场温度计及行情解读】\n{quotations[:2000]}"
        except Exception as e:
            logging.warning(f"盈米 MCP 数据获取失败: {e}")

        # 市场全景
        market_context = "暂无行情数据"
        try:
            from services.market_data import get_market_overview
            overview = get_market_overview()
            market_lines = []
            if overview.get("indices"):
                market_lines.append("【主要指数】")
                for idx in overview["indices"]:
                    sign = "+" if idx["change_pct"] >= 0 else ""
                    market_lines.append(f"- {idx['name']}: {idx['price']}（{sign}{idx['change_pct']}%）成交{idx.get('volume_yi',0):.0f}亿")
            b = overview.get("breadth", {})
            if b.get("up"):
                market_lines.append(f"\n【涨跌统计】上涨{b['up']} / 下跌{b['down']} / 成交{b.get('total_volume_yi',0):.0f}亿")
            if overview.get("sectors_top"):
                market_lines.append("\n【领涨板块】")
                for s in overview["sectors_top"]:
                    market_lines.append(f"- {s['name']}: +{s['change_pct']}%")
            if overview.get("sectors_bottom"):
                market_lines.append("\n【领跌板块】")
                for s in overview["sectors_bottom"]:
                    market_lines.append(f"- {s['name']}: {s['change_pct']}%")
            market_context = "\n".join(market_lines) if market_lines else "暂无行情数据"
        except Exception as e:
            logging.warning(f"行情数据获取失败: {e}")

        val_context = "暂无估值数据"
        try:
            indexes = list_valuation_indexes()
            seen = {}
            for i in indexes:
                code = i.get("index_code", "")
                if code and code not in seen:
                    seen[code] = i
            all_indexes = list(seen.values())
            if all_indexes:
                val_lines = []
                for i in all_indexes:
                    pct = i.get("percentile", None)
                    # 规范化：字符串如 "57.53%" → float 57.53
                    if isinstance(pct, str):
                        pct = pct.strip().rstrip('%')
                        try:
                            pct = float(pct)
                        except ValueError:
                            pct = None
                    pct_str = f"{pct:.0f}%" if pct is not None else "N/A"
                    val_lines.append(
                        f"- {i['index_name']}（{i['index_code']}）: "
                        f"{i.get('metric_type','PE')}={i.get('current_value','?')}, 百分位={pct_str}"
                    )
                val_context = "\n".join(val_lines)
        except Exception:
            pass

        holding_text = "暂无持仓"
        portfolio_text = "暂无"
        try:
            holdings = list_holdings()
            div = get_portfolio_diversification()
            cash_balance = get_total_cash_balance()
            if holdings:
                sorted_holdings = sorted(holdings, key=lambda x: x.get("profit_rate") or 0, reverse=True)
                holding_lines = []
                for h in sorted_holdings[:15]:
                    pct = h.get("profit_rate")
                    pct_str = f"{pct:+.1%}" if pct is not None else "N/A"
                    val = h.get("current_value", 0) or 0
                    profit = h.get("profit_loss", 0) or 0
                    holding_lines.append(
                        f"- {h['fund_name']}（{h.get('fund_code','')}）: "
                        f"市值{val:.0f}元, 收益率{pct_str}, 盈亏{profit:+.0f}元"
                    )
                holding_text = "\n".join(holding_lines)
            portfolio_text = (
                f"持仓{div.get('holding_count',0)}只基金，"
                f"总市值{div.get('total_value',0):.0f}元，"
                f"累计盈亏{div.get('total_profit',0):+.0f}元，"
                f"可用零钱{cash_balance:.0f}元"
            )
        except Exception:
            pass

        bond_text = "暂无"
        try:
            from tools import _get_bond_temperature
            bond_raw = json.loads(_get_bond_temperature())
            rate_val = bond_raw.get('rate', None)
            if rate_val is not None and isinstance(rate_val, (int, float)):
                rate_pct = rate_val * 100  # 0.017448 → 1.7448
                bond_text = f"债券温度{bond_raw.get('temperature','?')}°，10年国债收益率{rate_pct:.2f}%"
            else:
                bond_text = f"债券温度{bond_raw.get('temperature','?')}°，收益率{bond_raw.get('rate','?')}%"
        except Exception:
            pass

        full_prompt = agent["system_prompt"] + f"""

【今日日期】
{time.strftime("%Y-%m-%d")}（{["周一","周二","周三","周四","周五","周六","周日"][time.localtime().tm_wday]}）

"""

        # 注入组合约束
        try:
            from services.portfolio_fact_layer import build_portfolio_facts
            facts = build_portfolio_facts()
            facts_json = json.dumps(facts, ensure_ascii=False, indent=2, default=str)
            full_prompt += f"""【组合约束】
```json
{facts_json}
```

"""
        except Exception:
            pass

        full_prompt += f"""
【今日新闻】
{news_context}

{yingmi_context}

【市场行情】
{market_context}

【指数估值】
{val_context}

【持仓明细】（已按收益率从高到低排序）
{holding_text}

【持仓概况】
{portfolio_text}

【债券市场】
{bond_text}

请按照报告结构要求，基于以上真实数据撰写今日市场简报。"""

        response = await asyncio.to_thread(lambda: _call_llm(
            caller="daily_report",
            trace_id=trace_id,
            model=MODEL,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": "请生成今日市场分析报告。"},
            ],
            temperature=get_config_float('llm.temperature_default', 0.3),
            max_tokens=get_config_int('llm.max_tokens_report', 8192),
        ))
        result_text = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0

        new_id = create_analysis_history(
            index_code="", index_name="",
            agent_id=1, agent_name=agent["name"],
            prompt_used=full_prompt[:500], news_context=news_context[:500],
            valuation_context=val_context[:500], result=result_text,
            token_usage=token_usage,
        )
        _elapsed_ms = int((time.time() - _start_ts) * 1000)
        create_analysis_log(
            trace_id=trace_id, agent_id=1, agent_name="市场日报分析师",
            analysis_type="daily_report", source_table="analysis_history",
            source_id=new_id, query="生成今日市场简报",
            input_summary=f"日报:{time.strftime('%Y-%m-%d')}",
        )
        complete_analysis_log(trace_id=trace_id, status="done", duration_ms=_elapsed_ms, token_usage=token_usage)

        # ── 桥接 B：保存分析结论 + 关联对话上下文 ──
        try:
            # 提取摘要为前100字
            summary = result_text[:100].replace("\n", " ").strip() if result_text else ""
            # 提取核心操作建议
            action = "hold"
            for candidate, act in [("减仓", "decrease"), ("加仓", "increase"),
                                    ("买入", "buy"), ("卖出", "sell"),
                                    ("止盈", "decrease"), ("止损", "sell"),
                                    ("持有", "hold"), ("定投", "increase"),
                                    ("观望", "hold")]:
                if candidate in (result_text or ""):
                    action = act
                    break
            # 提取关键变量
            key_vars = []
            for var in ["债市温度", "利率", "估值", "百分位", "PE", "PB",
                        "收益率", "集中度", "仓位", "成交量"]:
                if var in (result_text or ""):
                    key_vars.append(var)

            save_analysis_conclusion(
                source_system="independent_analysis",
                source_type="daily_report",
                source_id=new_id,
                target_subject="整体组合",
                action=action,
                summary=summary,
                reasoning=result_text[100:250].replace("\n", " ").strip() if len(result_text or "") > 100 else "",
                key_variables=key_vars[:5] if key_vars else None,
            )
            # 关联对话上下文
            _attach_chat_context(result_text)
        except Exception as e:
            logger.warning(f"日报结论保存/关联对话失败: {e}")

        # 后台自动质量评估
        async def _auto_eval():
            try:
                from agent.eval_scorer import evaluate_llm_output
                await evaluate_llm_output(
                    query="生成今日市场简报",
                    output=result_text,
                    context=f"新闻: {news_context[:300]}\n估值: {val_context[:300]}",
                    target_type="daily_report",
                    target_id=new_id,
                )
            except Exception as e:
                logging.warning(f"简报自动质量评估失败: {e}")
        asyncio.create_task(_auto_eval())

        update_async_task(task_id, status="done", result={"ok": True, "id": new_id, "token_usage": token_usage}, token_usage=token_usage)
    except Exception as e:
        logging.error(f"重新生成日报异步任务失败: {e}")
        _elapsed_ms = int((time.time() - _start_ts) * 1000)
        create_analysis_log(
            trace_id=trace_id, agent_id=1, agent_name="市场日报分析师",
            analysis_type="daily_report", source_table="analysis_history",
            source_id=None, query="生成今日市场简报",
            input_summary=f"日报:{time.strftime('%Y-%m-%d')}",
        )
        complete_analysis_log(trace_id=trace_id, status="error", duration_ms=_elapsed_ms, error_msg=str(e))
        update_async_task(task_id, status="error", error_msg=str(e))
