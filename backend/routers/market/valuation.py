"""估值数据路由 — /api/valuation/*"""

import asyncio
import json
import logging
import re
import ssl
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from db.valuations import (
    save_valuation, get_valuation_history, get_latest_valuation,
    list_valuation_indexes, list_index_freshness, get_index_info, save_index_info,
    save_dd_valuation, list_dd_valuations, get_dd_valuation,
    save_liuyi_valuation, list_liuyi_valuations, get_liuyi_valuation,
    get_best_valuation, get_latest_market_temperature, get_latest_dd_valuation_for_index,
    list_index_code_mappings, save_index_code_mapping,
    get_valuation_query_stats,
)
from db._conn import _get_conn
from services.image_parser import DDImageParser, LiuyiImageParser

logger = logging.getLogger(__name__)
from models.valuations import ParseAndSaveRequest, ParseBatchRequest, ParseDDRequest, ParseDDBatchRequest
from services.valuation_parser import parse_single_valuation
from services.valuation.image_paths import resolve_valuation_image, valuation_image_location

router = APIRouter(prefix="/api/valuation", tags=["valuation"])


# ── 解析相关 ──────────────────────────────────────

@router.post("/parse")
async def parse_and_save(req: ParseAndSaveRequest):
    """解析图片并存储估值数据。"""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, parse_single_valuation, req.path, req.model_type, req.source_url, req.snapshot_date)
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    data = result["data"]
    data["trace_id"] = result.get("trace_id", "")
    return data


@router.post("/parse-batch")
async def parse_valuation_batch(req: ParseBatchRequest):
    """并发批量解析多张估值图片。"""
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, parse_single_valuation, p, req.model_type)
        for p in req.paths
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            output.append({"ok": False, "error": str(r), "path": req.paths[i]})
        else:
            output.append(r)
    return {"results": output}


@router.post("/parse-dd")
async def parse_dd_image(req: ParseDDRequest):
    """解析螺丝钉估值表图片（多指数表格数据）。"""
    img_path = resolve_valuation_image(req.path, "dd_images")
    if not req.path or not img_path.is_file():
        raise HTTPException(400, f"图片路径无效: {img_path}")

    dd_parser = DDImageParser(model_type=req.model_type)
    result = dd_parser.parse(str(img_path))
    result["source_path"] = str(img_path)

    # 解析成功后持久化到数据库
    if result.get("ok"):
        # 计算相对路径
        rel_path, image_url = valuation_image_location(img_path)
        dd_id = save_dd_valuation(result, rel_path, image_url)
        result["dd_id"] = dd_id

        # 注意：螺丝钉估值写入 dd_valuations 表，不写 analysis_records（那是雷牛牛估值图片的表）

    return result


@router.post("/parse-dd-async")
async def parse_dd_image_async(req: ParseDDRequest):
    """异步解析螺丝钉估值表图片。立即返回 task_id，后台执行解析。"""
    img_path = resolve_valuation_image(req.path, "dd_images")
    if not req.path or not img_path.is_file():
        raise HTTPException(400, f"图片路径无效: {img_path}")

    str_path = str(img_path)

    # 去重：检查是否已有正在运行的任务
    from db.dd_tasks import find_running_task, create_dd_parse_task
    existing = find_running_task(str_path, parse_type="dd")
    if existing:
        return {"task_id": existing["id"], "status": existing["status"], "dedup": True}

    task_id = create_dd_parse_task(str_path, Path(req.path).name, parse_type="dd")

    # 后台启动解析
    from scripts.dd_parse_worker import run_dd_parse
    asyncio.create_task(run_dd_parse(task_id, str_path, "dd"))

    return {"task_id": task_id, "status": "pending"}


@router.get("/parse-dd-task/{task_id}")
async def get_dd_parse_task_status(task_id: int):
    """查询螺丝钉图片解析任务状态。"""
    from db.dd_tasks import get_dd_parse_task
    task = get_dd_parse_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.post("/parse-dd-batch-async")
async def parse_dd_batch_async(req: ParseDDBatchRequest):
    """批量异步解析螺丝钉估值表图片。"""
    from db.dd_tasks import find_running_task, create_dd_parse_task
    from scripts.dd_parse_worker import run_dd_parse

    tasks = []
    for path in req.paths:
        img_path = resolve_valuation_image(path, "dd_images")
        str_path = str(img_path)
        name = Path(path).name

        # 去重
        existing = find_running_task(str_path, parse_type="dd")
        if existing:
            tasks.append({"task_id": existing["id"], "image_path": str_path, "status": existing["status"], "dedup": True})
            continue

        if not path or not img_path.is_file():
            tasks.append({"task_id": None, "image_path": str_path, "status": "error", "error": "文件不存在"})
            continue

        task_id = create_dd_parse_task(str_path, name, parse_type="dd")
        asyncio.create_task(run_dd_parse(task_id, str_path, "dd"))
        tasks.append({"task_id": task_id, "image_path": str_path, "status": "pending"})

    return {"tasks": tasks}


# ── 六亿估值 ──────────────────────────────────────

