"""持仓路由 — /api/portfolio/*

含五大板块：
  - 持仓 CRUD：持仓列表/汇总/创建、现金、基金净值历史、清空
  - 调仓管理：调仓分析、配置 CRUD（获取/更新/历史/详情/回滚）
  - 持仓分析：分散度、AI 汇总、穿透、表现、交易汇总、AI 分析、AI 记录、反馈、bad-case、全景、深度、交易复盘、指定基金分析、今日状态
  - 风险预警：列表、未读数、标记已读、删除、生成
  - 交易标签：添加/移除/获取交易标签
"""

# 后台任务引用集合（防止被垃圾回收）
_background_tasks = set()

import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from infra.schemas import QuickEntryRequest, AiSuggestionToDecisionRequest

from infra.state import (
    track_agent as _track_agent,
    untrack_agent as _untrack_agent,
)
from db import (
    create_holding, get_holding, get_holding_by_fund, list_holdings, update_holding, delete_holding, get_portfolio_summary,
    create_transaction, list_transactions, confirm_transaction, auto_confirm_due_transactions,
    settle_transaction, delete_transaction,
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
    create_async_task, update_async_task, get_async_task,
    get_analysis_cache,
)
from db.portfolio import update_analysis_record
from db.config import get_config as _get_config
from mcp.trading_calendar import expected_confirm_date
from services.rag import build_rag_context_with_details, log_rag_search
from models.portfolio import (
    CreateHoldingRequest, UpdateHoldingRequest,
    CreateTransactionRequest, ConfirmTransactionRequest,
    CreateAlertRequest, TagRequest, AdjustCashRequest,
    PortfolioAiAnalysisRequest, FeedbackRequest,
    PanoramaAnalysisRequest, DeepDiveRequest, TradeReviewRequest, WhatIfRequest,
    StressTestRequest, SellPreviewRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio"])


def save_rebalance_drift_candidate(drift: dict, user_id: str = "default") -> int:
    """把调仓偏离结果保存为再平衡建议候选。"""
    from db.decisions import create_candidate_from_structured_recommendation

    drift = drift or {}
    target_name = drift.get("target_name") or "整体组合"
    summary = drift.get("summary") or f"{target_name} 出现配置偏离，建议复核再平衡"
    drift_pct = drift.get("drift_pct")
    return create_candidate_from_structured_recommendation({
        "source_type": "rebalance",
        "scenario_type": "rebalance_drift",
        "action_type": "rebalance",
        "target_type": "portfolio",
        "target_code": "portfolio",
        "target_name": target_name,
        "summary": summary,
        "reason": drift.get("reason") or summary,
        "suggested_ratio": (float(drift_pct) / 100) if isinstance(drift_pct, (int, float)) else None,
        "confidence": "medium",
        "evidence": {"drift_pct": drift_pct},
        "risk": {"notes": ["再平衡前需确认交易成本、持有期和税费影响"]},
        "source_snapshot": drift,
        "dedupe_key": "rebalance_drift:portfolio",
        "priority": 6,
    }, user_id=user_id)


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


@router.get("/api/portfolio/allocation-dashboard")
async def allocation_dashboard_api():
    """获取目标配置、当前配置和偏离度驾驶舱。"""
    from services.allocation_dashboard import build_allocation_dashboard
    return build_allocation_dashboard()


@router.post("/api/portfolio/stress-test")
async def portfolio_stress_test_api(req: StressTestRequest):
    """确定性组合压力测试：不调用 LLM，按资产类别冲击估算损失。"""
    from services.stress_test import run_portfolio_stress_test
    try:
        return run_portfolio_stress_test(req.scenario, custom_shocks=req.custom_shocks)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/portfolio/clear")
async def clear_portfolio_api():
    """清空所有持仓数据。"""
    clear_all_portfolio_data()
    return {"ok": True, "message": "所有持仓数据已清空"}


# ── CSV 导入导出 ──

@router.get("/api/portfolio/export-csv")
async def export_portfolio_csv(account: str = None):
    """导出持仓为 CSV 文件。"""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    holdings = list_holdings(account=account) if account else list_holdings()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["fund_code", "fund_name", "shares", "cost_price", "current_price",
                      "current_value", "float_pnl", "account", "fund_category"])
    for h in holdings:
        writer.writerow([
            h.get("fund_code", ""),
            h.get("fund_name", ""),
            h.get("shares", 0),
            h.get("cost_price", ""),
            h.get("current_price", ""),
            h.get("current_value", 0),
            h.get("float_pnl", 0),
            h.get("account", ""),
            h.get("fund_category", ""),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portfolio.csv"},
    )


@router.post("/api/portfolio/import-csv-file")
async def import_portfolio_csv_file(file: UploadFile = File(...)):
    """从上传的 CSV 文件导入持仓。"""
    import csv
    import io

    if not file.filename.endswith('.csv'):
        raise HTTPException(400, "请上传 CSV 文件")

    content = await file.read()
    text = content.decode('utf-8-sig')  # 处理 BOM
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            fund_code = (row.get("fund_code") or "").strip()
            fund_name = (row.get("fund_name") or "").strip()
            if not fund_code:
                skipped += 1
                continue
            # 检查是否已存在
            existing = get_holding_by_fund(fund_code)
            if existing:
                # 更新份额
                shares = float(row.get("shares") or 0)
                if shares > 0:
                    update_holding(existing["id"], shares=shares)
                skipped += 1
                continue
            shares = float(row.get("shares") or 0)
            cost_price = float(row["cost_price"]) if row.get("cost_price") else None
            account = (row.get("account") or _get_config('portfolio.default_account', '花无缺')).strip()
            fund_category = (row.get("fund_category") or "").strip() or None
            create_holding(
                fund_code=fund_code,
                fund_name=fund_name,
                shares=shares,
                cost_price=cost_price,
                account=account,
                fund_category=fund_category,
            )
            imported += 1
        except Exception as e:
            errors.append(f"第 {i} 行: {str(e)}")

    return {"ok": True, "imported": imported, "skipped": skipped, "errors": errors}


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


@router.post("/api/portfolio/backfill-snapshots")
async def backfill_snapshots_api():
    """回填历史交易的估值快照。"""
    from db import backfill_valuation_snapshots
    updated = backfill_valuation_snapshots()
    return {"ok": True, "updated": updated}


@router.get("/api/portfolio/alerts")
async def list_alerts_api(unread_only: bool = False, limit: int = 50):
    """获取预警列表（含可靠性标注 + 相关新闻）。

    P0-1: 跨日合并 — 同基金同类型同 severity 合并为一条。
    P0-2: reliability 字段 — 基于 alert_accuracy_stats 回测数据。
    P1-3: related_news 字段 — MCP 财经新闻（受 alert.news_integration 开关控制）。
    """
    alerts = list_alerts(limit=limit, unread_only=unread_only)
    # 附加新闻（受开关控制，关闭时直接返回空列表）；MCP 调用是同步阻塞，放到线程池
    if alerts:
        from services.alert_news_service import enrich_alerts_with_news
        alerts = await asyncio.to_thread(enrich_alerts_with_news, alerts)
    return {"alerts": alerts}


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


@router.get("/api/portfolio/alerts/{alert_id}/history")
async def get_alert_history_api(alert_id: int, days: int = 30):
    """P1-3.1：查询同持仓同类型预警的历史记录。"""
    from db.portfolio import get_alert_history
    history = get_alert_history(alert_id, days)
    return {"history": history, "count": len(history)}


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

    # 自动清理 30 天前的已读预警，防止表无限膨胀
    from db.portfolio import cleanup_old_alerts
    cleaned = cleanup_old_alerts()
    if cleaned:
        logger.info(f"自动清理 {cleaned} 条过期预警")

    # 读取可配置阈值
    val_high = get_config_int('alert.valuation_high', 80)       # 高估百分位
    val_low = get_config_int('alert.valuation_low', 20)          # 低估百分位
    drawdown_threshold = get_config_int('alert.drawdown_pct', 10)  # 回撤预警(%)
    concentration_threshold = get_config_int('alert.concentration_pct', 30)  # 集中度(%)
    cash_high_pct = get_config_int('alert.cash_high_pct', 15)    # 现金闲置(%)
    stale_days = get_config_int('alert.stale_days', 5)           # 数据过期(天)
    buy_drop_threshold = get_config_int('alert.buy_drop_pct', 4)  # 补仓后跌幅(%)

    generated = 0
    today = datetime.now().strftime("%Y-%m-%d")
    today_prefix = datetime.now().strftime("%Y-%m-%d")

    # ── 去重：同一天同一类型+同一基金不重复生成 ──
    # P0-1 修复：原代码用 list_alerts 聚合行的 created_at（实际字段是 latest_at），永远拿不到今日记录
    # 改为直接从原始表查今日已生成的 (alert_type, fund_code) 对
    from db._conn import _get_conn as _get_alert_conn
    _alert_conn = _get_alert_conn()
    try:
        _today_start = f"{today} 00:00:00"
        _existing_rows = _alert_conn.execute(
            """SELECT DISTINCT alert_type, COALESCE(related_fund_code, '') as fund_code
               FROM portfolio_alerts
               WHERE created_at >= ? AND user_id = 'default'""",
            (_today_start,),
        ).fetchall()
        existing_keys = {(r["alert_type"], r["fund_code"]) for r in _existing_rows}
    finally:
        _alert_conn.close()

    def should_create(alert_type, fund_code=""):
        key = (alert_type, fund_code)
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

    # ── 3. 集中度预警（P1-3.1: 集中度+趋势双因子）──
    try:
        total_value = sum(h.get("current_value", 0) or 0 for h in holdings)
        if total_value > 0:
            for h in holdings:
                code = h.get("fund_code", "")
                name = h.get("fund_name", code)
                value = h.get("current_value", 0) or 0
                pct = value / total_value * 100
                if pct >= concentration_threshold and should_create("concentration_alert", code):
                    # P1-3.1: 计算近5日趋势（双因子判定）
                    trend_ch = 0.0
                    try:
                        from services.fund_data_service import get_fund_nav_history_from_cache
                        navs = get_fund_nav_history_from_cache(code, days=5)
                        if navs and len(navs) >= 2:
                            valid = [n for n in navs if n.get("nav") and n["nav"] > 0]
                            if len(valid) >= 2 and valid[0]["nav"] > 0:
                                trend_ch = (valid[-1]["nav"] - valid[0]["nav"]) / valid[0]["nav"] * 100
                    except Exception as te:
                        logger.debug(f"[alert_scan] 集中度趋势计算跳过 {code}: {te}")

                    # 双因子判定 severity
                    if pct >= concentration_threshold * 1.5:  # ≥45% 严重超限 → danger
                        severity = "danger"
                        advice = f"占比 {pct:.1f}% 严重超限，建议立即减仓 10-15%"
                    elif trend_ch < -1.0:  # 集中度高 + 持续下跌 → danger
                        severity = "danger"
                        advice = f"占比 {pct:.1f}% 且近5日下跌 {trend_ch:.2f}%，建议减仓 5-10%"
                    elif trend_ch < 0:  # 集中度高 + 微跌 → warning
                        severity = "warning"
                        advice = f"占比 {pct:.1f}%，近5日 {trend_ch:.2f}%，建议关注走势"
                    else:  # 集中度高 + 上涨/平稳 → warning
                        severity = "warning"
                        advice = f"占比 {pct:.1f}%，建议适当分散配置"

                    create_alert(
                        alert_type="concentration_alert",
                        title=f"{name} 占比过高（{pct:.1f}%）",
                        content=f"{name}（{code}）占组合总市值 {pct:.1f}%，超过集中度阈值 {concentration_threshold}%。近5日走势 {trend_ch:+.2f}%。{advice}。",
                        severity=severity,
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

    # ── 6. 补仓后跌幅预警 ──
    try:
        for h in holdings:
            code = h.get("fund_code", "")
            name = h.get("fund_name", code)
            if not code:
                continue
            last_buy_price = h.get("last_buy_price")
            current_price = h.get("current_price")
            if not last_buy_price or last_buy_price <= 0 or not current_price:
                continue
            drop_pct = (last_buy_price - current_price) / last_buy_price * 100
            if drop_pct >= buy_drop_threshold:
                last_buy_date = h.get("last_buy_date", "")
                if should_create("buy_drop_alert", code):
                    is_danger = drop_pct >= buy_drop_threshold * 1.5
                    create_alert(
                        alert_type="buy_drop_alert",
                        title=f"{name} 补仓后下跌 {drop_pct:.1f}%",
                        content=f"{name}（{code}）最近一次买入价 {last_buy_price:.4f}（{last_buy_date}），当前净值 {current_price:.4f}，已下跌 {drop_pct:.1f}%（阈值 {buy_drop_threshold}%）。请评估是否继续持有或止损。",
                        severity="danger" if is_danger else "warning",
                        related_fund_code=code,
                        related_fund_name=name,
                        source="system_scan",
                    )
                    generated += 1

                    # P0-2.2: danger 级预警联动 dca_add 信号
                    # 历史回测：buy_drop_alert danger 级后续 100% 反弹，平均涨幅 3-7%
                    if is_danger:
                        try:
                            from db.daily_advice import create_signal
                            today = datetime.now().strftime("%Y-%m-%d")
                            base_amount = 500
                            if drop_pct >= 8:
                                suggested_amount = base_amount * 2.0   # 2倍
                            elif drop_pct >= 6:
                                suggested_amount = base_amount * 1.5   # 1.5倍
                            else:
                                suggested_amount = base_amount
                            create_signal({
                                "run_id": 0,  # 0 表示预警联动生成，非每日建议运行
                                "user_id": "default",
                                "signal_date": today,
                                "signal_type": "dca_add",
                                "action_type": "dca",
                                "target_type": "fund",
                                "target_code": code,
                                "target_name": name,
                                "severity": "actionable",  # danger 级预警直接 actionable
                                "score": 75,               # 基础分 75（高分组）
                                "confidence": "high",
                                "score_detail": {
                                    "来源": "buy_drop_alert danger 联动",
                                    "补仓后跌幅": round(drop_pct, 2),
                                    "历史胜率": "100%",
                                    "平均反弹": "3-7%",
                                },
                                "suggested_amount": suggested_amount,
                                "suggested_ratio": None,
                                "summary": f"{name} 补仓后跌 {drop_pct:.1f}%，短期超跌反弹概率高，建议加仓 ¥{suggested_amount:.0f}",
                                "rationale": f"历史回测：buy_drop_alert danger 级预警后续 100% 反弹，平均涨幅 3-7%。最近买入价 {last_buy_price:.4f}，当前 {current_price:.4f}。",
                                "evidence": {
                                    "alert_type": "buy_drop_alert",
                                    "drop_pct": round(drop_pct, 2),
                                    "last_buy_price": last_buy_price,
                                    "current_price": current_price,
                                    "last_buy_date": last_buy_date,
                                },
                                "risks": {"notes": ["短期超跌反弹信号，非长期投资判断"]},
                                "dedupe_key": f"alert_link:{today}:buy_drop_danger:{code}",
                            })
                            logger.info(f"buy_drop_alert danger 联动生成 dca_add 信号: {code}")
                        except Exception as e:
                            logger.warning(f"buy_drop_alert 联动 dca_add 失败: {e}")
    except Exception as e:
        logger.warning(f"[alert_scan] 补仓后跌幅预警异常: {e}")

    # ── P0-2.3：预警→候选自动转化 — danger/warning 预警自动入 recommendation_candidates ──
    # 纯数据库操作，0 LLM；dedupe_key 防止同日重复转化
    try:
        from db.decisions import create_recommendation_candidate
        from db._conn import _get_conn
        conn = _get_conn()
        today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
        new_alerts = conn.execute(
            """SELECT id, alert_type, severity, title, content, related_fund_code, related_fund_name
               FROM portfolio_alerts
               WHERE severity IN ('warning', 'danger')
                 AND created_at >= ?
                 AND source = 'system_scan'
               ORDER BY created_at DESC""",
            (today_start,),
        ).fetchall()
        conn.close()

        # alert_type → action_type 映射
        _ALERT_ACTION_MAP = {
            "drawdown_alert": "reduce",          # 回撤→减仓
            "buy_drop_alert": "watch",           # 补仓后跌幅→观察
            "concentration_alert": "rebalance",  # 集中度→调仓
            "valuation_alert": "reduce",         # 高估→减仓
            "valuation_opportunity": "add",      # 低估→加仓
            "cash_idle": "cash_reserve",         # 现金闲置→备用金
            "stale_data": "watch",               # 数据过期→观察
        }
        converted = 0
        for a in new_alerts:
            a = dict(a)
            fund_code = a.get("related_fund_code") or ""
            action_type = _ALERT_ACTION_MAP.get(a["alert_type"], "watch")
            dedupe_key = f"alert:{a['alert_type']}:{fund_code}:{today_prefix}"
            try:
                create_recommendation_candidate(
                    source_type="alert",
                    source_id=a["id"],
                    action_type=action_type,
                    target_type="fund",
                    target_code=fund_code,
                    target_name=a.get("related_fund_name") or "",
                    summary=a["title"],
                    rationale=a.get("content", "")[:500],
                    confidence="high" if a["severity"] == "danger" else "medium",
                    evidence={"alert_type": a["alert_type"], "severity": a["severity"], "alert_id": a["id"]},
                    dedupe_key=dedupe_key,
                    priority=5 if a["severity"] == "danger" else 3,
                )
                converted += 1
            except Exception as ce:
                logger.debug(f"[alert_to_candidate] 转化失败 alert_id={a['id']}: {ce}")
        if converted:
            logger.info(f"[alert_to_candidate] 转化 {converted} 条预警为决策候选")
    except Exception as e:
        logger.warning(f"[alert_to_candidate] 预警转候选批量异常: {e}")

    return {"ok": True, "generated": generated}


# ── P2-4.3: 预警准确性回测 API ──────────────────────────────


@router.get("/api/portfolio/alerts/accuracy-stats")
async def get_alert_accuracy_stats_api(weeks: int = 4):
    """查询预警准确性回测统计。

    参数:
        weeks: 查询最近 N 周（默认 4）
    """
    from services.alert_accuracy_backtest import get_alert_accuracy_stats
    stats = get_alert_accuracy_stats(weeks=weeks)
    return {"stats": stats}


@router.post("/api/portfolio/alerts/backtest")
async def backtest_alert_accuracy_api(week_start: str = None):
    """手动触发预警准确性回测。

    参数:
        week_start: 周一日期（YYYY-MM-DD），不传则回测上周
    """
    from services.alert_accuracy_backtest import backtest_alert_accuracy
    try:
        result = backtest_alert_accuracy(week_start=week_start)
        return {"ok": True, **result}
    except Exception as e:
        logger.error(f"预警准确性回测失败: {e}", exc_info=True)
        raise HTTPException(500, f"回测失败: {e}")


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
    # 为交易补充基金名称
    for tx in txs:
        if not tx.get("fund_name"):
            # 从持仓表查基金名称
            fund_code = tx.get("fund_code", "")
            if fund_code:
                from db.portfolio import get_holding_by_fund
                h = get_holding_by_fund(fund_code)
                if h:
                    tx["fund_name"] = h.get("fund_name", fund_code)
                else:
                    info = lookup_fund_info(fund_code)
                    tx["fund_name"] = (info or {}).get("fund_name") or fund_code
                    if info and info.get("fund_name"):
                        try:
                            from db._conn import _get_conn
                            conn = _get_conn()
                            conn.execute(
                                "UPDATE portfolio_transactions SET fund_name = ? WHERE id = ?",
                                (info["fund_name"], tx["id"]),
                            )
                            conn.commit()
                            conn.close()
                        except Exception:
                            pass
    return {"transactions": txs}


@router.get("/api/portfolio/audit-log")
async def get_audit_log(fund_code: str = None, tx_id: int = None, limit: int = 50):
    """获取交易操作审计日志。"""
    from db._conn import _get_conn
    conn = _get_conn()
    conn.row_factory = __import__('sqlite3').Row
    conditions = []
    params = []
    if fund_code:
        conditions.append("fund_code = ?")
        params.append(fund_code)
    if tx_id:
        conditions.append("tx_id = ?")
        params.append(tx_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM portfolio_tx_audit_log {where} ORDER BY id DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    conn.close()
    return {"logs": [dict(r) for r in rows]}


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
        fund_name=req.fund_name,
        account=req.account,
    )
    return {"ok": True, "transaction_id": tx_id, "expected_confirm_date": expected_confirm}


@router.post("/api/portfolio/transactions/auto-confirm")
async def auto_confirm_transactions_api():
    """自动确认已到确认日的待确认交易。"""
    return {"ok": True, "result": auto_confirm_due_transactions()}


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


@router.post("/api/portfolio/transactions/{tx_id}/auto-confirm")
async def auto_confirm_transaction_api(tx_id: int):
    """自动获取确认日净值并确认交易。"""
    from db.portfolio import get_transaction, get_nav_by_date, confirm_transaction, refresh_holding_price

    tx = get_transaction(tx_id)
    if not tx:
        raise HTTPException(404, "交易记录不存在")
    if tx.get("status") != "pending":
        raise HTTPException(400, "只能自动确认 pending 状态的交易")

    fund_code = tx["fund_code"]
    expected_date = tx.get("expected_confirm_date") or tx.get("transaction_date")

    # 优先查询历史净值
    confirmed_price = get_nav_by_date(fund_code, expected_date)

    # 没有历史净值则获取最新净值（并提示）
    used_latest = False
    if not confirmed_price:
        holding_id = tx.get("holding_id")
        if holding_id:
            nav_data = refresh_holding_price(holding_id)
            if nav_data:
                confirmed_price = nav_data.get("nav")
                used_latest = True
        if not confirmed_price:
            raise HTTPException(502, "无法获取基金净值，请稍后重试或手动填入")

    ok = confirm_transaction(tx_id, confirmed_price)
    if not ok:
        raise HTTPException(500, "确认失败")

    return {
        "ok": True,
        "confirmed_price": confirmed_price,
        "nav_source": "history" if not used_latest else "latest",
        "message": "已使用历史净值确认" if not used_latest else f"未找到 {expected_date} 历史净值，已使用最新净值确认，请核实",
    }


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
    """批量刷新所有持仓的最新净值，并缓存到 fund_nav_history。"""
    results = refresh_all_fund_prices()
    failed = [r for r in results if "error" in r]
    return {
        "ok": True,
        "results": results,
        "total": len(results),
        "success": len(results) - len(failed),
        "failed_count": len(failed),
    }


@router.get("/api/portfolio/refresh-status")
async def refresh_status_api():
    """查询最近一次批量刷新净值的状态。"""
    status = get_analysis_cache("portfolio_last_refresh_status")
    if not status:
        return {"status": "unknown", "message": "暂无刷新记录"}
    return {"status": "ok", "data": status}


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


@router.get("/api/portfolio/{holding_id}/dca-suggestion")
async def get_dca_suggestion_api(holding_id: int):
    """获取加仓建议（4%定投法）。"""
    from db.portfolio import get_dca_suggestion
    result = get_dca_suggestion(holding_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.post("/api/portfolio/{holding_id}/sell-preview")
async def preview_sell_api(holding_id: int, req: SellPreviewRequest):
    """减仓预览：预计盈亏和约束警告。"""
    from db.portfolio import preview_sell
    result = preview_sell(holding_id, req.shares)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/api/portfolio/snapshot")
async def save_snapshot_api():
    """手动保存今日持仓快照（每日自动快照由定时任务触发）。"""
    from db.portfolio import save_portfolio_snapshot
    snapshot_id = save_portfolio_snapshot()
    return {"ok": True, "snapshot_id": snapshot_id}


@router.get("/api/portfolio/snapshots")
async def list_snapshots_api(limit: int = 365):
    """查询持仓快照历史（用于收益曲线）。"""
    from db.portfolio import list_portfolio_snapshots
    snapshots = list_portfolio_snapshots(limit=limit)
    return {"snapshots": snapshots}


@router.post("/api/portfolio/quick-entry")
async def quick_entry_api(data: QuickEntryRequest):
    """快速录入：只输基金代码+金额，自动查基金名称和当前净值。"""
    fund_code = data.fund_code.strip()
    amount = data.amount
    tx_type = data.transaction_type
    from datetime import datetime as _dt
    tx_date = data.transaction_date or _dt.now().strftime("%Y-%m-%d")
    account = data.account
    notes = data.notes

    if not fund_code:
        raise HTTPException(400, "基金代码不能为空")
    if amount <= 0:
        raise HTTPException(400, "金额必须大于 0")

    # 自动查基金名称
    fund_name = fund_code
    try:
        import akshare as ak
        info = ak.fund_individual_basic_info_xq(symbol=fund_code)
        if info is not None and not info.empty:
            name_row = info[info["item"] == "基金简称"]
            if not name_row.empty:
                fund_name = str(name_row.iloc[0]["value"])
    except Exception:
        pass

    # 自动查当前净值
    current_price = None
    try:
        import akshare as ak
        nav = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值")
        if nav is not None and not nav.empty:
            current_price = float(nav.iloc[-1]["单位净值"])
    except Exception:
        pass

    # 计算份额（如果有净值）
    shares = None
    price = current_price
    if price and price > 0:
        shares = round(amount / price, 2)

    # 创建交易记录
    from db.portfolio import create_transaction
    tx_id = create_transaction(
        fund_code=fund_code,
        transaction_type=tx_type,
        amount=amount,
        transaction_date=tx_date,
        shares=shares,
        price=price,
        fund_name=fund_name,
        account=account,
        notes=notes or "快速录入",
    )

    return {
        "ok": True,
        "transaction_id": tx_id,
        "fund_code": fund_code,
        "fund_name": fund_name,
        "current_price": current_price,
        "shares": shares,
        "amount": amount,
    }


@router.post("/api/portfolio/ai-suggestion-to-decision")
async def ai_suggestion_to_decision_api(data: AiSuggestionToDecisionRequest):
    """AI建议→一键生成决策卡片。

    接收 AI 对话中的建议文本，解析后创建决策候选。
    前端可在 AI 分析完成后展示「保存为决策」按钮，用户点击后调用此接口。
    """
    from db.decisions import create_candidate_from_structured_recommendation

    # 从 AI 建议中提取结构化信息
    suggestion_text = data.suggestion or data.analysis or ""
    if not suggestion_text:
        raise HTTPException(400, "建议内容不能为空")

    # 自动检测行动类型
    action_type = "watch"
    suggestion_lower = suggestion_text.lower()
    if any(kw in suggestion_lower for kw in ["买入", "加仓", "建仓", "定投"]):
        action_type = "buy"
    elif any(kw in suggestion_lower for kw in ["卖出", "减仓", "清仓", "止盈"]):
        action_type = "sell"
    elif any(kw in suggestion_lower for kw in ["调仓", "再平衡", "转换"]):
        action_type = "convert"

    # 提取基金代码（如果有的话）
    import re
    fund_code = ""
    fund_name = ""
    code_match = re.search(r"\d{6}(?:\.\w+)?", suggestion_text)
    if code_match:
        fund_code = code_match.group(0)

    # 提取金额提示
    amount_hint = None
    amount_patterns = [
        r"(?:不超过|约|投入|买入|加仓|单次)[^\d]{0,8}(\d+(?:\.\d+)?)\s*(?:元|块)",
        r"(\d+(?:\.\d+)?)\s*(?:元|块)",
    ]
    for pat in amount_patterns:
        m = re.search(pat, suggestion_text)
        if m:
            try:
                amount_hint = float(m.group(1))
            except (TypeError, ValueError):
                pass
            break

    # 提取置信度
    confidence = "medium"
    if any(kw in suggestion_text for kw in ["强烈", "非常确定", "高置信"]):
        confidence = "high"
    elif any(kw in suggestion_text for kw in ["谨慎", "不确定", "低置信"]):
        confidence = "low"

    candidate_id = create_candidate_from_structured_recommendation({
        "source_type": "ai_chat",
        "source_id": data.get("conversation_id"),
        "scenario_type": data.get("scenario_type", "ai_analysis"),
        "action_type": action_type,
        "target_type": "fund" if fund_code else "portfolio",
        "target_code": fund_code,
        "target_name": fund_name,
        "summary": suggestion_text[:200],
        "rationale": suggestion_text,
        "suggested_amount": amount_hint,
        "confidence": confidence,
        "evidence": {"source": "ai_chat", "agent_id": data.get("agent_id")},
        "priority": 2 if action_type in ("buy", "sell") else 3,
    })

    return {
        "ok": True,
        "candidate_id": candidate_id,
        "action_type": action_type,
        "fund_code": fund_code,
        "amount_hint": amount_hint,
        "confidence": confidence,
        "message": "决策卡片已创建，可在决策中心查看",
    }
