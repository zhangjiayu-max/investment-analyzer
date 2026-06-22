"""意图识别、复杂度判断、需求澄清、专家路由"""
import json
import logging
import re

from llm_service import _call_llm
from db.agents import load_specialist_agents

logger = logging.getLogger(__name__)


# ── 需求澄清 Agent（LLM 版）──────────────────────────────────

# ── 需求澄清 Agent（LLM 版）──────────────────────────────────

def build_clarification_prompt() -> str:
    """从数据库动态生成需求路由提示词。"""
    specialists = load_specialist_agents()
    expert_lines = []
    for key, info in specialists.items():
        expert_lines.append(f"- {key}: {info['description']}")
    expert_list = "\n".join(expert_lines)
    keys_json = json.dumps(list(specialists.keys()), ensure_ascii=False)

    return f"""你是投资分析需求路由专家。分析用户问题，返回 JSON。

## 可用专家
{expert_list}

## 复杂度判断规则

### chat - 闲聊/科普（不调用任何专家）
- 纯问候/感谢/道歉（"你好"、"谢谢"）
- 概念解释（"什么是PE"、"解释定投"）
- 与投资无关的问题（"今天天气"）

### simple - 单一数据查询（1个专家）
- 查询单一指标（"沪深300估值"、"债市温度"）
- 查询持仓概况（"我持有什么"）

### medium - 分析任务（1个专家）
- **对比分析**（"A和B区别"、"A和B哪个好"）→ 只用 1 个估值专家
- 单一维度深度分析（"白酒估值高吗"、"适合买债券吗"）
- **"可以买吗"/"值得买吗"** → 只问估值判断
- 持仓诊断（"我的持仓健康吗"）
- 简单建议（"买点货币基金可以吗"）

### complex - 综合决策（2+个专家）
- 需要多维度分析（估值+配置+风险）
- 涉及具体操作建议（"帮我做个定投方案"）
- 多市场联动分析（"美股大跌对A股影响"）

## 关键规则
1. **对比类问题只用 1 个专家**，不要触发多 agent
2. **"可以买吗"是 medium**，不是 complex
3. **简单建议类是 medium**，只需 1 个专家

## 输出格式（只输出JSON）
{{"complexity":"chat|simple|medium|complex","specialists":["expert1"],"reason":"判断原因","refined_query":"优化后的查询","confidence":0.95}}

- specialists 中的值必须是：{keys_json}，chat 时为空数组
- confidence 低于 0.7 时系统会降级处理

## 示例

Q: 你好
A: {{"complexity":"chat","specialists":[],"reason":"纯问候","refined_query":"你好","confidence":0.99}}

Q: 沪深300估值多少
A: {{"complexity":"simple","specialists":["valuation_expert"],"reason":"单一指数估值查询","refined_query":"沪深300当前PE/PB估值和百分位","confidence":0.95}}

Q: 红利质量和中证红利有什么区别
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"对比分析，只需1个估值专家","refined_query":"红利质量和中证红利的估值对比（PE/PB/百分位/股息率）","confidence":0.90}}

Q: 恒生科技怎么样，可以买吗
A: {{"complexity":"medium","specialists":["valuation_expert"],"reason":"估值判断，不需要多专家","refined_query":"恒生科技指数估值水平和投资建议","confidence":0.90}}

Q: 帮我做个定投方案
A: {{"complexity":"complex","specialists":["valuation_expert","allocation_advisor"],"reason":"定投需要估值+配置策略","refined_query":"基于当前估值的定投方案","confidence":0.92}}

Q: 美股大跌，A股明天会怎么走
A: {{"complexity":"complex","specialists":["market_analyst","valuation_expert","risk_assessor"],"reason":"多市场联动分析，需要多维分析","refined_query":"美股大跌原因、A股走势预判及持仓影响","confidence":0.90}}"""



# ── 基于规则的复杂度预判（零 LLM 调用）────────────────────────

