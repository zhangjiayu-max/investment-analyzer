"""RAG 模块聚合 — 从原 rag.py（2269行）拆分为 6 个子模块：

  - config.py    — 配置管理
  - reranker.py  — 重排序器、个性化加权
  - query.py     — 查询重写、分词、FTS查询构建
  - fts.py       — FTS5 全文索引、搜索
  - chroma.py    — ChromaDB 向量语义搜索
  - context.py   — RAG 上下文构建、批量索引

本文件聚合所有导出，对外接口不变。
"""

# 配置
from .config import (
    get_rag_config, get_rag_config_float, get_rag_config_int,
    _invalidate_rag_config_cache, _RAG_CONFIG_DEFAULTS,
)

# 重排序
from .reranker import rerank_results, _get_reranker, _apply_personalization_boost

# 查询
from .query import (
    rewrite_query, _tokenize, _get_jieba,
    _build_fts_query, _build_fts_query_core, _build_fts_query_relaxed,
    _sanitize_fts_token,
)

# FTS5
from .fts import (
    init_fts, search_knowledge, _index_document,
    index_article, index_valuation, index_analysis_record,
    index_author_article, index_skill_document, index_skill_extraction,
    _format_analysis_json, _get_conn, log_rag_search,
    build_rag_context,
)

# ChromaDB
from .chroma import (
    init_chroma, reset_chroma_collection,
    _ensure_embed_model, _get_chroma, _get_embed_model,
    _chunk_text, _chunk_by_structure,
    index_to_chroma, delete_chroma_by_filter,
    index_book_knowledge, index_note_knowledge,
    search_chroma, _filter_old_results, _enrich_results_with_time,
    _detect_index_names, _get_known_index_names, _inject_valuation_data,
)

# 上下文构建+批量索引
from .context import (
    build_rag_context_with_details,
    index_article_to_rag, index_analysis_to_rag,
    reindex_all_articles, reindex_all_analysis_records, reindex_all,
    get_rag_stats_summary,
)

__all__ = [
    "init_fts", "init_chroma", "search_knowledge", "search_chroma",
    "build_rag_context", "build_rag_context_with_details",
    "index_article", "index_valuation", "index_to_chroma",
    "index_author_article", "index_skill_document", "index_skill_extraction",
    "index_book_knowledge", "index_note_knowledge",
    "delete_chroma_by_filter", "log_rag_search",
    "rerank_results", "rewrite_query",
    "get_rag_config", "get_rag_config_float", "get_rag_config_int",
    "_get_chroma", "_get_embed_model", "_chunk_text",
]
