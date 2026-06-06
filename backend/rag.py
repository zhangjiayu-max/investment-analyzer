"""RAG 检索服务 — SQLite FTS5 全文检索 + ChromaDB 向量语义搜索

功能特性：
- FTS5 全文检索（jieba 中文分词）
- ChromaDB 向量语义搜索（bge-small-zh-v1.5）
- RRF 融合排序
- 查询扩展（同义词 + 知识图谱）
- 轻量级重排序
- Reranker 重排序（可选）
- 时效性管理
- 批量索引
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

from db._conn import DB_PATH
from rag_enhanced import expand_query, lightweight_rerank

logger = logging.getLogger(__name__)

CHROMA_DIR = Path(__file__).parent.parent / "data" / "chroma_db"

# Reranker 配置：默认关闭，RRF + 标题加权 + 时效性加权已能提供合理排序
# 设置环境变量 RERANK_ENABLED=true 可开启
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() == "true"

# Reranker（延迟加载，首次使用时初始化）
_reranker = None
_reranker_model_name = "BAAI/bge-reranker-base"


def _get_reranker():
    """获取 Reranker 模型（延迟加载）。"""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(_reranker_model_name)
            logger.info(f"Reranker 模型加载成功: {_reranker_model_name}")
        except Exception as e:
            logger.warning(f"Reranker 模型加载失败: {e}")
            _reranker = False
    return _reranker


def rerank_results(query: str, results: list[dict], top_k: int = 5) -> list[dict]:
    """对检索结果进行重排序（使用 cross-encoder）。

    Args:
        query: 用户查询
        results: 检索结果列表
        top_k: 返回前 k 个结果

    Returns:
        重排序后的结果列表
    """
    if not results or len(results) <= 1:
        return results

    reranker = _get_reranker()
    if not reranker:
        return results[:top_k]

    try:
        # 构建 query-document 对（扩展到 1024 字符，保留更多上下文）
        pairs = [(query, r.get("body", "")[:1024]) for r in results]

        # 计算相关性分数
        scores = reranker.predict(pairs)

        # 将分数添加到结果中
        for i, score in enumerate(scores):
            results[i]["rerank_score"] = float(score)

        # 按 rerank 分数排序
        results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

        return results[:top_k]
    except Exception as e:
        logger.error(f"Rerank 失败: {e}")
        return results[:top_k]


def rewrite_query(query: str) -> str:
    """将用户口语化问题转换为更适合检索的查询。

    使用 LLM 将自然语言查询转换为关键词查询，提升检索效果。

    Args:
        query: 用户原始查询

    Returns:
        优化后的查询字符串
    """
    try:
        from llm_service import _call_llm

        prompt = f"""将以下用户问题转换为适合知识库检索的关键词查询。

用户问题：{query}

要求：
1. 提取核心关键词（2-5个）
2. 去除口语化表达（如"我想"、"可以吗"、"怎么样"）
3. 保留投资术语（如"估值"、"百分位"、"PE"）
4. 如果涉及指数，保留指数名称
5. 输出格式：关键词1 关键词2 关键词3

示例：
- "白酒现在估值高吗" → "白酒 估值 百分位"
- "沪深300可以买吗" → "沪深300 估值 买入"
- "机器人指数怎么样" → "机器人 指数 估值"

只输出关键词，不要其他文字。"""

        rewritten = _call_llm(prompt, temperature=0.1, max_tokens=50)

        # 清理输出
        rewritten = rewritten.strip()
        if rewritten and len(rewritten) < len(query) * 2:
            logger.info(f"Query Rewrite: '{query}' -> '{rewritten}'")
            return rewritten

        return query
    except Exception as e:
        logger.warning(f"Query Rewrite 失败: {e}")
        return query


# 中文分词（延迟导入，首次使用时加载）
_jieba = None


def _get_jieba():
    global _jieba
    if _jieba is None:
        try:
            import jieba
            _jieba = jieba
        except ImportError:
            _jieba = False
    return _jieba


def _tokenize(text: str) -> str:
    """用 jieba 分词，返回空格分隔的词语。"""
    jb = _get_jieba()
    if jb:
        return " ".join(jb.cut(text))
    # jieba 不可用时，按字符分割（效果较差但不报错）
    return " ".join(text)


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_fts():
    """初始化 FTS5 虚拟表，应用启动时调用。

    使用 unicode61 tokenizer，配合 jieba 预分词：
    - 入库时：jieba 分词 -> 空格连接 -> 存入 FTS5
    - 查询时：jieba 分词 -> 空格连接 -> FTS5 MATCH
    """
    conn = _get_conn()
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            content_type,
            title,
            body,
            reference_id UNINDEXED,
            tokenize='unicode61'
        )
    """)
    conn.commit()
    conn.close()


def _index_document(content_type: str, title: str, body: str, reference_id: str):
    """插入或更新一条 FTS 索引记录。"""
    conn = _get_conn()
    # 先删除旧记录（幂等）
    conn.execute(
        "DELETE FROM knowledge_fts WHERE content_type = ? AND reference_id = ?",
        (content_type, reference_id),
    )
    # 插入分词后的内容
    tokenized_title = _tokenize(title) if title else ""
    tokenized_body = _tokenize(body) if body else ""
    conn.execute(
        "INSERT INTO knowledge_fts (content_type, title, body, reference_id) VALUES (?, ?, ?, ?)",
        (content_type, tokenized_title, tokenized_body, reference_id),
    )
    conn.commit()
    conn.close()


