"""每日投资决策看板 + 推荐反馈 + LLM 反馈路由 — /api/dashboard/*, /api/llm-feedback/*"""

import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException

from db import (
    list_valuation_indexes, list_index_freshness,
    list_holdings, get_portfolio_summary, get_portfolio_diversification,
    get_cash_balance, get_total_cash_balance,
    get_analysis_agent, create_analysis_history,
    save_recommendations, save_analysis_cache, get_analysis_cache,
    list_recommendations, auto_verify_pending_recommendations,
    save_recommendation_feedback, list_recommendation_feedback,
    get_recommendation_feedback_stats,
    save_llm_feedback, list_llm_feedback,
    get_config_int, get_config_float,
    create_async_task, update_async_task, get_async_task,
)
from db._conn import _get_conn
from llm_service import _call_llm, MODEL
from market_data import get_index_current_price
from state import track_agent as _track_agent, untrack_agent as _untrack_agent, hot_topics_cache as _hot_topics_cache

router = APIRouter(tags=["dashboard"])

_background_tasks = set()


# ── Dashboard 辅助函数 ────────────────────────────────────


def _assess_valuation(percentile: float) -> dict:
    """根据百分位给出估值评估（阈值从 system_config 读取）。"""
    extreme_low = get_config_int('valuation.extreme_undervalued', 10)
    undervalued = get_config_int('valuation.undervalued_percentile', 30)
    overvalued = get_config_int('valuation.overvalued_percentile', 70)
    extreme_high = get_config_int('valuation.extreme_overvalued', 90)
    # 中等阈值为可配置高低阈值的中间值
    low_mid = (extreme_low + undervalued) // 2
    fair_range = (undervalued + overvalued) // 2
    high_mid = (overvalued + extreme_high) // 2

    if percentile <= extreme_low:
        return {"label": "极度低估", "level": "extreme"}
    elif percentile <= low_mid:
        return {"label": "低估", "level": "undervalued"}
    elif percentile <= undervalued:
        return {"label": "偏低", "level": "slightly_low"}
    elif percentile <= fair_range:
        return {"label": "合理", "level": "fair"}
    elif percentile <= overvalued:
        return {"label": "偏高", "level": "slightly_high"}
    elif percentile <= high_mid:
        return {"label": "高估", "level": "overvalued"}
    else:
        return {"label": "极度高估", "level": "extreme_high"}


def _get_cash_advice(temperature, balance: float, total_assets: float = 0, undervalued_indexes: list = None) -> dict:
    """根据债市温度 + 权益估值给出零钱配置建议。

    Args:
        temperature: 债市温度 (0-100)，None 表示数据缺失
        balance: 现金余额
        total_assets: 总资产（持仓+现金），用于计算现金占比
        undervalued_indexes: 低估指数列表 [{index_name, percentile, ...}]
    """
    if not balance or balance <= 0:
        return {"summary": "暂无可用零钱", "allocation": [], "alerts": []}

    alerts = []
    cash_ratio = balance / total_assets if total_assets > 0 else 0

    cash_warning = get_config_float('cash.ratio_warning', 0.20)
    cash_low = get_config_float('cash.ratio_low', 0.03)
    cash_info = (cash_warning + cash_low) / 2  # 中间点用于 info 级别提示

    # 现金占比预警
    if cash_ratio > cash_warning:
        alerts.append({"level": "warning", "message": f"现金占比{cash_ratio:.0%}偏高，资金闲置会拖低整体收益"})
    elif cash_ratio > cash_info:
        alerts.append({"level": "info", "message": f"现金占比{cash_ratio:.0%}，可适当配置"})
    elif cash_ratio < cash_low and total_assets > 0:
        alerts.append({"level": "info", "message": f"现金占比仅{cash_ratio:.0%}，建议保留少量流动性"})

    # 低估权益机会
    equity_tip = None
    if undervalued_indexes and cash_ratio > 0.05:
        top = undervalued_indexes[:2]
        names = "、".join(i.get("index_name", "") for i in top if i.get("index_name"))
        if names:
            equity_tip = f"{names}处于低估区间，可考虑用少量零钱定投"
            alerts.append({"level": "opportunity", "message": equity_tip})

    # 债券建议
    bond_advice = _get_bond_allocation(temperature)

    # 组合建议摘要
    summary_parts = [bond_advice["summary"]]
    if equity_tip:
        summary_parts.append(equity_tip)
    if cash_ratio > 0.15:
        summary_parts.append(f"当前现金占比{cash_ratio:.0%}，建议逐步配置")

    return {
        "summary": "；".join(summary_parts),
        "allocation": bond_advice["allocation"],
        "cash_ratio": round(cash_ratio, 4),
        "alerts": alerts,
        "equity_opportunity": equity_tip,
    }


