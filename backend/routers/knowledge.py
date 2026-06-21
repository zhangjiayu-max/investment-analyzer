"""投资知识库路由 — /api/knowledge/*"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from db.knowledge import (search_knowledge, list_knowledge, get_knowledge_stats,
                          delete_knowledge, list_knowledge_books, cleanup_orphan_fts_records)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)


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


@router.post("/import")
async def import_obsidian(vault_path: str = None):
    """触发 Obsidian vault 同步到知识库。"""
    import subprocess, sys
    script = str(__import__('pathlib').Path(__file__).resolve().parent.parent / "scripts" / "sync_obsidian.py")
    cmd = [sys.executable, script, "--sync"]
    if vault_path:
        cmd += ["--vault", vault_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {
            "ok": result.returncode == 0,
            "output": result.stdout[-2000:] if result.stdout else "",
            "error": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "同步超时（120s）")
    except Exception as e:
        raise HTTPException(500, f"同步失败: {e}")


@router.get("/obsidian/status")
async def obsidian_status(vault_path: str = None):
    """查看 Obsidian 同步状态。"""
    import subprocess, sys
    script = str(__import__('pathlib').Path(__file__).resolve().parent.parent / "scripts" / "sync_obsidian.py")
    cmd = [sys.executable, script, "--stats"]
    if vault_path:
        cmd += ["--vault", vault_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {"ok": True, "output": result.stdout}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 知识反馈回流 API ──

@router.get("/lessons")
async def get_lessons(target_code: str = None, limit: int = 20):
    """获取经验教训列表，或按标的代码筛选。"""
    from db.knowledge import get_lessons_for_target, list_knowledge
    if target_code:
        items = get_lessons_for_target(target_code, limit=limit)
    else:
        items = list_knowledge(category="user_lesson", limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/feedback-stats")
async def feedback_stats():
    """知识反馈统计。"""
    from db.knowledge import get_knowledge_feedback_stats
    return get_knowledge_feedback_stats()


class FeedbackRequest(BaseModel):
    feedback_type: str = "user_correction"  # decision_lesson / user_correction / analysis_feedback
    content: str
    source_id: int | None = None
    target_code: str = ""
    target_name: str = ""
    metadata: dict = {}


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """手动提交反馈/纠正到知识库。"""
    from db.knowledge import add_knowledge
    from datetime import datetime

    if not req.content.strip():
        raise HTTPException(400, "内容不能为空")

    category_map = {
        "decision_lesson": "user_lesson",
        "user_correction": "user_correction",
        "analysis_feedback": "analysis_feedback",
    }
    category = category_map.get(req.feedback_type, "user_feedback")
    title = f"用户反馈：{req.target_name or req.target_code or req.feedback_type}"

    kid = add_knowledge(
        category=category,
        subcategory=req.feedback_type,
        title=title,
        content=req.content,
        source=f"manual_feedback:{req.source_id}" if req.source_id else "manual_feedback",
        keywords=[req.target_code, req.target_name, "用户反馈"],
        importance=6,
        atom_type="user_feedback",
        evidence_level="user_memory",
        as_of_date=datetime.now().strftime("%Y-%m-%d"),
    )
    return {"ok": True, "id": kid}
