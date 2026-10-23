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
    update_alert_acknowledgment,
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
    create_async_task, update_async_task, get_async_task, get_running_async_task,
    get_analysis_cache,
)
from db.portfolio import update_analysis_record, preview_transaction_nav
from db.portfolio_accounting import list_cash_ledger, get_portfolio_performance
from db.config import get_config as _get_config
from mcp.trading_calendar import expected_confirm_date
from services.rag import build_rag_context_with_details, log_rag_search
from models.portfolio import (
    CreateHoldingRequest, UpdateHoldingRequest,
    CreateTransactionRequest, ConfirmTransactionRequest,
    CreateAlertRequest, AcknowledgeAlertRequest, TagRequest, AdjustCashRequest,
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
    try:
        clear_all_portfolio_data()
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
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
            # account 解析提前到查重之前，避免跨账户同基金被错误合并
            account = (row.get("account") or _get_config('portfolio.default_account', '花无缺')).strip()
            shares = float(row.get("shares") or 0)
            cost_price = float(row["cost_price"]) if row.get("cost_price") else None
            fund_category = (row.get("fund_category") or "").strip() or None
            # 检查是否已存在（按 account 精确匹配）
            existing = get_holding_by_fund(fund_code, account=account)
            if existing:
                # 更新份额
                if shares > 0:
                    update_holding(existing["id"], shares=shares)
                skipped += 1
                continue
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
    try:
        if req.mode == "set":
            new_balance = set_cash_balance(uid, req.amount)
        else:
            new_balance = add_cash(uid, req.amount)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
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
async def list_alerts_api(unread_only: bool = False, limit: int = 50, offset: int = 0,
                          active_only: bool = False, severity: str | None = None, alert_type: str | None = None):
    """服务端先筛选活动风险及严重度，再分页；通知已读与风险恢复相互独立。"""
    from db.alert_lifecycle import list_active_alerts_page
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(400, "limit 必须为1–200，offset 不得为负")
    result = list_active_alerts_page(limit=limit, offset=offset, active_only=active_only,
                                    unread_only=unread_only, severity=severity, alert_type=alert_type)
    if result['alerts']:
        from services.alert_news_service import enrich_alerts_with_news
        try:
            result['alerts'] = await asyncio.to_thread(enrich_alerts_with_news, result['alerts'])
        except Exception as exc:
            result['news_error'] = str(exc)
    return result


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
    """仅允许清理完整恢复的风险分组，活动重复行也受保护。"""
    from db.alert_lifecycle import delete_resolved_alert
    try:
        deleted = delete_resolved_alert(alert_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "预警不存在")
    return {"ok": True}


class AlertLifecycleActionRequest(BaseModel):
    status: str
    reason: str | None = None
    review_date: str | None = None


@router.post("/api/portfolio/alerts/{alert_id}/acknowledge")
async def acknowledge_alert_api(alert_id: int, body: AlertLifecycleActionRequest):
    """采纳表示处理中而非成交/恢复；忽略须说明原因和复查日期。"""
    from db.alert_lifecycle import update_alert_action
    try:
        updated = update_alert_action(alert_id, body.status, reason=body.reason, review_date=body.review_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not updated:
        raise HTTPException(404, "预警不存在")
    return {"ok": True, "trade_executed": False, "risk_resolved": False}


@router.get("/api/portfolio/alerts/{alert_id}/history")
async def get_alert_history_api(alert_id: int, days: int = 30):
    """P1-3.1：查询同持仓同类型预警的历史记录。"""
    from db.alert_lifecycle import get_lifecycle_history
    history = get_lifecycle_history(alert_id)
    return {"history": history, "count": len(history)}


@router.post("/api/portfolio/alerts/scan")
async def trigger_proactive_scan_api():
    """P0-B 主动提醒扫描：手动触发一次扫描（也由 app.py 后台定时调用）。

    扫描 4 项：建议验证 + 估值阈值 + 持仓风险 + 关注列表信号，生成 portfolio_alerts。
    开关：alerts.proactive_scan_enabled（默认 true）。
    """
    from services.alert_scanner import run_periodic_scan
    return await asyncio.to_thread(run_periodic_scan)


@router.post("/api/portfolio/alerts/scan-watchlist")
async def trigger_watchlist_scan_api():
    """P0-C 关注列表信号扫描：单独触发一次关注列表上车信号扫描（异步执行）。

    扫描关注列表基金，触发目标价/估值低分位/单日大跌三类信号。
    开关：alerts.watchlist_signal_enabled（默认 true）。
    """
    # 幂等保护：已有 running 任务时直接返回该 task_id
    existing = get_running_async_task("watchlist_signal_scan")
    if existing:
        return {"task_id": existing["id"], "status": "running", "reused": True}

    task_id = create_async_task("watchlist_signal_scan", caller="portfolio")

    async def _run():
        try:
            from services.alert_scanner import scan_watchlist_signals
            result = await asyncio.to_thread(scan_watchlist_signals)
            update_async_task(task_id, status="done", result=result)
        except Exception as e:
            logger.error(f"任务 {task_id} 失败: {e}", exc_info=True)
            update_async_task(task_id, status="error", error_msg=str(e))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "running"}


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


@router.post("/api/portfolio/alerts/scan-detailed")
async def scan_portfolio_alerts():
    """完整覆盖证明驱动恢复；历史失败或数据缺失均保留活动风险。"""
    from services.advisor.alert_scanner import scan_detailed_risks
    return await asyncio.to_thread(scan_detailed_risks, holdings=list_holdings(),
                                   nav_history_loader=get_fund_nav_history,
                                   valuation_loader=get_latest_valuation,cash_loader=get_cash_balance)


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


@router.get("/api/portfolio/cash-ledger")
async def cash_ledger_api(account: str = None, limit: int = 200):
    """现金变动、冻结和应收流水；读取不结息。"""
    return {'ledger': list_cash_ledger(account=account,limit=max(1,min(limit,1000)))}


@router.get("/api/portfolio/performance")
async def portfolio_performance_api(account: str = None):
    """已确认真实成交收益与历史覆盖，不读取或创建假设成交。"""
    return get_portfolio_performance(account=account)


@router.get("/api/portfolio/transactions/{tx_id}/nav-preview")
async def transaction_nav_preview_api(tx_id: int):
    """行情预览与成交确认严格分离。"""
    from db.portfolio import get_transaction
    from infra.blocking import run_blocking
    if not get_transaction(tx_id):
        raise HTTPException(404,'交易记录不存在')
    try:
        return await run_blocking(preview_transaction_nav,tx_id)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


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
    try:
        deleted = delete_holding(holding_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "持仓不存在")
    return {"ok": True}


@router.get("/api/portfolio/{holding_id}/transactions")
async def list_transactions_api(holding_id: int, limit: int = 100,
                                 start_date: str = None, end_date: str = None,
                                 status: str = None):
    """获取持仓的交易记录。"""
    return {"transactions": list_transactions(holding_id=holding_id, limit=limit,
                                              start_date=start_date, end_date=end_date,
                                              status=status)}


@router.post("/api/portfolio/transactions")
async def create_transaction_api(req: CreateTransactionRequest):
    """新增实际操作必须明确资金来源；历史导入使用独立对账路径。"""
    if req.transaction_type == 'buy' and not req.funding_source:
        raise HTTPException(400,'请明确买入资金来源：账户内现金或外部新增资金')
    # 自动计算 T+1 确认日
    expected_confirm = None
    if req.status == "pending" and req.transaction_date:
        try:
            from datetime import datetime as dt
            d = dt.strptime(req.transaction_date, "%Y-%m-%d").date()
            expected_confirm = str(expected_confirm_date(d, req.transaction_time))
        except (ValueError, TypeError):
            pass

    try:
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
            funding_source=req.funding_source or 'internal',
            cash_account=req.cash_account,
        )
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc

    # 买入交易自动标记关注列表为「已上车」
    if req.transaction_type in ("buy", "buyin", "subscribe"):
        try:
            # 实际买入同时推进主题机会闭环：发现/观察 → 上车 → 跟踪验证。
            from db.opportunities import mark_matching_opportunities_bought
            from datetime import datetime as _dt_opp
            mark_matching_opportunities_bought(
                fund_code=req.fund_code,
                amount=req.amount or 0,
                entry_price=req.price,
                entry_date=req.transaction_date or _dt_opp.now().strftime("%Y-%m-%d"),
                transaction_id=tx_id,
            )
        except Exception as e:
            logger.warning(f"[portfolio] 同步主题机会上车记录失败: {e}")
        try:
            from db.config import get_config_bool
            auto_mark_full = get_config_bool("watchlist.auto_mark_bought_enabled", False)
            if auto_mark_full:
                # Batch2 增强点 1：完整字段同步（status + entry_price + entry_date）
                from db.watchlist import auto_mark_bought_on_trade
                from datetime import datetime as _dt_b2
                entry_price = req.price or 0.0
                entry_date = req.transaction_date or _dt_b2.now().strftime("%Y-%m-%d")
                affected = auto_mark_bought_on_trade(req.fund_code, entry_price, entry_date)
                if affected > 0:
                    logger.info(f"[portfolio] 关注基金 {req.fund_code} 已自动同步上车信息（entry_price={entry_price}, entry_date={entry_date}，影响 {affected} 行）")
            else:
                # 保留原有简单逻辑：仅标 status=bought，不填 entry_price
                from db.watchlist import list_watchlist, update_watchlist_item
                watching = list_watchlist(status="watching")
                for w in watching:
                    if w.get("fund_code") == req.fund_code:
                        update_watchlist_item(w["id"], status="bought")
                        logger.info(f"[portfolio] 关注基金 {req.fund_code} 已自动标记 bought（交易 {tx_id}）")
                        break
        except Exception as e:
            logger.warning(f"[portfolio] 自动标记 watchlist bought 失败: {e}")

    return {"ok": True, "transaction_id": tx_id, "expected_confirm_date": expected_confirm}


