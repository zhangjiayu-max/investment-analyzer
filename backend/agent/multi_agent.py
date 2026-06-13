"""多 Agent 协作架构 — 专家 Agent 执行引擎"""

import json
import logging
import re
import time

from llm_service import client, MODEL, _call_llm, _parse_tool_args
from tools import TOOLS, execute_tool
from db.agents import load_specialist_agents

logger = logging.getLogger(__name__)

# ── 通用数据约束（追加到所有 Agent prompt 末尾） ──
_UNIVERSAL_DATA_CONSTRAINT = """
## ⚠️ 数据真实性约束（强制遵守）
1. **只使用上下文中明确提供的数据**，包括持仓、估值、新闻等
2. **绝对禁止编造**任何基金名称、基金代码、金额、收益率、持仓比例等数据
3. 如果上下文中没有持仓数据或显示"无持仓"，**必须明确告知用户**"未获取到持仓信息"，不得自行补充
4. 如果某个数据在上下文中缺失，直接说明"暂无该数据"，不要猜测或杜撰
5. 所有数字必须来自上下文或工具返回结果，不得凭记忆或推测给出具体数值
"""

# ── 专家主动检索指令（追加到有 search_knowledge 工具的 Agent） ──
_ACTIVE_RETRIEVAL_INSTRUCTION = """
## 📚 书籍知识主动检索指令
你拥有 search_knowledge 工具，可以检索投资经典书籍的知识库。在以下场景**必须主动调用**：

1. **提出估值判断时** → 检索 query="估值 安全边际 PE PB 百分位"，content_types=["book"]
2. **建议买卖操作时** → 检索 query="择时 仓位 策略 买入卖出"，content_types=["book"]
3. **分析市场情绪时** → 检索 query="心理 偏差 情绪 损失厌恶"，content_types=["book"]
4. **评估企业质量时** → 检索 query="护城河 ROE 竞争优势 现金流"，content_types=["book"]
5. **讨论资产配置时** → 检索 query="配置 分散 再平衡 股债比例"，content_types=["book"]
6. **分析周期位置时** → 检索 query="周期 牛熊 转折 估值温度"，content_types=["book"]

引用书籍观点时请注明来源（如"根据《聪明的投资者》..."），增强分析的可信度。
"""

# ── 风险与深度分析约束（追加到所有专家 prompt） ──
_RISK_AND_DEPTH_CONSTRAINT = """
## 🎯 分析质量要求（必须遵守）

### 1. 风险分析必须到位
- **不要只看估值**，必须同时分析风险因素
- 考虑：近期波动、政策风险、市场情绪、流动性风险
- 如果用户问"可以买吗"，必须同时说明风险

### 2. 分析要透彻
- 如果用户问基金，要穿透查看重仓股、历史业绩、基金经理
- 不要泛泛而谈，要有具体数据支撑
- 对比类问题要列出具体差异点

### 3. 回答要直接相关
- 直接回答用户问题，不要绕圈子
- 不要重复用户已经知道的信息
- 如果用户问"A和B区别"，直接说区别，不要从头分析两个

### 4. 数据要准确
- 使用工具获取最新数据，不要凭记忆
- 如果数据获取失败，明确告知用户
- 不要编造数据或使用过时数据
"""


def _extract_text_tool_calls(content_str):
    """从 LLM 文本中提取 XML 格式的 tool_call。"""
    if not content_str or '<tool_call>' not in content_str:
        return None
    import re as _re
    # Build pattern with chr() to avoid XML interpretation
    tc_open = chr(60) + 'tool' + '_call' + chr(62)
    tc_close = chr(60) + '/tool' + '_call' + chr(62)
    fn_open = chr(60) + 'function='
    fn_close = chr(60) + '/function' + chr(62)
    param_open = chr(60) + 'parameter='
    param_close = chr(60) + '/parameter' + chr(62)
    
    pattern = tc_open + r'\s*' + fn_open + r'(\w+)>' + r'(.*?)' + fn_close + r'\s*' + tc_close
    matches = _re.findall(pattern, content_str, _re.DOTALL)
    if not matches:
        return None
    results = []
    for func_name, params_block in matches:
        pp = param_open + r'(\w+)>' + r'(.*?)' + param_close
        param_matches = _re.findall(pp, params_block, _re.DOTALL)
        args = {pname: pval.strip() for pname, pval in param_matches}
        results.append({'name': func_name, 'arguments': args})
    return results if results else None


