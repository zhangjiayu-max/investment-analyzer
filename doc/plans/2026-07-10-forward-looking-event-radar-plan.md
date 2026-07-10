# 前瞻性事件雷达 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现从每日新闻中 LLM 提取未来 1-2 周市场事件，按板块匹配持仓/候选基金，生成 3 级推送 alert 的前瞻性事件雷达系统。

**Architecture:** 新增 `market_events` 表存储结构化事件 + `services/event_radar.py` 承载扫描主逻辑（新闻采集→LLM 提取→去重→状态流转→板块匹配→alert 生成）+ `app.py` 每晚 20:00 独立调度 + `routers/event_radar.py` 提供 API + 前端 AlertBell 扩展渲染。复用现有 `create_alert`、`_normalize_index_code`、`_fetch_news_from_mcp` 能力。

**Tech Stack:** Python 3.11+ / FastAPI / SQLite / Vue 3 Composition API / MIMO LLM / 盈米 MCP

**Spec:** `doc/plans/2026-07-10-forward-looking-event-radar.md`

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `backend/db/market_events.py` | market_events 表 CRUD | Create |
| `backend/db/__init__.py` | 导出 + init_db 注册 | Modify |
| `backend/db/config.py` | DEFAULT_CONFIGS 新增配置项 | Modify |
| `backend/services/event_radar.py` | 扫描主逻辑 | Create |
| `backend/routers/event_radar.py` | API 端点 | Create |
| `backend/app.py` | 调度任务注册 | Modify |
| `backend/tests/test_market_events.py` | DB 层测试 | Create |
| `backend/tests/test_event_radar.py` | 服务层测试 | Create |
| `backend/tests/test_event_radar_api.py` | API 层测试 | Create |
| `frontend/src/components/AlertBell.vue` | 事件卡片渲染 | Modify |
| `frontend/src/api/index.js` | API 调用函数 | Modify |

---

## Task 1: 数据库表 + CRUD

**Files:**
- Create: `backend/db/market_events.py`
- Modify: `backend/db/__init__.py`（导出 + init_db 注册）
- Test: `backend/tests/test_market_events.py`

- [ ] **Step 1: 写 DB 层失败测试**

创建 `backend/tests/test_market_events.py`：

```python
"""前瞻性事件雷达 — DB 层测试。"""
import json
from db.market_events import (
    init_market_events_tables, create_market_event, get_market_event,
    list_market_events, update_market_event_status, list_active_events,
)


def test_create_and_get_event(tmp_db):
    """创建事件并按 event_id 查询。"""
    event_id = create_market_event(
        title="SpaceX 星舰试飞",
        summary="7 月 18 日星舰试飞首次尝试助推器回收",
        event_type="industry",
        direction="positive",
        expected_date="2026-07-18",
        affected_sectors=["军工"],
        affected_themes=["火箭回收"],
        confidence=0.95,
        sources=[{"url": "http://x", "title": "新闻1"}],
    )
    assert event_id  # 返回非空 event_id

    ev = get_market_event(event_id)
    assert ev is not None
    assert ev["title"] == "SpaceX 星舰试飞"
    assert ev["status"] == "upcoming"
    assert json.loads(ev["affected_sectors"]) == ["军工"]
    assert ev["confidence"] == 0.95


def test_create_event_idempotent(tmp_db):
    """相同 title+expected_date 重复创建返回相同 event_id。"""
    eid1 = create_market_event(
        title="美联储议息", summary="", event_type="macro",
        direction="neutral", expected_date="2026-07-20",
        affected_sectors=[], affected_themes=[], confidence=0.8, sources=[],
    )
    eid2 = create_market_event(
        title="美联储议息", summary="更新摘要", event_type="macro",
        direction="neutral", expected_date="2026-07-20",
        affected_sectors=[], affected_themes=[], confidence=0.85, sources=[],
    )
    assert eid1 == eid2
    ev = get_market_event(eid1)
    # 重复创建不覆盖已有数据（保留首次检测）
    assert ev["confidence"] == 0.8


def test_list_active_events(tmp_db):
    """查询 upcoming/imminent 状态事件。"""
    create_market_event(
        title="事件A", summary="", event_type="policy", direction="positive",
        expected_date="2026-07-25", affected_sectors=["半导体"],
        affected_themes=[], confidence=0.7, sources=[],
    )
    create_market_event(
        title="事件B", summary="", event_type="earnings", direction="neutral",
        expected_date="2026-07-15", affected_sectors=[],
        affected_themes=[], confidence=0.6, sources=[],
    )
    active = list_active_events()
    assert len(active) == 2
    titles = [e["title"] for e in active]
    assert "事件A" in titles and "事件B" in titles


def test_update_event_status(tmp_db):
    """更新事件状态并追加 timeline。"""
    eid = create_market_event(
        title="测试事件", summary="", event_type="theme", direction="neutral",
        expected_date="2026-07-18", affected_sectors=[],
        affected_themes=[], confidence=0.5, sources=[],
    )
    updated = update_market_event_status(eid, "imminent")
    assert updated is True

    ev = get_market_event(eid)
    assert ev["status"] == "imminent"
    timeline = json.loads(ev["timeline"])
    assert any("imminent" in t.get("event", "") for t in timeline)


def test_list_events_by_status(tmp_db):
    """按状态过滤事件列表。"""
    eid1 = create_market_event(
        title="E1", summary="", event_type="policy", direction="positive",
        expected_date="2026-07-25", affected_sectors=[],
        affected_themes=[], confidence=0.7, sources=[],
    )
    create_market_event(
        title="E2", summary="", event_type="policy", direction="positive",
        expected_date="2026-07-26", affected_sectors=[],
        affected_themes=[], confidence=0.7, sources=[],
    )
    update_market_event_status(eid1, "imminent")

    imminent = list_market_events(status="imminent")
    assert len(imminent) == 1
    assert imminent[0]["title"] == "E1"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_market_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db.market_events'`

- [ ] **Step 3: 实现 db/market_events.py**

创建 `backend/db/market_events.py`：