@router.post("/parse-liuyi")
async def parse_liuyi_image(req: ParseDDRequest):
    """解析六亿估值表图片（多指数表格数据，格式与螺丝钉一致）。"""
    img_path = resolve_valuation_image(req.path, "liuyi_images")
    if not req.path or not img_path.is_file():
        raise HTTPException(400, f"图片路径无效: {img_path}")

    liuyi_parser = LiuyiImageParser(model_type=req.model_type)
    result = liuyi_parser.parse(str(img_path))
    result["source_path"] = str(img_path)

    if result.get("ok"):
        rel_path, image_url = valuation_image_location(img_path)
        liuyi_id = save_liuyi_valuation(result, rel_path, image_url)
        result["liuyi_id"] = liuyi_id

    return result


@router.post("/parse-liuyi-async")
async def parse_liuyi_image_async(req: ParseDDRequest):
    """异步解析六亿估值表图片。立即返回 task_id，后台执行解析。"""
    img_path = resolve_valuation_image(req.path, "liuyi_images")
    if not req.path or not img_path.is_file():
        raise HTTPException(400, f"图片路径无效: {img_path}")

    str_path = str(img_path)

    from db.dd_tasks import find_running_task, create_dd_parse_task
    existing = find_running_task(str_path, parse_type="liuyi")
    if existing:
        return {"task_id": existing["id"], "status": existing["status"], "dedup": True}

    task_id = create_dd_parse_task(str_path, Path(req.path).name, parse_type="liuyi")

    from scripts.dd_parse_worker import run_dd_parse
    asyncio.create_task(run_dd_parse(task_id, str_path, "liuyi"))

    return {"task_id": task_id, "status": "pending"}


@router.post("/parse-liuyi-batch-async")
async def parse_liuyi_batch_async(req: ParseDDBatchRequest):
    """批量异步解析六亿估值表图片。"""
    from db.dd_tasks import find_running_task, create_dd_parse_task
    from scripts.dd_parse_worker import run_dd_parse

    tasks = []
    for path in req.paths:
        img_path = resolve_valuation_image(path, "liuyi_images")
        str_path = str(img_path)
        name = Path(path).name

        existing = find_running_task(str_path, parse_type="liuyi")
        if existing:
            tasks.append({"task_id": existing["id"], "image_path": str_path, "status": existing["status"], "dedup": True})
            continue

        if not path or not img_path.is_file():
            tasks.append({"task_id": None, "image_path": str_path, "status": "error", "error": "文件不存在"})
            continue

        task_id = create_dd_parse_task(str_path, name, parse_type="liuyi")
        asyncio.create_task(run_dd_parse(task_id, str_path, "liuyi"))
        tasks.append({"task_id": task_id, "image_path": str_path, "status": "pending"})

    return {"tasks": tasks}


@router.get("/liuyi/list")
async def list_liuyi_valuations_api():
    """列出所有六亿估值记录。"""
    return {"records": list_liuyi_valuations()}


@router.get("/liuyi/indexes")
async def get_liuyi_indexes(liuyi_id: int | None = Query(None)):
    """获取六亿估值表中的指数列表。

    参数:
        liuyi_id: 六亿估值记录 ID（可选，默认最新）

    返回:
        六亿估值表的完整数据，包含市场温度和指数列表
    """
    if liuyi_id:
        record = get_liuyi_valuation(liuyi_id)
    else:
        records = list_liuyi_valuations()
        if records:
            record = get_liuyi_valuation(records[0]["id"])
        else:
            record = None

    if not record:
        raise HTTPException(404, "未找到六亿估值记录")

    parsed_data = record.get("parsed_data")

    return {
        "liuyi_id": record["id"],
        "update_date": record.get("update_date"),
        "market_temperature": record.get("market_temperature"),
        "index_count": record.get("index_count"),
        "image_url": record.get("image_url"),
        "indexes": parsed_data.get("data", []) if parsed_data else [],
    }


