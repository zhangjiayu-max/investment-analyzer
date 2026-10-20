"""估值日期与多来源候选；抓取时间不参与估值新鲜度判断。"""
import json
from collections import defaultdict
from datetime import date

from db._conn import _get_conn


def mark_freshness(row: dict, max_days: int = 3) -> dict:
    """保留来源日期；未知或未来日期不能证明当前估值。"""
    result = dict(row)
    try:
        source_day = date.fromisoformat(str(row.get('snapshot_date') or row.get('date') or '')[:10])
        age = (date.today() - source_day).days
        if age < 0:
            raise ValueError('future source date')
    except (TypeError, ValueError):
        result.update(days_old=None, is_expired=True, date_verified=False, freshness='unknown')
    else:
        result.update(days_old=age, is_expired=age > max_days, date_verified=True,
                      freshness='stale' if age > max_days else 'fresh')
    return result


def newest_valuation(rows, metric_type=None):
    """只比较同指标日期，有效日期优先；同日保持原来源顺序。"""
    candidates = [mark_freshness(row) for row in rows if row
                  and (metric_type is None or row.get('metric_type') == metric_type)
                  and (row.get('current_value') is not None or row.get('percentile') is not None)]
    return max(candidates, key=lambda row: (
        row['date_verified'], -(row['days_old'] if row['days_old'] is not None else 0)
    ), default=None)


def image_valuation_rows(source=None):
    """读取图片表；缺代码时只接受唯一的标准名称或显式别名。"""
    from db.valuations import normalize_index_code, _normalize_percentile, _percentile_unit
    conn = _get_conn()
    try:
        names = defaultdict(set)
        for row in conn.execute('SELECT DISTINCT index_code,index_name FROM index_valuations'):
            if row['index_name']:
                names[row['index_name'].strip().upper()].add(normalize_index_code(row['index_code']))
        for row in conn.execute('SELECT index_code,index_name,aliases FROM index_code_mapping'):
            try:
                aliases = json.loads(row['aliases'] or '[]')
            except (TypeError, ValueError):
                aliases = []
            for name in [row['index_name'], *(aliases if isinstance(aliases, list) else [])]:
                if isinstance(name, str) and name.strip():
                    names[name.strip().upper()].add(normalize_index_code(row['index_code']))
        results = []
        for table, provider in (('dd_valuations', '螺丝钉'), ('liuyi_valuations', '六亿')):
            if source and source != provider:
                continue
            for record in conn.execute(f'SELECT * FROM {table} ORDER BY update_date DESC, id DESC'):
                try:
                    parsed = json.loads(record['raw_json'] or '{}')
                except (TypeError, ValueError):
                    continue
                if not isinstance(parsed, dict) or not parsed.get('ok') or not isinstance(parsed.get('data'), list):
                    continue
                for item in parsed['data']:
                    if not isinstance(item, dict):
                        continue
                    code = normalize_index_code(item.get('index_code') or '')
                    name = item.get('index_name') or ''
                    if not code:
                        choices = names.get(name.strip().upper(), set())
                        if len(choices) != 1:
                            continue
                        code = next(iter(choices))
                    for key, metric in (('pe', '市盈率'), ('pb', '市净率'), ('ps', '市销率'), ('dividend_yield', '股息率')):
                        if item.get(key) is None:
                            continue
                        unit_data = {**parsed, **item}
                        try:
                            unit = _percentile_unit(unit_data)
                        except ValueError:
                            continue
                        verified = (item.get('metric_verification') or {}).get(key)
                        if verified is None:
                            verified = item.get('metric_type') == metric
                        # 低 PE 且无逐行类型证据时只隔离，不猜成 PS；其他指标仍可查询。
                        suspicious = key == 'pe' and isinstance(item[key], (int, float)) and 0 < item[key] < 2 and not verified
                        if suspicious or item.get('evidence_eligible') is False:
                            continue
                        raw_pct = item.get(f'{key}_percentile')
                        results.append({
                            'index_code': code, 'index_name': name, 'metric_type': metric,
                            'current_value': item[key],
                            'percentile': _normalize_percentile(raw_pct, fractional=unit == 'ratio'),
                            'percentile_raw': raw_pct, 'percentile_input_unit': unit,
                            'percentile_unit': 'percent', 'metric_verified': bool(verified),
                            'validation_status': item.get('validation_status') or ('source_extracted' if verified else 'legacy_unverified'),
                            'history_window': item.get(f'{key}_history_window') or item.get('history_window') or parsed.get('history_window'),
                            'snapshot_date': record['update_date'], 'source': provider,
                            'data_source': provider, 'percentile_scale': 100,
                            'source_image': record['image_path'], 'source_url': record['image_url'],
                            'dd_id' if provider == '螺丝钉' else 'liuyi_id': record['id'],
                            'valuation_status': item.get('valuation_status'),
                        })
        return results
    finally:
        conn.close()
