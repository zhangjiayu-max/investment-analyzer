"""RAG 管理路由 — /api/rag/*

路径规范：
  - /api/rag/stats           - RAG 索引统计
  - /api/rag/reindex         - 重建索引
  - /api/rag/reindex/articles - 重建文章索引
  - /api/rag/reindex/analysis - 重建分析记录索引
  - /api/rag/test-search     - 测试搜索
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from rag import (
    reindex_all, reindex_all_articles, reindex_all_analysis_records,
    get_rag_stats_summary, build_rag_context_with_details, rewrite_query,
)

router = APIRouter(prefix="/api/rag", tags=["rag"])


class TestSearchRequest(BaseModel):
    query: str
    limit: int = 5
    content_types: Optional[list[str]] = None
    use_rewrite: bool = False


@router.get("/stats")
async def get_rag_stats():
    """获取 RAG 索引统计信息。"""
    return get_rag_stats_summary()


@router.post("/reindex")
async def reindex_all_api(limit: int = 1000):
    """批量重建所有索引。"""
    try:
        results = reindex_all(limit)
        return {"ok": True, "results": results}
    except Exception as e:
        logging.error(f"重建索引失败: {e}")
        raise HTTPException(500, f"重建索引失败: {str(e)}")


@router.post("/reindex/articles")
async def reindex_articles_api(limit: int = 1000):
    """重建文章索引。"""
    try:
        result = reindex_all_articles(limit)
        return {"ok": True, "result": result}
    except Exception as e:
        logging.error(f"重建文章索引失败: {e}")
        raise HTTPException(500, f"重建失败: {str(e)}")


@router.post("/reindex/analysis")
async def reindex_analysis_api(limit: int = 1000):
    """重建分析记录索引。"""
    try:
        result = reindex_all_analysis_records(limit)
        return {"ok": True, "result": result}
    except Exception as e:
        logging.error(f"重建分析记录索引失败: {e}")
        raise HTTPException(500, f"重建失败: {str(e)}")


@router.post("/test-search")
async def test_search_api(req: TestSearchRequest):
    """测试 RAG 搜索（支持 Query Rewrite）。"""
    try:
        # Query Rewrite（可选）
        search_query = req.query
        rewritten = None
        if req.use_rewrite:
            search_query = rewrite_query(req.query)
            if search_query != req.query:
                rewritten = search_query

        result = build_rag_context_with_details(
            query=search_query,
            content_types=req.content_types,
            limit=req.limit,
        )

        # 添加 rewrite 信息
        if rewritten:
            result["original_query"] = req.query
            result["rewritten_query"] = rewritten

        return {"ok": True, "result": result}
    except Exception as e:
        logging.error(f"测试搜索失败: {e}")
        raise HTTPException(500, f"搜索失败: {str(e)}")


@router.get("/rewrite")
async def rewrite_query_api(query: str):
    """测试 Query Rewrite（GET 方法）。"""
    try:
        rewritten = rewrite_query(query)
        return {
            "ok": True,
            "original": query,
            "rewritten": rewritten,
            "changed": rewritten != query,
        }
    except Exception as e:
        logging.error(f"Query Rewrite 失败: {e}")
        raise HTTPException(500, f"Rewrite 失败: {str(e)}")
