"""RAG 检索增强模块。

提供查询扩展、多路召回、轻量级重排序等功能。
"""

import re
from typing import List, Dict, Tuple, Optional

# ══════════════════════════════════════════════════════════════
# 同义词词典
# ══════════════════════════════════════════════════════════════

SYNONYMS = {
    # 估值指标
    "PE": ["市盈率", "估值", "valuation", "earnings ratio", "盈利收益率"],
    "PB": ["市净率", "净资产", "book value", "账面价值"],
    "PEG": ["成长性估值", "增长比率"],
    "PS": ["市销率", "营收比"],
    "股息": ["分红", "dividend", "派息", "红利"],
    "估值": ["PE", "PB", "市盈率", "市净率", "百分位", "分位点"],

    # 投资策略
    "定投": ["定期定额", "DCA", "dollar cost averaging", "定额投资"],
    "止盈": ["获利了结", "take profit", "profit taking", "卖出"],
    "止损": ["stop loss", "割肉"],
    "回撤": ["下跌", "drawdown", "decline", "跌幅"],
    "抄底": ["低吸", "逢低买入", "buy the dip"],
    "追涨": ["追高", "追买"],

    # 风险指标
    "夏普": ["夏普比率", "sharpe ratio", "风险调整收益"],
    "波动": ["波动率", "volatility", "标准差"],
    "风险": ["risk", "不确定性", "回撤"],

    # 基金类型
    "指数基金": ["ETF", "被动基金", "index fund"],
    "主动基金": ["主动管理基金", "active fund"],
    "债券基金": ["债基", "bond fund"],
    "货币基金": ["余额宝", "money market fund"],

    # 宏观指标
    "GDP": ["国内生产总值", "经济增长"],
    "CPI": ["通胀", "物价", "消费价格指数"],
    "PMI": ["采购经理指数", "经济景气"],
    "M2": ["货币供应", "流动性"],

    # 市场术语
    "牛市": ["上涨", "多头", "bull market"],
    "熊市": ["下跌", "空头", "bear market"],
    "震荡": ["横盘", "盘整", "区间波动"],
    "趋势": ["方向", "走势", "trend"],
}


# ══════════════════════════════════════════════════════════════
# 知识图谱（简化版）
# ══════════════════════════════════════════════════════════════

KNOWLEDGE_GRAPH = {
    "PE": {
        "related": ["PB", "PEG", "股息率", "百分位", "估值"],
        "parent": "估值指标",
        "description": "市盈率 = 股价 / 每股收益",
    },
    "PB": {
        "related": ["PE", "净资产", "破净"],
        "parent": "估值指标",
        "description": "市净率 = 股价 / 每股净资产",
    },
    "定投": {
        "related": ["指数基金", "定期定额", "微笑曲线", "估值定投"],
        "parent": "投资策略",
        "description": "定期定额投资",
    },
    "指数基金": {
        "related": ["ETF", "定投", "被动投资", "沪深300"],
        "parent": "基金类型",
        "description": "跟踪指数的被动基金",
    },
    "估值": {
        "related": ["PE", "PB", "百分位", "低估", "高估"],
        "parent": "投资分析",
        "description": "判断资产贵贱的方法",
    },
}


# ══════════════════════════════════════════════════════════════
# 查询扩展
# ══════════════════════════════════════════════════════════════

def expand_query_with_synonyms(query: str) -> str:
    """使用同义词扩展查询。"""
    words = query.split()
    expanded = list(words)

    for word in words:
        # 精确匹配
        if word in SYNONYMS:
            expanded.extend(SYNONYMS[word])
        # 大小写不敏感匹配
        word_lower = word.lower()
        for key, values in SYNONYMS.items():
            if key.lower() == word_lower:
                expanded.extend(values)
                break

    # 去重并保持顺序
    seen = set()
    result = []
    for w in expanded:
        if w not in seen:
            seen.add(w)
            result.append(w)

    return " ".join(result)


def expand_query_with_graph(query: str) -> List[str]:
    """使用知识图谱扩展查询。"""
    expanded = []
    words = query.split()

    for word in words:
        if word in KNOWLEDGE_GRAPH:
            node = KNOWLEDGE_GRAPH[word]
            expanded.extend(node.get("related", []))

    return list(set(expanded))


def expand_query(query: str, use_synonyms: bool = True, use_graph: bool = True) -> str:
    """综合查询扩展。"""
    expanded = query

    if use_synonyms:
        expanded = expand_query_with_synonyms(expanded)

    if use_graph:
        graph_terms = expand_query_with_graph(query)
        if graph_terms:
            expanded += " " + " ".join(graph_terms)

    return expanded


# ══════════════════════════════════════════════════════════════
# 轻量级重排序
# ══════════════════════════════════════════════════════════════

def tokenize_chinese(text: str) -> List[str]:
    """简单的中文分词（按字和词切分）。"""
    # 简单实现：按空格切分 + 单字切分
    tokens = []
    for word in text.split():
        if len(word) <= 2:
            tokens.append(word)
        else:
            # 长词按 2-gram 切分
            for i in range(len(word) - 1):
                tokens.append(word[i:i+2])
    return tokens


def calculate_overlap(query_tokens: set, doc_tokens: set) -> float:
    """计算 token 重叠率。"""
    if not query_tokens:
        return 0.0
    intersection = query_tokens & doc_tokens
    return len(intersection) / len(query_tokens)


