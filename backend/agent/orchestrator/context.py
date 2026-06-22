"""文章缓存、URL检测、RAG场景映射、上下文压缩"""
import json
import logging
import re
import time
import asyncio

from agent.memory import estimate_tokens

logger = logging.getLogger(__name__)


# ── 文章缓存（避免 orchestrator 预抓取与 agent 工具双重抓取） ──
_article_cache: dict[str, dict] = {}
_ARTICLE_CACHE_MAX = 32

def _cache_article(url: str, article: dict):
    """缓存文章抓取结果。"""
    if len(_article_cache) >= _ARTICLE_CACHE_MAX:
        # 移除最早的条目
        oldest = next(iter(_article_cache))
        del _article_cache[oldest]
    _article_cache[url] = article


def detect_urls(text: str) -> list[str]:
    """检测文本中的 URL。"""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    return re.findall(url_pattern, text)


def fetch_article_content(url: str) -> dict | None:
    """同步获取文章内容（用于 orchestrator 中）。优先使用缓存。"""
    # 先查缓存
    if url in _article_cache:
        logger.info(f"文章缓存命中: {url}")
        return _article_cache[url]

    try:
        from article_reader import fetch_article
        # 在后台线程中创建新的事件循环运行异步函数
        # （不能用 asyncio.get_event_loop()，后台线程没有默认事件循环）
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(fetch_article(url))
        except Exception as e:
            logger.warning(f"获取文章失败: {e}")
            return None
        finally:
            loop.close()

        # 缓存成功结果
        if result:
            _cache_article(url, result)
        return result
    except ImportError:
        logger.warning("article_reader 模块未安装")
        return None


def enrich_query_with_article(query: str) -> tuple[str, str]:
    """
    检测查询中的链接，抓取文章内容并注入到查询中。

    返回: (enriched_query, article_context)
    article_context 为失败提示时以 "[抓取失败]" 开头。
    """
    urls = detect_urls(query)
    if not urls:
        return query, ""

    # 只处理第一个链接
    url = urls[0]
    logger.info(f"检测到链接: {url}，正在抓取文章内容...")

    article = fetch_article_content(url)
    if not article:
        logger.warning(f"文章抓取失败: {url}")
        fail_hint = (
            f"[抓取失败] 无法获取链接内容: {url}\n"
            "可能原因：链接已失效、被防爬机制拦截、或非文章页面。\n"
            "请引导用户：1) 检查链接是否可正常打开 2) 直接粘贴文章正文。"
        )
        enriched_query = f"{query}\n\n{fail_hint}"
        return enriched_query, fail_hint

    title = article.get("title", "未知标题")
    content = article.get("content_text", "") or article.get("content", "")
    author = article.get("author", "")
    publish_time = article.get("publish_time", "")

    if not content:
        logger.warning(f"文章内容为空: {url}")
        fail_hint = (
            f"[抓取失败] 链接已打开但未提取到正文: {url}\n"
            "请引导用户直接粘贴文章正文。"
        )
        enriched_query = f"{query}\n\n{fail_hint}"
        return enriched_query, fail_hint

    # 截取策略：前 2000 + 后 1500，保留开头背景和结尾结论
    max_chars = 3500
    if len(content) > max_chars:
        head = content[:2000]
        tail = content[-1500:]
        content = f"{head}\n\n...（中间内容省略）...\n\n{tail}"

    # 构建文章上下文
    meta_parts = [f"标题: {title}"]
    if author:
        meta_parts.append(f"作者: {author}")
    if publish_time:
        meta_parts.append(f"发布时间: {publish_time}")
    meta_parts.append(f"来源: {url}")

    article_context = f"""## 参考文章
{chr(10).join(meta_parts)}

{content}"""

    # 注入查询，明确告知 agent 已提供文章内容，避免重复调用 fetch_article
    enriched_query = (
        f"{query}\n\n"
        "请参考以下文章内容进行分析（文章已抓取完毕，无需再次调用 fetch_article 工具）：\n"
        f"{article_context}"
    )

    logger.info(f"文章抓取成功: {title} ({len(content)} 字符)")
    return enriched_query, article_context



