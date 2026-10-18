"""全局搜索路由 — /api/search/*，检索本地知识、文章、持仓与最新估值。"""

import logging
from contextlib import closing

from fastapi import APIRouter, Query

from db import _get_conn

router = APIRouter(prefix="/api/search", tags=["search"])
logger = logging.getLogger(__name__)


@router.get("/global")
def global_search(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(5, ge=1, le=20)):
    """全局搜索：中文预分词与知识索引一致，SQLite 查询在线程池执行。"""
    results = {"knowledge": [], "funds": [], "valuations": []}
    keyword = q.strip()
    if not keyword:
        return {"ok": True, "data": results, "query": keyword}
    # 用户输入按字面匹配，不能把 %、_ 当作全库通配符。
    like = '%' + keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'

    # 原始文章标题不依赖 FTS 是否已索引，优先补齐标题命中。
    try:
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT id, title FROM articles WHERE title LIKE ? ESCAPE '\\' "
                "ORDER BY created_at DESC, id DESC LIMIT ?", (like, limit),
            ).fetchall()
        results["knowledge"] = [
            {"content_type": "article", "title": row["title"], "body": "", "reference_id": str(row["id"])}
            for row in rows
        ]
    except Exception as exc:
        logger.warning("Article search failed: %s", exc)

    try:
        from services.rag.rag import _build_fts_query
        fts_query = _build_fts_query(keyword)
        if fts_query:
            with closing(_get_conn()) as conn:
                rows = conn.execute(
                    "SELECT content_type, title, body, reference_id "
                    "FROM knowledge_fts WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_query, limit * 2),
                ).fetchall()
            seen = {(row["content_type"], row["reference_id"]) for row in results["knowledge"]}
            for row in rows:
                key = (row["content_type"], str(row["reference_id"]))
                if key in seen or len(results["knowledge"]) >= limit:
                    continue
                seen.add(key)
                results["knowledge"].append({
                    "content_type": row["content_type"], "title": row["title"] or "",
                    "body": (row["body"] or "")[:200], "reference_id": str(row["reference_id"]),
                })
    except Exception as exc:
        logger.warning("FTS search failed: %s", exc)

    # 使用真实持仓表与净值列，同基金多账户汇总，排除已清仓记录。
    try:
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT fund_code, MAX(fund_name) AS fund_name, SUM(shares) AS shares, "
                "CASE WHEN COUNT(current_price) = COUNT(*) "
                "THEN SUM(current_price * shares) / SUM(shares) END AS current_nav, "
                "CASE WHEN COUNT(current_value) = COUNT(*) THEN SUM(current_value) END AS current_value "
                "FROM portfolio_holdings WHERE (fund_code LIKE ? ESCAPE '\\' OR fund_name LIKE ? ESCAPE '\\') "
                "AND shares > 0 GROUP BY fund_code ORDER BY fund_code LIMIT ?",
                (like, like, limit),
            ).fetchall()
        results["funds"] = [dict(row) for row in rows]
    except Exception as exc:
        logger.warning("Fund search failed: %s", exc)

    # 复用数据层的代码标准化、百分位处理与最新记录去重，PE/PB 分别展示。
    try:
        from db import list_valuation_indexes
        results["valuations"] = [
            {**row, "snapshot_date": row["latest_date"]}
            for row in list_valuation_indexes()
            if keyword.casefold() in (row.get("index_name") or "").casefold()
            or keyword.casefold() in row["index_code"].casefold()
        ][:limit]
    except Exception as exc:
        logger.warning("Valuation search failed: %s", exc)

    return {"ok": True, "data": results, "query": keyword}
