"""投资知识库 CRUD 操作。"""

import json
import logging
from db._conn import _get_conn

logger = logging.getLogger(__name__)


def add_knowledge(category: str, title: str, content: str,
                  subcategory: str = None, source: str = None,
                  keywords: list = None, importance: int = 5,
                  atom_type: str = "", evidence_level: str = "",
                  as_of_date: str = "", valid_until: str = "",
                  limitations: list = None, counterpoints: list = None,
                  source_decision_id: int = None,
                  domain: str = "", school: str = "",
                  temporal_type: str = "") -> int:
    """添加知识条目，返回 ID。

    domain / school / temporal_type 为空时按治理规则自动推断，
    保证新入库内容不会成为「治理盲区」。
    """
    if not domain or not school or not temporal_type:
        try:
            from services.knowledge_governance import (
                infer_domain, infer_temporal, SCHOOL_BY_BOOK,
            )
            if not domain:
                domain = infer_domain(category, source or "", title, content)
            if not school:
                school = SCHOOL_BY_BOOK.get(source or "", "")
                if not school and domain == "investment" and category in ("concept", "strategy", "fund"):
                    school = "general"
            if not temporal_type:
                temporal_type, inferred_as_of = infer_temporal(atom_type, content)
                if not as_of_date and inferred_as_of:
                    as_of_date = inferred_as_of
        except Exception as e:
            logger.debug(f"知识治理字段自动推断失败，使用空值: {e}")

    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT OR REPLACE INTO knowledge_base
            (category, subcategory, title, content, source, keywords, importance,
             atom_type, evidence_level, as_of_date, valid_until, limitations, counterpoints,
             source_decision_id, domain, school, temporal_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (category, subcategory, title, content, source,
              json.dumps(keywords or [], ensure_ascii=False), importance,
              atom_type or "", evidence_level or "", as_of_date or "", valid_until or "",
              json.dumps(limitations or [], ensure_ascii=False),
              json.dumps(counterpoints or [], ensure_ascii=False),
              source_decision_id,
              domain or "", school or "", temporal_type or ""))
        conn.commit()
        try:
            from services.knowledge_governance import invalidate_meta_cache
            invalidate_meta_cache()
        except Exception:
            pass
        return cur.lastrowid
    finally:
        conn.close()


