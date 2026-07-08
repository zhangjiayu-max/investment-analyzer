"""大模型服务 — 封装 LLM API 调用，提供文章解读、图片分析和投资建议"""

import base64
import json
import logging
from functools import wraps
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception, before_sleep_log
from config import get_llm_config, get_llm_fallback_config, ARBITRATION_API_KEY, ARBITRATION_BASE_URL, ARBITRATION_MODEL
from db.config import get_config_float, get_config_int

logger = logging.getLogger(__name__)


# ── SSE 活跃连接计数 ──────────────────────────────────────

_sse_active = 0


def _active_sse_count() -> int:
    """返回当前活跃的 SSE 连接数。"""
    return _sse_active


# ── LLM 调用重试装饰器 ──────────────────────────────────

def _is_retryable(e):
    """判断异常是否需要重试。"""
    if isinstance(e, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(e, APIStatusError) and e.status_code in (500, 502, 503, 529):
        return True
    return False


# 缺口 10：指数退避重试（修正 + 抖动）
# - 用 retry_if_exception(predicate) 替代 exception_type，避免误重试 4xx（400/404/422）
# - wait_exponential_jitter 加随机抖动，避免多并发请求同步重试造成惊群
_llm_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=2, max=30, jitter=2),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

_api_key, _base_url, _model = get_llm_config()
# 超时保护：3 分钟，避免 MIMO API hang 住导致 pipeline 死锁（对话 99 案例）
client = OpenAI(api_key=_api_key, base_url=_base_url, timeout=180.0)
MODEL = _model

# 兜底客户端（主用失败时切换）
_fallback_config = get_llm_fallback_config()
_fallback_client = OpenAI(api_key=_fallback_config[0], base_url=_fallback_config[1], timeout=180.0) if _fallback_config else None
_fallback_model = _fallback_config[2] if _fallback_config else None

# 仲裁 Agent 客户端（高级推理模型，如 DeepSeek R1）
_arbitration_client = OpenAI(api_key=ARBITRATION_API_KEY, base_url=ARBITRATION_BASE_URL, timeout=180.0) if ARBITRATION_API_KEY else None
_arbitration_model = ARBITRATION_MODEL if ARBITRATION_API_KEY else None

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


def _call_llm(caller: str = "", trace_id: str = "", **kwargs):
    """统一的 LLM 调用入口，带指数退避重试、token 记录和兜底切换。"""
    @_llm_retry
    def _do_call():
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            # 主用失败，尝试兜底
            if _fallback_client and _fallback_model:
                logger.warning(f"[trace:{trace_id}] 主用 LLM 失败 ({e.__class__.__name__}: {e})，切换到兜底: {_fallback_model}")
                kwargs["model"] = _fallback_model
                resp = _fallback_client.chat.completions.create(**kwargs)
            else:
                raise
        if resp.usage:
            logger.info(
                f"[trace:{trace_id}] LLM tokens — prompt: {resp.usage.prompt_tokens}, "
                f"completion: {resp.usage.completion_tokens}, "
                f"total: {resp.usage.total_tokens}, model: {resp.model}, caller: {caller}"
            )
            _record_token_usage(resp.usage, resp.model, caller, trace_id=trace_id)
        return resp
    return _do_call()


async def call_llm_async(caller: str = "", **kwargs):
    """异步版本的 _call_llm，可直接 await。"""
    import asyncio
    return await asyncio.to_thread(lambda: _call_llm(caller=caller, **kwargs))


