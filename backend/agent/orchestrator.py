"""Orchestrator — 主控 Agent，协调各专家 Agent 完成分析"""

import json
import logging
import re
import time

from llm_service import client, MODEL, _call_llm, _parse_tool_args
from agent.multi_agent import SPECIALIST_AGENTS, run_specialist

logger = logging.getLogger(__name__)


# ── 需求澄清 Agent（LLM 版）──────────────────────────────────

CLARIFICATION_PROMPT = """<role>你是一位需求分析专家，负责理解用户的投资问题，并决定如何最优地回答它。</role>

<task>
收到用户问题后，分析并返回以下 JSON 格式的结果：
```json
{
  "complexity": "simple|medium|complex",
  "specialists": ["valuation_expert", "market_analyst", "risk_assessor", "allocation_advisor"],
  "reason": "简要说明为什么这样分类",
  "refined_query": "优化后的问题（如果需要）"
}
```
</task>

<complexity_criteria>
### simple（简单）
- 单一数据查询：如"沪深300估值多少"、"债市温度"
- 直接查表类：如"PE是多少"、"百分位多少"
- 只需要1个专家即可回答

### medium（中等）
- 需要分析但范围明确：如"白酒估值高吗"、"最近有什么新闻"
- 需要1-2个专家协作
- 可能需要RAG知识库辅助

### complex（复杂）
- 投资决策类：如"白酒能买吗"、"该加仓还是减仓"
- 多维度分析：如"帮我做个定投方案"、"现在怎么配置"
- 需要3-4个专家协作
- 必须结合估值、新闻、风险等多方面信息
</complexity_criteria>

<specialist_guide>
- **估值相关**（PE/PB/百分位/高估低估）→ valuation_expert
- **新闻/政策/市场动态** → market_analyst
- **风险/回撤/波动率/持仓风险** → risk_assessor
- **配置/定投/股债配比/持仓配置** → allocation_advisor
- **持仓/加仓/减仓/盈亏/我的基金** → 需要结合持仓数据，选 risk_assessor 或 allocation_advisor
- **基金分析/操作复盘/交易记录/基金表现** → fund_analyst
- **债券相关**（债券基金/久期/收益率曲线/债市/利率）→ market_analyst 或 allocation_advisor，market_analyst 有 get_bond_yield_curve 和 get_bond_market_overview 工具
</specialist_guide>

<examples>
用户: 沪深300估值多少？
输出: {"complexity": "simple", "specialists": ["valuation_expert"], "reason": "单一估值查询", "refined_query": "沪深300当前估值"}

用户: 白酒能买吗？
输出: {"complexity": "complex", "specialists": ["valuation_expert", "risk_assessor", "allocation_advisor"], "reason": "投资决策需要估值+风险+配置多维度分析", "refined_query": "白酒当前估值水平、风险评估与配置建议"}

用户: 帮我做个定投方案
输出: {"complexity": "complex", "specialists": ["valuation_expert", "allocation_advisor"], "reason": "定投方案需要估值数据和配置策略", "refined_query": "基于当前估值的定投方案设计"}
</examples>

<output_rule>只输出 JSON，不要其他文字。</output_rule>"""


