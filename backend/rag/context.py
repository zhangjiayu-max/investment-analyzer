"""RAG 上下文构建、批量索引、统计"""
import json
import logging
import re
import time

from .config import get_rag_config, get_rag_config_int
from .fts import search_knowledge, log_rag_search, init_fts
from .chroma import search_chroma, index_to_chroma, init_chroma
from .reranker import rerank_results

logger = logging.getLogger(__name__)

def build_rag_context_with_details(query: str, content_types: list[str] = None, limit: int = 8) -> dict:
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
    # 检测查询中的指数名称，直接注入估值数据
    detected_indexes = _detect_index_names(query)
    direct_valuation_text, direct_valuation_results = _inject_valuation_data(query, detected_indexes)
    if detected_indexes:
        logger.info(f"检测到指数名称: {detected_indexes}，注入估值数据 {len(direct_valuation_results)} 条")

    # 提取检索关键词
    tokens = _tokenize(query).split()
    # 先过滤停用词，再清洗特殊字符
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    cleaned = [_sanitize_fts_token(t) for t in tokens]
    cleaned = [t for t in cleaned if t]
    multi_char = [t for t in cleaned if len(t) >= 2]
    single_char = [t for t in cleaned if len(t) == 1 and '一' <= t <= '鿿']
    keywords = multi_char + single_char if multi_char else (single_char or cleaned)

    # 查询扩展（同义词 + 知识图谱）— 仅用于 ChromaDB 语义搜索
    expanded_query = expand_query(query)
    if expanded_query != query:
        logger.info(f"查询扩展: '{query}' -> '{expanded_query[:100]}...'")

    # FTS5 搜索（使用原始查询，避免扩展的英文特殊字符破坏 FTS 语法）
    total_freshness_filtered = 0
    if content_types:
        fts_results = []
        for ct in content_types:
            partial, dropped = search_knowledge(query, content_type=ct, limit=limit)
            fts_results.extend(partial)
            total_freshness_filtered += dropped
    else:
        fts_results, dropped = search_knowledge(query, limit=limit)
        total_freshness_filtered += dropped

    fts_count = len(fts_results)

    # 补充搜索：当查询较长且 FTS 结果中没有新入库的知识条目时，用核心词再搜一次
    # 解决口语化长查询中 AND 匹配了噪音词但漏掉核心知识的问题
    if fts_results and len(multi_char) > 4:
        has_recent_entry = any(
            r.get('reference_id') and str(r['reference_id']).isdigit() and int(r['reference_id']) >= 12001
            for r in fts_results
        )
        if not has_recent_entry:
            # 取最长的 2 个核心词做 AND 补充搜索
            query_core_terms = [t for t in multi_char if t.strip('"') in _FINANCE_CORE_TERMS]
            if not query_core_terms:
                query_core_terms = sorted(multi_char, key=len, reverse=True)[:2]
            core_supplement_q = " AND ".join(query_core_terms[:2])
            try:
                conn2 = _get_conn()
                supplement_rows = conn2.execute("""
                    SELECT content_type, title, body, reference_id, rank
                    FROM knowledge_fts WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?
                """, (core_supplement_q, limit * 2)).fetchall()
                conn2.close()
                supplement_results = [dict(r) for r in supplement_rows]
                supplement_results, supp_dropped = _filter_old_results(supplement_results)
                total_freshness_filtered += supp_dropped
                existing_keys = {f"{r['content_type']}:{r['reference_id']}" for r in fts_results}
                added = 0
                for r in supplement_results:
                    key = f"{r['content_type']}:{r['reference_id']}"
                    if key not in existing_keys:
                        fts_results.append(r)
                        existing_keys.add(key)
                        added += 1
                if added:
                    logger.info(f"核心词补充搜索: '{core_supplement_q}' 补入 {added} 条")
            except Exception as e:
                logger.warning(f"核心词补充搜索失败: {e}")

    # 向量搜索（使用扩展查询，补充口语化同义词提升语义匹配）
    # 候选数量放大 2 倍，经 RRF 融合 + 分数阈值后再截取 top limit
    chroma_results, _ = search_chroma(
        expanded_query,
        content_type=content_types[0] if content_types and len(content_types) == 1 else None,
        content_types=content_types if content_types and len(content_types) > 1 else None,
        limit=limit * 2
    )
    chroma_count = len(chroma_results)

    # 过滤 ChromaDB 中的旧数据（FTS 已在 search_knowledge 中过滤）
    chroma_results, chroma_dropped = _filter_old_results(chroma_results)
    total_freshness_filtered += chroma_dropped

    logger.info(f"RAG 搜索: query='{query}', FTS5={fts_count}条, 向量={chroma_count}条, 过滤={total_freshness_filtered}条")

    # RRF (Reciprocal Rank Fusion) 合并排序 — 比独立归一化更可靠
    # 优先使用意图分类的 k 值，配置值作为覆盖
    intent_k = get_rrf_params(query)
    config_k = get_rag_config_int("rrf_k", 0)
    rrf_k = config_k if config_k > 0 else intent_k
    logger.info(f"RRF k={rrf_k} (intent={intent_k}, config={config_k})")

    # 每个来源只取 top-N 参与融合，避免长尾低质量结果干扰排序
    rrf_top_n = get_rag_config_int("rrf_top_n", 5)
    fts_candidates = sorted(fts_results, key=lambda x: x["rank"])[:rrf_top_n]
    chroma_candidates = sorted(chroma_results, key=lambda x: x["rank"])[:rrf_top_n]

    def _rrf_score(results: list[dict], k: int = None) -> dict:
        """返回 {key: rrf_score} 的映射。rank 越小越好。"""
        if k is None:
            k = rrf_k
        sorted_r = sorted(results, key=lambda x: x["rank"])
        return {f"{r['content_type']}:{r['reference_id']}": 1.0 / (k + i + 1) for i, r in enumerate(sorted_r)}

    fts_rrf = _rrf_score(fts_candidates)
    chroma_rrf = _rrf_score(chroma_candidates)

    # 记录每个 key 的来源
    fts_keys = set(fts_rrf.keys())
    chroma_keys = set(chroma_rrf.keys())
    cross_source_bonus = get_rag_config_float("rrf_cross_bonus", 0.01)

    # 合并去重（按 content_type + reference_id 去重，用 RRF 分数合并）
    seen = {}
    for r in fts_results + chroma_results:
        key = f"{r['content_type']}:{r['reference_id']}"
        rft_score = fts_rrf.get(key, 0)
        chroma_score = chroma_rrf.get(key, 0)
        # 跨来源加权：同时出现在 FTS5 和 ChromaDB 的结果更可信
        cross_bonus = cross_source_bonus if (key in fts_keys and key in chroma_keys) else 0
        r["_score"] = rft_score + chroma_score + cross_bonus
        r["source"] = ("both" if cross_bonus else ("fts" if key in fts_keys else "chroma"))
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

    # 指数名称匹配加权：当查询包含指数名称时，提升估值和分析结果的权重
    if detected_indexes:
        for r in all_results:
            if r.get("_direct_inject"):
                continue  # 直接注入的已经是最高分，跳过
            if r.get("content_type") == "valuation":
                r["_score"] *= 1.5  # 估值数据加权 50%
            elif r.get("content_type") == "analysis":
                r["_score"] *= 1.2  # 分析记录加权 20%

    # 知识反馈加权：用户标记有用/无用的条目加权
    try:
        from db.knowledge import get_knowledge_usefulness
        for r in all_results:
            ref_id = r.get("reference_id")
            if ref_id:
                score = get_knowledge_usefulness(int(ref_id))
                if score != 0:
                    r["_score"] += score * 0.05  # 每次+/-1 影响 0.05 分
    except Exception:
        pass

    all_results.sort(key=lambda x: x["_score"], reverse=True)

    # 同书多样性：同一本书的结果超过 2 条后，逐条递减分数
    book_diversity_penalty = get_rag_config_float("book_diversity_penalty", 0.85)
    book_count = {}
    for r in all_results:
        if r.get("content_type") == "book":
            source = r.get("source", "")
            if not source:
                # 从 title 中提取书名
                title = r.get("title", "")
                if title.startswith("["):
                    source = title.split("]")[0].strip("[")
            book_count[source] = book_count.get(source, 0) + 1
            if book_count[source] > 2:
                r["_score"] *= book_diversity_penalty

    # 内容长度加权：低质量碎片额外降权，抑制目录级摘要淹没高质量内容
    short_content_penalty = get_rag_config_float("short_content_penalty", 0.9)
    short_content_threshold = get_rag_config_int("short_content_threshold", 200)
    for r in all_results:
        body_len = len(r.get("body", ""))
        if body_len > 0 and body_len < short_content_threshold:
            r["_score"] *= short_content_penalty

    # 书籍内容相关度优化：检查书籍标题/内容与查询的关键词重叠度
    book_relevance_boost = get_rag_config_float("book_relevance_boost", 1.2)
    book_relevance_penalty = get_rag_config_float("book_relevance_penalty", 0.7)
    query_tokens = set(query.replace(" ", ""))
    for r in all_results:
        if r.get("content_type") == "book":
            title = r.get("title", "")
            body = r.get("body", "")[:200]  # 只检查前 200 字符
            book_text = title + body
            # 计算关键词重叠
            overlap = sum(1 for token in query_tokens if token in book_text)
            overlap_ratio = overlap / len(query_tokens) if query_tokens else 0
            if overlap_ratio > 0.3:
                # 高相关：提升权重
                r["_score"] *= book_relevance_boost
            elif overlap_ratio < 0.1:
                # 低相关：降低权重
                r["_score"] *= book_relevance_penalty

    # 双路命中加分：同时被 FTS5 和 ChromaDB 命中的结果更可靠
    # 必须在阈值过滤之前，避免边界结果被误杀
    dual_hit_boost = get_rag_config_float("dual_hit_boost", 1.15)
    for r in all_results:
        key = f"{r['content_type']}:{r['reference_id']}"
        if key in fts_keys and key in chroma_keys:
            r["_score"] *= dual_hit_boost

    # 最低分数阈值：过滤明显不相关的结果
    # 绝对阈值 0.018（RRF k=60 时，排名第 55 的结果分数约为 1/(60+55) ≈ 0.0087）
    # 双路命中加分后的最低有效分数约 0.018
    SCORE_THRESHOLD = get_rag_config_float("score_threshold", 0.018)
    if all_results:
        before_count = len(all_results)
        all_results = [r for r in all_results if r["_score"] >= SCORE_THRESHOLD]
        if len(all_results) < before_count:
            logger.info(f"分数阈值过滤: {before_count} → {len(all_results)}条 (阈值={SCORE_THRESHOLD})")

    # 同指数 analysis 去重：同一指数最多保留 2 条（最新 + 最高分），避免重复记录占满结果
    _analysis_dedup = {}
    for r in all_results:
        if r.get("content_type") == "analysis":
            key = r.get("title", "").strip()
            if key not in _analysis_dedup:
                _analysis_dedup[key] = []
            _analysis_dedup[key].append(r)
    _dedup_limit = 2
    _to_remove = []
    for key, items in _analysis_dedup.items():
        if len(items) > _dedup_limit:
            # 保留分数最高的 2 条
            items.sort(key=lambda x: x.get("_score", 0), reverse=True)
            _to_remove.extend(items[_dedup_limit:])
    if _to_remove:
        remove_ids = {id(r) for r in _to_remove}
        all_results = [r for r in all_results if id(r) not in remove_ids]
        logger.info(f"analysis 去重: 移除 {len(_to_remove)} 条重复记录")

    # 查询实体过滤：当查询包含具体指数名时，移除 valuation/analysis 中标题不匹配的结果
    # 避免语义搜索返回"中证白酒""中证红利"等不相关指数
    detected_indexes = _detect_index_names(query)
    if detected_indexes:
        before_count = len(all_results)
        index_keywords = set()
        for name in detected_indexes:
            index_keywords.add(name)
            core = name.replace("中证", "").replace("上证", "").replace("深证", "").strip()
            if core and len(core) >= 2:
                index_keywords.add(core)
        _strict_types = {"valuation", "analysis"}
        filtered = []
        for r in all_results:
            if r.get("content_type") in _strict_types:
                title = r.get("title", "")
                if any(kw in title for kw in index_keywords):
                    filtered.append(r)
            else:
                filtered.append(r)  # book/skill/knowledge 不过滤
        all_results = filtered
        if len(all_results) < before_count:
            logger.info(f"实体过滤: {before_count} → {len(all_results)}条 (指数: {index_keywords})")

    all_results.sort(key=lambda x: x["_score"], reverse=True)
    logger.info(f"RAG 合并后: {len(all_results)}条, 类型: {[r['content_type'] for r in all_results]}")

    # 结果多样性保证：如果查询涉及行业/指数，确保至少有 1 条估值数据
    _has_valuation = any(r.get("content_type") == "valuation" for r in all_results)
    _industry_keywords = ["消费", "白酒", "医药", "科技", "新能源", "金融", "地产", "军工", "周期", "行业"]
    _needs_valuation = any(kw in query for kw in _industry_keywords)
    if _needs_valuation and not _has_valuation and direct_valuation_results:
        # 从直接注入的估值数据中补充 1 条
        all_results.insert(0, direct_valuation_results[0])
        logger.info(f"多样性保证: 补充 1 条估值数据")

    # 轻量级重排序（基于 token 重叠率，快速提升精度）
    if len(all_results) > 3:
        all_results = lightweight_rerank(query, all_results, top_k=limit)
        logger.info(f"轻量级重排序后: {len(all_results)}条")

    # 默认个性化重排：画像 2.0、行为偏差、知识原子元数据提供温和加权。
    if all_results:
        _apply_personalization_boost(all_results, "default")
        for r in all_results:
            r["_score"] = r.get("_score", 0) + r.get("personal_boost", 0)
        all_results.sort(key=lambda x: x.get("_score", 0), reverse=True)

    # Reranker 重排序（可选，进一步提升精度，但增加 3-15s 延迟）
    # 默认关闭：RRF + 标题加权 + 时效性加权已能提供合理排序
    # 设置环境变量 RERANK_ENABLED=true 可开启
    if RERANK_ENABLED and len(all_results) > 3:
        all_results = rerank_results(query, all_results, top_k=limit, user_id="default")
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
        "book": "书籍知识",
    }
    for r in all_results:
        r["label"] = label_map.get(r["content_type"], r["content_type"])

    # 补充时间信息
    _enrich_results_with_time(all_results)

    # 合并直接注入的估值数据（放在最前面，确保 LLM 优先看到）
    if direct_valuation_results:
        # 去重：如果 RAG 已经有相同的估值结果，用直接注入的替换
        rag_keys = {f"{r['content_type']}:{r['reference_id']}" for r in all_results}
        for r in direct_valuation_results:
            key = f"{r['content_type']}:{r['reference_id']}"
            if key not in rag_keys:
                all_results.insert(0, r)

    if not all_results:
        return {
            "context": "", "results": [], "keywords": keywords, "query": query,
            "fts_count": fts_count, "chroma_count": chroma_count,
            "freshness_filtered": total_freshness_filtered,
        }

    # 构建上下文：以完整结果为单位，避免截断丢失来源标注
    parts = []
    total_chars = 0
    max_context_chars = get_rag_config_int("max_context_chars", 3000)
    body_preview_chars = get_rag_config_int("body_preview_chars", 600)

    for r in all_results:
        # 构建单条结果（包含来源标注）
        part = f"[{r['label']}] {r['title']}"
        if r.get("time"):
            part += f" ({r['time']})"
        if r.get("source"):
            part += f" [来源: {r['source']}]"

        # 添加 body（限制长度但保持完整性）
        if r["body"]:
            body_preview = r["body"][:body_preview_chars]
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
        "detected_indexes": detected_indexes,
        "direct_valuation_count": len(direct_valuation_results),
    }


