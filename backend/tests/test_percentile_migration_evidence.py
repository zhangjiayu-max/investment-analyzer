"""旧批量尺度迁移不得依据数值大小推测原单位。"""
import sqlite3

import pytest

from db._conn import _get_conn
from scripts.migrate_percentile_scale import migrate_percentile_scale


@pytest.fixture
def conn():
    connection = _get_conn()
    connection.execute('''INSERT INTO index_valuations
        (index_code,index_name,snapshot_date,metric_type,percentile)
        VALUES ('HSTECH','恒生科技','2026-10-19','市销率',0.62)''')
    connection.commit()
    yield connection
    connection.close()


def test_old_migration_does_not_guess_percentile_unit(conn):
    report = migrate_percentile_scale(conn)
    assert report['index_valuations']['candidates'] == 0
    with pytest.raises(ValueError, match='显式.*证据'):
        migrate_percentile_scale(conn, apply=True)
    assert conn.execute('SELECT percentile FROM index_valuations').fetchone()[0] == 0.62


def test_explicit_ratio_evidence_requires_backup_and_updates_only_named_row(conn, tmp_path):
    conn.execute('''INSERT INTO index_valuations
        (index_code,index_name,snapshot_date,metric_type,percentile)
        VALUES ('RATIO','比例指数','2026-10-19','市盈率',0.25)''')
    conn.commit()
    row_id = conn.execute("SELECT id FROM index_valuations WHERE index_code='RATIO'").fetchone()[0]
    evidence = {'index_valuations': [{'id': row_id, 'unit': 'ratio', 'value': 0.25, 'source': 'provider-contract-v1'}]}
    with pytest.raises(ValueError, match='备份'):
        migrate_percentile_scale(conn, apply=True, evidence=evidence)
    backup = tmp_path / 'before.sqlite'
    result = migrate_percentile_scale(conn, apply=True, evidence=evidence, backup_path=backup)
    assert result['index_valuations']['updated'] == 1
    assert conn.execute("SELECT percentile FROM index_valuations WHERE index_code='RATIO'").fetchone()[0] == 25
    assert conn.execute("SELECT percentile FROM index_valuations WHERE index_code='HSTECH'").fetchone()[0] == 0.62
    with sqlite3.connect(backup) as saved:
        assert saved.execute("SELECT percentile FROM index_valuations WHERE index_code='RATIO'").fetchone()[0] == 0.25
    again = migrate_percentile_scale(conn, apply=True, evidence=evidence, backup_path=backup)
    assert again['index_valuations']['updated'] == 0


def test_evidence_with_unexpected_current_value_rejects_apply(conn, tmp_path):
    row_id = conn.execute('SELECT id FROM index_valuations').fetchone()[0]
    evidence = {'index_valuations': [{'id': row_id, 'unit': 'ratio', 'value': 0.25, 'source': 'verified-source'}]}
    with pytest.raises(ValueError, match='原值'):
        migrate_percentile_scale(conn, apply=True, evidence=evidence, backup_path=tmp_path/'before.sqlite')


def test_percent_unit_evidence_never_changes_small_percentile(conn, tmp_path):
    row_id = conn.execute('SELECT id FROM index_valuations').fetchone()[0]
    evidence = {'index_valuations': [{'id': row_id, 'unit': 'percent', 'value': 0.62, 'source': 'verified-image'}]}
    result = migrate_percentile_scale(conn, apply=True, evidence=evidence, backup_path=tmp_path/'unused.sqlite')
    assert result['index_valuations']['updated'] == 0
    assert conn.execute('SELECT percentile FROM index_valuations').fetchone()[0] == 0.62
