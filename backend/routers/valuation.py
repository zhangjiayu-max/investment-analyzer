"""估值数据路由 — /api/valuation/*"""

import asyncio
import json
import logging
import re
import ssl
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException

from config import IMAGES_DIR, VALUATION_IMAGES_DIR, DD_IMAGES_DIR
from db.valuations import (
    save_valuation, get_valuation_history, get_latest_valuation,
    list_valuation_indexes, list_index_freshness, get_index_info, save_index_info,
    save_dd_valuation, list_dd_valuations, get_dd_valuation,
    get_best_valuation, get_latest_market_temperature, get_latest_dd_valuation_for_index,
    list_index_code_mappings, save_index_code_mapping,
)
from db._conn import _get_conn
from image_parser import DDImageParser

logger = logging.getLogger(__name__)
from models.valuations import ParseAndSaveRequest, ParseBatchRequest, ParseDDRequest, ParseDDBatchRequest
from services.valuation_parser import parse_single_valuation

router = APIRouter(prefix="/api/valuation", tags=["valuation"])


# ── 解析相关 ──────────────────────────────────────

@router.post("/parse")
async def parse_and_save(req: ParseAndSaveRequest):
    """解析图片并存储估值数据。"""
    result = parse_single_valuation(req.path, req.model_type, req.source_url, req.snapshot_date)
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return result["data"]


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
    img_path = Path(req.path)
    if not img_path.is_absolute():
        for base in [DD_IMAGES_DIR, IMAGES_DIR, VALUATION_IMAGES_DIR]:
            candidate = base / img_path
            if candidate.exists():
                img_path = candidate
                break
        else:
            img_path = DD_IMAGES_DIR / req.path
    if not req.path or not img_path.exists():
        raise HTTPException(400, f"图片路径无效: {img_path}")

    dd_parser = DDImageParser(model_type=req.model_type)
    result = dd_parser.parse(str(img_path))
    result["source_path"] = str(img_path)

    # 解析成功后持久化到数据库
    if result.get("ok"):
        # 计算相对路径
        try:
            rel_path = f"data/dd_images/{img_path.relative_to(DD_IMAGES_DIR)}"
        except ValueError:
            rel_path = f"data/dd_images/{req.path}"
        image_url = f"/static/dd_images/{img_path.relative_to(DD_IMAGES_DIR)}"
        dd_id = save_dd_valuation(result, rel_path, image_url)
        result["dd_id"] = dd_id

        # 注意：螺丝钉估值写入 dd_valuations 表，不写 analysis_records（那是雷牛牛估值图片的表）

    return result


@router.post("/parse-dd-async")
async def parse_dd_image_async(req: ParseDDRequest):
    """异步解析螺丝钉估值表图片。立即返回 task_id，后台执行解析。"""
    img_path = Path(req.path)
    if not img_path.is_absolute():
        for base in [DD_IMAGES_DIR, IMAGES_DIR, VALUATION_IMAGES_DIR]:
            candidate = base / img_path
            if candidate.exists():
                img_path = candidate
                break
        else:
            img_path = DD_IMAGES_DIR / req.path
    if not req.path or not img_path.exists():
        raise HTTPException(400, f"图片路径无效: {img_path}")

    str_path = str(img_path)

    # 去重：检查是否已有正在运行的任务
    from db.dd_tasks import find_running_task, create_dd_parse_task
    existing = find_running_task(str_path)
    if existing:
        return {"task_id": existing["id"], "status": existing["status"], "dedup": True}

    task_id = create_dd_parse_task(str_path, Path(req.path).name, parse_type="dd")

    # 后台启动解析
    from dd_parse_worker import run_dd_parse
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
    from dd_parse_worker import run_dd_parse

    tasks = []
    for path in req.paths:
        img_path = Path(path)
        if not img_path.is_absolute():
            for base in [DD_IMAGES_DIR, IMAGES_DIR, VALUATION_IMAGES_DIR]:
                candidate = base / img_path
                if candidate.exists():
                    img_path = candidate
                    break
            else:
                img_path = DD_IMAGES_DIR / path

        str_path = str(img_path)
        name = Path(path).name

        # 去重
        existing = find_running_task(str_path)
        if existing:
            tasks.append({"task_id": existing["id"], "image_path": str_path, "status": existing["status"], "dedup": True})
            continue

        if not img_path.exists():
            tasks.append({"task_id": None, "image_path": str_path, "status": "error", "error": "文件不存在"})
            continue

        task_id = create_dd_parse_task(str_path, name, parse_type="dd")
        asyncio.create_task(run_dd_parse(task_id, str_path, "dd"))
        tasks.append({"task_id": task_id, "image_path": str_path, "status": "pending"})

    return {"tasks": tasks}


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
        spot_df = ak.stock_zh_index_spot_sina()
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


