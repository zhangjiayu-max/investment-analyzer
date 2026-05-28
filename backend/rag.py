"""RAG 检索服务 — SQLite FTS5 全文检索 + ChromaDB 向量语义搜索"""

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "valuations.db"
CHROMA_DIR = Path(__file__).parent.parent / "data" / "chroma_db"

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
    """初始化 FTS5 虚拟表，应用启动时调用。"""
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
_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "都", "上", "也",
    "到", "说", "要", "去", "你", "会", "着", "看", "好", "这", "他", "她",
    "能", "下", "过", "么", "吗", "呢", "把", "让", "被", "从", "向", "对",
    "以", "可以", "应该", "需要", "现在", "目前", "当前", "最近", "怎么样",
    "如何", "什么", "多少", "为什么", "怎样", "哪个", "哪些", "是否",
    "买", "卖", "持有", "投资", "分析", "看看", "帮忙", "帮我", "请问", "想问",
    "估值", "数据", "情况", "趋势", "走势", "那", "个", "一", "人", "很", "没有",
    "点", "想", "入手", "帮", "问",
}


def _build_fts_query(query: str) -> str:
    """将用户问题转为 FTS5 查询：分词后过滤停用词，核心词用 AND、辅助词用 OR。"""
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
        core = " AND ".join(multi_char)
        if single_char:
            # 核心词 AND，辅助单字 OR
            return f"({core}) OR ({' OR '.join(single_char)})"
        return core
    return " OR ".join(single_char)


# ── 时效性策略：不同类型内容的有效期不同 ──────────────────
# author_article: 市场评论/行情分析，时效性强，2个月
# skill: 投资方法论/认知框架，长期有效，12个月
# valuation: 实时估值数据，不过滤
# article/analysis: 时效性强，2个月
_FRESHNESS_POLICY = {
    "author_article": 2,
    "skill": 6,
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


def search_knowledge(query: str, content_type: str = None, limit: int = 5) -> list[dict]:
    """FTS5 全文检索，按内容类型自动过滤过时数据。优先 AND 匹配，无结果时降级为 OR。"""
    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

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
                return []

    results = [dict(r) for r in rows]
    results = _filter_old_results(results, conn=conn)
    conn.close()
    return results[:limit]


def build_rag_context(query: str, content_types: list[str] = None, limit: int = 5) -> str:
    """检索知识库并格式化为 LLM 上下文文本。"""
    result = build_rag_context_with_details(query, content_types, limit)
    return result["context"]


def log_rag_search(conversation_id: int, message_id: int, query: str, keywords: list,
                   results: list, content_types: list = None):
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
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        INSERT INTO rag_logs (conversation_id, message_id, query, keywords, content_types, results_count, results)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        conversation_id,
        message_id,
        query,
        json.dumps(keywords, ensure_ascii=False),
        json.dumps(content_types, ensure_ascii=False),
        len(results),
        json.dumps(results, ensure_ascii=False),
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


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """将长文本切分为 chunk_size 字符的块，overlap 字符重叠。"""
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


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


def search_chroma(query: str, content_type: str = None, limit: int = 5) -> list[dict]:
    """语义搜索，返回与 search_knowledge 相同格式的结果。"""
    collection = _get_chroma()
    model = _get_embed_model()
    if not collection or not model:
        logger.warning(f"search_chroma 跳过: collection={collection is not None}, model={model is not None}")
        return []

    query_embedding = model.encode([query], normalize_embeddings=True).tolist()

    where = None
    if content_type:
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
        return []

    if not results or not results["ids"] or not results["ids"][0]:
        return []

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
    return output


def _filter_old_results(results: list[dict], conn=None) -> list[dict]:
    """按 _FRESHNESS_POLICY 策略过滤过时数据。不同类型内容有效期不同。"""
    if not results:
        return results

    from datetime import datetime, timedelta

    # 按类型分组需要查日期的 reference_id
    ids_by_type = {}
    for r in results:
        ct = r.get("content_type", "")
        max_months = _FRESHNESS_POLICY.get(ct, 0)
        if max_months > 0 and ct in ("author_article", "skill"):
            ids_by_type.setdefault(ct, set()).add(r["reference_id"])

    if not ids_by_type:
        return results

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
    for r in results:
        ct = r.get("content_type", "")
        max_months = _FRESHNESS_POLICY.get(ct, 0)
        if max_months > 0 and ct in ("author_article", "skill"):
            date_str = date_map.get(f"{ct}:{r['reference_id']}", "")
            if date_str:
                cutoff = (now - timedelta(days=max_months * 30)).strftime("%Y-%m")
                if date_str < cutoff:
                    continue
        filtered.append(r)
    return filtered


def _enrich_results_with_time(results: list[dict]) -> list[dict]:
    """为检索结果补充时间信息，从原表查询。"""
    if not results:
        return results

    # 按类型分组 reference_id
    ids_by_type = {}
    for r in results:
        ct = r.get("content_type", "")
        ids_by_type.setdefault(ct, set()).add(r["reference_id"])

    time_map = {}
    try:
        conn = _get_conn()

        # articles: created_at
        if "article" in ids_by_type:
            ids = list(ids_by_type["article"])
            ph = ",".join("?" * len(ids))
            for row in conn.execute(
                f"SELECT id, created_at FROM articles WHERE id IN ({ph})", ids
            ).fetchall():
                time_map[f"article:{row['id']}"] = row["created_at"] or ""

        # author_article: publish_time
        if "author_article" in ids_by_type:
            ids = list(ids_by_type["author_article"])
            ph = ",".join("?" * len(ids))
            for row in conn.execute(
                f"SELECT id, publish_time FROM author_articles WHERE id IN ({ph})", ids
            ).fetchall():
                time_map[f"author_article:{row['id']}"] = row["publish_time"] or ""

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
                    f"SELECT id, publish_time FROM author_articles WHERE id IN ({ph2})", missing_ids
                ).fetchall():
                    time_map[f"skill:{row['id']}"] = row["publish_time"] or ""

        # analysis: analysis_records.created_at
        if "analysis" in ids_by_type:
            ids = list(ids_by_type["analysis"])
            ph = ",".join("?" * len(ids))
            for row in conn.execute(
                f"SELECT id, created_at FROM analysis_records WHERE id IN ({ph})", ids
            ).fetchall():
                time_map[f"analysis:{row['id']}"] = row["created_at"] or ""

        conn.close()
    except Exception as e:
        logger.warning(f"补充时间信息失败: {e}")

    for r in results:
        key = f"{r['content_type']}:{r['reference_id']}"
        r["time"] = time_map.get(key, "")

    return results


