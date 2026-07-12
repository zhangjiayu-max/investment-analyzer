"""资金桶与持仓联动引擎。"""

import json
import logging
from datetime import datetime

from db import (
    list_goal_buckets, get_goal_bucket, update_goal_bucket,
    list_holdings, update_holding,
)

logger = logging.getLogger(__name__)


def sync_holdings_to_bucket(bucket_id):
    """根据风险等级自动分配持仓到桶。"""
    bucket = get_goal_bucket(bucket_id)
    if not bucket:
        return None

    holdings = list_holdings()
    linked_ids = []

    for h in holdings:
        fund_category = h.get('fund_category', 'equity')
        risk_level = _map_category_to_risk_level(fund_category)

        if risk_level == bucket.get('risk_level') or fund_category == bucket.get('bucket_type'):
            update_holding(h['id'], bucket_id=bucket_id)
            linked_ids.append(h['id'])

    update_goal_bucket(bucket_id, linked_holdings=json.dumps(linked_ids, ensure_ascii=False))
    logger.info(f"持仓同步完成: bucket_id={bucket_id}, linked_count={len(linked_ids)}")
    return linked_ids


def assign_holding_to_bucket(holding_id, bucket_id):
    """手动分配持仓到桶。"""
    update_holding(holding_id, bucket_id=bucket_id)

    bucket = get_goal_bucket(bucket_id)
    if bucket:
        linked_holdings = bucket.get('linked_holdings')
        linked_ids = json.loads(linked_holdings) if linked_holdings else []
        if holding_id not in linked_ids:
            linked_ids.append(holding_id)
            update_goal_bucket(bucket_id, linked_holdings=json.dumps(linked_ids, ensure_ascii=False))

    return True


def calculate_bucket_value(bucket_id):
    """计算桶内实际金额（持仓市值 + 现金）。"""
    bucket = get_goal_bucket(bucket_id)
    if not bucket:
        return 0

    linked_holdings = bucket.get('linked_holdings')
    linked_ids = json.loads(linked_holdings) if linked_holdings else []
    if not linked_ids:
        return bucket.get('current_amount', 0)

    holdings = list_holdings()
    total_value = bucket.get('current_amount', 0)

    for h in holdings:
        if h['id'] in linked_ids:
            total_value += h.get('current_value', 0)

    return total_value


def generate_allocation_suggestion():
    """生成跨桶调拨建议。"""
    buckets = list_goal_buckets()
    suggestions = []

    for bucket in buckets:
        bucket_id = bucket['id']
        actual_amount = calculate_bucket_value(bucket_id)
        target_amount = bucket.get('target_amount', 0)

        if target_amount <= 0:
            continue

        gap = actual_amount - target_amount
        gap_ratio = gap / target_amount

        if gap_ratio > 0.2:
            suggestions.append({
                'from_bucket': bucket_id,
                'from_bucket_name': bucket.get('name'),
                'from_bucket_type': bucket.get('bucket_type'),
                'excess_amount': gap,
                'excess_ratio': gap_ratio,
                'suggested_action': 'transfer',
                'reason': f"{bucket.get('name')}资金过剩{gap:.0f}元，超出目标{gap_ratio*100:.0f}%",
            })
        elif gap_ratio < -0.1:
            suggestions.append({
                'to_bucket': bucket_id,
                'to_bucket_name': bucket.get('name'),
                'to_bucket_type': bucket.get('bucket_type'),
                'deficit_amount': abs(gap),
                'deficit_ratio': abs(gap_ratio),
                'suggested_action': 'add',
                'reason': f"{bucket.get('name')}资金不足，缺口{abs(gap):.0f}元",
            })

    return sorted(suggestions, key=lambda x: x.get('excess_ratio', x.get('deficit_ratio', 0)), reverse=True)


def transfer_between_buckets(from_bucket_id, to_bucket_id, amount):
    """执行跨桶调拨。"""
    from_bucket = get_goal_bucket(from_bucket_id)
    to_bucket = get_goal_bucket(to_bucket_id)

    if not from_bucket or not to_bucket:
        return False

    from_current = from_bucket.get('current_amount', 0)
    if from_current < amount:
        return False

    update_goal_bucket(from_bucket_id, current_amount=from_current - amount)
    update_goal_bucket(to_bucket_id, current_amount=to_bucket.get('current_amount', 0) + amount)

    logger.info(f"跨桶调拨完成: from={from_bucket_id}, to={to_bucket_id}, amount={amount}")
    return True


def _map_category_to_risk_level(category):
    """将基金类别映射到风险等级。"""
    risk_map = {
        'money': 'low',
        'bond': 'low',
        'balanced': 'medium',
        'equity': 'high',
        'index': 'high',
    }
    return risk_map.get(category, 'medium')


def get_bucket_with_holdings(bucket_id):
    """获取桶及其关联的持仓详情。"""
    bucket = get_goal_bucket(bucket_id)
    if not bucket:
        return None

    linked_holdings = bucket.get('linked_holdings')
    linked_ids = json.loads(linked_holdings) if linked_holdings else []
    all_holdings = list_holdings()

    bucket_holdings = []
    total_value = bucket.get('current_amount', 0)

    for h in all_holdings:
        if h['id'] in linked_ids:
            bucket_holdings.append(h)
            total_value += h.get('current_value', 0)

    return {
        **bucket,
        'holdings': bucket_holdings,
        'total_value': total_value,
    }