def calculate_bm25_like(query_tokens: List[str], doc_tokens: List[str],
                         k1: float = 1.5, b: float = 0.75) -> float:
    """简化的 BM25 风格打分。"""
    doc_len = len(doc_tokens)
    avg_doc_len = 100  # 假设平均文档长度

    score = 0.0
    for qt in query_tokens:
        tf = doc_tokens.count(qt)
        if tf > 0:
            idf = 1.0  # 简化：不计算真实 IDF
            tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_len))
            score += idf * tf_norm

    return score


def lightweight_rerank(query: str, results: List[Dict], top_k: int = 10) -> List[Dict]:
    """轻量级重排序。"""
    if not results:
        return results

    query_tokens = set(tokenize_chinese(query))
    query_tokens_lower = {t.lower() for t in query_tokens}

    for r in results:
        title = r.get("title", "")
        body = r.get("body", "")[:1000]

        title_tokens = set(tokenize_chinese(title))
        body_tokens = set(tokenize_chinese(body))

        # 计算多种相似度
        title_overlap = calculate_overlap(query_tokens, title_tokens)
        body_overlap = calculate_overlap(query_tokens, body_tokens)

        # 标题精确匹配（加分）
        title_exact = 1.0 if query.lower() in title.lower() else 0.0

        # 内容包含查询（加分）
        body_contains = 1.0 if query.lower() in body.lower() else 0.0

        # 综合打分
        r["_rerank_score"] = (
            0.35 * title_overlap +
            0.25 * body_overlap +
            0.25 * title_exact +
            0.15 * body_contains
        )

    # 按重排序分数排序
    results.sort(key=lambda x: x.get("_rerank_score", 0), reverse=True)

    return results[:top_k]


# ══════════════════════════════════════════════════════════════
# 多路召回融合
# ══════════════════════════════════════════════════════════════

def rrf_fusion(routes: List[Tuple[str, List[Dict], float]], k: int = 60) -> List[Dict]:
    """RRF (Reciprocal Rank Fusion) 融合多路召回结果。

    Args:
        routes: [(路由名, 结果列表, 权重), ...]
        k: RRF 参数

    Returns:
        融合后的结果列表
    """
    scores = {}  # key -> (score, result)

    for route_name, results, weight in routes:
        for i, r in enumerate(results):
            key = f"{r.get('content_type', '')}:{r.get('reference_id', '')}"
            rrf_score = weight / (k + i + 1)

            if key in scores:
                scores[key] = (scores[key][0] + rrf_score, scores[key][1])
            else:
                scores[key] = (rrf_score, r)

    # 按分数排序
    sorted_items = sorted(scores.values(), key=lambda x: x[0], reverse=True)

    # 更新分数并返回
    results = []
    for score, r in sorted_items:
        r["_score"] = score
        results.append(r)

    return results


def multi_route_recall(query: str, search_fts_func, search_chroma_func,
                        limit: int = 10) -> List[Dict]:
    """多路召回融合。

    Args:
        query: 用户查询
        search_fts_func: FTS5 搜索函数
        search_chroma_func: ChromaDB 搜索函数
        limit: 返回结果数

    Returns:
        融合后的结果列表
    """
    routes = []

    # 路由 1: 原始查询 FTS5
    fts_results = search_fts_func(query)
    routes.append(("fts_original", fts_results, 1.0))

    # 路由 2: 扩展查询 FTS5
    expanded_query = expand_query(query)
    if expanded_query != query:
        fts_expanded = search_fts_func(expanded_query)
        routes.append(("fts_expanded", fts_expanded, 0.8))

    # 路由 3: 原始查询向量
    chroma_results = search_chroma_func(query)
    routes.append(("chroma_original", chroma_results, 1.0))

    # RRF 融合
    fused = rrf_fusion(routes)

    # 轻量级重排序
    reranked = lightweight_rerank(query, fused, top_k=limit)

    return reranked


# ══════════════════════════════════════════════════════════════
# 查询意图分类
# ══════════════════════════════════════════════════════════════

def classify_query_intent(query: str) -> str:
    """分类查询意图。

    Returns:
        "factual" | "analytical" | "conceptual" | "advisory"
    """
    query_lower = query.lower()

    # 事实查询：问具体数据
    factual_patterns = [
        r"\d+%", r"多少", r"几", r"什么时候", r"最新",
        r"当前", r"现在", r"今天", r"昨天",
    ]
    for pattern in factual_patterns:
        if re.search(pattern, query_lower):
            return "factual"

    # 分析查询：问原因、比较
    analytical_patterns = [
        r"为什么", r"原因", r"区别", r"对比", r"比较",
        r"哪个更", r"怎么样", r"如何",
    ]
    for pattern in analytical_patterns:
        if re.search(pattern, query_lower):
            return "analytical"

    # 建议查询：问该怎么做
    advisory_patterns = [
        r"应该", r"建议", r"推荐", r"怎么买", r"怎么卖",
        r"要不要", r"可以买", r"可以卖",
    ]
    for pattern in advisory_patterns:
        if re.search(pattern, query_lower):
            return "advisory"

    # 概念查询：问定义、解释
    conceptual_patterns = [
        r"是什么", r"什么是", r"解释", r"含义", r"意思",
        r"定义", r"概念",
    ]
    for pattern in conceptual_patterns:
        if re.search(pattern, query_lower):
            return "conceptual"

    return "analytical"  # 默认为分析查询


def get_rrf_params(query: str) -> int:
    """根据查询意图返回 RRF 参数 k。"""
    intent = classify_query_intent(query)

    params = {
        "factual": 30,      # 事实查询：更信任精确匹配
        "analytical": 60,   # 分析查询：平衡
        "conceptual": 100,  # 概念查询：更信任语义
        "advisory": 60,     # 建议查询：平衡
    }

    return params.get(intent, 60)
