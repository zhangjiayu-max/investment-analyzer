"""按逐条显式单位证据迁移比例制百分位；禁止按值小于1猜尺度。"""

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db._conn import _get_conn

_TARGETS = {
    'index_valuations': 'percentile',
    'smart_add_plans': 'valuation_percentile',
    'theme_opportunities': 'valuation_percentile',
    'theme_opportunity_backtests': 'entry_percentile',
}


def migrate_percentile_scale(conn, apply=False, *, evidence=None, backup_path=None):
    """evidence={table:[{id,unit:'ratio'|'percent',value,source}]}。

    value 是人工核对的源值，source 是单位证据来源；仅更新显式 ratio 条目。
    默认空预览不扫描数值；无证据禁止提交。整批校验、全库备份、事务提交。
    """
    result = {table: {'candidates': 0, 'updated': 0, 'already_applied': 0, 'error': None, 'changes': []}
              for table in _TARGETS}
    if not evidence:
        if apply:
            raise ValueError('迁移必须提供逐条显式单位证据，禁止按数值猜尺度')
        return result
    if not isinstance(evidence, dict) or any(table not in _TARGETS for table in evidence):
        raise ValueError('证据包含不支持的数据表')
    updates, seen = [], set()
    for table, entries in evidence.items():
        if not isinstance(entries, list):
            raise ValueError('单位证据必须是逐条列表')
        column = _TARGETS[table]
        for item in entries:
            if not isinstance(item, dict) or item.get('unit') not in ('ratio', 'percent') or not item.get('source'):
                raise ValueError('每条证据必须显式提供原单位 unit 与来源 source')
            row_id, original = item.get('id'), item.get('value')
            if not isinstance(row_id, int) or (table, row_id) in seen:
                raise ValueError('证据记录 id 无效或重复')
            seen.add((table, row_id))
            if isinstance(original, bool) or not isinstance(original, (int, float)) or not math.isfinite(original):
                raise ValueError('证据原值必须是有限数值')
            limit = 1 if item['unit'] == 'ratio' else 100
            if not 0 <= original <= limit:
                raise ValueError('证据原值超出其声明单位范围')
            row = conn.execute(f'SELECT {column} FROM {table} WHERE id=?', (row_id,)).fetchone()
            if row is None:
                raise ValueError(f'证据记录不存在：{table}:{row_id}')
            current = row[0]
            converted = round(original * 100, 4) if item['unit'] == 'ratio' else original
            if current == converted:
                result[table]['already_applied'] += 1
                continue
            if current != original:
                raise ValueError(f'证据原值不符：{table}:{row_id}')
            result[table]['candidates'] += 1
            result[table]['changes'].append({'id': row_id, 'before': current, 'after': converted, 'source': item['source']})
            updates.append((table, column, row_id, current, converted))
    if not apply or not updates:
        return result
    if backup_path is None:
        raise ValueError('迁移提交必须指定备份路径 backup_path/--backup')
    backup_path = Path(backup_path)
    if backup_path.exists():
        raise ValueError('备份路径已存在，拒绝覆盖')
    if conn.in_transaction:
        raise ValueError('连接存在未完成事务')
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup_path) as backup:
        conn.backup(backup)
    try:
        conn.execute('BEGIN IMMEDIATE')
        for table, column, row_id, current, converted in updates:
            cursor = conn.execute(f'UPDATE {table} SET {column}=? WHERE id=? AND {column}=?', (converted, row_id, current))
            if cursor.rowcount != 1:
                raise ValueError('证据原值在审核后发生变化，已回滚')
            result[table]['updated'] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--evidence', type=Path, help='包含逐条 id/unit/value/source 的 JSON 文件')
    parser.add_argument('--backup', type=Path, help='提交前的全库备份（不能已存在）')
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text()) if args.evidence else None
    conn = _get_conn()
    try:
        result = migrate_percentile_scale(conn, apply=args.apply, evidence=evidence, backup_path=args.backup)
    finally:
        conn.close()
    print(json.dumps({'mode': 'apply' if args.apply else 'dry-run', **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
