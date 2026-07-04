"""查询改写引擎 — 规则 + LLM 混合改写，解决多轮对话中的代词/省略问题。

借鉴企业级 RAG 多轮对话题：用户第 2 轮"那差旅标准是多少"需改写为完整 query。

策略：
- 80% 情况：规则改写（代词替换、主题补充），零 LLM 成本
- 20% 情况：规则搞不定的复杂场景用 LLM 改写，成本 < 100 tokens/次
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 改写触发器 ──────────────────────────────────────────────

# 代词触发词（注意：\b 在中文字符间不生效，改用位置锚定）
PRONOUN_PATTERNS = [
    r"^那", r"^这", r"^它", r"^它们",
    r"^他", r"^她", r"^他们",
    r"^这些", r"^那些", r"^该",
    r"^其", r"^刚才说的", r"^上面",
    r"^你的建议", r"^那个", r"^这个",
    r"那个", r"这个", r"这些", r"那些",  # 句中匹配
]

# 上下文依赖模式（简短问题，缺乏明确主语）
CONTEXT_PATTERNS = [
    r"现在能买吗",
    r"要卖吗",
    r"怎么看",
    r"多少仓位",
    r"具体说说",
    r"还有吗",
    r"然后呢",
    r"^所以",
    r"^继续",
    r"详细说",
    r"^为什么",
    r"怎么办",
]


def needs_rewrite(query: str) -> tuple[bool, str]:
    """判断当前 query 是否依赖上下文。

    Returns:
        (True, "pronoun")  → 含代词
        (True, "short")    → 简短问题，缺乏上下文
        (False, "")        → 无需改写
    """
    query_stripped = query.strip()
    for pat in PRONOUN_PATTERNS:
        if re.search(pat, query_stripped):
            return True, "pronoun"
    for pat in CONTEXT_PATTERNS:
        if re.search(pat, query_stripped):
            return True, "short"
    return False, ""


# ── 规则改写 ────────────────────────────────────────────────

# 核心名词抽取模式
_NOUN_PATTERNS = [
    r"\b\d{6}\b",                           # 基金代码（6位数字）
    r"[\u4e00-\u9fa5]{2,8}(?:指数|基金|ETF|LOF)",  # 名称+指数/基金
    r"[\u4e00-\u9fa5]{2,6}(?:A股|港股|美股)",       # 市场
]


def _extract_core_nouns(text: str) -> list[str]:
    """从文本中抽取核心名词（基金代码/名称/指数）。"""
    nouns: list[str] = []
    for pat in _NOUN_PATTERNS:
        nouns.extend(re.findall(pat, text))
    # 去重保序
    seen = set()
    unique = []
    for n in nouns:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique


def _extract_topic(text: str) -> str:
    """从上一轮问题中提取主题关键词。"""
    nouns = _extract_core_nouns(text)
    if nouns:
        return nouns[0]
    # 没有 noun 时取前 10 字
    return text.strip()[:10]


def _rewrite_by_rules(query: str, history: list[dict], reason: str) -> tuple[bool, str]:
    """规则改写策略。

    Returns:
        (need_llm, rewritten_query) — need_llm=True 表示规则搞不定，交给 LLM
    """
    if not history or reason == "none":
        return True, query  # 没上下文，交给 LLM

    last_turn = history[-1]
    last_query = last_turn.get("query", "") or last_turn.get("content", "") or ""
    last_answer = last_turn.get("answer", "") or last_turn.get("analysis", "") or ""

    if reason == "pronoun":
        # 抽取上一轮的核心名词
        core_nouns = _extract_core_nouns(last_query + " " + last_answer)
        if core_nouns:
            # 用核心名词补全 query
            topic = core_nouns[0]
            rewritten = f"{query}（关于{topic}）"
            return False, rewritten
        return True, query  # 规则没抽到，交给 LLM

    if reason == "short":
        # 从上一轮提取主题
        topic = _extract_topic(last_query)
        if topic:
            return False, f"{query}（关于{topic}）"
        return True, query

    return True, query


# ── LLM 改写 ────────────────────────────────────────────────

def _format_history(history: list[dict]) -> str:
    """格式化历史对话为 LLM 可读文本。"""
    lines = []
    for msg in history[-2:]:  # 最近 2 轮
        role = msg.get("role", "user")
        content = msg.get("query", "") or msg.get("content", "") or ""
        answer = msg.get("answer", "") or msg.get("analysis", "") or ""
        if role == "user" and content:
            lines.append(f"用户: {content[:150]}")
        if answer:
            lines.append(f"助手: {answer[:150]}")
    return "\n".join(lines) if lines else "（无历史）"


def _rewrite_by_llm(query: str, history: list[dict]) -> str:
    """LLM 改写（规则搞不定的复杂情况）。

    只对改写结果做合法性检查，不可信时保留原 query。
    """
    prompt = f"""请将以下对话中的用户问题改写为完整的、不含代词的自包含问题。

历史对话（最近2轮）：
{_format_history(history)}

当前用户问题：{query}

要求：
1. 补充会话历史中已经提到的核心实体（基金名称、代码、策略等）
2. 将代词替换为具体名词
3. 保持原问题的意图不变
4. 只输出改写后的结果，不要解释，不要加引号"""

    try:
        from services.llm_service import _call_llm, MODEL
        from db.config import get_config_float
        resp = _call_llm(
            caller="query_rewriter",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=100,
        )
        rewritten = (resp.choices[0].message.content or "").strip()
        # 合法性检查：非空、不太长、不包含解释性前缀
        if rewritten and 3 < len(rewritten) < 200:
            # 去掉可能的引号包裹
            rewritten = rewritten.strip("\"'""「」")
            if rewritten and rewritten != query:
                return rewritten
    except Exception as e:
        logger.warning(f"LLM 查询改写失败: {e}")

    return query  # 改写失败，保底返回原 query


# ── 对外入口 ────────────────────────────────────────────────

def rewrite_query(query: str, history: list[dict] | None = None,
                  enable_llm: bool = True) -> tuple[str, dict]:
    """混合改写策略入口。

    Args:
        query: 当前用户问题
        history: 历史对话 [{"role": "user", "content": "...", "answer": "..."}]
        enable_llm: 是否允许 LLM 改写（配置开关可关闭）

    Returns:
        (rewritten_query, meta) — meta 包含改写信息
    """
    if not query or not query.strip():
        return query, {"rewritten": False, "reason": "empty"}

    history = history or []
    need, reason = needs_rewrite(query)
    if not need:
        return query, {"rewritten": False, "reason": "no_rewrite_needed"}

    # 先尝试规则改写
    need_llm, rewritten = _rewrite_by_rules(query, history, reason)
    if not need_llm and rewritten != query:
        logger.info(f"Query 规则改写: {query} → {rewritten}")
        return rewritten, {"rewritten": True, "method": "rule", "reason": reason}

    # 规则搞不定，交给 LLM
    if enable_llm:
        from db.config import get_config
        if get_config("query_rewriter.llm_enabled", "true") == "true":
            rewritten = _rewrite_by_llm(query, history)
            if rewritten != query:
                logger.info(f"Query LLM 改写: {query} → {rewritten}")
                return rewritten, {"rewritten": True, "method": "llm", "reason": reason}

    return query, {"rewritten": False, "reason": "rewrite_failed"}
