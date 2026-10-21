"""持仓约束冲突守卫：同一份报告不得同时承认"已超标"又建议"加仓"。

背景（2026-10-21 分析记录复核，log#139 市场日报）：报告自己算出
"权益 20.2% 已超 KYC 15% 目标 5 个百分点"，同一段又建议"加仓到 30%、
单次买入不超 5 万"。读者照做会进一步偏离已确认的约束。

这里只做**文本层一致性核查**：约束数值与动作方向都取自报告自身，
不判断估值好坏，也不改写正文——命中时只在末尾追加提示块，
把"必须先调降/减仓"这个前置条件显性化。

开关：agent.position_constraint_guard_enabled（默认开启）。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 已超标的自我陈述：必须有数字，避免把"仓位偏高"这类定性描述当作冲突依据
_OVERWEIGHT_PATTERNS = (
    re.compile(r'(?:权益|股票)[^。\n]{0,24}?(?:已|已经)?(?:超|高于|超出|突破)[^。\n]{0,12}?(?:目标|KYC|上限|比例)[^。\n]{0,12}?\d'),
    re.compile(r'(?:权益|股票)[^。\n]{0,16}?(?:超|高于|超出)[^。\n]{0,8}?\d+(?:\.\d+)?\s*(?:个)?(?:百分点|pct)'),
    re.compile(r'(?:占|超过)[^。\n]{0,8}?\d+(?:\.\d+)?\s*%[^。\n]{0,12}?(?:突破|超出|高于)[^。\n]{0,10}?(?:单只|单一)[^。\n]{0,6}?(?:上限|限制)'),
    re.compile(r'(?:单只|单一基金|单一标的)[^。\n]{0,20}?(?:突破|超出|超过)[^。\n]{0,10}?\d+(?:\.\d+)?\s*%[^。\n]{0,8}?(?:上限|限制)'),
    re.compile(r'(?:已超|突破)[^。\n]{0,10}?KYC[^。\n]{0,12}?(?:上限|目标)'),
)

# 正向加仓动作
_ADD_ACTION = re.compile(r'买入|加仓|补仓|建仓|定投|增配|上车')

# 否定与条件化：出现即认为该动作未被无条件建议
_NEGATED = re.compile(
    r'不建议|不应|不宜|无需|不要|禁止|避免|暂不|暂时不|不急于|不考虑|尚未|不做|不能算'
    r'|不足以|不足|缺乏|不支持|难以|尚不|没有(?:证据|依据)'
)
_CONDITIONAL = re.compile(
    r'待触发|观察清单|前提条件|冷却期|触发条件|仅在|除非'
    r'|(?:若|如|假如)[^。；\n]{0,24}?(?:后|再|前提|同意|条件|满足|出现|落地|达成|兑现)'
    r'|先[^。；\n]{0,16}?再'
)
# 讨论/评估语境：出现这些词说明是在分析可行性，不是下达指令
_DISCUSSIVE = re.compile(
    r'不等于|不能算|属于|该不该|是否|评估|讨论|步骤|卡死|空间|逻辑|假设|前提|'
    r'观点|分歧|反驳|风险提示|盲点|结论：|判断倾向'
)
# 要求同句出现金额或明确指令词，否则视为叙述而非建议
_AMOUNT = re.compile(r'\d+(?:\.\d+)?\s*(?:元|万|%|个?百分点)')
_DIRECTIVE = re.compile(r'建议|可以|可考虑|宜|应|优先')

_SENTENCE_SPLIT = re.compile(r'[。；\n]')


def _operation_section(answer: str) -> str:
    """只取操作建议段落；找不到就返回空串（宁可漏报，不误报）。"""
    headers = list(re.finditer(r'^(#{1,6})\s*(.*?)\s*$', answer or '', re.M))
    for index, header in enumerate(headers):
        if re.search(r'第\s*4\s*段|(?:具体)?操作建议|行动建议|投资建议|操作清单|怎么做', header.group(2)):
            end = next((nxt.start() for nxt in headers[index + 1:]
                        if len(nxt.group(1)) <= len(header.group(1))), len(answer))
            return answer[header.end():end]
    return ''


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or '') if s.strip()]


def _find_overweight_evidence(text: str) -> list[str]:
    hits = []
    for pattern in _OVERWEIGHT_PATTERNS:
        for match in pattern.finditer(text or ''):
            snippet = match.group(0).strip()
            if snippet and snippet not in hits:
                hits.append(snippet)
    return hits


def _find_unconditional_add(text: str) -> list[str]:
    """只收集操作段内、未被否定、未附前置条件的加仓指令。"""
    section = _operation_section(text)
    if not section:
        return []
    hits = []
    for sentence in _sentences(section):
        line = sentence.strip()
        if not line or line.startswith('#') or line.startswith('|'):
            continue
        if _DISCUSSIVE.search(line) or _CONDITIONAL.search(line):
            continue
        if '《' in line or '》' in line:      # 引书句是方法论参考，不是指令
            continue
        if not (_AMOUNT.search(line) or _DIRECTIVE.search(line)):
            continue
        for match in _ADD_ACTION.finditer(line):
            prefix = line[max(0, match.start() - 14):match.start()]
            if _NEGATED.search(prefix):
                continue
            if line not in hits:
                hits.append(line[:80])
            break
    return hits


def enforce_position_constraint_guard(answer: str, trace_id: str = '') -> tuple[str, list[str]]:
    """检测"已超标 + 无条件加仓"的自相矛盾，命中则追加前置条件提示。

    返回 (处理后的文本, 告警列表)；告警为空表示未命中，文本原样返回。
    """
    warnings: list[str] = []
    try:
        from db.config import get_config_bool
        if not get_config_bool('agent.position_constraint_guard_enabled', True):
            return answer, warnings
    except Exception:
        return answer, warnings
    if not answer:
        return answer, warnings

    overweight = _find_overweight_evidence(answer)
    if not overweight:
        return answer, warnings
    adds = _find_unconditional_add(answer)
    if not adds:
        return answer, warnings

    warnings.append('position_conflict')
    warnings.extend('conflict_add:' + item for item in adds[:3])
    logger.warning('[trace:%s] [position_constraint_guard] 超标与加仓并存: 超标=%s 加仓=%s',
                   trace_id, overweight[:2], adds[:3])

    block = (
        '\n\n---\n\n**持仓约束冲突提示**\n\n'
        '本报告一方面认定组合已超出既定约束（' + '；'.join(overweight[:2]) + '），'
        '另一方面给出了未附加前提的加仓建议。两者不能同时执行：\n\n'
        '- 先执行约束修复（减仓或调降目标），再考虑新增权益敞口；\n'
        '- 若确实要加仓，需用户先明确同意上调权益目标，并按调整后的目标重新计算可投金额。\n'
    )
    return answer.rstrip() + block, warnings