@router.get("/dd/{dd_id}")
async def get_dd_valuation_api(dd_id: int):
    """获取单条螺丝钉估值记录详情。"""
    record = get_dd_valuation(dd_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    return record


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
        record = records[0] if records else None

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
        result = get_best_valuation(index_code, metric_type)
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
        best = get_best_valuation(code, metric_type)
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


@router.get("/super-value")
async def get_super_value_indexes():
    """扫描所有指数的历史估值数据，识别超性价比指数。

    数据源优先级：螺丝钉(dd_valuations) > 雷牛牛(图片解析)
    同一指数只用一个数据源，避免混用导致百分位不一致。

    评分维度：
    - 当前估值水位（30分）：percentile 越低越好
    - 连续下跌天数（25分）：连续 N 天 percentile 下降
    - 近期跌幅（20分）：最近 7 天 percentile 降幅
    - Z-score 偏离（15分）：zscore 越低越低估
    - 趋势加速（10分）：近期跌幅 > 前期跌幅
    """
    conn = _get_conn()

    # 优先用螺丝钉(dd_valuations)的数据，其次用雷牛牛(图片解析)的数据
    # 同一指数只用一个数据源，避免百分位不一致
    rows = conn.execute("""
        SELECT index_code, index_name, snapshot_date, percentile, current_value, zscore,
               source_image,
               CASE
                 WHEN source_image LIKE '%dd_%' THEN 1  -- 螺丝钉优先
                 ELSE 2  -- 雷牛牛
               END as source_priority
        FROM index_valuations
        WHERE metric_type = '市盈率' AND percentile IS NOT NULL
        ORDER BY index_code, source_priority, snapshot_date
    """).fetchall()
    conn.close()

    # 按指数分组，同一指数只用优先级最高的数据源
    from collections import defaultdict
    index_data = defaultdict(list)
    index_source = {}  # 记录每个指数使用的数据源
    for r in rows:
        d = dict(r)
        code = d["index_code"]
        try:
            d["percentile"] = float(d["percentile"]) if d["percentile"] is not None else None
            d["current_value"] = float(d["current_value"]) if d["current_value"] is not None else None
            d["zscore"] = float(d["zscore"]) if d["zscore"] is not None else None
        except (ValueError, TypeError):
            continue
        if d["percentile"] is None:
            continue
        # 确定该指数的数据源：第一次出现的 source_priority 决定了数据源
        source = "螺丝钉" if "dd_" in (d.get("source_image") or "") else "雷牛牛"
        if code not in index_source:
            index_source[code] = source
        # 只使用同一数据源的数据
        if index_source[code] == source:
            index_data[code].append(d)

    opportunities = []

    for code, records in index_data.items():
        if len(records) < 3:
            continue  # 数据太少，跳过

        name = records[0]["index_name"]
        latest = records[-1]
        current_pct = latest["percentile"]
        current_val = latest["current_value"]
        zscore = latest.get("zscore")

        # 确保是数值类型
        try:
            current_pct = float(current_pct) if current_pct is not None else None
            current_val = float(current_val) if current_val is not None else None
            zscore = float(zscore) if zscore is not None else None
        except (ValueError, TypeError):
            continue

        if current_pct is None:
            continue

        # ── 维度 1：当前估值水位（30分）──
        if current_pct < 5:
            score_valuation = 30
            level = "极度低估"
        elif current_pct < 10:
            score_valuation = 27
            level = "极度低估"
        elif current_pct < 20:
            score_valuation = 22
            level = "低估"
        elif current_pct < 30:
            score_valuation = 16
            level = "偏低"
        elif current_pct < 50:
            score_valuation = 8
            level = "适中"
        else:
            score_valuation = 0
            level = "偏高" if current_pct < 70 else "高估"

        # ── 维度 2：连续下跌天数（25分）──
        consecutive_drop = 0
        for i in range(len(records) - 1, 0, -1):
            if records[i]["percentile"] < records[i - 1]["percentile"]:
                consecutive_drop += 1
            else:
                break

        if consecutive_drop >= 6:
            score_consecutive = 25
        elif consecutive_drop >= 5:
            score_consecutive = 22
        elif consecutive_drop >= 4:
            score_consecutive = 18
        elif consecutive_drop >= 3:
            score_consecutive = 14
        elif consecutive_drop >= 2:
            score_consecutive = 10
        elif consecutive_drop >= 1:
            score_consecutive = 5
        else:
            score_consecutive = 0

        # ── 维度 3：近 7 天跌幅（20分）──
        drop_7d = 0
        if len(records) >= 7:
            pct_7d_ago = records[-7]["percentile"]
            drop_7d = pct_7d_ago - current_pct  # 正数表示下跌

        if drop_7d > 15:
            score_drop = 20
        elif drop_7d > 10:
            score_drop = 17
        elif drop_7d > 5:
            score_drop = 13
        elif drop_7d > 3:
            score_drop = 9
        elif drop_7d > 1:
            score_drop = 5
        else:
            score_drop = 0

        # ── 维度 4：Z-score 偏离（15分）──
        if zscore is not None:
            if zscore < -2:
                score_zscore = 15
            elif zscore < -1.5:
                score_zscore = 12
            elif zscore < -1:
                score_zscore = 9
            elif zscore < -0.5:
                score_zscore = 5
            else:
                score_zscore = 0
        else:
            score_zscore = 0

        # ── 维度 5：趋势加速（10分）──
        # 比较最近 3 天平均跌幅 vs 最近 7 天平均跌幅
        score_accel = 0
        drop_trend = "平稳"
        if len(records) >= 7:
            recent_3d_avg = (records[-4]["percentile"] - current_pct) / 3 if len(records) >= 4 else 0
            recent_7d_avg = drop_7d / 7
            if recent_3d_avg > recent_7d_avg * 1.3 and recent_3d_avg > 0.5:
                score_accel = 10
                drop_trend = "加速下跌"
            elif recent_3d_avg > recent_7d_avg and recent_3d_avg > 0.3:
                score_accel = 6
                drop_trend = "温和下跌"
            elif recent_3d_avg > 0:
                score_accel = 3
                drop_trend = "缓慢下跌"
            else:
                drop_trend = "企稳"

        # ── 总分 ──
        total_score = score_valuation + score_consecutive + score_drop + score_zscore + score_accel

        # 只保留 40 分以上的
        if total_score < 40:
            continue

        # 标签
        tags = []
        if consecutive_drop >= 3:
            tags.append(f"连续下跌{consecutive_drop}天")
        if current_pct < 10:
            tags.append("极度低估")
        if zscore is not None and zscore < -1:
            tags.append(f"Z-score {zscore:+.2f}")
        if drop_7d > 5:
            tags.append(f"7日跌{drop_7d:.1f}%")
        if drop_trend == "加速下跌":
            tags.append("趋势加速")

        # 摘要
        pct_str = f"{current_pct:.1f}%" if current_pct < 10 else f"{current_pct:.0f}%"
        summary_parts = [f"{name} {latest.get('current_value', '')}，百分位 {pct_str}"]
        if consecutive_drop > 0:
            summary_parts.append(f"连续 {consecutive_drop} 天走低")
        if zscore is not None and zscore < -0.5:
            summary_parts.append(f"Z-score {zscore:+.2f}")
        if drop_trend in ("加速下跌", "温和下跌"):
            summary_parts.append(f"趋势{drop_trend}")

        opportunities.append({
            "index_name": name,
            "index_code": code,
            "score": total_score,
            "score_breakdown": {
                "valuation": {"score": score_valuation, "max": 30, "detail": f"百分位{current_pct:.1f}% → {level}"},
                "consecutive": {"score": score_consecutive, "max": 25, "detail": f"连续下跌{consecutive_drop}天"},
                "drop_7d": {"score": score_drop, "max": 20, "detail": f"7日跌幅{drop_7d:.1f}%"},
                "zscore": {"score": score_zscore, "max": 15, "detail": f"Z-score {zscore:+.2f}" if zscore else "无数据"},
                "acceleration": {"score": score_accel, "max": 10, "detail": drop_trend},
            },
            "current_percentile": round(current_pct, 2),
            "current_value": current_val,
            "zscore": round(zscore, 2) if zscore is not None else None,
            "consecutive_drop_days": consecutive_drop,
            "drop_7d": round(drop_7d, 2) if drop_7d else 0,
            "drop_trend": drop_trend,
            "valuation_level": level,
            "tags": tags,
            "summary": "，".join(summary_parts),
            "latest_date": latest["snapshot_date"],
            "data_source": index_source.get(code, "未知"),
            "data_points": len(records),
        })

    # 按分数降序排列
    opportunities.sort(key=lambda x: x["score"], reverse=True)

    return {
        "opportunities": opportunities,
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_scanned": len(index_data),
        "data_range": f"{rows[0]['snapshot_date']} ~ {rows[-1]['snapshot_date']}" if rows else "无数据",
    }


# ── 增强策略分析 ──────────────────────────────────────


@router.get("/enhanced-strategy")
async def get_enhanced_strategy():
    """增强策略分析 — 结合估值趋势 + 新闻/政策 + LLM 推理，判断「是机会还是陷阱」。

    输出每个低估指数的：
    - 机会类型（真低估/价值陷阱/趋势性下行）
    - 预期恢复时间（短/中/长期）
    - 催化剂（政策/业绩/资金轮动）
    - 建议操作（立即买入/分批建仓/观望/回避）
    """
    from llm_service import _call_llm, MODEL
    from db import get_config_float, get_config_int

    conn = _get_conn()

    # 1. 获取所有指数的估值数据
    rows = conn.execute("""
        SELECT index_code, index_name, snapshot_date, percentile, current_value, zscore,
               source_image,
               CASE WHEN source_image LIKE '%dd_%' THEN 1 ELSE 2 END as source_priority
        FROM index_valuations
        WHERE metric_type = '市盈率' AND percentile IS NOT NULL
        ORDER BY index_code, source_priority, snapshot_date
    """).fetchall()
    conn.close()

    # 按指数分组，同一指数只用优先级最高的数据源
    from collections import defaultdict
    index_data = defaultdict(list)
    index_source = {}
    for r in rows:
        d = dict(r)
        code = d["index_code"]
        try:
            d["percentile"] = float(d["percentile"]) if d["percentile"] is not None else None
            d["current_value"] = float(d["current_value"]) if d["current_value"] is not None else None
            d["zscore"] = float(d["zscore"]) if d["zscore"] is not None else None
        except (ValueError, TypeError):
            continue
        if d["percentile"] is None:
            continue
        source = "螺丝钉" if "dd_" in (d.get("source_image") or "") else "雷牛牛"
        if code not in index_source:
            index_source[code] = source
        if index_source[code] == source:
            index_data[code].append(d)

    # 2. 筛选出值得分析的指数（当前百分位 < 30% 或近期跌幅 > 5%）
    candidates = []
    for code, records in index_data.items():
        if len(records) < 3:
            continue
        latest = records[-1]
        pct = latest["percentile"]
        if pct is None or pct >= 30:
            continue
        # 计算近期跌幅
        drop_7d = 0
        if len(records) >= 7:
            drop_7d = records[-7]["percentile"] - pct
        # 计算连续下跌天数
        consecutive_drop = 0
        for i in range(len(records) - 1, 0, -1):
            if records[i]["percentile"] < records[i - 1]["percentile"]:
                consecutive_drop += 1
            else:
                break
        candidates.append({
            "code": code,
            "name": latest["index_name"],
            "percentile": round(pct, 2),
            "current_value": latest["current_value"],
            "zscore": round(latest.get("zscore") or 0, 2),
            "consecutive_drop": consecutive_drop,
            "drop_7d": round(drop_7d, 2),
            "data_source": index_source.get(code, ""),
        })

    # 2.5 为每个候选指数附加 PE/PB 背离预警和数据覆盖年限
    from valuation import check_pe_pb_divergence, get_index_history_years, get_history_years_warning
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
        return {"strategies": [], "message": "当前没有明显低估的指数"}

    # 3. 获取新闻和宏观数据（用于 LLM 判断催化剂）
    news_text = ""
    try:
        from mcp.yingmi_client import get_yingmi_client
        mcp = get_yingmi_client()
        raw = await asyncio.to_thread(
            lambda: mcp.call_tool("SearchFinancialNews", {"keyword": "A股 政策 板块", "pageSize": 5})
        )
        if isinstance(raw, dict):
            items = []
            for c in raw.get("content", []):
                if c.get("type") == "text":
                    parsed = json.loads(c["text"])
                    if parsed.get("success") and parsed.get("data", {}).get("items"):
                        for item in parsed["data"]["items"]:
                            items.append(f"- {item.get('title', '')}: {item.get('summary', '')[:80]}")
            news_text = "\n".join(items[:5])
    except Exception:
        news_text = "暂无新闻"

    # 4. 检索知识库（获取相关投资知识）
    knowledge_context = ""
    try:
        from db.knowledge import search_knowledge
        # 搜索与低估指数相关的知识
        knowledge_results = search_knowledge("低估 估值 百分位 价值投资", limit=5)
        if knowledge_results:
            knowledge_lines = []
            for k in knowledge_results:
                knowledge_lines.append(f"- 【{k['title']}】{k['content'][:200]}")
            knowledge_context = "\n\n## 参考知识库\n" + "\n".join(knowledge_lines)
    except Exception as e:
        logger.warning(f"知识库检索失败: {e}")

    # 5. 构建 LLM prompt
    candidate_lines = []
    for c in candidates:
        line = (f"- {c['name']}({c['code']}): 百分位{c['percentile']}%, Z-score{c['zscore']}, "
                f"连续下跌{c['consecutive_drop']}天, 7日跌幅{c['drop_7d']}%")
        # 附加风险提示
        warnings = c.get("valuation_warnings", [])
        for w in warnings:
            line += f"\n  ⚠️ {w['message']}"
        if c.get("recommended_metric"):
            line += f"\n  📊 建议参考{c['recommended_metric']}百分位"
        if c.get("history_years") and c["history_years"] < 5:
            line += f"\n  📅 数据仅覆盖{c['history_years']}年"
        candidate_lines.append(line)
    candidate_text = "\n".join(candidate_lines)

    prompt = f"""你是一位资深的 A 股投资策略分析师。以下是一批当前估值较低的指数，请对每个指数进行深度分析。

## 分析框架

对每个指数，判断以下维度：

1. **机会类型**：
   - 真低估：基本面健康，只是短期回调，大概率会修复
   - 价值陷阱：基本面恶化（行业衰退、政策打压、盈利下滑），低估可能是合理的
   - 趋势性下行：长期逻辑变了（如消费降级、人口老龄化），短期不会反转

2. **预期恢复时间**：
   - 短期（1-3个月）：有明确催化剂（政策利好、业绩拐点、资金轮动）
   - 中期（3-12个月）：需要等待基本面改善
   - 长期（1年以上）：行业周期底部，需要耐心等待

3. **催化剂**：什么事件可能触发反弹？
   - 政策面：降准降息、行业扶持政策、财政刺激
   - 基本面：盈利拐点、库存周期见底、需求回暖
   - 资金面：北向资金流入、板块轮动、估值修复

4. **建议操作**：
   - 立即买入：极度低估 + 催化剂明确 + 基本面健康
   - 分批建仓：低估 + 催化剂不明确但基本面尚可
   - 观望：低估但基本面不确定
   - 回避：价值陷阱或趋势性下行

## 输出格式（严格JSON）

{{
  "strategies": [
    {{
      "index_name": "指数名",
      "index_code": "代码",
      "opportunity_type": "真低估|价值陷阱|趋势性下行",
      "recovery_time": "短期(1-3月)|中期(3-12月)|长期(1年+)",
      "catalysts": ["催化剂1", "催化剂2"],
      "risk_factors": ["风险因素1"],
      "action": "立即买入|分批建仓|观望|回避",
      "action_detail": "具体操作建议（2-3句话）",
      "confidence": "high|medium|low"
    }}
  ],
  "overall_summary": "一段话总结当前市场低估板块的整体情况和投资建议"
}}

## 待分析指数
{candidate_text}

## 今日新闻（供参考）
{news_text}
{knowledge_context}

请基于以上数据和知识库参考，结合你对 A 股市场的理解，给出专业分析。"""

    # 5. 加载 agent 配置
    from db import get_analysis_agent, create_analysis_history
    from db.agents import create_agent_run

    agent = None
    agent_id = None
    try:
        conn = _get_conn()
        row = conn.execute("SELECT id, system_prompt FROM analysis_agents WHERE name = ?", ("增强策略分析师",)).fetchone()
        conn.close()
        if row:
            agent_id = row["id"]
            agent = dict(row)
    except Exception:
        pass

    system_prompt = agent["system_prompt"] if agent else ""
    if not system_prompt:
        from db.analysis import DEFAULT_ENHANCED_STRATEGY_PROMPT
        system_prompt = DEFAULT_ENHANCED_STRATEGY_PROMPT

    full_prompt = system_prompt + "\n\n---\n\n" + prompt

    # 6. 调用 LLM
    llm_start = time.time()
    llm_status = "success"
    tokens = 0
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: _call_llm(
                caller="enhanced_strategy",
                model=MODEL,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=get_config_float("llm.temperature_default", 0.3),
                max_tokens=get_config_int("llm.max_tokens_report", 8192),
            )),
            timeout=120,
        )
        content = response.choices[0].message.content or "{}"
        tokens = response.usage.total_tokens if response.usage else 0
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(content)
    except Exception as e:
        logger.warning(f"增强策略 LLM 分析失败: {e}")
        parsed = {"strategies": [], "overall_summary": f"分析失败: {e}"}
        llm_status = "error"
        content = ""

    llm_duration = int((time.time() - llm_start) * 1000)

    # 7. 记录分析历史 + agent_runs
    try:
        create_analysis_history(
            agent_id=agent_id or 0,
            agent_name="增强策略分析师",
            prompt_used=system_prompt[:500],
            news_context=news_text[:500],
            result=json.dumps(parsed, ensure_ascii=False)[:2000],
            token_usage=tokens,
        )
    except Exception as e:
        logger.warning(f"记录增强策略分析历史失败: {e}")

    try:
        create_agent_run(
            conversation_id=0, message_id=0,
            agent_key="enhanced_strategy", agent_name="增强策略分析师",
            query=candidate_text[:500],
            result=parsed.get("overall_summary", "")[:500],
            duration_ms=llm_duration, status=llm_status,
        )
    except Exception as e:
        logger.warning(f"记录 agent_run 失败: {e}")

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
        "strategies": parsed.get("strategies", []),
        "overall_summary": parsed.get("overall_summary", ""),
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "candidates_count": len(candidates),
        "agent_name": "增强策略分析师",
        "token_usage": tokens,
    }
