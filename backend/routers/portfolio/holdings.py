"""持仓CRUD、现金管理、净值历史、CSV导入导出、快照"""
import asyncio
import csv
import io
import json
import logging

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db import (
    list_holdings, get_holding, get_holding_by_fund, create_holding,
    update_holding, delete_holding, get_portfolio_summary,
    clear_all_portfolio_data, get_cash_balance, add_cash, set_cash_balance,
    get_fund_nav_history, refresh_holding_price, refresh_all_fund_prices,
    fetch_fund_nav, lookup_fund_info, get_fund_holdings,
    create_async_task, update_async_task,
    get_active_rebalance_config,
)
from db.portfolio import save_portfolio_snapshot, list_portfolio_snapshots
from models.portfolio import (
    CreateHoldingRequest, UpdateHoldingRequest, AdjustCashRequest, StressTestRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio-holdings"])

_background_tasks = set()


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


@router.post("/api/portfolio/rebalancing/trigger")
async def trigger_rebalancing_api():
    """触发智能调仓建议分析（异步）：结合持仓分布和市场估值，分析偏离度并给出建议。"""
    task_id = create_async_task("rebalancing", caller="rebalancing")
    task = asyncio.create_task(_run_rebalancing_async(task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"task_id": task_id, "status": "running"}


async def _run_rebalancing_async(task_id: int):
    """后台执行智能调仓建议分析。"""
    try:
        from rebalancer import analyze_rebalancing_need
        result = await asyncio.to_thread(analyze_rebalancing_need)
        if "error" in result:
            update_async_task(task_id, status="error", error_msg=result["error"])
            return
        update_async_task(task_id, status="done", result=result)
    except Exception as e:
        logger.error(f"调仓分析失败 task_id={task_id}: {e}")
        update_async_task(task_id, status="error", error_msg=str(e))


@router.get("/api/portfolio/rebalance/config")
async def get_rebalance_config_api():
    """获取当前调仓配置（优先从数据库读取）和所有可用策略预设。"""
    from config import get_rebalance_config, list_strategy_presets, get_strategy_info

    # 优先从数据库读取活跃配置
    db_config = get_active_rebalance_config()
    if db_config:
        return {
            "config": db_config["config"],
            "presets": list_strategy_presets(),
            "current_strategy": get_strategy_info(db_config["strategy"]),
            "config_id": db_config["id"],
            "created_at": db_config["created_at"],
        }

    # 数据库无配置时，返回 env 中的默认值
    return {
        "config": get_rebalance_config(),
        "presets": list_strategy_presets(),
        "current_strategy": get_strategy_info(),
        "config_id": None,
        "created_at": None,
    }


@router.post("/api/portfolio/rebalance/config")
async def update_rebalance_config_api(req: dict):
    """保存调仓配置到数据库（创建新版本）。"""
    from config import get_rebalance_config, get_strategy_info

    # 合并：当前配置 + 本次修改
    current = get_active_rebalance_config()
    if current:
        merged = {**current["config"]}
    else:
        merged = get_rebalance_config()

    # 应用本次修改
    for key, value in req.items():
        if key in ("strategy", "base_allocation", "valuation_adjustment",
                    "valuation_percentiles", "drift_thresholds", "cash_targets",
                    "cash_triggers", "drift_ignore", "undervalue_max", "undervalue_amount"):
            merged[key] = value

    strategy = merged.get("strategy", "balanced")
    config_json = json.dumps(merged, ensure_ascii=False)

    # 生成变更摘要
    changes = [k for k in req if k in merged]
    note = f"修改: {', '.join(changes)}" if changes else None

    config_id = save_rebalance_config(strategy, config_json, note=note)

    return {
        "ok": True,
        "message": f"配置已保存（版本 #{config_id}）",
        "config_id": config_id,
    }


@router.get("/api/portfolio/rebalance/config/history")
async def get_rebalance_config_history_api(limit: int = 20):
    """获取调仓配置变更历史。"""
    return {"records": list_rebalance_configs(limit=limit)}


@router.get("/api/portfolio/rebalance/config/{config_id}")
async def get_rebalance_config_detail_api(config_id: int):
    """获取指定版本的配置详情。"""
    cfg = get_rebalance_config_by_id(config_id)
    if not cfg:
        raise HTTPException(404, "配置版本不存在")
    return cfg


@router.post("/api/portfolio/rebalance/config/{config_id}/rollback")
async def rollback_rebalance_config_api(config_id: int):
    """回滚到指定配置版本。"""
    ok = rollback_rebalance_config(config_id)
    if not ok:
        raise HTTPException(404, "配置版本不存在")
    return {"ok": True, "message": f"已回滚到版本 #{config_id}"}


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
            account = (row.get("account") or "花无缺").strip()
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

@router.post("/api/portfolio/backfill-snapshots")
async def backfill_snapshots_api():
    """回填历史持仓快照。"""
    from db.portfolio import backfill_portfolio_snapshots
    count = backfill_portfolio_snapshots()
    return {"ok": True, "backfilled": count}