```python
"""前瞻性事件雷达 — market_events 表 CRUD。

事件结构见 doc/plans/2026-07-10-forward-looking-event-radar.md §3。
状态流转见 §5：upcoming → imminent → materialized → expired。
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_market_events_tables(conn) -> None:
    """创建 market_events 表（由 init_db 调用）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_events (
            event_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'upcoming',
            direction TEXT,
            confidence REAL DEFAULT 0.5,
            expected_date TEXT,
            detected_date TEXT NOT NULL,
            materialized_date TEXT,
            expired_date TEXT,
            affected_sectors TEXT,
            affected_themes TEXT,
            relevance_to_user TEXT NOT NULL DEFAULT 'market_watch',
            matched_holdings TEXT,
            candidate_funds TEXT,
            sources TEXT,
            timeline TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_status ON market_events(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_expected ON market_events(expected_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_relevance ON market_events(relevance_to_user)")


def _gen_event_id(title: str, expected_date: str) -> str:
    """事件唯一 ID：sha1(title+expected_date)[:16]，保证幂等。"""
    raw = f"{title}|{expected_date}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def create_market_event(
    title: str,
    summary: str,
    event_type: str,
    direction: str,
    expected_date: str,
    affected_sectors: list,
    affected_themes: list,
    confidence: float,
    sources: list,
) -> str:
    """创建事件（幂等：相同 title+expected_date 不重复创建）。

    Returns:
        event_id（已存在则返回已有 id，不覆盖）
    """
    event_id = _gen_event_id(title, expected_date)
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        # 幂等检查
        existing = conn.execute(
            "SELECT event_id FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing:
            return event_id  # 已存在，不覆盖

        timeline = json.dumps(
            [{"date": today, "event": "首次检测"}], ensure_ascii=False
        )
        conn.execute("""
            INSERT INTO market_events (
                event_id, title, summary, event_type, status, direction, confidence,
                expected_date, detected_date, affected_sectors, affected_themes,
                relevance_to_user, sources, timeline
            ) VALUES (?, ?, ?, ?, 'upcoming', ?, ?, ?, ?, ?, ?, 'market_watch', ?, ?)
        """, (
            event_id, title, summary, event_type, direction, confidence,
            expected_date, today,
            json.dumps(affected_sectors, ensure_ascii=False),
            json.dumps(affected_themes, ensure_ascii=False),
            json.dumps(sources, ensure_ascii=False),
            timeline,
        ))
        conn.commit()
        return event_id
    finally:
        conn.close()


def get_market_event(event_id: str) -> Optional[dict]:
    """按 event_id 查询事件详情。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_market_events(
    status: Optional[str] = None,
    relevance: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """查询事件列表（可按 status/relevance 过滤）。"""
    sql = "SELECT * FROM market_events"
    params = []
    conditions = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if relevance:
        conditions.append("relevance_to_user = ?")
        params.append(relevance)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY expected_date ASC LIMIT ?"
    params.append(limit)

    conn = _get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_active_events() -> list[dict]:
    """查询所有 upcoming/imminent 状态事件（供状态流转扫描）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM market_events WHERE status IN ('upcoming','imminent') "
            "ORDER BY expected_date ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_market_event_status(event_id: str, new_status: str) -> bool:
    """更新事件状态，追加 timeline 记录。

    Returns:
        True if 更新成功，False if 事件不存在
    """
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT timeline FROM market_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if not row:
            return False

        timeline = json.loads(row["timeline"]) if row["timeline"] else []
        timeline.append({"date": today, "event": f"状态更新为 {new_status}"})

        # 根据 new_status 填写对应日期字段
        date_field = ""
        if new_status == "materialized":
            date_field = ", materialized_date = ?"
            date_val = today
        elif new_status == "expired":
            date_field = ", expired_date = ?"
            date_val = today
        else:
            date_field = ""
            date_val = None

        sql = f"""
            UPDATE market_events
            SET status = ?, timeline = ?, updated_at = ?{date_field}
            WHERE event_id = ?
        """
        params = [new_status, json.dumps(timeline, ensure_ascii=False), today]
        if date_field:
            params.append(date_val)
        params.append(event_id)

        conn.execute(sql, params)
        conn.commit()
        return True
    finally:
        conn.close()


def update_event_relevance(
    event_id: str,
    relevance: str,
    matched_holdings: list,
    candidate_funds: list,
) -> bool:
    """更新事件的推送分级与关联基金（每次扫描重新计算）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        conn.execute("""
            UPDATE market_events
            SET relevance_to_user = ?, matched_holdings = ?, candidate_funds = ?,
                updated_at = ?
            WHERE event_id = ?
        """, (
            relevance,
            json.dumps(matched_holdings, ensure_ascii=False),
            json.dumps(candidate_funds, ensure_ascii=False),
            today,
            event_id,
        ))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_market_event(event_id: str) -> bool:
    """删除事件。"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM market_events WHERE event_id = ?", (event_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()
```

- [ ] **Step 4: 在 db/__init__.py 导出并注册**

在 `backend/db/__init__.py` 顶部导入块（约第 30-150 行，跟随其他 `from db.xxx import` 之后）添加：

```python
# 前瞻性事件雷达
from db.market_events import (
    init_market_events_tables, create_market_event, get_market_event,
    list_market_events, list_active_events, update_market_event_status,
    update_event_relevance, delete_market_event,
)
```

在 `init_db()` 函数内（约第 1100-1121 行，跟随其他 `init_xxx_tables(conn)` 调用之后）添加：

```python
    # 前瞻性事件雷达
    init_market_events_tables(conn)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_market_events.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/db/market_events.py backend/db/__init__.py backend/tests/test_market_events.py
git commit -m "feat(event-radar): 新增 market_events 表 CRUD"
```

---

## Task 2: 配置开关

**Files:**
- Modify: `backend/db/config.py`（DEFAULT_CONFIGS 列表）
- Test: `backend/tests/test_market_events.py`（追加配置测试）

- [ ] **Step 1: 写配置默认值测试**

在 `backend/tests/test_market_events.py` 末尾追加：

```python
def test_event_radar_config_defaults(tmp_db):
    """验证事件雷达配置项默认值。"""
    from db.config import get_config, get_config_bool, get_config_int, get_config_float

    # LLM 相关开关默认 false（硬约束）
    assert get_config_bool("alerts.event_radar_enabled", True) is False
    assert get_config_int("alerts.event_radar_lookforward_days", 99) == 14
    assert get_config_int("alerts.event_radar_max_events", 99) == 15
    assert get_config_float("alerts.event_radar_min_confidence", 99.0) == 0.4
    assert get_config("alerts.event_radar_scan_time", "xxx") == "20:00"
    assert get_config("alerts.event_radar_news_sources", "xxx") == "yingmi,eastmoney,akshare"
    assert get_config_int("alerts.event_radar_max_candidate_funds", 99) == 5
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_market_events.py::test_event_radar_config_defaults -v`
Expected: FAIL（配置项不存在，返回 default 参数值）

- [ ] **Step 3: 在 DEFAULT_CONFIGS 添加配置项**

在 `backend/db/config.py` 的 `DEFAULT_CONFIGS` 列表中（约第 85-87 行 `alert.news_*` 附近之后）添加：

```python
    # 前瞻性事件雷达（LLM 相关，默认关闭）
    ("alerts.event_radar_enabled", "false", "前瞻性事件雷达总开关", "alerts"),
    ("alerts.event_radar_lookforward_days", "14", "前瞻视野天数（1-14）", "alerts"),
    ("alerts.event_radar_max_events", "15", "单次扫描最多提取事件数", "alerts"),
    ("alerts.event_radar_min_confidence", "0.4", "低于此置信度不推送", "alerts"),
    ("alerts.event_radar_scan_time", "20:00", "每日扫描时间 HH:MM", "alerts"),
    ("alerts.event_radar_news_sources", "yingmi,eastmoney,akshare", "新闻源（逗号分隔）", "alerts"),
    ("alerts.event_radar_max_candidate_funds", "5", "建仓机会卡片最多展示基金数", "alerts"),
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_market_events.py::test_event_radar_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/db/config.py backend/tests/test_market_events.py
git commit -m "feat(event-radar): 新增 7 个配置开关（默认关闭）"
```

---

## Task 3: 新闻采集函数

**Files:**
- Create: `backend/services/event_radar.py`
- Test: `backend/tests/test_event_radar.py`

- [ ] **Step 1: 写新闻采集失败测试**

创建 `backend/tests/test_event_radar.py`：

