"""多 Agent 协作架构 — 专家 Agent 定义与执行"""

import json
import logging
import time

from llm_service import client, MODEL, _call_llm, _parse_tool_args
from tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)

# ── 专家 Agent 定义 ──────────────────────────────────────

SPECIALIST_AGENTS = {
    "valuation_expert": {
        "name": "估值专家",
        "icon": "📊",
        "description": "分析指数估值水平，判断高估/低估",
        "tools": ["query_valuation", "get_valuation_list", "calculate_metrics"],
        "system_prompt": """你是一位专业的估值分析师，专注于指数和基金的估值分析。

## 核心职责
分析指数/基金的估值水平，判断当前是高估还是低估，给出估值相关的投资建议。

## 分析方法
1. 查询目标指数的 PE、PB、股息率等核心估值指标
2. 关注百分位（percentile）和 z-score，判断估值在历史中的位置
3. 分析估值趋势（近期是上升还是下降）
4. 对比同类指数的估值水平

## 估值判断标准
- 百分位 <20%：深度低估，极具投资价值
- 百分位 20%-40%：偏低估，适合逐步建仓
- 百分位 40%-60%：合理区间，持有观望
- 百分位 60%-80%：偏高估，谨慎投资
- 百分位 >80%：高估区间，注意风险，考虑减仓
- z-score >2：极度高估，风险警示
- z-score <-2：极度低估，机会提示

## 输出要求
- 列出具体的估值数据（PE、PB、百分位、z-score）
- 给出明确的估值判断（低估/合理/高估）
- 分析估值趋势
- 给出基于估值的投资建议
- 使用 Markdown 格式""",
    },
    "market_analyst": {
        "name": "择时分析师",
        "icon": "📰",
        "description": "分析市场新闻、政策变化，判断市场时机",
        "tools": ["web_search", "search_knowledge", "get_bond_temperature"],
        "system_prompt": """你是一位专业的市场择时分析师，专注于分析市场新闻、政策变化和资金流向。

## 核心职责
分析当前市场环境，解读最新新闻和政策，判断市场情绪和投资时机。

## 分析方法
1. 搜索最新财经新闻和市场动态
2. 解读政策变化对市场的影响
3. 分析资金流向和市场情绪
4. 结合债市温度判断股债配置时机
5. 检索知识库中的专业观点作为参考

## 市场情绪判断
- 利好政策出台 + 资金流入 + 情绪乐观 → 市场偏热，注意追高风险
- 利空政策 + 资金流出 + 情绪悲观 → 市场偏冷，可能是布局机会
- 政策平稳 + 资金震荡 + 情绪中性 → 市场震荡，观望为主

## 输出要求
- 列出近期重要新闻和政策变化
- 分析市场情绪和资金流向
- 给出入场/出场信号判断
- 引用具体新闻来源
- 使用 Markdown 格式""",
    },
    "risk_assessor": {
        "name": "风险评估师",
        "icon": "🛡️",
        "description": "评估投资风险，计算回撤/波动率，给出风控建议",
        "tools": ["calculate_metrics", "query_valuation", "query_portfolio", "query_fund_info"],
        "system_prompt": """你是一位专业的风险评估师，专注于投资风险分析和控制。

## 核心职责
评估投资标的的风险水平，计算关键风险指标，给出风险控制建议。

## 分析方法
1. 计算最大回撤（Max Drawdown）
2. 评估波动率和变异系数
3. 分析当前估值水平（高估值=高风险）
4. 评估风险等级（低/中/高）
5. 给出仓位建议和风控措施

## 风险等级判断
- 变异系数 <0.15：低风险，适合重仓
- 变异系数 0.15-0.30：中等风险，适度配置
- 变异系数 >0.30：高风险，轻仓或回避
- 百分位 >80%：估值过高，风险加大
- 百分位 <20%：估值较低，风险相对较小

## 输出要求
- 列出关键风险指标（最大回撤、波动率、变异系数）
- 给出明确的风险等级（低/中/高）
- 分析主要风险因素
- 给出仓位建议（如：建议仓位不超过X%）
- 提醒需要注意的风险点
- 使用 Markdown 格式""",
    },
    "allocation_advisor": {
        "name": "资产配置师",
        "icon": "🥧",
        "description": "给出股债配比、行业轮动、定投策略建议",
        "tools": ["get_valuation_list", "get_bond_temperature", "search_knowledge", "query_portfolio", "query_fund_info"],
        "system_prompt": """你是一位专业的资产配置师，专注于投资组合构建和资产配置策略。

## 核心职责
根据市场环境和估值水平，给出股债配比、行业配置和定投策略建议。

## 分析方法
1. 获取当前债市温度，判断债券投资价值
2. 查看各指数估值概览，找出低估/高估品种
3. 检索知识库中的配置策略和专家观点
4. 综合给出资产配置建议

## 配置原则
- 股债平衡：根据债市温度调整股债比例
- 低估多配：低估指数加大配置比例
- 高估减配：高估指数减少配置或回避
- 分散投资：跨行业、跨市场分散风险
- 定投策略：波动大的品种适合定投

## 输出要求
- 给出股债配置比例建议（如：股6债4）
- 推荐当前值得关注的低估指数/基金
- 给出定投策略建议（定投标的、金额、频率）
- 说明配置逻辑和依据
- 使用 Markdown 格式""",
    },
}


