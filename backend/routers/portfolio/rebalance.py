"""调仓管理：智能调仓分析、配置CRUD"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException

from db import (
    get_active_rebalance_config, save_rebalance_config,
    list_rebalance_configs, get_rebalance_config_by_id, rollback_rebalance_config,
    create_async_task, update_async_task,
)
from models.portfolio import StressTestRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio-rebalance"])

_background_tasks = set()


# ── 调仓管理 API ──────────────────────────────────────────

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



@router.post("/api/portfolio/stress-test")
async def portfolio_stress_test_api(req: StressTestRequest):
    """确定性组合压力测试：不调用 LLM，按资产类别冲击估算损失。"""
    from stress_test import run_portfolio_stress_test
    try:
        return run_portfolio_stress_test(req.scenario, custom_shocks=req.custom_shocks)
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.get("/api/portfolio/allocation-dashboard")
async def allocation_dashboard_api():
    """获取目标配置、当前配置和偏离度驾驶舱。"""
    from allocation_dashboard import build_allocation_dashboard
    return build_allocation_dashboard()