```python
"""前瞻性事件雷达 — 服务层测试。"""
from unittest.mock import patch, MagicMock
from services.event_radar import _collect_news, SECTOR_TO_INDEX


def test_collect_news_returns_list():
    """_collect_news 返回新闻列表（mock MCP）。"""
    fake_news = [
        {"news_title": "SpaceX 宣布 7 月 18 日星舰试飞", "news_summary": "首次尝试助推器回收",
         "news_source": "新华财经", "news_url": "http://x", "published_at": "2026-07-10"},
        {"news_title": "美联储 7 月议息会议", "news_summary": "预计讨论降息",
         "news_source": "路透", "news_url": "http://y", "published_at": "2026-07-10"},
    ]
    with patch("services.event_radar._fetch_news_from_mcp", return_value=fake_news):
        result = _collect_news()
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all("news_title" in n for n in result)


def test_collect_news_failure_returns_empty():
    """MCP 失败时返回空列表，不抛异常。"""
    with patch("services.event_radar._fetch_news_from_mcp", side_effect=Exception("MCP down")):
        result = _collect_news()
    assert result == []


def test_sector_to_index_has_all_known_sectors():
    """SECTOR_TO_INDEX 覆盖 hotspots.py 的 18 个板块。"""
    expected = {"半导体", "人工智能", "新能源", "消费", "医药", "金融", "地产", "军工",
                "教育", "体育", "传媒", "汽车", "基建", "科技", "农业", "环保", "有色", "化工"}
    assert expected.issubset(set(SECTOR_TO_INDEX.keys()))
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.event_radar'`

- [ ] **Step 3: 实现 services/event_radar.py 骨架 + 新闻采集**

创建 `backend/services/event_radar.py`：

```python
"""前瞻性事件雷达 — 从每日新闻提取未来 1-2 周市场事件。

调度：每晚 20:00（app.py 的 _auto_event_radar_scan）
流程：新闻采集 → LLM 提取 → 去重写表 → 状态流转 → 板块匹配 → 3 级 alert
设计稿：doc/plans/2026-07-10-forward-looking-event-radar.md
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from db._conn import _get_conn
from db.config import get_config, get_config_bool, get_config_int, get_config_float, get_config_list

logger = logging.getLogger(__name__)


# ── 板块 → 跟踪指数映射 ──────────────────────────────
# 参考 hotspots.py 的 sector_keywords，覆盖 18 个板块。
# 指数代码需对照 fund_metadata.tracking_index 实际数据校准。
SECTOR_TO_INDEX = {
    "半导体": ["990001", "H30184"],
    "人工智能": ["930713", "931071"],
    "新能源": ["399808", "931151"],
    "消费": ["000932", "399932"],
    "医药": ["930791", "000993"],
    "金融": ["399949", "930601"],
    "地产": ["931775", "399393"],
    "军工": ["399967", "930798"],
    "教育": ["930711"],
    "体育": ["930711"],
    "传媒": ["930681", "930901"],
    "汽车": ["930758", "399975"],
    "基建": ["399388", "930608"],
    "科技": ["931087", "930986"],
    "农业": ["930687", "000936"],
    "环保": ["930790", "930615"],
    "有色": ["930708", "399395"],
    "化工": ["930695", "930751"],
}


def _fetch_news_from_mcp(keyword: str = "", limit: int = 50) -> list[dict]:
    """从盈米 MCP 检索财经新闻（复用 alert_news_service 的调用模式）。

    Args:
        keyword: 检索关键词（空则检索综合财经新闻）
        limit: 最多返回条数

    Returns:
        [{news_title, news_summary, news_source, news_url, published_at}, ...]
        失败返回空列表。
    """
    try:
        from services.alert_news_service import _fetch_news_from_mcp as _mcp_fetch
        # 复用现有 MCP 检索实现（已封装好响应解析 + 异常吞掉）
        kw = keyword or "财经"
        return _mcp_fetch(kw, limit=limit)
    except Exception as e:
        logger.warning(f"[event_radar] MCP 新闻检索失败: {e}")
        return []


def _collect_news() -> list[dict]:
    """采集最近 24 小时财经新闻（多源融合 + 去重）。

    数据源优先级：盈米 MCP → 东财妙想 → akshare（当前仅实现盈米）
    单次最多 50 条，跨源去重。
    """
    max_news = 50
    try:
        news = _fetch_news_from_mcp(limit=max_news)
    except Exception as e:
        logger.warning(f"[event_radar] 新闻采集异常: {e}")
        news = []

    if not news:
        logger.info("[event_radar] 未采集到新闻，跳过本次扫描")
        return []

    # 去重：按标题相似度（简化版：完全一致去重）
    seen_titles = set()
    unique = []
    for n in news:
        title = n.get("news_title", "").strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            unique.append(n)

    logger.info(f"[event_radar] 采集新闻 {len(news)} 条，去重后 {len(unique)} 条")
    return unique[:max_news]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/services/event_radar.py backend/tests/test_event_radar.py
git commit -m "feat(event-radar): 实现新闻采集 + 板块指数映射表"
```

---

## Task 4: LLM 事件提取

**Files:**
- Modify: `backend/services/event_radar.py`
- Test: `backend/tests/test_event_radar.py`（追加）

- [ ] **Step 1: 写 LLM 提取失败测试**

在 `backend/tests/test_event_radar.py` 追加：

```python
def test_extract_events_from_news_llm_success():
    """LLM 成功返回事件列表。"""
    from services.event_radar import _extract_events_from_news

    fake_news = [
        {"news_title": "SpaceX 宣布 7 月 18 日星舰第六次试飞",
         "news_summary": "首次尝试用机械臂回收超重型助推器",
         "news_source": "新华财经", "news_url": "http://x", "published_at": "2026-07-10"},
    ]
    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = '''[
        {
            "title": "SpaceX 星舰第六次试飞首次尝试助推器回收",
            "summary": "7 月 18 日星舰试飞首次尝试用机械臂回收超重型助推器",
            "event_type": "industry",
            "direction": "positive",
            "expected_date": "2026-07-18",
            "affected_sectors": ["军工"],
            "affected_themes": ["火箭回收", "商业航天"],
            "confidence": 0.95
        }
    ]'''

    with patch("services.event_radar._call_llm", return_value=fake_llm_resp):
        events = _extract_events_from_news(fake_news, trace_id="test")

    assert len(events) == 1
    assert events[0]["title"] == "SpaceX 星舰第六次试飞首次尝试助推器回收"
    assert events[0]["affected_sectors"] == ["军工"]
    assert events[0]["confidence"] == 0.95


def test_extract_events_filter_out_of_range_date():
    """过滤 expected_date 超出 [今天, 今天+14天] 的事件。"""
    from services.event_radar import _extract_events_from_news

    future_over_14 = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    in_range = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")

    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = f'''[
        {{"title": "超范围事件", "summary": "", "event_type": "theme", "direction": "neutral",
          "expected_date": "{future_over_14}", "affected_sectors": [], "affected_themes": [], "confidence": 0.8}},
        {{"title": "范围内事件", "summary": "", "event_type": "theme", "direction": "neutral",
          "expected_date": "{in_range}", "affected_sectors": [], "affected_themes": [], "confidence": 0.8}}
    ]'''

    with patch("services.event_radar._call_llm", return_value=fake_llm_resp):
        events = _extract_events_from_news([], trace_id="test")

    titles = [e["title"] for e in events]
    assert "范围内事件" in titles
    assert "超范围事件" not in titles


def test_extract_events_low_confidence_filtered():
    """confidence < 0.4 的事件被过滤。"""
    from services.event_radar import _extract_events_from_news

    in_range = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = f'''[
        {{"title": "低置信度", "summary": "", "event_type": "theme", "direction": "neutral",
          "expected_date": "{in_range}", "affected_sectors": [], "affected_themes": [], "confidence": 0.2}}
    ]'''

    with patch("services.event_radar._call_llm", return_value=fake_llm_resp):
        events = _extract_events_from_news([], trace_id="test")

    assert len(events) == 0


def test_extract_events_llm_failure_returns_empty():
    """LLM 异常时返回空列表，不抛异常。"""
    from services.event_radar import _extract_events_from_news

    with patch("services.event_radar._call_llm", side_effect=Exception("LLM down")):
        events = _extract_events_from_news([{"news_title": "x"}], trace_id="test")

    assert events == []
```

