"""Query 理解器 — 轻量级 LLM 调用，做意图识别和信息需求提取。

设计要点：
- 一次 LLM 调用（100-200 tokens），不调专家不调工具
- 输出 JSON：intent / targets / needed_info / needs_clarification / complexity
- 识别简单闲聊 → 走快速路径，节省 90% token
- 复杂度评估 → 决定 token 预算和专家数量上限
- LLM 调用失败时降级为规则识别（零成本）

与现有模块的关系：
- query_rewriter.py 负责 query 改写（代词/省略补全），本模块负责意图识别
- 两者互补：先 understand_query 识别意图，再 rewrite_query 补全 query
"""

import json
import logging
import re
from typing import Optional

from db.config import get_config_bool, get_config_int

logger = logging.getLogger(__name__)


# ── 意图类型 ──────────────────────────────────

INTENT_GREETING = "greeting"              # 问候
INTENT_THANKS = "thanks"                  # 感谢
INTENT_FAREWELL = "farewell"              # 告别
INTENT_CAPABILITY_QUERY = "capability"    # 能力咨询
INTENT_VALUATION = "valuation"            # 估值查询
INTENT_BUY_SELL = "buy_sell"              # 买卖建议
INTENT_PORTFOLIO_REVIEW = "portfolio"     # 持仓分析
INTENT_ARTICLE = "article"                # 文章解读
INTENT_STRATEGY = "strategy"              # 策略咨询
INTENT_RISK = "risk"                      # 风险评估
INTENT_GENERAL_CHAT = "general_chat"      # 通用闲聊
INTENT_FINANCIAL_QUERY = "financial"      # 金融专业问题


# ── 简单闲聊模式（规则识别，零 LLM 成本） ──────────

_SIMPLE_CHAT_PATTERNS = {
    INTENT_GREETING: [r"^(你好|您好|hi|hello|hey|早上好|下午好|晚上好|哈喽|在吗)$",
                      r"^(你好|您好|hi|hello)[\s!！。.]*$"],
    INTENT_THANKS: [r"^(谢谢|感谢|thanks|thank you|多谢|辛苦了|谢啦)[\s!！。.]*$"],
    INTENT_FAREWELL: [r"^(再见|拜拜|bye|goodbye|88|晚安)[\s!！。.]*$"],
    INTENT_CAPABILITY_QUERY: [r"(你能做什么|你有什么功能|你可以做什么|功能|能力|帮什么忙)",
                              r"(怎么用|如何使用|使用方法)"],
}


def _match_simple_chat(query: str) -> Optional[str]:
    """规则匹配简单闲聊，返回 intent 或 None。"""
    q = query.strip().lower()
    if not q:
        return None
    for intent, patterns in _SIMPLE_CHAT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, q):
                return intent
    return None


# ── 关键词驱动的意图识别（规则降级用） ──────────────

_KEYWORD_INTENT_RULES = [
    # (关键词列表, intent, needed_info)
    (["估值", "pe", "pb", "百分位", "低估", "高估", "贵不贵", "便宜"], INTENT_VALUATION, ["valuation"]),
    (["买", "卖", "加仓", "减仓", "止盈", "止损", "定投", "入手", "出手", "操作"], INTENT_BUY_SELL, ["valuation", "portfolio", "risk"]),
    (["持仓", "我的基金", "我的仓位", "配置怎么样", "组合分析"], INTENT_PORTFOLIO_REVIEW, ["portfolio"]),
    (["文章", "公众号", "解读", "这篇"], INTENT_ARTICLE, ["article"]),
    (["策略", "方法", "怎么投", "如何配置", "投资方式"], INTENT_STRATEGY, ["strategy"]),
    (["风险", "回撤", "波动", "危险"], INTENT_RISK, ["risk", "portfolio"]),
]


def _rule_based_understand(query: str) -> dict:
    """规则驱动的 Query 理解（降级方案，零 LLM 成本）。"""
    q_lower = query.lower()

    # 1. 简单闲聊
    simple = _match_simple_chat(query)
    if simple:
        return {
            "intent": simple,
            "targets": [],
            "needed_info": [],
            "needs_clarification": False,
            "clarification_question": "",
            "clarification_reason": "",
            "clarification_options": [],
            "complexity": "simple",
            "source": "rule",
        }

    # 2. 关键词匹配
    for keywords, intent, needed_info in _KEYWORD_INTENT_RULES:
        if any(kw in q_lower for kw in keywords):
            # 复杂度估计
            complexity = _estimate_complexity_by_query(query)
            return {
                "intent": intent,
                "targets": _extract_targets(query),
                "needed_info": needed_info,
                "needs_clarification": False,
                "clarification_question": "",
                "clarification_reason": "",
                "clarification_options": [],
                "complexity": complexity,
                "source": "rule",
            }

    # 3. 默认：通用金融问题
    complexity = _estimate_complexity_by_query(query)
    return {
        "intent": INTENT_FINANCIAL_QUERY,
        "targets": _extract_targets(query),
        "needed_info": ["valuation", "portfolio"],
        "needs_clarification": False,
        "clarification_question": "",
        "clarification_reason": "",
        "clarification_options": [],
        "complexity": complexity,
        "source": "rule",
    }