def build_rag_context_with_details(query: str, content_types: list[str] = None, limit: int = 5) -> dict:
    """检索知识库（FTS5 + 向量混合），返回上下文文本和详细检索结果。

    返回:
        {
            "context": "格式化的上下文文本",
            "results": [{"content_type", "title", "body", "reference_id", "rank", "label"}],
            "keywords": ["检索关键词"],
            "query": "原始查询"
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

    # FTS5 搜索
    if content_types:
        fts_results = []
        for ct in content_types:
            fts_results.extend(search_knowledge(query, content_type=ct, limit=limit))
    else:
        fts_results = search_knowledge(query, limit=limit)

    # 向量搜索
    chroma_results = search_chroma(query, content_type=content_types[0] if content_types and len(content_types) == 1 else None, limit=limit)

    # 过滤 ChromaDB 中的旧数据（FTS 已在 search_knowledge 中过滤）
    chroma_results = _filter_old_results(chroma_results)

    logger.info(f"RAG 搜索: query='{query}', FTS5={len(fts_results)}条, 向量={len(chroma_results)}条")

    # RRF (Reciprocal Rank Fusion) 合并排序 — 比独立归一化更可靠
    def _rrf_score(results: list[dict], k: int = 60) -> dict:
        """返回 {key: rrf_score} 的映射。rank 越小越好。"""
        sorted_r = sorted(results, key=lambda x: x["rank"])
        return {f"{r['content_type']}:{r['reference_id']}": 1.0 / (k + i + 1) for i, r in enumerate(sorted_r)}

    fts_rrf = _rrf_score(fts_results)
    chroma_rrf = _rrf_score(chroma_results)

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

    # 标题匹配加权：查询关键词出现在标题中的结果加权（提高精确度）
    title_boost_words = [t for t in _tokenize(query).split() if len(t) >= 2]
    for r in all_results:
        title_text = r.get("title", "")
        if any(w in title_text for w in title_boost_words):
            r["_score"] += 0.1  # 加权

    all_results.sort(key=lambda x: x["_score"], reverse=True)
    logger.info(f"RAG 合并后: {len(all_results)}条, 类型: {[r['content_type'] for r in all_results]}")

    # 添加标签
    label_map = {"article": "文章", "valuation": "估值", "analysis": "分析记录",
                 "author_article": "作者文章", "skill": "技能知识", "linked_doc": "个人文档"}
    for r in all_results:
        r["label"] = label_map.get(r["content_type"], r["content_type"])

    # 补充时间信息
    _enrich_results_with_time(all_results)

    if not all_results:
        return {"context": "", "results": [], "keywords": keywords, "query": query}

    parts = []
    for r in all_results:
        part = f"[{r['label']}] {r['title']}"
        if r["body"]:
            body_preview = r["body"][:500]
            part += f"\n{body_preview}"
        parts.append(part)

    # Lost-in-the-Middle 缓解：最相关的放首尾，次要的放中间
    if len(parts) > 2:
        reordered = [parts[0]]  # 最相关放最前面
        middle = parts[1:-1]
        reordered.append(parts[-1])  # 次相关放最后面
        # 中间按原序（已经按分数排过）
        reordered = [parts[0]] + middle + [parts[-1]]
        parts = reordered

    return {
        "context": "\n\n---\n\n".join(parts),
        "results": all_results,
        "keywords": keywords,
        "query": query,
    }
