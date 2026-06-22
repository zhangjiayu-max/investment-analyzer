"""查询重写、分词、FTS查询构建"""
import sqlite3
import json
import logging
import re

from .config import get_rag_config

logger = logging.getLogger(__name__)

_jieba_loaded = False
_jieba = None

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
4. 如果涉及指数/资产，保留名称（如"沪深300"、"黄金"、"美债"）
5. 跨市场查询保留关键关联词（如"美联储降息对A股影响"需保留"美联储"、"降息"、"A股"）
6. 输出格式：关键词1 关键词2 关键词3

示例：
- "白酒现在估值高吗" → "白酒 估值 百分位"
- "沪深300可以买吗" → "沪深300 估值 买入"
- "机器人指数怎么样" → "机器人 指数 估值"
- "黄金还能买吗" → "黄金 价格 投资策略 实际利率"
- "纳斯达克现在贵吗" → "NASDAQ 估值 PE 百分位"
- "Fed降息对A股有什么影响" → "美联储 降息 中国 A股 影响"
- "美债收益率倒挂意味着什么" → "美债 收益率曲线 倒挂 经济衰退"
- "实际利率和黄金的关系" → "实际利率 黄金 价格 负相关"

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
    body = " | ".join(body_parts)
    _index_document("valuation", title, body, index_code)
    # ChromaDB 索引
    index_to_chroma("valuation", index_code, title, body)


def _format_analysis_json(raw_response: str, index_name: str = "") -> str:
    """将 analysis_records 的 JSON raw_response 格式化为可读文本。"""
    if not raw_response:
        return ""
    try:
        import json
        data = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
        if not isinstance(data, dict):
            return raw_response[:3000]

        def _float(v, default=None):
            """安全转 float。"""
            if v is None:
                return default
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        parts = []
        name = data.get("index_name") or index_name or ""
        metric = data.get("metric_type", "估值")
        if name:
            parts.append(f"指数: {name}")

        # 估值核心数据
        val = _float(data.get("current_value"))
        if val is not None:
            parts.append(f"当前{metric}: {val}")
        pct = _float(data.get("percentile"))
        if pct is not None:
            parts.append(f"百分位: {pct:.1f}%")
        point = _float(data.get("current_point"))
        if point is not None:
            parts.append(f"当前点位: {point}")
        change = _float(data.get("change_pct"))
        if change is not None:
            parts.append(f"涨跌幅: {change:+.2f}%")

        # 估值区间
        for key, label in [("danger_value", "危险值"), ("median", "中位数"), ("opportunity_value", "机会值"),
                           ("max_value", "历史最高"), ("min_value", "历史最低"), ("avg_value", "历史均值")]:
            v = _float(data.get(key))
            if v is not None:
                parts.append(f"{label}: {v}")

        zscore = _float(data.get("zscore"))
        if zscore is not None:
            parts.append(f"Z-Score: {zscore}")

        # 评估结论
        if pct is not None:
            if pct >= 80:
                parts.append("估值评估: 高估区域，注意风险")
            elif pct >= 50:
                parts.append("估值评估: 适中偏高")
            elif pct >= 20:
                parts.append("估值评估: 适中偏低，可关注")
            else:
                parts.append("估值评估: 低估区域，投资机会")

        return " | ".join(parts) if parts else raw_response[:3000]
    except (json.JSONDecodeError, TypeError):
        return raw_response[:3000]


def index_analysis_record(record_id: int, index_name: str, raw_response: str):
    """索引一条分析记录。"""
    formatted = _format_analysis_json(raw_response, index_name)
    _index_document("analysis", index_name or "", formatted[:3000] if formatted else "", str(record_id))


def index_author_article(article_id: int, title: str, content: str, publish_time: str = ""):
    """索引一篇作者文章（含发布日期，便于判断时效性）。"""
    # 在 body 开头添加日期前缀，让 LLM 能判断内容时效性
    date_prefix = f"[发布日期: {publish_time}] " if publish_time else ""
    body = date_prefix + (content[:5000] if content else "")
    _index_document("author_article", title or "", body, str(article_id))
    # ChromaDB 索引
    index_to_chroma("author_article", str(article_id), title or "", body[:8000])


def index_skill_document(doc_id: int, title: str, content: str):
    """索引一篇 Skill 文档（蒸馏后的结构化知识）。"""
    body = content[:8000] if content else ""
    _index_document("skill", title or "", body, str(doc_id))
    # ChromaDB 索引
    index_to_chroma("skill", str(doc_id), title or "", body)


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
    # ChromaDB 索引
    index_to_chroma("skill", str(article_id), title or "", body[:3000])


def _sanitize_fts_token(token: str) -> str:
    """处理 FTS5 特殊字符：含特殊字符或多字中文 token 用双引号包裹做短语匹配。"""
    import re
    # FTS5 特殊字符：% * " ( ) : ^ - & 等，全部需要引号包裹
    if re.search(r'[%*"():^\-&]', token):
        return f'"{token}"'
    # 多字中文 token 加引号，让 FTS5 做短语匹配而非单字符匹配
    # "债券" → "\"债券\"" → FTS5 要求"债"和"券"相邻出现
    if len(token) >= 2 and all('一' <= c <= '鿿' for c in token):
        return f'"{token}"'
    return token


