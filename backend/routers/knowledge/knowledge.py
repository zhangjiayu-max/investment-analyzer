"""投资知识库路由 — /api/knowledge/*"""

import asyncio
import sys
from pathlib import Path
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
async def knowledge_search(q: str, category: str = None, limit: int = 20,
                           domain: str = None, school: str = None):
    """搜索知识库（按标题命中优先排序，可按领域/流派过滤）。"""
    if not q or len(q) < 2:
        raise HTTPException(400, "搜索词至少 2 个字符")
    results = search_knowledge(q, category=category, limit=limit,
                               domain=domain, school=school)
    return {"results": results, "total": len(results)}


# ── 知识库治理 ──

@router.get("/governance/report")
async def governance_report():
    """治理覆盖率报告：领域/流派/时序/有用度的填写率与分布。"""
    from db._conn import _get_conn
    from services.knowledge_governance import governance_report as _report
    conn = _get_conn()
    try:
        _report_safe = _report(conn)
    except Exception as exc:  # 未迁移时给出可执行的指引，而不是 500
        raise HTTPException(503, f"治理字段尚未初始化，请先执行 scripts/kb_governance.py --apply：{exc}")
    finally:
        conn.close()
    return _report_safe


class GovernanceRunRequest(BaseModel):
    classify: bool = False
    temporal: bool = False
    lessons: bool = False
    usefulness: bool = False
    dry_run: bool = True


@router.post("/governance/run")
async def governance_run(req: GovernanceRunRequest):
    """执行知识库治理。

    默认 dry_run=True：只返回将要发生的变更，不落库。
    落库需要显式传 dry_run=false —— 治理会批量改写元数据，误操作代价高。
    """
    from db._conn import _get_conn
    from services import knowledge_governance as gov
    from services import user_lessons

    conn = _get_conn()
    try:
        gov.migrate_schema(conn)
        out = {"dry_run": req.dry_run}
        if req.classify:
            out["classify"] = gov.classify_entries(conn, dry_run=req.dry_run)
            gov.invalidate_meta_cache()
        if req.temporal:
            out["temporal"] = gov.annotate_temporal(conn, dry_run=req.dry_run)
        if req.lessons:
            out["lessons"] = user_lessons.sync_lessons(conn, dry_run=req.dry_run)
        if req.usefulness:
            out["usefulness"] = gov.refresh_usefulness(conn, dry_run=req.dry_run)
        if not req.dry_run:
            gov.invalidate_meta_cache()
        return out
    finally:
        conn.close()


@router.delete("/{knowledge_id}")
async def delete_knowledge_item(knowledge_id: int):
    """删除知识条目。"""
    success = delete_knowledge(knowledge_id)
    if not success:
        raise HTTPException(404, "知识条目不存在")
    return {"ok": True}


@router.post("/cleanup-orphans")
async def cleanup_orphan_records(dry_run: bool = False):
    """清理 FTS 索引中的孤儿记录（knowledge_base 中已删除但 FTS 中仍存在的记录）。"""
    result = cleanup_orphan_fts_records(dry_run=dry_run)
    return {"ok": True, **result}


OBSIDIAN_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'sync_obsidian.py'
_OBSIDIAN_SLOTS = asyncio.Semaphore(1)


async def _run_obsidian(mode, vault_path=None, timeout=120):
    if not OBSIDIAN_SCRIPT.is_file():
        raise HTTPException(503, 'Obsidian 同步脚本不存在')
    command = [sys.executable, str(OBSIDIAN_SCRIPT), mode]
    if vault_path:
        command += ['--vault', vault_path]
    async with _OBSIDIAN_SLOTS:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HTTPException(504, f'同步超时（{timeout}s）')
        return stdout, stderr, process.returncode


@router.post('/import')
async def import_obsidian(vault_path: str = None):
    """异步子进程执行同步；失败不返回假成功。"""
    stdout, stderr, returncode = await _run_obsidian('--sync', vault_path)
    if returncode:
        raise HTTPException(502, stderr.decode(errors='replace')[-500:] or '同步失败')
    return {'ok':True, 'output':stdout.decode(errors='replace')[-2000:], 'error':''}


@router.get('/obsidian/status')
async def obsidian_status(vault_path: str = None):
    stdout, stderr, returncode = await _run_obsidian('--stats', vault_path, timeout=30)
    if returncode:
        raise HTTPException(502, stderr.decode(errors='replace')[-500:] or '状态读取失败')
    return {'ok':True, 'output':stdout.decode(errors='replace')}


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