def _get_bond_allocation(temperature) -> dict:
    """根据债市温度返回债券配置建议（阈值从 system_config 读取）。"""
    temp_cold = get_config_int('bond.temp_cold', 30)
    temp_cool = get_config_int('bond.temp_cool', 50)
    temp_warm = get_config_int('bond.temp_warm', 70)

    if temperature is None:
        return {
            "summary": "债市数据暂缺，建议暂时放在货币基金中",
            "allocation": [
                {"name": "货币基金", "ratio": 100, "desc": "流动性好，风险低"},
            ],
        }
    elif temperature <= temp_cold:
        return {
            "summary": f"债市温度 {temperature}°，处于历史低位。债券收益率高，是配置中长期债券基金的好时机",
            "allocation": [
                {"name": "中长期债券基金", "ratio": 60, "desc": "收益率高位锁定收益"},
                {"name": "短债基金", "ratio": 25, "desc": "兼顾收益与流动性"},
                {"name": "货币基金", "ratio": 15, "desc": "日常备用"},
            ],
        }
    elif temperature <= (temp_cold + temp_cool) // 2:
        return {
            "summary": f"债市温度 {temperature}°，仍处于偏低区域，适合增加债券配置",
            "allocation": [
                {"name": "中长期债券基金", "ratio": 40, "desc": "获取较高收益"},
                {"name": "短债基金", "ratio": 40, "desc": "灵活调整"},
                {"name": "货币基金", "ratio": 20, "desc": "日常备用"},
            ],
        }
    elif temperature <= temp_cool:
        return {
            "summary": f"债市温度 {temperature}°，处于适中区域，建议短债为主均衡配置",
            "allocation": [
                {"name": "短债基金", "ratio": 50, "desc": "收益率尚可，风险可控"},
                {"name": "中长期债券基金", "ratio": 25, "desc": "少量参与"},
                {"name": "货币基金", "ratio": 25, "desc": "保留流动性"},
            ],
        }
    elif temperature <= temp_warm:
        return {
            "summary": f"债市温度 {temperature}°，偏高区域，债券价格已在高位，注意利率风险",
            "allocation": [
                {"name": "货币基金", "ratio": 50, "desc": "规避回调风险"},
                {"name": "短债基金", "ratio": 50, "desc": "短久期低波动"},
            ],
        }
    else:
        return {
            "summary": f"债市温度 {temperature}°，高温预警！债券价格处于历史高位，建议减配债券等待回调",
            "allocation": [
                {"name": "货币基金", "ratio": 70, "desc": "等待债市回调"},
                {"name": "短债基金", "ratio": 30, "desc": "极小仓位保持参与"},
            ],
        }


# ── Dashboard 主看板 ──────────────────────────────────────


@router.get("/api/dashboard")
async def get_dashboard():
    """每日投资决策看板 — 聚合四块核心数据。每个模块独立容错。"""
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")

    # ── 自动抓取近期估值数据（如果今天没有数据）──
    try:
        conn = _get_conn()
        today_count = conn.execute(
            "SELECT COUNT(*) FROM index_valuations WHERE snapshot_date = ?", (today,)
        ).fetchone()[0]
        conn.close()
        if today_count == 0:
            logging.info("今日无估值数据，自动抓取中...")
            # 延迟导入避免循环依赖（fetch_recent_valuations 定义在 app.py）
            from app import fetch_recent_valuations
            await fetch_recent_valuations()
    except Exception as e:
        logging.warning(f"自动抓取估值失败: {e}")

    # ── Section 1: 低估指数 ──
    undervalued = []
    try:
        indexes = list_valuation_indexes()
        # 按 index_code 去重，保留百分位最低的指标
        best_per_code = {}
        for idx in indexes:
            code = idx["index_code"]
            p = idx.get("percentile")
            if p is None:
                continue
            if code not in best_per_code or p < best_per_code[code]["percentile"]:
                assess = _assess_valuation(p)
                best_per_code[code] = {
                    "index_code": code,
                    "index_name": idx.get("index_name", ""),
                    "metric_type": idx.get("metric_type", ""),
                    "current_value": idx.get("current_value"),
                    "percentile": p,
                    "latest_date": idx.get("latest_date", ""),
                    "assessment": assess["label"],
                    "assessment_level": assess["level"],
                }
        # 过滤：低于系统配置的低估百分位阈值且数据新鲜
        undervalued_threshold = get_config_int('valuation.undervalued_percentile', 30)
        freshness_days = get_config_int('valuation.freshness_days', 30)
        from datetime import datetime, timedelta
        freshness_cutoff = (datetime.now() - timedelta(days=freshness_days)).strftime("%Y-%m-%d")
        undervalued = [
            v for v in best_per_code.values()
            if v["percentile"] <= undervalued_threshold and v.get("latest_date", "") >= freshness_cutoff
        ]
        undervalued.sort(key=lambda x: x["percentile"])
        # 记录数据最新日期
        if undervalued:
            latest_dates = [v.get("latest_date", "") for v in undervalued if v.get("latest_date")]
            undervalued_data_date = max(latest_dates) if latest_dates else ""
        else:
            undervalued_data_date = ""
    except Exception as e:
        undervalued_data_date = ""
        logging.warning(f"Dashboard 低估指数获取失败: {e}")

    # ── Section 2: 持仓健康度 ──
    portfolio_health = None
    try:
        holdings = list_holdings()
        active = [h for h in holdings if (h.get("shares") or 0) > 0]
        if active:
            summary = get_portfolio_summary()
            divers = get_portfolio_diversification()
            total_val = summary.get("total_value", 0) or 0
            sorted_h = sorted(active, key=lambda h: (h.get("current_value", 0) or 0), reverse=True)
            top3_pct = round(
                sum(h.get("current_value", 0) or 0 for h in sorted_h[:3]) / total_val * 100, 1
            ) if total_val > 0 else 0

            # 集中度评估（从 system_config 读取阈值）
            conc_high = get_config_int('concentration.top3_high', 60)
            conc_moderate = get_config_int('concentration.top3_moderate', 40)
            if top3_pct > conc_high:
                conc_level, conc_assess = "high", "前3持仓占比 %.1f%%，集中度很高，建议分散" % top3_pct
            elif top3_pct > conc_moderate:
                conc_level, conc_assess = "moderate", "前3持仓占比 %.1f%%，集中度偏高，可适当调整" % top3_pct
            else:
                conc_level, conc_assess = "low", "前3持仓占比 %.1f%%，分散度良好" % top3_pct

            portfolio_health = {
                "holding_count": summary.get("holding_count", 0),
                "total_value": round(total_val, 2),
                "total_profit": round(summary.get("total_profit", 0), 2),
                "profit_rate": summary.get("profit_rate", 0),
                "max_holding_pct": divers.get("max_holding_pct", 0),
                "top3_concentration": top3_pct,
                "type_distribution": divers.get("type_distribution", {}),
                "concentration_level": conc_level,
                "concentration_assessment": conc_assess,
            }
    except Exception as e:
        logging.warning(f"Dashboard 持仓数据获取失败: {e}")

    # ── Section 3: 零钱 + 债券 ──
    cash_balance = 0
    cash_details = {}
    try:
        for uid in ["小鱼儿", "花无缺"]:
            bal = get_cash_balance(uid).get("balance", 0)
            cash_details[uid] = round(bal, 2)
            cash_balance += bal
    except Exception:
        pass

    bond_info = None
    try:
        from routers.bond import _fetch_bond_data
        raw_bond = _fetch_bond_data()
        if raw_bond and len(raw_bond) > 1:
            last = raw_bond[-1]
            # 计算趋势：找 7天前、30天前、90天前的数据点
            ref_dates = {}
            last_date_str = last.get("date", "")
            for d in raw_bond:
                ref_dates[d["date"]] = {"temp": d.get("degree"), "yield": d.get("yield")}

            def _lookup_bond(days_ago):
                """找距离指定天数最近的交易日数据。"""
                from datetime import datetime, timedelta
                target = datetime.strptime(last_date_str, "%Y-%m-%d") - timedelta(days=days_ago)
                for i in range(7):
                    look = target.strftime("%Y-%m-%d")
                    if look in ref_dates:
                        return ref_dates[look]
                    target -= timedelta(days=1)
                return None

            ref_7d = _lookup_bond(7)
            ref_30d = _lookup_bond(30)

            bond_info = {
                "temperature": last.get("degree"),
                "yield_val": float(last["yield"]) if last.get("yield") else None,
                "date": last.get("date", ""),
                "trend": {
                    "week_ago_temp": ref_7d["temp"] if ref_7d else None,
                    "week_ago_yield": float(ref_7d["yield"]) if ref_7d and ref_7d.get("yield") else None,
                    "month_ago_temp": ref_30d["temp"] if ref_30d else None,
                    "month_ago_yield": float(ref_30d["yield"]) if ref_30d and ref_30d.get("yield") else None,
                },
            }
    except Exception as e:
        logging.warning(f"Dashboard 债市数据获取失败: {e}")

    total_assets = (portfolio_health or {}).get("total_value", 0) + cash_balance
    cash_advice = _get_cash_advice(
        bond_info["temperature"] if bond_info else None, cash_balance,
        total_assets=total_assets, undervalued_indexes=undervalued,
    )

    # ── 数据新鲜度 ──
    freshness_info = {"stale_count": 0, "stale_indexes": []}
    try:
        all_freshness = list_index_freshness()
        stale = [f for f in all_freshness if f.get("stale_days", 0) >= 10]
        freshness_info = {
            "stale_count": len(stale),
            "stale_indexes": [
                {"name": f["index_name"], "code": f["index_code"],
                 "latest_date": f["latest_date"], "stale_days": int(f["stale_days"])}
                for f in stale[:8]
            ],
        }
    except Exception as e:
        logging.warning(f"Dashboard 新鲜度获取失败: {e}")

    # 各模块数据实际更新时间
    undervalued_updated_at = ""
    portfolio_updated_at = ""
    try:
        conn = _get_conn()
        row = conn.execute("SELECT MAX(created_at) FROM index_valuations").fetchone()
        if row and row[0]:
            undervalued_updated_at = str(row[0])[:16]
        row = conn.execute("SELECT MAX(updated_at) FROM portfolio_holdings WHERE shares > 0").fetchone()
        if row and row[0]:
            portfolio_updated_at = str(row[0])[:16]
        conn.close()
    except Exception:
        pass

    payload = {
        "date": today,
        "undervalued_indexes": undervalued,
        "undervalued_data_date": undervalued_data_date,
        "undervalued_updated_at": undervalued_updated_at,
        "portfolio_health": portfolio_health,
        "portfolio_updated_at": portfolio_updated_at,
        "cash_management": {
            "balance": round(cash_balance, 2),
            "cash_details": cash_details,
            "bond_market": bond_info,
            "suggestion": cash_advice,
        },
        "cash_updated_at": bond_info.get("date", "") if bond_info else "",
        "data_freshness": freshness_info,
    }
    try:
        from db import ensure_dashboard_decisions
        ensure_dashboard_decisions(payload)
    except Exception as e:
        logging.warning(f"生成今日行动失败: {e}")
    return payload