def _process_text_tool_calls(content_str, llm_messages, tool_calls_log, agent_name):
    """处理文本格式的 tool_call：解析、执行、将结果追加到消息列表。"""
    text_calls = _extract_text_tool_calls(content_str)
    if not text_calls:
        return False
    logger.info(f'[{agent_name}] 检测到文本格式 tool_call: {[tc["name"] for tc in text_calls]}')
    for tc in text_calls:
        args = tc['arguments']
        logger.info(f'[{agent_name}] 文本 Tool: {tc["name"]}({json.dumps(args, ensure_ascii=False)[:100]})')
        result = execute_tool(tc['name'], args)
        if len(result) > 3000:
            result = result[:3000] + chr(10) + '... (结果过长，已截断)'
        tool_calls_log.append({
            'name': tc['name'],
            'arguments': args,
            'result_preview': result[:200] if result.strip() else '（无数据返回）',
        })
        llm_messages.append({
            'role': 'user',
            'content': f'工具 {tc["name"]} 的执行结果：' + chr(10) + result,
        })
    return True


# ── 专家 Agent 加载 ──────────────────────────────────────

# SPECIALIST_AGENTS 从数据库加载（db.agents.load_specialist_agents），不再硬编码。
# 通过 Agent 管理页面修改 prompt 后，编排器自动使用新版本。

def run_specialist(agent_key: str, query: str, context: str = "",
                   prebuilt_context: str = "") -> dict:
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
    agent = load_specialist_agents()[agent_key]
    start_time = time.time()
    _caller = f"specialist:{agent_key}"

    # 只给该专家分配它的专属工具
    agent_tools = [t for t in TOOLS if t["function"]["name"] in agent["tools"]]

    # 构建消息
    system_content = agent["system_prompt"]
    if context:
        system_content += f"\n\n以下是相关上下文信息，请结合分析：\n{context[:3000]}"

    # 注入持仓上下文（优先使用预构建的，避免重复 DB 查询）
    if prebuilt_context:
        system_content += f"\n\n{prebuilt_context}"
    else:
        try:
            from portfolio_context import build_portfolio_context
            portfolio_ctx = build_portfolio_context()
            system_content += f"\n\n## 用户当前持仓\n{portfolio_ctx}"
        except Exception:
            pass

    # 追加主动检索指令（仅当专家有 search_knowledge 工具时）
    if "search_knowledge" in agent.get("tools", []):
        system_content += _ACTIVE_RETRIEVAL_INSTRUCTION

    # 追加通用数据约束
    system_content += _UNIVERSAL_DATA_CONSTRAINT

    # 追加风险与深度分析约束
    system_content += _RISK_AND_DEPTH_CONSTRAINT

    llm_messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": query},
    ]

    MAX_TURNS = 3
    tool_calls_log = []
    answer = ""

    for turn in range(MAX_TURNS):
        try:
            response = _call_llm(
                caller=_caller,
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
                    caller=_caller,
                    model=MODEL,
                    messages=llm_messages,
                    temperature=0.3,
                    max_tokens=2000,
                )
                answer = response.choices[0].message.content or ""
                break
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 检查文本格式 tool_call，否则为最终回答
        if not msg.tool_calls:
            if _process_text_tool_calls(msg.content or "", llm_messages, tool_calls_log, agent["name"]):
                continue
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
                "result_preview": result[:200] if result.strip() else "（无数据返回）",
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
                caller=_caller,
                model=MODEL,
                messages=llm_messages,
                temperature=0.3,
                max_tokens=2000,
            )
            answer = response.choices[0].message.content or ""
        except Exception:
            answer = "分析过程较长，请参考以上工具调用结果。"

    duration_ms = int((time.time() - start_time) * 1000)

    # 清理：如果 answer 中仍包含文本格式 tool_call，去除之
    if answer and '<tool_call>' in answer:
        answer = re.sub(r'<tool_call>.*?</tool_call>', '', answer, flags=re.DOTALL).strip()
        if not answer:
            answer = "分析完成，请参考以上工具调用结果。"

    return {
        "agent_key": agent_key,
        "agent": agent["name"],
        "icon": agent["icon"],
        "analysis": answer,
        "tool_calls": tool_calls_log,
        "duration_ms": duration_ms,
    }


