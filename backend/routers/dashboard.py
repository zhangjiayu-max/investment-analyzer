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
    get_config_int, get_config_float, get_config, get_config_list,
    create_async_task, update_async_task, get_async_task, get_latest_async_task,
)
from db._conn import _get_conn
from services.llm_service import _call_llm, MODEL
from services.market_data import get_index_current_price
from infra.state import track_agent as _track_agent, untrack_agent as _untrack_agent, hot_topics_cache as _hot_topics_cache

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
            logging.info("今日无估值数据，后台异步抓取中...")
            # 异步后台执行，不阻塞 dashboard 响应
            import asyncio as _aio
            async def _bg_fetch():
                try:
                    from app import fetch_recent_valuations
                    await _aio.wait_for(fetch_recent_valuations(), timeout=30)
                except _aio.TimeoutError:
                    logging.warning("估值抓取超时(30s)，跳过")
                except Exception as e:
                    logging.warning(f"后台估值抓取失败: {e}")
            _aio.create_task(_bg_fetch())
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
            try:
                p = float(p)
            except (TypeError, ValueError):
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
        for uid in get_config_list('portfolio.users', ['小鱼儿', '花无缺']):
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
