#!/usr/bin/env python3
"""修复 fund 分类的 FTS 和 ChromaDB 索引。

背景：21 条基金档案原 category='book'，已通过 SQL 改为 category='fund'，
但 FTS 表 content_type 字段和 ChromaDB 的 metadata.content_type 仍是旧值 'book'。

本脚本：
1. 删除 FTS 表中这21条的旧索引，用 content_type='fund' 重新插入
2. 删除 ChromaDB 中这21条的旧向量，用 content_type='fund' 重新索引
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db._conn import _get_conn
from db.knowledge import get_knowledge
from services.rag.rag import _index_knowledge_entry, delete_chroma_by_filter


def main():
    print("=" * 50)
    print("修复 fund 分类的 FTS + ChromaDB 索引")
    print("=" * 50)

    # 1. 读取所有 category='fund' 的记录
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, title, content, source, keywords, importance,
               atom_type, evidence_level, subcategory
        FROM knowledge_base WHERE category='fund'
    """).fetchall()
    conn.close()

    print(f"找到 {len(rows)} 条 fund 分类记录")

    # 2. 逐条修复
    fixed_fts = 0
    fixed_chroma = 0
    for row in rows:
        kid = row["id"]
        title = row["title"] or ""
        content = row["content"] or ""
        source = row["source"] or ""
        importance = row["importance"] or 5
        atom_type = row["atom_type"] or ""
        evidence_level = row["evidence_level"] or ""

        # 解析 keywords
        import json
        try:
            keywords = json.loads(row["keywords"]) if row["keywords"] else []
        except (json.JSONDecodeError, TypeError):
            keywords = []
        keywords_str = " ".join(keywords) if isinstance(keywords, list) else str(keywords)

        # 2.1 删除 FTS 旧索引（content_type='book'）
        try:
            conn = _get_conn()
            conn.execute(
                "DELETE FROM knowledge_fts WHERE content_type='book' AND reference_id=?",
                (str(kid),)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  FTS 删除失败 kid={kid}: {e}")

        # 2.2 删除 ChromaDB 旧向量（content_type='book'）
        try:
            delete_chroma_by_filter("book", reference_id=str(kid))
        except Exception as e:
            print(f"  ChromaDB 删除失败 kid={kid}: {e}")

        # 2.3 用 content_type='fund' 重新索引 FTS + ChromaDB
        try:
            body = f"{title} {content} {keywords_str}"
            _index_knowledge_entry(
                content_type="fund",
                knowledge_id=kid,
                title=title,
                content=body[:5000],
                source=source,
                atom_type=atom_type,
                evidence_level=evidence_level,
                importance=importance,
            )
            fixed_fts += 1
            fixed_chroma += 1
            print(f"  ✓ kid={kid} {source[:30]}")
        except Exception as e:
            print(f"  重新索引失败 kid={kid}: {e}")

    print()
    print("=" * 50)
    print(f"修复完成！FTS 修复 {fixed_fts} 条，ChromaDB 修复 {fixed_chroma} 条")
    print("=" * 50)

    # 3. 验证
    conn = _get_conn()
    fts_fund = conn.execute(
        "SELECT COUNT(*) FROM knowledge_fts WHERE content_type='fund'"
    ).fetchone()[0]
    fts_book = conn.execute(
        "SELECT COUNT(*) FROM knowledge_fts WHERE content_type='book'"
    ).fetchone()[0]
    conn.close()
    print(f"验证：FTS 中 fund={fts_fund} 条，book={fts_book} 条")


if __name__ == "__main__":
    main()
