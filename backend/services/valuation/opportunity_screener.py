"""可审计的估值候选快照：来源独立、指标同口径、规则与趋势风险分离。"""
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from db.valuations import normalize_index_code

METRICS = ('市盈率', '市净率', '市销率', '市销率TTM', '股息率')
SOURCES = ('雷牛牛', '螺丝钉', '六亿')


def _number(value):
    try:
        n = float(str(value).replace('%', '').strip())
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _valid_day(value):
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def _source(row):
    """显式机构优先；旧文件名推断必须透出未核验状态。"""
    try:
        raw = json.loads(row.get('raw_json') or '{}')
    except (TypeError, ValueError):
        raw = {}
    metadata = [row]
    if isinstance(raw, dict):
        metadata.append(raw)
        if isinstance(raw.get('metadata'), dict):
            metadata.append(raw['metadata'])
    for obj in metadata:
        for key in ('provider', 'source', 'data_source'):
            value = obj.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            lower = value.lower()
            if any(alias in lower for alias in ('螺丝钉', 'luosiding', 'screw')) or lower == 'dd':
                return '螺丝钉', 'explicit_provider'
            if '六亿' in lower or 'liuyi' in lower:
                return '六亿', 'explicit_provider'
            if '雷牛牛' in lower or 'leiniuniu' in lower:
                return '雷牛牛', 'explicit_provider'
            # 未知显式提供者也必须独立分组，不与任何机构的历史合并。
            return f'未核验来源:{value}', 'explicit_unknown_provider'
    path = (row.get('source_image') or '').lower()
    # 已知的图片解析目录是历史雷牛牛入口；任意临时/裸文件名不能据此猜来源。
    if 'liuyi' in path:
        return '六亿', 'dedicated_table'
    if 'dd_' in path or '/dd_' in path:
        return '螺丝钉', 'dedicated_table'
    if path.startswith('data/images/') or '/data/images/' in path or path.startswith('valuation_images/') or '/valuation_images/' in path:
        return '雷牛牛', 'known_legacy_path'
    return f'未核验文件:{path or "无路径"}', 'legacy_filename_unverified'