def index_article(article_id: int, title: str, content: str):
    """索引一篇文章。"""
    # 截取前 5000 字符建索引（FTS5 对超长文本性能下降）
    _index_document("article", title or "", content[:5000] if content else "", str(article_id))


def index_valuation(index_code: str, index_name: str, valuation_data: dict):
    """索引一条估值数据（含日期，便于判断时效性）。"""
    date_str = valuation_data.get("snapshot_date", "")
    metric = valuation_data.get("metric_type", "")
    cur_val = valuation_data.get("current_value", "")
    pct = valuation_data.get("percentile", "")
    zscore = valuation_data.get("zscore", "")
    danger = valuation_data.get("danger_value", "")
    opp = valuation_data.get("opportunity_value", "")
    median = valuation_data.get("median", "")

    # 构建包含日期的可读 body
    title = f"{index_name or index_code} {metric}估值"
    body_parts = [f"日期: {date_str}"] if date_str else []
    body_parts.extend([
        f"指数: {index_name or index_code}",
        f"指标: {metric}",
        f"当前值: {cur_val}",
        f"百分位: {pct}%",
        f"Z-Score: {zscore}",
        f"危险值: {danger}",
        f"机会值: {opp}",
        f"中位数: {median}",
    ])
    _index_document("valuation", title, " | ".join(body_parts), index_code)


def index_analysis_record(record_id: int, index_name: str, raw_response: str):
    """索引一条分析记录。"""
    _index_document("analysis", index_name or "", raw_response[:3000] if raw_response else "", str(record_id))


def index_author_article(article_id: int, title: str, content: str, publish_time: str = ""):
    """索引一篇作者文章（含发布日期，便于判断时效性）。"""
    # 在 body 开头添加日期前缀，让 LLM 能判断内容时效性
    date_prefix = f"[发布日期: {publish_time}] " if publish_time else ""
    _index_document("author_article", title or "", date_prefix + (content[:5000] if content else ""), str(article_id))


def index_skill_document(doc_id: int, title: str, content: str):
    """索引一篇 Skill 文档（蒸馏后的结构化知识）。"""
    _index_document("skill", title or "", content[:8000] if content else "", str(doc_id))


def index_skill_extraction(article_id: int, title: str, skill_data: dict):
    """索引一篇文章的技能提取结果（按维度拆分）。"""
    parts = []
    if skill_data.get("cognitive_framework"):
        parts.append("认知框架: " + "; ".join(skill_data["cognitive_framework"]))
    if skill_data.get("behavior_patterns"):
        parts.append("言行模式: " + "; ".join(skill_data["behavior_patterns"]))
    if skill_data.get("knowledge_strengths"):
        parts.append("擅长领域: " + "; ".join(skill_data["knowledge_strengths"]))
    if skill_data.get("classic_quotes"):
        parts.append("经典观点: " + "; ".join(skill_data["classic_quotes"][:3]))
    body = "\n".join(parts)
    _index_document("skill", title or "", body[:3000], str(article_id))


def _sanitize_fts_token(token: str) -> str:
    """处理 FTS5 特殊字符：含特殊字符或多字中文 token 用双引号包裹做短语匹配。"""
    import re
    if re.search(r'[%*"():^\-]', token):
        return f'"{token}"'
    # 多字中文 token 加引号，让 FTS5 做短语匹配而非单字符匹配
    # "债券" → "\"债券\"" → FTS5 要求"债"和"券"相邻出现
    if len(token) >= 2 and all('一' <= c <= '鿿' for c in token):
        return f'"{token}"'
    return token


# ── 共享停用词表（FTS 查询和 RAG 上下文构建共用）─────────────────
# 注意：投资领域高价值词汇（估值、投资、分析、趋势等）已移除，避免检索退化
_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "都", "上", "也",
    "到", "说", "要", "去", "你", "会", "着", "看", "好", "这", "他", "她",
    "能", "下", "过", "么", "吗", "呢", "把", "让", "被", "从", "向", "对",
    "以", "可以", "应该", "需要", "现在", "目前", "当前", "最近", "怎么样",
    "如何", "什么", "多少", "为什么", "怎样", "哪个", "哪些", "是否",
    "看看", "帮忙", "帮我", "请问", "想问", "情况", "那", "个", "一", "人",
    "很", "没有", "点", "想", "入手", "帮", "问", "数据",
}


def _build_fts_query(query: str) -> str:
    """将用户问题转为 FTS5 查询：分词后过滤停用词，核心词用 AND、辅助词用 OR。

    优化策略：
    1. 对中文多字词使用 NEAR 查询，允许词之间有间隔
    2. 保留核心投资术语的完整性
    3. 对长句提取关键短语
    """
    tokens = _tokenize(query).split()
    # 先过滤停用词，再清洗特殊字符（避免引号影响停用词匹配）
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    cleaned = [_sanitize_fts_token(t) for t in tokens]
    cleaned = [t for t in cleaned if t]
    multi_char = [t for t in cleaned if len(t) >= 2]
    single_char = [t for t in cleaned if len(t) == 1 and '一' <= t <= '鿿']

    if not multi_char and not single_char:
        return ""

    # 优先用 AND 连接多字关键词（精确匹配），单字关键词作为补充用 OR
    if multi_char:
        # 改进：使用 AND 连接，允许词之间有间隔
        core = " AND ".join(multi_char)
        if single_char:
            # 核心词 AND，辅助单字 OR
            return f"({core}) OR ({' OR '.join(single_char)})"
        return core
    return " OR ".join(single_char)


