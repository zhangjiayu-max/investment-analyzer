"""市场日报自动生成服务 — 从 app.py 提取

职责：
1. 收集多维数据上下文（新闻、行情、估值、持仓、债市）
2. 调用 LLM 生成市场分析报告
3. 写入 analysis_history 表
4. 触发自动质量评估
"""

import asyncio
import json
import logging
import time

from db import get_analysis_agent, create_analysis_history
from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


async def generate_daily_report():
    """启动时自动检查并生成今日市场分析报告。"""
    try:
        # 等待服务完全启动
        await asyncio.sleep(5)
        # 检查今日是否已有报告
        today = time.strftime("%Y-%m-%d")
        from db import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT id FROM analysis_history WHERE agent_id = 1 AND date(created_at) = ? LIMIT 1",
            (today,)
        ).fetchone()
        conn.close()

        if row:
            logger.info(f"今日市场报告已存在 (id={row['id']})，跳过自动生成")
            return

        logger.info("今日市场报告不存在，后台自动生成中...")
        agent = get_analysis_agent(1)
        if not agent:
            logger.warning("市场日报分析师未配置，跳过自动生成")
            return

        # ── 收集丰富数据上下文 ──
        news_context = _fetch_news_context()
        yingmi_context = _fetch_yingmi_context()
        market_context = _fetch_market_context()
        val_context = _fetch_valuation_context()
        holding_text, portfolio_text = _fetch_portfolio_context()
        bond_text = _fetch_bond_context()

        # ── 组装 prompt ──
        full_prompt = _build_report_prompt(
            agent, news_context, yingmi_context, market_context,
            val_context, holding_text, portfolio_text, bond_text,
        )

        response = await asyncio.to_thread(lambda: _call_llm(
            caller="daily_report",
            model=MODEL,
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": "请生成今日市场分析报告。"},
            ],
            temperature=0.3,
            max_tokens=8192,
        ))
        result_text = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0

        report_id = create_analysis_history(
            index_code="", index_name="",
            agent_id=1, agent_name=agent["name"],
            prompt_used=full_prompt[:500], news_context=news_context[:500],
            valuation_context=val_context[:500], result=result_text,
            token_usage=token_usage,
        )
        logger.info(f"今日市场报告后台自动生成完成，token用量: {token_usage}")

        # 后台自动质量评估
        asyncio.create_task(_auto_eval_report(result_text, news_context, val_context, report_id))

    except Exception as e:
        logger.warning(f"自动生成市场报告失败: {e}")


# ── 数据收集函数 ──────────────────────────────────────────

def _fetch_news_context() -> str:
    """获取今日新闻。"""
    try:
        from routers.dashboard import get_hot_topics
        # 注意：get_hot_topics 是 async，需要事件循环
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在已有事件循环中，创建任务
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_hot_topics())
                news_data = future.result(timeout=30)
        else:
            news_data = asyncio.run(get_hot_topics())
        news_list = news_data.get("news", [])[:8]
        return "\n".join(
            f"- {n.get('title','')}（{n.get('source','')}）"
            for n in news_list if n.get('title')
        ) if news_list else "暂无新闻"
    except Exception as e:
        logger.warning(f"自动报告新闻检索失败: {e}")
        return "暂无新闻"


def _fetch_yingmi_context() -> str:
    """获取盈米 MCP 数据（市场温度 + 行情解读）。"""
    try:
        from mcp.yingmi_client import get_yingmi_client
        ym = get_yingmi_client()
        quotations = ym.call_tool_text("GetLatestQuotations")
        if quotations:
            return f"【盈米市场温度计及行情解读】\n{quotations[:2000]}"
    except Exception as e:
        logger.warning(f"盈米 MCP 数据获取失败: {e}")
    return ""