需要在测试文件顶部追加导入：

```python
from datetime import datetime, timedelta
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py::test_extract_events_from_news_llm_success -v`
Expected: FAIL with `ImportError: cannot import name '_extract_events_from_news'`

- [ ] **Step 3: 实现 _extract_events_from_news**

在 `backend/services/event_radar.py` 追加：

```python
def _extract_events_from_news(news_list: list[dict], trace_id: str = "") -> list[dict]:
    """用 LLM 从新闻列表中提取未来 1-2 周将发生的市场事件。

    Args:
        news_list: 新闻列表 [{news_title, news_summary, ...}]
        trace_id: 追踪 ID

    Returns:
        事件列表 [{title, summary, event_type, direction, expected_date,
                  affected_sectors, affected_themes, confidence}]
        过滤规则：expected_date 在 [今天, 今天+14天]、confidence >= 0.4
    """
    if not news_list:
        logger.info(f"[event_radar:{trace_id}] 无新闻输入，跳过 LLM 提取")
        return []

    max_events = get_config_int("alerts.event_radar_max_events", 15)
    lookforward_days = get_config_int("alerts.event_radar_lookforward_days", 14)
    min_confidence = get_config_float("alerts.event_radar_min_confidence", 0.4)

    today = datetime.now().strftime("%Y-%m-%d")
    future_limit = (datetime.now() + timedelta(days=lookforward_days)).strftime("%Y-%m-%d")

    # 构造新闻摘要 JSON（控制 token）
    news_for_llm = [
        {"title": n.get("news_title", ""), "summary": n.get("news_summary", "")[:100]}
        for n in news_list[:50]
    ]

    known_sectors = "/".join(SECTOR_TO_INDEX.keys())
    prompt = f"""你是一位资深财经分析师。请从以下新闻列表中提取「即将在未来 {lookforward_days} 天内发生」的市场事件。

【新闻列表】
{json.dumps(news_for_llm, ensure_ascii=False)}

【输出要求】
仅输出 JSON 数组，每个事件包含：
- title: 事件标题（≤50 字，主谓宾完整）
- summary: 事件摘要（≤200 字，说明影响）
- event_type: 事件分类（policy/industry/earnings/capital/macro/theme）
- direction: 影响方向（positive/negative/neutral）
- expected_date: 预期发生日期（YYYY-MM-DD 格式，从新闻推断）
- affected_sectors: 受影响板块（数组，从以下选取：{known_sectors}）
- affected_themes: 受影响主题（数组，自由文本如"国产替代""火箭回收"）
- confidence: 置信度（0-1，1 表示高度确定会发生）

【过滤规则】
1. 只提取"即将发生"的事件，不提取"已经发生"的新闻
2. 跳过模糊时间（如"近期""未来"），必须能推断到具体日期
3. 跳过无市场影响的事件（如纯娱乐八卦）
4. 单条新闻可提取 0-2 个事件，最多输出 {max_events} 个事件
5. expected_date 必须在 [{today}, {future_limit}] 范围内

只输出 JSON 数组，不要其他解释。"""

    try:
        from services.llm_service import _call_llm, MODEL
        resp = _call_llm(
            caller="event_radar_extractor",
            trace_id=trace_id,
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # 容错：剥离可能的 markdown 代码块包裹
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        events = json.loads(raw)
        if not isinstance(events, list):
            return []
    except Exception as e:
        logger.warning(f"[event_radar:{trace_id}] LLM 事件提取失败: {e}")
        return []

    # 二次过滤：日期范围 + 置信度
    filtered = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        exp_date = ev.get("expected_date", "")
        conf = float(ev.get("confidence", 0))
        if not exp_date:
            continue
        if exp_date < today or exp_date > future_limit:
            continue
        if conf < min_confidence:
            continue
        filtered.append(ev)

    logger.info(
        f"[event_radar:{trace_id}] LLM 提取 {len(events)} 个事件，"
        f"过滤后 {len(filtered)} 个"
    )
    return filtered[:max_events]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py -v`
Expected: 7 passed（3 个 Task 3 + 4 个 Task 4）

- [ ] **Step 5: Commit**

```bash
git add backend/services/event_radar.py backend/tests/test_event_radar.py
git commit -m "feat(event-radar): 实现 LLM 事件提取 + 日期/置信度过滤"
```

---

## Task 5: 板块匹配 + 3 级推送分级

**Files:**
- Modify: `backend/services/event_radar.py`
- Test: `backend/tests/test_event_radar.py`（追加）

- [ ] **Step 1: 写匹配逻辑失败测试**

在 `backend/tests/test_event_radar.py` 追加：

```python
def test_determine_relevance_holding_impact():
    """事件命中持仓 → holding_impact。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": ["军工"]}
    holdings = [{"fund_code": "161031", "fund_name": "军工ETF", "index_code": "399967"}]
    relevance, matched, candidates = _determine_relevance(event, holdings)
    assert relevance == "holding_impact"
    assert len(matched) == 1
    assert matched[0]["fund_code"] == "161031"
    assert candidates == []


def test_determine_relevance_opportunity():
    """未命中持仓但有候选基金 → opportunity。"""
    from services.event_radar import _determine_relevance, _find_candidate_funds

    event = {"affected_sectors": ["半导体"]}
    holdings = []  # 无持仓

    # mock _find_candidate_funds 返回候选
    fake_candidates = [
        {"fund_code": "159995", "fund_name": "芯片ETF", "match_reason": "跟踪半导体指数"}
    ]
    with patch("services.event_radar._find_candidate_funds", return_value=fake_candidates):
        relevance, matched, candidates = _determine_relevance(event, holdings)

    assert relevance == "opportunity"
    assert matched == []
    assert len(candidates) >= 1


def test_determine_relevance_market_watch():
    """无板块对应 → market_watch。"""
    from services.event_radar import _determine_relevance

    event = {"affected_sectors": []}  # 宏观事件无板块
    holdings = []
    relevance, matched, candidates = _determine_relevance(event, holdings)
    assert relevance == "market_watch"
    assert matched == []
    assert candidates == []


def test_find_candidate_funds_excludes_holdings(tmp_db):
    """候选基金排除已持仓。"""
    from services.event_radar import _find_candidate_funds
    from db import _get_conn

    # 准备 fund_metadata 测试数据
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_metadata (
            fund_code TEXT PRIMARY KEY, fund_name TEXT, fund_type TEXT,
            tracking_index TEXT
        )
    """)
    conn.execute(
        "INSERT OR REPLACE INTO fund_metadata VALUES (?, ?, ?, ?)",
        ("159995", "芯片ETF", "ETF", "990001"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO fund_metadata VALUES (?, ?, ?, ?)",
        ("161031", "军工ETF", "ETF", "399967"),
    )
    conn.commit()
    conn.close()

    # 查询半导体候选基金，排除持仓 161031
    candidates = _find_candidate_funds("990001", exclude_codes={"161031"})
    codes = [c["fund_code"] for c in candidates]
    assert "159995" in codes
    assert "161031" not in codes
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py::test_determine_relevance_holding_impact -v`
Expected: FAIL with `ImportError: cannot import name '_determine_relevance'`

