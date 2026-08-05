"""前瞻性事件雷达 — API 端点。

- POST /api/alerts/event-radar/scan：手动触发扫描（异步,立即返回 task_id）
- GET /api/alerts/event-radar/scan/status/{task_id}：查询扫描任务状态
- GET /api/alerts/event-radar/events：事件列表（可按 status/relevance 过滤）
- GET /api/alerts/event-radar/events/{event_id}：事件详情
- POST /api/alerts/event-radar/verify：手动触发落地验证
- GET /api/alerts/event-radar/accuracy：准确率统计
- POST /api/alerts/event-radar/analyze-article：抓取文章并提取投资趋势
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Body

from db.market_events import (
    list_market_events, get_market_event,
)
from api.response import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["event-radar"])

# ── 扫描任务状态（内存存储,单进程,任务完成后保留 1 小时）──
_scan_tasks: dict[str, dict] = {}
_SCAN_TTL_SECONDS = 3600


def _cleanup_expired_tasks() -> None:
    """清理超过 TTL 的已完成任务,避免内存泄漏。"""
    now = datetime.now()
    expired = [
        tid for tid, t in _scan_tasks.items()
        if t.get("status") in ("done", "failed")
        and t.get("finished_at")
        and (now - datetime.fromisoformat(t["finished_at"])).total_seconds() > _SCAN_TTL_SECONDS
    ]
    for tid in expired:
        _scan_tasks.pop(tid, None)


@router.post("/api/alerts/event-radar/scan")
async def manual_scan():
    """手动触发前瞻事件雷达扫描(异步执行)。

    立即返回 task_id,后台执行扫描(含多次 LLM 调用,可能耗时 1-3 分钟)。
    前端通过 /scan/status/{task_id} 轮询进度。

    如果已有扫描任务正在执行,直接返回该任务 ID,避免重复触发浪费 LLM 调用。
    """
    _cleanup_expired_tasks()

    # 幂等保护:已有 running 任务时直接复用,不重复触发
    for tid, t in _scan_tasks.items():
        if t.get("status") == "running":
            logger.info(f"[event_radar-scan] 已有扫描任务在执行 {tid},返回该任务ID(不重复触发)")
            return ApiResponse.success(data={
                "task_id": tid,
                "status": "running",
                "message": "已有扫描任务在执行,返回该任务ID",
                "reused": True,
            })

    task_id = f"scan_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    _scan_tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "result": None,
        "error": None,
    }

    async def _run_scan():
        """后台执行扫描,完成后更新任务状态。"""
        try:
            from services.event_radar import scan_forward_events
            # scan_forward_events 是同步阻塞函数(多次 LLM 调用),
            # 用 asyncio.to_thread 丢到线程池,避免阻塞事件循环
            result = await asyncio.to_thread(scan_forward_events, "")
            _scan_tasks[task_id]["status"] = "done"
            _scan_tasks[task_id]["result"] = result
            logger.info(f"[event_radar-scan] 任务 {task_id} 完成: {result}")
        except Exception as e:
            _scan_tasks[task_id]["status"] = "failed"
            _scan_tasks[task_id]["error"] = str(e)
            logger.error(f"[event_radar-scan] 任务 {task_id} 失败: {e}", exc_info=True)
        finally:
            _scan_tasks[task_id]["finished_at"] = datetime.now().isoformat()

    # 创建后台任务,不 await
    asyncio.create_task(_run_scan())
    logger.info(f"[event_radar-scan] 任务 {task_id} 已启动(后台异步执行)")

    return ApiResponse.success(data={
        "task_id": task_id,
        "status": "running",
        "message": "扫描已启动,请通过 /scan/status/{task_id} 查询进度",
        "reused": False,
    })


@router.get("/api/alerts/event-radar/scan/status/{task_id}")
async def scan_status(task_id: str):
    """查询扫描任务状态。

    返回:
        - status: running / done / failed
        - result: 扫描结果(status=done 时)
        - error: 错误信息(status=failed 时)
        - started_at / finished_at: 时间戳
    """
    task = _scan_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在或已过期: {task_id}")
    return ApiResponse.success(data=task)


@router.get("/api/alerts/event-radar/events")
async def list_events(
    status: Optional[str] = Query(None, description="按状态过滤：upcoming/imminent/materialized/expired"),
    relevance: Optional[str] = Query(None, description="按分级过滤：holding_impact/opportunity/market_watch"),
    limit: int = Query(50, ge=1, le=200),
):
    """查询事件列表。"""
    events = list_market_events(status=status, relevance=relevance, limit=limit)

    # Batch2 增强点 3：附加 effective_confidence 字段（开关控制）
    try:
        from services.event_radar import attach_effective_confidence
        attach_effective_confidence(events)
    except Exception as e:
        logger.warning(f"附加 effective_confidence 失败: {e}")

    conn = None
    last_scan_time = None
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT MAX(detected_date) as last_scan FROM market_events"
        ).fetchone()
        if row and row["last_scan"]:
            last_scan_time = row["last_scan"]
    except Exception as e:
        logger.warning(f"获取上次扫描时间失败: {e}")
    finally:
        if conn:
            conn.close()

    return ApiResponse.success(data={"events": events, "total": len(events), "last_scan_time": last_scan_time})


@router.get("/api/alerts/event-radar/events/{event_id}")
async def get_event(event_id: str):
    """查询事件详情。"""
    event = get_market_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="事件不存在")

    # Batch2 增强点 3：附加 effective_confidence 字段（开关控制）
    try:
        from services.event_radar import attach_effective_confidence
        attach_effective_confidence(event)
    except Exception as e:
        logger.warning(f"附加 effective_confidence 失败: {e}")

    return ApiResponse.success(data=event)


@router.post("/api/alerts/event-radar/verify")
async def manual_verify():
    """手动触发事件落地验证（扫描已落地超过 T+N 的事件）。"""
    try:
        from services.event_radar import verify_materialized_events
        result = verify_materialized_events()
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"手动触发事件验证失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"验证失败: {e}")


@router.post("/api/alerts/event-radar/backfill-verify")
async def backfill_verification(
    max_events: int = Body(200, embed=True, ge=1, le=1000),
    force: bool = Body(False, embed=True, description="True 时忽略 T+3 窗口检查，对所有未验证事件尝试验证"),
):
    """Accuracy-Boost（2026-07-30）：批量补全历史未验证事件。

    与 /verify 的区别：
    1. 独立运行，不依赖 scan_forward_events
    2. 使用 _infer_sectors_from_event 兜底空 affected_sectors
    3. 返回详细 skip 原因统计
    4. force=True 可突破 T+3 窗口限制（用于历史数据补全）

    场景：77 个 materialized 事件只有 25 个被验证，本接口补全剩余 52 个。
    """
    try:
        from services.market.event_radar import backfill_event_verification
        result = backfill_event_verification(max_events=max_events, force=force)
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"backfill 验证失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"backfill 验证失败: {e}")


@router.get("/api/alerts/event-radar/accuracy")
async def accuracy_stats():
    """获取事件验证准确率统计（总体 + 分板块）。"""
    try:
        from services.event_radar import get_sector_accuracy_stats
        stats = get_sector_accuracy_stats()
        return ApiResponse.success(data=stats)
    except Exception as e:
        logger.error(f"获取准确率统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取失败: {e}")


@router.post("/api/alerts/event-radar/analyze-impact")
async def analyze_event_impact_api(event_id: str = Body(..., embed=True)):
    """LLM 深度解读事件影响（结合用户持仓）。

    - 开关：alerts.event_impact_analysis_enabled（默认 false）
    - 缓存：alerts.event_impact_analysis_cache_days（默认 7 天）
    - 失败时返回 error 字段，HTTP 仍 200，前端按 data.error 判断
    """
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id 不能为空")
    try:
        from services.event_radar import analyze_event_impact
        result = analyze_event_impact(event_id)
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"事件影响分析失败 event_id={event_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


@router.get("/api/alerts/event-radar/events/{event_id}/impact-amount")
async def estimate_impact_amount_api(event_id: str):
    """Batch2 增强点 2：实时估算事件对用户持仓的金额影响（纯计算，无 LLM）。

    - 开关：alerts.event_impact_amount_enabled（默认 false）
    - 公式：影响金额 = expected_impact_pct × holding_value / 100
    - 不缓存：每次调用实时计算（持仓会变化）
    - 失败时返回 reason 字段说明原因
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("alerts.event_impact_amount_enabled", False):
            return ApiResponse.success(data={
                "event_id": event_id,
                "total_impact_amount": 0.0,
                "affected_holdings": [],
                "reason": "事件影响金额估算开关未开启",
            })
    except Exception as e:
        logger.warning(f"检查 event_impact_amount 开关失败: {e}")

    event = get_market_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="事件不存在")

    try:
        from services.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(event)
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"事件影响金额估算失败 event_id={event_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"估算失败: {e}")