# ── 市场热点 API（带缓存，解析JSON结构化输出）────────────


@router.get("/api/dashboard/daily-report")
async def get_daily_report():
    """获取今日自动生成的日报。如果没有今日的，返回最近一条。"""
    import logging
    logging.warning("=== get_daily_report called (NEW VERSION) ===")
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    # 先查今天的
    row = conn.execute(
        "SELECT * FROM analysis_history WHERE agent_id = 1 AND date(created_at) = ? ORDER BY created_at DESC LIMIT 1",
        (today,)
    ).fetchone()
    # 没有则查最近一条
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

        # 盈米 MCP 数据（市场温度 + 行情解读）
        yingmi_context = ""
        try:
            from mcp.yingmi_client import get_yingmi_client
            ym = get_yingmi_client()
            quotations = ym.call_tool_text("GetLatestQuotations")
            if quotations:
                yingmi_context = f"【盈米市场温度计及行情解读】\n{quotations[:2000]}"
        except Exception as e:
            logging.warning(f"盈米 MCP 数据获取失败: {e}")

        # 市场全景（指数行情 + 板块涨跌 + 涨跌家数）
        market_context = "暂无行情数据"
        try:
            from market_data import get_market_overview
            overview = get_market_overview()
            market_lines = []
            if overview.get("indices"):
                market_lines.append("【主要指数】")
                for idx in overview["indices"]:
                    sign = "+" if idx["change_pct"] >= 0 else ""
                    market_lines.append(f"- {idx['name']}: {idx['price']}（{sign}{idx['change_pct']}%）成交{idx.get('volume_yi',0):.0f}亿")
            b = overview.get("breadth", {})
            if b.get("up"):
                market_lines.append(f"\n【涨跌统计】上涨{b['up']} / 下跌{b['down']} / 涨停{b.get('limit_up',0)} / 跌停{b.get('limit_down',0)} / 成交{b.get('total_volume_yi',0):.0f}亿")
            if overview.get("sectors_top"):
                market_lines.append("\n【领涨板块】")
                for s in overview["sectors_top"]:
                    market_lines.append(f"- {s['name']}: +{s['change_pct']}%  领涨:{s['lead_stock']}{s['lead_change']}%")
            if overview.get("sectors_bottom"):
                market_lines.append("\n【领跌板块】")
                for s in overview["sectors_bottom"]:
                    market_lines.append(f"- {s['name']}: {s['change_pct']}%  领涨:{s['lead_stock']}{s['lead_change']}%")
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
            bond_text = f"债券温度{bond_raw.get('temperature','?')}°，收益率{bond_raw.get('rate','?')}%"
        except Exception:
            pass

        full_prompt = agent["system_prompt"] + f"""

【今日日期】
{time.strftime("%Y-%m-%d")}（{["周一","周二","周三","周四","周五","周六","周日"][time.localtime().tm_wday]}）

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
        update_async_task(task_id, status="error", error_msg=str(e))


@router.get("/api/dashboard/hot-topics")
async def get_hot_topics():
    """获取今日市场热点（盈米MCP + 东方财富互补，300秒缓存）。"""
    import time
    from pathlib import Path
    from config import ROOT
    now = time.time()
    if _hot_topics_cache["data"] and now - _hot_topics_cache["ts"] < 300:
        return _hot_topics_cache["data"]

    # 进程重启后首次请求：尝试从持久化文件恢复今日数据
    persisted_file = ROOT / "data" / "hot_topics_cache.json"
    if not _hot_topics_cache["data"]:
        try:
            import os
            if persisted_file.exists() and (now - os.path.getmtime(persisted_file)) < 86400:
                import json as _json
                cached = _json.loads(persisted_file.read_text())
                if cached.get("news") and cached.get("fetched_at"):
                    _hot_topics_cache["data"] = cached
                    _hot_topics_cache["ts"] = now
                    return cached
        except Exception:
            pass

    news_items = []
    sources_used = []

    # ── 数据源 1: 盈米 MCP SearchFinancialNews ──
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        raw = mcp.call_tool("SearchFinancialNews", {"keyword": "A股", "pageSize": 6})
        if isinstance(raw, dict):
            for c in raw.get("content", []):
                if c.get("type") == "text":
                    parsed = json.loads(c["text"])
                    if parsed.get("success") and parsed.get("data", {}).get("items"):
                        for item in parsed["data"]["items"]:
                            news_items.append({
                                "title": item.get("title", ""),
                                "summary": item.get("summary", ""),
                                "source": item.get("sources", "盈米"),
                                "date": item.get("publishDate", ""),
                                "url": item.get("url", ""),
                            })
        if news_items:
            sources_used.append("yingmi")
    except Exception as e:
        logging.warning(f"盈米热点新闻获取失败: {e}")

    # ── 数据源 2: 东方财富 financialSearch 金融资讯 ──
    eastmoney_items = []
    try:
        from mcp.eastmoney_client import get_eastmoney_client
        client = get_eastmoney_client()
        raw_text = client.financial_search("今日A股市场热点新闻")
        if raw_text:
            import re
            # 东方财富返回的可能是 markdown/文本格式，提取标题和摘要
            # 尝试按行解析，每条新闻通常有标题和摘要
            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
            current_item = None
            for line in lines:
                # 跳过纯标记行
                if line.startswith('#') or line.startswith('---') or len(line) < 5:
                    continue
                # 识别标题行（数字开头、或带【】、或较短的加粗文本）
                is_title = (
                    re.match(r'^\d+[.、）)]\s*', line) or
                    re.match(r'^【.+】', line) or
                    (len(line) < 50 and '**' in line)
                )
                if is_title:
                    if current_item and current_item.get("title"):
                        eastmoney_items.append(current_item)
                    title = re.sub(r'^\d+[.、）)]\s*', '', line)
                    title = re.sub(r'\*\*', '', title)
                    current_item = {"title": title.strip(), "summary": "", "source": "东方财富", "date": "", "url": ""}
                elif current_item:
                    # 作为摘要追加
                    if current_item["summary"]:
                        current_item["summary"] += " " + line
                    else:
                        current_item["summary"] = line
                    # 限制摘要长度
                    if len(current_item["summary"]) > 300:
                        current_item["summary"] = current_item["summary"][:300]
            if current_item and current_item.get("title"):
                eastmoney_items.append(current_item)

            # 如果文本解析没提取到结构化新闻，整段作为一条
            if not eastmoney_items and len(raw_text) > 50:
                eastmoney_items.append({
                    "title": "东方财富市场资讯",
                    "summary": raw_text[:500],
                    "source": "东方财富",
                    "date": "",
                    "url": "",
                })
        if eastmoney_items:
            sources_used.append("eastmoney")
    except Exception as e:
        logging.warning(f"东方财富资讯获取失败: {e}")

    # ── 合并去重（按标题相似度） ──
    if eastmoney_items:
        existing_titles = {item["title"][:15] for item in news_items}
        for item in eastmoney_items:
            # 标题前 15 字符去重
            short_title = item["title"][:15]
            if short_title not in existing_titles:
                news_items.append(item)
                existing_titles.add(short_title)

    # ── 兜底: web_search ──
    if not news_items:
        try:
            from tools import execute_tool
            web_raw = execute_tool("web_search", {"query": "A股 今日热点 板块 基金", "max_results": 5})
            if web_raw:
                news_items.append({"title": "网络资讯", "summary": web_raw[:500], "source": "web_search", "date": "", "url": ""})
                sources_used.append("web_search")
        except Exception as e:
            logging.warning(f"热点 web_search 失败: {e}")

    from datetime import datetime
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    source_label = "+".join(sources_used) if sources_used else "none"
    result = {"news": news_items, "source": source_label, "fetched_at": fetched_at}
    _hot_topics_cache["data"] = result
    _hot_topics_cache["ts"] = now
    # 持久化完整数据，进程重启后可恢复（含 fetched_at）
    try:
        import json as _json
        persisted_file.write_text(_json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


# ── 热点 AI 分析（结构化推荐） ────────────────────────────────
# prompt 通过 analysis_agents 配置管理，见 db.py 中"热点分析专家"系统提示词


@router.post("/api/dashboard/hotspots-relate")
async def hotspots_relate_indexes():
    """热点→指数关联：关键词匹配 + LLM 兜底推理。"""
    from db import list_valuation_indexes, list_holdings

    news_data = await get_hot_topics()
    news_list = news_data.get("news", [])[:6]
    if not news_list:
        return {"items": []}

    indexes = list_valuation_indexes()
    holdings = list_holdings()

    # ── 关键词映射（快速匹配，覆盖常见场景）──
    sector_keywords = {
        "半导体": ["芯片", "半导体", "集成电路", "晶圆", "封测"],
        "人工智能": ["AI", "人工智能", "大模型", "算力", "智谱", "GPT", "机器人", "深度学习", "机器学习"],
        "新能源": ["新能源", "光伏", "风电", "储能", "锂电", "电池"],
        "消费": ["消费", "白酒", "食品", "啤酒", "餐饮", "零售", "家电"],
        "医药": ["医药", "医疗", "创新药", "疫苗", "CXO", "中药", "器械"],
        "金融": ["银行", "保险", "券商", "金融", "证券"],
        "地产": ["地产", "房地产", "楼市", "房价", "万科"],
        "军工": ["军工", "国防", "航天", "导弹", "航空"],
        "教育": ["教育", "高考", "培训", "考研", "留学"],
        "体育": ["体育", "世界杯", "奥运", "足球", "赛事", "NBA"],
        "传媒": ["传媒", "游戏", "影视", "短视频", "直播", "出版"],
        "汽车": ["汽车", "新能源车", "电动车", "自动驾驶", "造车"],
        "基建": ["基建", "铁路", "公路", "水利", "城投"],
        "科技": ["科技", "互联网", "云计算", "数据", "5G", "6G", "量子"],
        "农业": ["农业", "种业", "养殖", "猪肉", "粮食"],
        "环保": ["环保", "碳中和", "碳达峰", "绿色", "减排"],
        "有色": ["有色", "铜", "铝", "黄金", "稀土", "锂矿"],
        "化工": ["化工", "石化", "化学", "材料"],
    }

    # 构建已有行业列表（供 LLM 参考）
    known_sectors = list(sector_keywords.keys())

    def keyword_match(title, summary):
        """关键词快速匹配。"""
        text = f"{title} {summary}".lower()
        matched = []
        for sector, keywords in sector_keywords.items():
            for kw in keywords:
                if kw.lower() in text:
                    matched.append(sector)
                    break
        return matched

    async def llm_infer_sectors(title, summary):
        """LLM 推理未匹配的新闻。"""
        from llm_service import _call_llm, MODEL
        prompt = f"""分析以下财经新闻，判断涉及哪些行业/板块。

