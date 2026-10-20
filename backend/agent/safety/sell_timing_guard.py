"""卖出依据守卫：逐标的核查理由，不以买入成本强制持有或继续定投。

组合持有与个别标的减持可以并存。集中度、风险预算、流动性或基本面
理由可支持减持；估值择时需给出该标的的估值与盈亏背景。此处只核查
报告中的依据是否齐备，事实真伪仍由上游原始工具证据校验。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_SELL_ACTION = re.compile(r'卖出|减仓|减持|清仓|赎回|止损|止盈')
_CODE = re.compile(r'(?<!\d)\d{6}(?!\d)')
_NEGATED = re.compile(r'(?:不建议|不应|无需|不要|避免|禁止|暂不|不|勿|尚未|历史|曾经|已完成)[^，。；\n]{0,6}$')
_PERCENTILE_PATTERNS = [
    re.compile(r'(?:百分位|分位|历史分位|估值分位|PE\s*分位|PB\s*分位)[^。\n]{0,30}?(\d+(?:\.\d+)?\s*%)', re.I),
    re.compile(r'(?:PE|PB|市盈率|市净率|pe_ttm|pb_ttm)[^。\n]{0,15}?(\d+(?:\.\d+)?)', re.I),
]
_PROFIT_PATTERNS = [
    re.compile(r'(?:盈亏率|盈亏|亏损|盈利|浮亏|浮盈|收益率|profit_rate|profit_pct)[^。\n]{0,30}?[-+]?\d+(?:\.\d+)?\s*%', re.I),
    re.compile(r'[-+]?\d+(?:\.\d+)?\s*%[^。\n]{0,15}?(?:盈亏|亏损|盈利|浮亏|浮盈)', re.I),
]
_FUNDAMENTAL_DECAY_KEYWORDS = [
    '基本面恶化', '业绩下滑', '营收下降', '营收下滑', 'ROE下降', 'ROE下滑',
    '净资产收益率下降', '评级下调', '信用降级', '违约',
]


def _operation_section(answer: str) -> str:
    """优先读取操作建议及其子节，排除后续风险描述。"""
    headers = list(re.finditer(r'^(#{1,6})\s*(.*?)\s*$', answer, re.M))
    for index, header in enumerate(headers):
        if re.search(r'第\s*4\s*段|(?:具体)?操作建议', header.group(2)):
            end = next((next_header.start() for next_header in headers[index + 1:]
                        if len(next_header.group(1)) <= len(header.group(1))), len(answer))
            return answer[header.end():end]
    risk = next((header.start() for header in headers
                 if re.search(r'第\s*5\s*段|风险提示|风险与盲点', header.group(2))), len(answer))
    return answer[:risk]


def _positive_sell_actions(text: str) -> list[str]:
    return [match.group(0) for match in _SELL_ACTION.finditer(text)
            if not _NEGATED.search(text[max(0, match.start() - 12):match.start()])]


def _detect_sell_actions(answer: str) -> list[str]:
    """只识别建议区域内的正向卖出动作，包含表格中的减持。"""
    return list(dict.fromkeys(_positive_sell_actions(_operation_section(answer))))


def _has_percentile_info(text: str) -> bool:
    return any(pattern.search(text or '') for pattern in _PERCENTILE_PATTERNS)


def _has_profit_info(text: str) -> bool:
    return any(pattern.search(text or '') for pattern in _PROFIT_PATTERNS)


def _has_high_valuation_info(text: str) -> bool:
    if re.search(r'(?:没有|不存在|并非|并不|尚未|未见|无)[^，。；\n]{0,8}(?:高估|估值高|估值偏高)', text):
        return False
    if re.search(r'估值高位|高估|估值偏高|估值较贵', text, re.I):
        return True
    return any(float(match.group(1)) >= 60 for match in re.finditer(
        r'分位(?:数)?\s*(?:为|是|[:：=])?\s*(\d+(?:\.\d+)?)\s*%', text))


def _has_fundamental_decay(text: str) -> bool:
    return any(keyword.lower() in clause.lower()
               and not re.search(r'没有|不存在|并非|并未|尚未|未见|无.*证据', clause)
               for clause in re.split(r'[。；\n]', text)
               for keyword in _FUNDAMENTAL_DECAY_KEYWORDS)


def _classify_sell_type(text: str) -> str:
    """分类仅供描述；浮亏本身不构成止损否决权。"""
    if not _detect_sell_actions(text):
        return 'unknown'
    if re.search(r'亏损|浮亏|盈亏率\s*[-：:]?\s*-\d', text):
        return 'stop_loss'
    if re.search(r'盈利|浮盈|止盈', text):
        return 'take_profit'
    return 'unknown'


def _local_following(lines: list[str], start: int, codes: set[str]) -> str:
    """同一标的的相邻解释可关联；其他代码/新动作/章节切断上下文。"""
    block = [lines[start]]
    for line in lines[start + 1:]:
        if re.match(r'\s*#', line):
            break
        line_codes = set(_CODE.findall(line))
        if line_codes and (not codes or line_codes != codes):
            break
        basis_line = bool(re.match(r'\s*(?:[-*]\s*)?(?:理由|依据|原因)[：:]', line))
        if _positive_sell_actions(line) and not basis_line and (not codes or line_codes != codes):
            break
        if line.strip() and not line_codes and not re.match(
                r'\s*(?:[-*]\s*)?(?:理由|依据|原因|当前|目前|PE|PB|市盈率|估值|盈亏|盈利|亏损|浮盈|浮亏|风险预算|集中度|流动性)',
                line, re.I):
            # 没有代码的另一只基金不能仅凭相邻位置借用为本标的证据。
            break
        block.append(line)
    return '\n'.join(block)


def _sell_contexts(answer: str) -> list[tuple[str, str]]:
    """为每个卖出标的独立关联正文行，避免借用另一只基金的指标。"""
    operation_lines = _operation_section(answer).splitlines()
    all_lines = answer.splitlines()
    contexts = []
    seen = set()
    for index, line in enumerate(operation_lines):
        if re.match(r'\s*(?:[-*]\s*)?(?:理由|依据|原因)[：:]', line):
            continue
        if not _positive_sell_actions(line):
            continue
        codes = set(_CODE.findall(line))
        label = '/'.join(sorted(codes)) if codes else re.sub(r'^[\s|*\d.、-]+', '', line).strip()[:40]
        if label in seen:
            continue
        seen.add(label)
        context = _local_following(operation_lines, index, codes)
        if len(codes) == 1:
            # 数据段与建议段分离时，仅补入明确写出同一代码的记录。
            for data_index, data_line in enumerate(all_lines):
                if set(_CODE.findall(data_line)) == codes:
                    context += '\n' + _local_following(all_lines, data_index, codes)
        elif len(codes) > 1:
            # 一个句子多个标的无法可靠分配估值和盈亏，不声称已逐标的核对。
            context = ''
        contexts.append((label or '未指明标的', context))
    return contexts


def _has_portfolio_reason(text: str) -> bool:
    """组合理由需给出约束、偏离或已确认用途，单个关键词不能授权卖出。"""
    # 去除否定/缺失的理由描述，避免把“没有集中度超限证据”当作依据。
    clauses = re.split(r'[。；\n]', text)
    for clause in clauses:
        if re.search(r'没有|不存在|未确认|未核实|尚未|不需要|无.*(?:证据|需求)|待确认|假设', clause):
            continue
        numeric = bool(re.search(r'\d+(?:\.\d+)?\s*[%元]', clause))
        if numeric and re.search(r'集中度|单只|单一|占总资产|权重|仓位', clause) and re.search(r'超限|超过|高于|上限|回到|降至|压降', clause):
            return True
        if re.search(r'风险预算|风险敞口|风险承受', clause) and re.search(r'超过|超出|回到|降低|下调|收紧', clause) and (numeric or '已确认' in clause):
            return True
        if re.search(r'流动性|还款|用款|现金需求|医疗|支出', clause) and re.search(r'需要|需求|不足|缺口|到期', clause) and (numeric or '已确认' in clause):
            return True
    return False


def enforce_sell_timing_guard(answer: str, trace_id: str = '',
                              arbitration_summary: dict | None = None) -> tuple[str, list[str]]:
    """逐标的检查减持理由；组合 HOLD 不跳过检查，依据不足只追加警示。"""
    warnings = []
    try:
        from db.config import get_config_bool
        if not get_config_bool('agent.sell_timing_guard_enabled', True):
            return answer, warnings
    except Exception:
        return answer, warnings
    if not answer:
        return answer, warnings
    missing = []
    for target, context in _sell_contexts(answer):
        if _has_portfolio_reason(context) or _has_fundamental_decay(context):
            continue
        has_valuation = _has_percentile_info(context)
        has_profit = _has_profit_info(context)
        if has_valuation and has_profit and _has_high_valuation_info(context):
            continue
        warnings.append('sell_without_basis:' + target)
        details = []
        if not has_valuation:
            warnings.append('sell_without_percentile')
            details.append('该标的的估值依据')
        if not has_profit:
            warnings.append('sell_without_profit')
            details.append('该标的的盈亏背景')
        details.append('明确的集中度、风险预算、用款需求、基本面变化或估值择时理由')
        missing.append(f'- {target}：需核对' + '、'.join(details) + '。')
    if not warnings:
        return answer, warnings
    warnings = list(dict.fromkeys(warnings))
    block = ('\n\n---\n\n**卖出时机守卫提示**\n\n'
             '以下减持建议缺少逐标的依据，需补充核验后再评估：\n' + '\n'.join(missing) + '\n\n'
             '盈亏和买入成本本身不能决定是否减持。减持应结合已确认的风险约束、资金用途及费用；'
             '其他标的的盈利或估值信息不能代替上述核验。\n')
    logger.warning('[trace:%s] [sell_timing_guard] 逐标的减持依据不足: %s', trace_id, warnings)
    return answer.rstrip() + block, warnings
