"""personal-kb 蒸馏知识 → Obsidian Vault 同步

将 personal-kb ChromaDB 中的蒸馏知识点同步为 Obsidian 笔记。
每本书生成一个汇总笔记 + 每个知识点生成独立笔记。

用法：
    python sync_personal_kb.py --sync                    # 同步到默认 vault
    python sync_personal_kb.py --sync --vault /path      # 指定 vault
    python sync_personal_kb.py --dry-run                 # 预览
    python sync_personal_kb.py --stats                   # 查看状态
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

KB_DATA_DIR = os.path.expanduser("~/projects/personal-kb/data")
DEFAULT_VAULT = os.path.expanduser("~/Documents/KnowledgeBase")
SYNC_TARGET = "04-Resources/书籍笔记"

# 同步状态文件（记录已同步的 ID，避免重复）
STATE_FILE = os.path.expanduser("~/Documents/KnowledgeBase/.obsidian/personal_kb_sync.json")


def load_sync_state() -> dict:
    """加载同步状态。"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"synced_ids": {}, "last_sync": None}


def save_sync_state(state: dict):
    """保存同步状态。"""
    state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_all_knowledge() -> list[dict]:
    """从 personal-kb ChromaDB 读取所有知识点。"""
    import chromadb
    client = chromadb.PersistentClient(path=KB_DATA_DIR)
    collection = client.get_collection("knowledge")

    # 获取总数
    count = collection.count()
    if count == 0:
        return []

    # 分批读取
    all_items = []
    batch_size = 500
    offset = 0
    while offset < count:
        results = collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"]
        )
        for doc, meta, doc_id in zip(results["documents"], results["metadatas"], results["ids"]):
            all_items.append({
                "id": doc_id,
                "title": meta.get("title", "未知"),
                "keywords": meta.get("keywords", ""),
                "category": meta.get("category", "general"),
                "source": meta.get("source", "未知来源"),
                "importance": meta.get("importance", 5),
                "content": doc,
            })
        offset += batch_size

    return all_items


def sanitize_filename(name: str) -> str:
    """清理文件名中的特殊字符。"""
    return name.replace("/", "-").replace("\\", "-").replace(":", "：").replace("?", "？").strip()


def generate_book_note(book_name: str, items: list[dict]) -> str:
    """生成单本书的汇总笔记。"""
    # 按 category 分组
    by_category = defaultdict(list)
    for item in items:
        cat = item["category"]
        if isinstance(cat, list):
            cat = cat[0] if cat else "general"
        by_category[str(cat)].append(item)

    # 按 importance 排序
    for cat in by_category:
        by_category[cat].sort(key=lambda x: -x.get("importance", 5))

    categories = sorted(by_category.keys())
    avg_importance = sum(i.get("importance", 5) for i in items) / max(len(items), 1)

    lines = [
        "---",
        f'title: "{book_name}（蒸馏笔记）"',
        f'source: personal-kb',
        "category: book",
        f"tags: [读书, personal-kb, 蒸馏]",
        f"importance: {round(avg_importance)}",
        f"created: \"{time.strftime('%Y-%m-%d')}\"",
        f"knowledge_count: {len(items)}",
        "---",
        "",
        f"# {book_name}（蒸馏笔记）",
        "",
        f"> 由 personal-kb 蒸馏系统自动生成，共 {len(items)} 个知识点",
        f"> 分类：{', '.join(categories)}",
        "",
        "## 知识点总览",
        "",
    ]

    for cat in categories:
        cat_items = by_category[cat]
        lines.append(f"### {cat}（{len(cat_items)} 条）")
        lines.append("")
        for item in cat_items:
            imp = item.get("importance", 5)
            star = "⭐" * min(imp, 5) if imp >= 7 else ""
            lines.append(f"- **{item['title']}** {star}")
            kw = item.get("keywords", "")
            if isinstance(kw, list):
                kw = ", ".join(kw)
            if kw:
                lines.append(f"  - 关键词：{kw}")
        lines.append("")

    return "\n".join(lines)