@router.post("/api/alerts/event-radar/analyze-article")
async def analyze_article_trends(url: str = Body(..., embed=True)):
    """抓取文章并提取投资趋势。

    流程：
    1. 调用 services/article_reader.py 的 fetch_generic_article 抓取文章
    2. 调用 services/event_radar.py 的 _extract_trends_from_articles 提取趋势
    3. 将趋势写入 market_events 表（带 time_frame/evidence 字段）
    4. 返回提取的趋势列表
    """
    if not url or not url.strip().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请提供合法的文章 URL（http/https）")
    try:
        from services.article_reader import fetch_generic_article
        from services.event_radar import _extract_trends_from_articles
        from db.market_events import create_market_event, get_market_event, _gen_event_id

        # 1. 抓取文章
        article = await fetch_generic_article(url)
        content = (article or {}).get("content_text", "") or ""
        title = (article or {}).get("title", "") or ""

        if len(content) < 500:
            raise HTTPException(
                status_code=400,
                detail=f"文章内容过短或抓取失败（{len(content)} 字符），无法提取趋势。标题：{title or '未知'}",
            )

        # 2. 提取趋势
        trends = _extract_trends_from_articles(content, title)

        # 3. 写入 market_events 表（幂等）
        saved_new = 0
        for trend in trends:
            try:
                eid = _gen_event_id(trend.get("title", ""), "")
                existing = get_market_event(eid)
                create_market_event(
                    title=trend.get("title", ""),
                    summary=trend.get("summary", ""),
                    event_type=trend.get("event_type", "theme"),
                    direction=trend.get("direction", "neutral"),
                    expected_date="",
                    affected_sectors=trend.get("affected_sectors", []),
                    affected_themes=trend.get("affected_themes", []),
                    confidence=float(trend.get("confidence", 0.5)),
                    sources=[{"title": title, "url": url}],
                    time_frame=trend.get("time_frame", ""),
                    evidence=trend.get("evidence", ""),
                )
                if not existing:
                    saved_new += 1
            except Exception as e:
                logger.warning(f"写入趋势事件失败 '{trend.get('title', '')}': {e}")

        logger.info(
            f"[event_radar] 文章趋势分析完成: url={url}, title={title}, "
            f"提取 {len(trends)} 个趋势，新增 {saved_new} 个"
        )

        return ApiResponse.success(data={
            "trends": trends,
            "total": len(trends),
            "new": saved_new,
            "article_title": title,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分析文章趋势失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


# ── O-8（2026-07-21）：一键触发历史数据 backfill ──

@router.post("/api/alerts/event-radar/backfill")
async def backfill_history(
    max_events: int = Body(100, embed=True, ge=1, le=500),
    only: Optional[str] = Query(None, description="仅执行指定 backfill 类型：sources/impact/direction/confidence/opportunity/watchlist。空则执行全部"),
):
    """一键触发历史数据 backfill（O-2/O-3/O-4/O-5/O-6/O-7/O-8）。

    Args:
        max_events: 每类 backfill 最多处理多少条
        only: 仅执行指定类型（默认全部）
    Returns:
        各类 backfill 的 processed/updated/skipped 统计
    """
    try:
        results = {}
        if only is None or only == "sources":
            from services.market.event_radar import backfill_event_sources
            results["sources"] = backfill_event_sources(max_events=max_events)
        if only is None or only == "impact":
            from services.market.event_radar import backfill_event_impact_fields
            results["impact"] = backfill_event_impact_fields(max_events=max_events)
        if only is None or only == "direction":
            from services.market.event_radar import backfill_event_direction
            results["direction"] = backfill_event_direction(max_events=max_events)
        if only is None or only == "confidence":
            from services.market.event_radar import backfill_event_confidence
            results["confidence"] = backfill_event_confidence(max_events=max_events)
        if only is None or only == "opportunity":
            from services.advisor.opportunity_engine import backfill_opportunity_fields
            results["opportunity"] = backfill_opportunity_fields(max_items=max_events)
        if only is None or only == "watchlist":
            from db.watchlist import refresh_watchlist_percentile
            results["watchlist"] = refresh_watchlist_percentile()
        return ApiResponse.success(data=results)
    except Exception as e:
        logger.error(f"backfill 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"backfill 失败: {e}")


# ── LI-8（2026-07-22）：领先指标接入层 API ──

@router.get("/api/alerts/leading-indicators/signals")
async def list_leading_indicator_signals(
    lookback_days: int = Query(7, ge=1, le=30, description="回看天数"),
):
    """获取领先指标信号列表（政策草案/资本开支/产业资本/海关/PMI）。"""
    try:
        from services.market.leading_indicators import collect_leading_signals
        signals = collect_leading_signals(lookback_days=lookback_days)
        return ApiResponse.success(data={
            "signals": [
                {
                    "signal_type": s.signal_type,
                    "leading_level": s.leading_level,
                    "title": s.title,
                    "summary": s.summary,
                    "source_url": s.source_url,
                    "publish_date": s.publish_date,
                    "affected_sectors": s.affected_sectors,
                    "affected_themes": s.affected_themes,
                    "direction": s.direction,
                    "confidence": s.confidence,
                    "metric_value": s.metric_value,
                    "metric_unit": s.metric_unit,
                    "metric_yoy": s.metric_yoy,
                    "metric_mom": s.metric_mom,
                }
                for s in signals
            ],
            "total": len(signals),
        })
    except Exception as e:
        logger.error(f"获取领先指标信号失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/opportunities/backtest-stats-by-source")
async def get_backtest_stats_by_source():
    """LI-6：按信号来源分组统计回测命中率。"""
    try:
        from db.opportunities import get_backtest_stats_by_source
        stats = get_backtest_stats_by_source()
        return ApiResponse.success(data=stats)
    except Exception as e:
        logger.error(f"获取回测统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/opportunities/backtest-stats-by-signal")
async def get_backtest_stats_by_signal():
    """Accuracy-Fix（2026-07-27）：按资金面/量能信号分组统计回测命中率。

    用于验证"资金流入+放量"信号的命中率是否高于"资金流出+缩量"信号，
    为后续调整评分维度权重提供数据支撑。
    """
    try:
        from db.opportunities import get_backtest_stats_by_signal
        stats = get_backtest_stats_by_signal()
        return ApiResponse.success(data=stats)
    except Exception as e:
        logger.error(f"获取信号分组统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/opportunities/cleanup-avoid-backtests")
async def cleanup_avoid_backtests():
    """Accuracy-Fix（2026-07-27）：手动清理 verdict=avoid 机会的未回测 backtest 记录。

    启动时已自动执行，此接口供手动触发使用。
    """
    try:
        from db.opportunities import delete_avoid_verdict_backtests
        result = delete_avoid_verdict_backtests()
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"清理 avoid 回测记录失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── 事件受益标的发现（事件 → 受益标的 → 估值 → 个性化推荐）──


@router.get("/api/alerts/event-radar/events/{event_id}/beneficiaries")
async def get_event_beneficiaries(event_id: str):
    """获取事件的受益标的推荐。

    流程：发现 → 估值筛选 → 用户上下文 → 落库 → 返回
    开关：alerts.beneficiary_finder_enabled
    """
    try:
        from db.config import get_config_bool
        from db.market_events import (
            save_event_beneficiaries, get_market_event,
        )
        from services.advisor.beneficiary_finder import discover_beneficiaries
        from services.advisor.valuation_filter import apply_valuation_filter
        from services.advisor.context_integrator import apply_user_context

        # 1. 查事件
        event = get_market_event(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="事件不存在")

        # 2. 总开关关闭时返回空列表
        enabled = True
        try:
            enabled = get_config_bool("alerts.beneficiary_finder_enabled", False)
        except Exception as e:
            logger.warning(f"检查 beneficiary_finder 开关失败: {e}")

        if not enabled:
            return ApiResponse.success(data={
                "event": {"event_id": event_id, "title": event.get("title", "")},
                "beneficiaries": [],
                "summary": {
                    "total": 0, "strong_buy": 0, "watch": 0,
                    "add_position": 0, "observe_only": 0,
                },
                "enabled": False,
            })

        # 3. 发现 → 估值筛选 → 用户上下文
        beneficiaries = discover_beneficiaries(event)
        beneficiaries = apply_valuation_filter(beneficiaries)
        beneficiaries = apply_user_context(beneficiaries)

        # 4. 落库
        try:
            save_event_beneficiaries(event_id, beneficiaries)
        except Exception as e:
            logger.warning(f"落库受益标的失败 event_id={event_id}: {e}")

        # 5. 汇总统计
        summary = {
            "total": len(beneficiaries),
            "strong_buy": sum(1 for b in beneficiaries if b.get("recommendation_tier") == "strong_buy"),
            "watch": sum(1 for b in beneficiaries if b.get("recommendation_tier") == "watch"),
            "add_position": sum(1 for b in beneficiaries if b.get("recommendation_tier") == "add_position"),
            "observe_only": sum(1 for b in beneficiaries if b.get("recommendation_tier") == "observe_only"),
        }

        return ApiResponse.success(data={
            "event": {
                "event_id": event_id,
                "title": event.get("title", ""),
                "event_type": event.get("event_type", ""),
                "affected_sectors": event.get("affected_sectors", []),
                "affected_themes": event.get("affected_themes", []),
            },
            "beneficiaries": beneficiaries,
            "summary": summary,
            "enabled": True,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取事件受益标推荐失败 event_id={event_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/alerts/event-radar/recommendations/stats")
async def recommendation_stats(days: int = Query(30, ge=1, le=365)):
    """获取推荐统计。"""
    try:
        from db.market_events import get_beneficiary_stats
        stats = get_beneficiary_stats(days=days)
        return ApiResponse.success(data=stats)
    except Exception as e:
        logger.error(f"获取推荐统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/alerts/event-radar/recommendations/accuracy")
async def recommendation_accuracy():
    """获取推荐验证准确率（按 tier / valuation 分组）。

    返回 {overall: {...}, by_tier: {...}, by_valuation: {...}}。
    """
    try:
        import json
        from db.market_events import list_event_beneficiaries, list_verified_events

        # 收集所有已验证事件对应的受益标的
        verified_events = list_verified_events(limit=500)
        verified_event_ids = [e["event_id"] for e in verified_events]

        all_beneficiaries: list[dict] = []
        for eid in verified_event_ids:
            try:
                all_beneficiaries.extend(list_event_beneficiaries(eid))
            except Exception:
                continue

        # 只统计有验证结果的标的
        verified_bens = []
        for b in all_beneficiaries:
            vr = b.get("verification_result")
            if not vr:
                continue
            if isinstance(vr, str):
                try:
                    vr = json.loads(vr)
                except Exception:
                    continue
            if isinstance(vr, dict):
                b = dict(b)
                b["verification_result"] = vr
                verified_bens.append(b)

        if not verified_bens:
            return ApiResponse.success(data={
                "overall": {"total": 0, "correct": 0, "wrong": 0, "neutral": 0, "accuracy": 0.0},
                "by_tier": {},
                "by_valuation": {},
            })

        # 总体统计
        def _status_of(b):
            return (b.get("verification_result") or {}).get("status", "neutral")

        overall_correct = sum(1 for b in verified_bens if _status_of(b) == "correct")
        overall_wrong = sum(1 for b in verified_bens if _status_of(b) == "wrong")
        overall_neutral = sum(1 for b in verified_bens if _status_of(b) == "neutral")
        overall_acc = overall_correct / len(verified_bens) if verified_bens else 0.0

        # 按 recommendation_tier 分组
        by_tier: dict[str, dict] = {}
        for b in verified_bens:
            tier = b.get("recommendation_tier") or "unknown"
            grp = by_tier.setdefault(tier, {"total": 0, "correct": 0, "wrong": 0, "neutral": 0})
            grp["total"] += 1
            grp[_status_of(b)] = grp.get(_status_of(b), 0) + 1
        for grp in by_tier.values():
            grp["accuracy"] = round(grp["correct"] / grp["total"], 4) if grp["total"] else 0.0

        # 按 valuation_status 分组
        by_valuation: dict[str, dict] = {}
        for b in verified_bens:
            vst = b.get("valuation_status") or "unknown"
            grp = by_valuation.setdefault(vst, {"total": 0, "correct": 0, "wrong": 0, "neutral": 0})
            grp["total"] += 1
            grp[_status_of(b)] = grp.get(_status_of(b), 0) + 1
        for grp in by_valuation.values():
            grp["accuracy"] = round(grp["correct"] / grp["total"], 4) if grp["total"] else 0.0

        return ApiResponse.success(data={
            "overall": {
                "total": len(verified_bens),
                "correct": overall_correct,
                "wrong": overall_wrong,
                "neutral": overall_neutral,
                "accuracy": round(overall_acc, 4),
            },
            "by_tier": by_tier,
            "by_valuation": by_valuation,
        })
    except Exception as e:
        logger.error(f"获取推荐验证准确率失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