def run_specialist_with_context(agent_key: str, query: str, peer_analyses: dict,
                                max_turns: int = 2, prebuilt_context: str = "") -> dict:
    """
    交叉审阅模式：专家拿到其他专家的分析结果后进行二次审阅。

    与 run_specialist() 的区别：
    - system_prompt 末尾追加 <round_table> 段，注入其他专家的分析
    - max_turns 默认为 2（控制成本）
    - 要求专家指出认同/质疑/补充

    参数:
        agent_key: 专家 key
        query: 原始用户问题
        peer_analyses: {agent_key: analysis_text} 其他专家的 Phase A 结果
        max_turns: 最大工具调用轮次
        prebuilt_context: 预构建的持仓+估值上下文，避免重复 DB 查询
    """
    agent = load_specialist_agents()[agent_key]
    start_time = time.time()
    _caller = f"specialist:{agent_key}:cross_review"

    agent_tools = [t for t in TOOLS if t["function"]["name"] in agent["tools"]]

    # 构建交叉审阅的 system prompt
    system_content = agent["system_prompt"]

    # 注入持仓上下文（优先使用预构建的）
    if prebuilt_context:
        system_content += f"\n\n{prebuilt_context}"
    else:
        try:
            from portfolio_context import build_portfolio_context
            portfolio_ctx = build_portfolio_context()
            if portfolio_ctx:
                system_content += f"\n\n## 用户当前持仓（分析时务必结合）\n{portfolio_ctx}"
        except Exception:
            pass

    # 追加圆桌审阅指令
    peer_sections = []
    for peer_key, peer_analysis in peer_analyses.items():
        if peer_key == agent_key:
            continue
        peer_agent = load_specialist_agents().get(peer_key)
        peer_name = peer_agent["name"] if peer_agent else peer_key
        peer_sections.append(f"【{peer_name}】的分析：\n{peer_analysis[:2000]}")

    if peer_sections:
        round_table = (
            "\n\n<round_table>\n"
            "以下是其他专家的分析结果，请结合你的专业视角进行交叉审阅：\n\n"
            + "\n\n---\n\n".join(peer_sections)
            + "\n\n请在你的分析中：\n"
            "1. 指出你认同的其他专家观点（引用具体内容）\n"
            "2. 指出你认为有疑问或需要补充的地方（用数据或逻辑反驳）\n"
            "3. 从你的专业角度提供其他专家未覆盖的独特见解\n"
            "4. 如果你改变了之前的判断，说明原因\n"
            "</round_table>"
        )
        system_content += round_table

    llm_messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"请基于其他专家的分析结果，从你的专业角度进行交叉审阅和补充。原始问题：{query}"},
    ]

    tool_calls_log = []
    answer = ""

    for turn in range(max_turns):
        try:
            response = _call_llm(
                caller=_caller,
                model=MODEL,
                messages=llm_messages,
                tools=agent_tools,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"[{agent['name']}] 交叉审阅 LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                response = _call_llm(
                    caller=_caller,
                    model=MODEL,
                    messages=llm_messages,
                    temperature=0.3,
                    max_tokens=2000,
                )
                answer = response.choices[0].message.content or ""
                break
            raise

        msg = response.choices[0].message

        if not msg.tool_calls:
            if _process_text_tool_calls(msg.content or "", llm_messages, tool_calls_log, agent["name"]):
                continue
            answer = msg.content or ""
            break

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
            logger.info(f"[{agent['name']}] 交叉审阅 Tool: {tc.function.name}({json.dumps(args, ensure_ascii=False)[:100]})")
            result = execute_tool(tc.function.name, args)
            if len(result) > 3000:
                result = result[:3000] + "\n... (结果过长，已截断)"
            tool_calls_log.append({
                "name": tc.function.name,
                "arguments": args,
                "result_preview": result[:200] if result.strip() else "（无数据返回）",
            })
            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    if not answer:
        try:
            llm_messages.append({
                "role": "user",
                "content": "请根据以上信息，给出你的交叉审阅结论。",
            })
            response = _call_llm(
                caller=_caller,
                model=MODEL,
                messages=llm_messages,
                temperature=0.3,
                max_tokens=2000,
            )
            answer = response.choices[0].message.content or ""
        except Exception:
            answer = "交叉审阅完成，请参考以上分析。"

    duration_ms = int((time.time() - start_time) * 1000)

    # 清理：如果 answer 中仍包含文本格式 tool_call，去除之
    if answer and '<tool_call>' in answer:
        answer = re.sub(r'<tool_call>.*?</tool_call>', '', answer, flags=re.DOTALL).strip()
        if not answer:
            answer = "交叉审阅完成。"

    return {
        "agent_key": agent_key,
        "agent": agent["name"],
        "icon": agent["icon"],
        "analysis": answer,
        "tool_calls": tool_calls_log,
        "duration_ms": duration_ms,
        "is_cross_review": True,
    }


