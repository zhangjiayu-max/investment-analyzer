"""文章 + 分析记录 + 作者文章 + 链接文章 + 文档分块 CRUD。"""

from datetime import datetime

from db._conn import _get_conn, _row_to_dict
from db._utils import _extract_url_id


# ── 文章管理 CRUD ──────────────────────────────────────


def sync_articles(articles: list[dict]):
    """从 articles.json 同步文章列表到 DB。"""
    conn = _get_conn()
    for a in articles:
        conn.execute("""
            INSERT OR IGNORE INTO articles (seq, url, title, publish_time, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (a.get("seq"), a.get("url"), a.get("title"), a.get("publish_time", "")))
    conn.commit()
    conn.close()


def list_articles(status: str = None) -> list[dict]:
    """列出所有文章，可选按状态筛选。附带分析记录统计。"""
    conn = _get_conn()
    base = """
        SELECT a.*,
               COALESCE(stats.total_records, 0) AS total_records,
               COALESCE(stats.success_count, 0) AS success_count,
               COALESCE(stats.error_count, 0)   AS error_count,
               COALESCE(stats.pending_count, 0)  AS pending_count
        FROM articles a
        LEFT JOIN (
            SELECT article_id,
                   COUNT(*)                          AS total_records,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN status='error'   THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count
            FROM analysis_records
            GROUP BY article_id
        ) stats ON a.id = stats.article_id
    """
    if status:
        rows = conn.execute(base + " WHERE a.status = ? ORDER BY a.publish_time DESC", (status,)).fetchall()
    else:
        rows = conn.execute(base + " ORDER BY a.publish_time DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_article(article_id: int) -> dict | None:
    """获取单篇文章详情（含分析记录统计）。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT a.*,
               COALESCE(stats.total_records, 0) AS total_records,
               COALESCE(stats.success_count, 0) AS success_count,
               COALESCE(stats.error_count, 0)   AS error_count,
               COALESCE(stats.pending_count, 0)  AS pending_count
        FROM articles a
        LEFT JOIN (
            SELECT article_id,
                   COUNT(*)                          AS total_records,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN status='error'   THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count
            FROM analysis_records
            GROUP BY article_id
        ) stats ON a.id = stats.article_id
        WHERE a.id = ?
    """, (article_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_article_by_seq(seq: int) -> dict | None:
    """按 seq 查找文章。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM articles WHERE seq = ?", (seq,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_article_by_url(url: str) -> dict | None:
    """按 URL 尾部 ID 查找文章（去重用）。"""
    url_id = _extract_url_id(url)
    conn = _get_conn()
    # 用 LIKE 匹配所有包含该 ID 的 URL（兼容带/不带查询参数、不同域名等情况）
    row = conn.execute(
        "SELECT * FROM articles WHERE url LIKE ?", (f"%/{url_id}",)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_article(url: str, title: str = "", publish_time: str = "") -> int:
    """创建文章记录，返回 article_id。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO articles (url, title, publish_time, status) VALUES (?, ?, ?, 'pending')",
        (url, title, publish_time),
    )
    article_id = cur.lastrowid
    conn.commit()
    conn.close()
    return article_id


def update_article(article_id: int, **fields):
    """更新文章字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [article_id]
    conn = _get_conn()
    conn.execute(f"UPDATE articles SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


# ── 图片分析记录 CRUD ────────────────────────────────


def create_analysis_record(article_id: int, image_index: int, image_path: str, image_url: str) -> int:
    """创建一条图片分析记录（已存在同一图片则忽略）。"""
    # 归一化路径为相对路径
    if image_path.startswith("/"):
        idx = image_path.find("data/images/")
        if idx >= 0:
            image_path = image_path[idx:]
    conn = _get_conn()
    cur = conn.execute("""
        INSERT OR IGNORE INTO analysis_records (article_id, image_index, image_path, image_url, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (article_id, image_index, image_path, image_url))
    conn.commit()
    if cur.lastrowid:
        rid = cur.lastrowid
    else:
        rid = conn.execute(
            "SELECT id FROM analysis_records WHERE image_path = ?", (image_path,)
        ).fetchone()[0]
    conn.close()
    return rid


def update_analysis_record(record_id: int, **fields):
    """更新分析记录字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [record_id]
    conn = _get_conn()
    conn.execute(f"UPDATE analysis_records SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def list_all_analysis_records(search: str = None, limit: int = 200) -> list[dict]:
    """列出所有分析记录，关联文章信息，支持按指标名/指数名模糊搜索。"""
    conn = _get_conn()
    base = """
        SELECT ar.*, a.title as article_title,
               COALESCE(a.publish_time, ar.created_at) as publish_time,
               a.url as article_url
        FROM analysis_records ar
        LEFT JOIN articles a ON ar.article_id = a.id
    """
    params = []
    if search:
        base += " WHERE ar.index_name LIKE ? OR ar.index_code LIKE ? OR ar.metric_type LIKE ? OR a.title LIKE ?"
        q = f"%{search}%"
        params = [q, q, q, q]
    base += " ORDER BY COALESCE(a.publish_time, ar.created_at) DESC, ar.image_index LIMIT ?"
    params.append(limit)
    rows = conn.execute(base, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analysis_records(article_id: int) -> list[dict]:
    """获取某篇文章的所有分析记录。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM analysis_records WHERE article_id = ? ORDER BY image_index",
        (article_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analysis_record(record_id: int) -> dict | None:
    """获取单条分析记录。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM analysis_records WHERE id = ?", (record_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── 作者文章 CRUD ──────────────────────────────────────


def create_author_article(url: str, title: str = "", publish_time: str = "",
                          summary: str = "", article_type: str = "", tags: str = "",
                          read_count: int = None, like_count: int = None) -> int:
    """创建作者文章记录，返回 id。"""
    conn = _get_conn()
    cur = conn.execute(
        """INSERT OR IGNORE INTO author_articles
           (url, title, publish_time, summary, article_type, tags, read_count, like_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (url, title, publish_time, summary, article_type, tags, read_count, like_count),
    )
    row_id = cur.lastrowid
    if row_id == 0:
        row_id = conn.execute(
            "SELECT id FROM author_articles WHERE url = ?", (url,)
        ).fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def update_author_article(article_id: int, **fields):
    """更新作者文章字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [article_id]
    conn = _get_conn()
    conn.execute(f"UPDATE author_articles SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_author_article_by_url(url: str) -> dict | None:
    """按 URL 查找作者文章（去重用）。"""
    url_id = _extract_url_id(url)
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM author_articles WHERE url LIKE ?", (f"%/{url_id}",)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_author_articles(status: str = None, search: str = None, limit: int = 200) -> list[dict]:
    """列出作者文章，可选按状态筛选和搜索。"""
    conn = _get_conn()
    base = "SELECT * FROM author_articles"
    conditions = []
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if search:
        conditions.append("(title LIKE ? OR summary LIKE ? OR content_text LIKE ?)")
        q = f"%{search}%"
        params.extend([q, q, q])
    if conditions:
        base += " WHERE " + " AND ".join(conditions)
    base += " ORDER BY publish_time DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(base, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_author_article(article_id: int) -> dict | None:
    """获取单篇作者文章。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM author_articles WHERE id = ?", (article_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_author_article(article_id: int) -> bool:
    """删除作者文章。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM author_articles WHERE id = ?", (article_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def count_author_articles() -> dict:
    """统计作者文章数量（总数、各状态）。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM author_articles GROUP BY status"
    ).fetchall()
    conn.close()
    result = {"total": 0, "pending": 0, "crawling": 0, "done": 0, "error": 0}
    for r in rows:
        result[r["status"]] = r["cnt"]
        result["total"] += r["cnt"]
    return result


# ── 链接文章 CRUD ──────────────────────────────────────────

def create_linked_article(title: str = "", file_path: str = "",
                          file_size: int = 0, file_type: str = "") -> int:
    """创建文档记录，返回 id。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO linked_articles (title, file_path, file_size, file_type) VALUES (?, ?, ?, ?)",
        (title, file_path, file_size, file_type),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def list_linked_articles(limit: int = 200) -> list[dict]:
    """列出文档。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM linked_articles ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_linked_article(article_id: int) -> dict | None:
    """获取单篇文档。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM linked_articles WHERE id = ?", (article_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_linked_article_file(article_id: int, file_path: str):
    """更新文档的文件路径。"""
    conn = _get_conn()
    conn.execute("UPDATE linked_articles SET file_path = ? WHERE id = ?", (file_path, article_id))
    conn.commit()
    conn.close()


def delete_linked_article(article_id: int) -> bool:
    """删除文档记录。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM linked_articles WHERE id = ?", (article_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def update_linked_article_embed_status(article_id: int, status: str, chunks_count: int = 0):
    """更新文档的 embedding 状态。"""
    conn = _get_conn()
    conn.execute(
        "UPDATE linked_articles SET embed_status = ?, chunks_count = ? WHERE id = ?",
        (status, chunks_count, article_id),
    )
    conn.commit()
    conn.close()


def save_document_chunks(article_id: int, chunks: list[str]):
    """保存文档分块数据（先删旧的再插入新的）。"""
    conn = _get_conn()
    conn.execute("DELETE FROM document_chunks WHERE article_id = ?", (article_id,))
    for i, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO document_chunks (article_id, chunk_index, content, char_count) VALUES (?, ?, ?, ?)",
            (article_id, i, chunk, len(chunk)),
        )
    conn.commit()
    conn.close()


def get_document_chunks(article_id: int) -> list[dict]:
    """获取文档的所有分块。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM document_chunks WHERE article_id = ? ORDER BY chunk_index",
        (article_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
