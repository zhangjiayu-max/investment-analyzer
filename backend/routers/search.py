"""全局搜索路由 — /api/search/*"""

import json
import logging
from fastapi import APIRouter, Query

from db import _get_conn

router = APIRouter(prefix="/api/search", tags=["search"])

logger = logging.getLogger(__name__)


@router.get("/global")
async def global_search(q: str = Query(..., min_length=1), limit: int = Query(5, ge=1, le=20)):
    """全局搜索：知识库 + 持仓基金 + 估值指数。"""
    results = {"knowledge": [], "funds": [], "valuations": []}
    keyword = q.strip()

    # 1. 知识库搜索（FTS5）
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT content_type, title, body, reference_id "
            "FROM knowledge_fts WHERE knowledge_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (f'"{keyword}"', limit * 2),
        ).fetchall()
        conn.close()
        for r in rows:
            results["knowledge"].append({
                "content_type": r["content_type"] if isinstance(r, dict) else r[0],
                "title": (r["title"] if isinstance(r, dict) else r[1]) or "",
                "body": ((r["body"] if isinstance(r, dict) else r[2]) or "")[:200],
                "reference_id": r["reference_id"] if isinstance(r, dict) else r[3],
            })
    except Exception as e:
        logger.warning(f"FTS search failed: {e}")

    # 2. 持仓基金搜索
    try:
        conn = _get_conn()
        like = f"%{keyword}%"
        rows = conn.execute(
            "SELECT DISTINCT fund_code, fund_name, shares, current_nav, current_value "
            "FROM holdings WHERE (fund_code LIKE ? OR fund_name LIKE ?) AND (shares IS NULL OR shares > 0) "
            "LIMIT ?",
            (like, like, limit),
        ).fetchall()
        conn.close()
        for r in rows:
            results["funds"].append({
                "fund_code": r["fund_code"] if isinstance(r, dict) else r[0],
                "fund_name": r["fund_name"] if isinstance(r, dict) else r[1],
                "shares": r["shares"] if isinstance(r, dict) else r[2],
                "current_nav": r["current_nav"] if isinstance(r, dict) else r[3],
                "current_value": r["current_value"] if isinstance(r, dict) else r[4],
            })
    except Exception as e:
        logger.warning(f"Fund search failed: {e}")

    # 3. 估值指数搜索
    try:
        conn = _get_conn()
        like = f"%{keyword}%"
        rows = conn.execute(
            "SELECT DISTINCT index_code, index_name, snapshot_date, current_value, percentile "
            "FROM index_valuations WHERE index_name LIKE ? "
            "ORDER BY snapshot_date DESC LIMIT ?",
            (like, limit),
        ).fetchall()
        conn.close()
        for r in rows:
            results["valuations"].append({
                "index_code": r["index_code"] if isinstance(r, dict) else r[0],
                "index_name": r["index_name"] if isinstance(r, dict) else r[1],
                "snapshot_date": r["snapshot_date"] if isinstance(r, dict) else r[2],
                "current_value": r["current_value"] if isinstance(r, dict) else r[3],
                "percentile": r["percentile"] if isinstance(r, dict) else r[4],
            })
    except Exception as e:
        logger.warning(f"Valuation search failed: {e}")

    return {"ok": True, "data": results, "query": keyword}
