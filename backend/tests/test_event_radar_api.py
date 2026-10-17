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
    # 扫描已改为后台任务，接口只保证立即返回可轮询的任务凭据。
    assert data["code"] == 0
    assert data["data"]["status"] == "running"
    assert data["data"]["task_id"]


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