def _call_llm_stream(caller: str = "", trace_id: str = "", **kwargs):
    """流式 LLM 调用，生成器逐 chunk yield。

    每个 chunk yield dict: {"content": str, "reasoning": str}
    （content/reasoning 可能为空串，表示该 chunk 无此部分）。
    流式 usage 常为 None，仅在末包有值时记录 token（best-effort）。

    用法:
        for chunk in _call_llm_stream(caller="orchestrator", model=MODEL, messages=msgs):
            if chunk["content"]:    # 最终答案增量
                ...
            if chunk["reasoning"]:  # 思考过程增量
                ...
    """
    kwargs["stream"] = True

    used_client = client
    try:
        stream = used_client.chat.completions.create(**kwargs)
    except Exception as e:
        if _fallback_client and _fallback_model:
            logger.warning(f"[trace:{trace_id}] 主用 LLM 流式失败 ({e.__class__.__name__}: {e})，切换兜底: {_fallback_model}")
            kwargs["model"] = _fallback_model
            used_client = _fallback_client
            stream = used_client.chat.completions.create(**kwargs)
        else:
            raise

    last_usage = None
    last_model = kwargs.get("model", MODEL)
    collected_content = []  # 收集所有 content 用于估算
    for chunk in stream:
        # 末包可能携带 usage
        if getattr(chunk, "usage", None):
            last_usage = chunk.usage
        if getattr(chunk, "model", None):
            last_model = chunk.model
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None) or ""
        # reasoning_content 提取（兼容 model_extra，MIMO/DeepSeek thinking mode）
        reasoning = getattr(delta, "reasoning_content", None)
        if not reasoning and hasattr(delta, "model_extra") and delta.model_extra:
            reasoning = delta.model_extra.get("reasoning_content")
        reasoning = reasoning or ""
        if content:
            collected_content.append(content)
        if content or reasoning:
            yield {"content": content, "reasoning": reasoning}

    # 末包 token 记录（best-effort，部分 provider 流式不返回 usage）
    if last_usage:
        try:
            _record_token_usage(last_usage, last_model, caller, trace_id=trace_id)
        except Exception:
            pass
    else:
        # 流式 usage 为 None 时，用 tiktoken 估算
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model("gpt-4")
            msgs = kwargs.get("messages", [])
            prompt_tokens = sum(len(enc.encode(m.get("content", ""))) for m in msgs if m.get("content"))
            completion_tokens = sum(len(enc.encode(c)) for c in collected_content)
            if prompt_tokens or completion_tokens:
                class _EstUsage:
                    def __init__(self, p, c):
                        self.prompt_tokens = p
                        self.completion_tokens = c
                        self.total_tokens = p + c
                last_usage = _EstUsage(prompt_tokens, completion_tokens)
                _record_token_usage(last_usage, last_model, caller, trace_id=trace_id)
        except Exception:
            pass


def call_arbitration_llm(trace_id: str = "", **kwargs):
    """仲裁 Agent 专用 LLM 调用（高级推理模型，如 DeepSeek R1）。未配置则返回 None。

    支持通过 system_config 'arbitration.model' 覆盖默认模型名。
    """
    if not _arbitration_client or not _arbitration_model:
        return None

    # 从数据库配置读取差异化仲裁模型
    try:
        from db.config import get_config
        config_model = get_config('arbitration.model', '')
        effective_model = config_model.strip() if config_model.strip() else _arbitration_model
    except Exception:
        effective_model = _arbitration_model

    kwargs.setdefault("model", effective_model)
    try:
        resp = _arbitration_client.chat.completions.create(**kwargs)
        if resp.usage:
            logger.info(
                f"[trace:{trace_id}] Arbitration LLM tokens — prompt: {resp.usage.prompt_tokens}, "
                f"completion: {resp.usage.completion_tokens}, "
                f"total: {resp.usage.total_tokens}, model: {resp.model}"
            )
            _record_token_usage(resp.usage, resp.model, "arbitration", trace_id=trace_id)
        return resp
    except Exception as e:
        logger.error(f"[trace:{trace_id}] 仲裁 LLM 调用异常: {e}")
        return None


_token_log_conn = None