@router.post("/api/portfolio/transactions/auto-confirm")
async def auto_confirm_transactions_api():
    """自动确认已到确认日的待确认交易。"""
    return {"ok": True, "result": auto_confirm_due_transactions()}


@router.post("/api/portfolio/transactions/{tx_id}/confirm")
async def confirm_transaction_api(tx_id: int, req: ConfirmTransactionRequest):
    """确认交易：填入 T+1 实际净值，计算实际份额/金额。

    手续费：用户填了以用户值为准；未填（fee=0）且开关开启时按费率自动计算。
    """
    # 用户未填手续费时按费率自动计算
    fee = req.fee
    fee_basis = "用户填入"
    if fee == 0:
        from services.fee_calculator import calc_fee_for_tx
        from db.portfolio import get_transaction
        tx = get_transaction(tx_id)
        if tx:
            holding = get_holding(tx["holding_id"]) if tx.get("holding_id") else None
            fee, fee_basis = calc_fee_for_tx(tx, req.confirmed_price, holding)

    try:
        ok = confirm_transaction(tx_id, req.confirmed_price,
                                 confirmed_shares=req.confirmed_shares,
                                 confirmed_amount=req.confirmed_amount,
                                 target_fund_code=req.target_fund_code,
                                 target_fund_name=req.target_fund_name,
                                 fee=fee, target_price=req.target_price,
                                 target_shares=req.target_shares)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    if not ok:
        raise HTTPException(409, "确认失败：请检查交易状态、可用份额及转换目标净值")
    return {"ok": True, "fee": fee, "fee_basis": fee_basis}


