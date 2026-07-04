#!/usr/bin/env python3
"""一键重建所有向量索引（用当前配置的 embedding 模型）。

用法：
    cd backend && python3 scripts/reindex_vectors.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.rag import (
    init_fts, init_chroma, reset_chroma_collection,
    index_book_knowledge, index_to_chroma, _get_embed_model,
)
from db.knowledge import list_knowledge
from db._conn import _get_conn


def main():
    init_fts()
    init_chroma()

    model = _get_embed_model()
    dim = model.get_embedding_dimension()
    print(f"当前 Embedding 模型: {dim} 维")

    # 1. 重建 ChromaDB collection
    print("\n[1/3] 重建 ChromaDB collection...")
    reset_chroma_collection()
    print("  ✅ 已清空旧向量")

    # 2. 重建书籍知识
    print("\n[2/3] 重建书籍知识向量...")
    books = list_knowledge(category="book", limit=10000)
    ok, fail = 0, 0
    for i, item in enumerate(books):
        try:
            index_book_knowledge(
                knowledge_id=item["id"],
                title=item["title"],
                content=item["content"],
                source=item.get("source", ""),
            )
            ok += 1
        except Exception as e:
            fail += 1
            if fail <= 3:
                print(f"  ❌ {item['title'][:40]}: {e}")
        if (i + 1) % 200 == 0:
            print(f"  进度: {i+1}/{len(books)}")
    print(f"  完成: {ok} 成功, {fail} 失败")

    # 3. 重建作者文章
    print("\n[3/3] 重建作者文章向量...")
    conn = _get_conn()
    articles = conn.execute(
        'SELECT id, title, summary, content_text FROM author_articles WHERE status = "done" LIMIT 10000'
    ).fetchall()
    ok2, fail2 = 0, 0
    for i, a in enumerate(articles):
        try:
            content = a["content_text"] or a["summary"] or ""
            if content:
                index_to_chroma("author_article", str(a["id"]), a["title"] or "", content[:5000])
                ok2 += 1
        except Exception as e:
            fail2 += 1
        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(articles)}")
    conn.close()
    print(f"  完成: {ok2} 成功, {fail2} 失败")

    # 验证
    from services.rag import _get_chroma
    collection = _get_chroma()
    total = collection.count()
    print(f"\n{'='*50}")
    print(f"重建完成!")
    print(f"  向量维度: {dim}")
    print(f"  ChromaDB 总记录: {total}")
    for ct in ["author_article", "book"]:
        r = collection.get(where={"content_type": ct})
        cnt = len(r["ids"]) if r and r["ids"] else 0
        if cnt > 0:
            print(f"    {ct}: {cnt}")


if __name__ == "__main__":
    main()