def _parse_knowledge_row(row) -> dict:
    d = dict(row)
    for key in ("keywords", "limitations", "counterpoints"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = []
        else:
            d[key] = []
    return d


def get_knowledge(knowledge_id: int) -> dict | None:
    """获取单条知识。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM knowledge_base WHERE id = ?", (knowledge_id,)).fetchone()
    conn.close()
    if row:
        return _parse_knowledge_row(row)
    return None


def search_knowledge(query: str, category: str = None, limit: int = 10,
                     domain: str = None, school: str = None) -> list[dict]:
    """搜索知识库（LIKE 子串匹配，按相关度优先排序）。

    2026-10-23 修复：原实现 `ORDER BY importance DESC`，等于**完全忽略匹配质量**。
    症状：查「黄金」时，importance 高的《金融炼金术》条目排在
    《黄金投资新时代》之前，即使后者标题里就有「黄金」。
    现在的排序优先级：标题命中 > 关键词命中 > 正文命中 > importance。

    局限（已知，未在本轮处理）：LIKE 无法做相关性打分，也无法区分
    「黄金法则」与「黄金资产」的语义差别。检索主链路走
    services.rag.search_knowledge（FTS5 + 实体消歧），本函数仅供
    /api/knowledge/search 这类轻量接口使用。
    """
    conn = _get_conn()
    like = f"%{query}%"

    base_where = "(title LIKE ? OR content LIKE ? OR keywords LIKE ?)"
    params: list = [like, like, like]
    if category:
        base_where += " AND category = ?"
        params.append(category)
    if domain:
        base_where += " AND COALESCE(NULLIF(domain,''),'investment') = ?"
        params.append(domain)
    if school:
        base_where += " AND school = ?"
        params.append(school)

    try:
        rows = conn.execute(f"""
            SELECT * FROM knowledge_base
            WHERE {base_where}
            ORDER BY
                CASE WHEN title LIKE ? THEN 0 ELSE 1 END,
                CASE WHEN keywords LIKE ? THEN 0 ELSE 1 END,
                importance DESC,
                id ASC
            LIMIT ?
        """, params + [like, like, limit]).fetchall()
    except Exception as e:
        logger.warning(f"知识库搜索失败: {e}")
        rows = []
    finally:
        conn.close()

    return [_parse_knowledge_row(row) for row in rows]


def list_knowledge(category: str = None, subcategory: str = None,
                   source: str = None, limit: int = 100) -> list[dict]:
    """列出知识条目。"""
    conn = _get_conn()

    conditions = []
    params = []

    if category:
        conditions.append("category = ?")
        params.append(category)
    if subcategory:
        conditions.append("subcategory = ?")
        params.append(subcategory)
    if source:
        conditions.append("source = ?")
        params.append(source)

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    rows = conn.execute(f"""
        SELECT * FROM knowledge_base
        WHERE {where}
        ORDER BY importance DESC, id ASC
        LIMIT ?
    """, params).fetchall()

    conn.close()

    results = []
    for row in rows:
        results.append(_parse_knowledge_row(row))
    return results


def delete_knowledge(knowledge_id: int) -> bool:
    """删除知识条目，同步清理 FTS 索引和 ChromaDB。"""
    conn = _get_conn()
    # 先查询记录获取 category，用于清理索引
    row = conn.execute("SELECT category FROM knowledge_base WHERE id = ?", (knowledge_id,)).fetchone()
    if not row:
        conn.close()
        return False

    category = row["category"]

    # 删除 knowledge_base 记录
    cur = conn.execute("DELETE FROM knowledge_base WHERE id = ?", (knowledge_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()

    if deleted:
        # 同步删除 FTS 索引
        try:
            from services.rag import _get_conn as _get_rag_conn
            rag_conn = _get_rag_conn()
            rag_conn.execute(
                "DELETE FROM knowledge_fts WHERE content_type = ? AND reference_id = ?",
                (category, str(knowledge_id))
            )
            rag_conn.commit()
            rag_conn.close()
        except Exception as e:
            logger.warning(f"删除 FTS 索引失败 (id={knowledge_id}): {e}")

        # 同步删除 ChromaDB
        try:
            from services.rag import delete_chroma_by_filter
            delete_chroma_by_filter(category, reference_id=str(knowledge_id))
        except Exception as e:
            logger.warning(f"删除 ChromaDB 失败 (id={knowledge_id}): {e}")

    return deleted


def delete_knowledge_by_source(source: str) -> int:
    """按来源（书名）批量删除知识条目，同步清理 FTS 索引和 ChromaDB。返回删除数量。"""
    conn = _get_conn()

    # 先查询所有要删除的 ID
    rows = conn.execute(
        "SELECT id, category FROM knowledge_base WHERE source = ?", (source,)
    ).fetchall()

    if not rows:
        conn.close()
        return 0

    # 收集要删除的 ID 和类型
    ids_to_delete = [(row["id"], row["category"]) for row in rows]

    # 删除 knowledge_base 记录
    cur = conn.execute("DELETE FROM knowledge_base WHERE source = ?", (source,))
    conn.commit()
    count = cur.rowcount
    conn.close()

    if count > 0:
        # 同步删除 FTS 索引
        try:
            from services.rag import _get_conn as _get_rag_conn
            rag_conn = _get_rag_conn()
            for kid, category in ids_to_delete:
                rag_conn.execute(
                    "DELETE FROM knowledge_fts WHERE content_type = ? AND reference_id = ?",
                    (category, str(kid))
                )
            rag_conn.commit()
            rag_conn.close()
        except Exception as e:
            logger.warning(f"批量删除 FTS 索引失败 (source={source}): {e}")

        # 同步删除 ChromaDB
        try:
            from services.rag import delete_chroma_by_filter
            for kid, category in ids_to_delete:
                delete_chroma_by_filter(category, reference_id=str(kid))
        except Exception as e:
            logger.warning(f"批量删除 ChromaDB 失败 (source={source}): {e}")

    return count


def cleanup_orphan_fts_records(dry_run: bool = False) -> dict:
    """按类型对应源表判定孤儿；未知类型不删，支持只读候选预览。"""
    from db.index_sources import source_exists
    conn = _get_conn()
    try:
        rows = conn.execute('SELECT rowid,content_type,reference_id FROM knowledge_fts').fetchall()
        orphans = [dict(row) for row in rows if not source_exists(conn,row['content_type'],row['reference_id'])]
        if not dry_run:
            with conn:
                conn.executemany('DELETE FROM knowledge_fts WHERE rowid=?',[(r['rowid'],) for r in orphans])
        return {'cleaned':0 if dry_run else len(orphans), 'candidates':orphans,
                'details':f"发现 {len(orphans)} 条孤儿 FTS 记录", 'dry_run':dry_run}
    finally:
        conn.close()


def list_knowledge_books() -> list[dict]:
    """列出已蒸馏的书籍及其知识点数量。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT source, COUNT(*) as count,
               MIN(created_at) as first_created,
               MAX(created_at) as last_created
        FROM knowledge_base
        WHERE category = 'book' AND source IS NOT NULL AND source != ''
        GROUP BY source
        ORDER BY count DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_knowledge_stats() -> dict:
    """获取知识库统计信息。"""
    conn = _get_conn()

    total = conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()[0]

    categories = conn.execute("""
        SELECT category, COUNT(*) as count
        FROM knowledge_base
        GROUP BY category
        ORDER BY count DESC
    """).fetchall()

    subcategories = conn.execute("""
        SELECT category, subcategory, COUNT(*) as count
        FROM knowledge_base
        WHERE subcategory IS NOT NULL
        GROUP BY category, subcategory
        ORDER BY count DESC
    """).fetchall()

    conn.close()

    return {
        "total": total,
        "categories": {row["category"]: row["count"] for row in categories},
        "subcategories": [
            {"category": row["category"], "subcategory": row["subcategory"], "count": row["count"]}
            for row in subcategories
        ]
    }


def get_lessons_for_target(target_code: str, limit: int = 5) -> list[dict]:
    """获取某个标的的历史教训（供 Agent 参考）。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM knowledge_base
            WHERE category = 'user_lesson'
              AND (title LIKE ? OR content LIKE ?)
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
        """, (f'%{target_code}%', f'%{target_code}%', limit)).fetchall()
        return [_parse_knowledge_row(row) for row in rows]
    finally:
        conn.close()


