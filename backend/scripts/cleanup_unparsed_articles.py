"""清理指定日期之前未解析的历史文章；默认只预览，--apply 备份后执行。"""
import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ROOT
from db._conn import _get_conn


def cleanup_articles(before: str, articles_file: Path, backup_root: Path, apply: bool = False) -> dict:
    """只删除 pending 且无任何分析记录的文章，同时裁剪静态同步清单。"""
    before = date.fromisoformat(before).isoformat()
    articles_file = Path(articles_file)
    original = articles_file.read_bytes()
    seeds = json.loads(original)
    if not isinstance(seeds, list):
        raise ValueError('articles.json 必须是文章数组')
    conn = _get_conn()
    backup_dir = None
    replaced = False
    try:
        if apply:
            conn.execute('BEGIN IMMEDIATE')
        candidates = conn.execute("""
            SELECT a.id, a.url FROM articles a
            WHERE date(a.publish_time) < ? AND a.status='pending'
              AND NOT EXISTS (SELECT 1 FROM analysis_records ar WHERE ar.article_id=a.id)
        """, (before,)).fetchall()
        candidate_ids = {r['id'] for r in candidates}
        protected_urls = {r['url'] for r in conn.execute('SELECT id,url FROM articles') if r['id'] not in candidate_ids}

        def removable_seed(article):
            try:
                published = date.fromisoformat(str(article.get('publish_time') or '')[:10])
            except ValueError:
                return False
            return published < date.fromisoformat(before) and article.get('url') not in protected_urls

        retained = [a for a in seeds if not removable_seed(a)]
        result = {'before': before, 'applied': apply, 'database_count': len(candidates),
                  'seed_count': len(seeds) - len(retained), 'backup_dir': None}
        if not apply or not (candidates or result['seed_count']):
            return result

        backup_root = Path(backup_root)
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=backup_root))
        # 独立读连接的 backup 包括已提交 WAL 数据；不在持有写事务的连接上执行 backup。
        source = _get_conn()
        destination = sqlite3.connect(backup_dir / 'valuations.db')
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        shutil.copy2(articles_file, backup_dir / 'articles.json')
        result['backup_dir'] = str(backup_dir.resolve())
        (backup_dir / 'cleanup.json').write_text(json.dumps({**result, 'article_ids': sorted(candidate_ids)}, ensure_ascii=False, indent=2), encoding='utf-8')
        conn.executemany('DELETE FROM articles WHERE id=?', [(r['id'],) for r in candidates])
        if result['seed_count']:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=articles_file.parent, delete=False, suffix='.tmp') as temp:
                json.dump(retained, temp, ensure_ascii=False, indent=2)
                temp.write('\n')
                temp_path = Path(temp.name)
            try:
                os.replace(temp_path, articles_file)
                replaced = True
            finally:
                temp_path.unlink(missing_ok=True)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        if replaced:
            articles_file.write_bytes(original)
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, help='不含当天，YYYY-MM-DD')
    parser.add_argument('--apply', action='store_true', help='备份后实际清理；默认预览')
    args = parser.parse_args()
    print(json.dumps(cleanup_articles(args.before, ROOT / 'doc/articles.json',
                                     ROOT / 'data/backups/article-cleanup', args.apply), ensure_ascii=False, indent=2))
