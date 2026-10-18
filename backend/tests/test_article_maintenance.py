"""文章添加顺序与历史清理的边界、备份和重导入保护。"""
import json
from pathlib import Path

from db.articles import create_article, list_articles, sync_articles
from db._conn import _get_conn


def test_new_articles_first_even_without_publish_date():
    old = create_article('https://example.com/old', publish_time='2026-10-18')
    new = create_article('https://example.com/new')
    assert [a['id'] for a in list_articles()] == [new, old]
    assert [a['id'] for a in list_articles('pending')] == [new, old]


def test_cleanup_is_preview_by_default_and_preserves_boundary_and_analyzed(tmp_path):
    from scripts.cleanup_unparsed_articles import cleanup_articles
    seeds = [
        {'url': 'https://example.com/old', 'publish_time': '2026-04-27 23:59:59'},
        {'url': 'https://example.com/boundary', 'publish_time': '2026-04-28 00:00:00'},
        {'url': 'https://example.com/analyzed', 'publish_time': '2026-04-26'},
        {'url': 'https://example.com/unknown', 'publish_time': ''},
    ]
    sync_articles(seeds)
    conn = _get_conn()
    conn.execute("UPDATE articles SET status='analyzed' WHERE url=?", (seeds[2]['url'],))
    conn.commit()
    conn.close()
    seed_file = tmp_path / 'articles.json'
    seed_file.write_text(json.dumps(seeds), encoding='utf-8')
    result = cleanup_articles('2026-04-28', seed_file, tmp_path / 'backups')
    assert result['database_count'] == 1
    assert result['seed_count'] == 1
    assert len(list_articles()) == 4
    assert json.loads(seed_file.read_text()) == seeds
    result = cleanup_articles('2026-04-28', seed_file, tmp_path / 'backups', apply=True)
    assert len(list_articles()) == 3
    assert len(json.loads(seed_file.read_text())) == 3
    assert (Path(result['backup_dir']) / 'valuations.db').exists()
    assert json.loads((Path(result['backup_dir']) / 'articles.json').read_text()) == seeds
    sync_articles(json.loads(seed_file.read_text()))
    assert len(list_articles()) == 3


def test_cleanup_preserves_pending_article_with_analysis_record(tmp_path):
    from scripts.cleanup_unparsed_articles import cleanup_articles
    aid = create_article('https://example.com/has-record', publish_time='2026-04-27')
    conn = _get_conn()
    conn.execute("INSERT INTO analysis_records(article_id,image_path,status) VALUES (?,?,'error')", (aid, 'data/images/keep.png'))
    conn.commit()
    conn.close()
    seed_file = tmp_path / 'articles.json'
    seed_file.write_text('[]')
    result = cleanup_articles('2026-04-28', seed_file, tmp_path / 'backups', apply=True)
    assert result['database_count'] == 0
    assert len(list_articles()) == 1
