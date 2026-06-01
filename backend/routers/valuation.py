"""估值数据路由 — /api/valuation/*"""

import asyncio
import logging
import ssl
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
from models.valuations import ParseAndSaveRequest, ParseBatchRequest, ParseDDRequest
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

        # 写 analysis_records 用于图片状态追踪
        # 螺丝钉估值图片包含多指数，用 index_name 标识避免显示"未识别"
        update_date = result.get("update_date", "")
        index_count = result.get("count", 0)
        dd_label = f"螺丝钉估值表（{update_date}，{index_count}个指数）" if update_date else "螺丝钉估值表"

        conn = _get_conn()
        existing = conn.execute("SELECT id FROM analysis_records WHERE image_path = ?", (rel_path,)).fetchone()
        if existing:
            conn.execute("""UPDATE analysis_records SET status='success', index_name=?, updated_at=datetime('now','localtime') WHERE id=?""",
                         (dd_label, existing[0]))
        else:
            conn.execute("""INSERT INTO analysis_records (image_path, image_url, index_name, status) VALUES (?, ?, ?, 'success')""",
                         (rel_path, image_url, dd_label))
        conn.commit()
        conn.close()

    return result


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
