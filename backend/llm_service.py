"""大模型服务 — 封装 LLM API 调用，提供文章解读、图片分析和投资建议"""

import base64
import json
import logging
from functools import wraps
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from config import get_llm_config

logger = logging.getLogger(__name__)


# ── LLM 调用重试装饰器 ──────────────────────────────────

def _is_retryable(e):
    """判断异常是否需要重试。"""
    if isinstance(e, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(e, APIStatusError) and e.status_code in (500, 502, 503, 529):
        return True
    return False


_llm_retry = retry(
    retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError, APIStatusError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

_api_key, _base_url, _model = get_llm_config()
client = OpenAI(api_key=_api_key, base_url=_base_url)
MODEL = _model

SYSTEM_PROMPT = """<role>你是一位专业的投资分析师。请根据提供的微信公众号文章内容和市场数据，给出客观的投资分析。</role>

<instructions>
分析要求：
1. **文章核心观点** — 用 2-3 句话概括作者的核心论点
2. **涉及标的** — 列出文章提到的股票/基金/指数，附代码
3. **估值分析** — 结合提供的行情数据，分析当前估值水平
4. **风险提示** — 指出文章可能忽略的风险
5. **综合建议** — 给出 actionable 的投资建议
</instructions>

<constraints>
- 保持客观中立，不要盲目跟随文章观点
- 如果文章数据过时，指出时间差异
- 投资建议需加免责声明
- 输出格式：Markdown，使用清晰的标题层级
</constraints>"""


def _call_llm(caller: str = "", **kwargs):
    """统一的 LLM 调用入口，带指数退避重试和 token 记录。"""
    @_llm_retry
    def _do_call():
        resp = client.chat.completions.create(**kwargs)
        if resp.usage:
            logger.info(
                f"LLM tokens — prompt: {resp.usage.prompt_tokens}, "
                f"completion: {resp.usage.completion_tokens}, "
                f"total: {resp.usage.total_tokens}, model: {resp.model}, caller: {caller}"
            )
            _record_token_usage(resp.usage, resp.model, caller)
        return resp
    return _do_call()


_token_log_conn = None

def _record_token_usage(usage, model: str, caller: str = ""):
    """将 token 用量写入数据库。"""
    try:
        from db import _get_conn
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                caller TEXT DEFAULT '',
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # 兼容已有表
        try:
            conn.execute("ALTER TABLE token_usage ADD COLUMN caller TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute(
            "INSERT INTO token_usage (model, caller, prompt_tokens, completion_tokens, total_tokens, created_at) VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))",
            (model, caller, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to record token usage: {e}")


def analyze_article(title: str, content: str, market_data: dict = None) -> str:
    """
    让 LLM 解读公众号文章并生成投资分析。

    参数:
        title: 文章标题
        content: 文章正文（纯文本）
        market_data: 可选的行情数据摘要

    返回:
        LLM 生成的分析文本（Markdown 格式）
    """
    user_msg = f"## 文章标题\n{title}\n\n## 文章正文\n{content[:8000]}"

    if market_data:
        user_msg += f"\n\n## 相关行情数据\n{market_data}"

    response = _call_llm(
        caller="article_analysis",
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=4000,
    )

    return response.choices[0].message.content


def analyze_article_stream(title: str, content: str, market_data: dict = None):
    """
    流式版本的 analyze_article，返回生成器。

    用法:
        for chunk in analyze_article_stream(title, content):
            print(chunk, end="", flush=True)
    """
    user_msg = f"## 文章标题\n{title}\n\n## 文章正文\n{content[:8000]}"

    if market_data:
        user_msg += f"\n\n## 相关行情数据\n{market_data}"

    @_llm_retry
    def _stream():
        return client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=4000,
            stream=True,
        )

    stream = _stream()
    for chunk in stream:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def chat_about_investment(question: str, context: str = "", valuation_context: str = "") -> str:
    """
    自由问答模式 — 用户针对分析结果追问，自动关联估值数据。

    参数:
        question: 用户问题
        context: 上下文（之前的分析结果等）
        valuation_context: 自动检索到的估值数据上下文
    """
    system_prompt = """你是一位专业的投资分析师。请简洁明了地回答用户问题。

如果提供了指数估值数据，请结合数据分析：
- 估值高低（分位点 <30% 为低估，30-70% 为合理，>70% 为高估）
- 趋势变化（近期估值是上升还是下降）
- 风险提示（接近危险值时提醒风险，接近机会值时提示机会）

回答时引用具体数字，给出明确的分析观点。"""

    messages = [{"role": "system", "content": system_prompt}]

    # 估值数据优先级更高，放在前面
    if valuation_context:
        messages.append({"role": "user", "content": f"相关指数估值数据：\n{valuation_context}"})
        messages.append({"role": "assistant", "content": "好的，我已了解相关指数的估值数据，请提问。"})

    if context:
        messages.append({"role": "user", "content": f"文章分析背景：\n{context[:3000]}"})
        messages.append({"role": "assistant", "content": "好的，我已了解文章分析背景。"})

    messages.append({"role": "user", "content": question})

    response = _call_llm(
        caller="chat",
        model=MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=2000,
    )

    return response.choices[0].message.content


def chat_with_agent(
    agent_prompt: str,
    messages: list[dict],
    rag_context: str = "",
) -> str:
    """Agent 对话模式，支持多轮历史 + RAG 上下文。

    参数:
        agent_prompt: Agent 的 system prompt
        messages: 完整对话历史 [{"role": "user"/"assistant", "content": "..."}]
        rag_context: RAG 检索到的知识上下文
    """
    llm_messages = []

    # System prompt + RAG 上下文
    system_content = agent_prompt
    if rag_context:
        system_content += f"\n\n以下是从知识库中检索到的相关信息，请结合回答：\n{rag_context[:4000]}"
    llm_messages.append({"role": "system", "content": system_content})

    # 对话历史（最近 20 条，避免超长）
    for msg in messages[-20:]:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    response = _call_llm(
        caller="agent_chat",
        model=MODEL,
        messages=llm_messages,
        temperature=0.3,
        max_tokens=2000,
    )

    return response.choices[0].message.content


IMAGE_ANALYSIS_PROMPT = """你是一位专业的金融数据分析师。请分析这张图片，提取其中的关键信息。

要求：
1. 识别图片类型（K线图、估值图、数据表格、文章截图等）
2. 提取所有可见的数字、指标、指数名称
3. 如果是估值图，提取：指数名称、当前值、百分位、历史区间
4. 如果是K线图，提取：股票/指数名称、当前价格、趋势判断
5. 如果是表格，按行列结构提取数据

输出格式：JSON 结构化数据，包含以下字段：
{
  "image_type": "图片类型",
  "title": "图表/数据标题",
  "data": { ... },
  "summary": "一句话总结"
}

只输出 JSON，不要其他文字。"""


def analyze_image(image_path: str) -> dict:
    """分析单张图片，提取结构化数据（使用 mimo-v2-omni 视觉模型）。"""
    import json as _json

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    ext = image_path.rsplit(".", 1)[-1].lower()
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "jpeg")

    vision_client = OpenAI(api_key=_api_key, base_url=_base_url)

    response = vision_client.chat.completions.create(
        model="mimo-v2-omni",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": IMAGE_ANALYSIS_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{img_b64}"}},
            ],
        }],
        temperature=0.1,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        return {"raw_response": raw}