@router.post("/api/portfolio/transactions/{tx_id}/auto-confirm")
async def auto_confirm_transaction_api(tx_id: int):
    """自动获取确认日净值并确认交易。"""
    from db.portfolio import get_transaction, get_nav_by_date, confirm_transaction, refresh_holding_price

    tx = get_transaction(tx_id)
    if not tx:
        raise HTTPException(404, "交易记录不存在")
    if tx.get("status") != "pending":
        raise HTTPException(400, "只能自动确认 pending 状态的交易")

    if tx.get('transaction_type') == 'convert':
        raise HTTPException(400, "转换交易请手动填写目标基金和目标实际净值后确认")
    from db.portfolio import _effective_trade_date, fetch_fund_nav_on_or_before
    trade_date = _effective_trade_date(tx['transaction_date'], tx.get('transaction_time'))
    from infra.blocking import run_blocking
    nav_data = await run_blocking(fetch_fund_nav_on_or_before, tx['fund_code'], trade_date)
    if not nav_data or nav_data.get('date') != trade_date:
        raise HTTPException(422, f"尚无 {trade_date} 的实际成交净值，请稍后重试或手动核实")
    confirmed_price = nav_data['nav']
    expected_date = trade_date
    used_latest = False

    # 按费率自动计算手续费（开关 fee.auto_calc_enabled，默认 true）
    from services.fee_calculator import calc_fee_for_tx
    holding = get_holding(tx["holding_id"]) if tx.get("holding_id") else None
    fee, fee_basis = calc_fee_for_tx(tx, confirmed_price, holding)

    ok = confirm_transaction(tx_id, confirmed_price, fee=fee)
    if not ok:
        raise HTTPException(500, "确认失败")

    return {
        "ok": True,
        "confirmed_price": confirmed_price,
        "nav_source": "history" if not used_latest else "latest",
        "fee": fee,
        "fee_basis": fee_basis,
        "message": ("已使用历史净值确认" if not used_latest else f"未找到 {expected_date} 历史净值，已使用最新净值确认，请核实")
                   + (f"，手续费 ¥{fee}" if fee > 0 else ""),
    }


