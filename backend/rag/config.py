"""RAG 配置管理"""
import time
import json
import logging
import os
import sqlite3

from db._conn import DB_PATH

logger = logging.getLogger(__name__)

_RAG_CONFIG_DEFAULTS = {
    "score_threshold": ("0.018", "RRF 分数阈值，低于此值的结果被过滤"),
    "rrf_k": ("60", "RRF (Reciprocal Rank Fusion) k 参数"),
    "and_fallback_threshold": ("3", "FTS5 AND 查询结果少于此值时降级为 OR"),
    "max_context_chars": ("3000", "上下文最大字符数"),
    "body_preview_chars": ("600", "正文预览字符数"),
    "book_diversity_penalty": ("0.85", "同书惩罚系数（超过 2 条后每条乘此系数）"),
    "short_content_penalty": ("0.9", "短内容惩罚系数（<200 字的内容）"),
    "short_content_threshold": ("200", "短内容阈值（字符数）"),
    "dual_hit_boost": ("1.15", "双通道命中加成系数"),
    "chroma_max_distance": ("0.8", "ChromaDB 最大余弦距离阈值"),
    "rrf_top_n": ("5", "每个来源参与 RRF 融合的最大候选数"),
    "rrf_cross_bonus": ("0.01", "跨来源（FTS+Chroma）命中加成"),
}

_rag_config_cache: dict[str, str] = {}
_rag_config_cache_ts: float = 0
_RAG_CONFIG_CACHE_TTL = 300  # 5 分钟

def get_rag_config(key: str, default=None):
    """从 rag_config 表读取配置，带 5 分钟内存缓存。

    Args:
        key: 配置键名
        default: 默认值（如果表中不存在）

    Returns:
        配置值（字符串）
    """
    import time
    global _rag_config_cache, _rag_config_cache_ts

    now = time.time()
    if now - _rag_config_cache_ts > _RAG_CONFIG_CACHE_TTL:
        _rag_config_cache.clear()
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT key, value FROM rag_config").fetchall()
            for row in rows:
                _rag_config_cache[row["key"]] = row["value"]
            conn.close()
            _rag_config_cache_ts = now
        except Exception as e:
            logger.warning(f"读取 rag_config 失败: {e}")

    if key in _rag_config_cache:
        return _rag_config_cache[key]

    # 回退到默认值
    if default is not None:
        return str(default)
    if key in _RAG_CONFIG_DEFAULTS:
        return _RAG_CONFIG_DEFAULTS[key][0]
    return None


def get_rag_config_float(key: str, default: float = 0.0) -> float:
    """读取浮点型配置。"""
    val = get_rag_config(key, str(default))
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_rag_config_int(key: str, default: int = 0) -> int:
    """读取整型配置。"""
    val = get_rag_config(key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _invalidate_rag_config_cache():
    """使配置缓存失效（更新配置后调用）。"""
    global _rag_config_cache_ts
    _rag_config_cache_ts = 0

# Reranker 配置：默认关闭（轻量级重排序已够用，reranker 增加 500ms+ 延迟）
# 设置环境变量 RERANK_ENABLED=true 可开启
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() == "true"

# Reranker（延迟加载，首次使用时初始化）
_reranker = None
_reranker_model_name = "BAAI/bge-reranker-base"