def _estimate_complexity_by_query(query: str) -> str:
    """根据 query 长度和关键词估计复杂度。"""
    length = len(query)
    # 复杂信号：多个问号、多个标的、嵌套问题
    complex_signals = sum([
        query.count("？") + query.count("?") >= 2,
        query.count("和") + query.count("与") + query.count("对比") >= 1,
        length > 80,
        any(kw in query for kw in ["综合", "全面", "详细", "深入", "对比分析"]),
    ])
    if complex_signals >= 2:
        return "complex"
    if length > 30 or complex_signals >= 1:
        return "medium"
    return "simple"


# ── 标的提取 ──────────────────────────────────

# 常见指数/基金关键词
_INDEX_KEYWORDS = [
    "沪深300", "中证500", "中证1000", "创业板", "科创50", "上证50",
    "中证A500", "中证白酒", "中证消费", "中证医药", "中证银行",
    "恒生", "恒生科技", "纳斯达克", "标普500",
    "高端装备", "基建", "新能源", "光伏", "半导体", "芯片",
    "环保", "军工", "煤炭", "钢铁", "有色", "房地产",
]

_FUND_PATTERN = re.compile(r'(\d{6})')  # 6位基金代码
_INDEX_CODE_PATTERN = re.compile(r'(SH\d{6}|SZ\d{6}|000\d{3}|399\d{3})')


def _extract_targets(query: str) -> list[str]:
    """从 query 中提取涉及标的（指数名/基金代码）。"""
    targets = []
    # 1. 匹配指数关键词
    for kw in _INDEX_KEYWORDS:
        if kw in query:
            targets.append(kw)
    # 2. 匹配 6 位基金代码
    fund_codes = _FUND_PATTERN.findall(query)
    targets.extend(fund_codes)
    # 3. 去重保序
    seen = set()
    result = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


# ── LLM Prompt ──────────────────────────────

_UNDERSTAND_PROMPT = """## 任务：理解用户问题的意图和信息需求

用户问题：{query}

对话历史摘要：{history_summary}
持仓摘要：{portfolio_summary}

### 输出格式（严格 JSON，不要额外文本）
```json
{{
  "intent": "greeting|thanks|farewell|capability|valuation|buy_sell|portfolio|article|strategy|risk|general_chat|financial",
  "targets": ["沪深300", "中证500"],
  "needed_info": ["valuation", "portfolio", "risk", "strategy", "article"],
  "needs_clarification": false,
  "clarification_question": "",
  "clarification_reason": "",
  "clarification_options": [],
  "complexity": "simple|medium|complex"
}}
```

### 判断规则
1. **intent**：
   - greeting/thanks/farewell: 简单问候/感谢/告别
   - capability: 询问系统能力
   - valuation: 估值相关问题（PE/PB/百分位/低估高估）
   - buy_sell: 买卖操作建议（加仓/减仓/止盈/止损）
   - portfolio: 持仓分析/配置建议
   - article: 文章解读
   - strategy: 投资策略咨询
   - risk: 风险评估
   - general_chat: 通用闲聊（与投资无关）
   - financial: 金融专业问题（不属于上述类别）

2. **targets**：涉及的标的（指数名、基金代码、股票名），无则空数组

3. **needed_info**：需要收集的信息类型（可多选）
   - valuation: 估值数据
   - portfolio: 持仓数据
   - risk: 风险指标
   - strategy: 策略知识
   - article: 文章内容

4. **needs_clarification**：
   - true: 问题模糊，需要先问用户（如"那这只基金怎么样"无上下文）
   - false: 问题清晰，可直接分析

5. **clarification_reason**：
   - 当 needs_clarification=true 时，简要说明为什么需要澄清（如"问题含代词无上下文"、"意图模糊缺少具体标的"）
   - needs_clarification=false 时返回空字符串 ""

6. **clarification_options**：
   - 当 needs_clarification=true 时，提供 2-4 个选项供用户快速选择
   - 选项应覆盖可能的意图方向（如 ["估值分析", "买卖建议", "风险评估"]）
   - needs_clarification=false 时返回空数组 []

7. **complexity**：
   - simple: 单一标的、单一问题、长度<20字
   - medium: 1-2个标的、需要数据支撑、长度20-60字
   - complex: 多标的对比、嵌套问题、需要综合分析、长度>60字
"""


