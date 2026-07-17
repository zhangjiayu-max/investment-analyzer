"""工具调用结果缓存 — 同参数调用直接返回缓存结果，避免重复工具调用浪费 token。

设计要点：
- TTL 5 分钟，同一对话内的重复查询直接复用
- 按工具名+参数哈希做 key，参数顺序不影响命中
- 对 query_valuation / search_knowledge 等数据查询工具效果最显著
- 命中统计：hits/misses，便于审计

集成方式（在 execute_tool 中包装）：
    from agent.infra.tool_dedup import get_tool_call_cache
    cache = get_tool_call_cache()
    cached = cache.get(tool_name, args)
    if cached is not None:
        return cached
    result = execute_tool_original(tool_name, args)
    cache.set(tool_name, args, result)
    return result
"""

import hashlib
import json
import logging
import time
from typing import Any, Optional

from db.config import get_config_int, get_config_bool

logger = logging.getLogger(__name__)


# ── 可缓存的工具白名单 ─────────────────────────
# 只缓存纯数据查询类工具，不缓存会改变状态的工具（如写入、删除）
_CACHEABLE_TOOLS = {
    "query_valuation",       # 估值查询
    "search_knowledge",      # 知识库检索
    # P2-10: 移除 get_portfolio — 盈亏需实时刷新，5分钟缓存会导致数据不准
    "query_fund_info",       # 基金信息查询
    "ttfund_search",         # 基金搜索
    "get_index_valuation",   # 指数估值
    "get_market_status",     # 市场状态
    "get_valuation_history", # 估值历史
}


def is_cacheable(tool_name: str) -> bool:
    """判断工具是否可缓存。"""
    return tool_name in _CACHEABLE_TOOLS


class ToolCallCache:
    """工具调用结果缓存。

    用法：
        cache = ToolCallCache(ttl_seconds=300)
        cached = cache.get("query_valuation", {"index_code": "000300"})
        if cached is not None:
            return cached
        result = call_tool(...)
        cache.set("query_valuation", {"index_code": "000300"}, result)
    """

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 100):
        self._cache: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        # 统计
        self._hits = 0
        self._misses = 0

    def _key(self, tool_name: str, args: dict) -> str:
        """生成缓存 key：工具名 + 参数哈希。"""
        # 对参数排序后哈希，避免参数顺序影响缓存命中
        try:
            normalized = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # 不可序列化的参数，退化为字符串表示
            normalized = str(sorted(args.items(), key=lambda x: x[0]))
        digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]
        return f"{tool_name}:{digest}"

    def get(self, tool_name: str, args: dict) -> Optional[Any]:
        """获取缓存结果。

        Returns:
            缓存结果（命中），None（未命中或已过期）
        """
        if not is_cacheable(tool_name):
            self._misses += 1
            return None

        key = self._key(tool_name, args)
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        ts, result = entry
        if time.time() - ts > self._ttl:
            # 过期
            del self._cache[key]
            self._misses += 1
            return None

        self._hits += 1
        logger.debug(f"[tool_cache] 命中 {tool_name} (key={key[:20]})")
        return result

    def set(self, tool_name: str, args: dict, result: Any) -> None:
        """写入缓存。"""
        if not is_cacheable(tool_name):
            return

        # 容量保护：超过上限时清理最旧的 20%
        if len(self._cache) >= self._max_entries:
            self._evict_oldest(int(self._max_entries * 0.2))

        key = self._key(tool_name, args)
        self._cache[key] = (time.time(), result)

    def _evict_oldest(self, count: int) -> None:
        """清理最旧的 count 个条目。"""
        if not self._cache or count <= 0:
            return
        # 按时间戳排序，删除最旧的
        sorted_items = sorted(self._cache.items(), key=lambda x: x[1][0])
        for k, _ in sorted_items[:count]:
            del self._cache[k]
        logger.debug(f"[tool_cache] 清理 {min(count, len(sorted_items))} 个过期条目")

    # ── 统计与维护 ─────────────────────────────

    @property
    def stats(self) -> dict:
        """返回缓存统计。"""
        total = self._hits + self._misses
        hit_rate = (self._hits / total) if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "ttl_seconds": self._ttl,
        }

    def invalidate(self, tool_name: Optional[str] = None) -> int:
        """失效缓存。

        Args:
            tool_name: 指定工具名则只失效该工具的缓存，None 则清空全部

        Returns:
            失效条目数
        """
        if tool_name is None:
            count = len(self._cache)
            self._cache.clear()
            return count
        prefix = f"{tool_name}:"
        keys_to_del = [k for k in self._cache if k.startswith(prefix)]
        for k in keys_to_del:
            del self._cache[k]
        return len(keys_to_del)

    def reset_stats(self) -> None:
        """重置统计计数器（不清空缓存）。"""
        self._hits = 0
        self._misses = 0


# ── 全局单例 ──────────────────────────────────

_global_cache: Optional[ToolCallCache] = None


def get_tool_call_cache() -> ToolCallCache:
    """获取全局工具调用缓存实例。

    TTL 从配置 agent.tool_cache_ttl_seconds 读取，默认 300 秒。
    缓存开关从配置 agent.tool_cache_enabled 读取，默认 True。
    """
    global _global_cache
    if _global_cache is not None:
        return _global_cache

    # 被禁用时返回一个空操作的缓存（hits 永远为 0，所有 get 返回 None）
    enabled = True
    try:
        enabled = get_config_bool("agent.tool_cache_enabled", True)
    except Exception:
        pass

    if not enabled:
        _global_cache = _NoOpCache()
        return _global_cache

    ttl = 300
    try:
        ttl = get_config_int("agent.tool_cache_ttl_seconds", 300)
    except Exception:
        pass

    _global_cache = ToolCallCache(ttl_seconds=ttl)
    return _global_cache


def reset_tool_call_cache() -> None:
    """重置全局工具缓存（新对话开始或调试时调用）。"""
    global _global_cache
    _global_cache = None


class _NoOpCache(ToolCallCache):
    """缓存被禁用时的空操作实现。"""

    def __init__(self):
        super().__init__(ttl_seconds=0, max_entries=0)

    def get(self, tool_name: str, args: dict) -> Optional[Any]:
        self._misses += 1
        return None

    def set(self, tool_name: str, args: dict, result: Any) -> None:
        return None
