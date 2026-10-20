"""估值工具与文本上下文共享展示约束，过期数据不能产生当前机会标签。"""
from db.valuation_sources import mark_freshness


def metric_payload(row):
    row = mark_freshness(row)
    result = {key: row.get(key) for key in (
        'metric_type', 'current_value', 'percentile', 'danger_value', 'opportunity_value',
        'zscore', 'days_old', 'is_expired', 'date_verified', 'freshness', 'percentile_window',
    )}
    result['date'] = row.get('snapshot_date')
    result['snapshot_date'] = row.get('snapshot_date')
    result['source'] = row.get('source') or row.get('data_source') or '来源未标注'
    result['source_image'] = row.get('source_image')
    result['source_url'] = row.get('source_url')
    pct_key = {'市盈率': 'pe_percentile', '市净率': 'pb_percentile', '市销率': 'ps_percentile',
               '市销率TTM': 'ps_percentile', '股息率': 'dividend_percentile'}.get(row.get('metric_type'), 'percentile')
    result[pct_key] = row.get('percentile')
    pct = row.get('percentile')
    if row['is_expired']:
        result['data_freshness_hint'] = '估值过期或来源日期未知，仅供历史参考，不能据此判断当前低估或买卖机会。'
    elif pct is not None:
        cheapness = 100 - pct if row.get('metric_type') == '股息率' else pct
        result['level'] = '低估' if cheapness < 30 else ('合理' if cheapness < 70 else '高估')
    return result


def _render(rows):
    lines = ['### 市场估值（逐指标列示来源日期）',
             '| 指数 | 指标 | 值 | 分位点 | 来源 | 估值日期 | 状态 |',
             '|------|------|----|--------|------|----------|------|']
    for name, row in rows:
        data = metric_payload(row)
        pct = data['percentile']
        pct_text = f'{pct:g}%' if pct is not None else '缺失'
        status = data.get('level') or ('历史参考' if data['is_expired'] else '分位缺失，不能判断低估等级')
        lines.append(f"| {name} | {data['metric_type']} | {data['current_value']} | {pct_text} | "
                     f"{data['source']} | {data['date'] or '未知'} | {status} |")
    lines.append('过期或日期未知的数据仅供历史参考，不得据此给出当前机会或确定买卖结论；不同来源百分位口径不可拼接。')
    return '\n'.join(lines) if rows else ''


def build_market_valuation_context():
    """批量上下文只读本地多来源；逐指标标明日期，不串行联网。"""
    from db.valuations import get_best_valuation, list_available_valuation_indexes
    rows = []
    for item in list_available_valuation_indexes():
        row = get_best_valuation(item['index_code'], item['metric_type'],
                                 query_source='portfolio', enable_online=False)
        if row:
            rows.append((item['index_name'], row))
    return _render(rows)


def build_question_valuation_context(question):
    """旧问答入口也使用同一日期选择策略，仅对问题涉及的指数补查在线。"""
    from db.valuations import get_best_valuation, list_available_valuation_indexes
    rows, matched = [], set()
    for item in list_available_valuation_indexes():
        name, code = item['index_name'], item['index_code']
        variants = {name, code}
        for prefix in ('中证', '国证', '沪', '深', '恒生'):
            core = name.removeprefix(prefix)
            variants.add(core)
            for middle in ('全指', '综指'):
                variants.add(core.removeprefix(middle))
        if not any(len(key) >= 2 and key in question for key in variants):
            continue
        if code not in matched and len(matched) >= 3:
            continue
        matched.add(code)
        row = get_best_valuation(code, item['metric_type'], query_source='chat')
        if row:
            rows.append((name, row))
    return _render(rows)
