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
    """索引一条估值数据。"""
    body_parts = []
    for key in ["metric_type", "current_value", "percentile", "danger_value",
                 "opportunity_value", "median", "zscore"]:
        val = valuation_data.get(key)
        if val is not None:
            body_parts.append(f"{key}: {val}")
    _index_document("valuation", index_name or index_code, " ".join(body_parts), index_code)


def index_analysis_record(record_id: int, index_name: str, raw_response: str):
    """索引一条分析记录。"""
    _index_document("analysis", index_name or "", raw_response[:3000] if raw_response else "", str(record_id))


def index_author_article(article_id: int, title: str, content: str):
    """索引一篇作者文章。"""
    _index_document("author_article", title or "", content[:5000] if content else "", str(article_id))


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


def _build_fts_query(query: str) -> str:
    """将用户问题转为 FTS5 查询：分词后过滤停用词，用 OR 连接。"""
    tokens = _tokenize(query).split()
    # 过滤掉单字和常见停用词
    _stopwords = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "都", "上", "也",
                  "到", "说", "要", "去", "你", "会", "着", "看", "好", "这", "他", "她",
                  "能", "下", "过", "么", "吗", "呢", "把", "让", "被", "从", "向", "对",
                  "以", "可以", "应该", "需要", "现在", "目前", "当前", "最近", "怎么样",
                  "如何", "什么", "多少", "为什么", "怎样", "哪个", "哪些", "是否",
                  "买", "卖", "持有", "投资", "分析", "看看", "帮忙", "帮我", "请问", "想问",
                  "估值", "数据", "情况", "趋势", "走势", "那", "个", "一", "人", "很", "没有"}
    keywords = [t for t in tokens if len(t) >= 2 and t not in _stopwords]
    if not keywords:
        # 没有有效关键词，用原始分词结果
        keywords = [t for t in tokens if len(t) >= 1]
    # FTS5 OR 查询
    return " OR ".join(keywords)


def search_knowledge(query: str, content_type: str = None, limit: int = 5) -> list[dict]:
    """FTS5 全文检索，返回最相关的知识片段。"""
    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    conn = _get_conn()
    try:
        if content_type:
            rows = conn.execute("""
                SELECT content_type, title, body, reference_id, rank
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ? AND content_type = ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, content_type, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT content_type, title, body, reference_id, rank
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit)).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    conn.close()
    return [dict(r) for r in rows]


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
    _stopwords = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "都", "上", "也",
                  "到", "说", "要", "去", "你", "会", "着", "看", "好", "这", "他", "她",
                  "能", "下", "过", "么", "吗", "呢", "把", "让", "被", "从", "向", "对",
                  "以", "可以", "应该", "需要", "现在", "目前", "当前", "最近", "怎么样",
                  "如何", "什么", "多少", "为什么", "怎样", "哪个", "哪些", "是否"}
    keywords = [t for t in tokens if len(t) >= 2 and t not in _stopwords]

    # FTS5 搜索
    if content_types:
        fts_results = []
        for ct in content_types:
            fts_results.extend(search_knowledge(query, content_type=ct, limit=limit))
    else:
        fts_results = search_knowledge(query, limit=limit)

    # 向量搜索
    chroma_results = search_chroma(query, content_type=content_types[0] if content_types and len(content_types) == 1 else None, limit=limit)

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
    logger.info(f"RAG 合并后: {len(all_results)}条, 类型: {[r['content_type'] for r in all_results]}")

    # 添加标签
    label_map = {"article": "文章", "valuation": "估值", "analysis": "分析记录",
                 "author_article": "作者文章", "skill": "技能知识", "linked_doc": "个人文档"}
    for r in all_results:
        r["label"] = label_map.get(r["content_type"], r["content_type"])

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