def _record_token_usage(usage, model: str, caller: str = "", trace_id: str = ""):
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
        for col in ['caller', 'trace_id']:
            try:
                conn.execute(f"ALTER TABLE token_usage ADD COLUMN {col} TEXT DEFAULT ''")
            except Exception:
                pass
        conn.execute(
            "INSERT INTO token_usage (model, caller, prompt_tokens, completion_tokens, total_tokens, created_at, trace_id) VALUES (?, ?, ?, ?, ?, datetime('now','localtime'), ?)",
            (model, caller, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, trace_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[trace:{trace_id}] Failed to record token usage: {e}")

    # 成本治理：同步记录到 cost_logs 表
    try:
        from infra.cost_tracker import cost_tracker
        cost_tracker.record(caller or "unknown", model,
                            usage.prompt_tokens or 0, usage.completion_tokens or 0)
    except Exception:
        pass


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
        temperature=get_config_float('llm.temperature_default', 0.3),
        max_tokens=get_config_int('llm.max_tokens_chat', 8000),
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
            temperature=get_config_float('llm.temperature_default', 0.3),
            max_tokens=get_config_int('llm.max_tokens_chat', 8000),
            stream=True,
        )

    stream = _stream()
    _collected = []
    _usage = None
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            _collected.append(chunk.choices[0].delta.content)
            yield chunk.choices[0].delta.content
        # 取末包 usage（部分模型流式会返回）
        if hasattr(chunk, "usage") and chunk.usage:
            _usage = chunk.usage

    # A1 修复：流式结束后记录 token，优先用 API 返回 usage，否则 tiktoken 估算
    if not _usage:
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model("gpt-4")
            prompt_tokens = len(enc.encode(SYSTEM_PROMPT + user_msg))
            completion_tokens = len(enc.encode("".join(_collected)))
            if prompt_tokens or completion_tokens:
                class _EstUsage:
                    def __init__(self, p, c):
                        self.prompt_tokens = p
                        self.completion_tokens = c
                        self.total_tokens = p + c
                _usage = _EstUsage(prompt_tokens, completion_tokens)
        except Exception:
            pass
    if _usage:
        _record_token_usage(_usage, MODEL, caller="article_analysis_stream")


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
        messages.append({"role": "user", "content": f"文章分析背景：\n{context[:6000]}"})
        messages.append({"role": "assistant", "content": "好的，我已了解文章分析背景。"})

    messages.append({"role": "user", "content": question})

    response = _call_llm(
        caller="chat",
        model=MODEL,
        messages=messages,
        temperature=get_config_float('llm.temperature_default', 0.3),
        max_tokens=get_config_int('llm.max_tokens_analysis', 8000),
    )

    return response.choices[0].message.content


def chat_with_agent(
    agent_prompt: str,
    messages: list[dict],
    rag_context: str = "",
    max_tokens: int = None,
) -> str:
    """Agent 对话模式，支持多轮历史 + RAG 上下文。

    参数:
        agent_prompt: Agent 的 system prompt
        messages: 完整对话历史 [{"role": "user"/"assistant", "content": "..."}]
        rag_context: RAG 检索到的知识上下文
        max_tokens: 最大输出 token 数（None 则从配置读取）
    """
    if max_tokens is None:
        from db.config import get_config_int
        max_tokens = get_config_int('llm.max_tokens_agent', 4000)
    llm_messages = []

    # System prompt + RAG 上下文
    system_content = agent_prompt
    if rag_context:
        from db.config import get_config_int
        _rag_trunc = get_config_int('truncation.rag_context', 4000)
        system_content += f"\n\n以下是从知识库中检索到的相关信息，请结合回答：\n{rag_context[:_rag_trunc]}"
    llm_messages.append({"role": "system", "content": system_content})

    # 对话历史（从配置读取保留条数，避免超长）
    from db.config import get_config_int
    _history_n = get_config_int('truncation.history_messages', 20)
    for msg in messages[-_history_n:]:
        llm_messages.append({"role": msg["role"], "content": msg["content"]})

    try:
        response = _call_llm(
            caller="agent_chat",
            model=MODEL,
            messages=llm_messages,
            temperature=get_config_float('llm.temperature_default', 0.3),
            max_tokens=max_tokens,
        )

        result = response.choices[0].message.content
        logger.info(f"[chat_with_agent] LLM response length: {len(result) if result else 0}")
        if result is None:
            logger.warning("[chat_with_agent] LLM returned None content")
            return ""
        return result
    except Exception as e:
        logger.error(f"[chat_with_agent] LLM call failed: {e}")
        raise


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
    """分析单张图片，提取结构化数据（使用 VISION_MODEL 配置的视觉模型）。

    复用 image_parser._call_vision()，运行时从 DB 读取视觉模型配置，
    支持 Ollama / MiMo 等多 provider 切换，无需重启。
    """
    import json as _json
    from services.image_parser import _call_vision

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    ext = image_path.rsplit(".", 1)[-1].lower()
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "jpeg")

    raw = _call_vision(IMAGE_ANALYSIS_PROMPT, img_b64, mime)

    raw = raw.strip()
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
        system_content += f"\n\n以下是从知识库中预先检索到的参考信息：\n{rag_context[:6000]}"

    llm_messages = [{"role": "system", "content": system_content}]

    # 对话历史（从配置读取保留条数）
    _history_n = get_config_int('truncation.history_messages', 20)
    for msg in messages[-_history_n:]:
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
                temperature=get_config_float('llm.temperature_default', 0.3),
                max_tokens=get_config_int('llm.max_tokens_analysis', 8000),
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
            temperature=get_config_float('llm.temperature_default', 0.3),
            max_tokens=get_config_int('llm.max_tokens_analysis', 8000),
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