def clarify_requirement(query: str) -> dict:
    """
    使用 LLM 分析用户问题，返回需求澄清结果。

    返回:
        {
            "complexity": "simple|medium|complex",
            "specialists": ["valuation_expert", ...],
            "reason": "...",
            "refined_query": "..."
        }
    """
    try:
        response = _call_llm(
            caller="clarify",
            model=MODEL,
            messages=[
                {"role": "system", "content": CLARIFICATION_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip()

        # 提取 JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)

        # 验证并设置默认值
        complexity = result.get("complexity", "medium")
        if complexity not in ("simple", "medium", "complex"):
            complexity = "medium"

        specialists = result.get("specialists", [])
        valid_specialists = ["valuation_expert", "market_analyst", "risk_assessor", "allocation_advisor", "fund_analyst"]
        specialists = [s for s in specialists if s in valid_specialists]

        # 如果没有选择专家，默认选估值专家
        if not specialists:
            specialists = ["valuation_expert"]

        return {
            "complexity": complexity,
            "specialists": specialists,
            "reason": result.get("reason", ""),
            "refined_query": result.get("refined_query", query),
        }

    except Exception as e:
        logger.warning(f"需求澄清失败，回退到关键词匹配: {e}")
        # 回退到关键词匹配
        complexity = detect_complexity_by_keywords(query)
        specialists = route_to_specialists_by_keywords(query)
        return {
            "complexity": complexity,
            "specialists": specialists,
            "reason": "关键词匹配（LLM澄清失败）",
            "refined_query": query,
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

    # 复杂任务关键词（需要多专家协作）
    complex_keywords = [
        "加仓", "减仓", "买入", "卖出", "持有", "建仓", "清仓",
        "定投", "配置", "组合", "方案", "策略", "计划",
        "风险", "回撤", "波动",
        "对比", "比较", "哪个更好", "选哪个",
        "怎么样", "怎么看", "怎么看", "值得买", "能买吗",
        "现在", "当前", "适合", "应该",
        "持仓", "盈亏", "我的基金", "仓位",
    ]

    # 简单任务关键词（单一数据查询）
    simple_keywords = [
        "估值", "百分位", "PE", "PB", "z-score",
        "债市温度", "温度",
        "多少", "是什么", "查一下", "查询",
        "最新", "今天", "最近",
    ]

    # 检查是否是复杂任务
    complex_score = sum(1 for kw in complex_keywords if kw in query)

    # 检查是否是简单任务
    simple_score = sum(1 for kw in simple_keywords if kw in query)

    # 如果包含"吗"、"呢"等疑问词，倾向于中等或复杂
    has_question_mark = bool(re.search(r'[吗呢？?]', query))

    # 如果只是查询单一指标（很短的查询，且无疑问词），倾向于简单
    if len(query) <= 6 and simple_score > 0 and not has_question_mark and complex_score == 0:
        return "simple"

    # 有疑问词时，需要进一步分析
    if has_question_mark:
        # 包含复杂关键词 → complex
        if complex_score >= 1:
            return "complex"
        # 包含简单关键词但有疑问 → medium（如"估值高吗"）
        if simple_score >= 1:
            return "medium"
        # 其他有疑问的 → medium
        return "medium"

    # 无疑问词时
    if complex_score >= 2:
        return "complex"
    elif complex_score >= 1:
        return "medium"
    elif simple_score >= 1:
        return "medium"
    else:
        return "simple"


def route_to_specialists_by_keywords(query: str) -> list[str]:
    """根据关键词路由到合适的专家。返回 agent_key 列表。"""
    query = query.strip()
    specialists = []

    # 估值相关关键词 → 估值专家
    valuation_keywords = ["估值", "PE", "PB", "百分位", "z-score", "高估", "低估", "贵不贵", "便宜"]
    if any(kw in query for kw in valuation_keywords):
        specialists.append("valuation_expert")

    # 新闻/市场动态关键词 → 择时分析师
    news_keywords = ["新闻", "最新", "动态", "政策", "消息", "市场", "今天", "昨天"]
    if any(kw in query for kw in news_keywords):
        specialists.append("market_analyst")

    # 风险相关关键词 → 风险评估师
    risk_keywords = ["风险", "回撤", "波动", "最大回撤"]
    if any(kw in query for kw in risk_keywords):
        specialists.append("risk_assessor")

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


def get_context_config(complexity: str) -> dict:
    """根据复杂度返回上下文配置。"""
    if complexity == "simple":
        return {
            "history_limit": 3,      # 只保留最近3条历史
            "rag_enabled": False,    # 简单查询不需要RAG
            "max_specialists": 1,    # 只调用1个专家
            "rag_max_chars": 0,      # RAG上下文最大字符数
        }
    elif complexity == "medium":
        return {
            "history_limit": 5,
            "rag_enabled": True,
            "max_specialists": 2,
            "rag_max_chars": 1500,   # RAG上下文压缩到1500字符
        }
    else:  # complex
        return {
            "history_limit": 10,
            "rag_enabled": True,
            "max_specialists": 5,
            "rag_max_chars": 2500,   # RAG上下文压缩到2500字符
        }


def compress_history(history: list, max_messages: int = 10) -> list:
    """
    压缩对话历史：
    - 保留最近 max_messages 条完整消息
    - 更早的消息只保留摘要（第一条用户消息的前50字）
    """
    if len(history) <= max_messages:
        return history

    # 早期消息：只保留摘要
    early_messages = history[:-max_messages]
    recent_messages = history[-max_messages:]

    # 从早期消息中提取关键信息
    summary_parts = []
    for msg in early_messages:
        if msg["role"] == "user":
            # 用户消息：取前50字
            summary_parts.append(f"用户曾问: {msg['content'][:50]}...")
        elif msg["role"] == "assistant":
            # 助手消息：取前30字
            summary_parts.append(f"助手曾答: {msg['content'][:30]}...")

    # 构建摘要消息
    if summary_parts:
        summary = "以下是早期对话摘要（省略了详细内容）：\n" + "\n".join(summary_parts[-5:])  # 最多保留5条摘要
        compressed = [{"role": "system", "content": summary}] + recent_messages
    else:
        compressed = recent_messages

    return compressed


def compress_rag_context(rag_context: str, max_chars: int = 2000) -> str:
    """
    压缩 RAG 上下文：
    - 截断到 max_chars 字符
    - 保留完整段落，避免截断在句子中间
    """
    if not rag_context or len(rag_context) <= max_chars:
        return rag_context

    # 截断到最大字符数
    truncated = rag_context[:max_chars]

    # 找到最后一个完整段落（双换行符）
    last_paragraph_end = truncated.rfind("\n\n")
    if last_paragraph_end > max_chars * 0.7:  # 如果截断点在70%以后
        truncated = truncated[:last_paragraph_end]

    return truncated + "\n...(已截断，更多内容请参考知识库)"

# ── Orchestrator 的工具 = 调用各个专家 Agent ──────────────

ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "consult_valuation_expert",
            "description": "咨询估值专家，获取指数/基金的估值分析（PE/PB/百分位/z-score/估值趋势）。适用于：估值高低判断、是否值得投资、估值对比等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "具体问题，如'白酒指数当前估值如何'、'沪深300和中证500哪个估值更低'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consult_market_analyst",
            "description": "咨询择时分析师，获取市场新闻、政策解读、入场/出场信号。适用于：最新市场动态、政策影响、市场情绪判断等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "具体问题，如'最近有什么利好政策'、'白酒板块最近有什么新闻'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consult_risk_assessor",
            "description": "咨询风险评估师，获取风险等级、最大回撤、仓位建议。适用于：风险评估、回撤计算、仓位控制等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "具体问题，如'白酒指数风险大吗'、'沪深300最大回撤多少'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consult_allocation_advisor",
            "description": "咨询资产配置师，获取股债配比、定投策略、行业配置建议。适用于：资产配置方案、定投计划、再平衡建议等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "具体问题，如'现在股债怎么配'、'帮我做个定投方案'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consult_fund_analyst",
            "description": "咨询基金分析师，获取单只基金的收益表现、操作复盘、持仓结构分析。适用于：查某只基金赚了还是亏了、操作记录分析、基金持仓分析等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "具体问题，如'基金161725收益怎么样'、'帮我复盘白酒基金的操作记录'、'分析我的中证500持仓'",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# 专家名称到 agent_key 的映射
_EXPERT_MAP = {
    "consult_valuation_expert": "valuation_expert",
    "consult_market_analyst": "market_analyst",
    "consult_risk_assessor": "risk_assessor",
    "consult_allocation_advisor": "allocation_advisor",
    "consult_fund_analyst": "fund_analyst",
}

ORCHESTRATOR_SYSTEM_PROMPT = """你是投资分析助手的主控（Orchestrator），负责协调各领域专家 Agent 完成投资分析。

## 工作方式
1. 理解用户问题的核心意图
2. 决定需要咨询哪些专家（可同时咨询多个）
3. 收集各专家的分析结果
4. 综合各专家意见，给出最终的投资建议

## 专家团队
- 📊 **估值专家**：分析指数估值水平（PE/PB/百分位），判断高估/低估
- 📰 **择时分析师**：分析市场新闻、政策变化，判断市场时机
- 🛡️ **风险评估师**：评估投资风险，计算回撤/波动率，给出风控建议
- 🥧 **资产配置师**：给出股债配比、定投策略、行业配置建议
- 🔍 **基金分析师**：分析具体基金的投资表现、操作复盘、持仓结构

## 调用策略
- **简单估值问题**（如"沪深300估值多少"）→ 只调估值专家
- **市场动态问题**（如"最近有什么新闻"）→ 只调择时分析师
- **投资决策问题**（如"白酒能买吗"）→ 调估值专家 + 风险评估师
- **买卖建议问题**（如"该加仓还是减仓"）→ 调估值专家 + 风险评估师 + 择时分析师
- **配置方案问题**（如"帮我做个定投方案"）→ 调资产配置师 + 估值专家
- **基金分析问题**（如"我的白酒基金收益怎么样"）→ 调基金分析师 + 估值专家
- **操作复盘问题**（如"帮我复盘基金操作"）→ 调基金分析师 + 风险评估师
- **综合性问题**（如"白酒现在怎么操作"）→ 全部 5 个专家

## 回答原则
- 综合各专家意见，给出明确的判断和建议
- 如果专家意见有分歧，指出分歧点并给出你的倾向
- 引用专家的具体数据和分析
- 给出 actionable 的投资建议
- 使用 Markdown 格式，层次清晰"""


def _execute_specialist(tool_name: str, query: str) -> str:
    """执行专家 Agent 调用，返回 JSON 字符串结果。"""
    agent_key = _EXPERT_MAP.get(tool_name)
    if not agent_key:
        return json.dumps({"error": f"未知专家: {tool_name}"}, ensure_ascii=False)

    try:
        result = run_specialist(agent_key, query)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error(f"专家 {tool_name} 执行异常: {e}")
        return json.dumps({"error": f"专家执行失败: {e}"}, ensure_ascii=False)


def orchestrate(query: str, history: list, rag_context: str = "") -> dict:
    """
    Orchestrator 主循环。

    流程：
    1. 检测任务复杂度
    2. 根据复杂度优化上下文
    3. LLM 分析用户意图
    4. 决定调用哪些专家
    5. 执行专家 Agent（每个专家独立完成工具调用）
    6. 将专家结果反馈给 Orchestrator
    7. Orchestrator 综合给出最终建议

    返回:
        {
            "answer": "最终综合建议",
            "specialist_results": [
                {"agent": "估值专家", "icon": "📊", "analysis": "..."},
                ...
            ],
            "tool_calls": [...],
            "turns": 实际轮次数,
            "complexity": "simple/medium/complex"
        }
    """
    start_time = time.time()

    # 1. 需求澄清（使用 LLM 分析问题）
    clarification = clarify_requirement(query)
    complexity = clarification["complexity"]
    context_config = get_context_config(complexity)
    logger.info(f"需求澄清: {clarification}")

    # 使用澄清后的问题（如果有优化）
    refined_query = clarification.get("refined_query", query)

    # 2. 根据复杂度优化上下文（Token 优化）
    system_content = ORCHESTRATOR_SYSTEM_PROMPT

    # 只有中等和复杂任务才添加 RAG 上下文，并压缩
    if rag_context and context_config["rag_enabled"]:
        compressed_rag = compress_rag_context(rag_context, context_config["rag_max_chars"])
        system_content += f"\n\n参考信息：\n{compressed_rag}"

    llm_messages = [{"role": "system", "content": system_content}]

    # 压缩历史消息（早期消息摘要化）
    history_limit = context_config["history_limit"]
    compressed_history = compress_history(history, history_limit)
    for msg in compressed_history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    # 当前用户问题（使用优化后的问题）
    llm_messages.append({"role": "user", "content": refined_query})

    MAX_TURNS = 6
    specialist_results = []
    all_tool_calls = []

    for turn in range(MAX_TURNS):
        try:
            response = _call_llm(
                caller="orchestrator",
                model=MODEL,
                messages=llm_messages,
                tools=ORCHESTRATOR_TOOLS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Orchestrator LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning("模型不兼容，回退到普通模式")
                return _fallback_orchestrate(query, history, rag_context)
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 最终回答
        if not msg.tool_calls:
            answer = msg.content or ""
            duration_ms = int((time.time() - start_time) * 1000)
            return {
                "answer": answer,
                "specialist_results": specialist_results,
                "tool_calls": all_tool_calls,
                "turns": turn + 1,
                "duration_ms": duration_ms,
                "complexity": complexity,
            }

        # 有工具调用 → 执行专家
        assistant_msg = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        }

        # MIMO thinking mode: 传递 reasoning_content
        reasoning = None
        if hasattr(msg, "model_extra") and msg.model_extra:
            reasoning = msg.model_extra.get("reasoning_content")
        if not reasoning:
            reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning

        llm_messages.append(assistant_msg)

        for tc in msg.tool_calls:
            args = _parse_tool_args(tc.function.arguments, tc.function.name)

            expert_query = args.get("query", query)
            logger.info(f"Orchestrator → {tc.function.name}: {expert_query[:100]}")

            # 执行专家 Agent
            result_str = _execute_specialist(tc.function.name, expert_query)

            try:
                result_data = json.loads(result_str)
            except json.JSONDecodeError:
                result_data = {"raw": result_str}

            # 记录专家结果
            if "error" not in result_data:
                specialist_results.append({
                    "agent_key": result_data.get("agent_key", _EXPERT_MAP.get(tc.function.name, "")),
                    "agent": result_data.get("agent", tc.function.name),
                    "icon": result_data.get("icon", "🤖"),
                    "analysis": result_data.get("analysis", ""),
                    "tool_calls": result_data.get("tool_calls", []),
                    "duration_ms": result_data.get("duration_ms", 0),
                })

            all_tool_calls.append({
                "name": tc.function.name,
                "arguments": args,
                "result_preview": result_str[:300],
            })

            # 将专家结果反馈给 Orchestrator
            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str[:4000],
            })

    # 超过最大轮次，做最后一次总结
    try:
        llm_messages.append({
            "role": "user",
            "content": "请根据以上各专家的分析结果，给出最终的综合投资建议。",
        })
        response = _call_llm(
            caller="orchestrator",
            model=MODEL,
            messages=llm_messages,
            temperature=0.3,
            max_tokens=2000,
        )
        final_answer = response.choices[0].message.content or ""
    except Exception:
        final_answer = "分析过程较长，请参考以上各专家的分析结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    return {
        "answer": final_answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "turns": MAX_TURNS,
        "duration_ms": duration_ms,
        "complexity": complexity,
    }


def orchestrate_stream(query: str, history: list, rag_context: str = ""):
    """
    Orchestrator 的流式版本，通过生成器逐步返回事件。

    事件类型：
    - {"type": "specialist_start", "agent_key": "...", "agent": "...", "icon": "..."}
    - {"type": "specialist_done", "agent_key": "...", "agent": "...", "icon": "...", "analysis": "...", "duration_ms": ...}
    - {"type": "status", "message": "..."}
    - {"type": "answer_chunk", "content": "..."}
    - {"type": "answer", "content": "...", "specialist_results": [...], "tool_calls": [...], "complexity": "..."}
    """
    start_time = time.time()

    # 1. 需求澄清（使用 LLM 分析问题）
    yield {"type": "status", "message": "正在理解您的问题..."}
    clarification = clarify_requirement(query)
    complexity = clarification["complexity"]
    context_config = get_context_config(complexity)
    logger.info(f"需求澄清: {clarification}")

    # 使用澄清后的问题（如果有优化）
    refined_query = clarification.get("refined_query", query)

    # 2. 根据复杂度优化上下文（Token 优化）
    system_content = ORCHESTRATOR_SYSTEM_PROMPT

    # 只有中等和复杂任务才添加 RAG 上下文，并压缩
    if rag_context and context_config["rag_enabled"]:
        compressed_rag = compress_rag_context(rag_context, context_config["rag_max_chars"])
        system_content += f"\n\n参考信息：\n{compressed_rag}"

    llm_messages = [{"role": "system", "content": system_content}]

    # 压缩历史消息（早期消息摘要化）
    history_limit = context_config["history_limit"]
    compressed_history = compress_history(history, history_limit)
    for msg in compressed_history:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    # 使用优化后的问题
    llm_messages.append({"role": "user", "content": refined_query})

    # 根据复杂度显示不同的状态消息
    if complexity == "simple":
        yield {"type": "status", "message": f"正在分析问题... ({clarification.get('reason', '')})"}
    elif complexity == "medium":
        yield {"type": "status", "message": f"正在咨询专家... ({clarification.get('reason', '')})"}
    else:
        yield {"type": "status", "message": f"正在协调多个专家... ({clarification.get('reason', '')})"}

    MAX_TURNS = 6
    specialist_results = []
    all_tool_calls = []

    for turn in range(MAX_TURNS):
        try:
            response = _call_llm(
                caller="orchestrator",
                model=MODEL,
                messages=llm_messages,
                tools=ORCHESTRATOR_TOOLS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Orchestrator LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning("模型不兼容，回退到普通模式")
                yield {"type": "status", "message": "模型不支持工具调用，切换到普通模式..."}
                result = _fallback_orchestrate(query, history, rag_context)
                yield {
                    "type": "answer",
                    "content": result["answer"],
                    "specialist_results": [],
                    "tool_calls": [],
                }
                return
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 最终回答
        if not msg.tool_calls:
            answer = msg.content or ""
            duration_ms = int((time.time() - start_time) * 1000)
            yield {
                "type": "answer",
                "content": answer,
                "specialist_results": specialist_results,
                "tool_calls": all_tool_calls,
                "duration_ms": duration_ms,
            }
            return

        # 有工具调用 → 执行专家
        assistant_msg = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        }

        # MIMO thinking mode
        reasoning = None
        if hasattr(msg, "model_extra") and msg.model_extra:
            reasoning = msg.model_extra.get("reasoning_content")
        if not reasoning:
            reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning

        llm_messages.append(assistant_msg)

        for tc in msg.tool_calls:
            args = _parse_tool_args(tc.function.arguments, tc.function.name)

            expert_query = args.get("query", query)
            agent_key = _EXPERT_MAP.get(tc.function.name, "")
            agent_info = SPECIALIST_AGENTS.get(agent_key, {})

            logger.info(f"Orchestrator → {tc.function.name}: {expert_query[:100]}")

            # 通知前端：专家开始工作
            yield {
                "type": "specialist_start",
                "agent_key": agent_key,
                "agent": agent_info.get("name", tc.function.name),
                "icon": agent_info.get("icon", "🤖"),
            }

            # 执行专家 Agent
            result_str = _execute_specialist(tc.function.name, expert_query)

            try:
                result_data = json.loads(result_str)
            except json.JSONDecodeError:
                result_data = {"raw": result_str}

            # 记录专家结果
            if "error" not in result_data:
                specialist_result = {
                    "agent_key": result_data.get("agent_key", agent_key),
                    "agent": result_data.get("agent", agent_info.get("name", "")),
                    "icon": result_data.get("icon", agent_info.get("icon", "🤖")),
                    "analysis": result_data.get("analysis", ""),
                    "tool_calls": result_data.get("tool_calls", []),
                    "duration_ms": result_data.get("duration_ms", 0),
                }
                specialist_results.append(specialist_result)

                # 通知前端：专家分析完成
                yield {
                    "type": "specialist_done",
                    "agent_key": specialist_result["agent_key"],
                    "agent": specialist_result["agent"],
                    "icon": specialist_result["icon"],
                    "analysis": specialist_result["analysis"],
                    "duration_ms": specialist_result["duration_ms"],
                }

            all_tool_calls.append({
                "name": tc.function.name,
                "arguments": args,
                "result_preview": result_str[:300],
            })

            # 将专家结果反馈给 Orchestrator
            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str[:4000],
            })

        yield {"type": "status", "message": "正在综合各专家意见..."}

    # 超过最大轮次，做最后一次总结
    try:
        llm_messages.append({
            "role": "user",
            "content": "请根据以上各专家的分析结果，给出最终的综合投资建议。",
        })
        response = _call_llm(
            caller="orchestrator",
            model=MODEL,
            messages=llm_messages,
            temperature=0.3,
            max_tokens=2000,
        )
        final_answer = response.choices[0].message.content or ""
    except Exception:
        final_answer = "分析过程较长，请参考以上各专家的分析结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    yield {
        "type": "answer",
        "content": final_answer,
        "specialist_results": specialist_results,
        "tool_calls": all_tool_calls,
        "duration_ms": duration_ms,
        "complexity": complexity,
    }


def _fallback_orchestrate(query: str, history: list, rag_context: str = "") -> dict:
    """当模型不支持 function calling 时，回退到普通对话模式。"""
    from llm_service import chat_with_agent

    answer = chat_with_agent(ORCHESTRATOR_SYSTEM_PROMPT, history + [{"role": "user", "content": query}], rag_context)
    return {
        "answer": answer,
        "specialist_results": [],
        "tool_calls": [],
        "turns": 1,
        "fallback": True,
    }
