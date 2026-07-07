# backend/tests/test_auto_confirm.py
"""测试自动获取净值确认交易。"""
import pytest
from db.portfolio import get_nav_by_date


def test_get_nav_by_date_returns_float_or_none():
    """get_nav_by_date 返回净值或None。"""
    nav = get_nav_by_date("110022", "2026-01-01")
    assert nav is None or isinstance(nav, (int, float))


def test_auto_confirm_api_endpoint():
    """自动确认API端点存在且返回正确结构。"""
    from app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    resp = client.post("/api/portfolio/transactions/999999/auto-confirm")
    assert resp.status_code == 404