新闻标题：{title}
新闻摘要：{summary[:200]}

已知行业列表：{', '.join(known_sectors)}

请返回 JSON 格式：
{{"sectors": ["行业1", "行业2"], "reason": "简短说明"}}

要求：
1. sectors 从已知行业中选择，如果没有完全匹配的，可以新增合理的行业名
2. 最多返回 3 个最相关的行业
3. 只返回 JSON，不要其他文字"""

        try:
            response = await asyncio.to_thread(lambda: _call_llm(
                caller="hotspots_relate",
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            ))
            text = response.choices[0].message.content or ""
            # 提取 JSON
            import re, json as _json
            match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
            if match:
                parsed = _json.loads(match.group())
                return parsed.get("sectors", [])
        except Exception as e:
            logging.warning(f"[hotspots-relate] LLM 推理失败: {e}")
        return []

    def find_related_indexes(sectors):
        results = []
        for idx in indexes:
            name = (idx.get("index_name") or "").lower()
            for sector in sectors:
                if sector.lower() in name:
                    results.append({
                        "index_code": idx.get("index_code"),
                        "index_name": idx.get("index_name"),
                        "percentile": idx.get("percentile"),
                        "assessment": idx.get("assessment"),
                    })
                    break
        return results

    def find_related_holdings(sectors):
        results = []
        for h in holdings:
            name = (h.get("fund_name") or "").lower()
            for sector in sectors:
                if sector.lower() in name:
                    results.append({
                        "fund_code": h.get("fund_code"),
                        "fund_name": h.get("fund_name"),
                        "current_value": h.get("current_value"),
                    })
                    break
        return results

    # ── 处理每条新闻：先关键词，未匹配则 LLM ──
    llm_tasks = []
    llm_indices = []  # 需要 LLM 推理的新闻索引

    for i, n in enumerate(news_list):
        title = n.get("title", "")
        summary = n.get("summary", "")
        sectors = keyword_match(title, summary)
        if sectors:
            # 关键词命中，直接用
            llm_tasks.append(None)
        else:
            # 未命中，标记需要 LLM 推理
            llm_tasks.append((title, summary))
            llm_indices.append(i)

    # 并行调用 LLM（如果有需要推理的）
    llm_results = {}
    if llm_indices:
        tasks = [llm_infer_sectors(news_list[i]["title"], news_list[i].get("summary", "")) for i in llm_indices]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in zip(llm_indices, results):
            llm_results[idx] = result if isinstance(result, list) else []

    # ── 组装结果 ──
    items = []
    for i, n in enumerate(news_list):
        title = n.get("title", "")
        summary = n.get("summary", "")
        if llm_tasks[i] is None:
            sectors = keyword_match(title, summary)
            source = "keyword"
        else:
            sectors = llm_results.get(i, [])
            source = "llm"

        related_indexes = find_related_indexes(sectors) if sectors else []
        related_holdings = find_related_holdings(sectors) if sectors else []
        items.append({
            "title": title,
            "sectors": sectors,
            "related_indexes": related_indexes[:5],
            "related_holdings": related_holdings[:3],
            "match_source": source,
        })

    return {"items": items}


@router.post("/api/dashboard/hotspots-analysis")
async def trigger_hotspots_analysis():
    """触发结构化热点分析（异步）。立即返回 task_id，后台执行。"""
    task_id = create_async_task("hotspots_analysis", caller="hotspots_analysis")
    task = asyncio.create_task(_run_hotspots_analysis_async(task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_hotspots_analysis_async(task_id: int):
    """后台执行热点分析。"""
    try:
        result = await _do_hotspots_analysis()
        update_async_task(task_id, status="done", result=result)
    except Exception as e:
        logging.error(f"热点分析异步任务失败: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


async def _do_hotspots_analysis():
    """结构化热点分析 — LLM 输出 JSON 推荐。"""
    # 1. 收集今日数据
    news_data = await get_hot_topics()
    news_list = news_data.get("news", [])[:5]
    news_text = "\n".join(
        f"- {n.get('title','')}（{n.get('source','')}）"
        for n in news_list if n.get('title')
    ) if news_list else "暂无新闻"

    # 估值数据 + 可参考指数代码
    try:
        indexes = list_valuation_indexes()
        # 去重，按 index_code 分组，优先展示最新数据
        seen = {}
        for i in indexes:
            code = i.get("index_code", "")
            if code and code not in seen:
                seen[code] = i
        all_indexes = list(seen.values())
        # 可参考指数代码表（按当日涨跌幅排序，让LLM优先看到热门板块）
        sorted_by_chg = sorted(all_indexes, key=lambda x: x.get('change_pct') if x.get('change_pct') is not None else 0, reverse=True)
        code_ref_text = "\n".join(
            f"- {i['index_name']}（{i['index_code']}）: 当日涨跌={i.get('change_pct',0):+.2f}%, {i.get('metric_type','PE')}={i.get('current_value','?')}, "
            f"百分位={i.get('percentile',100):.0f}%"
            for i in sorted_by_chg
        ) if all_indexes else "暂无指数数据"

        # 估值分布概览（供参考，不单独强调低估）
        low_val = [i for i in all_indexes if i.get("percentile", 100) < 30]
        high_val = [i for i in all_indexes if i.get("percentile", 100) > 70]
        val_text = (
            f"低估(<30%): {len(low_val)}只, "
            f"高估(>70%): {len(high_val)}只, "
            f"共{len(all_indexes)}只跟踪指数"
        )
    except Exception as e:
        code_ref_text = "暂无"
        val_text = "暂无"

    policy_keywords = [
        "政策", "国务院", "发改委", "工信部", "财政部", "央行", "证监会",
        "刺激", "补贴", "规划", "十五五", "新质生产力", "人工智能", "半导体",
        "算力", "机器人", "低空经济", "新能源", "储能", "消费", "设备更新",
        "出海", "自主可控", "并购重组",
    ]
    policy_lines = []
    for n in news_list:
        text = f"{n.get('title','')} {n.get('summary','')}"
        if any(k in text for k in policy_keywords):
            policy_lines.append(
                f"- {n.get('title','')}（{n.get('source','')}）: {n.get('summary','')[:160]}"
            )
    policy_text = "\n".join(policy_lines) if policy_lines else "今日热点新闻中未提取到明确政策线索，需降低政策驱动权重。"

    # 持仓明细 + 概况
    try:
        holdings = list_holdings()
        div = get_portfolio_diversification()
        cash_balance = get_total_cash_balance()

        # 持仓明细文本
        if holdings:
            holding_lines = []
            for h in holdings[:15]:
                pct = h.get("profit_rate")
                pct_str = f"{pct:+.1%}" if pct is not None else "N/A"
                val = h.get("current_value", 0) or 0
                profit = h.get("profit_loss", 0) or 0
                holding_lines.append(
                    f"- {h['fund_name']}（{h.get('fund_code','')}）: "
                    f"市值{val:.0f}元, 收益率{pct_str}, 盈亏{profit:+.0f}元"
                )
            holding_text = "\n".join(holding_lines)
        else:
            holding_text = "暂无持仓"

        portfolio_text = (
            f"持仓{div.get('holding_count',0)}只基金，"
            f"总市值{div.get('total_value',0):.0f}元，"
            f"累计盈亏{div.get('total_profit',0):+.0f}元，"
            f"可用零钱{cash_balance:.0f}元"
        )
    except Exception:
        holding_text = "暂无"
        portfolio_text = "暂无"

    # 债券
    try:
        from tools import _get_bond_temperature
        bond_raw = json.loads(_get_bond_temperature())
        bond_text = f"债券温度{bond_raw.get('temperature','?')}°，收益率{bond_raw.get('rate','?')}%"
    except Exception:
        bond_text = "暂无"

    # 从 analysis_agents 加载热点分析 prompt，支持通过管理页面动态修改
    try:
        agent = get_analysis_agent(7)
        base_prompt = agent["system_prompt"] if agent else ""
    except Exception:
        base_prompt = ""
    if not base_prompt:
        base_prompt = "你是一位专业的A股市场分析专家。请基于以下市场数据分析今日投资机会，输出结构化JSON。\n\n## 输出格式\n返回严格JSON：{\"summary\":\"...\", \"recommendations\":[{\"direction\":\"up|down|watch\",\"index_name\":\"...\",\"index_code\":\"...\",\"reason\":\"...\",\"confidence\":\"high|medium|low\"}]}\n\n## 今日数据："

    prompt = base_prompt + f"""
