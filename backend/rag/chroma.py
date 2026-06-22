"""ChromaDB 向量语义搜索"""
import json
import logging
import os
import re
import time

from .config import get_rag_config, get_rag_config_int

logger = logging.getLogger(__name__)

_chroma_client = None
_chroma_collection = None
_embed_model = None

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


def reset_chroma_collection():
    """删除并重建 ChromaDB collection（切换 embedding 模型后必须调用）。

    不同 embedding 模型的向量维度不同（如 bge-small=512, m3e-base=768），
    旧向量无法与新模型混用，必须清空后重新生成。
    """
    global _chroma_collection
    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # 删除旧 collection
    try:
        client.delete_collection("knowledge")
        logger.info("已删除旧 ChromaDB collection")
    except Exception:
        pass

    # 用新维度重建
    _chroma_collection = client.create_collection(
        name="knowledge",
        metadata={"hnsw:space": "cosine"},
    )
    logger.info("已重建 ChromaDB collection")
    return _chroma_collection


def _ensure_embed_model():
    """延迟加载 embedding 模型。模型名从 config.EMBED_MODEL_NAME 读取。"""
    global _embed_model
    if _embed_model is not None:
        return _embed_model

    import os
    import ssl
    import warnings
    warnings.filterwarnings("ignore", message=".*certificate.*")

    # 修复 macOS SSL 证书问题 + 国内镜像加速
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_SSL_VERIFICATION"] = "1"
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""

    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except Exception:
        pass

    from config import EMBED_MODEL_NAME
    from sentence_transformers import SentenceTransformer

    logger.info(f"加载 Embedding 模型: {EMBED_MODEL_NAME}")
    try:
        _embed_model = SentenceTransformer(EMBED_MODEL_NAME, local_files_only=True)
        dim = _embed_model.get_embedding_dimension()
        logger.info(f"Embedding 模型从本地加载成功 (维度: {dim})")
    except Exception as e:
        logger.warning(f"本地加载失败，尝试在线下载: {e}")
        import httpx
        old_client = httpx.Client
        class NoVerifyClient(old_client):
            def __init__(self, *args, **kwargs):
                kwargs["verify"] = False
                super().__init__(*args, **kwargs)
        httpx.Client = NoVerifyClient
        try:
            _embed_model = SentenceTransformer(EMBED_MODEL_NAME)
            dim = _embed_model.get_embedding_dimension()
            logger.info(f"Embedding 模型在线下载成功 (维度: {dim})")
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


def index_to_chroma(content_type: str, reference_id: str, title: str, body: str,
                    extra_metadata: dict = None):
    """将文档 chunk 后存入 ChromaDB。

    Args:
        content_type: 内容类型
        reference_id: 引用 ID
        title: 标题
        body: 正文内容
        extra_metadata: 额外元数据（会合并到每个 chunk 的 metadata 中）
    """
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
    base_meta = {"content_type": content_type, "reference_id": reference_id,
                 "title": title or "", "chunk_index": 0}
    if extra_metadata:
        base_meta.update(extra_metadata)

    metadatas = []
    for i in range(len(chunks)):
        meta = base_meta.copy()
        meta["chunk_index"] = i
        metadatas.append(meta)

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=chunks,
        metadatas=metadatas,
    )
    return len(chunks)


def delete_chroma_by_filter(content_type: str, **filters) -> int:
    """按过滤条件删除 ChromaDB 中的文档，返回删除条数。"""
    collection = _get_chroma()
    if not collection:
        return 0

    try:
        conditions = [{"content_type": content_type}]
        for k, v in filters.items():
            conditions.append({k: v})

        where = {"$and": conditions} if len(conditions) > 1 else conditions[0]
        existing = collection.get(where=where)
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
            return len(existing["ids"])
    except Exception as e:
        logger.warning(f"ChromaDB 删除失败: {e}")
    return 0


def index_book_knowledge(knowledge_id: int, title: str, content: str, source: str):
    """索引一条书籍知识到 FTS + ChromaDB。"""
    # FTS 索引
    _index_document("book", title, content, str(knowledge_id))
    # ChromaDB 索引（带上 source 便于后续清理）
    index_to_chroma("book", str(knowledge_id), title, content,
                    extra_metadata={"source": source or ""})


