"""RAG 管理路由 — /api/rag/*

路径规范：
  - /api/rag/stats           - RAG 索引统计
  - /api/rag/reindex         - 重建索引
  - /api/rag/reindex/articles - 重建文章索引
  - /api/rag/reindex/analysis - 重建分析记录索引
  - /api/rag/test-search     - 测试搜索
  - /api/rag/config          - RAG 配置管理
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.rag import (
    reindex_all, reindex_all_articles, reindex_all_analysis_records,
    get_rag_stats_summary, build_rag_context_with_details, rewrite_query,
    backfill_atom_metadata,
    _RAG_CONFIG_DEFAULTS, _invalidate_rag_config_cache,
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


@router.post("/backfill-atom-metadata")
async def backfill_atom_metadata_api(dry_run: bool = False):
    """回填 knowledge_base 的 atom_type / evidence_level 元数据。

    Args:
        dry_run: 仅统计不写入（用于预览）
    """
    try:
        stats = backfill_atom_metadata(dry_run=dry_run)
        return {"ok": True, "stats": stats}
    except Exception as e:
        logging.error(f"回填知识原子元数据失败: {e}")
        raise HTTPException(500, f"回填失败: {str(e)}")


@router.post("/test-search")
async def test_search_api(req: TestSearchRequest):
    """测试 RAG 搜索（支持 Query Rewrite）。"""
    import time
    try:
        # Query Rewrite（可选）
        search_query = req.query
        rewritten = None
        if req.use_rewrite:
            search_query = rewrite_query(req.query)
            if search_query != req.query:
                rewritten = search_query

        t0 = time.time()
        result = build_rag_context_with_details(
            query=search_query,
            content_types=req.content_types,
            limit=req.limit,
        )
        elapsed_ms = round((time.time() - t0) * 1000)

        # 统计来源分布
        results = result.get("results", [])
        source_breakdown = {"fts_only": 0, "chroma_only": 0, "both": 0}
        for r in results:
            src = r.get("source", "")
            if src == "both":
                source_breakdown["both"] += 1
            elif src == "fts":
                source_breakdown["fts_only"] += 1
            elif src == "chroma":
                source_breakdown["chroma_only"] += 1

        # 添加计时和统计信息
        result["timing"] = {
            "total_ms": elapsed_ms,
            "fts_count": result.get("fts_count", 0),
            "chroma_count": result.get("chroma_count", 0),
            "merged_count": len(results),
            "source_breakdown": source_breakdown,
        }

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


# ── RAG 配置管理 ──────────────────────────────────────────

class UpdateConfigRequest(BaseModel):
    value: str
    description: Optional[str] = None


@router.get("/config")
async def get_all_config():
    """获取所有 RAG 配置（含默认值和当前值）。"""
    import sqlite3
    from db._conn import DB_PATH

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value, description, updated_at FROM rag_config").fetchall()
        conn.close()

        # 合并默认值和数据库值
        config = {}
        db_values = {row["key"]: dict(row) for row in rows}

        for key, (default_val, desc) in _RAG_CONFIG_DEFAULTS.items():
            if key in db_values:
                config[key] = {
                    "value": db_values[key]["value"],
                    "default": default_val,
                    "description": db_values[key]["description"] or desc,
                    "updated_at": db_values[key]["updated_at"],
                    "source": "custom",
                }
            else:
                config[key] = {
                    "value": default_val,
                    "default": default_val,
                    "description": desc,
                    "updated_at": None,
                    "source": "default",
                }

        return {"ok": True, "config": config}
    except Exception as e:
        logging.error(f"获取 RAG 配置失败: {e}")
        raise HTTPException(500, f"获取配置失败: {str(e)}")


@router.put("/config/{key}")
async def update_config(key: str, req: UpdateConfigRequest):
    """更新单个 RAG 配置项。"""
    import sqlite3
    from db._conn import DB_PATH

    if key not in _RAG_CONFIG_DEFAULTS:
        raise HTTPException(400, f"未知配置项: {key}，可选: {list(_RAG_CONFIG_DEFAULTS.keys())}")

    # 验证值类型（所有值都应该是数字）
    try:
        float(req.value)
    except ValueError:
        raise HTTPException(400, f"配置值必须是数字: {req.value}")

    description = req.description or _RAG_CONFIG_DEFAULTS[key][1]

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR REPLACE INTO rag_config (key, value, description, updated_at) "
            "VALUES (?, ?, ?, datetime('now','localtime'))",
            (key, req.value, description)
        )
        conn.commit()
        conn.close()

        # 使缓存失效
        _invalidate_rag_config_cache()

        return {"ok": True, "key": key, "value": req.value}
    except Exception as e:
        logging.error(f"更新 RAG 配置失败: {e}")
        raise HTTPException(500, f"更新配置失败: {str(e)}")


@router.delete("/config/{key}")
async def reset_config(key: str):
    """重置单个 RAG 配置为默认值。"""
    import sqlite3
    from db._conn import DB_PATH

    if key not in _RAG_CONFIG_DEFAULTS:
        raise HTTPException(400, f"未知配置项: {key}")

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("DELETE FROM rag_config WHERE key = ?", (key,))
        conn.commit()
        conn.close()

        # 使缓存失效
        _invalidate_rag_config_cache()

        default_val = _RAG_CONFIG_DEFAULTS[key][0]
        return {"ok": True, "key": key, "value": default_val, "source": "default"}
    except Exception as e:
        logging.error(f"重置 RAG 配置失败: {e}")
        raise HTTPException(500, f"重置配置失败: {str(e)}")


# ── RAG 质量评估 ──────────────────────────────────────

class RagEvalRequest(BaseModel):
    query: str
    expected_topics: list[str] = []


@router.post("/api/rag/eval/run")
async def run_rag_eval(req: RagEvalRequest):
    """运行单次 RAG 检索质量评估。"""
    from agent.rag_evaluator import evaluate_rag_retrieval
    try:
        result = await evaluate_rag_retrieval(
            query=req.query,
            expected_topics=req.expected_topics if req.expected_topics else None,
        )
        return {"ok": True, "result": result}
    except Exception as e:
        logging.error(f"RAG 评估失败: {e}")
        raise HTTPException(500, f"评估失败: {str(e)}")


@router.post("/api/rag/eval/suite")
async def run_rag_eval_suite():
    """运行完整 RAG 评估套件（16 个测试用例）。"""
    from scripts.rag_eval_suite import run_eval_suite, save_results
    try:
        result = await run_eval_suite(verbose=False)
        save_results(result)
        return {"ok": True, "summary": result["summary"], "cases": result["cases"]}
    except Exception as e:
        logging.error(f"RAG 评估套件失败: {e}")
        raise HTTPException(500, f"评估失败: {str(e)}")


@router.get("/api/rag/eval/results")
async def get_rag_eval_results():
    """获取最近一次评估套件的结果。"""
    import os
    import json
    path = "data/rag_eval_results.json"
    if not os.path.exists(path):
        return {"ok": True, "result": None, "message": "暂无评估结果，请先运行评估套件"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"ok": True, "result": data}
    except Exception as e:
        raise HTTPException(500, f"读取结果失败: {str(e)}")


# ── RAG 反馈 API ──

class RagFeedbackRequest(BaseModel):
    knowledge_id: int | None = None
    content_type: str = ""
    query: str = ""
    rating: int  # 1=赞, -1=踩
    reasons: list[str] = []


@router.post("/feedback")
async def submit_rag_feedback_api(req: RagFeedbackRequest):
    """提交 RAG 检索结果反馈（点赞/点踩）。"""
    if req.rating not in (1, -1):
        raise HTTPException(400, "rating 必须为 1（赞）或 -1（踩）")
    from db import save_rag_feedback
    from db.knowledge import update_knowledge_usefulness
    feedback_id = save_rag_feedback(
        knowledge_id=req.knowledge_id,
        content_type=req.content_type,
        query=req.query,
        rating=req.rating,
        reasons=req.reasons,
    )
    # 同步更新知识条目的 usefulness_score
    if req.knowledge_id:
        update_knowledge_usefulness(req.knowledge_id, helpful=(req.rating == 1))
    return {"ok": True, "id": feedback_id}