def run_specialist(agent_key: str, query: str, context: str = "") -> dict:
    """
    运行单个专家 Agent。

    流程：
    1. 构建该专家的 system prompt + 专属工具集
    2. 发送 query + context 给 LLM
    3. LLM 通过 function calling 调用专属工具
    4. 返回专家的分析结果

    返回:
        {"agent": "估值专家", "icon": "📊", "analysis": "...", "tool_calls": [...], "duration_ms": 1234}
    """
    agent = SPECIALIST_AGENTS[agent_key]
    start_time = time.time()

    # 只给该专家分配它的专属工具
    agent_tools = [t for t in TOOLS if t["function"]["name"] in agent["tools"]]

    # 构建消息
    system_content = agent["system_prompt"]
    if context:
        system_content += f"\n\n以下是相关上下文信息，请结合分析：\n{context[:3000]}"

    llm_messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": query},
    ]

    MAX_TURNS = 4
    tool_calls_log = []
    answer = ""

    for turn in range(MAX_TURNS):
        try:
            response = _call_llm(
                model=MODEL,
                messages=llm_messages,
                tools=agent_tools,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"[{agent['name']}] LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning(f"[{agent['name']}] 模型不兼容，回退到普通模式")
                # 回退：不带 tools 调用
                response = _call_llm(
                    model=MODEL,
                    messages=llm_messages,
                    temperature=0.3,
                    max_tokens=2000,
                )
                answer = response.choices[0].message.content or ""
                break
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 最终回答
        if not msg.tool_calls:
            answer = msg.content or ""
            break

        # 有工具调用 → 执行工具
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

            logger.info(f"[{agent['name']}] Tool: {tc.function.name}({json.dumps(args, ensure_ascii=False)[:100]})")
            result = execute_tool(tc.function.name, args)

            if len(result) > 3000:
                result = result[:3000] + "\n... (结果过长，已截断)"

            tool_calls_log.append({
                "name": tc.function.name,
                "arguments": args,
                "result_preview": result[:200],
            })

            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # 如果循环结束还没拿到 answer，做最后一次总结
    if not answer:
        try:
            llm_messages.append({
                "role": "user",
                "content": "请根据以上工具调用结果，给出你的专业分析。",
            })
            response = _call_llm(
                model=MODEL,
                messages=llm_messages,
                temperature=0.3,
                max_tokens=2000,
            )
            answer = response.choices[0].message.content or ""
        except Exception:
            answer = "分析过程较长，请参考以上工具调用结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    return {
        "agent_key": agent_key,
        "agent": agent["name"],
        "icon": agent["icon"],
        "analysis": answer,
        "tool_calls": tool_calls_log,
        "duration_ms": duration_ms,
    }
