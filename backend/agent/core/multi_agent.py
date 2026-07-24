from db.config import get_config_float
"""多 Agent 协作架构 — 专家 Agent 执行引擎"""

import json
import logging
import re
import time

from services.llm_service import client, MODEL, _call_llm, _parse_tool_args
from tools import TOOLS, execute_tool
from db.agents import load_specialist_agents, create_agent_run
from db.config import get_config_int, get_config_bool

logger = logging.getLogger(__name__)


def _get_tool_result_max_chars() -> int:
    """工具结果截断阈值（可配置，默认 1500）。"""
    try:
        from db.config import get_config_int
        return get_config_int("agent.react_tool_result_max_chars", 1500)
    except Exception:
        return 1500


def _compress_prior_tool_messages(llm_messages: list):
    """把已累积的历史 tool 消息合并为一条摘要，保留最新一条原文。

    当 llm_messages 中有 >=2 个 tool 消息时触发：
    - 保留最后一个 tool 消息原文（最新数据最重要）
    - 前面的 tool 消息合并为一条摘要消息（每条取前 200 字）
    - 对应的 assistant tool_calls 消息保留（结构需要）
    """
    tool_indices = [i for i, m in enumerate(llm_messages) if m.get("role") == "tool"]
    if len(tool_indices) < 2:
        return

    # 收集前面所有 tool 消息的摘要（保留最后一个原文）
    keep_idx = tool_indices[-1]
    summaries = []
    for i in tool_indices[:-1]:
        msg = llm_messages[i]
        content = msg.get("content", "") or ""
        # 尝试从对应的 assistant tool_calls 拿工具名
        tool_name = "unknown"
        # 找前一条 assistant 消息中的 tool_call_id 匹配
        for j in range(i - 1, -1, -1):
            prev = llm_messages[j]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                for tc in prev["tool_calls"]:
                    if tc.get("id") == msg.get("tool_call_id"):
                        tool_name = tc.get("function", {}).get("name", "unknown")
                        break
                break
        summaries.append(f"[{tool_name}] {content[:200]}...")

    if not summaries:
        return

    # 用摘要替换最早的那条 tool 消息，删除中间的 tool 消息
    summary_msg = {
        "role": "tool",
        "tool_call_id": llm_messages[tool_indices[0]]["tool_call_id"],
        "content": f"（历史工具结果摘要，共{len(summaries)}条）\n" + "\n".join(summaries),
    }
    llm_messages[tool_indices[0]] = summary_msg
    # 从后往前删除中间的 tool 消息（不影响索引）
    for i in reversed(tool_indices[1:-1]):
        del llm_messages[i]
    logger.info(f"ReAct 历史压缩：合并 {len(tool_indices) - 1} 条 tool 消息为 1 条摘要")


def _maybe_compress_tool_history(llm_messages: list):
    """按配置开关调用历史压缩。"""
    try:
        if get_config_bool("agent.react_compress_history", True):
            _compress_prior_tool_messages(llm_messages)
    except Exception as e:
        logger.debug(f"历史压缩跳过: {e}")

# ── 分析模板约束（按分析类型强制结构化输出） ──
ANALYSIS_TEMPLATES = {
    "valuation": {
        "required_fields": ["当前估值", "历史分位", "估值水平", "数据来源", "风险因素"],
        "format_hint": "必须包含：当前PE/PB值、历史百分位、估值水平判断（低估/合理/高估）、数据日期、主要风险",
    },
    "risk": {
        "required_fields": ["风险类型", "风险等级", "触发条件", "应对策略"],
        "format_hint": "必须包含：风险类型（市场/流动性/政策/信用）、风险等级（低/中/高）、什么条件下触发、建议的应对措施",
    },
    "allocation": {
        "required_fields": ["当前配置", "目标配置", "偏离度", "调整建议", "理由"],
        "format_hint": "必须包含：当前各资产占比、目标比例、偏离幅度、具体调整操作、调整理由",
    },
    "market": {
        "required_fields": ["市场状态", "关键指标", "驱动因素", "展望"],
        "format_hint": "必须包含：当前市场状态（牛/熊/震荡）、关键估值/资金指标、主要驱动因素、短中期展望",
    },
    "strategy": {
        "required_fields": ["策略名称", "适用场景", "具体操作", "回测数据", "风险提示"],
        "format_hint": "必须包含：策略名称、什么情况下适用、具体买入/卖出操作、历史回测胜率/收益、最大回撤风险",
    },
}

# 通用可执行性约束 — 强制每条建议都有具体操作
_ACTIONABILITY_CONSTRAINT = """
## 🎯 可执行性要求（强制遵守）

你的分析结尾必须包含「具体操作建议」段落，格式：

### 💡 具体操作建议
1. **操作**：买/卖/持有/定投（选一）
   - **标的**：基金名称+代码
   - **金额或比例**：具体数字，如"2000元"或"现有仓位的10%"
   - **触发条件**：什么情况下执行，如"当PE分位降到20%时"
   - **理由**：一句话说明为什么

禁止出现：
- ❌ "建议根据自身情况决定"（等于没说）
- ❌ "可以考虑"（模棱两可）
- ❌ "适当调整"（没有具体比例）
- ❌ "仅供参考"（推卸责任）
"""


def _detect_analysis_type(query: str) -> str:
    """根据用户问题关键词检测分析类型。"""
    query_lower = query.lower()
    if any(kw in query_lower for kw in ["估值", "pe", "pb", "百分位", "低估", "高估"]):
        return "valuation"
    if any(kw in query_lower for kw in ["风险", "回撤", "亏损", "止损"]):
        return "risk"
    if any(kw in query_lower for kw in ["配置", "比例", "仓位", "股债"]):
        return "allocation"
    if any(kw in query_lower for kw in ["市场", "大盘", "行情", "走势"]):
        return "market"
    if any(kw in query_lower for kw in ["策略", "定投", "止盈", "择时"]):
        return "strategy"
    return "general"


def _get_template_constraint(analysis_type: str) -> str:
    """返回指定分析类型的模板约束 prompt 片段。"""
    template = ANALYSIS_TEMPLATES.get(analysis_type, {})
    if not template:
        return ""
    fields = "、".join(template.get("required_fields", []))
    return f"""
## 📋 分析模板（必须遵守）
你的分析必须包含以下字段：{fields}
格式要求：{template.get('format_hint', '')}
如果缺少上述字段，分析将被视为不合格。
"""

# ── 通用数据约束（追加到所有 Agent prompt 末尾） ──
_UNIVERSAL_DATA_CONSTRAINT = """
## ⚠️ 数据真实性约束（强制遵守）
1. **只使用上下文中明确提供的数据**，包括持仓、估值、新闻等
2. **绝对禁止编造**任何基金名称、基金代码、金额、收益率、持仓比例等数据
3. 如果上下文中没有持仓数据或显示"无持仓"，**必须明确告知用户**"未获取到持仓信息"，不得自行补充
4. 如果某个数据在上下文中缺失，直接说明"暂无该数据"，不要猜测或杜撰
5. 所有数字必须来自上下文或工具返回结果，不得凭记忆或推测给出具体数值

## 🔢 基金代码验证（强制遵守 - 违反将导致分析被拒绝）
1. **推荐任何基金代码前**，必须先调用 ttfund_search 或 query_fund_info 工具验证该代码的真实基金名称
2. 代码必须是 6 位数字，如不确认就说"无法获取"而非编造
3. 如果展示对比表格，每一行的代码、名称、类型都必须来自工具返回结果
4. **绝对不凭记忆或训练数据中的参数知识直接写出基金代码**

## 📈 估值数据时效性（最高优先级 — 估值必须新）
**核心原则：所有 LLM 分析用到的估值数据一定要是新的，过时数据会导致错误结论。**

1. 先用 query_valuation 查估值：
   - 库内数据超 3 天未更新时，系统会**自动补充在线最新数据**到 `online_latest` 字段
   - 若返回结果含 `online_latest` 字段，**以 online_latest 为准**（数据更新）
   - 若返回结果含 `data_freshness_hint`，说明库内数据已过时，已在线补充
2. **若库内数据超过 3 天（days_old > 3）或标记 is_expired，且无 online_latest**，主动调用 query_online_valuation 查 akshare 在线最新估值
3. 在线数据返回后，在分析中标注"数据来源：akshare在线（日期）"
4. 若用户明确要求"查最新"，直接调用 query_online_valuation
5. 禁止：库内数据超过 3 天却不查在线，导致估值判断滞后

## ✍️ 输出格式约束（强制遵守）
1. **禁止使用 emoji**（如 📊🛡️🤖⚖️🔍🔴 等）作为标题、列表标记或装饰
2. 使用 Markdown 标题（## ###）、表格、加粗、引用块来组织结构
3. 数据用表格呈现，关键结论用加粗，风险提示用引用块（> ）
4. 保持专业严谨的金融分析文风，不要用口语化或情绪化表达
"""

