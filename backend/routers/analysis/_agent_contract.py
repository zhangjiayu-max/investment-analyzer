"""独立分析入口共享的 Agent 配置及响应校验。"""
import json
import re

from fastapi import HTTPException


def require_analysis_agent(name, getter):
    """要求数据库中已启用且具有有效提示词的分析 Agent。"""
    agent = getter(name)
    if not agent or not agent.get('is_active', 1):
        raise HTTPException(400, f'分析 Agent「{name}」不存在或已禁用，请先启用配置')
    if not (agent.get('system_prompt') or '').strip():
        raise HTTPException(400, f'分析 Agent「{name}」缺少 system_prompt，请先配置提示词')
    return agent


def require_specialist_analysis(result):
    """拒绝专家失败占位文本或空结果，防止成功记录污染。"""
    if not isinstance(result, dict) or result.get('status') in ('failed', 'error'):
        detail = result.get('error') or result.get('analysis') if isinstance(result, dict) else ''
        raise RuntimeError(f'专家分析失败: {detail or "无有效结果"}')
    content = result.get('analysis')
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('专家分析返回空结果')
    return content


def parse_analysis_response(response, summary_key, items_key):
    """校验结构化模型结果；空内容/空对象不属于成功分析。"""
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('LLM 分析返回空结果')
    match = re.search(r'\{.*\}', content, re.DOTALL)
    parsed = json.loads(match.group() if match else content)
    if not isinstance(parsed, dict):
        raise RuntimeError('LLM 分析未返回 JSON 对象')
    summary = parsed.get(summary_key)
    items = parsed.get(items_key, [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise RuntimeError(f'LLM 分析字段 {items_key} 格式无效')
    if not (isinstance(summary, str) and summary.strip()) and not items:
        raise RuntimeError('LLM 分析返回空结果')
    return content, parsed