@router.get("/liuyi/{liuyi_id}")
async def get_liuyi_valuation_api(liuyi_id: int):
    """获取单条六亿估值记录详情。"""
    record = get_liuyi_valuation(liuyi_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    return record


# ── 指数列表 ──────────────────────────────────────

@router.get("/indexes")
async def list_indexes():
    """列出所有有估值数据的指数。"""
    return {"indexes": list_valuation_indexes()}


@router.get("/freshness")
async def index_freshness():
    """所有估值指数的数据新鲜度。"""
    return {"indexes": list_index_freshness()}


@router.post("/refresh-prices")
async def refresh_index_prices():
    """用实时行情刷新指数最新价。"""
    ssl._create_default_https_context = ssl._create_unverified_context
    import akshare as ak

    freshness = list_index_freshness()
    updated = 0
    errors = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        # 2026-07-13 性能优化：akshare 同步调用改用 to_thread，避免阻塞事件循环
        spot_df = await asyncio.to_thread(ak.stock_zh_index_spot_sina)
        conn = _get_conn()
        for idx in freshness:
            code = idx["index_code"]
            base = code.replace(".SZ", "").replace(".SH", "").replace(".CSI", "")
            for prefix in ["sh", "sz"]:
                sina_code = f"{prefix}{base}"
                match = spot_df[spot_df["代码"] == sina_code]
                if not match.empty:
                    row = match.iloc[0]
                    latest_price = float(row.get("最新价", 0))
                    change_pct = float(row.get("涨跌幅", 0))
                    if latest_price > 0:
                        conn.execute("""
                            UPDATE index_valuations SET current_point=?, change_pct=?
                            WHERE index_code=? AND snapshot_date=?
                        """, (latest_price, change_pct, code, today))
                        updated += 1
                    break
        conn.commit()
        conn.close()
    except Exception as e:
        errors.append(str(e))

    return {"ok": True, "updated": updated, "errors": errors}


# ── 螺丝钉估值 ──────────────────────────────────────

@router.get("/dd/list")
async def list_dd_valuations_api():
    """列出所有螺丝钉估值记录。"""
    return {"records": list_dd_valuations()}


@router.get("/dd/indexes")
async def get_dd_indexes(dd_id: int = None):
    """获取螺丝钉估值表中的指数列表。

    参数:
        dd_id: 螺丝钉记录 ID（可选，默认最新）

    返回:
        螺丝钉估值表的完整数据，包含市场温度和指数列表
    """
    if dd_id:
        record = get_dd_valuation(dd_id)
    else:
        # 获取最新记录
        records = list_dd_valuations()
        record = get_dd_valuation(records[0]["id"]) if records else None

    if not record:
        raise HTTPException(404, "未找到螺丝钉估值记录")

    # 解析 raw_json
    parsed_data = None
    if record.get("raw_json"):
        try:
            import json
            parsed_data = json.loads(record["raw_json"])
        except Exception:
            pass

    return {
        "dd_id": record["id"],
        "update_date": record.get("update_date"),
        "market_temperature": record.get("market_temperature"),
        "index_count": record.get("index_count"),
        "image_url": record.get("image_url"),
        "indexes": parsed_data.get("data", []) if parsed_data else [],
    }


@router.get("/dd/{dd_id}")
async def get_dd_valuation_api(dd_id: int):
    """获取单条螺丝钉估值记录详情。"""
    record = get_dd_valuation(dd_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    return record


# ── 统一查询 ──────────────────────────────────────

@router.get("/unified")
async def unified_valuation_query(
    index_code: str = None,
    metric_type: str = "市盈率",
    source: str = "all",
    max_days: int = 7
):
    """统一估值查询接口（智能降级）。

    参数:
        index_code: 指数代码（可选，不传则返回所有指数）
        metric_type: 指标类型（默认市盈率）
        source: 数据来源筛选（all/manual/akshare/螺丝钉）
        max_days: 最大有效天数（默认 7 天）

    返回:
        合并后的估值数据列表，包含数据来源和时效性信息
    """
    if index_code:
        # 查询单个指数
        result = get_best_valuation(index_code, metric_type, query_source="valuation_page")
        if not result:
            raise HTTPException(404, f"未找到 {index_code} 的估值数据")
        return {"indexes": [result]}

    # 查询所有指数
    indexes = list_valuation_indexes()
    results = []

    for idx in indexes:
        code = idx.get("index_code")
        if not code:
            continue

        # 获取最佳估值数据
        best = get_best_valuation(code, metric_type, query_source="valuation_page")
        if best:
            # 按来源筛选
            if source != "all" and best.get("data_source") != source:
                continue
            results.append(best)

    # 获取市场温度
    market_temp = get_latest_market_temperature()

    return {
        "indexes": results,
        "market_temperature": market_temp.get("market_temperature") if market_temp else None,
        "market_temperature_status": market_temp.get("status") if market_temp else None,
        "summary": {
            "total": len(results),
            "valid": len([r for r in results if not r.get("is_expired")]),
            "expired": len([r for r in results if r.get("is_expired")]),
            "degraded": len([r for r in results if r.get("degraded")]),
        }
    }


# ── 市场温度 ──────────────────────────────────────

@router.get("/market-temperature")
async def get_market_temperature():
    """获取最新市场温度。"""
    result = get_latest_market_temperature()
    if not result:
        return {"temperature": None, "status": "未知", "description": "暂无市场温度数据"}
    return result


# ── 指数代码映射 ──────────────────────────────────────

@router.get("/code-mappings")
async def list_code_mappings():
    """列出所有指数代码映射。"""
    return {"mappings": list_index_code_mappings()}


@router.post("/code-mappings")
async def create_code_mapping(index_code: str, index_name: str, aliases: list = None, sina_code: str = None):
    """创建或更新指数代码映射。"""
    save_index_code_mapping(index_code, index_name, aliases, sina_code)
    return {"ok": True, "index_code": index_code}


# ── 指数历史 ──────────────────────────────────────

@router.get("/history/{index_code}")
async def get_history(index_code: str, days: int = 30, metric_type: str = None):
    """查询某指数的估值历史。"""
    history = get_valuation_history(index_code, days, metric_type)
    latest = get_latest_valuation(index_code, metric_type)
    return {
        "index_code": index_code,
        "latest": latest,
        "history": history,
    }


# ── 超性价比识别 ──────────────────────────────────────


# 超性价比：数据时效过滤阈值（latest_date 距今超过 N 天则视为过期，不进性价比池）
SUPER_VALUE_MAX_STALE_DAYS = 10
# 超性价比：metric_type 优先级（行业特性适配，重资产行业看 PB，轻资产行业看 PE）
_METRIC_PRIORITY = ["市盈率", "市净率", "市销率", "市销率TTM", "股息率"]


def _pick_best_metric_for_index(conn, index_code: str) -> str | None:
    """为单个指数挑选最合适的 metric_type。

    选择规则（解决「PE 断更被当最新」「重资产行业只有 PB 被漏掉」两类问题）：
      1. 候选 metric_type 必须有 ≥3 条数据
      2. 在候选中，按 latest_date 距今天数升序（越新越好）
      3. latest_date 相同时，按 _METRIC_PRIORITY 优先级排序（PE > PB > PS > 股息率）
      4. 如果最新 metric 距今 > SUPER_VALUE_MAX_STALE_DAYS，尝试回退到次新 metric（前提是它数据更新）
      5. 全部过期则返回 None（由调用方跳过该指数）

    案例：中证白酒 399997 PE 最新 2026-07-06（断更），PB 最新 2026-07-28（连续）
         → 返回 "市净率"，避免用过期 PE 当"最新"
    """
    rows = conn.execute("""
        SELECT metric_type, MAX(snapshot_date) as latest_date, COUNT(*) as cnt
        FROM index_valuations
        WHERE index_code = ? AND percentile IS NOT NULL
        GROUP BY metric_type
    """, (index_code,)).fetchall()

    if not rows:
        return None

    today = date.today()
    candidates = []
    for r in rows:
        d = dict(r)
        if d["cnt"] < 3:
            continue
        try:
            latest_dt = datetime.strptime(d["latest_date"], "%Y-%m-%d").date()
            stale_days = (today - latest_dt).days
        except (ValueError, TypeError):
            continue
        prio = _METRIC_PRIORITY.index(d["metric_type"]) if d["metric_type"] in _METRIC_PRIORITY else 99
        candidates.append({
            "metric_type": d["metric_type"],
            "latest_date": d["latest_date"],
            "stale_days": stale_days,
            "priority": prio,
            "cnt": d["cnt"],
        })

    if not candidates:
        return None

    # 按 stale_days 升序 → priority 升序，优先选最新且优先级高的 metric
    candidates.sort(key=lambda x: (x["stale_days"], x["priority"]))
    return candidates[0]["metric_type"]


def _scan_super_value_impl(min_score: float = 40, top_n: int | None = None) -> dict:
    """生成同口径候选快照；两个深度入口共享此服务。"""
    from services.valuation.opportunity_screener import scan_snapshot
    conn = _get_conn()
    try:
        return scan_snapshot(conn, min_score, top_n, max_stale_days=SUPER_VALUE_MAX_STALE_DAYS)
    finally:
        conn.close()


@router.get("/super-value")
async def get_super_value_indexes():
    """扫描所有指数的历史估值数据，识别超性价比指数（HTTP 接口，委托 _scan_super_value_impl）。

    增强版（2026-07-29）：
      - 多 metric_type 支持：每个指数按数据时效自动选 PE/PB/PS/股息率
      - 数据时效过滤：latest_date 距今 >10 天的指数直接跳过
      - 透出 metric_type 字段，前端标注 [PE]/[PB]

    同机构同指标独立构建历史，优先最新可用序列；无分位数据只作覆盖参考。
    """
    return _scan_super_value_impl()


# ── 增强策略分析 ──────────────────────────────────────


@router.post("/super-value-deep")
async def get_super_value_deep():
    """超性价比深度解读 — 规则筛选 + Agent 编排（在线补数据 + 预测信号 + LLM 综合解读）。

    流程：
    1. 调 _scan_super_value_impl 获取规则评分候选
    2. 对 top_n 候选：库内数据过期(>threshold_days) → 调 query_online_valuation 补最新
    3. 对 top_n 候选：调 query_valuation_forecast 获取均值回归/极值预警信号
    4. LLM 综合三方数据输出深度解读（真低估/价值陷阱/催化剂/操作建议/置信度）

    开关：agent.valuation_screener_enabled（默认 false）。关闭时降级返回纯规则评分。
    """
    from db.config import get_config_bool, get_config_int, get_config_float
    from services.llm_service import _call_llm, MODEL_AUX

    # 1. 检查开关
    if not get_config_bool("agent.valuation_screener_enabled", False):
        # 开关关闭：降级返回纯规则评分（兼容前端）
        return {**_scan_super_value_impl(), "enabled": False, "status": "disabled",
                "deep_analysis": [], "agent_meta": {"enabled": False, "status": "disabled",
                "note": "估值机会筛选官已关闭，当前仅展示规则候选"}}

    from db import get_analysis_agent_by_name
    from routers.analysis._agent_contract import require_analysis_agent, parse_analysis_response
    from services.valuation.opportunity_screener import enrich_candidate, validate_interpretations
    from db.valuations import fetch_online_valuation
    from services.valuation_forecast import mean_reversion_analysis, extreme_warning

    try:
        agent = require_analysis_agent("估值机会筛选官", get_analysis_agent_by_name)
    except HTTPException:
        # 部署尚未单独配置筛选官时，复用已有增强策略 Agent 的数据库提示词；不硬编码新 prompt。
        agent = require_analysis_agent("增强策略分析师", get_analysis_agent_by_name)
        agent = {**agent, "agent_name": "估值机会筛选官"}
    start_time = time.time()
    top_n = max(1, min(30, get_config_int("agent.valuation_screener_top_n", 10)))
    online_threshold_days = get_config_int("agent.valuation_screener_online_threshold_days", 3)
    scan_result = _scan_super_value_impl()
    opportunities = scan_result["opportunities"]
    tools_called = ["scan_super_value"]
    if not opportunities:
        return {**scan_result, "enabled": True, "status": "no_candidates", "deep_analysis": [],
                "agent_meta": {"enabled": True, "status": "no_candidates", "note": "无候选指数，跳过深度解读"}}

    semaphore = asyncio.Semaphore(4)
    async def enrich(opp):
        async with semaphore:
            return await enrich_candidate(opp, fetch_online_valuation, mean_reversion_analysis, extreme_warning,
                                          online_threshold_days=online_threshold_days)
    enriched = await asyncio.gather(*(enrich(o) for o in opportunities[:top_n]))
    enrichment_map = {o["index_code"]: o for o in enriched}
    full_opportunities = [enrichment_map.get(o["index_code"], o) for o in opportunities]
    tools_called.extend(["query_online_valuation", "query_valuation_forecast"])
    candidate_text = json.dumps({"snapshot_id": scan_result["snapshot_id"],
        "as_of_date": scan_result["as_of_date"], "cutoff_date": scan_result["cutoff_date"],
        "candidates": enriched}, ensure_ascii=False)
    # 身份及投资分析原则来自 analysis_agents；这里只定义接口输出约束。
    prompt = ("基于候选快照输出JSON，字段 deep_analysis 数组及 overall_summary 字符串。"
              "仅解读候选；每项 index_code、opportunity_type(真低估/价值陷阱/趋势性下行)、"
              "action(立即买入/分批建仓/观望/回避)、confidence(high/medium/low)、"
              "catalysts(字符串数组)、action_detail(字符串)。"
              "催化剂仅为未来条件假设，未核验前不得声称已发生或编造今日事件。"
              "区分历史评分与在线核验，观测分位变化单位为百分点，不能当价格跌幅或保证收益。\n" + candidate_text)
    llm_start = time.time()
    deep_analysis, overall_summary, warnings = [], "", []
    token_usage, status, error = 0, "success", ""
    trace_id = f"svd_{uuid.uuid4().hex[:12]}"
    try:
        from db.agent_analysis_log import create_analysis_log
        create_analysis_log(trace_id=trace_id, agent_id=agent["id"], agent_name="估值机会筛选官",
            analysis_type="super_value_deep", source_table="index_valuations", source_id=None,
            query=candidate_text[:300], input_summary=f"超性价比深度解读:{len(enriched)}只候选")
    except Exception as exc:
        logger.warning("创建深度分析日志失败: %s", exc)
    try:
        response = await asyncio.wait_for(asyncio.to_thread(lambda: _call_llm(
            caller="valuation_screener", model=MODEL_AUX,
            messages=[{"role": "system", "content": agent["system_prompt"]}, {"role": "user", "content": prompt}],
            temperature=get_config_float("llm.temperature_default", 0.3),
            max_tokens=get_config_int("llm.max_tokens_report", 8192))), timeout=120)
        _, parsed = parse_analysis_response(response, "overall_summary", "deep_analysis")
        deep_analysis, warnings = validate_interpretations(parsed.get("deep_analysis", []), enriched)
        if not deep_analysis:
            raise ValueError("模型未返回有效候选解读")
        overall_summary = parsed.get("overall_summary", "")
        if not isinstance(overall_summary, str):
            raise ValueError("overall_summary 必须是字符串")
        token_usage = response.usage.total_tokens if response.usage else 0
        if warnings:
            status = "partial"
    except Exception as exc:
        logger.warning("[super-value-deep] LLM 解读失败: %s", exc)
        status, error = "error", str(exc)
        deep_analysis = []
        overall_summary = f"LLM 解读失败: {exc}"
    try:
        from db.agent_analysis_log import complete_analysis_log
        complete_analysis_log(trace_id=trace_id, status="done" if status == "success" else status,
            token_usage=token_usage, duration_ms=int((time.time()-llm_start)*1000), error_msg=error or None)
    except Exception as exc:
        logger.warning("完成深度分析日志失败: %s", exc)
    return {**scan_result, "opportunities": full_opportunities,
            "enabled": True, "status": status, "error": error or None,
            "deep_analysis": deep_analysis, "overall_summary": overall_summary,
            "validation_warnings": warnings,
            "agent_meta": {"agent_name": "估值机会筛选官", "enabled": True, "status": status,
                "tools_called": tools_called, "analyzed_count": len(enriched),
                "online_queries": sum(o["current_verification"]["status"] == "available" for o in enriched),
                "duration_ms": int((time.time()-start_time)*1000),
                "llm_duration_ms": int((time.time()-llm_start)*1000)}}


@router.get("/enhanced-strategy")
async def get_enhanced_strategy():
    """增强策略分析 — 结合估值趋势 + 新闻/政策 + LLM 推理，判断「是机会还是陷阱」。

    输出每个低估指数的：
    - 机会类型（真低估/价值陷阱/趋势性下行）
    - 预期恢复时间（短/中/长期）
    - 催化剂（政策/业绩/资金轮动）
    - 建议操作（立即买入/分批建仓/观望/回避）
    """
    from services.llm_service import _call_llm, MODEL_AUX
    from db import get_config_float, get_config_int

    from db import get_analysis_agent_by_name
    from routers.analysis._agent_contract import require_analysis_agent, parse_analysis_response
    agent = require_analysis_agent("增强策略分析师", get_analysis_agent_by_name)
    agent_id = agent["id"]
    system_prompt = agent["system_prompt"]

    scan_result = _scan_super_value_impl()
    candidates = [{**c, "code": c["index_code"], "name": c["index_name"],
                   "percentile": c["current_percentile"],
                   "consecutive_drop": c["consecutive_drop_days"]}
                  for c in scan_result["opportunities"]]

    # 2.5 为每个候选指数附加 PE/PB 背离预警和数据覆盖年限
    from services.valuation import check_pe_pb_divergence, get_index_history_years, get_history_years_warning
    for c in candidates:
        # PE/PB 背离检查
        divergence = check_pe_pb_divergence(c["code"])
        if divergence:
            c["valuation_warnings"] = [divergence]
            c["recommended_metric"] = divergence["recommended_metric"]
        else:
            c["valuation_warnings"] = []
            c["recommended_metric"] = None

        # 数据覆盖年限
        years = get_index_history_years(c["code"])
        c["history_years"] = years
        years_warning = get_history_years_warning(years)
        if years_warning:
            c["valuation_warnings"].append({
                "type": "short_history",
                "level": years_warning["level"],
                "message": years_warning["message"],
            })

    if not candidates:
        return {**scan_result, "enabled": True, "strategies": [], "status": "no_candidates", "message": "当前没有达到规则阈值的低估候选"}

    # 3. 获取新闻和宏观数据（用于 LLM 判断催化剂）
    news_text = ""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        raw = await asyncio.wait_for(asyncio.to_thread(
            lambda: mcp.call_tool("SearchFinancialNews", {"keyword": "A股 政策 板块", "pageSize": 5})
        ), timeout=20)
        if isinstance(raw, dict):
            items = []
            for c in raw.get("content", []):
                if c.get("type") == "text":
                    parsed = json.loads(c["text"])
                    if parsed.get("success") and parsed.get("data", {}).get("items"):
                        for item in parsed["data"]["items"]:
                            published = item.get("publish_time") or item.get("publishTime") or item.get("date") or item.get("time") or "日期缺失"
                            url = item.get("url") or item.get("link") or "链接缺失"
                            items.append(f"- {item.get('title', '')} ({published}, {url}): {item.get('summary', '')[:80]}")
            news_text = "\n".join(items[:5])
    except Exception:
        news_text = "暂无新闻"

    # 4. 检索知识库（获取相关投资知识）
    knowledge_context = ""
    try:
        from db.knowledge import search_knowledge
        # 搜索与低估指数相关的知识
        knowledge_results = await asyncio.to_thread(search_knowledge, "低估 估值 百分位 价值投资", limit=5)
        if knowledge_results:
            knowledge_lines = []
            for k in knowledge_results:
                knowledge_lines.append(f"- 【{k['title']}】{k['content'][:200]}")
            knowledge_context = "\n\n## 参考知识库\n" + "\n".join(knowledge_lines)
    except Exception as e:
        logger.warning(f"知识库检索失败: {e}")

    # 分析原则来自已配置 Agent；此处只构造候选证据和结构契约。
    candidate_text = json.dumps({"snapshot_id": scan_result["snapshot_id"],
        "as_of_date": scan_result["as_of_date"], "cutoff_date": scan_result["cutoff_date"],
        "candidates": candidates}, ensure_ascii=False)
    prompt = ("输出JSON，字段 strategies 数组及 overall_summary 字符串。每项只解读候选代码："
              "index_code、opportunity_type(真低估/价值陷阱/趋势性下行)、"
              "action(立即买入/分批建仓/观望/回避)、confidence(high/medium/low)、"
              "recovery_time(短期(1-3月)/中期(3-12月)/长期(1年+))、"
              "catalysts与risk_factors字符串数组、action_detail字符串。"
              "催化剂仅为未来条件假设，未核验前不得声称已发生或编造今日事件。"
              "指标分位变化为百分点，并非价格变化，低估不保证反转。\n"
              + candidate_text + "\n新闻证据：\n" + news_text + knowledge_context)

    # 5. 加载 agent 配置
    from db import get_analysis_agent_by_name, create_analysis_history
    from db.agents import create_agent_run

    full_prompt = system_prompt + "\n\n---\n\n" + prompt

    # 6. 调用 LLM
    llm_start = time.time()
    llm_status = "success"
    tokens = 0
    llm_error_msg = ""
    # 接入统一日志（running）
    _es_trace_id = f"log_{uuid.uuid4().hex[:12]}"
    try:
        from db.agent_analysis_log import create_analysis_log, complete_analysis_log, update_analysis_log_source
        create_analysis_log(
            trace_id=_es_trace_id, agent_id=agent_id, agent_name="增强策略分析师",
            analysis_type="enhanced_strategy", source_table="analysis_history",
            source_id=None, query=candidate_text[:300],
            input_summary=f"增强策略:{len(candidates)}只低估指数",
        )
    except Exception as _e:
        logger.warning(f"create_analysis_log 失败: {_e}")
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: _call_llm(
                caller="enhanced_strategy",
                model=MODEL_AUX,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=get_config_float("llm.temperature_default", 0.3),
                max_tokens=get_config_int("llm.max_tokens_report", 8192),
            )),
            timeout=120,
        )
        content, parsed = parse_analysis_response(response, "overall_summary", "strategies")
        from services.valuation.opportunity_screener import validate_interpretations
        strategies, validation_warnings = validate_interpretations(parsed.get("strategies", []), candidates)
        if not strategies:
            raise ValueError("模型未返回有效候选策略")
        if not isinstance(parsed.get("overall_summary", ""), str):
            raise ValueError("overall_summary 必须是字符串")
        parsed = {"strategies": strategies, "overall_summary": parsed.get("overall_summary", ""),
                  "snapshot_id": scan_result["snapshot_id"], "as_of_date": scan_result["as_of_date"],
                  "cutoff_date": scan_result["cutoff_date"], "candidate_snapshot": scan_result,
                  "validation_warnings": validation_warnings}
        if validation_warnings:
            llm_status = "partial"
        tokens = response.usage.total_tokens if response.usage else 0
    except Exception as e:
        logger.warning(f"增强策略 LLM 分析失败: {e}")
        parsed = {"strategies": [], "overall_summary": f"分析失败: {e}"}
        llm_status = "error"
        llm_error_msg = str(e)
        content = ""

    llm_duration = int((time.time() - llm_start) * 1000)

    # 7. 记录分析历史 + agent_runs
    _es_history_id = None
    try:
        from db._conn import _get_conn as _es_get_conn
        _es_conn = _es_get_conn()
        _es_cur = _es_conn.execute(
            "INSERT INTO analysis_history (agent_id, agent_name, prompt_used, news_context, result, token_usage, status, error_msg) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id or 0, "增强策略分析师", system_prompt[:500], news_text[:500],
             json.dumps(parsed, ensure_ascii=False), tokens, llm_status,
             llm_error_msg[:500] if llm_status == "error" else ""),
        )
        _es_conn.commit()
        _es_history_id = _es_cur.lastrowid
        _es_conn.close()
    except Exception as e:
        logger.warning(f"记录增强策略分析历史失败: {e}")
        if llm_status in ("success", "partial"):
            try:
                complete_analysis_log(trace_id=_es_trace_id, status="error", duration_ms=llm_duration, error_msg=str(e))
            except Exception:
                pass
            raise HTTPException(500, f"增强策略分析历史保存失败: {e}") from e

    # 回补 source_id 到统一日志
    if _es_history_id:
        try:
            update_analysis_log_source(_es_trace_id, _es_history_id)
        except Exception as _e:
            logger.warning(f"update_analysis_log_source 失败: {_e}")

    try:
        create_agent_run(
            conversation_id=0, message_id=0,
            agent_key="enhanced_strategy", agent_name="增强策略分析师",
            query=candidate_text[:500],
            result=parsed.get("overall_summary", "")[:500],
            duration_ms=llm_duration, status=llm_status,
            trace_id=_es_trace_id,  # 关联 analysis_log，用于详情页 fallback 查询
        )
    except Exception as e:
        logger.warning(f"记录 agent_run 失败: {e}")

    # 完成统一日志
    try:
        complete_analysis_log(
            trace_id=_es_trace_id,
            status="done" if llm_status == "success" else llm_status,
            duration_ms=llm_duration,
            token_usage=tokens,
            error_msg=llm_error_msg if llm_status == "error" else None,
        )
    except Exception as _e:
        logger.warning(f"complete_analysis_log 失败: {_e}")

    if llm_status == "error":
        raise HTTPException(500, f"增强策略分析失败: {llm_error_msg}")

    # 8. 附加原始数据和风险预警到结果
    strategy_map = {s.get("index_code", ""): s for s in parsed.get("strategies", [])}
    for c in candidates:
        if c["code"] in strategy_map:
            s = strategy_map[c["code"]]
            s["raw_data"] = c
            # 提升关键字段到顶层，方便前端直接使用
            if c.get("valuation_warnings"):
                s["valuation_warnings"] = c["valuation_warnings"]
            if c.get("recommended_metric"):
                s["recommended_metric"] = c["recommended_metric"]
            if c.get("history_years"):
                s["history_years"] = c["history_years"]

    return {
        **scan_result,
        "enabled": True,
        "status": "partial" if validation_warnings else "success",
        "validation_warnings": validation_warnings,
        "strategies": parsed.get("strategies", []),
        "overall_summary": parsed.get("overall_summary", ""),
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "candidates_count": len(candidates),
        "agent_name": "增强策略分析师",
        "token_usage": tokens,
    }