- [ ] **Step 3: 实现匹配 + 分级逻辑**

在 `backend/services/event_radar.py` 追加：

```python
def _find_candidate_funds(index_code: str, exclude_codes: set = None) -> list[dict]:
    """查询跟踪该指数的候选建仓基金（排除已持仓）。

    复用 fund_metadata.tracking_index 列做精确匹配。
    """
    exclude_codes = exclude_codes or set()
    conn = _get_conn()
    try:
        # 检查 fund_metadata 表是否存在
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fund_metadata'"
        ).fetchone()
        if not row:
            return []

        # 动态构造 NOT IN 占位
        placeholders = ",".join("?" * len(exclude_codes)) if exclude_codes else "''"
        sql = f"""
            SELECT fund_code, fund_name, fund_type, tracking_index
            FROM fund_metadata
            WHERE tracking_index = ?
              AND fund_code NOT IN ({placeholders})
            ORDER BY fund_type, fund_code
            LIMIT 5
        """
        params = [index_code] + list(exclude_codes)
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "fund_code": r["fund_code"],
                "fund_name": r["fund_name"],
                "fund_type": r["fund_type"],
                "match_reason": f"跟踪指数 {index_code}",
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"[event_radar] 查询候选基金失败 index={index_code}: {e}")
        return []
    finally:
        conn.close()


def _determine_relevance(
    event: dict,
    user_holdings: list[dict],
) -> tuple[str, list[dict], list[dict]]:
    """判定推送分级。

    Args:
        event: 事件 dict（含 affected_sectors）
        user_holdings: 用户持仓 [{fund_code, fund_name, index_code}, ...]

    Returns:
        (relevance, matched_holdings, candidate_funds)
        relevance: holding_impact / opportunity / market_watch
    """
    affected_sectors = event.get("affected_sectors", [])
    if not affected_sectors:
        return "market_watch", [], []

    matched_holdings = []
    candidate_funds = []
    holding_codes = {h.get("fund_code") for h in user_holdings}

    for sector in affected_sectors:
        index_codes = SECTOR_TO_INDEX.get(sector, [])
        for idx_code in index_codes:
            # 1. 检查持仓基金是否跟踪该指数
            for h in user_holdings:
                h_index = h.get("index_code") or ""
                # 归一化比较（剥离 .SZ/.SH 后缀）
                from services.index_fund_mapper import _normalize_index_code
                if _normalize_index_code(h_index) == _normalize_index_code(idx_code):
                    matched_holdings.append({
                        "fund_code": h.get("fund_code", ""),
                        "fund_name": h.get("fund_name", ""),
                        "match_reason": f"跟踪 {sector} 相关指数 {idx_code}",
                    })

            # 2. 若无持仓命中，收集候选建仓基金
            if not matched_holdings:
                cands = _find_candidate_funds(idx_code, exclude_codes=holding_codes)
                # 去重（多个 sector 可能命中同一基金）
                existing_codes = {c["fund_code"] for c in candidate_funds}
                for c in cands:
                    if c["fund_code"] not in existing_codes:
                        candidate_funds.append(c)

    if matched_holdings:
        return "holding_impact", matched_holdings, []
    elif candidate_funds:
        max_cands = get_config_int("alerts.event_radar_max_candidate_funds", 5)
        return "opportunity", [], candidate_funds[:max_cands]
    else:
        return "market_watch", [], []
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py -v`
Expected: 11 passed（7 + 4）

- [ ] **Step 5: Commit**

```bash
git add backend/services/event_radar.py backend/tests/test_event_radar.py
git commit -m "feat(event-radar): 实现板块匹配 + 3 级推送分级"
```

---

## Task 6: 事件状态流转

**Files:**
- Modify: `backend/services/event_radar.py`
- Test: `backend/tests/test_event_radar.py`（追加）

- [ ] **Step 1: 写状态流转失败测试**

在 `backend/tests/test_event_radar.py` 追加：

```python
def test_update_event_statuses_upcoming_to_imminent(tmp_db):
    """距 expected_date ≤ 3 天的 upcoming → imminent。"""
    from services.event_radar import _update_event_statuses
    from db.market_events import create_market_event, get_market_event

    # 2 天后发生
    exp_date = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    eid = create_market_event(
        title="即将事件", summary="", event_type="theme", direction="neutral",
        expected_date=exp_date, affected_sectors=[], affected_themes=[],
        confidence=0.7, sources=[],
    )
    _update_event_statuses()
    ev = get_market_event(eid)
    assert ev["status"] == "imminent"


def test_update_event_statuses_to_materialized(tmp_db):
    """today >= expected_date → materialized。"""
    from services.event_radar import _update_event_statuses
    from db.market_events import create_market_event, get_market_event

    # 今天就是 expected_date
    today = datetime.now().strftime("%Y-%m-%d")
    eid = create_market_event(
        title="今日事件", summary="", event_type="theme", direction="neutral",
        expected_date=today, affected_sectors=[], affected_themes=[],
        confidence=0.7, sources=[],
    )
    _update_event_statuses()
    ev = get_market_event(eid)
    assert ev["status"] == "materialized"
    assert ev["materialized_date"] == today


def test_update_event_statuses_to_expired(tmp_db):
    """today > expected_date + 7 天 → expired。"""
    from services.event_radar import _update_event_statuses
    from db.market_events import create_market_event, get_market_event

    # 10 天前发生
    exp_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    eid = create_market_event(
        title="过期事件", summary="", event_type="theme", direction="neutral",
        expected_date=exp_date, affected_sectors=[], affected_themes=[],
        confidence=0.7, sources=[],
    )
    _update_event_statuses()
    ev = get_market_event(eid)
    assert ev["status"] == "expired"
    assert ev["expired_date"] is not None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py::test_update_event_statuses_upcoming_to_imminent -v`
Expected: FAIL with `ImportError: cannot import name '_update_event_statuses'`

- [ ] **Step 3: 实现状态流转**

在 `backend/services/event_radar.py` 追加：