def _classify_complexity_by_rules(query: str, has_portfolio: bool = False, has_watchlist: bool = False) -> str:
    """基于规则预判用户问题的复杂度，避免 LLM 调用。

    返回: "chat" | "simple" | "medium" | "complex"

    设计原则：
    - simple 和 complex 直接走规则（确定性高）
    - medium 需要 LLM 确认（边界情况多）
    """
    text = (query or "").strip()
    if not text:
        return "chat"

    text_lower = text.lower()
    length = len(text)

    # ── 闲聊检测（短消息 + 闲聊关键词 + 无投资内容）──
    chat_keywords = ["你好", "谢谢", "好的", "明白了", "知道了", "嗯",
                     "天气", "笑话", "故事", "晚安", "早上好", "嗨", "hi", "hello", "hey"]
    # 纯问候/感谢/闲聊
    if length <= 10 and any(kw in text_lower for kw in chat_keywords):
        # 确保没有投资关键词
        invest_markers = ["估值", "PE", "PB", "持仓", "买入", "卖出", "基金",
                          "股票", "债券", "定投", "配置", "风险", "收益"]
        if not any(m in text_lower for m in invest_markers):
            return "chat"

    # 极短消息（<=5字），无投资关键词，无疑问词
    if length <= 5:
        has_question = bool(re.search(r'[吗呢？?]', text))
        invest_markers = ["估值", "pe", "pb", "持仓", "买入", "卖出", "基金",
                          "股票", "债券", "债市", "定投", "配置"]
        concept_markers = ["什么是", "解释", "原理", "概念", "定义", "含义"]
        if not has_question and not any(m in text_lower for m in invest_markers) \
                and not any(m in text_lower for m in concept_markers):
            return "chat"

    # ── Simple 检测（短查询 + 简单关键词）──
    simple_keywords = ["什么是", "解释", "价格", "多少", "是多少", "查一下",
                       "查询", "最新", "今天", "估值", "百分位", "PE", "PB",
                       "z-score", "债市温度", "温度"]
    # 强制 simple 的前缀关键词（解释/定义类问题，即使包含投资术语也是 simple）
    force_simple_prefixes = ["什么是", "解释", "怎么算", "概念", "原理", "含义"]
    has_force_simple = any(text_lower.startswith(p) for p in force_simple_prefixes)
    has_simple = any(kw in text_lower for kw in simple_keywords)
    if length < 30 and has_simple:
        # 排除：虽然短但包含复杂意图（但强制 simple 前缀跳过排除）
        if not has_force_simple:
            complex_markers = ["分析", "对比", "比较", "建议", "配置", "风险",
                               "方案", "策略", "计划", "加仓", "减仓", "定投"]
            if any(m in text_lower for m in complex_markers):
                # 有疑问词 + 估值关键词 → 可能需要分析，交给 LLM 确认
                has_question = bool(re.search(r'[吗呢？?]', text))
                if has_question and any(kw in text_lower for kw in ["估值", "高", "低", "贵", "便宜"]):
                    return "medium"  # 边界情况，交给 LLM
                return "medium"  # 有复杂意图，交给 LLM
        # 有疑问词 + 估值关键词 → 可能需要分析，交给 LLM 确认
        has_question = bool(re.search(r'[吗呢？?]', text))
        if has_question and any(kw in text_lower for kw in ["估值", "高", "低", "贵", "便宜"]):
            return "medium"  # 边界情况，交给 LLM
        return "simple"

    # ── Complex 检测（长查询 + 多个投资关键词 + 有持仓数据）──
    complex_keywords = ["分析", "对比", "比较", "建议", "配置", "风险",
                        "方案", "策略", "计划", "加仓", "减仓", "定投",
                        "组合", "仓位", "再平衡", "回撤", "波动"]
    complex_match_count = sum(1 for kw in complex_keywords if kw in text_lower)

    # 长查询 + 多个复杂关键词 → complex
    if length > 100 and complex_match_count >= 2:
        return "complex"

    # 有持仓 + 复杂关键词组合 → complex
    if has_portfolio and complex_match_count >= 2:
        return "complex"

    # 涉及多维度分析的关键词组合 → complex
    multi_dim_triggers = [
        ("分析", ["配置", "风险", "建议"]),  # 分析 + 配置/风险/建议
        ("建议", ["配置", "风险", "组合"]),  # 建议 + 配置/风险/组合
        ("定投", ["方案", "策略", "计划"]),  # 定投 + 方案/策略/计划
    ]
    for primary, secondaries in multi_dim_triggers:
        if primary in text_lower and any(s in text_lower for s in secondaries):
            return "complex"

    # ── 未命中规则 → 返回 medium（需要 LLM 确认）──
    return "medium"


# Clarification 结果缓存（相同查询直接返回缓存结果，节省 2-5s LLM 调用）
_clarification_cache: dict[int, dict] = {}
_CLARIFICATION_CACHE_MAX = 128


