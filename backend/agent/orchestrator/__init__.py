"""Orchestrator 聚合模块 — 从原 orchestrator.py（2272行）拆分为 4 个子模块：

  - routing.py      — 意图识别、复杂度判断、需求澄清、专家路由
  - context.py      — 文章缓存、RAG场景映射、上下文压缩
  - core.py         — 主控编排逻辑（orchestrate/orchestrate_stream）
  - peer_review.py  — 多模型评审

本文件聚合所有导出，对外接口不变。
"""

# 核心编排
from .core import (
    orchestrate,
    orchestrate_stream,
    CancelledError,
    build_orchestrator_tools,
    build_expert_map,
    build_orchestrator_system_prompt,
    _execute_specialist,
    _fallback_orchestrate,
    _check_cancel,
    _check_timeout,
    _detect_specialist_disagreement,
    should_arbitrate,
)

# 路由
from .routing import (
    clarify_requirement,
    detect_complexity_by_keywords,
    route_to_specialists_by_keywords,
    _classify_complexity_by_rules,
    detect_scenario_type,
    build_clarification_prompt,
)

# 上下文
from .context import (
    detect_urls,
    fetch_article_content,
    enrich_query_with_article,
    get_orchestration_config,
    check_token_budget,
    build_scenario_rag_context,
    get_context_config,
    compress_history,
    compress_rag_context,
)

# 多模型评审
from .peer_review import run_peer_review

__all__ = [
    "orchestrate", "orchestrate_stream", "CancelledError",
    "clarify_requirement", "detect_complexity_by_keywords",
    "route_to_specialists_by_keywords", "run_peer_review",
    "detect_urls", "fetch_article_content", "enrich_query_with_article",
    "check_token_budget", "build_scenario_rag_context",
    "compress_history", "compress_rag_context",
]