@router.post("/api/portfolio/transactions/{tx_id}/calc-fee")
async def calc_transaction_fee_api(tx_id: int, body: dict = None):
    """预估交易手续费（用于前端确认弹窗预填）。

    Body: { "confirmed_price": 1.23 }  可选，卖出/转换需要净值来算
    Returns: { "fee": 12.50, "basis": "申购费率0.15%", "rate": 0.0015 }
    """
    from db.portfolio import get_transaction
    from services.fee_calculator import calc_fee_for_tx, is_auto_calc_enabled

    tx = get_transaction(tx_id)
    if not tx:
        raise HTTPException(404, "交易记录不存在")

    price = (body or {}).get("confirmed_price") or tx.get("price") or 0
    enabled = is_auto_calc_enabled()
    if not enabled:
        return {"fee": 0, "basis": "自动计算已关闭", "rate": 0, "auto_calc_enabled": False}

    holding = get_holding(tx["holding_id"]) if tx.get("holding_id") else None
    fee, basis = calc_fee_for_tx(tx, price, holding)
    return {"fee": fee, "basis": basis, "auto_calc_enabled": True}


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
    from infra.blocking import run_blocking
    results = await run_blocking(refresh_all_fund_prices)
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
    from infra.blocking import run_blocking
    nav_data = await run_blocking(refresh_holding_price, holding_id)
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
    """手动触发持仓快照（每日记录市值/盈亏/持仓明细，同日幂等覆盖）。"""
    from db.portfolio import create_snapshot
    result = create_snapshot()
    return {"ok": True, "data": result}


@router.get("/api/portfolio/snapshots")
async def list_snapshots_api(start_date: str = None, end_date: str = None, limit: int = 90):
    """查询持仓快照列表（用于收益曲线/盈亏趋势）。

    可选 ?start_date=2026-01-01&end_date=2026-06-30&limit=90 做日期范围筛选。
    """
    from db.portfolio import list_snapshots
    snapshots = list_snapshots(start_date=start_date, end_date=end_date, limit=limit)
    return {"snapshots": snapshots}