def index_note_knowledge(knowledge_id: int, title: str, content: str, source: str):
    """索引一条个人笔记到 FTS + ChromaDB（来自 Obsidian vault）。"""
    _index_document("note", title, content, str(knowledge_id))
    index_to_chroma("note", str(knowledge_id), title, content,
                    extra_metadata={"source": source or ""})


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

    # 距离阈值：过滤低相关结果
    max_distance = get_rag_config_float("chroma_max_distance", 0.8)

    output = []
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        doc = results["documents"][0][i]
        distance = results["distances"][0][i]
        # cosine distance 越小越相似，超过阈值的跳过
        if distance > max_distance:
            continue
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
        if max_months > 0:
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

        # article 类型：查 articles 表
        if "article" in ids_by_type:
            art_ids = list(ids_by_type["article"])
            placeholders = ",".join("?" * len(art_ids))
            date_rows = conn.execute(
                f"SELECT id, publish_time FROM articles WHERE id IN ({placeholders})",
                art_ids
            ).fetchall()
            for dr in date_rows:
                date_map[f"article:{dr['id']}"] = _normalize_date(dr["publish_time"] or "")

        # analysis 类型：通过 article_id 关联 articles 表
        if "analysis" in ids_by_type:
            ana_ids = list(ids_by_type["analysis"])
            placeholders = ",".join("?" * len(ana_ids))
            date_rows = conn.execute(
                f"SELECT ar.id, a.publish_time FROM analysis_records ar "
                f"JOIN articles a ON ar.article_id = a.id WHERE ar.id IN ({placeholders})",
                ana_ids
            ).fetchall()
            for dr in date_rows:
                date_map[f"analysis:{dr['id']}"] = _normalize_date(dr["publish_time"] or "")

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
        if max_months > 0:
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


# ── 指数名称检测 + 估值直接注入 ──────────────────────────────

# 已知指数名称缓存（启动时加载，5 分钟刷新）
_known_index_names: list[str] = []
_known_index_names_ts: float = 0


def _get_known_index_names() -> list[str]:
    """从 index_valuations 表获取所有已知指数名称。"""
    global _known_index_names, _known_index_names_ts
    import time

    now = time.time()
    if now - _known_index_names_ts < 300 and _known_index_names:
        return _known_index_names

    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT DISTINCT index_name FROM index_valuations WHERE index_name IS NOT NULL"
        ).fetchall()
        conn.close()
        _known_index_names = [r["index_name"] for r in rows if r["index_name"]]
        _known_index_names_ts = now
    except Exception:
        pass
    return _known_index_names


def _detect_index_names(query: str) -> list[str]:
    """从查询中检测指数名称（如"恒生科技"、"沪深300"等）。"""
    names = _get_known_index_names()
    matched = []
    # 按名称长度降序匹配，优先匹配长名称
    for name in sorted(names, key=len, reverse=True):
        if name in query and name not in matched:
            matched.append(name)
    return matched


def _inject_valuation_data(query: str, detected_indexes: list[str]) -> tuple[str, list[dict]]:
    """当检测到指数名称时，直接从 index_valuations 表注入最新估值数据。

    返回 (注入的上下文文本, 估值结果列表)。
    """
    if not detected_indexes:
        return "", []

    try:
        conn = _get_conn()
        valuation_results = []

        for index_name in detected_indexes:
            # 查该指数的所有指标最新数据
            rows = conn.execute("""
                SELECT index_code, index_name, metric_type, snapshot_date,
                       current_value, percentile, zscore, danger_value,
                       opportunity_value, median
                FROM index_valuations
                WHERE index_name = ?
                ORDER BY snapshot_date DESC
                LIMIT 3
            """, (index_name,)).fetchall()

            for r in rows:
                r = dict(r)
                # 构建标准化的 body
                body_parts = [f"日期: {r.get('snapshot_date', '')}"]
                body_parts.append(f"指数: {r.get('index_name', '')}")
                body_parts.append(f"指标: {r.get('metric_type', '')}")
                body_parts.append(f"当前值: {r.get('current_value', '')}")
                body_parts.append(f"百分位: {r.get('percentile', '')}%")
                body_parts.append(f"Z-Score: {r.get('zscore', '')}")
                body_parts.append(f"危险值: {r.get('danger_value', '')}")
                body_parts.append(f"机会值: {r.get('opportunity_value', '')}")
                body_parts.append(f"中位数: {r.get('median', '')}")
                body = " | ".join(body_parts)

                title = f"{r['index_name']} {r['metric_type']}估值"

                valuation_results.append({
                    "content_type": "valuation",
                    "title": title,
                    "body": body,
                    "reference_id": r.get("index_code", ""),
                    "rank": -100,  # 最高优先级
                    "label": "估值",
                    "source": "direct",
                    "time": r.get("snapshot_date", ""),
                    "_score": 1.0,  # 最高分
                    "_direct_inject": True,
                })

        conn.close()

        # 去重（同一指数+指标只保留最新）
        seen = set()
        unique_results = []
        for r in valuation_results:
            key = f"{r['reference_id']}:{r['body'].split('指标:')[1].split('|')[0].strip() if '指标:' in r['body'] else ''}"
            if key not in seen:
                seen.add(key)
                unique_results.append(r)

        # 构建注入文本
        if unique_results:
            parts = []
            for r in unique_results:
                parts.append(f"[{r['label']}] {r['title']} ({r['time']})\n{r['body']}")
            inject_text = "\n\n---\n\n".join(parts)
            return inject_text, unique_results

        return "", []
    except Exception as e:
        logger.warning(f"注入估值数据失败: {e}")
        return "", []


