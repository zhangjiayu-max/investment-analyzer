#!/usr/bin/env python3
"""同步知识库数据到 RAG 索引。

将 knowledge_base 表的数据同步到：
1. knowledge_fts 表（FTS5 全文检索）
2. ChromaDB（向量检索）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db._conn import _get_conn
from db.knowledge import list_knowledge
from services.rag import index_to_chroma


def sync_to_fts():
    """同步知识库到 FTS5 索引。"""
    print("同步到 FTS5 索引...")

    conn = _get_conn()

    # 检查 knowledge_fts 表结构
    try:
        conn.execute("SELECT content_type FROM knowledge_fts LIMIT 1")
    except Exception:
        print("  knowledge_fts 表不存在，跳过")
        conn.close()
        return 0

    # 获取已索引的知识库条目
    indexed = set()
    for row in conn.execute(
        "SELECT reference_id FROM knowledge_fts WHERE content_type = 'knowledge'"
    ).fetchall():
        indexed.add(row[0])

    # 获取所有知识库条目
    items = list_knowledge(limit=10000)
    print(f"  知识库条目: {len(items)}")
    print(f"  已索引: {len(indexed)}")

    # 插入新条目
    count = 0
    for item in items:
        ref_id = f"kb_{item['id']}"
        if ref_id in indexed:
            continue

        # 构建 FTS 内容
        keywords = item.get("keywords", [])
        if isinstance(keywords, list):
            keywords_str = " ".join(keywords)
        else:
            keywords_str = str(keywords)

        body = f"{item['title']} {item.get('content', '')} {keywords_str}"

        try:
            conn.execute("""
                INSERT INTO knowledge_fts (content_type, title, body, reference_id)
                VALUES (?, ?, ?, ?)
            """, ("knowledge", item["title"], body[:5000], ref_id))
            count += 1
        except Exception as e:
            print(f"  插入失败: {item['title']} - {e}")

    conn.commit()
    conn.close()

    print(f"  新增索引: {count}")
    return count


def sync_to_chroma():
    """同步知识库到 ChromaDB 向量索引。"""
    print("\n同步到 ChromaDB...")

    items = list_knowledge(limit=10000)
    print(f"  知识库条目: {len(items)}")

    count = 0
    for item in items:
        try:
            keywords = item.get("keywords", [])
            if isinstance(keywords, list):
                keywords_str = " ".join(keywords)
            else:
                keywords_str = str(keywords)

            title = f"[知识库] {item['title']}"
            body = item.get("content", "")[:8000]

            index_to_chroma(
                content_type="knowledge",
                reference_id=str(item["id"]),
                title=title,
                body=body
            )
            count += 1
        except Exception as e:
            print(f"  索引失败: {item['title']} - {e}")

    print(f"  新增索引: {count}")
    return count


def main():
    print("=" * 50)
    print("同步知识库到 RAG 索引")
    print("=" * 50)

    fts_count = sync_to_fts()
    chroma_count = sync_to_chroma()

    print("\n" + "=" * 50)
    print(f"同步完成!")
    print(f"  FTS5 新增: {fts_count}")
    print(f"  ChromaDB 新增: {chroma_count}")
    print("=" * 50)


if __name__ == "__main__":
    main()