def scan_snapshot(conn, min_score=40, top_n=None, *, today=None, max_stale_days=10):
    """同机构同指标至少3个真实日期才评分；没有百分位的数据只记覆盖。"""
    today = today or date.today()
    groups = defaultdict(dict)
    coverage = {s: {'source': s, 'records': 0, 'usable_series': 0,
                    'reference_only_records': 0, 'invalid_records': 0,
                    'missing_code_records': 0, 'resolved_name_records': 0} for s in SOURCES}
    all_codes, dates, evidence = set(), [], []
    # 只用明确映射/标准名称的唯一等值匹配，绝不依行业名称模糊猜代码。
    name_codes = defaultdict(set)
    standard_rows = conn.execute('SELECT DISTINCT index_name, index_code FROM index_valuations').fetchall()
    for row in standard_rows:
        if row['index_name'] and row['index_code']:
            name_codes[row['index_name'].strip().upper()].add(normalize_index_code(row['index_code']))
    for row in conn.execute('SELECT index_name, index_code, aliases FROM index_code_mapping').fetchall():
        code = normalize_index_code(row['index_code'])
        try:
            aliases = json.loads(row['aliases'] or '[]')
        except (TypeError, ValueError):
            aliases = []
        for name in [row['index_name'], *(aliases if isinstance(aliases, list) else [])]:
            if isinstance(name, str) and name.strip() and code:
                name_codes[name.strip().upper()].add(code)

    def ingest(row, source):
        source_name, source_basis = source if isinstance(source, tuple) else (source, 'dedicated_table')
        if source_basis == 'legacy_filename_unverified':
            coverage.setdefault(source_name, {'source': source_name, 'records': 0, 'usable_series': 0,
                'reference_only_records': 0, 'invalid_records': 0, 'missing_code_records': 0,
                'resolved_name_records': 0})['records'] += 1
            coverage[source_name]['reference_only_records'] += 1
            return
        if source_name not in coverage:
            coverage[source_name] = {'source': source_name, 'records': 0, 'usable_series': 0,
                'reference_only_records': 0, 'invalid_records': 0,
                'missing_code_records': 0, 'resolved_name_records': 0}
        cov = coverage[source_name]
        cov['records'] += 1
        code = normalize_index_code(str(row.get('index_code') or ''))
        if not code:
            choices = name_codes.get(str(row.get('index_name') or '').strip().upper(), set())
            if len(choices) == 1:
                code = next(iter(choices))
                cov['resolved_name_records'] += 1
            else:
                cov['missing_code_records'] += 1
        day = _valid_day(row.get('snapshot_date'))
        metric = row.get('metric_type')
        pct = _number(row.get('percentile'))
        value = _number(row.get('current_value'))
        if code:
            all_codes.add(code)
        if day and day <= today:
            dates.append(str(day))
        # 输入指纹包含交叉参考，新增来源同样使快照失效。
        evidence.append([source_name, code, str(day), metric, value, pct, row.get('zscore')])
        if not code or not day or day > today or metric not in METRICS:
            cov['invalid_records'] += 1
            return
        if pct is None or not 0 <= pct <= 100 or value is None or value <= 0:
            cov['reference_only_records'] += 1
            return
        groups[(code, metric, source_name)][str(day)] = {
            **row, 'index_code': code, 'snapshot_date': str(day), 'percentile': pct,
            'current_value': value, 'zscore': _number(row.get('zscore')),
        }

    for row in conn.execute('SELECT * FROM index_valuations ORDER BY id').fetchall():
        d = dict(row)
        source, basis = _source(d)
        ingest({**d, 'source_basis': basis}, (source, basis))
    for table, source in (('dd_valuations', '螺丝钉'), ('liuyi_valuations', '六亿')):
        for row in conn.execute(f'SELECT update_date, raw_json FROM {table} ORDER BY id').fetchall():
            try:
                parsed = json.loads(row['raw_json'] or '{}')
                if not isinstance(parsed, dict) or parsed.get('ok') is False:
                    raise ValueError('无有效解析结果')
                items = parsed.get('data', [])
                if not isinstance(items, list):
                    raise ValueError('指数列表格式无效')
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    found = False
                    for key, metric in (('pe', '市盈率'), ('pb', '市净率'), ('ps', '市销率'), ('dividend_yield', '股息率')):
                        if item.get(key) is None:
                            continue
                        found = True
                        ingest({**item, 'snapshot_date': row['update_date'] or parsed.get('update_date'),
                                'metric_type': metric, 'current_value': item[key],
                                'percentile': item.get(f'{key}_percentile'), 'zscore': None,
                                'source_basis': 'dedicated_table'}, source)
                    if not found:
                        coverage[source]['reference_only_records'] += 1
                        coverage[source]['records'] += 1
            except (TypeError, ValueError):
                coverage[source]['invalid_records'] += 1
    by_code = defaultdict(list)
    for (code, metric, source), points in groups.items():
        if source.startswith(('未核验来源:', '未核验文件:')):
            continue  # 来源未知只进 coverage，不得形成候选
        records = sorted(points.values(), key=lambda r: r['snapshot_date'])
        if len(records) < 3:
            continue
        coverage[source]['usable_series'] += 1
        by_code[code].append((metric, source, records))

    opportunities, skipped_stale, selected = [], 0, []
    for code, series in by_code.items():
        # 先选最新可用历史；同日才按指标优先级和覆盖量排序，无机构强制优先。
        metric, source, records = min(series, key=lambda s: (
            -date.fromisoformat(s[2][-1]['snapshot_date']).toordinal(), METRICS.index(s[0]), -len(s[2]), s[1]))
        selected.append([code, metric, source, records])
        stale = (today - date.fromisoformat(records[-1]['snapshot_date'])).days
        if stale > max_stale_days:
            skipped_stale += 1
            continue
        opp = _score_series(code, metric, source, records, stale)
        if opp['cheapness_percentile'] <= 30 and opp['score'] >= min_score:
            opportunities.append(opp)

    opportunities.sort(key=lambda o: (-o['score'], o['index_code']))
    count = len(opportunities)
    if top_n is not None:
        opportunities = opportunities[:max(0, top_n)]
    for cov in coverage.values():
        cov['status'] = ('available' if cov['usable_series'] else
                         'reference_only' if cov['records'] else 'unavailable')
        cov['note'] = '同来源同指标独立历史；无百分位不推算，不混合机构序列'
    if not all_codes:
        empty_reason = 'no_data'
    elif not by_code:
        empty_reason = 'insufficient_history'
    elif skipped_stale == len(by_code):
        empty_reason = 'all_stale'
    else:
        empty_reason = 'below_threshold' if not count else None
    fingerprint = json.dumps([str(today), min_score, max_stale_days, evidence], sort_keys=True, ensure_ascii=False, default=str)
    return {
        'opportunities': opportunities, 'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'snapshot_id': hashlib.sha256(fingerprint.encode()).hexdigest()[:20],
        'as_of_date': str(today), 'cutoff_date': str(today-timedelta(days=max_stale_days)),
        'total_scanned': len(all_codes), 'candidate_count': count, 'skipped_stale': skipped_stale,
        'stale_threshold_days': max_stale_days,
        'data_range': f'{min(dates)} ~ {max(dates)}' if dates else '无数据',
        'coverage': list(coverage.values()), 'empty_reason': empty_reason,
        'scoring_version': 'cheapness-v2: 原估值30与Z-score15按45归一至100；趋势只列风险，未经回测优化',
        'admission': {'max_cheapness_percentile': 30, 'min_score': min_score, 'min_observations': 3},
    }