def get_knowledge_feedback_stats() -> dict:
    """知识反馈统计。"""
    conn = _get_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM knowledge_base WHERE category = 'user_lesson'"
        ).fetchone()[0]

        by_subcategory = conn.execute("""
            SELECT subcategory, COUNT(*) as count
            FROM knowledge_base
            WHERE category = 'user_lesson'
            GROUP BY subcategory
            ORDER BY count DESC
        """).fetchall()

        recent = conn.execute("""
            SELECT id, title, created_at FROM knowledge_base
            WHERE category = 'user_lesson'
            ORDER BY created_at DESC LIMIT 10
        """).fetchall()

        return {
            "total_lessons": total,
            "by_subcategory": {row["subcategory"]: row["count"] for row in by_subcategory},
            "recent_lessons": [dict(row) for row in recent],
        }
    finally:
        conn.close()


def update_knowledge_usefulness(knowledge_id: int, helpful: bool):
    """更新知识条目的有用性分数。"""
    conn = _get_conn()
    try:
        # 确保字段存在
        try:
            conn.execute("ALTER TABLE knowledge_base ADD COLUMN usefulness_score INTEGER DEFAULT 0")
        except Exception:
            pass

        if helpful:
            conn.execute(
                "UPDATE knowledge_base SET usefulness_score = COALESCE(usefulness_score, 0) + 1 WHERE id = ?",
                (knowledge_id,),
            )
        else:
            conn.execute(
                "UPDATE knowledge_base SET usefulness_score = MAX(COALESCE(usefulness_score, 0) - 1, -10) WHERE id = ?",
                (knowledge_id,),
            )
        conn.commit()
    finally:
        conn.close()


def get_knowledge_usefulness(knowledge_id: int) -> int:
    """获取知识条目的有用性分数。"""
    conn = _get_conn()
    try:
        try:
            conn.execute("ALTER TABLE knowledge_base ADD COLUMN usefulness_score INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        row = conn.execute(
            "SELECT usefulness_score FROM knowledge_base WHERE id = ?", (knowledge_id,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