# ── 指数信息路由（挂在 /api/index-info/ 下）────────────────────

INDEX_INFO_DICT = {
    "000300": "沪深300指数由沪深A股中规模大、流动性好的最具代表性的300只股票组成，于2005年4月8日正式发布，以综合反映沪深A股市场整体表现。沪深300是A股市场最核心的宽基指数，常被用作业绩基准和指数基金跟踪标的。",
    "000905": "中证500指数由A股中剔除沪深300指数成份股后，总市值排名靠前的500只股票组成，反映中小市值上市公司的整体表现。与沪深300形成互补，是衡量中小盘股走势的重要指标。",
    "000016": "上证50指数由沪市A股中规模大、流动性好的最具代表性的50只股票组成，主要覆盖金融、能源等大盘蓝筹股，反映沪市最具影响力的一批龙头企业的整体表现。",
    "399006": "创业板指由创业板中市值大、流动性好的100只股票组成，代表创业板市场核心资产。创业板以成长型创新企业为主，集中在新能源、医药、TMT等高景气赛道。",
    "399001": "深证成指从深交所上市股票中选取500只代表性的股票作为样本，覆盖主板、中小板和创业板，是衡量深市整体表现的核心指标。",
    "000688": "科创50指数由科创板中市值大、流动性好的50只股票组成，集中体现科创板龙头企业的表现，行业以半导体、生物医药、新能源等硬科技为主。",
    "000852": "中证1000指数由A股中剔除沪深300和中证500成份股后，规模偏小且流动性好的1000只股票组成，是小盘股的代表性指数。",
    "399303": "国证2000指数由深交所市值排名1001-3000的股票组成，覆盖小盘股，是比中证1000更下沉的小盘股指数。",
    "000015": "红利指数由沪市A股中现金股息率高、分红稳定、具有一定规模及流动性的50只股票组成，反映高红利股票的整体表现，适合追求稳定分红收益的投资者。",
    "931009": "中证转债指数选取沪深交易所上市的可转换公司债券作为样本，反映可转债市场的整体表现。可转债兼具债券和股票特性，是攻守兼备的投资品种。",
    "HSI": "恒生指数由港交所上市的市值最大及成交最活跃的50只股票组成，是香港股市最重要的指标，涵盖金融、地产、科技等核心行业。",
    "HSTECH": "恒生科技指数由港交所上市的30只最大型科技企业股票组成，涵盖互联网、软件、半导体、消费电子等领域，被称为'港版纳斯达克'。",
}