def _score_series(code, metric, source, records, stale):
    latest = records[-1]
    dividend = metric == '股息率'
    cheap = [100-r['percentile'] if dividend else r['percentile'] for r in records]
    p, z = cheap[-1], latest.get('zscore')
    direction_z = -z if dividend and z is not None else z
    valuation = 30 if p < 5 else 27 if p < 10 else 22 if p < 20 else 16 if p <= 30 else 8 if p < 50 else 0
    zscore = 0 if direction_z is None else 15 if direction_z < -2 else 12 if direction_z < -1.5 else 9 if direction_z < -1 else 5 if direction_z < -.5 else 0
    level = '极度低估' if p < 10 else '低估' if p < 20 else '偏低' if p <= 30 else '适中' if p < 50 else '高估'
    consecutive = 0
    for i in range(len(cheap)-1, 0, -1):
        if cheap[i] >= cheap[i-1]:
            break
        consecutive += 1
    recent = records[-7:]
    change = cheap[-1]-cheap[-len(recent)]
    drop = -change
    start = records[-1-consecutive]['snapshot_date'] if consecutive else None
    span = (date.fromisoformat(latest['snapshot_date'])-date.fromisoformat(start)).days if start else None
    trend = '估值便宜程度加深' if drop > 0 else '估值便宜程度减弱' if drop < 0 else '估值分位平稳'
    risk = 'high' if consecutive >= 3 or drop > 5 else 'medium' if drop > 0 else 'low'
    short = {'市盈率': 'PE', '市净率': 'PB', '市销率': 'PS', '市销率TTM': 'PS', '股息率': '股息率'}[metric]
    score = round((valuation+zscore)/45*100, 2)
    return {
        'index_code': code, 'index_name': latest.get('index_name') or code,
        'score': score, 'historical_score': score,
        'score_breakdown': {
            'valuation': {'score': round(valuation/45*100, 2), 'max': 66.67, 'detail': f'便宜分位{p:.1f}% → {level}'},
            'zscore': {'score': round(zscore/45*100, 2), 'max': 33.33, 'detail': f'Z-score {z:+.2f}' if z is not None else '无数据'},
            'consecutive': {'score': 0, 'max': 0, 'detail': f'便宜分位连续{consecutive}期下降（风险参考）'},
            'drop_7d': {'score': 0, 'max': 0, 'detail': f'{len(recent)}条观测便宜分位下降{drop:.1f}个百分点'},
            'acceleration': {'score': 0, 'max': 0, 'detail': '趋势不计入低估分数'},
        },
        'current_percentile': latest['percentile'], 'cheapness_percentile': round(p, 2),
        'current_value': latest['current_value'], 'zscore': z,
        'consecutive_drop_days': consecutive,
        'consecutive_window': {'start_date': start, 'end_date': latest['snapshot_date'], 'span_days': span},
        'drop_7d': round(drop, 2), 'drop_trend': trend,
        'recent_window': {'start_date': recent[0]['snapshot_date'], 'end_date': latest['snapshot_date'],
                          'observations': len(recent), 'span_days': (date.fromisoformat(latest['snapshot_date'])-date.fromisoformat(recent[0]['snapshot_date'])).days,
                          'percentile_change_pp': round(latest['percentile']-recent[0]['percentile'], 2),
                          'cheapness_change_pp': round(change, 2), 'unit': '百分点'},
        'trend_risk': {'level': risk, 'description': '估值分位变化不代表价格跌幅；低估不保证反转', 'consecutive_observations': consecutive},
        'valuation_level': level, 'tags': [level, f'{len(recent)}条观测'],
        'summary': f"{latest.get('index_name') or code} [{short}] 分位{latest['percentile']:.1f}%，便宜分位{p:.1f}%；{trend}，需核验基本面和趋势风险",
        'latest_date': latest['snapshot_date'], 'data_source': source,
        'data_source_verified': all(r.get('source_basis') in ('explicit_provider', 'dedicated_table') for r in records),
        'data_source_basis': latest.get('source_basis', 'legacy_filename_unverified'),
        'data_source_note': '旧图片文件名推断来源未核验' if any(r.get('source_basis') == 'legacy_filename_unverified' for r in records) else '显式提供者或机构专表来源',
        'data_points': len(records),
        'metric_type': metric, 'metric_short': short, 'stale_days': stale,
        'data_freshness': 'fresh' if stale <= 3 else 'aging' if stale <= 7 else 'stale',
    }