# ── 专家主动检索指令（追加到有 search_knowledge 工具的 Agent） ──
# P4 半修修复：从 6 类场景全强制检索 book 降为 3 类（估值/情绪/周期，有引用证据）
# 买卖/企业质量/资产配置改用工具数据（靠持仓和行情工具，而非书籍）
# 升级：扩展为 3 阶段 Agentic RAG 策略（信息缺口判断→主动检索→充分性自检）
_ACTIVE_RETRIEVAL_INSTRUCTION = """
## 📚 主动检索策略（Agentic RAG）
你拥有 search_knowledge 工具，可以检索投资经典书籍、分析记录、文章等知识库。

### 阶段 1：信息缺口判断
分析用户问题，列出你需要的关键信息清单。对照已提供的上下文和工具结果，
明确标注哪些信息已有、哪些还缺失。**特别注意黑板上的"已有工具结果"区块**——
如果其他专家已经查询过相同数据，直接引用，不要重复查询。

### 阶段 2：主动检索
对每个缺失信息，使用 search_knowledge 工具主动检索：
- 第一轮：用核心关键词检索
- 若结果不足，第二轮：换同义词或扩展词检索
- 每个信息缺口最多 2 轮检索

**强制检索场景**（必须主动调用）：
1. **估值判断时** → 检索 query="估值 安全边际 PE PB 百分位"，content_types=["book", "analysis"]
2. **分析市场情绪/心理偏差时** → 检索 query="心理 偏差 情绪 损失厌恶"，content_types=["book", "analysis"]
3. **分析周期位置时** → 检索 query="周期 牛熊 转折 估值温度"，content_types=["book", "analysis"]
4. **行业/板块分析时** → 检索 query="<板块名> 政策 趋势"，content_types=["article", "analysis"]

### 阶段 3：充分性自检
检索完成后，判断信息是否充分：
- 充分 → 开始分析
- 仍不足 → 在分析中明确标注"该维度数据不足"，**不要编造数据**

### 不检索场景（用工具数据替代）
- 买卖操作 → 用 query_portfolio / query_valuation 等工具
- 企业质量评估 → 用 query_fund_info / ttfund_fund_manager 等工具
- 资产配置 → 用 query_portfolio 查持仓后分析

引用书籍/文章观点时请注明来源（如"根据《聪明的投资者》..."），增强分析的可信度。
**禁止引用工具未返回的数据**——如果 search_knowledge 或 query_valuation 未找到某指数数据，
不得在分析中编造该指数的 PE/PB/百分位等数值。
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
_NO_RAW_TOOLCALL_CONSTRAINT = """
## 🚫 工具调用禁止文本输出格式（强制遵守）
1. 禁止在分析文本中输出任何形式的 tool_call 标签或 XML
2. 包括但不限于：<tool_call>、<invoke>、<function=>、<parameter=> 等原始标签
3. 工具调用通过 function calling 机制自动执行，你只需要正常输出分析文本
4. 如果你需要调用工具，系统会自动识别和处理，不要在文本中手写工具调用代码
5. 违反此条的分析将被完全丢弃，视为失败
"""

_UNIVERSAL_CONTEXT_INSTRUCTION = """
## ⚠️ 全局视角要求（必须遵守）

在给出任何投资建议之前，你必须先考虑以下全局因素：

1. **持仓盈亏状况**：每只基金是赚是亏？幅度多大？
   - 亏损>15%的基金，继续加仓需格外谨慎（考虑用户心理承受力）
   - 盈利>15%的基金，应考虑部分止盈而非继续持有
   - 不要仅凭估值高低给出加减仓建议

2. **集中度风险**：某只基金占比是否超过25%？
   - 如果已高集中度，建议应优先考虑分散而非继续加仓

3. **近30天操作**：用户近期是否已对该基金有操作？
   - 已买入/定投多次 → 建议持有观察
   - 已卖出 → 不要马上劝再买

4. **历史结论**：之前是否对该标的有过分析结论？
   - 如果上下文提供了历史结论，请参考但不盲从
   - 如果历史结论和当前分析不一致，请说明差异原因

