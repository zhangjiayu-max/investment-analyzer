"""模型上下文结构化限额；原始证据由调用方保留，摘要显式标记省略。"""
from __future__ import annotations

import json
import hashlib


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str))


def estimate_request_tokens(messages, tools=None):
    """请求前保守估算，包含思考历史和工具定义；真实供应商 usage 仍为准。"""
    serialized = json.dumps({'messages': messages, 'tools': tools or []}, ensure_ascii=False, default=str)
    return max(1, (len(serialized) + 1) // 2)


def preview_context_text(text, max_chars=8000):
    """超长分析仅发送首尾预览，保留尾部条件和风险；原文供校验与追溯。"""
    text = str(text or '')
    if len(text) <= max_chars:
        return text
    marker = '\n[上下文预览省略中间内容；未显示部分不能视为已核验事实]\n'
    room = max(0, max_chars - len(marker))
    head = room * 2 // 3
    return text[:head] + marker + (text[-(room - head):] if room > head else '')


def summarize_evidence(value, max_chars=6000):
    """保留数值、口径与来源；优先省略重复明细和长文本，不切断 JSON。"""
    if _json_size(value) <= max_chars:
        return value

    priority = ('fund_code', 'index_code', 'index_name', 'metric_type', 'current_value',
                'percentile', 'unit', 'snapshot_date', 'date', 'as_of', 'data_source',
                'series_type', 'metric_status', 'data_points', 'status', 'is_hypothetical',
                'is_system', 'transaction_date', 'amount', 'transactions_complete',
                'date_scope', 'concentration_policy', 'target_equity_ratio',
                'total_assets', 'total_value', 'invested_value', 'cash_balance', 'calculations', 'error', 'reason')
    duplicates = {'records', 'raw_json', 'local_images', 'image_base64', 'image_data',
                  'embedding', 'vector', 'content_html'}

    def compact(node, items, string_chars, field_limit):
        if isinstance(node, dict):
            keys = sorted(node, key=lambda k: (k not in priority, priority.index(k) if k in priority else 0))
            result, omitted = {}, []
            for key in keys:
                if key in duplicates or len(result) >= field_limit:
                    omitted.append(key)
                    continue
                result[key] = compact(node[key], items, string_chars, field_limit)
            if omitted:
                result['_omitted_fields'] = omitted[:20]
            return result
        if isinstance(node, list):
            result = [compact(child, items, string_chars, field_limit) for child in node[:items]]
            if len(node) > items:
                result.append({'_omitted_items': len(node) - items})
            return result
        if isinstance(node, str) and len(node) > string_chars:
            return {'preview': node[:string_chars], 'preview_truncated': True}
        return node

    for items, string_chars, fields in ((20, 500, 60), (8, 300, 40), (3, 160, 30), (1, 80, 18)):
        summary = compact(value, items, string_chars, fields)
        if _json_size(summary) <= max_chars:
            return summary
    # 极大的对象仍保留关键标量，明确不是完整工具返回。
    summary = {'_context_omitted': True}
    if isinstance(value, dict):
        for key in priority:
            child = value.get(key)
            if key in value and not isinstance(child, (dict, list)):
                trial = {**summary, key: child if not isinstance(child, str) else child[:80]}
                if _json_size(trial) <= max_chars:
                    summary = trial
    return summary


def compact_model_messages(messages, *, per_tool_chars=6000, total_tool_chars=18000):
    """模型副本内按工具总量分配额度；重复结果引用首份，保留原始消息协议。"""
    result = [dict(message) for message in messages]
    tools = [m for m in result if m.get('role') == 'tool']
    if not tools:
        return result
    tool_names = {tc['id']: tc.get('function', {}).get('name', '')
                  for m in result for tc in m.get('tool_calls', []) or [] if isinstance(tc, dict) and 'id' in tc}
    # 预留 evidence id、引用及 JSON 包装；所有工具响应都有独立、完整的返回槽位。
    allowance = min(per_tool_chars, max(64, total_tool_chars // len(tools) - 160))
    seen = {}
    for message in result:
        content = message.get('content') or ''
        if message.get('role') != 'tool' or not isinstance(content, str):
            continue
        name = message.get('name') or tool_names.get(message.get('tool_call_id'), '')
        identity = hashlib.sha256((name + '\n' + content).encode()).hexdigest()[:20]
        evidence_id = 'ctx:' + identity
        if identity in seen:
            message['content'] = json.dumps({'_evidence_ref': evidence_id,
                '_same_as_tool_call_id': seen[identity]}, ensure_ascii=False, separators=(',', ':'))
            continue
        seen[identity] = message.get('tool_call_id')
        if len(content) <= allowance:
            # 首份小结果保持形状兼容，重复结果需要编号时再补 envelope。
            continue
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            parsed = content
        summary = summarize_evidence(parsed, allowance)
        if isinstance(summary, dict):
            summary = {**summary, '_evidence_id': evidence_id}
        else:
            summary = {'_evidence_id': evidence_id, 'data': summary}
        message['content'] = json.dumps(summary, ensure_ascii=False, separators=(',', ':'), default=str)
    # 小首份若被引用，用外层记录来源；不会改变调用方的原始对象。
    referenced = {json.loads(m['content'])['_evidence_ref'] for m in tools if m['content'].startswith('{"_evidence_ref":')}
    for identity, tool_id in seen.items():
        evidence_id = 'ctx:' + identity
        if evidence_id not in referenced:
            continue
        first = next(m for m in tools if m.get('tool_call_id') == tool_id)
        try:
            parsed = json.loads(first['content'])
        except (ValueError, TypeError):
            parsed = first['content']
        first['content'] = json.dumps({**parsed, '_evidence_id': evidence_id} if isinstance(parsed, dict)
            else {'_evidence_id': evidence_id, 'data': parsed}, ensure_ascii=False, separators=(',', ':'))
    if sum(len(m.get('content') or '') for m in tools) > total_tool_chars:
        from services.llm.text_completion import CompletionBudgetExhausted
        raise CompletionBudgetExhausted('工具响应数量超过上下文总额度，保留原始证据并停止追加模型调用')
    return result
