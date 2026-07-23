"""关注列表路由 — /api/watchlist/*

管理看好但未持有的基金，方便择机买入。"""

import logging
import time
from functools import lru_cache
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    add_to_watchlist, get_watchlist_item, get_watchlist_by_fund,
    list_watchlist, update_watchlist_item, remove_from_watchlist,
    batch_add_to_watchlist, refresh_watchlist_navs, get_watchlist_summary,
    lookup_fund_info, fetch_fund_nav,
    get_holding_by_fund,
)
from db.config import get_config, get_config_bool, get_config_float, get_config_int
from db.watchlist import update_entry_info, get_watchlist_with_exit_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["watchlist"])

# 巡检结果缓存（5分钟）
_patrol_cache = {}
_patrol_cache_time = 0


def save_watchlist_trigger_candidate(item: dict, user_id: str = "default") -> int:
    """把关注列表触发项保存为建议候选。"""
    from db.decisions import create_candidate_from_structured_recommendation

    fund_code = item.get("fund_code") or ""
    fund_name = item.get("fund_name") or fund_code or "关注标的"
    current_pct = item.get("current_percentile")
    target_pct = item.get("target_percentile")
    summary = item.get("reason") or f"{fund_name} 触发关注条件，可进入分批买入决策"
    return create_candidate_from_structured_recommendation({
        "source_type": "watchlist",
        "source_id": item.get("id"),
        "scenario_type": "watchlist_trigger",
        "action_type": "add",
        "target_type": "fund",
        "target_code": fund_code,
        "target_name": fund_name,
        "summary": summary,
        "reason": item.get("notes") or summary,
        "confidence": "medium",
        "evidence": {
            "current_percentile": current_pct,
            "target_percentile": target_pct,
            "source": item.get("source") or "watchlist",
        },
        "risk": {"notes": ["关注条件触发不等于必须买入，仍需确认资金和仓位"]},
        "source_snapshot": item,
        "dedupe_key": f"watchlist_trigger:{fund_code}",
        "priority": item.get("priority", 0) or 5,
    }, user_id=user_id)


class AddWatchlistRequest(BaseModel):
    fund_code: str
    fund_name: str = ""
    fund_category: str = None
    index_code: str = None
    index_name: str = None
    target_price: float | None = None
    target_percentile: float | None = None
    notes: str = None
    priority: int = 0


class UpdateWatchlistRequest(BaseModel):
    fund_name: str = None
    fund_category: str = None
    index_code: str = None
    index_name: str = None
    target_price: float | None = None
    target_percentile: float | None = None
    notes: str = None
    priority: int = None
    status: str = None


class BatchAddRequest(BaseModel):
    items: list[dict]


@router.get("/api/watchlist")
async def list_watchlist_api(status: str = None, category: str = None):
    """获取关注列表。可选 ?status=watching & ?category=index 筛选。"""
    return {"items": list_watchlist(status=status, category=category)}


@router.get("/api/watchlist/summary")
async def watchlist_summary_api():
    """获取关注列表统计。"""
    return get_watchlist_summary()