```python
def _update_event_statuses() -> dict:
    """扫描所有 upcoming/imminent 事件，按日期更新状态。

    规则：
    - today >= expected_date → materialized
    - today > expected_date + 7 → expired
    - today > expected_date - 3 且 status=upcoming → imminent

    Returns:
        {"imminent": int, "materialized": int, "expired": int}
    """
    from db.market_events import list_active_events, update_market_event_status

    today = datetime.now().strftime("%Y-%m-%d")
    today_dt = datetime.now()
    counts = {"imminent": 0, "materialized": 0, "expired": 0}

    active = list_active_events()
    for ev in active:
        exp_date_str = ev.get("expected_date")
        if not exp_date_str:
            continue
        try:
            exp_dt = datetime.strptime(exp_date_str, "%Y-%m-%d")
        except ValueError:
            continue

        status = ev["status"]
        days_to_event = (exp_dt - today_dt).days

        # today > expected_date + 7 → expired
        if days_to_event < -7:
            update_market_event_status(ev["event_id"], "expired")
            counts["expired"] += 1
        # today >= expected_date → materialized
        elif days_to_event <= 0:
            update_market_event_status(ev["event_id"], "materialized")
            counts["materialized"] += 1
        # today > expected_date - 3 且 status=upcoming → imminent
        elif days_to_event <= 3 and status == "upcoming":
            update_market_event_status(ev["event_id"], "imminent")
            counts["imminent"] += 1

    if any(counts.values()):
        logger.info(f"[event_radar] 状态流转: {counts}")
    return counts
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py -v`
Expected: 14 passed（11 + 3）

- [ ] **Step 5: Commit**

```bash
git add backend/services/event_radar.py backend/tests/test_event_radar.py
git commit -m "feat(event-radar): 实现事件状态流转"
```

---

## Task 7: 主扫描函数 scan_forward_events

**Files:**
- Modify: `backend/services/event_radar.py`
- Test: `backend/tests/test_event_radar.py`（追加集成测试）

- [ ] **Step 1: 写主扫描集成测试**

在 `backend/tests/test_event_radar.py` 追加：

```python
def test_scan_forward_events_integration(tmp_db):
    """scan_forward_events 端到端：新闻→LLM→写表→状态流转→alert 生成。"""
    from services.event_radar import scan_forward_events
    from db.market_events import list_market_events

    fake_news = [
        {"news_title": "SpaceX 7 月 18 日星舰试飞", "news_summary": "回收测试",
         "news_source": "x", "news_url": "http://x", "published_at": "2026-07-10"},
    ]
    in_range = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    fake_llm_resp = MagicMock()
    fake_llm_resp.choices = [MagicMock()]
    fake_llm_resp.choices[0].message.content = f'''[{{
        "title": "星舰试飞回收测试",
        "summary": "回收测试",
        "event_type": "industry",
        "direction": "positive",
        "expected_date": "{in_range}",
        "affected_sectors": ["军工"],
        "affected_themes": ["火箭回收"],
        "confidence": 0.9
    }}]'''

    with patch("services.event_radar._collect_news", return_value=fake_news), \
         patch("services.event_radar._call_llm", return_value=fake_llm_resp), \
         patch("services.event_radar._determine_relevance",
               return_value=("market_watch", [], [])), \
         patch("db.portfolio.list_holdings", return_value=[]):
        result = scan_forward_events(trace_id="test")

    assert "extracted" in result
    assert "new" in result
    assert "alerts_created" in result
    assert result["extracted"] == 1
    # 验证事件已写表
    events = list_market_events()
    assert any(e["title"] == "星舰试飞回收测试" for e in events)


def test_scan_forward_events_disabled():
    """开关关闭时返回 skipped。"""
    from services.event_radar import scan_forward_events

    with patch("services.event_radar.get_config", return_value="false"):
        result = scan_forward_events()
    assert result.get("skipped") == "disabled"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py::test_scan_forward_events_integration -v`
Expected: FAIL with `ImportError: cannot import name 'scan_forward_events'`

- [ ] **Step 3: 实现 scan_forward_events**

在 `backend/services/event_radar.py` 追加：

```python
def scan_forward_events(trace_id: str = "") -> dict:
    """前瞻性事件雷达主扫描函数。

    流程：
    1. 检查开关
    2. 采集新闻
    3. LLM 提取未来事件
    4. 写入 market_events 表（去重）
    5. 状态流转扫描
    6. 板块匹配 + 3 级分级
    7. 生成 alert

    Returns:
        {"extracted": int, "new": int, "updated": int,
         "alerts_created": int, "skipped": str?}
    """
    if not get_config_bool("alerts.event_radar_enabled", False):
        return {"skipped": "disabled", "extracted": 0, "new": 0, "updated": 0, "alerts_created": 0}

    trace_id = trace_id or datetime.now().strftime("%Y%m%d%H%M%S")
    logger.info(f"[event_radar:{trace_id}] 开始扫描")

    # 1. 采集新闻
    news = _collect_news()
    if not news:
        return {"extracted": 0, "new": 0, "updated": 0, "alerts_created": 0, "reason": "no_news"}

    # 2. LLM 提取
    events = _extract_events_from_news(news, trace_id=trace_id)
    if not events:
        return {"extracted": 0, "new": 0, "updated": 0, "alerts_created": 0, "reason": "no_events"}

    # 3. 写入 market_events 表（幂等）
    from db.market_events import create_market_event, update_event_relevance
    new_count = 0
    for ev in events:
        sources = [
            {"title": n.get("news_title", ""), "url": n.get("news_url", ""),
             "publish_date": n.get("published_at", "")}
            for n in news[:3]  # 最多关联 3 条来源
        ]
        try:
            # 检查是否已存在
            from db.market_events import get_market_event, _gen_event_id
            eid = _gen_event_id(ev["title"], ev["expected_date"])
            existing = get_market_event(eid)
            create_market_event(
                title=ev["title"],
                summary=ev.get("summary", ""),
                event_type=ev.get("event_type", "theme"),
                direction=ev.get("direction", "neutral"),
                expected_date=ev["expected_date"],
                affected_sectors=ev.get("affected_sectors", []),
                affected_themes=ev.get("affected_themes", []),
                confidence=float(ev.get("confidence", 0.5)),
                sources=sources,
            )
            if not existing:
                new_count += 1
        except Exception as e:
            logger.warning(f"[event_radar:{trace_id}] 写入事件失败 '{ev.get('title','')}': {e}")

    # 4. 状态流转
    status_counts = _update_event_statuses()

    # 5. 板块匹配 + 分级 + 生成 alert
    from db.portfolio import list_holdings, create_alert
    holdings = list_holdings()
    alerts_created = 0

    # 对所有 upcoming/imminent 事件重新计算分级
    from db.market_events import list_active_events
    active = list_active_events()
    for ev_row in active:
        try:
            affected = json.loads(ev_row.get("affected_sectors") or "[]")
            event_dict = {"affected_sectors": affected}
            relevance, matched, candidates = _determine_relevance(event_dict, holdings)

            # 更新事件的分级字段
            update_event_relevance(ev_row["event_id"], relevance, matched, candidates)

            # 仅对新生成的事件（本次扫描首次检测）生成 alert，避免重复推送
            ev_id = ev_row["event_id"]
            ev_detected = ev_row.get("detected_date", "")
            today = datetime.now().strftime("%Y-%m-%d")
            if ev_detected != today:
                continue  # 不是今天首次检测，不重复推送

            # 构造 alert
            severity = {
                "holding_impact": "warning",
                "opportunity": "info",
                "market_watch": "info",
            }.get(relevance, "info")

            title_prefix = {"holding_impact": "持仓影响", "opportunity": "建仓机会",
                            "market_watch": "市场关注"}.get(relevance, "市场关注")
            alert_title = f"[{title_prefix}] {ev_row['title']}"
            content_parts = [f"预期日期：{ev_row.get('expected_date','')}"]
            if matched:
                codes = [m["fund_name"] for m in matched[:3]]
                content_parts.append(f"关联持仓：{', '.join(codes)}")
            if candidates:
                codes = [c["fund_name"] for c in candidates[:3]]
                content_parts.append(f"候选基金：{', '.join(codes)}")
            content = " | ".join(content_parts)

            create_alert(
                alert_type="event_radar",
                title=alert_title,
                content=content,
                severity=severity,
                source="event_radar",
            )
            alerts_created += 1
        except Exception as e:
            logger.warning(f"[event_radar:{trace_id}] 生成 alert 失败 ev={ev_row.get('event_id')}: {e}")

    result = {
        "extracted": len(events),
        "new": new_count,
        "updated": status_counts,
        "alerts_created": alerts_created,
    }
    logger.info(f"[event_radar:{trace_id}] 扫描完成: {result}")
    return result
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar.py -v`
Expected: 16 passed（14 + 2）

