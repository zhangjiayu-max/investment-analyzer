"""大模型服务 — 封装 LLM API 调用，提供文章解读、图片分析和投资建议"""

import base64
from openai import OpenAI
from config import get_llm_config

_api_key, _base_url, _model = get_llm_config()
client = OpenAI(api_key=_api_key, base_url=_base_url)
MODEL = _model

SYSTEM_PROMPT = """你是一位专业的投资分析师。请根据提供的微信公众号文章内容和市场数据，给出客观的投资分析。

分析要求：
1. **文章核心观点** — 用 2-3 句话概括作者的核心论点
2. **涉及标的** — 列出文章提到的股票/基金/指数，附代码
3. **估值分析** — 结合提供的行情数据，分析当前估值水平
4. **风险提示** — 指出文章可能忽略的风险
5. **综合建议** — 给出 actionable 的投资建议

注意事项：
- 保持客观中立，不要盲目跟随文章观点
- 如果文章数据过时，指出时间差异
- 投资建议需加免责声明

输出格式：Markdown，使用清晰的标题层级"""


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

    response = client.chat.completions.create(
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

    stream = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=4000,
        stream=True,
    )

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

    response = client.chat.completions.create(
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

    response = client.chat.completions.create(
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