# ── 批量索引函数 ──────────────────────────────────────────

def index_article_to_rag(article_id: int):
    """将文章索引到 FTS + ChromaDB。"""
    conn = _get_conn()
    # articles 表可能没有 content_text 列（图片格式的文章）
    try:
        article = conn.execute(
            "SELECT id, title, content_text FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()
    except Exception:
        # 没有 content_text 列，只用 title
        article = conn.execute(
            "SELECT id, title FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()
        if article:
            article = dict(article)
            article["content_text"] = ""
    conn.close()

    if not article:
        return False

    title = article["title"] or ""
    content = article.get("content_text") or ""

    if not content and not title:
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

    # 格式化为可读文本（FTS5 和 ChromaDB 都用格式化后的内容）
    formatted = _format_analysis_json(raw_response, index_name)

    # FTS 索引
    index_analysis_record(record["id"], index_name, formatted)

    # ChromaDB 索引
    index_to_chroma("analysis", str(record["id"]), index_name, formatted[:8000])

    logger.info(f"已索引分析记录: {index_name} (id={record_id})")
    return True


def reindex_all_articles(limit: int = 1000):
    """批量重建所有文章索引。"""
    from db import list_articles

    articles = list_articles()
    if len(articles) > limit:
        articles = articles[:limit]
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
    """批量重建所有索引（含 ChromaDB collection 重建）。"""
    results = {}

    # 0. 重建 ChromaDB collection（清除旧向量，用当前 embedding 模型维度重建）
    logger.info("重建 ChromaDB collection...")
    reset_chroma_collection()

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

    # 5. 重建书籍知识索引
    logger.info("开始重建书籍知识索引...")
    from db.knowledge import list_knowledge
    books = list_knowledge(category="book", limit=limit)
    book_indexed = 0
    for item in books:
        try:
            index_book_knowledge(
                knowledge_id=item["id"],
                title=item["title"],
                content=item["content"],
                source=item.get("source", ""),
            )
            book_indexed += 1
        except Exception as e:
            logger.warning(f"书籍索引失败 id={item['id']}: {e}")
    results["books"] = {"total": len(books), "indexed": book_indexed}

    # 6. 重建个人笔记索引（Obsidian vault）
    logger.info("开始重建个人笔记索引...")
    notes = list_knowledge(category="note", limit=limit)
    note_indexed = 0
    for item in notes:
        try:
            index_note_knowledge(
                knowledge_id=item["id"],
                title=item["title"],
                content=item["content"],
                source=item.get("source", ""),
            )
            note_indexed += 1
        except Exception as e:
            logger.warning(f"笔记索引失败 id={item['id']}: {e}")
    results["notes"] = {"total": len(notes), "indexed": note_indexed}

    logger.info(f"全部索引完成: {results}")
    return results


def get_rag_stats_summary():
    """获取 RAG 索引统计信息。"""
    conn = _get_conn()

    # FTS 统计
    fts_counts = {}
    for content_type in ["article", "analysis", "author_article", "skill", "valuation", "book"]:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM knowledge_fts WHERE content_type = ?",
            (content_type,)
        ).fetchone()
        fts_counts[content_type] = row["cnt"] if row else 0

    conn.close()

    # ChromaDB 统计（确保已初始化）
    chroma_counts = {}
    try:
        init_chroma()
        collection = _get_chroma()
        if collection:
            for content_type in ["article", "analysis", "author_article", "skill", "valuation", "book"]:
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
