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
    assert event_id

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
    assert ev["confidence"] == 0.8  # 不覆盖


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