def generate_item_note(item: dict, book_name: str) -> str:
    """生成单个知识点的独立笔记。"""
    tags = ["personal-kb", "蒸馏"]
    keywords = item.get("keywords", "")
    if isinstance(keywords, list):
        keywords = ", ".join(keywords)
    if keywords:
        tags.extend([k.strip() for k in keywords.split(",")[:3]])

    cat = item.get("category", "general")
    if isinstance(cat, list):
        cat = cat[0] if cat else "general"

    lines = [
        "---",
        f'title: "{item["title"]}"',
        f'source: "{book_name}"',
        f'category: note',
        f'subcategory: "{cat}"',
        f'tags: [{", ".join(tags)}]',
        f'importance: {item.get("importance", 5)}',
        f'created: "{time.strftime("%Y-%m-%d")}"',
        "---",
        "",
        f"# {item['title']}",
        "",
        f"> 来源：{book_name} | 分类：{cat} | 重要性：{item.get('importance', 5)}/10",
        "",
        item["content"],
        "",
    ]

    if keywords:
        lines.append("## 关键词")
        lines.append("")
        for kw in keywords.split(","):
            lines.append(f"- {kw.strip()}")
        lines.append("")

    return "\n".join(lines)


def sync_to_obsidian(vault_path: str, dry_run: bool = False) -> dict:
    """同步 personal-kb 知识到 Obsidian vault。"""
    target_dir = os.path.join(vault_path, SYNC_TARGET)
    os.makedirs(target_dir, exist_ok=True)

    state = load_sync_state()
    synced_ids = state.get("synced_ids", {})

    print("📖 读取 personal-kb 知识库...")
    items = get_all_knowledge()
    print(f"   共 {len(items)} 个知识点")

    # 按书分组
    by_book = defaultdict(list)
    for item in items:
        by_book[item["source"]].append(item)

    stats = {"books": 0, "items_added": 0, "items_updated": 0, "items_skipped": 0}

    for book_name, book_items in by_book.items():
        safe_name = sanitize_filename(book_name)
        book_dir = os.path.join(target_dir, safe_name)
        os.makedirs(book_dir, exist_ok=True)

        # 生成书汇总笔记
        summary_path = os.path.join(book_dir, f"{safe_name}（汇总）.md")
        summary_content = generate_book_note(book_name, book_items)

        if not dry_run:
            with open(summary_path, 'w', encoding='utf-8') as f:
                f.write(summary_content)
        print(f"\n📚 {book_name}（{len(book_items)} 个知识点）")

        for item in book_items:
            item_id = item["id"]
            content_hash = hashlib.md5(item["content"].encode()).hexdigest()

            # 增量判断
            if item_id in synced_ids and synced_ids[item_id] == content_hash:
                stats["items_skipped"] += 1
                continue

            # 生成知识点笔记
            safe_title = sanitize_filename(item["title"])[:60]
            item_path = os.path.join(book_dir, f"{safe_title}.md")
            item_content = generate_item_note(item, book_name)

            if not dry_run:
                with open(item_path, 'w', encoding='utf-8') as f:
                    f.write(item_content)
                synced_ids[item_id] = content_hash

            action = "更新" if item_id in synced_ids else "新增"
            print(f"  [{action}] {item['title'][:50]}")
            stats[f"items_{'updated' if action == '更新' else 'added'}"] += 1

        stats["books"] += 1

    if not dry_run:
        state["synced_ids"] = synced_ids
        save_sync_state(state)

    return stats


def print_stats():
    """打印同步状态。"""
    state = load_sync_state()
    items = get_all_knowledge()

    by_book = defaultdict(int)
    for item in items:
        by_book[item["source"]] += 1

    print("📊 personal-kb 同步状态")
    print(f"   知识点总数: {len(items)}")
    print(f"   已同步: {len(state.get('synced_ids', {}))}")
    print(f"   待同步: {len(items) - len(state.get('synced_ids', {}))}")
    print(f"   上次同步: {state.get('last_sync', '从未')}")
    print()
    print("   书籍分布:")
    for book, count in sorted(by_book.items(), key=lambda x: -x[1]):
        print(f"     {book}: {count} 条")


def main():
    parser = argparse.ArgumentParser(description="personal-kb → Obsidian 同步")
    parser.add_argument("--vault", default=DEFAULT_VAULT, help="Obsidian vault 路径")
    parser.add_argument("--sync", action="store_true", help="执行同步")
    parser.add_argument("--dry-run", action="store_true", help="预览变更")
    parser.add_argument("--stats", action="store_true", help="查看状态")
    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    if args.sync or args.dry_run:
        print(f"🔄 同步 personal-kb → Obsidian")
        start = time.time()
        stats = sync_to_obsidian(args.vault, dry_run=args.dry_run)
        elapsed = time.time() - start

        print(f"\n{'=' * 40}")
        print(f"✅ 同步完成 ({elapsed:.1f}s)")
        print(f"   书籍: {stats['books']}")
        print(f"   新增: {stats['items_added']}")
        print(f"   更新: {stats['items_updated']}")
        print(f"   跳过: {stats['items_skipped']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