index_info_router = APIRouter(prefix="/api/index-info", tags=["index-info"])


@index_info_router.get("/{index_code}")
async def get_index_info_api(index_code: str, index_name: str = ""):
    """获取指数简介信息。优先查缓存（5天有效），其次查静态字典，最后用 LLM 生成。"""
    cached = get_index_info(index_code)
    if cached:
        try:
            created = datetime.strptime(cached["created_at"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() - created < timedelta(days=5):
                return {"index_code": index_code, "info": cached["info"], "source": "cache"}
        except (ValueError, KeyError):
            pass

    clean_code = index_code.split('.')[0] if '.' in index_code else index_code
    lookup_code = clean_code if clean_code in INDEX_INFO_DICT else index_code
    if lookup_code in INDEX_INFO_DICT:
        info = INDEX_INFO_DICT[lookup_code]
        save_index_info(index_code, index_name, info)
        return {"index_code": index_code, "info": info, "source": "dict"}

    try:
        from services.llm_service import _call_llm, call_llm_async, MODEL_AUX
        prompt = f"请用2-3句话简洁介绍「{index_name or index_code}」这个股票指数，包括它由哪些股票组成、覆盖什么行业、适合什么样的投资者。不要使用markdown格式，直接输出纯文本。"
        resp = await call_llm_async(caller="valuation", messages=[{"role": "user", "content": prompt}], model=MODEL_AUX, max_tokens=get_config_int('llm.max_tokens_valuation_summary', 800))
        info = resp.choices[0].message.content if resp and resp.choices else ""
        if info:
            save_index_info(index_code, index_name, info.strip())
            return {"index_code": index_code, "info": info.strip(), "source": "llm"}
    except Exception as e:
        logging.warning(f"LLM生成指数信息失败: {e}")

    return {"index_code": index_code, "info": "", "source": "none"}


# ── 估值查询闭环监控 ──────────────────────────────────────


@router.get("/query-stats")
async def valuation_query_stats_api(days: int = 7):
    """获取估值查询监控统计（数据源命中率、在线兜底次数、失败指数、平均耗时）。

    用于前端估值页面的「数据健康」卡片。
    """
    if days < 1 or days > 90:
        days = 7
    return get_valuation_query_stats(days)


@router.get("/online-query/{index_code}")
async def online_valuation_query_api(index_code: str, metric_type: str = "市盈率"):
    """手动触发在线估值查询（akshare → 天天基金）。

    前端估值页「在线查询」按钮调用此接口，
    绕过本地表直接查在线渠道，结果不入库。
    """
    from db.valuations import _online_fallback
    from datetime import datetime as _dt
    result = _online_fallback(index_code, metric_type, _dt.now(), "valuation_page_online_btn")
    if not result:
        return {"ok": False, "error": f"在线查询失败：{index_code} 无可用在线数据源"}
    return {"ok": True, "data": result}
