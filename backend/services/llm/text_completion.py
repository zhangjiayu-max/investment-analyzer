"""有界文本完成工具，供同步 pipeline 与异步分析入口共用。"""

import logging

logger = logging.getLogger(__name__)


class CompletionBudgetExhausted(RuntimeError):
    """调用前预算检查失败，保留已生成片段并停止续写。"""


def _join_continuation(previous, content):
    """只消除较长且精确的续写接缝，避免吞掉正常的短词重复。"""
    for size in range(min(len(previous), len(content), 2000), 11, -1):
        if previous.endswith(content[:size]):
            return previous + content[size:]
    return previous + content


def complete_text_analysis_sync(call, *, messages, max_continuations=2, can_continue=None, **kwargs):
    """同步调用文本模型并有限续写。

    返回 ``(text, total_tokens, status)``。status 为 completed、truncated 或
    failed；截断上限后保留已生成正文，但调用方必须把结果标记为降级。
    """
    history = [dict(message) for message in messages]
    text = ''
    tokens = 0
    for attempt in range(max_continuations + 1):
        try:
            response = call(messages=[dict(message) for message in history], **kwargs)
        except CompletionBudgetExhausted:
            return text, tokens, 'truncated' if text.strip() else 'failed'
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""
        usage = getattr(response, "usage", None)
        tokens += (usage.get('total_tokens', 0) if isinstance(usage, dict) else getattr(usage, "total_tokens", 0)) or 0
        truncated = getattr(choice, "finish_reason", None) == "length"
        if content:
            text = _join_continuation(text, content)
        if not truncated:
            if not text.strip():
                return "", tokens, "failed"
            return text, tokens, "completed"
        if attempt == max_continuations or (can_continue is not None and not can_continue()):
            logger.warning("文本完成达到续写上限 caller=%s", kwargs.get("caller", ""))
            return text, tokens, "truncated"
        assistant = {"role": "assistant", "content": content}
        reasoning = getattr(message, "reasoning_content", None) or (getattr(message, 'model_extra', None) or {}).get('reasoning_content')
        if reasoning:
            assistant["reasoning_content"] = reasoning
        history.extend([assistant, {
            "role": "user",
            "content": "请从中断处紧接着续写剩余正文，不要重复已有内容；完成原要求的全部章节并正常收尾。",
        }])
    return text, tokens, "failed"