def detect_scenario_type(query: str) -> str:
    """确定性识别投资问题场景，用于模板、RAG 和评测分流。"""
    text = query or ""
    if detect_urls(text) or any(word in text for word in ["文章", "作者观点", "这篇", "观点是否", "观点靠谱吗"]):
        return "article_check"
    if any(word in text for word in ["复盘", "回顾", "上次决策", "这次决策效果"]):
        return "decision_review"
    if any(word in text for word in ["什么是", "解释", "怎么算", "概念", "区别", "原理"]):
        return "knowledge_qa"
    if any(word in text for word in ["卖出", "减仓", "止盈", "止损", "退出", "要不要卖"]):
        return "sell_decision"
    if any(word in text for word in ["买入", "加仓", "建仓", "定投", "可以买吗", "值得买吗", "能不能买"]):
        return "buy_decision"
    if any(word in text for word in ["持仓", "组合", "仓位", "集中", "分散", "再平衡", "资产配置"]):
        return "portfolio_review"
    return "general_analysis"


def clarify_requirement(query: str) -> dict:
    """
    分析用户问题，返回需求澄清结果。

    优化策略：先走规则预判（零 LLM 调用），仅在边界情况（medium）时调用 LLM。
    预期节省 2-3 秒首响时间（>70% 的查询可跳过 LLM）。

    返回:
        {
            "complexity": "chat|simple|medium|complex",
            "specialists": ["valuation_expert", ...],
            "reason": "...",
            "refined_query": "..."
        }
    """
    # 检查缓存
    cache_key = hash(query)
    if cache_key in _clarification_cache:
        logger.debug(f"Clarification 缓存命中: {query[:30]}...")
        return _clarification_cache[cache_key]

    # ── Step 1: 规则预判（零 LLM 调用）──
    has_portfolio = False
    has_watchlist = False
    try:
        from portfolio_context import build_portfolio_summary_line
        portfolio_line = build_portfolio_summary_line()
        # 如果持仓摘要不是"无持仓"，说明有持仓数据
        has_portfolio = bool(portfolio_line and "无持仓" not in portfolio_line)
    except Exception:
        portfolio_line = ""

    try:
        from db.portfolio import get_watchlist
        watchlist = get_watchlist("default")
        has_watchlist = bool(watchlist)
    except Exception:
        pass

    rule_complexity = _classify_complexity_by_rules(query, has_portfolio, has_watchlist)
    logger.info(f"规则预判复杂度: {rule_complexity} (query={query[:50]}..., portfolio={has_portfolio}, watchlist={has_watchlist})")

    # ── Step 2: 非 medium 结果直接走规则路径（跳过 LLM）──
    if rule_complexity in ("chat", "simple", "complex"):
        specialists = route_to_specialists_by_keywords(query) if rule_complexity != "chat" else []
        result_out = {
            "complexity": rule_complexity,
            "specialists": specialists,
            "reason": f"规则预判（{rule_complexity}）",
            "refined_query": query,
            "confidence": 0.85,  # 规则预判置信度
            "scenario_type": detect_scenario_type(query),
            "classification_method": "rules",
        }
        # 缓存结果
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out
        return result_out

    # ── Step 3: medium 结果 → 调用 LLM 确认（保留原逻辑）──
    logger.info(f"规则返回 medium，调用 LLM 确认: {query[:50]}...")

    try:
        user_content = query
        if portfolio_line:
            user_content = f"{portfolio_line}\n\n用户问题: {query}"

        response = _call_llm(
            caller="clarify",
            model=MODEL,
            messages=[
                {"role": "system", "content": build_clarification_prompt()},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=8192,
        )

        raw = response.choices[0].message.content.strip()

        # 提取 JSON — 多种容错策略
        # 1. 去除 markdown 代码块
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # 2. 尝试直接解析
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # 3. 提取第一个 {...}
            import re
            match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
            if match:
                result = json.loads(match.group())
            else:
                raise

        # 兼容模型返回非标准格式（如 {"需求分析": {...}} ）
        if "complexity" not in result:
            # 尝试从嵌套结构中提取
            for key in result:
                if isinstance(result[key], dict) and "complexity" in result[key]:
                    result = result[key]
                    break
            # 仍然没有 → 检查是否有需求类型字段
            if "complexity" not in result:
                # 根据内容推断
                needs = str(result)
                if any(kw in needs for kw in ["买入", "建议", "配置", "决策", "风险"]):
                    result = {"complexity": "complex", "specialists": ["market_analyst", "allocation_advisor"],
                              "reason": "从非标准响应推断", "refined_query": query}
                else:
                    result = {"complexity": "medium", "specialists": ["valuation_expert"],
                              "reason": "从非标准响应推断", "refined_query": query}

        # 验证并设置默认值
        complexity = result.get("complexity", "medium")
        if complexity not in ("chat", "simple", "medium", "complex"):
            complexity = "medium"

        specialists = result.get("specialists", [])
        valid_specialists = list(load_specialist_agents().keys())
        specialists = [s for s in specialists if s in valid_specialists]

        # chat 类型不需要专家
        if complexity == "chat":
            specialists = []
        # 如果没有选择专家，默认选估值专家（chat 除外）
        elif not specialists:
            specialists = ["valuation_expert"]

        # 置信度检查
        confidence = result.get("confidence", 0.8)
        if confidence < 0.7:
            logger.warning(f"澄清置信度过低 ({confidence})，降级为 simple")
            complexity = "simple"
            specialists = ["valuation_expert"]

        result_out = {
            "complexity": complexity,
            "specialists": specialists,
            "reason": result.get("reason", ""),
            "refined_query": result.get("refined_query", query),
            "confidence": confidence,
            "scenario_type": detect_scenario_type(query),
            "classification_method": "llm",
        }

        # 缓存结果
        if len(_clarification_cache) >= _CLARIFICATION_CACHE_MAX:
            _clarification_cache.pop(next(iter(_clarification_cache)))
        _clarification_cache[cache_key] = result_out

        return result_out

    except Exception as e:
        logger.warning(f"LLM 澄清失败，回退到关键词匹配: {e}")
        # 回退到关键词匹配
        complexity = detect_complexity_by_keywords(query)
        specialists = route_to_specialists_by_keywords(query)
        return {
            "complexity": complexity,
            "specialists": specialists,
            "reason": "关键词匹配（LLM澄清失败）",
            "refined_query": query,
            "confidence": 0.5,
            "scenario_type": detect_scenario_type(query),
            "classification_method": "keywords_fallback",
        }



# ── 任务复杂度检测（关键词匹配，作为回退方案）──────────────────────────

def detect_complexity_by_keywords(query: str) -> str:
    """
    检测任务复杂度：simple / medium / complex

    simple: 单一数据查询（如"沪深300估值多少"、"债市温度"）
    medium: 需要分析但范围明确（如"白酒估值高吗"、"最近有什么新闻"）
    complex: 需要多维度分析（如"白酒能买吗"、"帮我做个定投方案"）
    """
    query = query.strip()

    # 复杂任务关键词（需要多专家协作：投资决策+仓位+风险）
    complex_keywords = [
        "加仓", "减仓", "建仓", "清仓",
        "定投", "配置", "组合", "方案", "策略", "计划",
        "风险", "回撤", "波动",
        "持仓", "盈亏", "我的基金", "仓位",
        "怎么分配", "如何配置",
    ]

    # 中等任务关键词（对比分析、单一维度分析）
    medium_keywords = [
        "对比", "比较", "区别", "差异", "哪个好", "选哪个", "还是",
        "怎么样", "怎么看", "值得买", "能买吗", "可以买", "买入",
        "卖出", "持有",
        "现在", "当前", "适合", "应该",
    ]

    # 简单任务关键词（单一数据查询）
    simple_keywords = [
        "估值", "百分位", "PE", "PB", "z-score",
        "债市温度", "温度",
        "多少", "是什么", "查一下", "查询",
        "最新", "今天", "最近",
    ]

    # 闲聊关键词（不需要专家分析）
    chat_keywords = [
        "你好", "谢谢", "好的", "明白了", "知道了",
        "什么是", "解释", "介绍", "定义", "含义",
        "天气", "笑话", "故事",
    ]

    # 检查是否是复杂任务
    complex_score = sum(1 for kw in complex_keywords if kw in query)
    # 检查是否是中等任务
    medium_score = sum(1 for kw in medium_keywords if kw in query)
    # 检查是否是简单任务
    simple_score = sum(1 for kw in simple_keywords if kw in query)
    # 检查是否是闲聊
    chat_score = sum(1 for kw in chat_keywords if kw in query)

    # 如果包含"吗"、"呢"等疑问词
    has_question_mark = bool(re.search(r'[吗呢？?]', query))

    # 纯闲聊：短消息 + 闲聊关键词 + 无投资关键词
    if len(query) <= 10 and chat_score > 0 and complex_score == 0 and medium_score == 0 and simple_score == 0:
        return "chat"

    # 很短的消息（<6字），没有投资关键词，也没有疑问词 → chat
    if len(query) <= 5 and complex_score == 0 and medium_score == 0 and simple_score == 0 and not has_question_mark:
        return "chat"

    # 如果只是查询单一指标（很短的查询，且无疑问词），倾向于简单
    if len(query) <= 6 and simple_score > 0 and not has_question_mark and complex_score == 0:
        return "simple"

    # 有疑问词时，需要进一步分析
    if has_question_mark:
        # 包含复杂关键词 → complex
        if complex_score >= 1:
            return "complex"
        # 包含中等关键词 → medium（如"可以买吗"、"A和B区别"）
        if medium_score >= 1:
            return "medium"
        # 包含简单关键词但有疑问 → medium（如"估值高吗"）
        if simple_score >= 1:
            return "medium"
        # 其他有疑问的 → medium
        return "medium"

    # 无疑问词时
    if complex_score >= 2:
        return "complex"
    elif complex_score >= 1 or medium_score >= 1:
        return "medium"
    elif simple_score >= 1:
        return "medium"
    else:
        return "simple"


def route_to_specialists_by_keywords(query: str) -> list[str]:
    """根据关键词路由到合适的专家。返回 agent_key 列表。"""
    query = query.strip()
    specialists = []

    # 链接检测 → 文章解读专家
    if detect_urls(query):
        specialists.append("article_expert")
        # 如果只是链接+简单指令，只用文章专家
        query_without_url = re.sub(r'https?://[^\s]+', '', query).strip()
        if len(query_without_url) < 20:
            return specialists

    # 估值相关关键词 → 估值专家
    valuation_keywords = ["估值", "PE", "PB", "百分位", "z-score", "高估", "低估", "贵不贵", "便宜"]
    if any(kw in query for kw in valuation_keywords):
        specialists.append("valuation_expert")

    # 新闻/市场动态关键词 → 择时分析师
    news_keywords = ["新闻", "最新", "动态", "政策", "消息", "市场", "今天", "昨天"]
    if any(kw in query for kw in news_keywords):
        specialists.append("market_analyst")

    # 风险相关关键词 → 风险评估师
    risk_keywords = ["风险", "回撤", "波动", "最大回撤", "重仓", "满仓", "清仓"]
    if any(kw in query for kw in risk_keywords):
        specialists.append("risk_assessor")

    # 行为偏差关键词 → 行为金融辅导师
    behavior_keywords = [
        "追涨", "杀跌", "恐慌", "很慌", "焦虑", "忍不住", "冲动",
        "补亏", "回本", "重仓", "满仓", "梭哈", "频繁交易", "踏空",
    ]
    if any(kw in query for kw in behavior_keywords):
        specialists.append("behavior_coach")

    # 债券相关关键词 → 市场分析师 + 资产配置师
    bond_keywords = ["债券", "债市", "国债", "利率债", "信用债", "可转债", "收益率",
                     "久期", "债券基金", "短债", "长债", "纯债", "债基",
                     "资金面", "货币宽松", "加息", "降息", "央行"]
    if any(kw in query for kw in bond_keywords):
        specialists.append("market_analyst")
        if "allocation_advisor" not in specialists:
            specialists.append("allocation_advisor")

    # 配置相关关键词 → 资产配置师
    allocation_keywords = ["配置", "配比", "定投", "股债", "组合"]
    if any(kw in query for kw in allocation_keywords):
        specialists.append("allocation_advisor")

    # 持仓相关关键词 → 风险评估师 + 资产配置师
    portfolio_keywords = ["持仓", "加仓", "减仓", "盈亏", "我的基金", "持有", "仓位"]
    if any(kw in query for kw in portfolio_keywords):
        if "risk_assessor" not in specialists:
            specialists.append("risk_assessor")
        if "allocation_advisor" not in specialists:
            specialists.append("allocation_advisor")

    # 高风险行动建议 → 反方观点审查员，必要时补风险评估和行为教练
    action_keywords = [
        "买入", "加仓", "建仓", "卖出", "减仓", "清仓", "追涨", "重仓",
        "满仓", "梭哈", "可以买吗", "要不要买", "要不要卖",
    ]
    if any(kw in query for kw in action_keywords):
        if "counter_argument" not in specialists:
            specialists.append("counter_argument")
        if "risk_assessor" not in specialists:
            specialists.append("risk_assessor")
    if any(kw in query for kw in ["追涨", "杀跌", "恐慌", "很慌", "重仓", "满仓", "梭哈", "冲动"]):
        if "behavior_coach" not in specialists:
            specialists.append("behavior_coach")

    # 基金分析关键词 → 基金分析师
    fund_analysis_keywords = ["操作记录", "交易记录", "基金分析", "基金表现", "复盘",
                               "收益怎么样", "赚了", "亏了", "买卖", "操作复盘",
                               "我的操作", "这只基金", "基金持仓"]
    if any(kw in query for kw in fund_analysis_keywords):
        if "fund_analyst" not in specialists:
            specialists.append("fund_analyst")

    # 默认返回估值专家
    if not specialists:
        specialists.append("valuation_expert")

    return specialists


