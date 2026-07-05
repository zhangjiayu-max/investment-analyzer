"""知识图谱 CRUD 操作 — 实体、关系、知识关联。"""

import json
import logging
from db._conn import _get_conn

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────

VALID_ENTITY_TYPES = {"stock", "fund", "index", "concept", "industry", "person"}
VALID_REL_TYPES = {"belongs_to", "correlates_with", "competes_with", "supplies", "triggers"}


# ── 建表 ──────────────────────────────────────────────

def init_knowledge_graph(conn=None):
    """创建知识图谱相关表。传入 conn 避免死锁（与 init_db 一致）。"""
    should_close = False
    if conn is None:
        conn = _get_conn()
        should_close = True

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            aliases_json TEXT DEFAULT '[]',
            attributes_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(entity_type, name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_entities_type ON kg_entities(entity_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(name)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
            target_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
            rel_type TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            evidence_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(source_id, target_id, rel_type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_rel_source ON kg_relationships(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_rel_target ON kg_relationships(target_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_rel_type ON kg_relationships(rel_type)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_entity_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
            knowledge_id INTEGER NOT NULL,
            mention_context TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(entity_id, knowledge_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_mention_entity ON kg_entity_mentions(entity_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_mention_knowledge ON kg_entity_mentions(knowledge_id)")

    if should_close:
        conn.commit()
        conn.close()
    logger.info("知识图谱表初始化完成")


# ── 工具函数 ──────────────────────────────────────────

def _parse_entity_row(row) -> dict:
    """将数据库行解析为字典，反序列化 JSON 字段。"""
    if row is None:
        return None
    d = dict(row)
    for key in ("aliases_json", "attributes_json"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = [] if "aliases" in key else {}
        else:
            d[key] = [] if "aliases" in key else {}
    return d


def _parse_relationship_row(row) -> dict:
    """将关系行解析为字典。"""
    if row is None:
        return None
    d = dict(row)
    if d.get("evidence_json"):
        try:
            d["evidence_json"] = json.loads(d["evidence_json"])
        except (json.JSONDecodeError, TypeError):
            d["evidence_json"] = []
    else:
        d["evidence_json"] = []
    return d


# ── Entity CRUD ───────────────────────────────────────

def add_entity(entity_type: str, name: str, aliases: list = None,
               attributes: dict = None) -> int:
    """添加实体，返回 ID。若已存在则更新并返回已有 ID。"""
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"无效 entity_type: {entity_type}，可选: {VALID_ENTITY_TYPES}")

    conn = _get_conn()
    try:
        # 检查是否已存在
        existing = conn.execute(
            "SELECT id FROM kg_entities WHERE entity_type = ? AND name = ?",
            (entity_type, name)
        ).fetchone()

        if existing:
            # 更新已有实体
            conn.execute("""
                UPDATE kg_entities SET
                    aliases_json = ?,
                    attributes_json = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
            """, (
                json.dumps(aliases or [], ensure_ascii=False),
                json.dumps(attributes or {}, ensure_ascii=False),
                existing["id"]
            ))
            conn.commit()
            return existing["id"]

        cur = conn.execute("""
            INSERT INTO kg_entities (entity_type, name, aliases_json, attributes_json)
            VALUES (?, ?, ?, ?)
        """, (
            entity_type, name,
            json.dumps(aliases or [], ensure_ascii=False),
            json.dumps(attributes or {}, ensure_ascii=False),
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_entity(entity_id: int) -> dict | None:
    """获取单个实体。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM kg_entities WHERE id = ?", (entity_id,)).fetchone()
    conn.close()
    return _parse_entity_row(row)


def search_entities(query: str, entity_type: str = None, limit: int = 10) -> list[dict]:
    """搜索实体（名称 + 别名模糊匹配）。"""
    conn = _get_conn()
    like = f"%{query}%"
    if entity_type:
        rows = conn.execute("""
            SELECT * FROM kg_entities
            WHERE entity_type = ? AND (name LIKE ? OR aliases_json LIKE ?)
            ORDER BY name ASC
            LIMIT ?
        """, (entity_type, like, like, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM kg_entities
            WHERE name LIKE ? OR aliases_json LIKE ?
            ORDER BY name ASC
            LIMIT ?
        """, (like, like, limit)).fetchall()
    conn.close()
    return [_parse_entity_row(row) for row in rows]


# ── Relationship CRUD ─────────────────────────────────

def add_relationship(source_id: int, target_id: int, rel_type: str,
                     weight: float = 1.0, evidence: list = None) -> int:
    """添加关系，返回 ID。若已存在则更新权重和证据。"""
    if rel_type not in VALID_REL_TYPES:
        raise ValueError(f"无效 rel_type: {rel_type}，可选: {VALID_REL_TYPES}")

    conn = _get_conn()
    try:
        # 检查源/目标实体是否存在
        for eid in (source_id, target_id):
            if not conn.execute("SELECT 1 FROM kg_entities WHERE id = ?", (eid,)).fetchone():
                raise ValueError(f"实体 ID {eid} 不存在")

        existing = conn.execute("""
            SELECT id FROM kg_relationships
            WHERE source_id = ? AND target_id = ? AND rel_type = ?
        """, (source_id, target_id, rel_type)).fetchone()

        if existing:
            conn.execute("""
                UPDATE kg_relationships SET
                    weight = ?,
                    evidence_json = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
            """, (weight, json.dumps(evidence or [], ensure_ascii=False), existing["id"]))
            conn.commit()
            return existing["id"]

        cur = conn.execute("""
            INSERT INTO kg_relationships (source_id, target_id, rel_type, weight, evidence_json)
            VALUES (?, ?, ?, ?, ?)
        """, (source_id, target_id, rel_type, weight,
              json.dumps(evidence or [], ensure_ascii=False)))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_related_entities(entity_id: int, rel_type: str = None, depth: int = 1) -> list[dict]:
    """获取关联实体，支持多跳查询。

    返回格式: [{"entity": {...}, "relationship": {...}, "depth": int}]
    """
    conn = _get_conn()
    visited = set()
    results = []
    frontier = [entity_id]

    for current_depth in range(1, depth + 1):
        next_frontier = []
        for eid in frontier:
            if eid in visited:
                continue
            visited.add(eid)

            # 查询出边（entity_id 作为 source）
            if rel_type:
                rows = conn.execute("""
                    SELECT r.*, e.entity_type, e.name, e.aliases_json, e.attributes_json
                    FROM kg_relationships r
                    JOIN kg_entities e ON e.id = r.target_id
                    WHERE r.source_id = ? AND r.rel_type = ?
                """, (eid, rel_type)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT r.*, e.entity_type, e.name, e.aliases_json, e.attributes_json
                    FROM kg_relationships r
                    JOIN kg_entities e ON e.id = r.target_id
                    WHERE r.source_id = ?
                """, (eid,)).fetchall()

            for row in rows:
                r = _parse_relationship_row(row)
                target_id = r["target_id"]
                entity_info = {
                    "id": r["entity_type"],
                    "entity_type": r["entity_type"],
                    "name": r["name"],
                    "aliases_json": _try_parse_json(r.get("aliases_json", "[]"), []),
                    "attributes_json": _try_parse_json(r.get("attributes_json", "{}"), {}),
                }
                results.append({
                    "entity": entity_info,
                    "relationship": {
                        "id": r["id"],
                        "source_id": r["source_id"],
                        "target_id": r["target_id"],
                        "rel_type": r["rel_type"],
                        "weight": r["weight"],
                        "evidence_json": r["evidence_json"],
                    },
                    "depth": current_depth,
                })
                if target_id not in visited:
                    next_frontier.append(target_id)

            # 查询入边（entity_id 作为 target）
            if rel_type:
                rows = conn.execute("""
                    SELECT r.*, e.entity_type, e.name, e.aliases_json, e.attributes_json
                    FROM kg_relationships r
                    JOIN kg_entities e ON e.id = r.source_id
                    WHERE r.target_id = ? AND r.rel_type = ?
                """, (eid, rel_type)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT r.*, e.entity_type, e.name, e.aliases_json, e.attributes_json
                    FROM kg_relationships r
                    JOIN kg_entities e ON e.id = r.source_id
                    WHERE r.target_id = ?
                """, (eid,)).fetchall()

            for row in rows:
                r = _parse_relationship_row(row)
                source_id = r["source_id"]
                entity_info = {
                    "id": source_id,
                    "entity_type": r["entity_type"],
                    "name": r["name"],
                    "aliases_json": _try_parse_json(r.get("aliases_json", "[]"), []),
                    "attributes_json": _try_parse_json(r.get("attributes_json", "{}"), {}),
                }
                results.append({
                    "entity": entity_info,
                    "relationship": {
                        "id": r["id"],
                        "source_id": r["source_id"],
                        "target_id": r["target_id"],
                        "rel_type": r["rel_type"],
                        "weight": r["weight"],
                        "evidence_json": r["evidence_json"],
                    },
                    "depth": current_depth,
                })
                if source_id not in visited:
                    next_frontier.append(source_id)

        frontier = next_frontier

    conn.close()
    return results


def _try_parse_json(text, default):
    try:
        return json.loads(text) if text else default
    except (json.JSONDecodeError, TypeError):
        return default


# ── Entity Mention CRUD ───────────────────────────────

def add_entity_mention(entity_id: int, knowledge_id: int, context: str = "") -> int:
    """关联实体与知识条目，返回 ID。"""
    conn = _get_conn()
    try:
        # 检查实体是否存在
        if not conn.execute("SELECT 1 FROM kg_entities WHERE id = ?", (entity_id,)).fetchone():
            raise ValueError(f"实体 ID {entity_id} 不存在")

        existing = conn.execute("""
            SELECT id FROM kg_entity_mentions
            WHERE entity_id = ? AND knowledge_id = ?
        """, (entity_id, knowledge_id)).fetchone()

        if existing:
            # 更新 context
            conn.execute("""
                UPDATE kg_entity_mentions SET mention_context = ?
                WHERE id = ?
            """, (context, existing["id"]))
            conn.commit()
            return existing["id"]

        cur = conn.execute("""
            INSERT INTO kg_entity_mentions (entity_id, knowledge_id, mention_context)
            VALUES (?, ?, ?)
        """, (entity_id, knowledge_id, context))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_entities_for_knowledge(knowledge_id: int) -> list[dict]:
    """获取某条知识关联的所有实体。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT e.*, m.mention_context, m.id as mention_id
        FROM kg_entity_mentions m
        JOIN kg_entities e ON e.id = m.entity_id
        WHERE m.knowledge_id = ?
        ORDER BY e.name ASC
    """, (knowledge_id,)).fetchall()
    conn.close()

    results = []
    for row in rows:
        d = _parse_entity_row(row)
        d["mention_context"] = row["mention_context"]
        d["mention_id"] = row["mention_id"]
        results.append(d)
    return results


def get_knowledge_for_entity(entity_id: int, limit: int = 20) -> list[dict]:
    """获取某实体关联的所有知识条目。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT kb.*, m.mention_context, m.id as mention_id
        FROM kg_entity_mentions m
        JOIN knowledge_base kb ON kb.id = m.knowledge_id
        WHERE m.entity_id = ?
        ORDER BY kb.importance DESC, kb.id DESC
        LIMIT ?
    """, (entity_id, limit)).fetchall()
    conn.close()

    results = []
    for row in rows:
        d = dict(row)
        # 反序列化 knowledge_base 的 JSON 字段
        for key in ("keywords", "limitations", "counterpoints"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
            else:
                d[key] = []
        results.append(d)
    return results


# ── 批量导入 ──────────────────────────────────────────

def bulk_import_entities(entities: list[dict]) -> int:
    """批量导入实体。

    每个 dict 需要: entity_type, name
    可选: aliases (list), attributes (dict)

    Returns:
        成功导入/更新的数量
    """
    count = 0
    for ent in entities:
        entity_type = ent.get("entity_type", "")
        name = ent.get("name", "")
        if not entity_type or not name:
            logger.warning(f"跳过无效实体: {ent}")
            continue
        if entity_type not in VALID_ENTITY_TYPES:
            logger.warning(f"跳过无效 entity_type: {entity_type}")
            continue
        try:
            add_entity(
                entity_type=entity_type,
                name=name,
                aliases=ent.get("aliases"),
                attributes=ent.get("attributes"),
            )
            count += 1
        except Exception as e:
            logger.error(f"导入实体失败 ({name}): {e}")

    logger.info(f"批量导入完成: {count}/{len(entities)}")
    return count