def _fetch_market_context() -> str:
    """获取市场全景（指数行情 + 板块涨跌 + 涨跌家数）。"""
    try:
        from market_data import get_market_overview
        overview = get_market_overview()
        market_lines = []
        if overview.get("indices"):
            market_lines.append("【主要指数】")
            for idx in overview["indices"]:
                sign = "+" if idx["change_pct"] >= 0 else ""
                market_lines.append(
                    f"- {idx['name']}: {idx['price']}（{sign}{idx['change_pct']}%）"
                    f"成交{idx.get('volume_yi', 0):.0f}亿"
                )
        b = overview.get("breadth", {})
        up = b.get('up')
        down = b.get('down')
        if up is not None or down is not None:
            limit_up = b.get('limit_up')
            limit_down = b.get('limit_down')
            lu_str = str(limit_up) if limit_up is not None else '暂无'
            ld_str = str(limit_down) if limit_down is not None else '暂无'
            market_lines.append(
                f"\n【涨跌统计】上涨{up or 0} / 下跌{down or 0} / "
                f"涨停{lu_str} / 跌停{ld_str} / "
                f"成交{b.get('total_volume_yi', 0):.0f}亿"
            )
        if overview.get("sectors_top"):
            market_lines.append("\n【领涨板块】")
            for s in overview["sectors_top"]:
                market_lines.append(f"- {s['name']}: +{s['change_pct']}%  领涨:{s['lead_stock']}{s['lead_change']}%")
        if overview.get("sectors_bottom"):
            market_lines.append("\n【领跌板块】")
            for s in overview["sectors_bottom"]:
                market_lines.append(f"- {s['name']}: {s['change_pct']}%  领涨:{s['lead_stock']}{s['lead_change']}%")
        return "\n".join(market_lines) if market_lines else "暂无行情数据"
    except Exception as e:
        logger.warning(f"行情数据获取失败: {e}")
        return "暂无行情数据"


def _fetch_valuation_context() -> str:
    """获取指数估值数据。"""
    try:
        from db import list_valuation_indexes
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
                pct_str = f"{pct:.0f}%" if pct is not None else "N/A"
                val_lines.append(
                    f"- {i['index_name']}（{i['index_code']}）: "
                    f"{i.get('metric_type', 'PE')}={i.get('current_value', '?')}, 百分位={pct_str}"
                )
            return "\n".join(val_lines)
    except Exception:
        pass
    return "暂无估值数据"


def _fetch_portfolio_context() -> tuple[str, str]:
    """获取持仓数据，返回 (holding_text, portfolio_text)。"""
    holding_text = "暂无持仓"
    portfolio_text = "暂无"
    try:
        from db import list_holdings, get_portfolio_diversification
        holdings = list_holdings()
        div = get_portfolio_diversification()
        if holdings:
            sorted_holdings = sorted(
                holdings, key=lambda x: x.get("profit_rate") or 0, reverse=True
            )
            holding_lines = []
            for h in sorted_holdings[:15]:
                pct = h.get("profit_rate")
                pct_str = f"{pct:+.1f}%" if pct is not None else "N/A"
                val = h.get("current_value", 0) or 0
                profit = h.get("profit", 0) or 0
                holding_lines.append(
                    f"- {h['fund_name']}（{h.get('fund_code', '')}）: "
                    f"市值{val:.0f}元, 收益率{pct_str}, 盈亏{profit:+.0f}元"
                )
            holding_text = "\n".join(holding_lines)
        portfolio_text = (
            f"持仓{div.get('holding_count', 0)}只基金，"
            f"总市值{div.get('total_value', 0):.0f}元，"
            f"累计盈亏{div.get('total_profit', 0):+.0f}元"
        )
    except Exception:
        pass
    return holding_text, portfolio_text


def _fetch_bond_context() -> str:
    """获取债市数据。"""
    try:
        from tools import _get_bond_temperature
        bond_raw = json.loads(_get_bond_temperature())
        return f"债券温度{bond_raw.get('temperature', '?')}°，收益率{bond_raw.get('rate', '?')}%"
    except Exception:
        pass
    return "暂无"


def _build_report_prompt(
    agent: dict, news_context: str, yingmi_context: str,
    market_context: str, val_context: str,
    holding_text: str, portfolio_text: str, bond_text: str,
) -> str:
    """组装日报生成的完整 prompt。"""
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return agent["system_prompt"] + f"""

【今日日期】
{time.strftime("%Y-%m-%d")}（{weekdays[time.localtime().tm_wday]}）

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


async def _auto_eval_report(result_text: str, news_context: str, val_context: str, report_id: int):
    """后台自动质量评估。"""
    try:
        from agent.eval_scorer import evaluate_llm_output
        await evaluate_llm_output(
            query="生成今日市场简报",
            output=result_text,
            context=f"新闻: {news_context[:300]}\n估值: {val_context[:300]}",
            target_type="daily_report",
            target_id=report_id,
        )
    except Exception as e:
        logger.warning(f"简报自动质量评估失败: {e}")