def _extract_key_phrases(query: str) -> list[str]:
    """从查询中提取关键短语（投资术语、指数名称等）。"""
    # 投资术语词典
    investment_terms = {
        "估值", "百分位", "市盈率", "市净率", "股息率", "风险溢价",
        "低估", "高估", "合理", "买入", "卖出", "持有", "定投",
        "沪深300", "中证500", "创业板", "科创板", "红利", "消费",
        "医药", "科技", "新能源", "白酒", "银行", "证券", "地产",
        "债券", "可转债", "QDII", "ETF", "LOF", "指数基金",
    }

    phrases = []
    # 提取匹配的投资术语
    for term in investment_terms:
        if term in query:
            phrases.append(term)

    # 提取指数代码（如 000300、399006）
    import re
    codes = re.findall(r'\b\d{6}\b', query)
    phrases.extend(codes)

    return phrases


# ── 时效性策略：不同类型内容的有效期不同 ──────────────────
# author_article: 市场评论/行情分析，时效性强，2个月
# skill: 投资方法论/认知框架，长期有效，12个月
# valuation: 实时估值数据，不过滤
# article/analysis: 时效性强，2个月
_FRESHNESS_POLICY = {
    "author_article": 2,
    "skill": 12,  # 投资方法论长期有效，12个月
    "valuation": 0,   # 0 = 不过滤
    "article": 2,
    "analysis": 2,
    "linked_doc": 0,  # 个人文档不过滤
}


def _build_fts_query_relaxed(query: str) -> str:
    """生成宽松的 FTS5 查询（OR 连接），作为 AND 无结果时的降级方案。"""
    tokens = _tokenize(query).split()
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    cleaned = [_sanitize_fts_token(t) for t in tokens]
    cleaned = [t for t in cleaned if t]
    if not cleaned:
        return ""
    return " OR ".join(cleaned)


