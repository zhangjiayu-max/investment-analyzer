"""投资决策流水线 + 统一决策账本 API。

三模块联动优化 P0（2026-08-01）：见 doc/plans/2026-08-01-三模块联动优化.md
模块前缀 /api/decision，使用统一响应协议 ApiResponse（{code, message, data}）。
"""
import asyncio
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.response import ApiResponse
from db import get_running_async_task, create_async_task, update_async_task

logger = logging.getLogger(__name__)
router = APIRouter()


class RunDecisionReq(BaseModel):
    fund_code: str
    user_id: str = "default"


@router.post("/api/decision/run")
async def run_decision(req: RunDecisionReq):
    """运行单标的投资决策流水线（发现→sizing→风控→融合→闭环），返回决策卡片（异步执行）。"""
    # 幂等保护：已有 running 任务时直接返回该 task_id
    existing = get_running_async_task("decision_run")
    if existing:
        return {"task_id": existing["id"], "status": "running", "reused": True}

    task_id = create_async_task("decision_run", caller="decision")

    async def _run():
        try:
            from services.advisor.decision_pipeline import run_investment_decision
            card = await asyncio.to_thread(run_investment_decision, req.fund_code, req.user_id)
            if card.get("error"):
                update_async_task(task_id, status="error", error_msg=card["error"])
                return
            update_async_task(task_id, status="done", result=card)
        except Exception as e:
            logger.error(f"任务 {task_id} 失败: {e}", exc_info=True)
            update_async_task(task_id, status="error", error_msg=str(e))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "running"}


@router.get("/api/decision/ledger")
async def list_ledger(
    user_id: str = "default",
    status: str | None = None,
    fund_code: str | None = None,
    decision_type: str | None = None,
    source_module: str | None = None,
    limit: int = Query(100, le=500),
):
    """查询统一决策账本列表。"""
    try:
        from db.investment_decisions import list_decisions
        data = list_decisions(
            user_id=user_id, status=status, fund_code=fund_code,
            decision_type=decision_type, source_module=source_module, limit=limit,
        )
        for item in data:
            item["decision_record_id"] = item.get("decision_record_id")
        return ApiResponse.success(data)
    except Exception as e:
        return ApiResponse.error(500, str(e))


@router.get("/api/decision/ledger/{decision_id}")
async def get_ledger_item(decision_id: int):
    """查询单条决策详情。"""
    try:
        from db.investment_decisions import get_decision
        data = get_decision(decision_id)
        if not data:
            return ApiResponse.error(404, f"未找到决策 {decision_id}")
        archive = None
        try:
            from services.advisor.decision_adapter import unified_decision

            archive = unified_decision(investment_id=decision_id)
        except Exception:
            logger.exception("量化账本关联档案加载失败 investment_id=%s", decision_id)
        if archive:
            data["decision_record"] = archive.get("decision_record")
            data["decision_record_id"] = archive.get("decision_record_id")
        return ApiResponse.success(data)
    except Exception as e:
        return ApiResponse.error(500, str(e))


@router.post("/api/decision/ledger/{decision_id}/archive")
async def create_ledger_archive(decision_id: int):
    """为没有档案的量化账本创建用户可见的决策档案。"""
    try:
        from services.advisor.decision_adapter import (
            ensure_decision_record_for_investment,
            unified_decision,
        )

        ensure_decision_record_for_investment(decision_id)
        result = unified_decision(investment_id=decision_id)
        if not result:
            return ApiResponse.error(404, f"未找到决策 {decision_id}")
        return ApiResponse.success(result)
    except ValueError as e:
        return ApiResponse.error(404, str(e))
    except Exception as e:
        logger.exception("量化账本档案创建失败 investment_id=%s", decision_id)
        return ApiResponse.error(500, str(e))


@router.get("/api/decision/accuracy")
async def accuracy_stats(user_id: str = "default", days: int = Query(90, le=365)):
    """决策准确率统计（命中率/平均超额收益，按来源模块与主题分组）。"""
    try:
        from db import get_decision_accuracy_stats
        return ApiResponse.success(get_decision_accuracy_stats(user_id, days))
    except Exception as e:
        return ApiResponse.error(500, str(e))


@router.get("/api/decision/unified")
async def get_unified_decision(investment_id: int | None = None, archive_id: int | None = None, user_id: str | None = None):
    """查询用户档案和量化账本组成的统一只读视图。"""
    if investment_id is None and archive_id is None:
        return ApiResponse.error(400, "至少提供 investment_id 或 archive_id")
    try:
        from services.advisor.decision_adapter import unified_decision

        result = unified_decision(investment_id=investment_id, archive_id=archive_id, user_id=user_id)
        if not result:
            return ApiResponse.error(404, "决策不存在")
        return ApiResponse.success(result)
    except Exception as e:
        logger.exception("统一决策视图查询失败")
        return ApiResponse.error(500, str(e))


@router.post("/api/decision/backtest")
async def run_backtest(user_id: str = "default"):
    """手动触发决策回测（到期决策计算超额收益→命中判定→归因→权重反哺）（异步执行）。"""
    # 幂等保护：已有 running 任务时直接返回该 task_id
    existing = get_running_async_task("decision_backtest")
    if existing:
        return {"task_id": existing["id"], "status": "running", "reused": True}

    task_id = create_async_task("decision_backtest", caller="decision")

    async def _run():
        try:
            from services.advisor.decision_backtest import run_decision_backtest
            result = await asyncio.to_thread(run_decision_backtest, user_id)
            update_async_task(task_id, status="done", result=result)
        except Exception as e:
            logger.error(f"任务 {task_id} 失败: {e}", exc_info=True)
            update_async_task(task_id, status="error", error_msg=str(e))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "running"}


@router.post("/api/decision/exit-scan")
async def exit_scan(user_id: str = "default"):
    """手动触发止盈闭环扫描（按状态机检查回本/分批止盈并生成提醒）（异步执行）。"""
    # 幂等保护：已有 running 任务时直接返回该 task_id
    existing = get_running_async_task("decision_exit_scan")
    if existing:
        return {"task_id": existing["id"], "status": "running", "reused": True}

    task_id = create_async_task("decision_exit_scan", caller="decision")

    async def _run():
        try:
            from services.advisor.exit_loop import scan_exit_signals
            result = await asyncio.to_thread(scan_exit_signals, user_id)
            update_async_task(task_id, status="done", result=result)
        except Exception as e:
            logger.error(f"任务 {task_id} 失败: {e}", exc_info=True)
            update_async_task(task_id, status="error", error_msg=str(e))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "running"}