【今日新闻】（重点关注，这是分析的核心线索）
{news_text}

【政策与未来方向线索】（必须用于机会筛选）
{policy_text}

【可参考指数代码及估值】
{code_ref_text}

【估值分布概览】
{val_text}

【持仓明细】
{holding_text}

【持仓概况】
{portfolio_text}

【债券市场】
{bond_text}

筛选要求：
1. 不要只按估值低排序。机会评分必须综合：政策/产业方向、新闻催化强度、未来 6-24 个月景气度、估值安全边际、与当前持仓/现金的适配度。
2. 对纯粹“低估但缺少催化或政策方向不清”的标的，优先给 watch，不要包装成强机会。
3. 对热门但估值过高、拥挤度高或只有短线消息的标的，必须说明风险，可给 down/watch。
4. reason 需写出政策或未来方向依据；如果没有依据，要明确写“缺少政策/产业趋势支撑”。
5. 返回 JSON 中每个 recommendation 尽量包含 opportunity_score(0-100)、policy_signal、future_direction、valuation_role、risk_note。

请严格按照JSON格式输出分析结果。"""

    uid = f"hotspots_{int(time.time())}"
    _track_agent(uid, "热点分析专家", "市场热点分析")
    try:
        response = await asyncio.wait_for(asyncio.to_thread(lambda: _call_llm(
            caller="hotspots_analysis",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=get_config_float('llm.temperature_default', 0.3),
            max_tokens=get_config_int('llm.max_tokens_report', 8192),
        )), timeout=120)
        content = response.choices[0].message.content or "{}"
        # 尝试提取 JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(content)
        # 确保字段完整
        recs = parsed.get("recommendations", [])
        # 回填估值百分位（LLM 输出不带此字段，从本地估值数据补充）
        index_lookup = {i["index_code"]: i for i in all_indexes} if all_indexes else {}
        for rec in recs:
            code = rec.get("index_code", "")
            if code and code in index_lookup:
                rec["percentile"] = index_lookup[code].get("percentile")
                rec["current_value"] = index_lookup[code].get("current_value")
                rec["metric_type"] = index_lookup[code].get("metric_type")
        # 保存到推荐验证库 + 缓存 + 分析历史
        if recs:
            try:
                from datetime import datetime
                analysis_id = datetime.now().strftime("hotspots_%Y%m%d_%H%M%S")
                # 获取每条推荐的当前指数点位作为验证基线
                baselines = []
                for rec in recs:
                    bl = get_index_current_price(rec.get("index_code", ""))
                    baselines.append(bl)
                rec_ids = save_recommendations(recs, analysis_id, baselines)
                # 将数据库 ID 回填到推荐数据中
                for i, rid in enumerate(rec_ids):
                    if i < len(recs):
                        recs[i]["id"] = rid
                # 记录分析历史（含使用的 prompt 版本）
                _conn = _get_conn()
                _conn.execute(
                    "INSERT INTO analysis_history (agent_id, agent_name, prompt_used, news_context, result, token_usage) VALUES (?, ?, ?, ?, ?, ?)",
                    (7, "热点分析专家", base_prompt[:500] if base_prompt else "", news_text[:500], content, 0)
                )
                _conn.commit()
                _conn.close()
            except Exception as e:
                logging.warning(f"保存推荐记录失败: {e}")
        result = {
            "analysis_date": time.strftime("%Y-%m-%d"),
            "summary": parsed.get("summary", ""),
            "recommendations": recs,
            "analysis_text": content,
        }
        if recs:
            try:
                save_analysis_cache("hotspots_latest", result)
            except Exception:
                pass
        return result
    except asyncio.TimeoutError:
        return {"summary": "分析超时，请重试", "recommendations": [], "analysis_text": ""}
    except Exception as e:
        logging.warning(f"热点结构化分析失败: {e}")
        return {"summary": f"分析失败: {str(e)}", "recommendations": [], "analysis_text": ""}
    finally:
        _untrack_agent(uid)


@router.get("/api/dashboard/hotspots-analysis/latest")
async def get_latest_hotspots_analysis():
    """返回最近一次缓存的热点分析结果（刷新页面后还原用）。"""
    cached = get_analysis_cache("hotspots_latest")
    if cached:
        today = time.strftime("%Y-%m-%d")
        if cached.get("analysis_date") != today:
            return {"summary": "", "recommendations": [], "analysis_text": "", "stale": True}
        # 补充 recommendations 中的 id 字段，供反馈使用
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT id, index_name FROM recommendations WHERE analysis_id LIKE 'hotspots_%' ORDER BY id DESC LIMIT 10"
            ).fetchall()
            conn.close()
            id_map = {r["index_name"]: r["id"] for r in rows}
            for rec in cached.get("recommendations", []):
                if rec.get("index_name") in id_map:
                    rec["id"] = id_map[rec["index_name"]]
        except Exception:
            pass
        # 补充估值百分位（缓存中可能没有）
        try:
            indexes = list_valuation_indexes()
            index_lookup = {}
            for i in indexes:
                code = i.get("index_code", "")
                if code and code not in index_lookup:
                    index_lookup[code] = i
            for rec in cached.get("recommendations", []):
                code = rec.get("index_code", "")
                if code and code in index_lookup and rec.get("percentile") is None:
                    rec["percentile"] = index_lookup[code].get("percentile")
                    rec["current_value"] = index_lookup[code].get("current_value")
                    rec["metric_type"] = index_lookup[code].get("metric_type")
        except Exception:
            pass
        return cached
    # 没有缓存，尝试从历史推荐记录重建
    try:
        recs = list_recommendations(limit=10)
        if recs:
            return {
                "summary": f"上次分析结果（共{len(recs)}条推荐）",
                "recommendations": recs,
                "analysis_text": "",
                "stale": True,
            }
    except Exception:
        pass
    return {"summary": "", "recommendations": [], "analysis_text": ""}


@router.get("/api/dashboard/recommendations")
async def list_recommendations_api(limit: int = 50, status: str = ""):
    """列出历史推荐记录。"""
    recs = list_recommendations(limit, status or None)
    return {"recommendations": recs}


@router.get("/api/dashboard/recommendations/auto-verify")
async def auto_verify_recommendations():
    """自动验证 pending 推荐：获取实时行情，与基线比较，更新状态。

    改进：
    - 仅验证到达 verify_after_date 的推荐（T+5 交易日）
    - watch 方向用沪深300 做基准对比
    - 涨跌幅 <2% 标记为 flat（无意义波动）
    """
    from datetime import date

    today = date.today().isoformat()
    conn = _get_conn()
    # 仅查找到期的 pending 推荐
    rows = conn.execute(
        "SELECT DISTINCT index_code FROM recommendations WHERE status = 'pending' "
        "AND baseline_value IS NOT NULL AND (verify_after_date IS NULL OR verify_after_date <= ?)",
        (today,),
    ).fetchall()

    # 检查是否有 watch 方向需要基准对比
    has_watch = conn.execute(
        "SELECT COUNT(*) FROM recommendations WHERE status = 'pending' AND direction = 'watch' "
        "AND baseline_value IS NOT NULL AND (verify_after_date IS NULL OR verify_after_date <= ?)",
        (today,),
    ).fetchone()[0]
    conn.close()

    if not rows:
        return {"ok": True, "verified": 0, "results": []}

    price_map = {}
    for row in rows:
        code = row["index_code"]
        bl = get_index_current_price(code)
        if bl.get("price") is not None:
            price_map[code] = bl["price"]

    if not price_map:
        return {"ok": True, "verified": 0, "results": []}

    # 获取沪深300基准涨跌幅（用于 watch 验证）
    benchmark_change = None
    if has_watch:
        try:
            hs300 = get_index_current_price("000300.SH")
            if hs300.get("price") and hs300.get("baseline"):
                benchmark_change = (hs300["price"] - hs300["baseline"]) / hs300["baseline"] * 100
        except Exception:
            pass

    results = auto_verify_pending_recommendations(
        price_map, today, benchmark_change_pct=benchmark_change, min_change_threshold=2.0
    )
    return {"ok": True, "verified": len(results), "results": results}


@router.get("/api/dashboard/recommendations/stats")
async def recommendations_stats_api():
    """推荐验证统计（含 watch 对比和平局）。"""
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    correct = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'correct'").fetchone()[0]
    wrong = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'wrong'").fetchone()[0]
    flat = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'flat'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM recommendations WHERE status = 'pending'").fetchone()[0]

    # 各方向统计
    watch_total = conn.execute("SELECT COUNT(*) FROM recommendations WHERE direction = 'watch'").fetchone()[0]
    watch_correct = conn.execute("SELECT COUNT(*) FROM recommendations WHERE direction = 'watch' AND status = 'correct'").fetchone()[0]
    watch_wrong = conn.execute("SELECT COUNT(*) FROM recommendations WHERE direction = 'watch' AND status = 'wrong'").fetchone()[0]

    # 待验证（未到期）
    today = __import__('datetime').date.today().isoformat()
    pending_not_due = conn.execute(
        "SELECT COUNT(*) FROM recommendations WHERE status = 'pending' AND verify_after_date > ?",
        (today,),
    ).fetchone()[0]
    conn.close()

    total_verified = correct + wrong
    accuracy = round(correct / total_verified * 100, 1) if total_verified > 0 else None
    return {
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "flat": flat,
        "pending": pending,
        "pending_not_due": pending_not_due,
        "verified": total_verified,
        "accuracy": accuracy,
        "watch_total": watch_total,
        "watch_correct": watch_correct,
        "watch_wrong": watch_wrong,
    }


# ── 推荐反馈 / 进化系统 API ──────────────────────────────


@router.post("/api/dashboard/recommendations/{rec_id}/feedback")
async def create_recommendation_feedback(rec_id: int, body: dict):
    """提交推荐反馈（点赞/点踩/评论）。"""
    fid = save_recommendation_feedback(
        recommendation_id=rec_id,
        rating=body.get("rating", "neutral"),
        tags=body.get("tags", ""),
        comment=body.get("comment", ""),
    )
    return {"ok": True, "id": fid}


@router.get("/api/dashboard/recommendations/feedback")
async def list_feedback_api():
    """列出所有推荐反馈。"""
    return {"feedback": list_recommendation_feedback()}


@router.get("/api/dashboard/recommendations/feedback-stats")
async def feedback_stats_api():
    """推荐反馈统计（点赞率等）。"""
    return get_recommendation_feedback_stats()


@router.post("/api/llm-feedback")
async def create_llm_feedback(body: dict):
    """提交 LLM 输出反馈（进化系统）。"""
    fid = save_llm_feedback(
        caller=body.get("caller", ""),
        input_summary=body.get("input_summary", ""),
        output_summary=body.get("output_summary", ""),
        rating=body.get("rating", "neutral"),
        tags=body.get("tags", ""),
        comment=body.get("comment", ""),
        reason_tag=body.get("reason_tag", ""),
        score_data_accuracy=body.get("score_data_accuracy"),
        score_logic=body.get("score_logic"),
        score_actionability=body.get("score_actionability"),
        target_type=body.get("target_type", ""),
        target_id=body.get("target_id"),
    )
    # 触发反馈学习
    try:
        from agent.feedback_learner import update_user_profile_from_feedback
        feedback_type = body.get("rating", "neutral")
        if feedback_type in ("helpful", "unhelpful"):
            update_user_profile_from_feedback("default", feedback_type, body.get("comment", ""), body.get("input_summary", ""))
    except Exception as e:
        logging.warning(f"反馈学习更新失败: {e}")
    return {"ok": True, "id": fid}


@router.get("/api/llm-feedback")
async def list_llm_feedback_api(caller: str = "", rating: str = ""):
    """列出 LLM 反馈。"""
    return {"feedback": list_llm_feedback(
        caller=caller or None,
        rating=rating or None,
    )}