def analyze_images_batch(image_paths: list[str]) -> list[dict]:
    """批量分析多张图片。"""
    results = []
    for path in image_paths:
        try:
            result = analyze_image(path)
            result["image_path"] = path
            results.append(result)
        except Exception as e:
            results.append({"image_path": path, "error": str(e)})
    return results


# ── 多 Agent 对话（带工具调用）──────────────────────────────────


ORCHESTRATOR_PROMPT = """<role>你是投资分析助手，擅长分析指数估值、解读市场数据、引用专业观点来回答用户的投资问题。</role>

<instructions>
1. 理解用户问题的核心意图
2. 如果需要数据支撑，主动调用工具获取真实数据（绝对不要编造数据）
3. 如果需要知识参考，用 search_knowledge 检索相关文章和文档
4. 综合所有收集到的信息，给出结构化的分析回答
</instructions>

<principles>
- 有数据就用数据说话，标注数据来源
- 引用作者观点时注明出处
- 给出明确的判断和建议，不要含糊其辞
- 如果数据不足以判断，坦诚说明
- 回答使用 Markdown 格式，层次清晰
</principles>

<valuation_reference>
- 百分位 <30%：低估区间，适合分批买入
- 百分位 30%-70%：合理区间，持有观望
- 百分位 >70%：高估区间，注意风险，考虑减仓
- z-score >2：极度高估，风险警示
- z-score <-2：极度低估，机会提示
</valuation_reference>

<tool_guidance>
- 估值相关问题优先用 query_valuation，数据最可靠
- 需要最新市场动态、政策变化时用 web_search 补充（知识库数据不一定最新）
- web_search 结果仅作参考，以估值数据和知识库为准
</tool_guidance>

<examples>
用户: 沪深300现在估值怎么样？
助手: 先调用 query_valuation 查询沪深300估值数据，然后基于返回的PE、PB、百分位数据给出判断。
</examples>"""


