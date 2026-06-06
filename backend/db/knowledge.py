"""投资知识库 CRUD 操作。"""

import json
from db._conn import _get_conn


def add_knowledge(category: str, title: str, content: str,
                  subcategory: str = None, source: str = None,
                  keywords: list = None, importance: int = 5) -> int:
    """添加知识条目，返回 ID。"""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT OR REPLACE INTO knowledge_base
            (category, subcategory, title, content, source, keywords, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (category, subcategory, title, content, source,
              json.dumps(keywords or [], ensure_ascii=False), importance))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_knowledge(knowledge_id: int) -> dict | None:
    """获取单条知识。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM knowledge_base WHERE id = ?", (knowledge_id,)).fetchone()
    conn.close()
    if row:
        d = dict(row)
        if d.get("keywords"):
            try:
                d["keywords"] = json.loads(d["keywords"])
            except:
                d["keywords"] = []
        return d
    return None


def search_knowledge(query: str, category: str = None, limit: int = 10) -> list[dict]:
    """搜索知识库（FTS5 + 关键词匹配）。"""
    conn = _get_conn()

    # 先尝试 FTS5 搜索
    try:
        if category:
            rows = conn.execute("""
                SELECT kb.* FROM knowledge_base kb
                WHERE kb.category = ?
                AND (kb.title LIKE ? OR kb.content LIKE ? OR kb.keywords LIKE ?)
                ORDER BY kb.importance DESC
                LIMIT ?
            """, (category, f"%{query}%", f"%{query}%", f"%{query}%", limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM knowledge_base
                WHERE title LIKE ? OR content LIKE ? OR keywords LIKE ?
                ORDER BY importance DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", f"%{query}%", limit)).fetchall()
    except Exception:
        rows = []

    conn.close()

    results = []
    for row in rows:
        d = dict(row)
        if d.get("keywords"):
            try:
                d["keywords"] = json.loads(d["keywords"])
            except:
                d["keywords"] = []
        results.append(d)
    return results


def list_knowledge(category: str = None, subcategory: str = None,
                   limit: int = 100) -> list[dict]:
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
        d = dict(row)
        if d.get("keywords"):
            try:
                d["keywords"] = json.loads(d["keywords"])
            except:
                d["keywords"] = []
        results.append(d)
    return results


def delete_knowledge(knowledge_id: int) -> bool:
    """删除知识条目。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM knowledge_base WHERE id = ?", (knowledge_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


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