# ── 持仓分析 API（P1 优化：配置分布 / 分基金盈亏 / 集中度 / 盈亏趋势） ──────────────


@router.get("/api/portfolio/analysis/distribution")
async def distribution_analysis_api():
    """配置分布分析：按账户、按基金类别聚合市值占比。"""
    from db.portfolio import get_distribution_analysis
    return get_distribution_analysis()


@router.get("/api/portfolio/analysis/profit-by-fund")
async def profit_by_fund_api():
    """分基金盈亏分析，按盈亏额倒序。"""
    from db.portfolio import get_profit_by_fund
    return {"holdings": get_profit_by_fund()}


@router.get("/api/portfolio/analysis/concentration")
async def concentration_analysis_api():
    """集中度分析：单基金占比、Top3 占比、超限预警。"""
    from db.portfolio import get_concentration_analysis
    return get_concentration_analysis()


@router.get("/api/portfolio/analysis/theme")
async def theme_analysis_api():
    """主题聚合分析：跨账户去重、同指数合并、主题集中度（HHI/Top3）、碎片仓位。"""
    from services.portfolio.theme_analysis import analyze_theme_concentration
    return analyze_theme_concentration()


@router.get("/api/portfolio/analysis/profit-trend")
async def profit_trend_api(days: int = 30):
    """盈亏趋势（基于持仓快照，按日期正序返回）。"""
    from db.portfolio import list_snapshots
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    snaps = list_snapshots(start_date=start, limit=days)
    return [
        {
            "date": s["snapshot_date"],
            "total_value": s.get("total_value"),
            "total_cost": s.get("total_cost"),
            "profit_loss": s.get("total_profit_loss"),
            "profit_rate": s.get("total_profit_rate"),
            "total_assets": s.get("total_assets"),
        }
        for s in reversed(snaps)
    ]


@router.post("/api/portfolio/quick-entry")
async def quick_entry_api(data: QuickEntryRequest):
    """快速提交待确认交易；必须明确资金来源，不用当前行情捏造历史成交。"""
    if data.transaction_type == 'buy' and not data.funding_source:
        raise HTTPException(400,'快速买入必须明确账户内现金或外部新增资金')
    from datetime import date
    holding = get_holding_by_fund(data.fund_code,account=data.account)
    if data.transaction_type in ('sell','convert') and (not holding or not data.shares):
        raise HTTPException(400,'快速卖出或转换需要已有持仓及明确提交份额')
    result = await create_transaction_api(CreateTransactionRequest(
        fund_code=data.fund_code, fund_name=data.fund_name or (holding or {}).get('fund_name') or data.fund_code,
        transaction_type=data.transaction_type, amount=0 if data.transaction_type=='buy' else data.amount,
        transaction_date=data.transaction_date or date.today().isoformat(),status='pending',
        holding_id=(holding or {}).get('id'),submitted_amount=data.amount if data.transaction_type=='buy' else None,
        submitted_shares=data.shares,account=data.account,notes=data.notes or '快速录入待确认',
        funding_source=data.funding_source,cash_account=data.cash_account))
    return {**result,'fund_code':data.fund_code,'status':'pending','preview_only':False,
            'message':'已提交待确认，需核对实际成交净值后确认'}


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

    payload = data.model_dump()
    candidate_id = create_candidate_from_structured_recommendation({
        "source_type": "ai_chat",
        "source_id": payload.get("conversation_id"),
        "scenario_type": payload.get("scenario_type") or "ai_analysis",
        "action_type": action_type,
        "target_type": "fund" if fund_code else "portfolio",
        "target_code": fund_code,
        "target_name": fund_name,
        "summary": suggestion_text[:200],
        "rationale": suggestion_text,
        "suggested_amount": amount_hint,
        "confidence": confidence,
        "evidence": {"source": "ai_chat", "agent_id": payload.get("agent_id")},
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
