"""估值数据 + 指数信息路由 — /api/valuations/*, /api/index-info/*"""

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
)
from db._conn import _get_conn
from image_parser import DDImageParser
from models.valuations import ParseAndSaveRequest, ParseBatchRequest, ParseDDRequest
from services.valuation_parser import parse_single_valuation

router = APIRouter(prefix="/api/valuations", tags=["valuations"])


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
    return result


@router.get("")
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
                    current_point = float(row["最新价"])
                    change_pct = float(row["涨跌幅"])
                    conn.execute("""
                        UPDATE index_valuations
                        SET current_point = ?, change_pct = ?
                        WHERE index_code = ? AND snapshot_date = (
                            SELECT MAX(snapshot_date) FROM index_valuations WHERE index_code = ?
                        )
                    """, (current_point, change_pct, code, code))
                    updated += 1
                    break
        conn.commit()
        conn.close()
    except Exception as e:
        errors.append(f"实时行情刷新失败: {e}")

    return {"ok": True, "updated": updated, "errors": errors}


@router.get("/{index_code}")
async def get_history(index_code: str, days: int = 30, metric_type: str = None):
    """查询某指数的估值历史。"""
    history = get_valuation_history(index_code, days, metric_type)
    latest = get_latest_valuation(index_code, metric_type)
    return {
        "index_code": index_code,
        "latest": latest,
        "history": history,
    }


# ── 常见指数简介（静态字典，兜底用）────────────────────

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


# ── 指数信息路由（挂在 /api/index-info/ 下）────────────────────

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
        from llm_service import _call_llm, MODEL
        prompt = f"请用2-3句话简洁介绍「{index_name or index_code}」这个股票指数，包括它由哪些股票组成、覆盖什么行业、适合什么样的投资者。不要使用markdown格式，直接输出纯文本。"
        resp = _call_llm(messages=[{"role": "user", "content": prompt}], model=MODEL, max_tokens=800)
        info = resp.choices[0].message.content if resp and resp.choices else ""
        if info:
            save_index_info(index_code, index_name, info.strip())
            return {"index_code": index_code, "info": info.strip(), "source": "llm"}
    except Exception as e:
        logging.warning(f"LLM生成指数信息失败: {e}")

    return {"index_code": index_code, "info": "", "source": "none"}
