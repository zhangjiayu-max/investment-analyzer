"""检索索引的源记录归属，未知类型保留供后续对账。"""
from db._conn import _get_conn

_SOURCE_QUERIES = {
    'article': 'SELECT 1 FROM articles WHERE id=?',
    'author_article': 'SELECT 1 FROM author_articles WHERE id=?',
    'linked_doc': 'SELECT 1 FROM linked_articles WHERE id=?',
    'valuation': 'SELECT 1 FROM index_valuations WHERE index_code=?',
    'analysis': 'SELECT 1 FROM analysis_records WHERE id=?',
    # skill 同时用于技能文档与蒸馏结果，不能仅对照其中一张表。
    'skill': 'SELECT 1 FROM skill_documents WHERE id=? UNION ALL SELECT 1 FROM author_articles WHERE id=?',
}
_KNOWLEDGE_TYPES = {'book','note','concept','strategy','bond','knowledge'}


def source_exists(conn, content_type, reference_id):
    if content_type in _KNOWLEDGE_TYPES:
        return conn.execute('SELECT 1 FROM knowledge_base WHERE id=? AND category=?',
                            (reference_id, content_type)).fetchone() is not None
    query = _SOURCE_QUERIES.get(content_type)
    if not query:
        return True
    params = (reference_id, reference_id) if content_type=='skill' else (reference_id,)
    return conn.execute(query, params).fetchone() is not None


def filter_existing_sources(results):
    conn = _get_conn()
    try:
        return [r for r in results if source_exists(conn, r.get('content_type'), r.get('reference_id'))]
    finally:
        conn.close()
