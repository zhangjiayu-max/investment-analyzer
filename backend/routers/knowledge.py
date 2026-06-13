"""投资知识库路由 — /api/knowledge/*"""

from fastapi import APIRouter, HTTPException
from db.knowledge import (search_knowledge, list_knowledge, get_knowledge_stats,
                          delete_knowledge, list_knowledge_books, cleanup_orphan_fts_records)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/stats")
async def knowledge_stats():
    """获取知识库统计信息。"""
    stats = get_knowledge_stats()
    return stats


@router.get("/books")
async def knowledge_books():
    """列出已蒸馏的书籍及其知识点数量。"""
    books = list_knowledge_books()
    return {"books": books, "total": len(books)}


@router.get("/list")
async def knowledge_list(category: str = None, subcategory: str = None,
                         source: str = None, limit: int = 100):
    """列出知识条目。"""
    items = list_knowledge(category=category, subcategory=subcategory,
                           source=source, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/search")
async def knowledge_search(q: str, category: str = None, limit: int = 20):
    """搜索知识库。"""
    if not q or len(q) < 2:
        raise HTTPException(400, "搜索词至少 2 个字符")
    results = search_knowledge(q, category=category, limit=limit)
    return {"results": results, "total": len(results)}


@router.delete("/{knowledge_id}")
async def delete_knowledge_item(knowledge_id: int):
    """删除知识条目。"""
    success = delete_knowledge(knowledge_id)
    if not success:
        raise HTTPException(404, "知识条目不存在")
    return {"ok": True}


@router.post("/cleanup-orphans")
async def cleanup_orphan_records():
    """清理 FTS 索引中的孤儿记录（knowledge_base 中已删除但 FTS 中仍存在的记录）。"""
    result = cleanup_orphan_fts_records()
    return {"ok": True, **result}