- [ ] **Step 5: Commit**

```bash
git add backend/services/event_radar.py backend/tests/test_event_radar.py
git commit -m "feat(event-radar): 实现主扫描函数 scan_forward_events"
```

---

## Task 8: 调度注册

**Files:**
- Modify: `backend/app.py`
- Test: 验证启动日志（手动）

- [ ] **Step 1: 添加 _auto_event_radar_scan 函数**

在 `backend/app.py` 的 `_auto_periodic_scan` 函数之后（约第 688 行后）添加：

```python
async def _auto_event_radar_scan():
    """前瞻性事件雷达 — 每晚 20:00 扫描一次。

    从新闻中提取未来 1-2 周的市场事件，匹配持仓/候选基金，生成 3 级 alert。

    开关：alerts.event_radar_enabled（默认 false，LLM 相关开关硬约束）
    调度：每晚 20:00 一次（不走 _auto_periodic_scan 的 30 分钟间隔）
    """
    from datetime import datetime, timedelta
    try:
        await asyncio.sleep(120)  # 等启动完成（比 _auto_periodic_scan 晚 1 分钟避免抢资源）

        while True:
            # 计算距下次 20:00 的等待秒数
            now = datetime.now()
            target = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if now >= target:
                target = target + timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if get_config("alerts.event_radar_enabled", "false") != "true":
                continue

            try:
                from services.event_radar import scan_forward_events
                result = scan_forward_events()
                logging.info(
                    f"[event-radar] 扫描完成: "
                    f"extracted={result.get('extracted', 0)}, "
                    f"new={result.get('new', 0)}, "
                    f"alerts={result.get('alerts_created', 0)}"
                )
            except Exception as e:
                logging.warning(f"[event-radar] 扫描异常: {e}")
    except Exception as e:
        logging.warning(f"前瞻事件雷达任务异常: {e}")
```

- [ ] **Step 2: 在 startup() 注册调度任务**

在 `backend/app.py` 的 `startup()` 函数中，`_auto_periodic_scan` 注册之后（约第 401 行后）添加：

```python
    # 前瞻性事件雷达（每晚 20:00，默认关闭，LLM 相关开关硬约束）
    if get_config("alerts.event_radar_enabled", "false") == "true":
        asyncio.create_task(_auto_event_radar_scan())
        logging.info("前瞻事件雷达任务已启动（alerts.event_radar_enabled=true）")
    else:
        logging.info("前瞻事件雷达已关闭（alerts.event_radar_enabled=false）")
```

- [ ] **Step 3: 验证启动不报错**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer/backend && python -c "import app; print('OK')"`
Expected: 输出 `OK`，无异常

- [ ] **Step 4: Commit**

```bash
git add backend/app.py
git commit -m "feat(event-radar): 注册每晚 20:00 调度任务"
```

---

## Task 9: API 端点

**Files:**
- Create: `backend/routers/event_radar.py`
- Modify: `backend/app.py`（注册 router）
- Test: `backend/tests/test_event_radar_api.py`

- [ ] **Step 1: 写 API 失败测试**

创建 `backend/tests/test_event_radar_api.py`：

```python
"""前瞻性事件雷达 — API 层测试。"""
from unittest.mock import patch
from fastapi.testclient import TestClient


def test_manual_scan_endpoint(tmp_db):
    """POST /api/alerts/event-radar/scan 手动触发扫描。"""
    from app import app

    with patch("services.event_radar.scan_forward_events",
               return_value={"extracted": 2, "new": 1, "alerts_created": 1}):
        client = TestClient(app)
        resp = client.post("/api/alerts/event-radar/scan")
    assert resp.status_code == 200
    data = resp.json()
    # 中间件包装为 {code, message, data}
    assert data["code"] == 0
    assert data["data"]["extracted"] == 2


def test_list_events_endpoint(tmp_db):
    """GET /api/alerts/event-radar/events 查询事件列表。"""
    from app import app
    from db.market_events import create_market_event

    create_market_event(
        title="测试事件API", summary="", event_type="theme", direction="neutral",
        expected_date="2026-07-25", affected_sectors=["军工"],
        affected_themes=[], confidence=0.8, sources=[],
    )

    client = TestClient(app)
    resp = client.get("/api/alerts/event-radar/events")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    titles = [e["title"] for e in data["data"]["events"]]
    assert "测试事件API" in titles