# ── 仲裁 Agent（高级推理模型）──────────────────────────────────

ARBITRATION_SYSTEM_PROMPT = """你是投资仲裁法官（Arbitration Agent），负责综合多位投资专家的分析结果，做出最终裁决。

## 职责
1. **审查分歧**：指出各专家之间的核心分歧点，评估谁的论据更有数据支撑
2. **数据验证**：检查专家引用的数据是否一致，指出可能的数据错误
3. **逻辑裁判**：当专家意见矛盾时，基于投资逻辑和数据权重做出裁决
4. **最终建议**：给出明确、可执行的投资建议

## 裁决原则
- 数据优先：有数据支撑的观点优先于主观判断
- 风险优先：在收益和风险之间，优先考虑风险控制
- 逆向思维：市场极度一致时保持警惕
- 时效性：优先考虑近期数据和当前市场环境

## 输出格式
1. **分歧分析**：各专家的核心分歧点
2. **裁决依据**：你做出判断的数据和逻辑依据
3. **最终建议**：明确的投资建议（买入/持有/卖出/观望）
4. **风险提示**：需要关注的风险因素

注意：你的裁决将直接影响用户的投资决策，请务必严谨、客观、有据可依。"""


def run_arbitration(query: str, specialist_results: list, rag_context: str = "") -> dict:
    """
    仲裁 Agent：使用高级推理模型（如 DeepSeek R1）审查所有专家分析，给出最终裁决。

    参数:
        query: 原始用户问题
        specialist_results: 所有专家的分析结果列表
        rag_context: RAG 检索上下文

    返回:
        {"agent_key": "arbitrator", "agent": "仲裁法官", "icon": "⚖️",
         "analysis": "...", "duration_ms": ..., "is_arbitration": True}
    """
    from llm_service import call_arbitration_llm

    start_time = time.time()

    # 从数据库加载仲裁 Agent 配置（支持通过 Agent 管理界面优化 prompt）
    agents = load_specialist_agents()
    arbitrator = agents.get("arbitrator")
    if arbitrator and arbitrator.get("system_prompt"):
        system_prompt = arbitrator["system_prompt"]
        agent_name = arbitrator.get("name", "仲裁法官")
        agent_icon = arbitrator.get("icon", "⚖️")
    else:
        # 回退到硬编码 prompt
        system_prompt = ARBITRATION_SYSTEM_PROMPT
        agent_name = "仲裁法官"
        agent_icon = "⚖️"

    # 构建专家分析摘要
    expert_sections = []
    for sr in specialist_results:
        sr_name = sr.get("agent", sr.get("agent_key", "未知"))
        icon = sr.get("icon", "🤖")
        analysis = sr.get("analysis", "")
        expert_sections.append(f"### {icon} {sr_name}\n{analysis[:2000]}")

    experts_text = "\n\n---\n\n".join(expert_sections)

    # 构建用户消息
    user_content = f"""## 用户问题
{query}

## 各专家分析结果
{experts_text}"""

    if rag_context:
        user_content += f"\n\n## 参考知识库\n{rag_context[:1500]}"

    llm_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # 调用高级推理模型
    response = call_arbitration_llm(
        messages=llm_messages,
        temperature=0.2,
        max_tokens=3000,
    )

    if response is None:
        # 仲裁模型未配置或调用失败，回退到主模型
        logger.warning("仲裁模型不可用，回退到主模型")
        response = _call_llm(
            caller="arbitration_fallback",
            model=MODEL,
            messages=llm_messages,
            temperature=0.2,
            max_tokens=3000,
        )

    answer = response.choices[0].message.content or ""

    # 提取 reasoning_content（DeepSeek R1 的思考过程）
    reasoning = None
    if hasattr(response.choices[0].message, "model_extra") and response.choices[0].message.model_extra:
        reasoning = response.choices[0].message.model_extra.get("reasoning_content")
    if not reasoning:
        reasoning = getattr(response.choices[0].message, "reasoning_content", None)

    duration_ms = int((time.time() - start_time) * 1000)

    return {
        "agent_key": "arbitrator",
        "agent": agent_name,
        "icon": agent_icon,
        "analysis": answer,
        "tool_calls": [],
        "duration_ms": duration_ms,
        "is_arbitration": True,
        "reasoning": reasoning,  # R1 的思考过程，前端可选择性展示
    }