def _call_llm_for_understanding(
    query: str,
    history_summary: str,
    portfolio_summary: str,
    trace_id: str,
) -> Optional[dict]:
    """调用 LLM 做 Query 理解，失败返回 None。"""
    try:
        from services.llm_service import _call_llm, MODEL
    except ImportError:
        return None

    prompt = _UNDERSTAND_PROMPT.format(
        query=query[:500],  # 限制长度防止注入
        history_summary=(history_summary or "（无历史）")[:500],
        portfolio_summary=(portfolio_summary or "（无持仓）")[:500],
    )

    try:
        response = _call_llm(
            caller="query_understander",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=400,
        )
        content = response.choices[0].message.content or ""
        # 提取 JSON
        json_match = re.search(r'```json\s*\n(.*?)\n```', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # 尝试直接解析
        return json.loads(content)
    except Exception as e:
        logger.debug(f"[query_understander] LLM 调用失败，降级规则: {e}")
        return None


# ── 主入口 ──────────────────────────────────

def understand_query(
    query: str,
    history_summary: str = "",
    portfolio_summary: str = "",
    trace_id: str = "",
) -> dict:
    """理解用户问题的意图和信息需求。

    返回结构：
        {
            "intent": str,
            "targets": list[str],
            "needed_info": list[str],
            "needs_clarification": bool,
            "clarification_question": str,
            "complexity": str,  # simple|medium|complex
            "source": str,      # llm|rule
        }

    策略：
    1. 先用规则匹配简单闲聊（零成本）
    2. 如果规则命中简单闲聊，直接返回
    3. 否则调用 LLM 做精细理解（受 agent.query_understander_enabled 控制，默认 True）
    4. LLM 失败则降级为规则识别
    """
    if not query or not query.strip():
        return {
            "intent": INTENT_GENERAL_CHAT,
            "targets": [],
            "needed_info": [],
            "needs_clarification": True,
            "clarification_question": "请问您想了解什么？",
            "clarification_reason": "用户输入为空，需要明确问题方向",
            "clarification_options": ["估值查询", "持仓分析", "买卖建议", "投资策略"],
            "complexity": "simple",
            "source": "rule",
        }

    # 1. 规则匹配简单闲聊
    simple_intent = _match_simple_chat(query)
    if simple_intent:
        logger.info(f"[query_understander] 规则识别简单闲聊: {simple_intent}")
        return {
            "intent": simple_intent,
            "targets": [],
            "needed_info": [],
            "needs_clarification": False,
            "clarification_question": "",
            "clarification_reason": "",
            "clarification_options": [],
            "complexity": "simple",
            "source": "rule",
        }

    # 2. LLM 精细理解（开关控制，默认开启）
    use_llm = True
    try:
        use_llm = get_config_bool("agent.query_understander_enabled", True)
    except Exception:
        pass

    if use_llm and trace_id:
        llm_result = _call_llm_for_understanding(
            query, history_summary, portfolio_summary, trace_id
        )
        if llm_result and "intent" in llm_result:
            llm_result["source"] = "llm"
            # 补全缺字段
            llm_result.setdefault("targets", _extract_targets(query))
            llm_result.setdefault("needed_info", [])
            llm_result.setdefault("needs_clarification", False)
            llm_result.setdefault("clarification_question", "")
            llm_result.setdefault("clarification_reason", "")
            llm_result.setdefault("clarification_options", [])
            llm_result.setdefault("complexity", _estimate_complexity_by_query(query))
            return llm_result

    # 3. 降级：规则识别
    return _rule_based_understand(query)


# ── 便捷判断函数 ──────────────────────────────

def is_simple_chat(query_info: dict) -> bool:
    """判断是否为简单闲聊，走快速路径不调专家。"""
    intent = query_info.get("intent", "")
    return intent in (INTENT_GREETING, INTENT_THANKS, INTENT_FAREWELL, INTENT_CAPABILITY_QUERY)


def get_complexity(query_info: dict) -> str:
    """获取复杂度。"""
    return query_info.get("complexity", "medium")


def needs_clarification(query_info: dict) -> tuple[bool, str]:
    """判断是否需要澄清，返回 (是否需要, 澄清问题)。"""
    if query_info.get("needs_clarification"):
        return True, query_info.get("clarification_question", "请提供更多细节")
    return False, ""