def validate_interpretations(items, candidates):
    """模型只补解释；白名单、枚举、结构校验，事实一律由候选快照回填。"""
    if not isinstance(items, list):
        raise ValueError('模型解读必须是列表')
    whitelist = {c['index_code']: c for c in candidates}
    result, warnings, seen = [], [], set()
    enums = {'opportunity_type': {'真低估', '价值陷阱', '趋势性下行'},
             'action': {'立即买入', '分批建仓', '观望', '回避'},
             'confidence': {'high', 'medium', 'low'}}
    for item in items:
        if not isinstance(item, dict):
            warnings.append('丢弃非对象解读')
            continue
        code = normalize_index_code(str(item.get('index_code') or ''))
        if code not in whitelist or code in seen or any(not isinstance(item.get(k), str) or item[k] not in values for k, values in enums.items()):
            warnings.append(f'丢弃候选外、重复或枚举无效的解读: {code}')
            continue
        if any(not isinstance(item.get(k, []), list) or any(not isinstance(v, str) for v in item.get(k, [])) for k in ('catalysts', 'risk_factors')):
            warnings.append(f'丢弃列表字段无效解读: {code}')
            continue
        if not isinstance(item.get('action_detail', ''), str):
            warnings.append(f'丢弃建议字段无效解读: {code}')
            continue
        recovery = item.get('recovery_time')
        if recovery is not None and (not isinstance(recovery, str) or recovery not in {'短期(1-3月)', '中期(3-12月)', '长期(1年+)', '短期（1-3个月）', '中期（3-12个月）', '长期（1年以上）'}):
            warnings.append(f'丢弃恢复时间无效解读: {code}')
            continue
        c = whitelist[code]
        row = {k: item[k] for k in ('opportunity_type', 'action', 'confidence', 'catalysts', 'risk_factors', 'action_detail', 'recovery_time') if k in item}
        row.update({k: c.get(k) for k in ('index_code', 'index_name', 'metric_type', 'latest_date', 'current_percentile', 'current_value', 'online_pe', 'online_data_date', 'forecast_signal', 'current_verification')})
        row['rule_score'] = c['score']
        row['historical_score'] = c['score']
        row['catalysts_status'] = 'hypothesis'
        result.append(row)
        seen.add(code)
    if len(seen) < len(whitelist):
        warnings.append('部分候选缺少有效模型解读')
    return result, warnings


