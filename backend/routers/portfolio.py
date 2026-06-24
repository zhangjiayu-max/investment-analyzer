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

from state import (
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
)
from db.portfolio import update_analysis_record
from db.config import get_config as _get_config
from mcp.trading_calendar import expected_confirm_date
from rag import build_rag_context_with_details, log_rag_search
from models.portfolio import (
    CreateHoldingRequest, UpdateHoldingRequest,
    CreateTransactionRequest, ConfirmTransactionRequest,
    CreateAlertRequest, TagRequest, AdjustCashRequest,
    PortfolioAiAnalysisRequest, FeedbackRequest,
    PanoramaAnalysisRequest, DeepDiveRequest, TradeReviewRequest, WhatIfRequest,
    StressTestRequest,
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
    from allocation_dashboard import build_allocation_dashboard
    return build_allocation_dashboard()


@router.post("/api/portfolio/stress-test")
async def portfolio_stress_test_api(req: StressTestRequest):
    """确定性组合压力测试：不调用 LLM，按资产类别冲击估算损失。"""
    from stress_test import run_portfolio_stress_test
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
    buy_drop_threshold = get_config_int('alert.buy_drop_pct', 4)  # 补仓后跌幅(%)

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
                    create_alert(
                        alert_type="buy_drop_alert",
                        title=f"{name} 补仓后下跌 {drop_pct:.1f}%",
                        content=f"{name}（{code}）最近一次买入价 {last_buy_price:.4f}（{last_buy_date}），当前净值 {current_price:.4f}，已下跌 {drop_pct:.1f}%（阈值 {buy_drop_threshold}%）。请评估是否继续持有或止损。",
                        severity="danger" if drop_pct >= buy_drop_threshold * 1.5 else "warning",
                        related_fund_code=code,
                        related_fund_name=name,
                        source="system_scan",
                    )
                    generated += 1
    except Exception as e:
        logger.warning(f"[alert_scan] 补仓后跌幅预警异常: {e}")

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
async def quick_entry_api(data: dict):
    """快速录入：只输基金代码+金额，自动查基金名称和当前净值。"""
    fund_code = (data.get("fund_code") or "").strip()
    amount = data.get("amount", 0)
    tx_type = data.get("transaction_type", "buy")
    from datetime import datetime as _dt
    tx_date = data.get("transaction_date", _dt.now().strftime("%Y-%m-%d"))
    account = data.get("account")
    notes = data.get("notes")

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
