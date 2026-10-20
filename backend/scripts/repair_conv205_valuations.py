"""按已人工核对的原图修复 conv205 两条估值；默认只预览，提交必须备份。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

LIUYI_IMAGE = 'data/liuyi_images/2026-10-20/074714_640.jpeg'
INDEX_IMAGE = 'data/images/2026-10-19/2026-10-19A股美股港股/img_007.png'
EVIDENCE_SHA256 = {
    LIUYI_IMAGE: 'e143a98e8e9ec55295ba9abd56266c22f2ef80521282b6910c5001205137e902',
    INDEX_IMAGE: '2820165dce5c1679de213e792319da50428a6e839324bb4fd47aa064a3e25f10',
}
REPAIR_ID = '2026-10-20-conv205-verified-images-v1'


def repair_conv205_valuations(conn, evidence_root, *, apply=False, backup_path=None):
    """只接受固定来源、原值及图片摘要；任一证据异常拒绝整批提交。

    返回 before/after 供审核；SQLite backup 保存整库，事务只更新两条记录。
    不扫描并猜测其他低百分位或恒生科技 PE 记录。六亿历史窗口未知，保留 null。
    """
    evidence_root = Path(evidence_root)
    report = {'repair_id': REPAIR_ID, 'applied': False, 'changes': [], 'blocked': [], 'already_applied': 0}
    updates = []
    for table, record_id, image_path in [('index_valuations', 1868, INDEX_IMAGE), ('liuyi_valuations', 3, LIUYI_IMAGE)]:
        cursor = conn.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,))
        row = cursor.fetchone()
        if row is None:
            continue
        row = dict(zip([col[0] for col in cursor.description], row))
        try:
            expected_image = evidence_root / image_path
            if not expected_image.is_file() or hashlib.sha256(expected_image.read_bytes()).hexdigest() != EVIDENCE_SHA256[image_path]:
                raise ValueError('原图证据缺失或 SHA256 不符')
            source = row['source_image' if table == 'index_valuations' else 'image_path']
            if not source or not (Path(source).as_posix() == image_path or Path(source).resolve() == expected_image.resolve()):
                raise ValueError('来源图片路径不符')
            parsed = json.loads(row['raw_json'] or '{}')
            if not isinstance(parsed, dict):
                raise ValueError('原始 JSON 不是对象')
            before = copy.deepcopy(parsed)
            if table == 'index_valuations':
                if (row['index_code'], row['snapshot_date'], row['metric_type'], row['current_value']) != ('HSTECH', '2026-10-19', '市销率', 1.44):
                    raise ValueError('指数/日期/指标原值不符')
                metadata = parsed.get('_valuation_contract') or {}
                if row['percentile'] == 0.62 and metadata.get('repair_id') == REPAIR_ID:
                    report['already_applied'] += 1
                    continue
                stats = parsed.get('市销率TTM统计指标') or {}
                if row['percentile'] != 62 or stats.get('当前值') != 1.44 or stats.get('分位点') != 0.62:
                    raise ValueError('百分位/原始 JSON 原值不符')
                parsed['_valuation_contract'] = {**metadata, 'repair_id': REPAIR_ID,
                    'percentile_unit': 'percent', 'percentile_input_unit': 'percent', 'percentile_raw': 0.62,
                    'history_window': '10Y', 'metric_verified': True, 'validation_status': 'image_verified',
                    'evidence_sha256': EVIDENCE_SHA256[image_path]}
                report['changes'].append({'table': table, 'id': record_id, 'evidence': image_path,
                    'before': {'percentile': 62, 'raw_json': before}, 'after': {'percentile': 0.62, 'raw_json': parsed}})
                updates.append((table, record_id, row['raw_json'], json.dumps(parsed, ensure_ascii=False), 0.62))
            else:
                if row['update_date'] != '2026-10-19':
                    raise ValueError('六亿日期原值不符')
                matches = [item for item in parsed.get('data', []) if item.get('index_name') == '恒生科技']
                if len(matches) != 1:
                    raise ValueError('六亿指数行证据不唯一')
                item = matches[0]
                if item.get('repair_id') == REPAIR_ID and item.get('ps') == 1.44 and item.get('pe') is None:
                    report['already_applied'] += 1
                    continue
                if tuple(item.get(key) for key in ('pe','pe_percentile','pb','pb_percentile')) != (1.44,1.3,2.23,10.1):
                    raise ValueError('六亿指标原值不符')
                previous = copy.deepcopy(item)
                item.update(pe=None, pe_percentile=None, ps=1.44, ps_percentile=1.3,
                    metric_type='市销率', metric_verification={'ps': True, 'pb': True, 'pe': False},
                    percentile_unit='percent', history_window=item.get('history_window'),
                    validation_status='image_verified', repair_id=REPAIR_ID,
                    evidence_sha256=EVIDENCE_SHA256[image_path], repair_before=previous)
                report['changes'].append({'table': table, 'id': record_id, 'evidence': image_path,
                    'before': previous, 'after': copy.deepcopy(item)})
                updates.append((table, record_id, row['raw_json'], json.dumps(parsed, ensure_ascii=False), None))
        except (TypeError, ValueError, KeyError) as exc:
            report['blocked'].append({'table': table, 'id': record_id, 'reason': str(exc)})
    if not apply or not updates and not report['blocked']:
        return report
    if report['blocked']:
        raise ValueError('原值或原图证据校验失败：' + json.dumps(report['blocked'], ensure_ascii=False))
    if backup_path is None:
        raise ValueError('提交前必须指定备份路径 backup_path/--backup')
    if conn.in_transaction:
        raise ValueError('提交前连接不能存在未完成事务')
    backup_path = Path(backup_path)
    if backup_path.exists():
        raise ValueError('备份路径已存在，拒绝覆盖')
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    # 独立备份连接保证得到一致 SQLite 快照（含 WAL）。
    with sqlite3.connect(backup_path) as backup:
        conn.backup(backup)
    try:
        conn.execute('BEGIN IMMEDIATE')
        for table, record_id, original, corrected, percentile in updates:
            if table == 'index_valuations':
                cursor = conn.execute('UPDATE index_valuations SET percentile=?,raw_json=? WHERE id=? AND raw_json=? AND percentile=62',
                    (percentile, corrected, record_id, original))
            else:
                cursor = conn.execute('UPDATE liuyi_valuations SET raw_json=? WHERE id=? AND raw_json=?',
                    (corrected, record_id, original))
            if cursor.rowcount != 1:
                raise ValueError('原值在审核后发生变化，已回滚')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    report.update(applied=True, backup_path=str(backup_path))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    # 预览连接为只读；明确目标数据库不存在时不创建空库。
    uri = args.db.resolve().as_uri() + ('?mode=rw' if args.apply else '?mode=ro')
    with sqlite3.connect(uri, uri=True) as conn:
        result = repair_conv205_valuations(conn, args.evidence_root, apply=args.apply, backup_path=args.backup)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