async def enrich_candidate(opp, fetch_online, mean_reversion, extreme, *, online_threshold_days=3):
    """在线核验与历史评分独立；无法确认历史口径时不改旧分位和分数。"""
    import asyncio
    code, metric = opp['index_code'], opp['metric_type']
    out = {**opp, 'historical_score': opp['score'], 'online_pe': None, 'online_data_date': '',
           'forecast_signal': '', 'mean_reversion': None, 'extreme_warning': None}
    verification = {'status': 'not_needed', 'metric_type': metric, 'comparable': False,
                    'note': '在线数据与原机构分位口径未证实可比，历史评分不重算'}
    if opp.get('stale_days', 0) > online_threshold_days:
        try:
            online = await asyncio.wait_for(asyncio.to_thread(fetch_online, normalize_index_code(code), metric), timeout=20)
            if not online:
                verification['status'] = 'unavailable'
            elif online.get('metric_type') != metric:
                verification.update(status='metric_mismatch', returned_metric_type=online.get('metric_type'))
            else:
                value, pct = _number(online.get('current_value')), _number(online.get('percentile'))
                day = _valid_day(online.get('snapshot_date'))
                historical_day = _valid_day(opp.get('latest_date'))
                if value is None or value <= 0 or day is None or day > date.today() or (historical_day and day < historical_day):
                    verification['status'] = 'invalid_or_stale'
                else:
                    verification.update(status='available', current_value=value,
                                        percentile=pct if pct is not None and 0 <= pct <= 100 else None,
                                        snapshot_date=str(day), source=online.get('source', 'online'))
                    out['online_value'] = value
                    out['online_metric_type'] = metric
                    # 兼容旧字段但 PE 仅对真实 PE 提供；PB/PS/股息率用通用 online_value。
                    out['online_pe'] = value if metric == '市盈率' else None
                    out['online_data_date'] = str(day)
                    out['online_source'] = verification['source']
        except Exception as exc:
            verification.update(status='error', error=str(exc))
    out['current_verification'] = verification

    async def predict(fn):
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn, code, metric), timeout=20)
            return result if isinstance(result, dict) else None
        except Exception as exc:
            return {'status': 'error', 'error': str(exc)}
    mr, ew = await asyncio.gather(predict(mean_reversion), predict(extreme))
    out['mean_reversion'], out['extreme_warning'] = mr, ew
    unavailable = {'insufficient_data', 'error', 'failed', 'unavailable', 'unknown', ''}
    for prediction in (mr, ew):
        if not prediction or prediction.get('error') or prediction.get('status') in unavailable- {''}:
            continue
        if prediction.get('metric_type', metric) != metric:
            continue
        signal = prediction.get('signal')
        if isinstance(signal, str) and signal not in unavailable:
            out['forecast_signal'] = signal
            break
        if prediction.get('is_extreme') is True and prediction.get('direction') in ('high', 'low'):
            out['forecast_signal'] = f"extreme_{prediction['direction']}"
            break
    out['forecast_status'] = 'available' if out['forecast_signal'] else 'unavailable'
    out['forecast_note'] = '预测为同指标辅助统计，未证实与评分来源可比；高低表示原指标方向，不保证反转'
    return out