# ── 共享停用词表（FTS 查询和 RAG 上下文构建共用）─────────────────
# 注意：投资领域高价值词汇（估值、投资、分析、趋势等）已移除，避免检索退化
_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "都", "上", "也",
    "到", "说", "要", "去", "你", "会", "着", "看", "好", "这", "他", "她",
    "能", "下", "过", "么", "吗", "呢", "把", "让", "被", "从", "向", "对",
    "以", "可以", "应该", "需要", "现在", "目前", "当前", "最近", "怎么样",
    "如何", "什么", "多少", "为什么", "怎样", "哪个", "哪些", "是否",
    "看看", "帮忙", "帮我", "请问", "想问", "情况", "那", "个", "一", "人",
    "很", "没有", "点", "想", "入手", "帮", "问", "数据",
    # 口语虚词 — 长句中常见但无检索价值
    "很多", "看到", "或者", "一些", "一直", "但是", "而且", "提出",
    "说什么", "也没有", "发现", "感觉", "觉得", "好像", "可能",
    "其实", "不过", "然后", "因为", "所以", "如果", "虽然",
    "已经", "还是", "这样", "那种", "比如",
    "外", "网", "发",
}


# 投资/财经领域核心术语 — 长查询时优先保留这些词
_FINANCE_CORE_TERMS = {
    "消费", "政策", "利好", "振兴", "内需", "零售", "通胀", "通缩",
    "降息", "加息", "降准", "GDP", "CPI", "PPI", "PMI", "LPR",
    "股市", "债市", "基金", "股票", "指数", "估值", "PE", "PB",
    "ROE", "ROA", "ETF", "定投", " rebalance", "再平衡",
    "医药", "科技", "新能源", "半导体", "军工", "房地产", "银行",
    "白酒", "食品", "饮料", "家电", "旅游", "教育",
    "红利", "质量", "价值", "成长", "沪深300", "中证500",
    "牛市", "熊市", "震荡", "反弹", "回调", "涨停", "跌停",
    "持仓", "仓位", "止损", "止盈", "加仓", "减仓",
    "宏观经济", "微观", "行业", "板块", "赛道", "龙头",
}

# 长查询阈值：超过此数量时启用核心词提取策略
_LONG_QUERY_TOKEN_THRESHOLD = 8


def _build_fts_query(query: str) -> str:
    """将用户问题转为 FTS5 查询：分词后过滤停用词，核心词用 AND、辅助词用 OR。

    优化策略：
    1. 对中文多字词使用 NEAR 查询，允许词之间有间隔
    2. 保留核心投资术语的完整性
    3. 对长句提取关键短语
    4. 长查询（>8 token）时只保留核心术语，避免噪音词淹没匹配
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

    # 长查询策略：当多字词超过阈值时，优先保留投资核心术语
    # 避免口语长句中 20+ 个词用 AND 连接导致零匹配
    if len(multi_char) > _LONG_QUERY_TOKEN_THRESHOLD:
        core_terms = [t for t in multi_char if t.strip('"') in _FINANCE_CORE_TERMS]
        if core_terms:
            # 核心术语用 AND，其余多字词用 OR 作为辅助
            other_multi = [t for t in multi_char if t not in core_terms]
            core = " AND ".join(core_terms)
            if other_multi:
                # 最多取前5个辅助词，避免查询过长
                aux = " OR ".join(other_multi[:5])
                return f"({core}) OR ({aux})"
            return core

    # 短查询：优先用 AND 连接多字关键词（精确匹配），单字关键词作为补充用 OR
    if multi_char:
        core = " AND ".join(multi_char)
        if single_char:
            return f"({core}) OR ({' OR '.join(single_char)})"
        return core
    return " OR ".join(single_char)

# ── 时效性策略：不同类型内容的有效期不同 ──────────────────
# 书籍知识(book)和估值数据(valuation)长期有效，不过滤
# 作者文章/分析记录有时效性，3个月
# 投资方法论(skill)长期有效，12个月
_FRESHNESS_POLICY = {
    "author_article": 3,  # 作者文章，季度内有效
    "skill": 12,  # 投资方法论长期有效，12个月
    "valuation": 0,   # 0 = 不过滤
    "article": 3,  # 文章，季度内有效
    "analysis": 3,  # 分析记录，季度内有效
    "linked_doc": 0,  # 个人文档不过滤
    "book": 0,  # 书籍知识长期有效，不过滤
}


def _build_fts_query_core(query: str) -> str:
    """中等严格度查询：仅用投资核心术语 AND，去掉口语辅助词。
    
    当全词 AND 结果太少时，用这个作为中间降级（比全词 AND 宽松，比全词 OR 严格）。
    策略：只取最长的 2-3 个核心术语做 AND，避免术语过多导致零匹配。
    """
    tokens = _tokenize(query).split()
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    cleaned = [_sanitize_fts_token(t) for t in tokens]
    cleaned = [t for t in cleaned if t]
    multi_char = [t for t in cleaned if len(t) >= 2]
    
    if not multi_char:
        return ""
    
    # 只保留投资核心术语
    core = [t for t in multi_char if t.strip('"') in _FINANCE_CORE_TERMS]
    if core:
        # 去重后取最长的 2-3 个（术语太多容易 AND 零匹配）
        unique = list(dict.fromkeys(core))  # 保序去重
        unique.sort(key=len, reverse=True)
        return " AND ".join(unique[:3])
    # 没有命中核心术语时，取前3个最长的词
    sorted_by_len = sorted(multi_char, key=len, reverse=True)
    return " AND ".join(sorted_by_len[:3])


def _build_fts_query_relaxed(query: str) -> str:
    """生成宽松的 FTS5 查询（OR 连接），作为 AND 无结果时的降级方案。"""
    tokens = _tokenize(query).split()
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    cleaned = [_sanitize_fts_token(t) for t in tokens]
    cleaned = [t for t in cleaned if t]
    if not cleaned:
        return ""
    return " OR ".join(cleaned)