def get_orchestration_config(key: str, default=None):
    """从数据库读取编排配置。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute("SELECT value FROM orchestration_config WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default



# ── Token 预算检查 ──────────────────────────────────────────

def check_token_budget() -> dict:
    """检查今日 token 用量是否超限。

    返回:
        {"ok": bool, "used": int, "limit": int, "pct": float,
         "mode": "normal"|"conservative"|"exceeded"}
    """
    from config import DAILY_TOKEN_LIMIT, TOKEN_WARN_THRESHOLD, TOKEN_BUDGET_BYPASS
    from db import get_today_token_total

    used = get_today_token_total()
    pct = used / DAILY_TOKEN_LIMIT if DAILY_TOKEN_LIMIT > 0 else 0

    if TOKEN_BUDGET_BYPASS:
        return {"ok": True, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "normal"}

    if pct >= 1.0:
        return {"ok": False, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "exceeded"}
    elif pct >= TOKEN_WARN_THRESHOLD:
        return {"ok": True, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "conservative"}
    else:
        return {"ok": True, "used": used, "limit": DAILY_TOKEN_LIMIT, "pct": pct, "mode": "normal"}



# ── 场景化 RAG 映射 ──────────────────────────────────────

SCENARIO_RAG_MAP = {
    "valuation_expert": {
        "query_suffix": "估值 PE PB 安全边际 内在价值 百分位",
        "content_types": ["book", "valuation", "analysis"],
    },
    "market_analyst": {
        "query_suffix": "市场周期 择时 牛熊 情绪 资金流向",
        "content_types": ["book", "article", "author_article", "valuation"],
    },
    "allocation_advisor": {
        "query_suffix": "资产配置 分散投资 仓位 再平衡 股债比例",
        "content_types": ["book", "valuation", "analysis"],
    },
    "risk_assessor": {
        "query_suffix": "风险 回撤 波动 最大回撤 风险控制",
        "content_types": ["book", "valuation", "analysis"],
    },
    "fund_analyst": {
        "query_suffix": "基金选择 业绩 费用 基金经理 跟踪误差",
        "content_types": ["book", "analysis", "valuation"],
    },
    "article_expert": {
        "query_suffix": "文章解读 观点分析 投资逻辑 研报",
        "content_types": ["article", "author_article", "book"],
    },
    "behavior_coach": {
        "query_suffix": "行为金融 心理偏差 追涨杀跌 损失厌恶 过度自信 情绪",
        "content_types": ["book", "analysis"],
    },
    "counter_argument": {
        "query_suffix": "反方观点 反例 失效条件 风险边界 决策清单 安全边际",
        "content_types": ["book", "analysis", "valuation"],
    },
}


def build_scenario_rag_context(query: str, specialists: list[str],
                                original_rag_context: str = "") -> str:
    """
    根据命中的专家类型，对原始 RAG 上下文做场景化增强。
    如果原始 RAG 已有内容，补充场景化检索结果；否则全新构建。
    """
    from rag import build_rag_context_with_details

    # 收集所有命中专家的场景配置
    scenario_queries = []
    all_content_types = set()
    for specialist in specialists:
        if specialist in SCENARIO_RAG_MAP:
            cfg = SCENARIO_RAG_MAP[specialist]
            scenario_queries.append(cfg["query_suffix"])
            all_content_types.update(cfg["content_types"])

    if not scenario_queries:
        return original_rag_context

    # 构建场景化查询：原始问题 + 场景关键词
    scenario_query = f"{query} {' '.join(scenario_queries)}"

    # 场景化检索（限制 3 条，避免过多）
    result = build_rag_context_with_details(
        scenario_query,
        content_types=list(all_content_types) if all_content_types else None,
        limit=3,
    )
    scenario_context = result.get("context", "")

    # 合并：原始 RAG + 场景化 RAG
    if original_rag_context and scenario_context:
        return f"{original_rag_context}\n\n---\n\n{scenario_context}"
    return scenario_context or original_rag_context


def get_context_config(complexity: str) -> dict:
    """根据复杂度返回上下文配置。"""
    if complexity == "simple":
        return {
            "history_limit": 3,      # 只保留最近3条历史
            "rag_enabled": True,     # 简单查询也启用 RAG（轻量级）
            "max_specialists": 1,    # 只调用1个专家
            "rag_max_chars": 800,    # 轻量级 RAG 上下文
        }
    elif complexity == "medium":
        return {
            "history_limit": 5,
            "rag_enabled": True,
            "max_specialists": 2,
            "rag_max_chars": 1500,   # RAG上下文压缩到1500字符
        }
    else:  # complex
        return {
            "history_limit": 10,
            "rag_enabled": True,
            "max_specialists": 5,
            "rag_max_chars": 2500,   # RAG上下文压缩到2500字符
        }


def compress_history(history: list, max_messages: int = 10) -> list:
    """
    压缩对话历史：
    - 保留最近 max_messages 条完整消息
    - 更早的消息只保留摘要（第一条用户消息的前50字）
    """
    if len(history) <= max_messages:
        return history

    # 早期消息：只保留摘要
    early_messages = history[:-max_messages]
    recent_messages = history[-max_messages:]

    # 从早期消息中提取关键信息
    summary_parts = []
    for msg in early_messages:
        if msg["role"] == "user":
            # 用户消息：取前50字
            summary_parts.append(f"用户曾问: {msg['content'][:50]}...")
        elif msg["role"] == "assistant":
            # 助手消息：取前30字
            summary_parts.append(f"助手曾答: {msg['content'][:30]}...")

    # 构建摘要消息
    if summary_parts:
        summary = "以下是早期对话摘要（省略了详细内容）：\n" + "\n".join(summary_parts[-5:])  # 最多保留5条摘要
        compressed = [{"role": "system", "content": summary}] + recent_messages
    else:
        compressed = recent_messages

    return compressed


def compress_rag_context(rag_context: str, max_chars: int = 2000) -> str:
    """
    压缩 RAG 上下文：
    - 截断到 max_chars 字符
    - 保留完整段落，避免截断在句子中间
    """
    if not rag_context or len(rag_context) <= max_chars:
        return rag_context

    # 截断到最大字符数
    truncated = rag_context[:max_chars]

    # 找到最后一个完整段落（双换行符）
    last_paragraph_end = truncated.rfind("\n\n")
    if last_paragraph_end > max_chars * 0.7:  # 如果截断点在70%以后
        truncated = truncated[:last_paragraph_end]

    return truncated + "\n...(已截断，更多内容请参考知识库)"