def search_knowledge(query: str, content_type: str = None, limit: int = 5) -> tuple[list[dict], int]:
    """FTS5 全文检索，按内容类型自动过滤过时数据。返回 (结果列表, 被过滤条数)。"""
    fts_query = _build_fts_query(query)
    if not fts_query:
        return [], 0

    conn = _get_conn()

    def _execute(q: str):
        if content_type:
            return conn.execute("""
                SELECT content_type, title, body, reference_id, rank
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ? AND content_type = ?
                ORDER BY rank
                LIMIT ?
            """, (q, content_type, limit * 3)).fetchall()
        else:
            return conn.execute("""
                SELECT content_type, title, body, reference_id, rank
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (q, limit * 3)).fetchall()

    try:
        rows = _execute(fts_query)
    except Exception:
        rows = []

    # AND 无结果时降级为 OR
    if not rows:
        relaxed_query = _build_fts_query_relaxed(query)
        if relaxed_query and relaxed_query != fts_query:
            try:
                rows = _execute(relaxed_query)
            except Exception:
                rows = []
                conn.close()
                return [], 0

    results = [dict(r) for r in rows]
    results, dropped = _filter_old_results(results, conn=conn)
    conn.close()
    return results[:limit], dropped


def build_rag_context(query: str, content_types: list[str] = None, limit: int = 5) -> str:
    """检索知识库并格式化为 LLM 上下文文本。"""
    result = build_rag_context_with_details(query, content_types, limit)
    return result["context"]


def log_rag_search(conversation_id: int, message_id: int, query: str, keywords: list,
                   results: list, content_types: list = None,
                   fts_count: int = 0, chroma_count: int = 0,
                   freshness_filtered: int = 0, trace_id: str = ""):
    """记录 RAG 检索日志到数据库。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            query TEXT,
            keywords TEXT,
            content_types TEXT,
            results_count INTEGER,
            results TEXT,
            fts_count INTEGER DEFAULT 0,
            chroma_count INTEGER DEFAULT 0,
            freshness_filtered INTEGER DEFAULT 0,
            result_sources TEXT,
            result_times TEXT,
            trace_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 兜底：为已有表添加新字段（ALTER IF NOT EXISTS 不存在，用 try 忽略重复）
    for col, typ in [("fts_count", "INTEGER DEFAULT 0"), ("chroma_count", "INTEGER DEFAULT 0"),
                     ("freshness_filtered", "INTEGER DEFAULT 0"), ("result_sources", "TEXT"),
                     ("result_times", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE rag_logs ADD COLUMN {col} {typ}")
        except Exception:
            pass
    conn.commit()

    # 提取每条结果的来源和时间
    result_sources = [r.get("source", "") for r in results]
    result_times = [r.get("time", "") for r in results]

    # 兜底：为已有表添加 trace_id 字段
    try:
        conn.execute("ALTER TABLE rag_logs ADD COLUMN trace_id TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()

    conn.execute("""
        INSERT INTO rag_logs (conversation_id, message_id, query, keywords, content_types,
                              results_count, results, fts_count, chroma_count,
                              freshness_filtered, result_sources, result_times, trace_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        conversation_id,
        message_id,
        query,
        json.dumps(keywords, ensure_ascii=False),
        json.dumps(content_types, ensure_ascii=False),
        len(results),
        json.dumps(results, ensure_ascii=False),
        fts_count,
        chroma_count,
        freshness_filtered,
        json.dumps(result_sources, ensure_ascii=False),
        json.dumps(result_times, ensure_ascii=False),
        trace_id,
    ))
    conn.commit()
    conn.close()


# ── ChromaDB 向量语义搜索 ──────────────────────────────────────

_chroma_collection = None
_embed_model = None


def init_chroma():
    """初始化 ChromaDB，应用启动时调用。embedding 模型延迟加载。"""
    global _chroma_collection
    if _chroma_collection is not None:
        return

    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _chroma_collection = client.get_or_create_collection(
        name="knowledge",
        metadata={"hnsw:space": "cosine"},
    )


def _ensure_embed_model():
    """延迟加载 embedding 模型（首次调用时下载 ~100MB）。"""
    global _embed_model
    if _embed_model is not None:
        return _embed_model

    import os
    import ssl
    import warnings
    warnings.filterwarnings("ignore", message=".*certificate.*")

    # 修复 macOS SSL 证书问题
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_SSL_VERIFICATION"] = "1"
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""

    # 强制禁用 SSL 验证
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except Exception:
        pass

    from sentence_transformers import SentenceTransformer
    try:
        _embed_model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
        logger.info("Embedding 模型从本地加载成功")
    except Exception as e:
        logger.warning(f"本地加载失败，尝试在线下载: {e}")
        # 如果本地文件不完整，尝试在线下载（忽略 SSL 错误）
        import httpx
        old_client = httpx.Client
        class NoVerifyClient(old_client):
            def __init__(self, *args, **kwargs):
                kwargs["verify"] = False
                super().__init__(*args, **kwargs)
        httpx.Client = NoVerifyClient
        try:
            _embed_model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
            logger.info("Embedding 模型在线下载成功")
        except Exception as e2:
            logger.error(f"Embedding 模型加载失败: {e2}")
            _embed_model = None
        finally:
            httpx.Client = old_client
    return _embed_model


def _get_chroma():
    """获取 ChromaDB collection，未初始化则返回 None。"""
    return _chroma_collection


def _get_embed_model():
    """获取 embedding 模型，延迟加载。"""
    return _ensure_embed_model()


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """将长文本切分为 chunk_size 字符的块，优先按语义边界切分。

    语义感知切分策略：
    1. 优先按段落（\\n\\n）切分
    2. 其次按句子（。！？；）切分
    3. 最后按字符位置切分

    Args:
        text: 待切分文本
        chunk_size: 目标 chunk 大小（字符数）
        overlap: 重叠字符数

    Returns:
        chunk 列表
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    # 语义边界标记（按优先级排序）
    separators = ["\n\n", "\n", "。", "！", "？", "；", "，", "、"]

    chunks = []
    start = 0

    while start < len(text):
        # 计算理想的结束位置
        ideal_end = start + chunk_size

        if ideal_end >= len(text):
            # 剩余文本不足一个 chunk，直接取完
            chunk = text[start:]
            if chunk.strip():
                chunks.append(chunk)
            break

        # 在理想结束位置附近寻找语义边界
        best_end = ideal_end
        search_window = min(200, chunk_size // 4)  # 搜索窗口：前后 200 字符或 chunk_size 的 1/4

        for sep in separators:
            # 在理想位置附近寻找分隔符
            search_start = max(start + chunk_size // 2, ideal_end - search_window)
            search_end = min(len(text), ideal_end + search_window)

            # 在搜索窗口内找最后一个分隔符
            pos = text.rfind(sep, search_start, search_end)
            if pos != -1:
                best_end = pos + len(sep)
                break

        # 提取 chunk
        chunk = text[start:best_end]
        if chunk.strip():
            chunks.append(chunk)

        # 计算下一个 chunk 的起始位置（考虑 overlap）
        start = best_end - overlap
        if start <= 0:
            start = best_end

    return chunks


def _chunk_by_structure(text: str, content_type: str) -> list[str]:
    """根据内容类型使用不同的切分策略。

    Args:
        text: 待切分文本
        content_type: 内容类型

    Returns:
        chunk 列表
    """
    # 估值数据：按指标维度切分
    if content_type == "valuation":
        # 估值数据通常格式为 "指标: 值 | 指标: 值"
        parts = text.split(" | ")
        if len(parts) > 1:
            # 每 3-5 个指标组成一个 chunk
            chunks = []
            for i in range(0, len(parts), 4):
                chunk = " | ".join(parts[i:i+4])
                if chunk.strip():
                    chunks.append(chunk)
            return chunks

    # 分析记录：按段落切分
    if content_type == "analysis":
        paragraphs = text.split("\n\n")
        if len(paragraphs) > 1:
            chunks = []
            current_chunk = ""
            for para in paragraphs:
                if len(current_chunk) + len(para) > 800:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = para
                else:
                    current_chunk += "\n\n" + para if current_chunk else para
            if current_chunk:
                chunks.append(current_chunk)
            return chunks

    # 默认：使用语义感知切分
    return _chunk_text(text)


def index_to_chroma(content_type: str, reference_id: str, title: str, body: str):
    """将文档 chunk 后存入 ChromaDB。"""
    collection = _get_chroma()
    model = _get_embed_model()
    if not collection or not model:
        return 0

    # 删除该文档旧 chunks
    try:
        existing = collection.get(where={
            "$and": [
                {"content_type": content_type},
                {"reference_id": reference_id},
            ]
        })
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass

    if not body:
        return 0

    chunks = _chunk_text(body)
    if not chunks:
        return 0

    # 批量 embed
    embeddings = model.encode(chunks, normalize_embeddings=True).tolist()

    ids = [f"{content_type}:{reference_id}:chunk{i}" for i in range(len(chunks))]
    metadatas = [
        {"content_type": content_type, "reference_id": reference_id, "title": title or "", "chunk_index": i}
        for i in range(len(chunks))
    ]

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=chunks,
        metadatas=metadatas,
    )
    return len(chunks)


def search_chroma(query: str, content_type: str = None, content_types: list[str] = None, limit: int = 5) -> tuple[list[dict], int]:
    """语义搜索，返回 (结果列表, 0)。支持单类型或多类型过滤。"""
    collection = _get_chroma()
    model = _get_embed_model()
    if not collection or not model:
        logger.warning(f"search_chroma 跳过: collection={collection is not None}, model={model is not None}")
        return [], 0

    query_embedding = model.encode([query], normalize_embeddings=True).tolist()

    # 构建过滤条件：支持多类型过滤
    where = None
    if content_types:
        # 多类型过滤：使用 $in 操作符
        where = {"content_type": {"$in": content_types}}
    elif content_type:
        # 单类型过滤
        where = {"content_type": content_type}

    try:
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=limit,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.error(f"search_chroma 查询异常: {e}")
        return [], 0

    if not results or not results["ids"] or not results["ids"][0]:
        return [], 0

    output = []
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        doc = results["documents"][0][i]
        distance = results["distances"][0][i]
        # cosine distance -> similarity score (越小越相似，转为负数做 rank)
        output.append({
            "content_type": meta.get("content_type", ""),
            "title": meta.get("title", ""),
            "body": doc,
            "reference_id": meta.get("reference_id", ""),
            "rank": -distance,  # 负数，和 FTS5 rank 一致（越小越相关）
        })
    return output, 0


def _filter_old_results(results: list[dict], conn=None) -> tuple[list[dict], int]:
    """按 _FRESHNESS_POLICY 策略过滤过时数据。返回 (过滤后结果, 被过滤条数)。"""
    if not results:
        return results, 0

    from datetime import datetime, timedelta

    # 按类型分组需要查日期的 reference_id
    ids_by_type = {}
    for r in results:
        ct = r.get("content_type", "")
        max_months = _FRESHNESS_POLICY.get(ct, 0)
        if max_months > 0 and ct in ("author_article", "skill"):
            ids_by_type.setdefault(ct, set()).add(r["reference_id"])

    if not ids_by_type:
        return results, 0

    # 按类型分别查日期（author_article 和 skill 来自不同表）
    date_map = {}
    try:
        own_conn = conn is None
        if own_conn:
            conn = _get_conn()

        def _normalize_date(date_str: str) -> str:
            """规范化日期为 YYYY-MM 格式（带前导零），确保字符串比较正确。"""
            if not date_str:
                return ""
            # "2025年7月12日" → "2025-07"
            # "2025-06-09" → "2025-06"
            # "2025-7" → "2025-07"
            raw = date_str.replace("年", "-").replace("月", "-").replace("日", "")
            parts = raw.split("-")
            if len(parts) >= 2:
                try:
                    return f"{int(parts[0]):04d}-{int(parts[1]):02d}"
                except (ValueError, IndexError):
                    pass
            return raw[:7]

        # author_article 查 author_articles 表
        if "author_article" in ids_by_type:
            aa_ids = list(ids_by_type["author_article"])
            placeholders = ",".join("?" * len(aa_ids))
            date_rows = conn.execute(
                f"SELECT id, publish_time FROM author_articles WHERE id IN ({placeholders})",
                aa_ids
            ).fetchall()
            for dr in date_rows:
                date_map[f"author_article:{dr['id']}"] = _normalize_date(dr["publish_time"] or "")

        # skill 类型：reference_id 可能是 skill_documents.id 或 article_id
        # 先查 skill_documents.created_at，再查 author_articles.publish_time
        if "skill" in ids_by_type:
            skill_ids = list(ids_by_type["skill"])
            placeholders = ",".join("?" * len(skill_ids))
            # 先查 skill_documents
            date_rows = conn.execute(
                f"SELECT id, created_at FROM skill_documents WHERE id IN ({placeholders})",
                skill_ids
            ).fetchall()
            for dr in date_rows:
                date_map[f"skill:{dr['id']}"] = _normalize_date(dr["created_at"] or "")
            # 未命中的 id 视为 article_id，查 author_articles.publish_time
            missing_ids = [i for i in skill_ids if f"skill:{i}" not in date_map]
            if missing_ids:
                ph2 = ",".join("?" * len(missing_ids))
                date_rows2 = conn.execute(
                    f"SELECT id, publish_time FROM author_articles WHERE id IN ({ph2})",
                    missing_ids
                ).fetchall()
                for dr in date_rows2:
                    date_map[f"skill:{dr['id']}"] = _normalize_date(dr["publish_time"] or "")

        if own_conn:
            conn.close()
    except Exception:
        pass

    # 过滤
    now = datetime.now()
    filtered = []
    dropped = 0
    for r in results:
        ct = r.get("content_type", "")
        max_months = _FRESHNESS_POLICY.get(ct, 0)
        if max_months > 0 and ct in ("author_article", "skill"):
            date_str = date_map.get(f"{ct}:{r['reference_id']}", "")
            if date_str:
                cutoff = (now - timedelta(days=max_months * 30)).strftime("%Y-%m")
                if date_str < cutoff:
                    dropped += 1
                    continue
        filtered.append(r)
    return filtered, dropped


def _enrich_results_with_time(results: list[dict]) -> list[dict]:
    """为检索结果补充时间信息和来源元数据，从原表查询。"""
    if not results:
        return results

    # 按类型分组 reference_id
    ids_by_type = {}
    for r in results:
        ct = r.get("content_type", "")
        ids_by_type.setdefault(ct, set()).add(r["reference_id"])

    time_map = {}
    author_map = {}
    url_map = {}
    try:
        conn = _get_conn()

        # articles: created_at
        if "article" in ids_by_type:
            ids = list(ids_by_type["article"])
            ph = ",".join("?" * len(ids))
            for row in conn.execute(
                f"SELECT id, created_at, url FROM articles WHERE id IN ({ph})", ids
            ).fetchall():
                time_map[f"article:{row['id']}"] = row["created_at"] or ""
                url_map[f"article:{row['id']}"] = row["url"] or ""

        # author_article: publish_time, author
        if "author_article" in ids_by_type:
            ids = list(ids_by_type["author_article"])
            ph = ",".join("?" * len(ids))
            for row in conn.execute(
                f"SELECT id, publish_time, author, url FROM author_articles WHERE id IN ({ph})", ids
            ).fetchall():
                time_map[f"author_article:{row['id']}"] = row["publish_time"] or ""
                author_map[f"author_article:{row['id']}"] = row["author"] or ""
                url_map[f"author_article:{row['id']}"] = row["url"] or ""

        # skill: 来自 skill_documents(created_at) 或 author_skills(article→publish_time)
        if "skill" in ids_by_type:
            ids = list(ids_by_type["skill"])
            ph = ",".join("?" * len(ids))
            # 先查 skill_documents
            for row in conn.execute(
                f"SELECT id, created_at FROM skill_documents WHERE id IN ({ph})", ids
            ).fetchall():
                time_map[f"skill:{row['id']}"] = row["created_at"] or ""
            # 再查 author_articles（skill extraction 的 reference_id 是 article_id）
            missing_ids = [i for i in ids if f"skill:{i}" not in time_map]
            if missing_ids:
                ph2 = ",".join("?" * len(missing_ids))
                for row in conn.execute(
                    f"SELECT id, publish_time, author FROM author_articles WHERE id IN ({ph2})", missing_ids
                ).fetchall():
                    time_map[f"skill:{row['id']}"] = row["publish_time"] or ""
                    author_map[f"skill:{row['id']}"] = row["author"] or ""

        # analysis: analysis_records.created_at
        if "analysis" in ids_by_type:
            ids = list(ids_by_type["analysis"])
            ph = ",".join("?" * len(ids))
            for row in conn.execute(
                f"SELECT id, created_at FROM analysis_records WHERE id IN ({ph})", ids
            ).fetchall():
                time_map[f"analysis:{row['id']}"] = row["created_at"] or ""

        # valuation: snapshot_date
        if "valuation" in ids_by_type:
            ids = list(ids_by_type["valuation"])
            ph = ",".join("?" * len(ids))
            for row in conn.execute(
                f"SELECT index_code, snapshot_date, source_url FROM index_valuations WHERE index_code IN ({ph})", ids
            ).fetchall():
                time_map[f"valuation:{row['index_code']}"] = row["snapshot_date"] or ""
                url_map[f"valuation:{row['index_code']}"] = row["source_url"] or ""

        conn.close()
    except Exception as e:
        logger.warning(f"补充时间信息失败: {e}")

    for r in results:
        key = f"{r['content_type']}:{r['reference_id']}"
        r["time"] = time_map.get(key, "")
        r["author"] = author_map.get(key, "")
        r["source_url"] = url_map.get(key, "")

    return results


def build_rag_context_with_details(query: str, content_types: list[str] = None, limit: int = 5) -> dict:
    """检索知识库（FTS5 + 向量混合），返回上下文文本和详细检索结果。

    返回:
        {
            "context": "格式化的上下文文本",
            "results": [{"content_type", "title", "body", "reference_id", "rank", "label", "source", "time"}],
            "keywords": ["检索关键词"],
            "query": "原始查询",
            "fts_count": int,           # FTS5 原始命中数
            "chroma_count": int,         # ChromaDB 原始命中数
            "freshness_filtered": int,   # 被时效性策略过滤的条数
        }
    """
    # 提取检索关键词
    tokens = _tokenize(query).split()
    # 先过滤停用词，再清洗特殊字符
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    cleaned = [_sanitize_fts_token(t) for t in tokens]
    cleaned = [t for t in cleaned if t]
    multi_char = [t for t in cleaned if len(t) >= 2]
    single_char = [t for t in cleaned if len(t) == 1 and '一' <= t <= '鿿']
    keywords = multi_char + single_char if multi_char else (single_char or cleaned)

    # 查询扩展（同义词 + 知识图谱）
    expanded_query = expand_query(query)
    if expanded_query != query:
        logger.info(f"查询扩展: '{query}' -> '{expanded_query[:100]}...'")

    # FTS5 搜索（使用扩展查询）
    total_freshness_filtered = 0
    if content_types:
        fts_results = []
        for ct in content_types:
            partial, dropped = search_knowledge(expanded_query, content_type=ct, limit=limit)
            fts_results.extend(partial)
            total_freshness_filtered += dropped
    else:
        fts_results, dropped = search_knowledge(expanded_query, limit=limit)
        total_freshness_filtered += dropped

    fts_count = len(fts_results)

    # 向量搜索（使用原始查询，保持语义准确性）
    chroma_results, _ = search_chroma(
        query,
        content_type=content_types[0] if content_types and len(content_types) == 1 else None,
        content_types=content_types if content_types and len(content_types) > 1 else None,
        limit=limit
    )
    chroma_count = len(chroma_results)

    # 过滤 ChromaDB 中的旧数据（FTS 已在 search_knowledge 中过滤）
    chroma_results, chroma_dropped = _filter_old_results(chroma_results)
    total_freshness_filtered += chroma_dropped

    logger.info(f"RAG 搜索: query='{query}', FTS5={fts_count}条, 向量={chroma_count}条, 过滤={total_freshness_filtered}条")

    # RRF (Reciprocal Rank Fusion) 合并排序 — 比独立归一化更可靠
    def _rrf_score(results: list[dict], k: int = 60) -> dict:
        """返回 {key: rrf_score} 的映射。rank 越小越好。"""
        sorted_r = sorted(results, key=lambda x: x["rank"])
        return {f"{r['content_type']}:{r['reference_id']}": 1.0 / (k + i + 1) for i, r in enumerate(sorted_r)}

    fts_rrf = _rrf_score(fts_results)
    chroma_rrf = _rrf_score(chroma_results)

    # 记录每个 key 的来源
    fts_keys = set(fts_rrf.keys())
    chroma_keys = set(chroma_rrf.keys())

    # 合并去重（按 content_type + reference_id 去重，用 RRF 分数合并）
    seen = {}
    for r in fts_results + chroma_results:
        key = f"{r['content_type']}:{r['reference_id']}"
        rft_score = fts_rrf.get(key, 0)
        chroma_score = chroma_rrf.get(key, 0)
        r["_score"] = rft_score + chroma_score  # RRF: 两个排名分数直接相加
        if key not in seen or r["_score"] > seen[key]["_score"]:
            seen[key] = r

    all_results = sorted(seen.values(), key=lambda x: x["_score"], reverse=True)[:limit]

    # 标题匹配加权：按关键词覆盖率比例加权（更精准）
    title_boost_words = [t for t in _tokenize(query).split() if len(t) >= 2]
    if title_boost_words:
        for r in all_results:
            title_text = r.get("title", "")
            matched_count = sum(1 for w in title_boost_words if w in title_text)
            if matched_count > 0:
                # 按覆盖率加权：匹配越多，权重越高
                boost = 0.1 * (matched_count / len(title_boost_words))
                r["_score"] += boost

    # 估值数据最新优先：snapshot_date 越新，权重越高
    for r in all_results:
        if r.get("content_type") == "valuation" and r.get("time"):
            try:
                from datetime import datetime
                date_str = r["time"][:10]  # 取日期部分
                days_old = (datetime.now() - datetime.strptime(date_str, "%Y-%m-%d")).days
                # 7天内的数据加权，超过7天逐渐衰减
                if days_old <= 7:
                    r["_score"] += 0.05
                elif days_old <= 30:
                    r["_score"] += 0.02
            except Exception:
                pass

    all_results.sort(key=lambda x: x["_score"], reverse=True)
    logger.info(f"RAG 合并后: {len(all_results)}条, 类型: {[r['content_type'] for r in all_results]}")

    # 轻量级重排序（基于 token 重叠率，快速提升精度）
    if len(all_results) > 3:
        all_results = lightweight_rerank(query, all_results, top_k=limit)
        logger.info(f"轻量级重排序后: {len(all_results)}条")

    # Reranker 重排序（可选，进一步提升精度，但增加 3-15s 延迟）
    # 默认关闭：RRF + 标题加权 + 时效性加权已能提供合理排序
    # 设置环境变量 RERANK_ENABLED=true 可开启
    if RERANK_ENABLED and len(all_results) > 3:
        all_results = rerank_results(query, all_results, top_k=limit)
        logger.info(f"Rerank 后: {len(all_results)}条")

    # 标记来源（fts / chroma / both）
    for r in all_results:
        key = f"{r['content_type']}:{r['reference_id']}"
        in_fts = key in fts_keys
        in_chroma = key in chroma_keys
        if in_fts and in_chroma:
            r["source"] = "both"
        elif in_fts:
            r["source"] = "fts"
        else:
            r["source"] = "chroma"

    # 添加标签
    label_map = {
        "article": "文章",
        "valuation": "估值",
        "analysis": "分析记录",
        "author_article": "作者文章",
        "skill": "技能知识",
        "linked_doc": "个人文档",
        "knowledge": "投资知识",
    }
    for r in all_results:
        r["label"] = label_map.get(r["content_type"], r["content_type"])

    # 补充时间信息
    _enrich_results_with_time(all_results)

    if not all_results:
        return {
            "context": "", "results": [], "keywords": keywords, "query": query,
            "fts_count": fts_count, "chroma_count": chroma_count,
            "freshness_filtered": total_freshness_filtered,
        }

    # 构建上下文：以完整结果为单位，避免截断丢失来源标注
    parts = []
    total_chars = 0
    max_context_chars = 3000  # 最大上下文字符数

    for r in all_results:
        # 构建单条结果（包含来源标注）
        part = f"[{r['label']}] {r['title']}"
        if r.get("time"):
            part += f" ({r['time']})"
        if r.get("source"):
            part += f" [来源: {r['source']}]"

        # 添加 body（限制长度但保持完整性）
        if r["body"]:
            body_preview = r["body"][:600]
            part += f"\n{body_preview}"

        # 以完整结果为单位截断
        if total_chars + len(part) > max_context_chars and parts:
            break  # 超出预算，停止添加更多结果

        parts.append(part)
        total_chars += len(part)

    # Lost-in-the-Middle 缓解：正确的实现
    # 原理：LLM 对开头和结尾的内容关注度更高，中间内容容易被忽略
    # 策略：最相关的放首位，次相关的放末位，中间按交替顺序排列
    if len(parts) > 2:
        # 使用 parts 的长度来限制索引范围（parts 可能被截断）
        sorted_indices = sorted(range(len(parts)),
                               key=lambda i: all_results[i]["_score"] if i < len(all_results) else 0, reverse=True)

        # 分离首尾
        best_idx = sorted_indices[0]
        worst_idx = sorted_indices[-1]
        middle_indices = sorted_indices[1:-1]

        # 交替排列中间部分：奇数位放前面，偶数位放后面
        front_middle = [middle_indices[i] for i in range(0, len(middle_indices), 2)]
        back_middle = [middle_indices[i] for i in range(1, len(middle_indices), 2)]

        reordered = []
        reordered.append(parts[best_idx])  # 最相关的放最前面
        for idx in front_middle:
            reordered.append(parts[idx])
        for idx in reversed(back_middle):
            reordered.append(parts[idx])
        reordered.append(parts[worst_idx])  # 次相关的放最后面
        parts = reordered

    return {
        "context": "\n\n---\n\n".join(parts),
        "results": all_results,
        "keywords": keywords,
        "query": query,
        "fts_count": fts_count,
        "chroma_count": chroma_count,
        "freshness_filtered": total_freshness_filtered,
    }


# ── 批量索引函数 ──────────────────────────────────────────

def index_article_to_rag(article_id: int):
    """将文章索引到 FTS + ChromaDB。"""
    conn = _get_conn()
    article = conn.execute(
        "SELECT id, title, content_text FROM articles WHERE id = ?",
        (article_id,)
    ).fetchone()
    conn.close()

    if not article:
        return False

    title = article["title"] or ""
    content = article["content_text"] or ""

    if not content:
        return False

    # FTS 索引
    index_article(article["id"], title, content)

    # ChromaDB 索引
    index_to_chroma("article", str(article["id"]), title, content[:8000])

    logger.info(f"已索引文章: {title[:50]}...")
    return True


def index_analysis_to_rag(record_id: int):
    """将分析记录索引到 FTS + ChromaDB。"""
    conn = _get_conn()
    record = conn.execute(
        "SELECT id, index_name, raw_response FROM analysis_records WHERE id = ?",
        (record_id,)
    ).fetchone()
    conn.close()

    if not record:
        return False

    index_name = record["index_name"] or ""
    raw_response = record["raw_response"] or ""

    if not raw_response:
        return False

    # FTS 索引
    index_analysis_record(record["id"], index_name, raw_response)

    # ChromaDB 索引
    index_to_chroma("analysis", str(record["id"]), index_name, raw_response[:8000])

    logger.info(f"已索引分析记录: {index_name} (id={record_id})")
    return True


def reindex_all_articles(limit: int = 1000):
    """批量重建所有文章索引。"""
    from db import list_articles

    articles = list_articles(limit=limit)
    indexed = 0
    for article in articles:
        if index_article_to_rag(article["id"]):
            indexed += 1

    logger.info(f"文章索引完成: {indexed}/{len(articles)}")
    return {"total": len(articles), "indexed": indexed}


def reindex_all_analysis_records(limit: int = 1000):
    """批量重建所有分析记录索引。"""
    from db import list_all_analysis_records

    records = list_all_analysis_records(limit=limit)
    indexed = 0
    for record in records:
        if index_analysis_to_rag(record["id"]):
            indexed += 1

    logger.info(f"分析记录索引完成: {indexed}/{len(records)}")
    return {"total": len(records), "indexed": indexed}


def reindex_all(limit: int = 1000):
    """批量重建所有索引。"""
    results = {}

    # 1. 重建文章索引
    logger.info("开始重建文章索引...")
    results["articles"] = reindex_all_articles(limit)

    # 2. 重建分析记录索引
    logger.info("开始重建分析记录索引...")
    results["analysis"] = reindex_all_analysis_records(limit)

    # 3. 重建作者文章索引
    logger.info("开始重建作者文章索引...")
    from db import list_author_articles
    author_articles = list_author_articles(limit=limit)
    aa_indexed = 0
    for article in author_articles:
        if article.get("content_text"):
            index_author_article(
                article["id"],
                article.get("title", ""),
                article["content_text"],
                article.get("publish_time", "")
            )
            aa_indexed += 1
    results["author_articles"] = {"total": len(author_articles), "indexed": aa_indexed}

    # 4. 重建估值索引
    logger.info("开始重建估值索引...")
    from db import list_valuation_indexes
    valuations = list_valuation_indexes()
    val_indexed = 0
    for val in valuations:
        index_valuation(val["index_code"], val["index_name"], val)
        val_indexed += 1
    results["valuations"] = {"total": len(valuations), "indexed": val_indexed}

    logger.info(f"全部索引完成: {results}")
    return results


def get_rag_stats_summary():
    """获取 RAG 索引统计信息。"""
    conn = _get_conn()

    # FTS 统计
    fts_counts = {}
    for content_type in ["article", "analysis", "author_article", "skill", "valuation"]:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM knowledge_fts WHERE content_type = ?",
            (content_type,)
        ).fetchone()
        fts_counts[content_type] = row["cnt"] if row else 0

    conn.close()

    # ChromaDB 统计
    chroma_counts = {}
    try:
        collection = _get_chroma()
        if collection:
            for content_type in ["article", "analysis", "author_article", "skill", "valuation"]:
                try:
                    result = collection.get(where={"content_type": content_type})
                    chroma_counts[content_type] = len(result["ids"]) if result and result["ids"] else 0
                except Exception:
                    chroma_counts[content_type] = 0
    except Exception:
        pass

    return {
        "fts": fts_counts,
        "chroma": chroma_counts,
        "total_fts": sum(fts_counts.values()),
        "total_chroma": sum(chroma_counts.values()),
    }
