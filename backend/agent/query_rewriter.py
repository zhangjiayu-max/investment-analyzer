"""查询改写引擎 — 规则 + LLM 混合改写，解决多轮对话中的代词/省略问题。

借鉴企业级 RAG 多轮对话题：用户第 2 轮"那差旅标准是多少"需改写为完整 query。

策略：
- 80% 情况：规则改写（代词替换、主题补充），零 LLM 成本
- 20% 情况：规则搞不定的复杂场景用 LLM 改写，成本 < 100 tokens/次

增强功能：
- expand_query(): 隐含意图识别 + 多角度子查询展开，提升 RAG 召回率
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 隐含意图识别规则 ──────────────────────────────────────────
# 用户口语化问题 → 隐含的检索维度
_IMPLICIT_INTENT_RULES: list[dict] = [
    {
        "patterns": [r"要不要加仓", r"加不加仓", r"现在能加仓", r"可以加仓"],
        "intent": "加仓时机判断",
        "sub_queries": [
            "当前估值水平 PE PB 百分位",
            "风险评估 最大回撤 波动率",
            "加仓时机判断 买入信号",
        ],
    },
    {
        "patterns": [r"要不要减仓", r"减不减仓", r"现在该减", r"可以减仓"],
        "intent": "减仓时机判断",
        "sub_queries": [
            "当前估值水平 是否高估",
            "风险信号 止盈信号 卖出时机",
            "减仓策略 仓位管理",
        ],
    },
    {
        "patterns": [r"要不要买", r"现在能买", r"可以买吗", r"入手"],
        "intent": "买入时机判断",
        "sub_queries": [
            "当前估值水平 PE PB 百分位",
            "风险评估 最大回撤",
            "买入时机 投资策略",
        ],
    },
    {
        "patterns": [r"要不要卖", r"现在该卖", r"可以卖吗", r"出手"],
        "intent": "卖出时机判断",
        "sub_queries": [
            "当前估值水平 是否高估",
            "止盈策略 卖出信号",
            "风险提示 市场情绪",
        ],
    },
    {
        "patterns": [r"怎么看", r"怎么样", r"如何看", r"分析一下"],
        "intent": "综合分析",
        "sub_queries": [
            "当前估值水平 PE PB 百分位",
            "风险评估 回撤 波动",
            "投资策略 建议",
        ],
    },
    {
        "patterns": [r"定投", r"定额"],
        "intent": "定投策略",
        "sub_queries": [
            "定投策略 定期定额 DCA",
            "估值定投 微笑曲线",
            "定投止盈 退出策略",
        ],
    },
    {
        "patterns": [r"配置", r"仓位", r"比例"],
        "intent": "资产配置",
        "sub_queries": [
            "资产配置 股债比例 分散投资",
            "仓位管理 风险控制",
            "再平衡策略",
        ],
    },
]


def _detect_implicit_intent(query: str) -> Optional[dict]:
    """检测用户查询中的隐含意图。

    Returns:
        匹配到的意图 dict（含 intent 和 sub_queries），未匹配返回 None。
    """
    for rule in _IMPLICIT_INTENT_RULES:
        for pat in rule["patterns"]:
            if re.search(pat, query):
                return rule
    return None


def expand_query(user_query: str) -> list[str]:
    """将用户查询展开为多个子查询，提升 RAG 召回率。

    策略：
    1. 隐含意图识别：检测口语化表达背后的真实检索需求
    2. 多角度展开：一个查询改写为 2-3 个子查询
    3. 保留原始查询作为第一个子查询（兜底）

    Args:
        user_query: 用户原始查询

    Returns:
        子查询列表（含原始查询），长度 1-4
    """
    if not user_query or not user_query.strip():
        return []

    sub_queries = [user_query.strip()]

    # 1. 隐含意图识别
    intent = _detect_implicit_intent(user_query)
    if intent:
        for sq in intent.get("sub_queries", []):
            if sq not in sub_queries:
                sub_queries.append(sq)
        logger.info(
            f"expand_query: 检测到隐含意图 '{intent['intent']}', "
            f"展开为 {len(sub_queries)} 个子查询"
        )
        return sub_queries

    # 2. 通用多角度展开（无隐含意图时）
    # 提取核心名词，生成不同检索角度
    core_nouns = _extract_core_nouns(user_query)
    if core_nouns:
        primary = core_nouns[0]
        # 角度1：估值视角
        val_q = f"{primary} 估值 PE PB 百分位"
        if val_q not in sub_queries:
            sub_queries.append(val_q)
        # 角度2：风险视角
        risk_q = f"{primary} 风险 回撤 波动"
        if risk_q not in sub_queries:
            sub_queries.append(risk_q)
        # 角度3：策略视角（仅当查询较长时）
        if len(user_query) > 8:
            strategy_q = f"{primary} 投资策略 买卖时机"
            if strategy_q not in sub_queries and len(sub_queries) < 4:
                sub_queries.append(strategy_q)

    return sub_queries


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
    """从文本中抽取核心名词（基金代码/名称/指数 + jieba 主题词）。"""
    nouns: list[str] = []
    # 1. 正则匹配基金代码/指数名/市场名
    for pat in _NOUN_PATTERNS:
        nouns.extend(re.findall(pat, text))

    # 2. jieba 分词提取名词性词语（主题词识别，如"自然灾害/恢复建设/政策利好"）
    try:
        import jieba
        import jieba.posseg as pseg
        # 名词性词性：n 普通/ nr 人名/ ns 地名/ nt 机构名/ nz 其他专名/ vn 动名词
        noun_poses = {'n', 'nr', 'ns', 'nt', 'nz', 'vn'}
        # 停用词过滤（短词/泛化词，但保留金融主题词如"政策/利好/估值"等）
        stop_words = {
            '什么', '怎么', '怎么样', '如何', '为什么', '最近', '目前', '现在',
            '当前', '今天', '情况', '问题',
            '时候', '地方', '方面', '东西', '事情', '感觉', '觉得',
            '可以', '能', '能不', '可以不', '是不是', '有没有', '好不好',
            '嘛', '吗', '呢', '啊', '吧', '哦', '呀',
        }
        words = pseg.cut(text)
        for word, flag in words:
            w = word.strip()
            # 名词性、长度≥2、非停用词、非纯数字
            if (flag in noun_poses and len(w) >= 2
                    and w not in stop_words and not w.isdigit()
                    and w not in nouns):
                nouns.append(w)
    except ImportError:
        # jieba 不可用时降级：提取2-4字中文词组
        for m in re.findall(r'[\u4e00-\u9fa5]{2,4}', text):
            if m not in nouns and len(m) >= 2:
                nouns.append(m)

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


# ── 澄清续答融合 ────────────────────────────────────────────

def fuse_clarified_query(original_query: str, user_answer: str, trace_id: str = "") -> str:
    """将原始问题与用户澄清回答用 LLM 语义融合为一个自完整查询。

    Args:
        original_query: 用户原始问题
        user_answer: 用户选择的澄清选项文本
        trace_id: 追踪 ID

    Returns:
        融合后的查询（失败时降级为字符串拼接）
    """
    if not user_answer or not user_answer.strip():
        return original_query

    # 快速路径：回答较短且原始问题不含代词时直接拼接（省 LLM 调用）
    if len(user_answer) < 10 and not any(
        re.search(pat, original_query) for pat in PRONOUN_PATTERNS
    ):
        return f"{original_query} {user_answer}".strip()

    prompt = f"""请将用户的原始问题与补充回答融合为一个完整的、自包含的投资分析问题。

原始问题：{original_query}
用户补充：{user_answer}

要求：
1. 融合后的问题应包含原始问题和补充回答的全部信息
2. 语言通顺，不是机械拼接
3. 保持投资分析的专业语境
4. 只输出融合后的结果，不要解释，不要加引号"""

    try:
        from services.llm_service import _call_llm, MODEL
        resp = _call_llm(
            caller="query_fuser",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        fused = (resp.choices[0].message.content or "").strip()
        if fused and 5 < len(fused) < 300:
            fused = fused.strip("\"'""「」")
            if fused and fused != original_query:
                logger.info(
                    f"[query_fuser:{trace_id}] 融合: '{original_query}' + '{user_answer}' → '{fused}'"
                )
                return fused
    except Exception as e:
        logger.warning(f"[query_fuser:{trace_id}] LLM 融合失败，降级为拼接: {e}")

    # 降级：字符串拼接
    return f"{original_query} {user_answer}".strip()