def test_event_detail_endpoint(tmp_db):
    """GET /api/alerts/event-radar/events/{id} 查询事件详情。"""
    from app import app
    from db.market_events import create_market_event

    eid = create_market_event(
        title="详情测试", summary="摘要内容", event_type="policy",
        direction="positive", expected_date="2026-07-25",
        affected_sectors=["半导体"], affected_themes=["国产替代"],
        confidence=0.85, sources=[],
    )

    client = TestClient(app)
    resp = client.get(f"/api/alerts/event-radar/events/{eid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["event_id"] == eid
    assert data["data"]["title"] == "详情测试"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar_api.py -v`
Expected: FAIL（404，路由未注册）

- [ ] **Step 3: 实现 routers/event_radar.py**

创建 `backend/routers/event_radar.py`：

```python
"""前瞻性事件雷达 — API 端点。

- POST /api/alerts/event-radar/scan：手动触发扫描
- GET /api/alerts/event-radar/events：事件列表（可按 status/relevance 过滤）
- GET /api/alerts/event-radar/events/{event_id}：事件详情
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from db.market_events import (
    list_market_events, get_market_event,
)
from api.response import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["event-radar"])


@router.post("/api/alerts/event-radar/scan")
async def manual_scan():
    """手动触发前瞻事件雷达扫描。"""
    try:
        from services.event_radar import scan_forward_events
        result = scan_forward_events()
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"手动触发事件雷达扫描失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")


@router.get("/api/alerts/event-radar/events")
async def list_events(
    status: Optional[str] = Query(None, description="按状态过滤：upcoming/imminent/materialized/expired"),
    relevance: Optional[str] = Query(None, description="按分级过滤：holding_impact/opportunity/market_watch"),
    limit: int = Query(50, ge=1, le=200),
):
    """查询事件列表。"""
    events = list_market_events(status=status, relevance=relevance, limit=limit)
    return ApiResponse.success(data={"events": events, "total": len(events)})


@router.get("/api/alerts/event-radar/events/{event_id}")
async def get_event(event_id: str):
    """查询事件详情。"""
    event = get_market_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="事件不存在")
    return ApiResponse.success(data=event)
```

- [ ] **Step 4: 在 app.py 注册 router**

在 `backend/app.py` 顶部 import 区（约第 148 行附近）添加：

```python
from routers.event_radar import router as event_radar_router  # /api/alerts/event-radar/*
```

在路由注册区（约第 219 行附近，跟随其他 `app.include_router` 之后）添加：

```python
app.include_router(event_radar_router)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer && python -m pytest backend/tests/test_event_radar_api.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add backend/routers/event_radar.py backend/app.py backend/tests/test_event_radar_api.py
git commit -m "feat(event-radar): 新增 3 个 API 端点"
```

---

## Task 10: 前端 AlertBell 扩展

**Files:**
- Modify: `frontend/src/components/AlertBell.vue`
- Modify: `frontend/src/api/index.js`
- Test: 手动浏览器验证

- [ ] **Step 1: 在 api/index.js 添加事件雷达 API**

在 `frontend/src/api/index.js` 中找到现有 alert 相关 API 函数（如 `listAlerts`），在其附近添加：

```javascript
// 前瞻性事件雷达
export const triggerEventRadarScan = () => api.post('/api/alerts/event-radar/scan')

export const listMarketEvents = (params = {}) =>
  api.get('/api/alerts/event-radar/events', { params })

export const getMarketEvent = (eventId) =>
  api.get(`/api/alerts/event-radar/events/${eventId}`)
```

- [ ] **Step 2: 在 AlertBell.vue 扩展 alert_type 路由**

在 `frontend/src/components/AlertBell.vue` 的 `_routeForAlert` 函数中（约第 61-67 行）添加 event_radar 分支：

```javascript
function _routeForAlert(a) {
  if (!a.alert_type) return null
  if (a.alert_type.startsWith('valuation_')) return 'valuation'
  if (a.alert_type === 'concentration_high' || a.alert_type === 'loss_warning') return 'portfolio'
  if (a.alert_type === 'recommendation_verified') return 'decisions'
  if (a.alert_type === 'event_radar') return null  // 事件雷达不跳转，直接在卡片展示
  return null
}
```

- [ ] **Step 3: 在 AlertBell.vue 扩展图标选择**

在 `severityIcon` 函数下方添加事件类型图标函数：

```javascript
function eventTypeIcon(a) {
  if (a.alert_type === 'event_radar') return 'satellite'  // 🛰️ 事件雷达图标
  return null
}

// 修改卡片渲染中的图标选择：优先事件类型图标
function alertIcon(a) {
  return eventTypeIcon(a) || severityIcon(a.severity)
}
```

- [ ] **Step 4: 在 AlertBell.vue 扩展卡片样式**

在 `severityClass` 函数中增加事件分级样式：

```javascript
function alertClass(a) {
  if (a.alert_type === 'event_radar') {
    // 事件雷达按 content 中前缀判断分级
    if (a.title && a.title.includes('[持仓影响]')) return 'alert-event-holding'
    if (a.title && a.title.includes('[建仓机会]')) return 'alert-event-opportunity'
    return 'alert-event-watch'
  }
  return severityClass(a.severity)
}
```

在 `<style scoped>` 中添加事件卡片样式（遵循非 AI 美学，与现有风格一致）：

```css
.alert-event-holding .alert-icon { color: #dc2626; }
.alert-event-opportunity .alert-icon { color: #d97706; }
.alert-event-watch .alert-icon { color: #2563eb; }

.alert-event-holding { border-left: 2px solid #dc2626; }
.alert-event-opportunity { border-left: 2px solid #d97706; }
.alert-event-watch { border-left: 2px solid #2563eb; }
```

- [ ] **Step 5: 修改卡片模板使用新函数**

在模板中将 `severityClass(a.severity)` 改为 `alertClass(a)`，将 `severityIcon(a.severity)` 改为 `alertIcon(a)`：

```html
<div v-for="a in alerts" :key="a.id"
     class="alert-row"
     :class="[alertClass(a), { 'alert-unread': !a.is_read }]"
     @click="handleClickAlert(a)">
  <Icon :name="alertIcon(a)" size="13" class="alert-icon" />
  <div class="alert-content">
    <div class="alert-title">{{ a.title }}</div>
    <div v-if="a.content" class="alert-text">{{ a.content }}</div>
    <div class="alert-meta">
      <span class="alert-time">{{ formatTimeAgo(a.latest_at || a.created_at) }}</span>
      <span v-if="a.cnt && a.cnt > 1" class="alert-cnt">×{{ a.cnt }}</span>
    </div>
  </div>
</div>
```

- [ ] **Step 6: 构建前端验证**

Run: `cd /Users/xiaoyuer/projects/investment-analyzer/frontend && npm run build`
Expected: 构建成功，输出到 `backend/static/`

- [ ] **Step 7: 浏览器验证**

1. 访问 `http://localhost:8000/app`
2. 在系统配置中开启 `alerts.event_radar_enabled = true`
3. 调用 `POST /api/alerts/event-radar/scan` 手动触发扫描
4. 点击铃铛图标，确认事件雷达类型的 alert 显示卫星图标 + 分级颜色边框

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/AlertBell.vue frontend/src/api/index.js
git commit -m "feat(event-radar): 前端 AlertBell 扩展事件雷达渲染"
```

---

## Self-Review

### Spec 覆盖检查

| Spec 章节 | 实现任务 | 状态 |
|-----------|----------|------|
| §3 market_events 表 | Task 1 | ✅ |
| §5 状态流转 | Task 6 | ✅ |
| §6 3 级推送分级 | Task 5 + Task 7 | ✅ |
| §7 LLM 事件提取 | Task 4 | ✅ |
| §8 板块→指数→基金匹配 | Task 5 | ✅ |
| §9 调度集成 | Task 8 | ✅ |
| §10 前端展示 | Task 10 | ✅ |
| §11 配置开关 | Task 2 | ✅ |
| §13 P0 + P1 | Task 1-10 | ✅ |
| §9.3 手动触发端点 | Task 9 | ✅ |

### Placeholder 扫描
- 无 TBD/TODO
- 所有代码步骤包含完整代码
- 所有测试包含具体断言

### 类型一致性
- `create_market_event` 在 Task 1 定义，Task 7 调用，签名一致
- `_determine_relevance` 在 Task 5 定义返回 `(relevance, matched, candidates)`，Task 7 解构一致
- `scan_forward_events` 在 Task 7 定义返回 dict，Task 8/9 调用一致
- `event_id` 生成逻辑（`_gen_event_id`）在 Task 1 定义，Task 7 复用

### P2 演进项（本计划不实现）
- 事件落地后行情验证
- 与建议系统联动
- 事件统计报表
- 多源新闻融合去重优化

---

## Execution Handoff

Plan complete and saved to `doc/plans/2026-07-10-forward-looking-event-radar-plan.md`. Two execution options:

1. **Subagent-Driven (recommended)** - 每个 Task 派发独立 subagent，任务间 review，快速迭代
2. **Inline Execution** - 在当前会话批量执行，带 checkpoint review

选择哪种方式？
