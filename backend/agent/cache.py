import hashlib
import time
import threading
import json
from datetime import datetime, timedelta
from typing import Optional, Any


def _compute_context_hash(context: str) -> str:
    """计算上下文的短哈希。"""
    if not context:
        return ""
    return hashlib.md5(context.encode("utf-8")).hexdigest()[:16]


def make_cache_key(analysis_type: str, **params) -> str:
    """统一缓存键生成。

    例: make_cache_key("daily_report", user_id="default", date="2026-07-02")
    → "daily_report:default:2026-07-02"
    """
    parts = [analysis_type]
    for key, value in sorted(params.items()):
        parts.append(str(value))
    return ":".join(parts)


class ExpertCache:
    """支持精确匹配 + 语义相似匹配的专家结果缓存。"""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 100, semantic_threshold: float = 0.92):
        self._cache: dict[str, tuple[Any, float]] = {}  # exact key -> (value, ts)
        self._semantic_entries: list[dict] = []         # [{agent_key, embedding, context_hash, value, ts}]
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._semantic_threshold = semantic_threshold
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._semantic_hits = 0
        self._embed_model = None

    def _get_embed_model(self):
        if self._embed_model is None:
            try:
                from rag import _get_embed_model
                self._embed_model = _get_embed_model()
            except Exception:
                self._embed_model = None
        return self._embed_model

    def _make_key(self, query: str, agent_key: str, context_hash: str = "") -> str:
        raw = f"{query}|{agent_key}|{context_hash}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _embed(self, query: str) -> Optional[list[float]]:
        model = self._get_embed_model()
        if model is None:
            return None
        try:
            import numpy as np
            vec = model.encode([query], normalize_embeddings=True)
            return vec[0].tolist()
        except Exception:
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        import numpy as np
        a_arr = np.array(a)
        b_arr = np.array(b)
        return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))

    def update_config(self, ttl_seconds: int | None = None, semantic_threshold: float | None = None):
        """线程安全地更新缓存配置。"""
        with self._lock:
            if ttl_seconds is not None:
                self._ttl = ttl_seconds
            if semantic_threshold is not None:
                self._semantic_threshold = semantic_threshold

    def _cleanup_expired(self):
        now = time.time()
        expired_keys = [k for k, (_, ts) in self._cache.items() if now - ts >= self._ttl]
        for k in expired_keys:
            del self._cache[k]
        self._semantic_entries = [
            e for e in self._semantic_entries if now - e["ts"] < self._ttl
        ]

    def get(self, query: str, agent_key: str, context_hash: str = "") -> Optional[Any]:
        with self._lock:
            self._cleanup_expired()
            # 1. 精确匹配
            exact_key = self._make_key(query, agent_key, context_hash)
            if exact_key in self._cache:
                value, ts = self._cache[exact_key]
                if time.time() - ts < self._ttl:
                    self._hits += 1
                    return value
                del self._cache[exact_key]

            # 2. 语义匹配
            query_emb = self._embed(query)
            if query_emb is not None:
                best = None
                best_score = 0.0
                for entry in self._semantic_entries:
                    if entry["agent_key"] != agent_key:
                        continue
                    if entry["context_hash"] != context_hash:
                        continue
                    score = self._cosine_similarity(query_emb, entry["embedding"])
                    if score > best_score:
                        best_score = score
                        best = entry
                if best and best_score >= self._semantic_threshold:
                    self._semantic_hits += 1
                    self._hits += 1
                    return best["value"]

            self._misses += 1
            return None

    def put(self, query: str, agent_key: str, value: Any, context_hash: str = ""):
        with self._lock:
            self._cleanup_expired()
            # 精确缓存
            exact_key = self._make_key(query, agent_key, context_hash)
            if len(self._cache) >= self._max_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]
            self._cache[exact_key] = (value, time.time())

            # 语义缓存
            query_emb = self._embed(query)
            if query_emb is not None:
                self._semantic_entries.append({
                    "agent_key": agent_key,
                    "embedding": query_emb,
                    "context_hash": context_hash,
                    "value": value,
                    "ts": time.time(),
                })
                # 限制语义条目数
                if len(self._semantic_entries) > self._max_size * 2:
                    self._semantic_entries.sort(key=lambda e: e["ts"])
                    self._semantic_entries = self._semantic_entries[-self._max_size:]

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "semantic_hits": self._semantic_hits,
            "hit_rate": self._hits / total if total > 0 else 0,
            "size": len(self._cache) + len(self._semantic_entries),
        }


# Global singleton
expert_cache = ExpertCache()


# ═══════════════════════════════════════════════════════════════
# L3 分析级缓存（数据库持久化）— 日报/周报等固定输出
# ═══════════════════════════════════════════════════════════════

class L3ReportCache:
    """分析级缓存（数据库持久化），支持历史回顾。

    复用已有的 analysis_cache 表（db/portfolio.py 中定义）。
    """

    def get_report(self, analysis_type: str, date_str: str) -> dict | None:
        """获取之前生成的分析报告。"""
        cache_key = make_cache_key("report", type=analysis_type, date=date_str)
        try:
            from db._conn import _get_conn
            conn = _get_conn()
            row = conn.execute(
                "SELECT data FROM analysis_cache WHERE cache_key = ?",
                (cache_key,)
            ).fetchone()
            conn.close()
            if row:
                return json.loads(row["data"])
        except Exception:
            pass
        return None

    def save_report(self, analysis_type: str, date_str: str, content: dict) -> bool:
        """保存分析报告。"""
        cache_key = make_cache_key("report", type=analysis_type, date=date_str)
        try:
            from db._conn import _get_conn
            conn = _get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO analysis_cache (cache_key, data, created_at) "
                "VALUES (?, ?, datetime('now','localtime'))",
                (cache_key, json.dumps(content, ensure_ascii=False))
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def invalidate(self, analysis_type: str = None):
        """按类型批量失效。"""
        try:
            from db._conn import _get_conn
            conn = _get_conn()
            if analysis_type:
                prefix = make_cache_key("report", type=analysis_type) + "%"
                conn.execute("DELETE FROM analysis_cache WHERE cache_key LIKE ?", (prefix,))
            else:
                conn.execute("DELETE FROM analysis_cache WHERE cache_key LIKE 'report:%'")
            conn.commit()
            conn.close()
        except Exception:
            pass


# 全局单例
l3_cache = L3ReportCache()


# ═══════════════════════════════════════════════════════════════
# 统一缓存入口 — 三级缓存编排
# ═══════════════════════════════════════════════════════════════

def invalidate_related_caches(event_type: str):
    """缓存失效策略。

    触发事件 → 失效相关缓存：
    - position_change: 失效 L3 持仓分析类报告
    - new_day: 失效所有 L3 当日报（由日期 key 自然过期）
    - force: 清空所有 L3
    """
    if event_type == "position_change":
        l3_cache.invalidate("diversification")
        l3_cache.invalidate("health_score")
        l3_cache.invalidate("panorama")
    elif event_type == "force":
        l3_cache.invalidate()

