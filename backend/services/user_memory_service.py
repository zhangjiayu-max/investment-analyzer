"""用户记忆服务 — 多轮上下文感知。"""

import json
import logging
from datetime import datetime

from db import (
    list_user_memory, create_user_memory, update_user_memory,
    get_conversation_context, set_conversation_context, delete_conversation_context,
    get_portfolio_summary, list_recommendations,
)

logger = logging.getLogger(__name__)


def build_context_for_conversation(conversation_id, query=None):
    """为对话构建上下文（用户记忆 + 最近决策 + 持仓）。"""
    context = {}

    memories = list_user_memory()
    memory_dict = {}
    for m in memories:
        try:
            content = json.loads(m.get('content', '{}'))
        except (json.JSONDecodeError, TypeError):
            content = m.get('content', '')
        memory_dict[m.get('memory_type', 'preference')] = content

    context['user_preferences'] = memory_dict.get('preference', {})
    context['risk_profile'] = memory_dict.get('risk', {})
    context['investment_goals'] = memory_dict.get('goal', {})

    recent_decisions = list_recommendations(limit=3)
    context['recent_decisions'] = recent_decisions

    portfolio_summary = get_portfolio_summary()
    context['portfolio'] = portfolio_summary

    if query:
        relevant_memories = _filter_relevant_memories(memories, query)
        context['relevant_memories'] = relevant_memories

    return context


def _filter_relevant_memories(memories, query):
    """基于查询关键词筛选相关记忆。"""
    query_lower = query.lower()
    relevant = []

    for m in memories:
        content = m.get('content', '')
        if isinstance(content, str):
            content_lower = content.lower()
        else:
            try:
                content_lower = json.dumps(content, ensure_ascii=False).lower()
            except (json.JSONDecodeError, TypeError):
                content_lower = str(content).lower()

        if any(keyword in content_lower for keyword in query_lower.split()):
            relevant.append({
                'id': m.get('id'),
                'memory_type': m.get('memory_type'),
                'content': content,
            })

    return relevant[:5]


def save_user_memory(memory_type, content):
    """保存用户记忆。"""
    content_json = json.dumps(content, ensure_ascii=False)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    existing = list_user_memory(memory_type=memory_type)
    if existing:
        update_user_memory(
            existing[0]['id'],
            content=content_json,
            memory_type=memory_type,
            last_accessed_at=now,
        )
        return existing[0]['id']
    else:
        return create_user_memory(
            content=content_json,
            memory_type=memory_type,
            last_accessed_at=now,
        )


def update_conversation_context(conversation_id, updates):
    """更新对话上下文。"""
    for key, value in updates.items():
        set_conversation_context(conversation_id, key, value)
    return True


def get_user_context_summary():
    """获取用户上下文摘要。"""
    context = build_context_for_conversation(None)

    preferences = context.get('user_preferences', {})
    portfolio = context.get('portfolio', {})

    summary = {
        'risk_preference': preferences.get('risk_preference', 'medium'),
        'investment_horizon': preferences.get('investment_horizon', '3-5年'),
        'total_assets': portfolio.get('total_value', 0),
        'cash_balance': portfolio.get('cash', 0),
        'holding_count': len(portfolio.get('holdings', {})),
    }

    return summary