@router.get("/api/watchlist/patrol")
async def watchlist_patrol_api():
    """关注列表巡检 — 刷新净值/估值，计算上车价位与信号灯。

    2026-07-14 增强：
    - 巡检时强制刷新净值（fetch_fund_nav）
    - 估值查询增加 akshare 兜底（本地 DB + 天天基金 + akshare 三级）
    - 自动推算建议上车价（suggested_buy_price）
    - 响应 all_items 增加 suggested_buy_price/buy_price_source/distance_to_buy
    """
    global _patrol_cache, _patrol_cache_time

    # 5分钟缓存
    now = time.time()
    if now - _patrol_cache_time < 5 * 60 and _patrol_cache:
        logger.info("[watchlist] 使用巡检缓存")
        return _patrol_cache

    items = list_watchlist(status="watching")
    if not items:
        return {"alerts": [], "checked": 0, "message": "关注列表为空"}

    from db.valuations import search_indexes_by_keyword, get_latest_valuation

    alerts = []
    all_items = []
    checked = 0

    for item in items:
        fund_code = item["fund_code"]
        fund_name = item.get("fund_name", fund_code)
        index_name = item.get("index_name", "")
        target_pct = item.get("target_percentile")

        # ── A1. 强制刷新净值 ──
        current_nav = item.get("current_nav")
        try:
            nav_data = fetch_fund_nav(fund_code)
            if nav_data and nav_data.get("nav"):
                current_nav = nav_data["nav"]
                from datetime import datetime
                update_watchlist_item(
                    item["id"],
                    current_nav=current_nav,
                    nav_updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
        except Exception as e:
            logger.debug(f"[watchlist] 刷新净值失败 {fund_code}: {e}")

        # ── A2. 估值查询（三级兜底）──
        current_pct = None
        pe = None
        pb = None
        source = ""

        # 1) 本地 DB
        if index_name and current_pct is None:
            try:
                search_term = index_name.replace("指数", "").replace("中证", "").replace("全指", "").replace("上证", "")
                matches = search_indexes_by_keyword(search_term)
                for m in matches:
                    val = get_latest_valuation(m["index_code"])
                    if val and val.get("percentile") is not None:
                        current_pct = val["percentile"]
                        pe = val.get("current_value")
                        source = "本地"
                        break
            except Exception:
                pass

        # 2) 天天基金兜底
        if current_pct is None and index_name:
            try:
                from mcp.ttfund_client import get_ttfund_client
                client = get_ttfund_client()
                raw = client._invoke("fund_index", {"index_id": index_name, "query_scope": "valuation"})
                if isinstance(raw, dict) and raw.get("success"):
                    v = raw.get("data", {}).get("valuation", {})
                    current_pct = v.get("pe_percentile_10y")
                    pe = v.get("pe_ttm")
                    pb = v.get("pb")
                    source = "天天基金"
            except Exception:
                pass

        # 3) akshare 兜底（按指数名反查代码再查估值）
        if current_pct is None and index_name:
            ak_result = _fetch_valuation_via_akshare(index_name)
            if ak_result:
                current_pct = ak_result.get("percentile")
                pe = ak_result.get("pe")
                pb = ak_result.get("pb")
                source = "akshare"

        # ── A2.5 混合基金机会值替代分析（无跟踪指数或估值查询失败）──
        # 混合基金无 index_name → 三级估值查询全跳过 → 启用回撤分位/净值分位替代
        drawdown_percentile = None
        nav_percentile = None
        analysis_method = "index_valuation" if current_pct is not None else "none"
        if current_pct is None:
            mixed = _analyze_mixed_fund_opportunity(fund_code)
            analysis_method = mixed["analysis_method"]
            drawdown_percentile = mixed["drawdown_percentile"]
            nav_percentile = mixed["nav_percentile"]
            try:
                update_watchlist_item(
                    item["id"],
                    analysis_method=analysis_method,
                    drawdown_percentile=drawdown_percentile,
                    nav_percentile=nav_percentile,
                )
            except Exception:
                pass

        checked += 1

        # 更新 watchlist 的当前百分位
        if current_pct is not None:
            try:
                update_watchlist_item(item["id"], current_percentile=current_pct)
            except Exception:
                pass

        # ── A3. 自动推算上车价 ──
        suggested_buy_price, buy_price_source = _calculate_suggested_buy_price(
            current_pct=current_pct,
            current_nav=current_nav,
            user_target_price=item.get("target_price"),
            drawdown_percentile=drawdown_percentile,
            nav_percentile=nav_percentile,
        )
        if suggested_buy_price is not None:
            try:
                update_watchlist_item(
                    item["id"],
                    suggested_buy_price=suggested_buy_price,
                    buy_price_source=buy_price_source,
                )
            except Exception:
                pass

        # 距上车价差距（正数=高于上车价，负数=已跌破）
        distance_to_buy = None
        if suggested_buy_price and current_nav and suggested_buy_price > 0:
            distance_to_buy = round((current_nav - suggested_buy_price) / suggested_buy_price * 100, 2)

        # 判断是否触发提醒
        is_alert = False
        alert_reason = ""
        if current_pct is not None:
            if target_pct is not None and current_pct <= target_pct:
                is_alert = True
                alert_reason = f"当前百分位 {current_pct:.0f}% 已低于目标 {target_pct:.0f}%"
            elif target_pct is None and current_pct <= 20:
                is_alert = True
                alert_reason = f"当前百分位 {current_pct:.0f}% 处于低估区域（≤20%）"
        elif drawdown_percentile is not None and drawdown_percentile >= 80:
            is_alert = True
            alert_reason = f"回撤分位 {drawdown_percentile:.0f}% 处于历史深度回撤区（≥80%）"
        elif nav_percentile is not None and nav_percentile <= 20:
            is_alert = True
            alert_reason = f"净值历史分位 {nav_percentile:.0f}% 处于低位（≤20%）"

        # ── P2-A（2026-07-21）自适应阈值：命中率反哺 target_percentile ──
        adaptive_target = target_pct
        adaptive_reason_text = ""
        if target_pct is not None:
            try:
                from services.advisor.watchlist_adaptive import apply_adaptive_threshold
                adaptive_target, adaptive_reason_text = apply_adaptive_threshold(fund_code, target_pct)
                if adaptive_target != target_pct:
                    try:
                        update_watchlist_item(
                            item["id"],
                            adaptive_target_pct=adaptive_target,
                            adaptive_reason=adaptive_reason_text,
                        )
                    except Exception:
                        pass
            except Exception as _e:
                logger.debug(f"[watchlist] 自适应阈值失败 {fund_code}: {_e}")

        # 信号灯状态（green/yellow/red/gray）— P2-A：使用 adaptive_target（若有调整）
        signal_status, signal_reason, distance = _compute_signal_status(
            current_pct, adaptive_target,
            drawdown_percentile=drawdown_percentile,
            nav_percentile=nav_percentile,
        )

        # F-3（2026-07-23）：估值高位否决 — 与机会雷达 P0-A「估值>80% 强制 avoid」对齐
        # 防止 adaptive_target 被调高后，current_pct 95% 仍判 green 的误触发
        try:
            veto_enabled = get_config_bool("watchlist.high_valuation_veto_enabled", True)
        except Exception:
            veto_enabled = True
        if veto_enabled:
            _veto_pct = current_pct if current_pct is not None else nav_percentile
            if _veto_pct is not None and _veto_pct > 80:
                signal_status = "red"
                signal_reason = f"估值高位否决（当前{_veto_pct:.0f}% > 80%阈值，禁止 green 信号）"

        # ── P0-1（2026-07-21）多维信号接入 + 信号置信度 ──
        tech_signal = "neutral"
        capital_signal = "neutral"
        sentiment_signal = "neutral"
        signal_confidence = None
        signal_reasons = []
        if adaptive_reason_text:
            signal_reasons.append(adaptive_reason_text)
        try:
            multidim_enabled = get_config_bool("watchlist.multidim_signal_enabled", True)
        except Exception:
            multidim_enabled = True
        if multidim_enabled and signal_status in ("green", "yellow"):
            try:
                timeout_sec = 5
                try:
                    timeout_sec = int(get_config("watchlist.multidim_signal_timeout_seconds", "5"))
                except Exception:
                    pass
                from services.advisor.watchlist_multidim import (
                    _compute_multidim_signals, adjust_signal_by_multidim, compute_signal_confidence
                )
                multidim = _compute_multidim_signals(
                    index_name=index_name,
                    index_code=item.get("index_code"),
                    timeout_seconds=timeout_sec,
                )
                tech_signal = multidim.get("tech_signal", "neutral")
                capital_signal = multidim.get("capital_signal", "neutral")
                sentiment_signal = multidim.get("sentiment_signal", "neutral")
                # 根据多维信号调整信号灯
                signal_status, signal_reason, signal_reasons = adjust_signal_by_multidim(
                    signal_status, signal_reason, multidim,
                )
                # 计算置信度（P1-B：传入 fund_code 用于历史命中率反哺）
                signal_confidence = compute_signal_confidence(signal_status, multidim, fund_code=fund_code)
            except Exception as _e:
                logger.debug(f"[watchlist] 多维信号获取失败 {fund_code}: {_e}")
                # 降级：仅按估值偏离度计算
                try:
                    from services.advisor.watchlist_multidim import compute_signal_confidence
                    signal_confidence = compute_signal_confidence(signal_status, None, fund_code=fund_code)
                except Exception:
                    signal_confidence = None

        # ── P1-D（2026-07-21）信号有效期：green 信号 5/10 天自动降级 ──
        try:
            expiry_enabled = get_config_bool("watchlist.signal_expiry_enabled", True)
        except Exception:
            expiry_enabled = True
        if expiry_enabled and signal_status == "green" and item.get("signal_triggered_at"):
            try:
                triggered_at = datetime.strptime(item["signal_triggered_at"], "%Y-%m-%d %H:%M:%S")
                days_since = (datetime.now() - triggered_at).days
                # 规则 1:5 天前触发 + 估值已涨 20% → 降 yellow
                if days_since >= 5:
                    # 用 entry_percentile（首次触发时记录的）与当前对比
                    entry_pct_at_trigger = item.get("entry_percentile")
                    if entry_pct_at_trigger is not None and current_pct is not None:
                        if current_pct - entry_pct_at_trigger >= 20:
                            signal_status = "yellow"
                            signal_reason = f"{signal_reason}（信号超 5 天且估值已涨 20%，自动降级）"
                # 规则 2:10 天前触发 + 多维转 bear → 降 yellow
                if days_since >= 10 and tech_signal == "bear" and signal_status == "green":
                    signal_status = "yellow"
                    signal_reason = f"{signal_reason}（信号超 10 天且技术面转空，自动降级）"
            except Exception as _e:
                logger.debug(f"[watchlist] 信号有效期检查失败 {fund_code}: {_e}")

        # ── P0-3（2026-07-21）信号回测闭环：signal_status 从非 green 变 green 时插入回测记录 ──
        try:
            last_status = item.get("last_signal_status")
            today_str = datetime.now().strftime("%Y-%m-%d")
            if signal_status == "green" and last_status != "green":
                # 同日去重
                from db.watchlist import has_signal_backtest_on_date, create_signal_backtest
                if not has_signal_backtest_on_date(item["id"], today_str):
                    # P1-B：用 akshare 交易日历精确计算 15 交易日（失败兜底 +21 自然日）
                    try:
                        from services.advisor.watchlist_backtest import _calc_review_date
                        review_date_str = _calc_review_date(today_str)
                    except Exception:
                        review_dt = datetime.now() + timedelta(days=21)
                        review_date_str = review_dt.strftime("%Y-%m-%d")
                    # 序列化多维信号快照
                    multidim_snapshot = None
                    if multidim_enabled and (tech_signal != "neutral" or capital_signal != "neutral" or sentiment_signal != "neutral"):
                        import json as _json
                        multidim_snapshot = _json.dumps({
                            "tech": tech_signal, "capital": capital_signal,
                            "sentiment": sentiment_signal,
                            "confidence": signal_confidence,
                        }, ensure_ascii=False)
                    create_signal_backtest({
                        "watchlist_id": item["id"],
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "signal_date": today_str,
                        "signal_status": "green",
                        "entry_nav": current_nav,
                        "entry_percentile": current_pct if current_pct is not None else (drawdown_percentile if drawdown_percentile is not None else nav_percentile),
                        "review_date": review_date_str,
                        "signal_confidence": signal_confidence,
                        "multidim_snapshot": multidim_snapshot,
                    })
            # 更新 last_signal_status（无论是否插入回测）+ 多维信号字段
            update_fields = {"last_signal_status": signal_status}
            if signal_confidence is not None:
                update_fields["signal_confidence"] = signal_confidence
            if tech_signal != "neutral":
                update_fields["tech_signal"] = tech_signal
            if capital_signal != "neutral":
                update_fields["capital_signal"] = capital_signal
            if sentiment_signal != "neutral":
                update_fields["sentiment_signal"] = sentiment_signal
            # P1-D：green 首次触发时写入 signal_triggered_at；非 green 清空
            if signal_status == "green" and last_status != "green":
                update_fields["signal_triggered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elif signal_status != "green" and item.get("signal_triggered_at"):
                update_fields["signal_triggered_at"] = None
            update_watchlist_item(item["id"], **update_fields)
        except Exception as _e:
            logger.debug(f"[watchlist] 信号回测记录插入失败 {fund_code}: {_e}")

        # ── P2-D（2026-07-21）信号变更主动通知：alert + SSE 推送 ──
        try:
            original_last_status = item.get("last_signal_status")
            if original_last_status and original_last_status != signal_status:
                try:
                    sse_enabled = get_config_bool("watchlist.signal_change_sse_enabled", True)
                    alert_enabled = get_config_bool("watchlist.signal_change_alert_enabled", True)
                except Exception:
                    sse_enabled = True
                    alert_enabled = True

                # severity 映射
                _to = signal_status
                _from = original_last_status
                if _to == "green":
                    change_severity = "info"
                    change_emoji = "🟢"
                    change_label = "机会出现"
                elif _to == "red":
                    change_severity = "warning"
                    change_emoji = "🔴"
                    change_label = "机会消失"
                elif _to == "yellow":
                    if _from == "green":
                        change_severity = "warning"
                        change_emoji = "🟡"
                        change_label = "机会减弱"
                    else:
                        change_severity = "info"
                        change_emoji = "🟡"
                        change_label = "接近上车"
                else:  # gray
                    change_severity = "info"
                    change_emoji = "⚪"
                    change_label = "数据缺失"

                change_title = f"{change_emoji} [信号变更] {fund_name} {_from}→{_to}"
                change_content = (
                    f"{fund_name}({fund_code}) 信号灯由 {_from} 变为 {_to}：{change_label}。"
                    f"{'原因：' + signal_reason if signal_reason else ''}"
                )

                # 1) 写入 portfolio_alerts
                if alert_enabled:
                    try:
                        from db.portfolio import create_alert
                        create_alert(
                            alert_type="watchlist_signal_change",
                            title=change_title,
                            content=change_content,
                            severity=change_severity,
                            related_fund_code=fund_code,
                            related_fund_name=fund_name,
                            source="watchlist_patrol",
                        )
                    except Exception as _ae:
                        logger.debug(f"[watchlist] 信号变更 alert 写入失败 {fund_code}: {_ae}")

                # 2) SSE 实时推送
                if sse_enabled:
                    try:
                        from routers.conversation.notifications import notify_subscribers
                        import asyncio as _asyncio
                        sse_payload = {
                            "title": change_title,
                            "message": change_content,
                            "type": change_severity,
                            "category": "watchlist_signal_change",
                            "data": {
                                "fund_code": fund_code,
                                "fund_name": fund_name,
                                "from_status": _from,
                                "to_status": _to,
                                "signal_reason": signal_reason,
                                "signal_confidence": signal_confidence,
                                "current_percentile": current_pct,
                                "target_percentile": target_pct,
                                "change_label": change_label,
                            },
                            "timestamp": _asyncio.get_event_loop().time(),
                        }
                        # patrol 是 async 函数，可直接 await
                        await notify_subscribers(sse_payload)
                    except Exception as _se:
                        logger.debug(f"[watchlist] 信号变更 SSE 推送失败 {fund_code}: {_se}")
        except Exception as _e:
            logger.debug(f"[watchlist] 信号变更检测失败 {fund_code}: {_e}")

        all_items.append({
            "id": item["id"],
            "fund_code": fund_code,
            "fund_name": fund_name,
            "index_name": index_name,
            "current_percentile": current_pct,
            "target_percentile": target_pct,
            "current_nav": current_nav,
            "target_price": item.get("target_price"),
            "priority": item.get("priority", 0),
            "signal_status": signal_status,
            "signal_reason": signal_reason,
            "distance_to_target": distance,
            "pe": pe,
            "pb": pb,
            "source": source,
            "suggested_buy_price": suggested_buy_price,
            "buy_price_source": buy_price_source,
            "distance_to_buy": distance_to_buy,
            "analysis_method": analysis_method,
            "drawdown_percentile": drawdown_percentile,
            "nav_percentile": nav_percentile,
            "nav_updated_at": item.get("nav_updated_at"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "notes": item.get("notes", ""),
            "status": item.get("status"),
            # P0-1（2026-07-21）多维信号 + 置信度
            "signal_confidence": signal_confidence,
            "tech_signal": tech_signal,
            "capital_signal": capital_signal,
            "sentiment_signal": sentiment_signal,
            "signal_reasons": signal_reasons,
            # P1-D 信号有效期
            "signal_triggered_at": item.get("signal_triggered_at"),
        })

        # ── Batch1 增强点 1：退出信号计算（P0-2 2026-07-21 默认改为 true） ──
        # P1-C（2026-07-21）退出信号动态化：移动止盈 + 保本止损 + 时间止损
        exit_signal = "none"
        exit_signal_reason = ""
        pnl_pct = None
        holding_days = None
        try:
            exit_enabled = get_config_bool("watchlist.exit_signal_enabled", True)
        except Exception:
            exit_enabled = True
        if exit_enabled:
            entry_price = item.get("entry_price")
            if entry_price and current_nav and entry_price > 0:
                pnl_pct = round((current_nav - entry_price) / entry_price * 100, 2)
                target_profit = item.get("target_profit_pct")
                stop_loss = item.get("stop_loss_pct")

                # 计算持有天数（P1-C 时间止损需要）
                entry_date = item.get("entry_date")
                if entry_date:
                    try:
                        holding_days = (datetime.now() - datetime.strptime(entry_date, "%Y-%m-%d")).days
                    except Exception:
                        holding_days = None

                # P1-C 移动止盈：更新 high_water_mark
                try:
                    moving_enabled = get_config_bool("watchlist.moving_profit_target_enabled", True)
                except Exception:
                    moving_enabled = True
                hwm = item.get("high_water_mark")
                if moving_enabled and current_nav:
                    if not hwm or current_nav > hwm:
                        try:
                            update_watchlist_item(item["id"], high_water_mark=current_nav)
                            hwm = current_nav
                        except Exception:
                            pass

                # 退出信号优先级：原 profit_target/stop_loss > P1-C 三类动态退出
                if target_profit and pnl_pct >= target_profit:
                    exit_signal = "profit_target"
                    exit_signal_reason = f"已涨 {pnl_pct:.1f}%，达到止盈目标 {target_profit}%"
                elif stop_loss and pnl_pct <= -stop_loss:
                    exit_signal = "stop_loss"
                    exit_signal_reason = f"已跌 {abs(pnl_pct):.1f}%，触发止损 -{stop_loss}%"
                else:
                    # P1-C 移动止盈：pnl>=20% 后从最高点回撤 5%
                    if moving_enabled and pnl_pct >= 20 and hwm and hwm > 0:
                        drawdown_from_peak = (current_nav - hwm) / hwm * 100
                        if drawdown_from_peak <= -5:
                            exit_signal = "moving_profit_target"
                            exit_signal_reason = f"已从最高点 {hwm:.3f} 回撤 {abs(drawdown_from_peak):.1f}%，移动止盈"

                    # P1-C 保本止损：pnl>=10% 后回落至 5% 以下
                    if exit_signal == "none":
                        try:
                            breakeven_enabled = get_config_bool("watchlist.breakeven_stop_loss_enabled", True)
                        except Exception:
                            breakeven_enabled = True
                        if breakeven_enabled and pnl_pct >= 10 and pnl_pct <= 5:
                            exit_signal = "breakeven_stop_loss"
                            exit_signal_reason = f"已涨超 10% 后回落至 {pnl_pct:.1f}%，触发保本止损(>5%)"

                    # P1-C 时间止损：持有 30 天 + pnl<3%
                    if exit_signal == "none":
                        try:
                            time_stop_enabled = get_config_bool("watchlist.time_stop_loss_enabled", True)
                        except Exception:
                            time_stop_enabled = True
                        if time_stop_enabled and holding_days is not None and holding_days >= 30 and pnl_pct < 3:
                            exit_signal = "time_stop_loss"
                            exit_signal_reason = f"持有 {holding_days} 天涨幅仅 {pnl_pct:.1f}%，时间止损提示减仓"

                # 写回 watchlist 表
                try:
                    update_watchlist_item(item["id"],
                                          exit_signal=exit_signal,
                                          exit_signal_reason=exit_signal_reason)
                except Exception as _e:
                    logger.debug(f"[watchlist] 退出信号写回失败 {fund_code}: {_e}")
        all_items[-1]["entry_price"] = item.get("entry_price")
        all_items[-1]["entry_date"] = item.get("entry_date")
        all_items[-1]["target_profit_pct"] = item.get("target_profit_pct")
        all_items[-1]["stop_loss_pct"] = item.get("stop_loss_pct")
        all_items[-1]["pnl_pct"] = pnl_pct
        all_items[-1]["exit_signal"] = exit_signal
        all_items[-1]["exit_signal_reason"] = exit_signal_reason
        # P1-C 新增字段
        all_items[-1]["holding_days"] = holding_days
        all_items[-1]["high_water_mark"] = item.get("high_water_mark")

        # ── Batch1 增强点 2：异常波动预警（P0-2 2026-07-21 默认改为 true） ──
        vol_alert = "none"
        vol_reason = ""
        daily_pct = None
        weekly_pct = None
        try:
            vol_enabled = get_config_bool("watchlist.volatility_alert_enabled", True)
        except Exception:
            vol_enabled = True
        if vol_enabled and current_nav:
            vol_alert, vol_reason, daily_pct, weekly_pct = _calculate_volatility_alert(fund_code, current_nav)
            if vol_alert != "none":
                try:
                    update_watchlist_item(
                        item["id"],
                        volatility_alert=vol_alert,
                        volatility_alert_reason=vol_reason,
                        daily_change_pct=daily_pct,
                        weekly_change_pct=weekly_pct,
                        volatility_updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                except Exception as _e:
                    logger.debug(f"[watchlist] 波动预警写回失败 {fund_code}: {_e}")
        all_items[-1]["volatility_alert"] = vol_alert
        all_items[-1]["volatility_alert_reason"] = vol_reason
        all_items[-1]["daily_change_pct"] = daily_pct
        all_items[-1]["weekly_change_pct"] = weekly_pct

        if is_alert:
            alert_item = {
                "id": item["id"],
                "fund_code": fund_code,
                "fund_name": fund_name,
                "index_name": index_name,
                "current_percentile": current_pct,
                "target_percentile": target_pct,
                "pe": pe,
                "pb": pb,
                "source": source,
                "reason": alert_reason,
                "notes": item.get("notes", ""),
                "analysis_method": analysis_method,
                "drawdown_percentile": drawdown_percentile,
                "nav_percentile": nav_percentile,
            }
            try:
                alert_item["candidate_id"] = save_watchlist_trigger_candidate(alert_item)
            except Exception as e:
                logger.warning(f"[watchlist] 保存建议候选失败: {e}")

            try:
                from db.portfolio import create_alert
                create_alert(
                    alert_type="watchlist_trigger",
                    title=f"关注基金买入信号: {fund_name}",
                    content=f"{fund_name}（{fund_code}）{alert_reason}，可考虑建仓。",
                    severity="info",
                    related_fund_code=fund_code,
                    related_fund_name=fund_name,
                    source="watchlist_patrol",
                )
            except Exception as e:
                logger.debug(f"[watchlist] 买入信号预警生成失败: {e}")

            alerts.append(alert_item)

    result = {
        "checked": checked,
        "alerts": alerts,
        "all_items": all_items,
        "message": f"巡检完成，{checked} 只基金中有 {len(alerts)} 只触发提醒",
    }

    _patrol_cache = result
    _patrol_cache_time = time.time()

    return result


def _fetch_valuation_via_akshare(index_name: str) -> dict | None:
    """akshare 兜底估值查询：按指数名反查代码，再查 PE 百分位。

    返回 {"percentile": 0.25, "pe": 12.3, "pb": 1.5} 或 None。
    """
    try:
        from services.market.market_data import get_index_valuation
        # 从 index_name 提取关键词反查代码
        from db._conn import _get_conn
        conn = _get_conn()
        search_term = index_name.replace("指数", "").replace("中证", "").replace("全指", "").replace("上证", "").strip()
        rows = conn.execute(
            "SELECT DISTINCT index_code FROM index_valuations WHERE index_name LIKE ? OR index_code LIKE ? LIMIT 1",
            (f"%{search_term}%", f"%{search_term}%"),
        ).fetchall()
        conn.close()
        if not rows:
            return None
        index_code = rows[0]["index_code"]
        # 去掉后缀（.CSI/.SH/.SZ）适配 akshare
        clean_code = index_code.split(".")[0]
        val = get_index_valuation(clean_code)
        if val and val.get("pe_percentile") is not None:
            return {
                "percentile": round(val["pe_percentile"] * 100, 1),
                "pe": val.get("pe"),
                "pb": val.get("pb"),
            }
    except Exception as e:
        logger.debug(f"[watchlist] akshare 估值兜底失败 {index_name}: {e}")
    return None


def _analyze_mixed_fund_opportunity(fund_code: str) -> dict:
    """混合基金（无跟踪指数）机会值替代分析。

    三级替代（巡检默认仅用本地数据，0 外部 MCP 调用）：
    1. 回撤分位分析（calculate_drawdown_analysis）— 数据足够则用此
    2. 净值历史分位（本地 fund_nav_history）— 回撤数据不足时兜底
    3. 盈米基金诊断（MCP）— 默认关闭，开关 watchlist.yingmi_diagnosis_enabled

    Returns:
        {
            "analysis_method": "drawdown" | "nav_percentile" | "yingmi" | "none",
            "drawdown_percentile": float | None,  # 0-100
            "nav_percentile": float | None,        # 0-100
            "current_drawdown": float | None,
            "detail": dict,
        }
    """
    # 第一级：回撤分位分析（本地，0 外部调用）
    try:
        from services.fund.fund_analysis import calculate_drawdown_analysis
        result = calculate_drawdown_analysis(fund_code)
        detail = result.get("detail", {}) or {}
        dd_pct = detail.get("drawdown_percentile")
        data_points = detail.get("data_points", 0)
        if dd_pct is not None and data_points >= 30:
            return {
                "analysis_method": "drawdown",
                "drawdown_percentile": round(dd_pct * 100, 1),
                "nav_percentile": None,
                "current_drawdown": detail.get("current_drawdown"),
                "detail": {
                    "max_drawdown": detail.get("max_drawdown"),
                    "avg_recovery_days": detail.get("avg_recovery_days"),
                    "is_bottoming": detail.get("is_bottoming"),
                    "data_points": data_points,
                },
            }
    except Exception as e:
        logger.debug(f"[watchlist] 回撤分析失败 {fund_code}: {e}")

    # 第二级：净值历史分位（本地 fund_nav_history）
    nav_pct = _calc_nav_percentile(fund_code)
    if nav_pct is not None:
        return {
            "analysis_method": "nav_percentile",
            "drawdown_percentile": None,
            "nav_percentile": nav_pct,
            "current_drawdown": None,
            "detail": {},
        }

    # 第三级：盈米基金诊断（MCP，默认关闭以控制成本）
    try:
        from db.config import get_config
        yingmi_enabled = str(get_config("watchlist.yingmi_diagnosis_enabled", "false")).lower()
        if yingmi_enabled == "true":
            from mcp.yingmi_client import get_yingmi_client
            client = get_yingmi_client()
            text = client.get_fund_diagnosis(fund_code)
            if text and len(text) > 50:
                return {
                    "analysis_method": "yingmi",
                    "drawdown_percentile": None,
                    "nav_percentile": None,
                    "current_drawdown": None,
                    "detail": {"diagnosis_text": text[:500]},
                }
    except Exception as e:
        logger.debug(f"[watchlist] 盈米诊断失败 {fund_code}: {e}")

    return {
        "analysis_method": "none",
        "drawdown_percentile": None,
        "nav_percentile": None,
        "current_drawdown": None,
        "detail": {},
    }


def _calc_nav_percentile(fund_code: str) -> float | None:
    """计算当前净值在历史净值中的分位（0-100）。

    0 = 历史最低（最便宜），100 = 历史最高。数据不足返回 None。
    """
    try:
        from services.fund.fund_data_service import get_or_refresh_fund_nav_history
        nav_history = get_or_refresh_fund_nav_history(fund_code, days=1000)
        if not nav_history or len(nav_history) < 30:
            return None
        navs = []
        for r in nav_history:
            try:
                n = float(r.get("nav", 0))
                if n > 0:
                    navs.append(n)
            except (TypeError, ValueError):
                continue
        if len(navs) < 30:
            return None
        current_nav = navs[-1]
        sorted_navs = sorted(navs)
        rank = sum(1 for n in sorted_navs if n <= current_nav) / len(sorted_navs)
        return round(rank * 100, 1)
    except Exception:
        return None


def _calculate_suggested_buy_price(current_pct: float | None, current_nav: float | None,
                                    user_target_price: float | None,
                                    drawdown_percentile: float | None = None,
                                    nav_percentile: float | None = None) -> tuple[float | None, str]:
    """自动推算建议上车价。

    规则（按估值分位分档）：
    - 分位 ≤20%（低估区）：上车价 = 当前净值 × 1.00
    - 分位 20-40%（偏低区）：上车价 = 当前净值 × 0.95
    - 分位 40-60%（中性区）：上车价 = 当前净值 × 0.90
    - 分位 >60%（偏高区）：上车价 = 当前净值 × 0.85
    - 分位缺失 + 回撤分位可用：按回撤分位分档（混合基金替代）
    - 分位缺失 + 净值分位可用：按净值分位分档（兜底）
    - 全部缺失：上车价 = None

    用户已设 target_price 时以用户设置为准。
    """
    # 用户已设目标价，优先使用
    if user_target_price and user_target_price > 0:
        return round(user_target_price, 4), "用户设置"

    if current_nav is None or current_nav <= 0:
        return None, ""

    # 估值分位可用
    if current_pct is not None:
        if current_pct <= 20:
            return round(current_nav, 4), "低估区·当前净值"
        elif current_pct <= 40:
            return round(current_nav * 0.95, 4), "偏低区·-5%"
        elif current_pct <= 60:
            return round(current_nav * 0.90, 4), "中性区·-10%"
        else:
            return round(current_nav * 0.85, 4), "偏高区·-15%"

    # 回撤分位替代（混合基金，回撤越深越接近机会）
    if drawdown_percentile is not None:
        if drawdown_percentile >= 80:
            return round(current_nav, 4), "历史大底区·当前净值"
        elif drawdown_percentile >= 60:
            return round(current_nav * 0.97, 4), "深度回撤区·-3%"
        elif drawdown_percentile >= 40:
            return round(current_nav * 0.93, 4), "中度回撤区·-7%"
        else:
            return round(current_nav * 0.90, 4), "等待更大回撤·-10%"

    # 净值历史分位替代（兜底，分位越低越便宜）
    if nav_percentile is not None:
        if nav_percentile <= 20:
            return round(current_nav, 4), "净值历史低位·当前净值"
        elif nav_percentile <= 40:
            return round(current_nav * 0.95, 4), "净值偏低区·-5%"
        elif nav_percentile <= 60:
            return round(current_nav * 0.90, 4), "净值中性区·-10%"
        else:
            return round(current_nav * 0.85, 4), "净值偏高区·-15%"

    return None, ""


# ── P0-2.2：信号灯状态计算（纯规则，0 LLM/0 MCP） ──

def _compute_signal_status(current_pct, target_pct, drawdown_percentile=None, nav_percentile=None):
    """根据当前估值百分位与目标百分位计算信号灯状态。

    返回 (signal_status, signal_reason, distance_to_target)：
    - green:  current ≤ target（或未设 target 且 ≤20）→ 可买入
    - yellow: abs(current - target) ≤ 5 → 接近目标，持续关注
    - red:    current > target + 5 → 估值仍高，继续等待
    - gray:   current_pct 为 None 且无替代分析 → 数据缺失，需巡检刷新

    混合基金替代：current_pct 为 None 时，依次用回撤分位/净值分位计算信号灯。
    """
    if current_pct is None:
        # 回撤分位替代（回撤越深越接近机会）
        if drawdown_percentile is not None:
            if drawdown_percentile >= 80:
                return "green", f"处于历史深度回撤区（回撤分位{drawdown_percentile:.0f}%），接近大底", None
            elif drawdown_percentile >= 60:
                return "yellow", f"回撤较深（分位{drawdown_percentile:.0f}%），接近机会区", None
            else:
                return "red", f"回撤不足（分位{drawdown_percentile:.0f}%），等待更深回调", None
        # 净值历史分位替代（分位越低越便宜）
        if nav_percentile is not None:
            if nav_percentile <= 20:
                return "green", f"净值处于历史低位（分位{nav_percentile:.0f}%），机会区", None
            elif nav_percentile <= 40:
                return "yellow", f"净值偏低（分位{nav_percentile:.0f}%），接近机会区", None
            else:
                return "red", f"净值未到低位（分位{nav_percentile:.0f}%），继续等待", None
        return "gray", "估值数据缺失，请巡检刷新", None
    distance = None
    if target_pct is not None:
        distance = round(current_pct - target_pct, 1)
        if current_pct <= target_pct:
            return "green", f"估值已进入目标区间（当前 {current_pct:.0f}% ≤ 目标 {target_pct:.0f}%）", distance
        elif abs(distance) <= 5:
            return "yellow", f"接近目标（当前 {current_pct:.0f}%，目标 {target_pct:.0f}%，差 {distance:+.1f}%）", distance
        else:
            return "red", f"估值仍高（当前 {current_pct:.0f}% > 目标 {target_pct:.0f}%，差 {distance:+.1f}%）", distance
    else:
        # 未设目标百分位
        if current_pct <= 20:
            return "green", f"处于低估区域（{current_pct:.0f}% ≤ 20%）", None
        elif current_pct <= 25:
            return "yellow", f"接近低估区域（{current_pct:.0f}%）", None
        else:
            return "red", f"估值未达低估（{current_pct:.0f}% > 20%）", None


# ── P2-4.1：买入时机综合评分（4维轻量版，纯本地数据，0 LLM/0 MCP/0 akshare） ──

@router.get("/api/watchlist/{item_id}/buy-score")
async def get_buy_score_api(item_id: int):
    """计算关注基金的买入时机综合评分（纯规则，无 LLM）。"""
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注基金不存在")
    score = _calculate_buy_score(item)
    return {
        "score": score["total"],
        "rating": "buy" if score["total"] >= 75 else ("watch" if score["total"] >= 50 else "wait"),
        "dimensions": score["dimensions"],
        "calculated_at": __import__("datetime").datetime.now().isoformat(),
    }


@router.get("/api/watchlist/signal-backtest-stats")
async def watchlist_signal_backtest_stats_api(fund_code: str = None):
    """获取关注列表信号回测命中率统计（P0-3 2026-07-21 新增）。

    Query 参数：
    - fund_code: 指定基金代码则返回该基金的统计，省略则返回全局统计

    Returns:
        {
            "total": int, "reviewed": int, "pending": int,
            "hit": int, "miss": int, "hit_rate": float | None
        }
    """
    from db.watchlist import get_signal_backtest_stats
    return get_signal_backtest_stats(fund_code=fund_code)


@router.post("/api/watchlist/review-backtests")
async def watchlist_review_backtests_api():
    """手动触发 watchlist 信号回测（管理员用，P0-3 2026-07-21 新增）。

    正常情况下由 _auto_watchlist_backtest 每日 09:35 自动执行；
    此接口用于调试或补回测。
    """
    from services.advisor.watchlist_backtest import review_watchlist_signal_backtests
    return review_watchlist_signal_backtests()


@router.get("/api/watchlist/{fund_code}/signal-history")
async def watchlist_signal_history_api(fund_code: str, limit: int = 20):
    """获取基金信号回测历史（P2-A 2026-07-21 新增，用于前端轨迹图）。

    Query 参数：
    - limit: 返回记录数上限，默认 20，范围 1-100

    Returns:
        {
            "fund_code": str,
            "history": [
                {
                    "signal_date": str, "signal_status": str,
                    "entry_nav": float, "entry_percentile": float,
                    "review_date": str, "review_nav": float | None,
                    "change_pct": float | None, "hit": int | None,
                    "signal_confidence": float | None,
                    "multidim_snapshot": str | None,
                    "reviewed_at": str | None
                },
                ...
            ],
            "count": int
        }
    """
    if limit < 1 or limit > 100:
        limit = 20
    from db.watchlist import get_fund_signal_backtest_history
    history = get_fund_signal_backtest_history(fund_code, limit=limit)
    return {"fund_code": fund_code, "history": history, "count": len(history)}


@router.get("/api/watchlist/resonance")
async def watchlist_resonance_api():
    """获取关注列表信号共振 + 持仓系统性风险分析（P2-C 2026-07-21 新增）。

    Returns:
        {
            "watchlist_resonance": {
                "green_count": int, "yellow_count": int, "red_count": int, "total": int,
                "green_ratio": float,
                "resonance_level": "strong" | "moderate" | "weak" | "none" | "strong_bearish",
                "resonance_type": "bullish" | "bearish" | "neutral",
                "alert_funds": [...], "suggestion": str
            },
            "holding_systemic_risk": {
                "systemic_risk": bool, "triggered_count": int, "total_holdings": int,
                "triggered_ratio": float, "triggered_funds": [...], "suggestion": str
            }
        }
    """
    from services.advisor.watchlist_resonance import (
        detect_watchlist_resonance, detect_holding_systemic_risk
    )
    wl_resonance = detect_watchlist_resonance()
    holding_risk = detect_holding_systemic_risk()

    # 强共振 / 系统性风险生成 alert
    try:
        from db.portfolio import create_alert
        if wl_resonance.get("resonance_level") == "strong":
            create_alert(
                alert_type="watchlist_resonance_bullish",
                title=f"关注列表强共振：{wl_resonance['green_count']} 只 green",
                content=wl_resonance.get("suggestion", ""),
                severity="info",
                source="watchlist_resonance",
            )
        if holding_risk.get("systemic_risk"):
            create_alert(
                alert_type="holding_systemic_risk",
                title=f"持仓系统性风险：{holding_risk['triggered_count']} 只触发退出",
                content=holding_risk.get("suggestion", ""),
                severity="danger",
                source="watchlist_resonance",
            )
    except Exception as _e:
        logger.debug(f"[watchlist] 共振 alert 写入失败: {_e}")

    return {"watchlist_resonance": wl_resonance, "holding_systemic_risk": holding_risk}


def _calculate_buy_score(item: dict) -> dict:
    """计算买入时机评分（纯本地数据，0 外部调用）。

    4 维度：估值百分位50% + 净值距目标价25% + 与已持仓相关性15% + 持仓集中度10%
    """
    dims = {}

    # 1. 估值百分位（50%）— 混合基金用回撤分位/净值分位替代
    pct = item.get("current_percentile")
    dd_pct = item.get("drawdown_percentile")
    nav_pct = item.get("nav_percentile")
    if pct is not None:
        v_score = max(0, 100 - max(0, pct - 20) * 1.2)
        dims["valuation"] = {"score": round(v_score), "weight": 0.5, "reason": f"PE百分位 {pct:.0f}%"}
    elif dd_pct is not None:
        # 回撤分位越高=回撤越深=越便宜，转换为等价估值分位: equiv = 100 - dd_pct
        equiv = 100 - dd_pct
        v_score = max(0, 100 - max(0, equiv - 20) * 1.2)
        dims["valuation"] = {"score": round(v_score), "weight": 0.5, "reason": f"回撤分位 {dd_pct:.0f}%（等价估值{equiv:.0f}%）"}
    elif nav_pct is not None:
        # 净值分位越低=越便宜，直接等价估值分位
        v_score = max(0, 100 - max(0, nav_pct - 20) * 1.2)
        dims["valuation"] = {"score": round(v_score), "weight": 0.5, "reason": f"净值分位 {nav_pct:.0f}%"}
    else:
        dims["valuation"] = {"score": 50, "weight": 0.5, "reason": "估值数据缺失"}

    # 2. 净值距目标价（25%）
    nav = item.get("current_nav")
    target = item.get("target_price")
    if not target or not nav or target <= 0:
        dims["price"] = {"score": 50, "weight": 0.25, "reason": "未设目标价"}
    else:
        distance_pct = (nav - target) / target * 100  # 正数=高于目标价
        p_score = max(0, 100 - max(0, distance_pct) * 5)
        dims["price"] = {"score": round(p_score), "weight": 0.25, "reason": f"距目标价 {distance_pct:+.1f}%"}

    # 3. 与已持仓相关性（15%）— 查本地相关性矩阵缓存
    try:
        from routers.analysis.correlation import _get_correlation_cache
        corr_cache = _get_correlation_cache()
        corr = corr_cache.get(item.get("fund_code", ""), {})
        avg_corr = sum(corr.values()) / len(corr) if corr else 0
        c_score = max(20, 100 - avg_corr * 100) if corr else 80
        dims["correlation"] = {"score": round(c_score), "weight": 0.15, "reason": f"平均相关性 {avg_corr:.2f}" if corr else "相关性数据缺失"}
    except Exception:
        dims["correlation"] = {"score": 80, "weight": 0.15, "reason": "相关性数据缺失"}

    # 4. 持仓集中度惩罚（10%）
    try:
        from db.portfolio import get_portfolio_summary
        h = get_holding_by_fund(item.get("fund_code", ""))
        if not h:
            dims["concentration"] = {"score": 100, "weight": 0.1, "reason": "未持有"}
        else:
            summary = get_portfolio_summary()
            total_value = max(summary.get("total_value", 1), 1)
            ratio = (h.get("current_value") or 0) / total_value
            conc_score = max(30, 100 - ratio * 200)  # 占比>35% → 30分
            dims["concentration"] = {"score": round(conc_score), "weight": 0.1, "reason": f"已持仓占比 {ratio:.0%}"}
    except Exception:
        dims["concentration"] = {"score": 80, "weight": 0.1, "reason": "持仓数据缺失"}

    total = sum(d["score"] * d["weight"] for d in dims.values())
    return {"total": round(total), "dimensions": dims}


@router.get("/api/watchlist/{item_id}")
async def get_watchlist_item_api(item_id: int):
    """获取单条关注记录。"""
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注记录不存在")
    return item


@router.post("/api/watchlist")
async def add_to_watchlist_api(req: AddWatchlistRequest):
    """添加基金到关注列表。自动查询基金信息补充名称。"""
    fund_code = req.fund_code.strip()

    # 自动查询基金信息
    fund_name = req.fund_name
    index_code = req.index_code
    index_name = req.index_name
    fund_category = req.fund_category

    if not fund_name:
        try:
            info = lookup_fund_info(fund_code)
            if info:
                fund_name = info.get("fund_name", fund_code)
                if not fund_category:
                    fund_category = info.get("fund_category")
                if not index_code:
                    tracking = info.get("tracking_index", "")
                    if tracking:
                        index_name = tracking
        except Exception as e:
            logger.warning(f"查询基金信息失败 {fund_code}: {e}")
            fund_name = fund_name or fund_code

    # 查重：已在持仓中时标记为补仓监控
    existing = get_holding_by_fund(fund_code)
    is_existing_holding = existing and (existing.get("shares") or 0) > 0

    # 已持仓的基金自动标记为补仓监控
    notes = req.notes
    if is_existing_holding and not notes:
        profit = existing.get("profit_rate", 0)
        notes = f"补仓监控（当前持仓盈亏 {profit:+.1%}）"

    try:
        item_id = add_to_watchlist(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_category=fund_category,
            index_code=index_code,
            index_name=index_name,
            target_price=req.target_price,
            target_percentile=req.target_percentile,
            notes=notes,
            priority=req.priority,
        )
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            raise HTTPException(400, f"{fund_name}({fund_code}) 已在关注列表中")
        raise HTTPException(500, f"添加失败: {e}")

    return {"ok": True, "id": item_id}


@router.post("/api/watchlist/batch")
async def batch_add_watchlist_api(req: BatchAddRequest):
    """批量添加基金到关注列表。"""
    result = batch_add_to_watchlist(req.items)
    return {"ok": True, **result}


@router.put("/api/watchlist/{item_id}")
async def update_watchlist_item_api(item_id: int, req: UpdateWatchlistRequest):
    """更新关注记录。"""
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "无更新字段")

    if not update_watchlist_item(item_id, **fields):
        raise HTTPException(404, "关注记录不存在")
    return {"ok": True}


@router.delete("/api/watchlist/{item_id}")
async def remove_from_watchlist_api(item_id: int):
    """从关注列表移除。"""
    if not remove_from_watchlist(item_id):
        raise HTTPException(404, "关注记录不存在")
    return {"ok": True}


@router.post("/api/watchlist/refresh-navs")
async def refresh_watchlist_navs_api():
    """批量刷新关注列表基金净值。"""
    results = refresh_watchlist_navs()
    return {"ok": True, "results": results}


@router.post("/api/watchlist/{item_id}/mark-bought")
async def mark_as_bought_api(item_id: int, payload: dict = None):
    """标记为已买入（从关注列表移到持仓）。

    Batch1 增强点 1：支持可选 payload 中的 entry_price/entry_date/target_profit_pct/stop_loss_pct，
    用于后续退出信号计算。
    """
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注记录不存在")

    payload = payload or {}
    entry_price = payload.get("entry_price")
    entry_date = payload.get("entry_date") or datetime.now().strftime("%Y-%m-%d")
    target_profit_pct = payload.get("target_profit_pct")
    stop_loss_pct = payload.get("stop_loss_pct")

    # 如果传了任意一个退出相关字段，走 update_entry_info 路径
    if any(v is not None for v in [entry_price, target_profit_pct, stop_loss_pct]):
        update_entry_info(
            item_id,
            entry_price=float(entry_price) if entry_price is not None else None,
            entry_date=entry_date,
            target_profit_pct=float(target_profit_pct) if target_profit_pct is not None else None,
            stop_loss_pct=float(stop_loss_pct) if stop_loss_pct is not None else None,
        )
        return {
            "ok": True,
            "message": f"{item.get('fund_name','')} 已标记为已买入，已记录上车信息",
            "entry_price": entry_price,
            "entry_date": entry_date,
            "target_profit_pct": target_profit_pct,
            "stop_loss_pct": stop_loss_pct,
        }
    else:
        update_watchlist_item(item_id, status="bought")
        return {"ok": True, "message": f"{item.get('fund_name','')} 已标记为已买入"}


# ── Batch1 增强点 1：退出机制 API ──────────────────────────────────

@router.post("/api/watchlist/{item_id}/entry")
async def update_entry_info_api(item_id: int, payload: dict):
    """用户上车后更新买入信息（买入价/日期/止盈/止损）。

    必填: entry_price
    可选: entry_date（默认今天）, target_profit_pct（默认 30）, stop_loss_pct（默认 10）
    """
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注记录不存在")

    entry_price = payload.get("entry_price")
    if entry_price is None:
        raise HTTPException(400, "缺少 entry_price 参数")
    try:
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        raise HTTPException(400, "entry_price 必须是数字")
    if entry_price <= 0:
        raise HTTPException(400, "entry_price 必须大于 0")

    entry_date = payload.get("entry_date") or datetime.now().strftime("%Y-%m-%d")
    target_profit_pct = payload.get("target_profit_pct")
    stop_loss_pct = payload.get("stop_loss_pct")
    # 未设止盈/止损时使用默认值
    if target_profit_pct is None:
        target_profit_pct = get_config_float("watchlist.default_target_profit_pct", 30.0)
    if stop_loss_pct is None:
        stop_loss_pct = get_config_float("watchlist.default_stop_loss_pct", 10.0)

    update_entry_info(
        item_id,
        entry_price=entry_price,
        entry_date=entry_date,
        target_profit_pct=float(target_profit_pct),
        stop_loss_pct=float(stop_loss_pct),
    )

    return {
        "ok": True,
        "message": f"{item.get('fund_name','')} 上车信息已记录",
        "entry_price": entry_price,
        "entry_date": entry_date,
        "target_profit_pct": float(target_profit_pct),
        "stop_loss_pct": float(stop_loss_pct),
    }


@router.get("/api/watchlist/{item_id}/exit-status")
async def get_exit_status_api(item_id: int):
    """查询退出信号状态（止盈/止损）。

    返回当前盈亏百分比和退出信号，前端用于展示止盈/止损徽章。
    """
    result = get_watchlist_with_exit_status(item_id)
    if not result:
        raise HTTPException(404, "关注记录不存在")
    return {
        "item_id": item_id,
        "fund_code": result.get("fund_code"),
        "fund_name": result.get("fund_name"),
        "entry_price": result.get("entry_price"),
        "current_nav": result.get("current_nav"),
        "pnl_pct": result.get("pnl_pct"),
        "target_profit_pct": result.get("target_profit_pct"),
        "stop_loss_pct": result.get("stop_loss_pct"),
        "exit_signal": result.get("exit_signal"),
        "exit_signal_reason": result.get("exit_signal_reason"),
    }


# ── Batch1 增强点 2：异常波动预警 ──────────────────────────────────

def _calculate_volatility_alert(fund_code: str, current_nav: float) -> tuple[str, str, float | None, float | None]:
    """计算异常波动预警。

    通过本地缓存查近 7 日净值历史，计算日/周涨跌幅，按阈值判定预警级别。

    Returns:
        (alert_level, reason, daily_pct, weekly_pct)
        alert_level: severe / warning / none
    """
    try:
        from services.fund.fund_data_service import get_fund_nav_history_from_cache
        history = get_fund_nav_history_from_cache(fund_code, days=7)
    except Exception as e:
        logger.debug(f"[volatility] 获取净值历史失败 {fund_code}: {e}")
        return "none", "", None, None

    if not history or len(history) < 2:
        return "none", "", None, None

    # history 按日期升序，最后一条是最近一天
    last_nav = float(history[-1].get("nav") or 0)
    if last_nav <= 0 or not current_nav:
        return "none", "", None, None

    daily_pct = (current_nav - last_nav) / last_nav * 100

    # 周涨跌幅：用最早的一条
    weekly_pct = None
    if len(history) >= 6:
        first_nav = float(history[0].get("nav") or 0)
        if first_nav > 0:
            weekly_pct = (current_nav - first_nav) / first_nav * 100

    # 读阈值配置
    severe_daily = get_config_float("watchlist.volatility_severe_daily_threshold", -3.0)
    severe_weekly = get_config_float("watchlist.volatility_severe_weekly_threshold", -6.0)
    warning_daily = get_config_float("watchlist.volatility_warning_daily_threshold", -1.5)
    warning_weekly = get_config_float("watchlist.volatility_warning_weekly_threshold", -3.0)

    # 判定级别
    reasons = []
    if daily_pct <= severe_daily:
        reasons.append(f"近1日跌{abs(daily_pct):.2f}%")
    if weekly_pct is not None and weekly_pct <= severe_weekly:
        reasons.append(f"近5日跌{abs(weekly_pct):.2f}%")

    if reasons:
        return "severe", "，".join(reasons), daily_pct, weekly_pct

    reasons = []
    if daily_pct <= warning_daily:
        reasons.append(f"近1日跌{abs(daily_pct):.2f}%")
    if weekly_pct is not None and weekly_pct <= warning_weekly:
        reasons.append(f"近5日跌{abs(weekly_pct):.2f}%")

    if reasons:
        return "warning", "，".join(reasons), daily_pct, weekly_pct

    return "none", "", daily_pct, weekly_pct


@router.post("/api/watchlist/{item_id}/lookup")
async def lookup_and_fill_api(item_id: int):
    """查询基金详细信息并自动填充。"""
    item = get_watchlist_item(item_id)
    if not item:
        raise HTTPException(404, "关注记录不存在")

    fund_code = item["fund_code"]
    try:
        info = lookup_fund_info(fund_code)
        if not info:
            raise HTTPException(404, f"未找到 {fund_code} 的基金信息")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"查询失败: {e}")

    updates = {}
    if info.get("fund_name") and not item.get("fund_name"):
        updates["fund_name"] = info["fund_name"]
    if info.get("fund_category") and not item.get("fund_category"):
        updates["fund_category"] = info["fund_category"]
    tracking = info.get("tracking_index", "")
    if tracking and not item.get("index_name"):
        updates["index_name"] = tracking

    # 获取最新净值
    try:
        nav_data = fetch_fund_nav(fund_code)
        if nav_data:
            updates["current_nav"] = nav_data.get("nav")
    except Exception:
        pass

    if updates:
        update_watchlist_item(item_id, **updates)

    return {"ok": True, "info": info, "updates": updates}

