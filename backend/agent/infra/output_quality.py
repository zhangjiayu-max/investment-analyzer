"""专家输出的纯结构验收；不访问数据库、网络或模型。

validate_specialist_output(result) 返回 valid/issues/status；valid 只对完整且
状态成功的分析为真。is_valid_analysis 仅验收正文形状，不证明投资事实正确。
"""
import json
import re


RAW_TOOL_TAG = re.compile(
    r'<\s*/?\s*(?:tool_call(?:s|_[\w-]+)?\b|invoke\b|function(?:\s|=|>)|parameter(?:\s|=|>)|｜｜DSML｜｜)',
    re.IGNORECASE,
)
FALLBACK_ANSWERS = {'分析过程遇到问题，请重试。', '交叉审阅完成，请参考其他专家分析。'}


def has_raw_toolcall_tags(text: str) -> bool:
    """识别完整、带 ID 和未闭合的工具调用标签。"""
    return bool(text and RAW_TOOL_TAG.search(text))


def analysis_issues(text: str, *, finish_reason=None, completion_status=None,
                    min_chars=50) -> list[str]:
    """结构错误原因；保守检测明显截断，不以标点判断事实正确性。"""
    issues = []
    if not isinstance(text, str) or len(text.strip()) < min_chars:
        return ['empty_or_short_analysis']
    text = text.strip()
    if text in FALLBACK_ANSWERS or text.startswith('（执行失败：'):
        issues.append('fallback_analysis')
    if has_raw_toolcall_tags(text):
        issues.append('raw_tool_call')
    if finish_reason in {'length', 'content_filter', 'tool_calls'} or completion_status in {'truncated', 'failed'}:
        issues.append('incomplete_completion')
    # A2A JSON 附块不能代替分析正文，也不参与中文引用括号检查。
    prose = re.sub(r'```(?:json)?\s*\n.*?\n```', '', text, flags=re.DOTALL).strip()
    try:
        if isinstance(json.loads(prose), (dict, list)):
            issues.append('structured_data_without_analysis')
    except (ValueError, TypeError):
        pass
    if len(prose) < min_chars:
        issues.append('empty_or_short_analysis')
    # 英文区间 a <= x 等无需括号配平；只检查引用/说明常用的成对括号。
    if text.count('```') % 2 or any(prose.count(left) > prose.count(right)
                                    for left, right in [('（', '）'), ('(', ')'), ('【', '】')]):
        issues.append('unfinished_structure')
    if prose.endswith(('：', ':', '，', ',', '、')):
        issues.append('unfinished_ending')
    return list(dict.fromkeys(issues))


def is_valid_analysis(text: str, *, finish_reason=None, completion_status=None,
                      min_chars=50) -> bool:
    """完整正文的轻量检测，可用于重试替换、专家完成率和仲裁入口。"""
    return not analysis_issues(text, finish_reason=finish_reason,
                               completion_status=completion_status, min_chars=min_chars)


def validate_specialist_output(result: dict) -> dict:
    """验收专家结果，兼容旧结果缺少 completion_status 的情况。"""
    result = result if isinstance(result, dict) else {}
    issues = analysis_issues(result.get('analysis', ''),
                             finish_reason=result.get('finish_reason'),
                             completion_status=result.get('completion_status'))
    status = result.get('status', 'success')
    if status not in {'success', 'completed', 'done'}:
        issues.append('specialist_status_' + str(status))
    reflection = result.get('self_reflection') or {}
    if reflection.get('need_retry') and reflection.get('gaps_resolved') is False:
        issues.append('unresolved_reflection_gaps')
    if result.get('output_source') == 'tool_fallback':
        issues.append('tool_evidence_without_analysis')
    if result.get('closure_failed'):
        issues.append('closure_failed')
    invalid_body = bool(analysis_issues(result.get('analysis', ''),
                                      completion_status=result.get('completion_status')))
    return {'valid': not issues, 'issues': list(dict.fromkeys(issues)),
            'status': 'success' if not issues else ('failed' if invalid_body else 'degraded')}
