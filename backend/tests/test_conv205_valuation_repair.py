"""历史修复只触及已核对原图的两条记录，预览、备份、幂等及证据守卫。"""
import hashlib
import importlib
import json
import sqlite3

import pytest

from db._conn import _get_conn


@pytest.fixture
def repair_case(tmp_path, monkeypatch):
    migration = importlib.import_module('scripts.repair_conv205_valuations')
    hashes = {}
    for path in migration.EVIDENCE_SHA256:
        image = tmp_path / path
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(path.encode())
        hashes[path] = hashlib.sha256(image.read_bytes()).hexdigest()
    monkeypatch.setattr(migration, 'EVIDENCE_SHA256', hashes)
    conn = _get_conn()
    conn.execute('''INSERT INTO index_valuations
        (id,index_code,index_name,snapshot_date,metric_type,current_value,percentile,source_image,raw_json)
        VALUES (1868,'HSTECH','恒生科技','2026-10-19','市销率',1.44,62,?,?)''',
        (str(tmp_path / migration.INDEX_IMAGE), json.dumps({'市销率TTM统计指标': {'当前值': 1.44, '分位点': 0.62}})))
    conn.execute('''INSERT INTO liuyi_valuations (id,image_path,update_date,index_count,raw_json)
        VALUES (3,?,'2026-10-19',2,?)''', (migration.LIUYI_IMAGE, json.dumps({'ok': True, 'data': [
            {'index_name': '恒生科技', 'pe': 1.44, 'pe_percentile': 1.3, 'pb': 2.23, 'pb_percentile': 10.1},
            {'index_name': '其他指数', 'pe': 1.44, 'pe_percentile': 5}]})))
    conn.commit()
    yield migration, conn, tmp_path
    conn.close()


def test_dry_run_is_reviewable_and_does_not_mutate(repair_case):
    migration, conn, root = repair_case
    report = migration.repair_conv205_valuations(conn, root)
    assert len(report['changes']) == 2
    assert report['changes'][0]['before'] != report['changes'][0]['after']
    assert conn.execute('SELECT percentile FROM index_valuations WHERE id=1868').fetchone()[0] == 62
    assert report['applied'] is False


def test_apply_creates_backup_and_is_idempotent(repair_case):
    migration, conn, root = repair_case
    backup = root / 'before.sqlite'
    result = migration.repair_conv205_valuations(conn, root, apply=True, backup_path=backup)
    assert result['applied'] is True
    with sqlite3.connect(backup) as snapshot:
        assert snapshot.execute('SELECT percentile FROM index_valuations WHERE id=1868').fetchone()[0] == 62
    row = conn.execute('SELECT percentile,raw_json FROM index_valuations WHERE id=1868').fetchone()
    assert row[0] == 0.62
    assert json.loads(row[1])['_valuation_contract']['history_window'] == '10Y'
    data = json.loads(conn.execute('SELECT raw_json FROM liuyi_valuations WHERE id=3').fetchone()[0])['data']
    assert data[0]['pe'] is None and data[0]['ps'] == 1.44
    assert data[0]['ps_percentile'] == 1.3 and data[0]['pb_percentile'] == 10.1
    assert data[1] == {'index_name': '其他指数', 'pe': 1.44, 'pe_percentile': 5}
    again = migration.repair_conv205_valuations(conn, root, apply=True, backup_path=backup)
    assert again['changes'] == []
    assert again['already_applied'] == 2


def test_wrong_original_value_blocks_atomic_apply(repair_case):
    migration, conn, root = repair_case
    conn.execute('UPDATE index_valuations SET percentile=30 WHERE id=1868')
    conn.commit()
    with pytest.raises(ValueError, match='原值|证据'):
        migration.repair_conv205_valuations(conn, root, apply=True, backup_path=root/'backup.sqlite')
    data = json.loads(conn.execute('SELECT raw_json FROM liuyi_valuations WHERE id=3').fetchone()[0])
    assert data['data'][0]['pe'] == 1.44


def test_changed_source_image_blocks_apply(repair_case):
    migration, conn, root = repair_case
    (root / migration.INDEX_IMAGE).write_bytes(b'changed')
    with pytest.raises(ValueError, match='证据'):
        migration.repair_conv205_valuations(conn, root, apply=True, backup_path=root/'backup.sqlite')


def test_apply_requires_backup_destination(repair_case):
    migration, conn, root = repair_case
    with pytest.raises(ValueError, match='备份'):
        migration.repair_conv205_valuations(conn, root, apply=True)