你的分析结尾必须包含「对当前持仓的影响」一段，指明你的建议会如何改变用户的持仓状况。
"""


def _has_raw_toolcall_tags(text: str) -> bool:
    """检测文本中是否包含未解析的工具调用标签。"""
    if not text:
        return False
    # XML 格式
    if '<tool_call>' in text or '<invoke name=' in text or '<function=>' in text:
        return True
    # DSML 格式
    if '｜｜DSML｜｜invoke' in text:
        return True
    return False


def _clear_raw_toolcall_tags(text: str) -> str:
    """清理文本中的原始工具调用标签。"""
    if not text:
        return text
    import re as _re
    # 删除 <tool_call>...</tool_call> 块
    text = _re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=_re.DOTALL)
    # 删除 <invoke name="...">...</invoke> 块
    text = _re.sub(r'<invoke name="[^"]*"[^>]*>.*?</invoke>', '', text, flags=_re.DOTALL)
    # 删除 DSML 块
    dsml = '｜｜DSML｜｜'
    text = _re.sub(r'<' + _re.escape(dsml) + r'(?:invoke|tool_calls)[^>]*>.*?</' + _re.escape(dsml) + r'(?:invoke|tool_calls)>', '', text, flags=_re.DOTALL)
    return text.strip()


def _extract_text_tool_calls(content_str):
    """从 LLM 文本中提取 XML 或 DSML 格式的 tool_call。

    支持两种格式：
    1. XML 格式: <tool_call><function=name>...<parameter=name>value</parameter></function></tool_call>
    2. DSML 格式 (DeepSeek): <｜｜DSML｜｜invoke name="func">...<｜｜DSML｜｜parameter name="arg">value</｜｜DSML｜｜/parameter></｜｜DSML｜｜/invoke>
    """
    if not content_str:
        return None
    import re as _re

    results = []

    # ── 格式1: XML <tool_call> ──
    if '<tool_call>' in content_str:
        tc_open = chr(60) + 'tool' + '_call' + chr(62)
        tc_close = chr(60) + '/tool' + '_call' + chr(62)
        fn_open = chr(60) + 'function='
        fn_close = chr(60) + '/function' + chr(62)
        param_open = chr(60) + 'parameter='
        param_close = chr(60) + '/parameter' + chr(62)

        pattern = tc_open + r'\s*' + fn_open + r'(\w+)>' + r'(.*?)' + fn_close + r'\s*' + tc_close
        matches = _re.findall(pattern, content_str, _re.DOTALL)
        for func_name, params_block in matches:
            pp = param_open + r'(\w+)>' + r'(.*?)' + param_close
            param_matches = _re.findall(pp, params_block, _re.DOTALL)
            args = {pname: pval.strip() for pname, pval in param_matches}
            results.append({'name': func_name, 'arguments': args})

    # ── 格式2: DSML <｜｜DSML｜｜invoke> ──
    _sep = '\uff5c\uff5c'  # ｜｜ (fullwidth)
    if _sep + 'DSML' + _sep + 'invoke' in content_str:
        dsml = _sep + 'DSML' + _sep
        close_param = '</' + dsml + 'parameter>'   # </｜｜DSML｜｜parameter>
        close_invoke = '</' + dsml + 'invoke>'       # </｜｜DSML｜｜invoke>
        invoke_pat = dsml + r'invoke name="(\w+)">(.*?)' + _re.escape(close_invoke)
        invoke_matches = _re.findall(invoke_pat, content_str, _re.DOTALL)
        for func_name, params_block in invoke_matches:
            param_pat = dsml + r'parameter name="(\w+)"[^>]*>(.*?)' + _re.escape(close_param)
            param_matches = _re.findall(param_pat, params_block, _re.DOTALL)
            args = {pname: pval.strip() for pname, pval in param_matches}
            results.append({'name': func_name, 'arguments': args})

    return results if results else None


def _process_text_tool_calls(content_str, llm_messages, tool_calls_log, agent_name, trace_id="",
                             conversation_id=None, message_id=None, user_query=""):
    """处理文本格式的 tool_call：解析、执行、将结果追加到消息列表。"""
    text_calls = _extract_text_tool_calls(content_str)
    if not text_calls:
        return False
    logger.info(f'[{agent_name}] 检测到文本格式 tool_call: {[tc["name"] for tc in text_calls]}')
    for tc in text_calls:
        args = tc['arguments']
        logger.info(f'[{agent_name}] 文本 Tool: {tc["name"]}({json.dumps(args, ensure_ascii=False)[:100]})')
        result = execute_tool(tc['name'], args, trace_id=trace_id,
                             conversation_id=conversation_id, message_id=message_id,
                             agent_name=agent_name, user_query=user_query)
        if len(result) > 8000:
            result = result[:8000] + chr(10) + '... (结果过长，已截断)'
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


def _clean_dsml_from_content(content: str) -> str:
    """清理 DSML 标记，防止泄露到最终回答。"""
    if not content:
        return content
    _sep = '\uff5c\uff5c'
    dsml = _sep + 'DSML' + _sep
    if dsml not in content:
        return content
    import re as _re
    # 删除整个 DSML 块: <｜｜DSML｜｜tool_calls>...</｜｜DSML｜｜tool_calls>
    tc_open = '<' + dsml + 'tool_calls>'
    tc_close = '</' + dsml + 'tool_calls>'
    if tc_open in content and tc_close in content:
        pattern = _re.escape(tc_open) + r'.*?' + _re.escape(tc_close)
        content = _re.sub(pattern, '', content, flags=_re.DOTALL)
    else:
        # 删除散落的 DSML 标签
        content = _re.sub(r'<' + _re.escape(dsml) + r'[^>]*>', '', content)
        content = _re.sub(r'</' + _re.escape(dsml) + r'[^>]*>', '', content)
    return content.strip()


# ── 专家 Agent 加载 ──────────────────────────────────────

# SPECIALIST_AGENTS 从数据库加载（db.agents.load_specialist_agents），不再硬编码。
# 通过 Agent 管理页面修改 prompt 后，编排器自动使用新版本。

def _inject_kyc_profile(system_content: str, agent: dict) -> str:
    """注入 KYC 理财画像到专家 system prompt。

    按专家配置的 kyc_dimensions 裁剪（从 knowledge_scope JSON 解析）；
    未配置则注入全部维度。画像为空时原样返回。
    """
    try:
        from agent.kyc.kyc import kyc_profile_to_text
        dims = None
        ks = agent.get("knowledge_scope")
        if ks:
            try:
                ks_dict = json.loads(ks) if isinstance(ks, str) else ks
                dims = ks_dict.get("kyc_dimensions")
            except (json.JSONDecodeError, TypeError):
                pass
        kyc_text = kyc_profile_to_text("default", dimensions=dims)
        if kyc_text:
            return system_content + f"\n\n{kyc_text}"
    except Exception as e:
        logger.debug(f"KYC 画像注入跳过: {e}")
    return system_content


def _check_react_convergence(tool_calls_log: list, llm_messages: list) -> bool:
    """P2: ReAct 收敛检测 — 判断是否已产出可用结论，可提前终止。

    判定标准（满足任一即视为收敛）：
    1. 工具结果中已含结构化 JSON（含 conclusion/action_signals/confidence 字段）
    2. 已有 assistant 消息含 ≥200 字的文本分析（非工具调用）
    3. 工具调用次数 >= 2 且最近一次工具结果含数值型数据（PE/百分位/估值等）

    Returns:
        True 表示已收敛，可注入收口提示提前终止
    """
    if not tool_calls_log:
        return False

    # 判定 1：工具结果中含结构化 JSON 结论
    for tc in tool_calls_log:
        result_preview = tc.get("result_preview", "")
        if any(kw in result_preview for kw in ['"conclusion"', '"action_signals"', '"confidence"', '"评级"', '"结论"']):
            return True

    # 判定 2：已有 assistant 消息含 ≥200 字文本分析
    for msg in llm_messages:
        if msg.get("role") == "assistant" and msg.get("content"):
            content = msg["content"]
            # 排除仍含工具调用标签的（修正拼写 bug: "arrison" → 工具调用标签检测）
            if "<tool_call" in content.lower() or "<invoke>" in content or "tool_calls" in content.lower():
                continue
            if len(content) >= 200:
                return True

    # 判定 3：工具调用 >= 2 且最近结果含数值数据
    if len(tool_calls_log) >= 2:
        latest_result = tool_calls_log[-1].get("result_preview", "")
        # 检测数值型数据（PE/百分位/估值/Z-Score 等）
        import re as _re
        numeric_patterns = [
            r'PE[：:]\s*\d', r'百分位[：:]\s*\d', r'估值[：:]\s*\d',
            r'Z-Score[：:]\s*-?\d', r'\d+\.\d+%', r'高估|低估|合理',
        ]
        for pattern in numeric_patterns:
            if _re.search(pattern, latest_result):
                return True

    # 判定 4（P2 扩展）：工具调用 >= 2 且已有足够非空结果（不限数值型，覆盖新闻/资讯类场景）
    if len(tool_calls_log) >= 2:
        valid_results = [tc for tc in tool_calls_log if tc.get("result_preview", "")]
        if len(valid_results) >= 2:
            return True

    return False



def run_specialist(agent_key: str, query: str, context: str = "",
                   prebuilt_context: str = "", model: str = None, trace_id: str = "",
                   from_pipeline: bool = False, conversation_id: int = None, message_id: int = None,
                   blackboard=None) -> dict:
    """
    运行单个专家 Agent。

    流程：
    1. 构建该专家的 system prompt + 专属工具集
    2. 发送 query + context 给 LLM
    3. LLM 通过 function calling 调用专属工具
    4. 返回专家的分析结果

    Args:
        from_pipeline: Pipeline 路径标记。True 时 prebuilt_context 已含 Layer 0-5
                      （含 system_prompt/KYC/持仓），跳过重复注入。

    返回:
        {"agent": "估值专家", "icon": "📊", "analysis": "...", "tool_calls": [...], "duration_ms": 1234}
    """
    # 兜底：trace_id 为空时自动生成，确保 token_usage/agent_runs/tool_audit_logs 可关联
    if not trace_id:
        import uuid as _uuid
        trace_id = f"spec-{_uuid.uuid4().hex[:8]}"
    agent = load_specialist_agents()[agent_key]
    start_time = time.time()
    _caller = f"specialist:{agent_key}"
    _model = model or MODEL  # 增强6: 成本路由 — 支持外部传入模型
    logger.info(f"[trace:{trace_id}] 专家开始: {agent['name']} ({agent_key}) query={query[:50]}...")

    # 只给该专家分配它的专属工具
    try:
        from tools.tool_registry import ToolRegistry
        registry = ToolRegistry.get_instance()
        agent_tools = registry.get_tools_for_agent(agent["tools"])
    except Exception:
        # 降级：ToolRegistry 未初始化时用硬编码 TOOLS
        agent_tools = [t for t in TOOLS if t["function"]["name"] in agent["tools"]]

    if from_pipeline:
        # Pipeline 路径：prebuilt_context 来自 build_specialist_context，已含 Layer 0-5
        # （system_prompt + KYC + 持仓 + RAG + 估值 + 黑板），直接用作 system_content
        # 跳过 system_prompt/KYC/持仓的重复注入，通用约束由后面全局代码统一追加
        system_content = prebuilt_context or agent["system_prompt"]
        # Agentic RAG：pipeline 路径也注入主动检索指令（让专家能主动多轮检索）
        if "search_knowledge" in agent.get("tools", []) and get_config_bool("agent.agentic_rag_enabled", True):
            system_content += _ACTIVE_RETRIEVAL_INSTRUCTION
    else:
        # ReAct 路径：原逻辑，手动构建上下文
        system_content = agent["system_prompt"]
        if context:
            system_content += f"\n\n以下是相关上下文信息，请结合分析：\n{context[:6000]}"

        # 注入持仓上下文（优先使用预构建的，避免重复 DB 查询）
        if prebuilt_context:
            system_content += f"\n\n{prebuilt_context}"
        else:
            try:
                from services.portfolio_context import build_portfolio_context
                portfolio_ctx = build_portfolio_context()
                system_content += f"\n\n## 用户当前持仓\n{portfolio_ctx}"
            except Exception:
                pass

        # 注入 KYC 理财画像（按专家职责裁剪维度）
        system_content = _inject_kyc_profile(system_content, agent)

        # 追加主动检索指令（仅当专家有 search_knowledge 工具时）
        if "search_knowledge" in agent.get("tools", []) and get_config_bool("agent.agentic_rag_enabled", True):
            system_content += _ACTIVE_RETRIEVAL_INSTRUCTION

    # 追加通用数据约束
    system_content += _UNIVERSAL_DATA_CONSTRAINT

    # 追加禁止原始 tool_call 文本约束
    system_content += _NO_RAW_TOOLCALL_CONSTRAINT

    # P0 全局视角指令：强制看全局再分析
    system_content += _UNIVERSAL_CONTEXT_INSTRUCTION

    # 追加分析模板约束（根据用户问题自动检测分析类型）
    analysis_type = _detect_analysis_type(query)
    template_constraint = _get_template_constraint(analysis_type)
    if template_constraint:
        system_content += template_constraint

    # 追加通用可执行性约束 + 数据约束（含emoji禁令）
    system_content += _ACTIONABILITY_CONSTRAINT
    system_content += _UNIVERSAL_DATA_CONSTRAINT

    # 注入幻觉防御 Prompt（事实约束 + 类型专用约束）
    try:
        from agent.safety.prompt_defense import attach_defense_prompt
        system_content = attach_defense_prompt(system_content, analysis_type)
    except Exception:
        pass

    # 注入 A2A 结构化输出指令
    try:
        from agent.infra.message_protocol import get_a2a_output_instruction
        system_content += get_a2a_output_instruction()
    except Exception:
        pass

    llm_messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": query},
    ]

    MAX_TURNS = 3
    tool_calls_log = []
    answer = ""
    # M6: Agentic RAG 硬性限制 — 检索类工具单独计数，超过上限强制进入分析
    # 解决原 max_rounds=2 仅是 prompt 指令、无硬性截断的问题
    _SEARCH_TOOLS = {
        "search_knowledge", "web_search", "yingmi_search_news", "eastmoney_search",
        "query_policy_news", "get_author_opinions", "fetch_article",
    }
    _MAX_SEARCH_ROUNDS = get_config_int("agent.agentic_rag_max_rounds", 2)
    _SEARCH_LIMIT_ENABLED = get_config_bool("agent.agentic_rag_hard_limit_enabled", True)
    # R-3（2026-07-23）：宏观策略师 search_knowledge 引导增强 — 轮次预算单独提升至 3
    # 修复断层 C：原 prompt 强引导 yingmi_search_news，挤占 search_knowledge 轮次
    # 默认关闭（需手动开启 rag.macro_strategist_search_knowledge_boost），开启后从 2 提升到 3
    if agent_key == "macro_strategist" and get_config_bool("rag.macro_strategist_search_knowledge_boost", False):
        _MAX_SEARCH_ROUNDS = max(_MAX_SEARCH_ROUNDS, 3)
        logger.info(f"[trace:{trace_id}] [{agent['name']}] R-3 boost: _MAX_SEARCH_ROUNDS → {_MAX_SEARCH_ROUNDS}")
    search_rounds = 0
    # 收口提示：末轮强制要求输出文本结论，不再调工具，避免进入强制总结+重新生成链路
    _CLOSURE_PROMPT = (
        "你已收集到足够信息。请不要再调用工具，直接基于以上数据给出你的专业分析结论（至少 200 字）。"
    )
    # P2: 收敛检测 — turn1 后若已有结构化结论（JSON with action_signal/confidence），提前终止
    _CONVERGENCE_PROMPT = (
        "你已在前一轮工具调用中获得了关键数据，并给出了结构化结论。"
        "现在请基于已有信息，直接输出最终的专业分析（至少 200 字），不要再调用工具。"
    )
    # M6: 检索轮次超限提示
    _SEARCH_LIMIT_PROMPT = (
        "检索轮次已达上限，请基于已有信息给出分析结论，标注数据缺口。"
        "不要再调用检索类工具（search_knowledge/web_search/yingmi_search_news/query_policy_news等），"
        "可以直接使用 query_valuation/query_portfolio 等数据查询工具。"
    )

    for turn in range(MAX_TURNS):
        # M6: Agentic RAG 硬性限制 — turn 开始时检查检索轮次
        # 超过上限时注入提示，强制 LLM 进入分析阶段（不再调用检索类工具）
        if _SEARCH_LIMIT_ENABLED and search_rounds >= _MAX_SEARCH_ROUNDS and turn > 0 and not answer:
            logger.info(f"[trace:{trace_id}] [{agent['name']}] turn{turn} 检索轮次达上限({search_rounds}/{_MAX_SEARCH_ROUNDS})，强制进入分析")
            llm_messages.append({"role": "user", "content": _SEARCH_LIMIT_PROMPT})

        # P2: 收敛检测 — turn >= 1 时，若上一轮工具结果已含结构化结论，注入收口提示
        if turn >= 1 and not answer:
            _has_converged = _check_react_convergence(tool_calls_log, llm_messages)
            if _has_converged:
                logger.info(f"[trace:{trace_id}] [{agent['name']}] turn{turn} 检测到收敛，注入收口提示提前终止")
                llm_messages.append({"role": "user", "content": _CONVERGENCE_PROMPT})

        # 方案1：末轮收口 — 注入提示让 LLM 输出文本结论，而非继续调工具
        if turn == MAX_TURNS - 1:
            llm_messages.append({"role": "user", "content": _CLOSURE_PROMPT})
        # P3 优化：历史 tool 消息压缩，避免 context 膨胀
        _maybe_compress_tool_history(llm_messages)
        # 方案3：caller 增加 turn 后缀，便于 token_usage 排查
        _caller_turn = f"{_caller}#turn{turn}"
        try:
            response = _call_llm(
                caller=_caller_turn,
                trace_id=trace_id,
                model=_model,
                messages=llm_messages,
                tools=agent_tools,
                tool_choice="auto",
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_agent', 8000),
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"[trace:{trace_id}] [{agent['name']}] LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning(f"[trace:{trace_id}] [{agent['name']}] 模型不兼容，回退到普通模式")
                # 回退：不带 tools 调用
                response = _call_llm(
                    caller=_caller_turn,
                    trace_id=trace_id,
                    model=_model,
                    messages=llm_messages,
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_agent', 8000),
                )
                answer = response.choices[0].message.content or ""
                break
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 检查文本格式 tool_call，否则为最终回答
        if not msg.tool_calls:
            if _process_text_tool_calls(msg.content or "", llm_messages, tool_calls_log, agent["name"], trace_id=trace_id,
                                        conversation_id=conversation_id, message_id=message_id, user_query=query):
                continue
            # 兜底：如果文本中仍有未解析的 tool_call 标签，清理后追加恢复消息
            raw = msg.content or ""
            if _has_raw_toolcall_tags(raw):
                logger.warning(f"[{agent['name']}] 输出含未解析 tool_call 标签，触发兜底清理")
                import re as _re
                cleaned = _clear_raw_toolcall_tags(raw)
                # 追加恢复提示，让 LLM 下一轮重新分析
                recovery_msg = (
                    "[系统提示] 你在分析文本中输出了工具调用标签（如 <tool_call> 或 <invoke>），"
                    "但格式未能被解析。请重新分析，将工具调用通过 function calling 机制使用，"
                    "或在文本中直接给出分析结论。\n\n"
                    "你上一轮的分析内容：\n```\n" + cleaned[:2000] + "\n```"
                )
                llm_messages.append({"role": "user", "content": recovery_msg})
                continue
            answer = raw
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
            _tool_name = tc.function.name

            # M6: Agentic RAG 硬性限制 — 检索类工具计数与拦截
            if _SEARCH_LIMIT_ENABLED and _tool_name in _SEARCH_TOOLS:
                if search_rounds >= _MAX_SEARCH_ROUNDS:
                    # 已超限，跳过检索工具调用，注入提示让 LLM 用已有数据分析
                    logger.info(f"[trace:{trace_id}] [{agent['name']}] 拦截检索工具 {_tool_name}（轮次 {search_rounds}/{_MAX_SEARCH_ROUNDS}）")
                    llm_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "检索轮次已达上限。请基于已有信息分析，标注数据缺口，不要再调用检索类工具。",
                    })
                    continue
                search_rounds += 1
                logger.info(f"[trace:{trace_id}] [{agent['name']}] 检索轮次 {search_rounds}/{_MAX_SEARCH_ROUNDS} ({_tool_name})")

            logger.info(f"[trace:{trace_id}] [{agent['name']}] Tool: {_tool_name}({json.dumps(args, ensure_ascii=False)[:100]})")
            result = execute_tool(_tool_name, args, trace_id=trace_id,
                                 conversation_id=conversation_id, message_id=message_id,
                                 agent_name=agent['name'], user_query=query)

            # P3 优化：截断阈值可配置（默认 1500，原 3000）
            _max_chars = _get_tool_result_max_chars()
            if len(result) > _max_chars:
                result = result[:_max_chars] + "\n... (结果过长，已截断)"

            tool_calls_log.append({
                "name": tc.function.name,
                "arguments": args,
                "result_preview": result[:200] if result.strip() else "（无数据返回）",
            })

            # 工具结果广播：结构化提取并写入黑板，供后续专家引用
            if blackboard is not None and get_config_bool("agent.tool_broadcast_enabled", True):
                try:
                    from agent.infra.tool_broadcast import extract_broadcast, should_broadcast
                    if should_broadcast(tc.function.name):
                        entry = extract_broadcast(
                            tc.function.name, args, result,
                            agent_key, agent["name"]
                        )
                        if entry:
                            blackboard.write_tool_broadcast(entry)
                except Exception as e:
                    logger.debug(f"[tool_broadcast] 广播失败 {tc.function.name}: {e}")

            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # 如果循环结束还没拿到 answer，用最后一次 LLM 回复的文本内容
    if not answer:
        # 先检查最后一条 assistant 消息是否有文本内容
        for m in reversed(llm_messages):
            if m.get("role") == "assistant" and m.get("content") and not m.get("tool_calls"):
                answer = m["content"]
                break

    # 方案2：合并"强制总结 + 重新生成"为单次收口调用
    # 原逻辑：answer 为空 → 强制总结（1次LLM）→ <200字 → 重新生成（1次LLM）= 最多2次
    # 新逻辑：answer 为空或 <200 字 → 单次收口调用（1次LLM），prompt 直接要求 ≥200 字
    _needs_closure = (not answer) or (len(answer) < 200) or ('<tool_call>' in answer)
    if _needs_closure:
        # 清理含 tool_call 标签的 answer
        if answer and '<tool_call>' in answer:
            answer = re.sub(r'<tool_call>.*?</function>', '', answer, flags=re.DOTALL).strip()
        if len(answer) < 200:
            logger.info(f'[trace:{trace_id}] [{agent["name"]}] answer 为空或过短({len(answer)}字)，触发单次收口')
            try:
                # L4 防幻觉：收口前检测基金代码幻觉，注入纠正提示
                _hallucination_hints = _detect_fund_code_hallucination(tool_calls_log, agent)
                if _hallucination_hints:
                    logger.warning(f'[trace:{trace_id}] [{agent["name"]}] 检测到基金代码幻觉，注入纠正提示')
                    llm_messages.append({
                        "role": "user",
                        "content": _hallucination_hints,
                    })
                llm_messages.append({
                    "role": "user",
                    "content": "请基于以上工具调用结果，给出你的专业分析结论。要求：不调用工具，直接输出分析，至少 200 字。",
                })
                _closure_resp = _call_llm(
                    caller=f"{_caller}#summary",
                    trace_id=trace_id,
                    model=_model,
                    messages=llm_messages,
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_agent', 8000),
                )
                _closure_answer = _closure_resp.choices[0].message.content or ""
                if len(_closure_answer) >= 200:
                    answer = _closure_answer
                else:
                    # 兜底：收口仍 <200 字 → 用工具结果拼接，不再二次调 LLM
                    logger.warning(f'[trace:{trace_id}] [{agent["name"]}] 收口仍 {len(_closure_answer)} 字，用工具结果兜底')
                    answer = _fallback_from_tool_results(tool_calls_log) or _closure_answer or "分析过程遇到问题，请重试。"
            except Exception as _e:
                logger.error(f'[trace:{trace_id}] [{agent["name"]}] 收口调用失败: {_e}')
                answer = _fallback_from_tool_results(tool_calls_log) or "分析过程遇到问题，请重试。"

    duration_ms = int((time.time() - start_time) * 1000)

    # 清理 DSML 标记
    if answer:
        answer = _clean_dsml_from_content(answer)

    # ── 单专家自我反思（Self-Reflection） ──
    # 在 answer 定型后、返回前，评估分析质量，发现缺口时重试补充
    self_reflection_result = None
    try:
        from agent.infra.self_reflection import (
            evaluate_analysis, build_retry_prompt,
            is_self_reflection_enabled, get_max_retry,
        )
        if is_self_reflection_enabled() and answer and len(answer) > 50:
            # M7：从黑板获取其他专家结论，供跨专家盲点检查（开关关闭时不传，无额外成本）
            peer_conclusions = ""
            if blackboard:
                try:
                    peer_conclusions = blackboard.to_context_text(exclude_agent=agent_key)
                except Exception:
                    peer_conclusions = ""
            reflection = evaluate_analysis(
                analysis=answer,
                tool_calls=tool_calls_log,
                user_question=query,
                agent_key=agent_key,
                agent_name=agent["name"],
                trace_id=trace_id,
                peer_conclusions=peer_conclusions,
            )

            if reflection and reflection.get("need_retry") and reflection.get("gaps"):
                # 注入补充提示，重新调用 LLM 完善分析
                retry_prompt = build_retry_prompt(reflection)
                if retry_prompt:
                    max_retry = get_max_retry()
                    _retry_model = model or MODEL
                    for retry_idx in range(max_retry):
                        retry_messages = llm_messages + [
                            {"role": "user", "content": retry_prompt}
                        ]
                        try:
                            retry_answer = _call_llm(
                                caller=f"{agent_key}#self_reflection_retry",
                                trace_id=trace_id,
                                model=_retry_model,
                                messages=retry_messages,
                                temperature=0.3,
                                max_tokens=4000,
                            )
                        except Exception as retry_err:
                            logger.warning(f"[self_reflection] {agent['name']} 重试调用失败: {retry_err}")
                            retry_answer = None
                        if retry_answer and len(retry_answer) > 50:
                            answer = _clean_dsml_from_content(retry_answer)
                            reflection["gaps_resolved"] = True
                            break
                    else:
                        reflection["gaps_resolved"] = False

            self_reflection_result = reflection
    except Exception as e:
        logger.warning(f"[self_reflection] {agent['name']} 反思异常: {e}")

    # Pipeline Phase C：估算 token 消耗（用于预算追踪）
    tokens_used = _estimate_specialist_tokens(llm_messages, answer, tool_calls_log)

    # 失败检测：兜底文本或过短内容标记为 failed
    _FALLBACK_ANSWERS = {"分析过程遇到问题，请重试。", "交叉审阅完成，请参考其他专家分析。"}
    is_fallback = answer in _FALLBACK_ANSWERS or answer.startswith("（执行失败：") or len(answer.strip()) < 50
    status = "failed" if is_fallback else "success"

    # 剥离 A2A JSON 块，analysis 字段只保留纯 Markdown 给用户
    import re as _re
    _json_match = _re.search(r'```json\s*\n.*?\n```', answer, _re.DOTALL)
    clean_analysis = answer
    if _json_match:
        clean_analysis = (answer[:_json_match.start()] + answer[_json_match.end():]).strip()

    return {
        "agent_key": agent_key,
        "agent": agent["name"],
        "icon": agent["icon"],
        "analysis": clean_analysis,
        "status": status,
        "structured": _parse_structured_output(answer, agent_key, agent["name"], trace_id, tool_calls_log, duration_ms),
        "tool_calls": tool_calls_log,
        "duration_ms": duration_ms,
        "tokens_used": tokens_used,
        "self_reflection": self_reflection_result,
    }


def _detect_fund_code_hallucination(tool_calls_log: list, agent: dict) -> str:
    """L4 防幻觉：检测基金分析师是否幻觉了不在持仓中的基金代码。

    检查 query_fund_info 调用的 fund_code 是否在用户持仓中存在。
    如果不存在，返回纠正提示；否则返回空字符串。

    案例：conv#133 基金分析师幻觉了 007679/016181/016182，实际博时恒乐债券代码是 014846。
    """
    if agent.get("agent_key") != "fund_analyst":
        return ""
    # 收集所有 query_fund_info 调用的 fund_code
    queried_codes = []
    for tc in tool_calls_log:
        if tc.get("name") == "query_fund_info":
            try:
                args = json.loads(tc.get("arguments", "{}") or "{}")
                code = (args.get("fund_code") or "").strip()
                if code and code.isdigit():
                    queried_codes.append(code)
            except Exception:
                pass
    if not queried_codes:
        return ""
    # 检查这些代码是否在持仓中
    try:
        from db import list_holdings
        holdings = list_holdings()
        holding_codes = {h.get("fund_code") for h in holdings}
    except Exception:
        return ""
    wrong_codes = [c for c in queried_codes if c not in holding_codes]
    if not wrong_codes:
        return ""
    # 尝试从工具结果中找到查到的基金名称，帮 LLM 对应到正确代码
    code_suggestions = []
    for wc in wrong_codes:
        for tc in tool_calls_log:
            if tc.get("name") != "query_fund_info":
                continue
            try:
                args = json.loads(tc.get("arguments", "{}") or "{}")
                if (args.get("fund_code") or "").strip() != wc:
                    continue
                result_raw = tc.get("result", "") or ""
                try:
                    result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
                except Exception:
                    result = {}
                wrong_name = (result.get("basic_info") or {}).get("fund_name", "")
                if wrong_name:
                    code_suggestions.append(f"代码 {wc} 实际是'{wrong_name}'，不是用户问的基金")
                break
            except Exception:
                pass
    return (
        f"⚠️ 检测到基金代码错误：您查询的代码 {wrong_codes} 不在用户持仓列表中。"
        f"{'；'.join(code_suggestions) if code_suggestions else ''}。"
        f"请重新调用 query_portfolio(query_type='detail') 获取持仓列表，"
        f"从中找到用户提到的基金名称对应的正确 fund_code，再调用 query_fund_info。"
        f"禁止使用持仓列表中不存在的基金代码。"
    )


def _fallback_from_tool_results(tool_calls_log: list) -> str:
    """收口失败兜底：用工具结果摘要拼接成 answer，避免二次调 LLM。

    只在收口调用仍 <200 字或异常时使用，保证专家至少返回有数据支撑的文本。
    """
    tool_summaries = []
    for tc in tool_calls_log:
        preview = tc.get("result_preview", "")
        if preview and preview != "（无数据返回）":
            tool_summaries.append(preview[:500])
    return "\n\n".join(tool_summaries) if tool_summaries else ""


def _estimate_specialist_tokens(llm_messages: list, answer: str, tool_calls_log: list) -> int:
    """估算专家调用的总 token 消耗（prompt + completion）。

    用于 Pipeline 的 token 预算追踪，不追求精确，只用于预算控制。
    """
    try:
        from agent.memory.memory import estimate_tokens
    except ImportError:
        estimate_tokens = lambda x: len(x) // 3

    # prompt tokens：所有输入消息
    prompt_tokens = 0
    for msg in llm_messages:
        content = msg.get("content", "") or ""
        prompt_tokens += estimate_tokens(content)
        # tool_calls 的 arguments 也算
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            prompt_tokens += estimate_tokens(fn.get("arguments", ""))

    # completion tokens：最终回答 + 工具结果摘要
    completion_tokens = estimate_tokens(answer)
    for tc in tool_calls_log:
        completion_tokens += estimate_tokens(tc.get("result_preview", ""))

    return prompt_tokens + completion_tokens


def _parse_structured_output(raw_text: str, agent_key: str, agent_name: str,
                              trace_id: str, tool_calls: list, duration_ms: int) -> dict:
    """解析 agent 的 LLM 输出，提取结构化数据。降级时返回空结构。"""
    try:
        from agent.infra.message_protocol import parse_agent_output, ROLE_MAP
        output = parse_agent_output(
            raw_text=raw_text,
            agent_key=agent_key,
            agent_name=agent_name,
            agent_role=ROLE_MAP.get(agent_key, "analyst"),
            trace_id=trace_id,
            tool_calls=tool_calls,
            duration_ms=duration_ms,
        )
        return {
            "structured": output.to_dict(),
            "a2a": output.to_a2a_message(),
        }
    except Exception:
        return {"structured": {}, "a2a": {}}


def run_specialist_with_context(agent_key: str, query: str, peer_analyses: dict, trace_id: str = "",
                                max_turns: int = 2, prebuilt_context: str = "",
                                model: str = None,
                                conversation_id: int = 0, message_id: int = 0) -> dict:
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
    _model = model or MODEL
    # 兜底：trace_id 为空时自动生成
    if not trace_id:
        import uuid as _uuid
        trace_id = f"cross-{_uuid.uuid4().hex[:8]}"

    agent_tools = [t for t in TOOLS if t["function"]["name"] in agent["tools"]]

    # 构建交叉审阅的 system prompt
    system_content = agent["system_prompt"]

    # 注入持仓上下文（优先使用预构建的）
    if prebuilt_context:
        system_content += f"\n\n{prebuilt_context}"
    else:
        try:
            from services.portfolio_context import build_portfolio_context
            portfolio_ctx = build_portfolio_context()
            if portfolio_ctx:
                system_content += f"\n\n## 用户当前持仓（分析时务必结合）\n{portfolio_ctx}"
        except Exception:
            pass

    # 注入 KYC 理财画像（按专家职责裁剪维度）
    system_content = _inject_kyc_profile(system_content, agent)

    # 追加分析模板约束（交叉审阅也需要结构化输出）
    analysis_type = _detect_analysis_type(query)
    template_constraint = _get_template_constraint(analysis_type)
    if template_constraint:
        system_content += template_constraint

    # 追加通用可执行性约束 + 数据约束（含emoji禁令）
    system_content += _ACTIONABILITY_CONSTRAINT
    system_content += _UNIVERSAL_DATA_CONSTRAINT

    # 追加禁止原始 tool_call 文本约束
    system_content += _NO_RAW_TOOLCALL_CONSTRAINT

    # 追加圆桌审阅指令
    peer_sections = []
    for peer_key, peer_analysis in peer_analyses.items():
        if peer_key == agent_key:
            continue
        peer_agent = load_specialist_agents().get(peer_key)
        peer_name = peer_agent["name"] if peer_agent else peer_key
        peer_sections.append(f"【{peer_name}】的分析：\n{peer_analysis[:6000]}")

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
        # P3 优化：历史 tool 消息压缩
        _maybe_compress_tool_history(llm_messages)
        try:
            response = _call_llm(
                caller=_caller,
                trace_id=trace_id,
                model=_model,
                messages=llm_messages,
                tools=agent_tools,
                tool_choice="auto",
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_agent', 8000),
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"[trace:{trace_id}] [{agent['name']}] 交叉审阅 LLM 调用异常 (turn {turn}): {err_msg}")
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                response = _call_llm(
                    caller=_caller,
                    trace_id=trace_id,
                    model=_model,
                    messages=llm_messages,
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_agent', 8000),
                )
                answer = response.choices[0].message.content or ""
                break
            raise

        msg = response.choices[0].message

        if not msg.tool_calls:
            if _process_text_tool_calls(msg.content or "", llm_messages, tool_calls_log, agent["name"], trace_id=trace_id,
                                        conversation_id=conversation_id, message_id=message_id, user_query=query):
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
            logger.info(f"[trace:{trace_id}] [{agent['name']}] 交叉审阅 Tool: {tc.function.name}({json.dumps(args, ensure_ascii=False)[:100]})")
            result = execute_tool(tc.function.name, args, trace_id=trace_id,
                                 conversation_id=conversation_id, message_id=message_id,
                                 agent_name=agent['name'], user_query=query)
            # P3 优化：截断阈值可配置（默认 1500，原 3000）
            _max_chars = _get_tool_result_max_chars()
            if len(result) > _max_chars:
                result = result[:_max_chars] + "\n... (结果过长，已截断)"
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
                trace_id=trace_id,
                model=_model,
                messages=llm_messages,
                temperature=get_config_float('llm.temperature_agent', 0.3),
                max_tokens=get_config_int('llm.max_tokens_agent', 8000),
            )
            answer = response.choices[0].message.content or ""
        except Exception:
            answer = "交叉审阅完成，请参考以上分析。"

    duration_ms = int((time.time() - start_time) * 1000)

    # 清理：如果 answer 中仍包含文本格式 tool_call，去除之
    if answer and '<tool_call>' in answer:
        answer = re.sub(r'<tool_call>.*?</tool_call>', '', answer, flags=re.DOTALL).strip()
        if not answer or len(answer) < 200:
            # P0-2 修复：清理后内容过短 → 重新生成结论
            logger.warning(f'[trace:{trace_id}] [{agent["name"]}] cross_review answer 清理后仅 {len(answer)} 字，重新生成')
            try:
                llm_messages.append({
                    "role": "user",
                    "content": "请基于以上信息和其他专家的分析结果，给出你的交叉审阅结论（不要调用工具，直接输出分析）。",
                })
                _regen_resp = _call_llm(
                    caller=_caller,
                    trace_id=trace_id,
                    model=_model,
                    messages=llm_messages,
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_agent', 8000),
                )
                _regenerated = _regen_resp.choices[0].message.content or ""
                if _regenerated and len(_regenerated) >= 200:
                    answer = _regenerated
                else:
                    answer = "交叉审阅完成，请参考其他专家分析。"
            except Exception as _e:
                logger.error(f'[trace:{trace_id}] [{agent["name"]}] cross_review 重新生成失败: {_e}')
                answer = "交叉审阅完成，请参考其他专家分析。"

    # P0: 写入 agent_runs（cross_review ReAct 路径补齐）— 失败不影响主流程
    if conversation_id and message_id:
        try:
            create_agent_run(
                conversation_id=conversation_id,
                message_id=message_id,
                agent_key=agent_key,
                agent_name=agent["name"],
                query=query,
                result=answer[:4000],
                tool_calls=str(tool_calls_log)[:2000],
                duration_ms=duration_ms,
                trace_id=trace_id,
                status="success",
            )
        except Exception as log_err:
            logger.warning(f"[cross_review] agent_runs 写入失败 ({agent_key}): {log_err}")

    return {
        "agent_key": agent_key,
        "agent": agent["name"],
        "icon": agent["icon"],
        "analysis": answer,
        "tool_calls": tool_calls_log,
        "duration_ms": duration_ms,
        "is_cross_review": True,
    }


def run_cross_review_opinion(agent_key: str, query: str, self_analysis: str,
                              peer_analyses: dict, trace_id: str = "",
                              model: str = None,
                              conversation_id: int = 0, message_id: int = 0) -> dict:
    """交叉审阅单轮意见模式（不调用工具，仅 1 次 LLM 调用）。

    与 run_specialist_with_context 的区别：
    - 无 ReAct 工具循环（Phase A 已查过数据，cross_review 只对比观点）
    - 单次 LLM 调用，输出认同/质疑/补充意见
    - 节省 2-3 次 LLM 调用 per 专家

    Args:
        agent_key: 专家 key
        query: 原始用户问题
        self_analysis: 自己 Phase A 的分析文本
        peer_analyses: {agent_key: analysis_text} 其他专家的分析
        trace_id: 追踪 ID
        model: 可选模型覆盖

    Returns:
        兼容 run_specialist_with_context 的返回结构：
        {
            "agent_key", "agent", "icon", "analysis",
            "opinion": {"agreements", "disagreements", "additions"},
            "tool_calls": [], "duration_ms", "is_cross_review": True
        }
    """
    agent = load_specialist_agents()[agent_key]
    start_time = time.time()
    _caller = f"specialist:{agent_key}:cross_review"
    _model = model or MODEL
    if not trace_id:
        import uuid as _uuid
        trace_id = f"cross-{_uuid.uuid4().hex[:8]}"

    # 构建 peer 分析摘要（每个 peer 截断到 1000 字）
    peer_sections = []
    for peer_key, peer_analysis in peer_analyses.items():
        if peer_key == agent_key:
            continue
        peer_agent = load_specialist_agents().get(peer_key)
        peer_name = peer_agent["name"] if peer_agent else peer_key
        peer_sections.append(f"【{peer_name}】的分析：\n{peer_analysis[:6000]}")
    peer_text = "\n\n---\n\n".join(peer_sections) if peer_sections else "（无其他专家分析）"

    system_content = (
        f"你是{agent['name']}。以下是其他专家的分析结果，请从你的专业视角进行交叉审阅。\n\n"
        "要求：\n"
        "1. 指出你认同的其他专家观点（引用具体内容）\n"
        "2. 指出你认为有疑问或需要补充的地方（用数据或逻辑反驳）\n"
        "3. 从你的专业角度提供其他专家未覆盖的独特见解\n"
        "4. 如果你改变了之前的判断，说明原因\n\n"
        "输出 JSON 格式：\n"
        "{\n"
        '  "agreements": ["认同点1", "认同点2"],\n'
        '  "disagreements": [{"peer": "专家名", "point": "质疑的观点", "reason": "反驳理由"}],\n'
        '  "additions": ["补充见解1", "补充见解2"],\n'
        '  "summary": "审阅总结（一段话）"\n'
        "}\n"
        "只输出 JSON，不要其他文字。"
    )

    user_content = (
        f"原始问题：{query}\n\n"
        f"我的 Phase A 分析：\n{self_analysis[:4000]}\n\n"
        f"其他专家分析：\n{peer_text}"
    )

    try:
        response = _call_llm(
            caller=_caller,
            trace_id=trace_id,
            model=_model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=get_config_float('llm.temperature_agent', 0.3),
            max_tokens=get_config_int('llm.max_tokens_agent', 8000),
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[trace:{trace_id}] [{agent['name']}] cross_review_opinion LLM 调用失败: {e}")
        raw = ""

    # 解析 JSON
    opinion = {"agreements": [], "disagreements": [], "additions": [], "summary": ""}
    analysis_text = ""
    if raw:
        # 去除 markdown 代码块
        cleaned = raw
        if "```" in cleaned:
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else parts[0]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                opinion = {
                    "agreements": parsed.get("agreements", []) or [],
                    "disagreements": parsed.get("disagreements", []) or [],
                    "additions": parsed.get("additions", []) or [],
                    "summary": parsed.get("summary", "") or "",
                }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[trace:{trace_id}] [{agent['name']}] cross_review_opinion JSON 解析失败: {e}")

        # M3：强制魔鬼代言人 — 若 disagreements 为空且存在其他专家，二次提示强制反驳
        has_peers = len(peer_analyses) > 1  # peer_analyses 含自己，>1 表示有其他专家
        try:
            force_devil = get_config_bool("agent.force_devil_advocate_enabled", True)
        except Exception:
            force_devil = True
        if (
            force_devil
            and has_peers
            and len(opinion.get("disagreements", [])) == 0
            and raw
        ):
            try:
                from db.config import get_config as _get_cfg
                devil_model = _get_cfg("agent.devil_advocate_model", "") or None
            except Exception:
                devil_model = None
            retry_system = (
                "你是魔鬼代言人。前面的交叉审阅未能提出任何质疑，这是不合格的。"
                "即使其他专家方向一致，也必须找到至少 1 个潜在盲点或风险。请从以下角度反思：\n"
                "- 是否存在价值陷阱？（低估可能有更深原因）\n"
                "- 是否低估了下行风险？（黑天鹅/尾部风险）\n"
                "- 数据是否有幸存者偏差？（只看成功的样本）\n"
                "- 推理链是否有逻辑跳跃？（因果倒置/相关≠因果）\n"
                "- 是否遗漏了重要前置假设？\n"
                "输出 JSON：{\"disagreements\": [{\"peer\": \"专家名\", \"point\": \"质疑的观点\", "
                "\"reason\": \"反驳理由\"}], \"summary\": \"魔鬼代言人总结\"}\n"
                "只输出 JSON，必须包含至少 1 条 disagreement。"
            )
            retry_user = (
                f"原始问题：{query}\n\n"
                f"其他专家分析：\n{peer_text[:6000]}"
            )
            try:
                retry_resp = _call_llm(
                    caller=_caller + ":devil",
                    trace_id=trace_id,
                    model=devil_model or _model,
                    messages=[
                        {"role": "system", "content": retry_system},
                        {"role": "user", "content": retry_user},
                    ],
                    temperature=0.5,
                    max_tokens=400,
                )
                retry_raw = retry_resp.choices[0].message.content.strip()
                cleaned_r = retry_raw
                if "```" in cleaned_r:
                    parts_r = cleaned_r.split("```")
                    cleaned_r = parts_r[1] if len(parts_r) > 1 else parts_r[0]
                    if cleaned_r.startswith("json"):
                        cleaned_r = cleaned_r[4:]
                    cleaned_r = cleaned_r.strip()
                retry_parsed = json.loads(cleaned_r)
                if isinstance(retry_parsed, dict):
                    retry_dis = retry_parsed.get("disagreements", []) or []
                    if retry_dis:
                        opinion["disagreements"] = retry_dis
                        if retry_parsed.get("summary"):
                            opinion["summary"] = retry_parsed["summary"]
                        logger.info(
                            f"[trace:{trace_id}] [{agent['name']}] 魔鬼代言人强制反驳成功: "
                            f"{len(retry_dis)} 条质疑"
                        )
            except Exception as devil_err:
                logger.warning(
                    f"[trace:{trace_id}] [{agent['name']}] 魔鬼代言人二次调用失败: {devil_err}"
                )

        # 构造兼容的 analysis 文本
        parts = []
        if opinion["summary"]:
            parts.append(opinion["summary"])
        if opinion["agreements"]:
            parts.append("认同：" + "；".join(opinion["agreements"]))
        if opinion["disagreements"]:
            dis_lines = []
            for d in opinion["disagreements"]:
                if isinstance(d, dict):
                    dis_lines.append(f"对{d.get('peer','')}的{d.get('point','')}有疑问（{d.get('reason','')}）")
                else:
                    dis_lines.append(str(d))
            parts.append("质疑：" + "；".join(dis_lines))
        if opinion["additions"]:
            parts.append("补充：" + "；".join(opinion["additions"]))
        analysis_text = "\n".join(parts) if parts else raw[:8000]

    if not analysis_text:
        analysis_text = "交叉审阅完成，请参考其他专家分析。"

    duration_ms = int((time.time() - start_time) * 1000)

    # P0: 写入 agent_runs（cross_review 路径补齐）— 失败不影响主流程
    if conversation_id and message_id:
        try:
            create_agent_run(
                conversation_id=conversation_id,
                message_id=message_id,
                agent_key=agent_key,
                agent_name=agent["name"],
                query=query,
                result=analysis_text[:4000],
                tool_calls="",
                duration_ms=duration_ms,
                trace_id=trace_id,
                status="success",
            )
        except Exception as log_err:
            logger.warning(f"[cross_review] agent_runs 写入失败 ({agent_key}): {log_err}")

    return {
        "agent_key": agent_key,
        "agent": agent["name"],
        "icon": agent["icon"],
        "analysis": analysis_text,
        "opinion": opinion,
        "tool_calls": [],  # 空数组，保持字段兼容
        "duration_ms": duration_ms,
        "is_cross_review": True,
    }


def _build_portfolio_summary(max_chars: int = 800) -> str:
    """构建用户当前持仓摘要，供仲裁法官做"持仓现状对照"。

    摘要包含：总资产、现金、重仓基金（按市值排序，前 5）、行业集中度、近 30 天交易频率。
    控制在 max_chars 字符内，超长时只保留前 5 大持仓。

    返回空字符串表示无持仓或查询失败（调用方应优雅降级）。
    """
    try:
        from db.portfolio import get_portfolio_summary, list_transactions
    except ImportError:
        return ""

    try:
        summary = get_portfolio_summary()
        holdings = summary.get("holdings", [])
        if not holdings:
            return ""

        total_assets = summary.get("total_assets", 0)
        cash = summary.get("cash_balance", 0)
        total_value = summary.get("total_value", 0)
        total_cost = summary.get("total_cost", 0)
        total_profit = summary.get("total_profit", 0)
        profit_rate = summary.get("profit_rate", 0)

        # 按 current_value 降序取前 5 大持仓
        active = [h for h in holdings if (h.get("shares") or 0) > 0]
        active.sort(key=lambda h: (h.get("current_value") or 0), reverse=True)
        top_holdings = active[:5]

        lines = [
            f"- 总资产：约 {total_assets/10000:.1f} 万",
            f"- 现金：约 {cash/10000:.1f} 万",
            f"- 持仓市值：约 {total_value/10000:.1f} 万（成本 {total_cost/10000:.1f} 万，盈亏 {total_profit/10000:+.1f} 万 / {profit_rate:+.1%}）",
            f"- 重仓基金（按市值排序，前 {len(top_holdings)}）：",
        ]
        for i, h in enumerate(top_holdings, 1):
            name = h.get("fund_name", "未知")
            cost = h.get("total_cost", 0) or 0
            value = h.get("current_value", 0) or 0
            rate = h.get("profit_rate", 0) or 0
            lines.append(f"  {i}. {name}：成本 {cost/10000:.2f} 万，市值 {value/10000:.2f} 万，盈亏 {rate:+.1%}")

        # 行业集中度（按 fund_category 聚合市值占比）
        cat_values = {}
        for h in active:
            cat = h.get("fund_category") or "equity"
            cat_values[cat] = cat_values.get(cat, 0) + (h.get("current_value") or 0)
        if total_value > 0 and cat_values:
            cat_str = "、".join(
                f"{cat} {v/total_value*100:.0f}%"
                for cat, v in sorted(cat_values.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"- 行业集中度（按市值）：{cat_str}")

        # 近 30 天交易频率
        try:
            txns = list_transactions()
            lines.append(f"- 历史交易记录：共 {len(txns)} 条")
        except Exception:
            pass

        text = "\n".join(lines)
        return text[:max_chars] if len(text) > max_chars else text
    except Exception as e:
        logger.warning(f"构建持仓摘要失败: {e}")
        return ""


# ── 三轮圆桌讨论：Phase A (独立分析) → Phase B (交叉质询) → Phase C (修正结论) ──

def run_round_table_discussion(query: str, specialist_results: list,
                                trace_id: str = "", model: str = None,
                                prebuilt_context: str = "") -> list:
    """三轮圆桌讨论增强：交叉质询 + 修正结论。

    流程：
    - Phase A（已完成）：专家各自独立分析（specialist_results 传入）
    - Phase B（第二轮）：每个专家看到其他专家的完整分析，提出 1-2 个关键质疑点
    - Phase C（第三轮）：各专家根据质疑简短修正自己的结论

    Args:
        query: 原始用户问题
        specialist_results: Phase A 的专家分析结果列表
        trace_id: 追踪 ID
        model: 可选模型覆盖
        prebuilt_context: 预构建的持仓+估值上下文

    Returns:
        更新后的 specialist_results 列表（包含 Phase B/C 结果）
    """
    if not trace_id:
        import uuid as _uuid
        trace_id = f"roundtable-{_uuid.uuid4().hex[:8]}"

    if len(specialist_results) < 2:
        logger.info(f"[trace:{trace_id}] 圆桌讨论跳过：专家数 < 2")
        return specialist_results

    # 过滤出 Phase A 的原始结果（非 cross_review、非 arbitration）
    phase_a_results = [
        sr for sr in specialist_results
        if not sr.get("is_cross_review") and not sr.get("is_arbitration")
    ]
    if len(phase_a_results) < 2:
        return specialist_results

    peer_analyses = {sr["agent_key"]: sr["analysis"] for sr in phase_a_results}

    # ── Phase B: 交叉质询 ──
    # 每个专家收到其他专家的完整分析（截断到 6000 字），提出 1-2 个关键质疑点
    logger.info(f"[trace:{trace_id}] 圆桌讨论 Phase B: 交叉质询，{len(phase_a_results)} 个专家")
    phase_b_results = []
    for sr in phase_a_results:
        agent_key = sr["agent_key"]
        try:
            # 使用 run_specialist_with_context 进行交叉质询
            # 它已经会将 peer_analyses 截断到 6000 字
            cr_result = run_specialist_with_context(
                agent_key, query, peer_analyses, max_turns=2,
                prebuilt_context=prebuilt_context,
                model=model,
                trace_id=trace_id,
            )
            phase_b_results.append(cr_result)
            specialist_results.append(cr_result)
        except Exception as e:
            logger.error(f"[trace:{trace_id}] Phase B 交叉质询 {agent_key} 失败: {e}")

    # ── Phase C: 修正结论 ──
    # 每个专家根据 Phase B 的质疑，简短修正自己的结论（单次 LLM 调用，无工具）
    if phase_b_results:
        logger.info(f"[trace:{trace_id}] 圆桌讨论 Phase C: 修正结论")

        for sr in phase_a_results:
            agent_key = sr["agent_key"]
            agent = load_specialist_agents().get(agent_key)
            if not agent:
                continue

            # 收集其他专家对该专家的质疑
            challenges_to_me = []
            for cr in phase_b_results:
                if cr["agent_key"] == agent_key:
                    continue
                # 提取质疑点
                opinion = cr.get("opinion", {})
                disagreements = opinion.get("disagreements", [])
                for d in disagreements:
                    if isinstance(d, dict):
                        target_peer = d.get("peer", "")
                        # 检查是否针对当前专家
                        if agent["name"] in target_peer or target_peer in agent["name"]:
                            challenges_to_me.append({
                                "from": cr.get("agent", ""),
                                "point": d.get("point", ""),
                                "reason": d.get("reason", ""),
                            })

            if not challenges_to_me:
                continue

            # 构建修正 prompt
            challenge_text = "\n".join(
                f"- {c['from']}质疑: {c['point']}（理由: {c['reason']}）"
                for c in challenges_to_me[:3]  # 最多 3 个质疑
            )

            _caller = f"specialist:{agent_key}:phase_c"
            _model = model or MODEL
            system_content = (
                f"你是{agent['name']}。在圆桌讨论中，其他专家对你的分析提出了以下质疑：\n\n"
                f"{challenge_text}\n\n"
                f"请根据这些质疑，简短修正你的结论（200-500字）。要求：\n"
                f"1. 如果质疑合理，承认并修正\n"
                f"2. 如果质疑不合理，用数据或逻辑反驳\n"
                f"3. 给出修正后的最终结论\n"
            )

            try:
                response = _call_llm(
                    caller=_caller,
                    trace_id=trace_id,
                    model=_model,
                    messages=[
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": f"原始问题：{query}\n\n你的原始分析：\n{sr['analysis'][:8000]}\n\n请给出修正后的结论。"},
                    ],
                    temperature=get_config_float('llm.temperature_agent', 0.3),
                    max_tokens=get_config_int('llm.max_tokens_agent', 8000),
                )
                revised = response.choices[0].message.content or ""
                if revised and len(revised) > 50:
                    revision_result = {
                        "agent_key": agent_key,
                        "agent": agent["name"],
                        "icon": agent["icon"],
                        "analysis": revised,
                        "tool_calls": [],
                        "duration_ms": 0,
                        "is_cross_review": True,
                        "is_phase_c_revision": True,
                    }
                    specialist_results.append(revision_result)
                    logger.info(f"[trace:{trace_id}] Phase C: {agent['name']} 修正结论完成")
            except Exception as e:
                logger.error(f"[trace:{trace_id}] Phase C 修正 {agent_key} 失败: {e}")

    return specialist_results
