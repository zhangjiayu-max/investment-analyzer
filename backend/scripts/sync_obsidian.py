"""Obsidian Vault → knowledge_base 同步脚本

用法：
    python sync_obsidian.py --sync                          # 同步默认 vault
    python sync_obsidian.py --vault /path/to/vault --sync   # 指定 vault 路径
    python sync_obsidian.py --dry-run                       # 预览变更，不写入
    python sync_obsidian.py --stats                         # 查看同步状态

功能：
- 读取 Obsidian vault 中所有 .md 文件
- 解析 YAML frontmatter（title, tags, category, importance）
- 根据文件夹路径自动推断 subcategory
- 写入 knowledge_base 表（category="note"）
- 索引到 FTS5 + ChromaDB
- 增量同步：只处理新增/修改的文件
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db._conn import DB_PATH, _get_conn
from db.knowledge import add_knowledge

DEFAULT_VAULT = os.path.expanduser("~/Documents/KnowledgeBase")

# 跳过的目录
SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules"}
# 跳过的目录前缀
SKIP_PREFIXES = ("01-Daily", "06-Templates", "05-Archives")

# 文件夹 → subcategory 映射
SUBCATEGORY_MAP = {
    "策略": "strategy",
    "行业": "industry",
    "基金": "fund",
    "宏观": "macro",
    "Python": "tech",
    "AI": "tech",
    "前端": "tech",
    "心理学": "psychology",
    "决策": "decision",
    "书籍笔记": "book",
    "文章拆解": "article",
    "课程笔记": "course",
}

# frontmatter 中的 category → knowledge_base category
CATEGORY_MAP = {
    "book": "book",
    "article": "article",
    "investment": "note",
    "note": "note",
}


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (metadata_dict, body)。"""
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not match:
        return {}, content

    fm = {}
    for line in match.group(1).split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            key, val = line.split(':', 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # 处理 YAML 列表
            if val.startswith('[') and val.endswith(']'):
                try:
                    val = json.loads(val)
                except Exception:
                    pass
            fm[key] = val

    body = content[match.end():]
    return fm, body


def infer_subcategory(rel_path: str) -> str:
    """根据文件相对路径推断 subcategory。"""
    parts = Path(rel_path).parts
    for part in parts:
        if part in SUBCATEGORY_MAP:
            return SUBCATEGORY_MAP[part]
    return "general"


def should_skip(rel_path: str) -> bool:
    """判断是否跳过此文件。"""
    parts = Path(rel_path).parts
    for part in parts:
        if part in SKIP_DIRS:
            return True
        if any(part.startswith(p) for p in SKIP_PREFIXES):
            return True
    return False


def file_hash(path: str) -> str:
    """计算文件内容 MD5。"""
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def get_sync_state(conn: sqlite3.Connection) -> dict:
    """获取同步状态表（创建如不存在）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS obsidian_sync_state (
            file_path TEXT PRIMARY KEY,
            file_hash TEXT NOT NULL,
            knowledge_id INTEGER,
            synced_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    rows = conn.execute("SELECT file_path, file_hash, knowledge_id FROM obsidian_sync_state").fetchall()
    return {r[0]: {"hash": r[1], "knowledge_id": r[2]} for r in rows}


def sync_vault(vault_path: str, dry_run: bool = False) -> dict:
    """同步 Obsidian vault 到 knowledge_base。

    返回: {"added": int, "updated": int, "deleted": int, "skipped": int, "errors": list}
    """
    vault = Path(vault_path)
    if not vault.exists():
        print(f"❌ Vault 路径不存在: {vault_path}")
        return {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": []}

    conn = _get_conn()
    sync_state = get_sync_state(conn)

    stats = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": []}
    seen_paths = set()

    # 扫描 vault 中所有 .md 文件
    for root, dirs, files in os.walk(vault):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for fname in files:
            if not fname.endswith('.md'):
                continue

            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, vault)

            if should_skip(rel_path):
                stats["skipped"] += 1
                continue

            seen_paths.add(rel_path)
            current_hash = file_hash(full_path)
            prev = sync_state.get(rel_path)

            # 增量判断：文件未变化则跳过
            if prev and prev["hash"] == current_hash:
                stats["skipped"] += 1
                continue

            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                fm, body = parse_frontmatter(content)

                if len(body.strip()) < 30:
                    stats["skipped"] += 1
                    continue

                title = fm.get("title", fname.replace(".md", ""))
                tags = fm.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                importance = int(fm.get("importance", 5))
                fm_category = fm.get("category", "note")
                kb_category = CATEGORY_MAP.get(fm_category, "note")
                subcategory = fm.get("subcategory") or infer_subcategory(rel_path)
                source = f"obsidian://{rel_path}"
                keywords = json.dumps(tags, ensure_ascii=False) if tags else None

                if dry_run:
                    action = "更新" if prev else "新增"
                    print(f"  [{action}] {title} ({kb_category}/{subcategory})")
                    stats["updated" if prev else "added"] += 1
                    continue

                # 写入 knowledge_base
                kid = add_knowledge(
                    category=kb_category,
                    title=title,
                    content=body[:8000],
                    subcategory=subcategory,
                    source=source,
                    keywords=keywords,
                    importance=importance,
                )

                # 同步到 RAG（FTS + ChromaDB）
                try:
                    from rag import index_note_knowledge
                    index_note_knowledge(kid, title, body[:8000], source)
                except Exception as e:
                    stats["errors"].append(f"RAG 索引失败 {title}: {e}")

                # 更新同步状态
                conn.execute(
                    "INSERT OR REPLACE INTO obsidian_sync_state (file_path, file_hash, knowledge_id) VALUES (?, ?, ?)",
                    (rel_path, current_hash, kid),
                )

                if prev:
                    stats["updated"] += 1
                    print(f"  [更新] {title}")
                else:
                    stats["added"] += 1
                    print(f"  [新增] {title}")

            except Exception as e:
                stats["errors"].append(f"处理失败 {rel_path}: {e}")

    # 检查已删除的文件
    for rel_path in list(sync_state.keys()):
        if rel_path not in seen_paths:
            prev = sync_state[rel_path]
            if prev.get("knowledge_id") and not dry_run:
                from db.knowledge import delete_knowledge
                delete_knowledge(prev["knowledge_id"])
                conn.execute("DELETE FROM obsidian_sync_state WHERE file_path = ?", (rel_path,))
                print(f"  [删除] {rel_path}")
                stats["deleted"] += 1
            elif dry_run:
                print(f"  [删除] {rel_path}")
                stats["deleted"] += 1

    if not dry_run:
        conn.commit()
    conn.close()

    return stats


def print_stats(vault_path: str):
    """打印同步状态。"""
    conn = _get_conn()
    state = get_sync_state(conn)

    # 统计 vault 文件数
    vault = Path(vault_path)
    total_files = 0
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.endswith('.md'):
                rel = os.path.relpath(os.path.join(root, f), vault)
                if not should_skip(rel):
                    total_files += 1

    print(f"📊 Obsidian 同步状态")
    print(f"   Vault 路径: {vault_path}")
    print(f"   Vault 笔记数: {total_files}")
    print(f"   已同步到知识库: {len(state)}")
    print(f"   待同步: {total_files - len(state)}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Obsidian Vault → knowledge_base 同步")
    parser.add_argument("--vault", default=DEFAULT_VAULT, help="Obsidian vault 路径")
    parser.add_argument("--sync", action="store_true", help="执行同步")
    parser.add_argument("--dry-run", action="store_true", help="预览变更")
    parser.add_argument("--stats", action="store_true", help="查看状态")
    args = parser.parse_args()

    if args.stats:
        print_stats(args.vault)
        return

    if args.sync or args.dry_run:
        print(f"🔄 同步 Obsidian vault: {args.vault}")
        start = time.time()
        stats = sync_vault(args.vault, dry_run=args.dry_run)
        elapsed = time.time() - start

        print(f"\n{'=' * 40}")
        print(f"✅ 同步完成 ({elapsed:.1f}s)")
        print(f"   新增: {stats['added']}")
        print(f"   更新: {stats['updated']}")
        print(f"   删除: {stats['deleted']}")
        print(f"   跳过: {stats['skipped']}")
        if stats["errors"]:
            print(f"   ⚠️ 错误: {len(stats['errors'])}")
            for e in stats["errors"][:5]:
                print(f"      - {e}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
