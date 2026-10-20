"""有界文本完成工具，供同步 pipeline 与异步分析入口共用。"""

import logging

logger = logging.getLogger(__name__)


def complete_text_analysis_sync(call, *, messages, max_continuations=2, **kwargs):
    """同步调用文本模型并有限续写。

    返回 ``(text, total_tokens, status)``。status 为 completed、truncated 或
    failed；截断上限后保留已生成正文，但调用方必须把结果标记为降级。
    """
    history = [dict(message) for message in messages]
    parts = []
    tokens = 0
    for attempt in range(max_continuations + 1):
        response = call(messages=[dict(message) for message in history], **kwargs)
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""
        usage = getattr(response, "usage", None)
        tokens += getattr(usage, "total_tokens", 0) or 0
        truncated = getattr(choice, "finish_reason", None) == "length"
        if content:
            parts.append(content)
        if not truncated:
            if not "".join(parts).strip():
                return "", tokens, "failed"
            return "".join(parts), tokens, "completed"
        if attempt == max_continuations:
            logger.warning("文本完成达到续写上限 caller=%s", kwargs.get("caller", ""))
            return "".join(parts), tokens, "truncated"
        assistant = {"role": "assistant", "content": content}
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            assistant["reasoning_content"] = reasoning
        history.extend([assistant, {
            "role": "user",
            "content": "请从中断处紧接着续写剩余正文，不要重复已有内容；完成原要求的全部章节并正常收尾。",
        }])
    return "".join(parts), tokens, "failed"