def _parse_tool_args(raw_args: str, tool_name: str) -> dict:
    """解析工具调用参数，带日志和修复尝试。"""
    # 1. 直接解析
    try:
        return json.loads(raw_args)
    except json.JSONDecodeError:
        pass

    # 2. 尝试去除 markdown 代码块
    cleaned = raw_args.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # 3. 尝试提取第一个 {...}
    import re
    match = re.search(r'\{[^{}]*\}', raw_args, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 4. 全部失败，记录并返回空
    logger.warning(f"Tool args JSON 解析失败: tool={tool_name}, raw={raw_args[:200]}")
    return {}


def chat_with_tools(
    agent_prompt: str,
    messages: list[dict],
    rag_context: str = "",
) -> dict:
    """带工具调用的对话循环。

    流程：
    1. 构建 system message（agent_prompt + RAG 上下文）
    2. 调用 LLM（带 tools 定义）
    3. 如果 LLM 返回 tool_calls → 执行工具 → 将结果加入消息 → 回到 2
    4. 如果 LLM 返回 text → 结束，返回最终回答
    5. 最多循环 MAX_TURNS 次，防止无限循环

    返回:
        {
            "answer": "最终回答文本",
            "tool_calls": [{"name": ..., "arguments": ..., "result_preview": ...}],
            "turns": 实际轮次数
        }
    """
    from tools import TOOLS, execute_tool

    # 构建 system message
    system_content = agent_prompt or ORCHESTRATOR_PROMPT
    if rag_context:
        system_content += f"\n\n以下是从知识库中预先检索到的参考信息：\n{rag_context[:3000]}"

    llm_messages = [{"role": "system", "content": system_content}]

    # 对话历史（最近 20 条）
    for msg in messages[-20:]:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    MAX_TURNS = 5
    tool_calls_log = []

    for turn in range(MAX_TURNS):
        try:
            response = _call_llm(
                caller="agent_tools",
                model=MODEL,
                messages=llm_messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            err_msg = str(e)
            logger.error(f"LLM 调用异常 (turn {turn}): {err_msg}")
            # reasoning_content 相关错误或 tool calling 不支持 → 回退
            if any(kw in err_msg.lower() for kw in ["tool", "function", "reasoning", "thinking"]):
                logger.warning("模型不兼容当前调用方式，回退到普通模式")
                return _fallback_chat(agent_prompt, messages, rag_context)
            raise

        msg = response.choices[0].message

        # 没有工具调用 → 最终回答
        if not msg.tool_calls:
            return {
                "answer": msg.content or "",
                "tool_calls": tool_calls_log,
                "turns": turn + 1,
            }

        # 有工具调用 → 执行工具
        # MIMO thinking mode 需要 reasoning_content 回传
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
        # 尝试获取 reasoning_content（MIMO thinking mode）
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
            result = execute_tool(tc.function.name, args)

            # 截断过长的结果
            if len(result) > 4000:
                result = result[:4000] + "\n... (结果过长，已截断)"

            tool_calls_log.append({
                "name": tc.function.name,
                "arguments": args,
                "result_preview": result[:300],
            })

            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # 超过最大轮次，做最后一次不带 tools 的调用来总结
    try:
        llm_messages.append({
            "role": "user",
            "content": "请根据以上工具调用的结果，给出最终的综合分析回答。",
        })
        response = _call_llm(
            caller="agent_tools",
            model=MODEL,
            messages=llm_messages,
            temperature=0.3,
            max_tokens=2000,
        )
        final_answer = response.choices[0].message.content or ""
    except Exception:
        final_answer = "分析过程较长，请参考以上工具调用结果。"

    return {
        "answer": final_answer,
        "tool_calls": tool_calls_log,
        "turns": MAX_TURNS,
    }


def _fallback_chat(agent_prompt: str, messages: list[dict], rag_context: str = "") -> dict:
    """当模型不支持 function calling 时，回退到普通对话模式。"""
    answer = chat_with_agent(agent_prompt, messages, rag_context)
    return {
        "answer": answer,
        "tool_calls": [],
        "turns": 1,
        "fallback": True,
    }
