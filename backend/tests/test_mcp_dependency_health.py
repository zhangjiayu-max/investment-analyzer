"""外部金融依赖契约；全部使用本地响应，禁止联网/登录/安装。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def reject(*args, **kwargs):
        raise AssertionError("unexpected network")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


def test_eastmoney_finance_data_uses_published_skill_contract(monkeypatch):
    from mcp import eastmoney_client as mod
    payload = {"status": 0, "data": {"data": {"searchDataResultDTO": {
        "dataTableDTOList": [{"code": "000300.SH"}]
    }}}}
    post = Mock(return_value=httpx.Response(200, json=payload,
                 request=httpx.Request("POST", "https://example.test")))
    monkeypatch.setattr(mod.httpx, "post", post)
    assert mod.EastMoneyClient("test-only-key").finance_data("沪深300指数最新点位") == payload
    args, kwargs = post.call_args
    assert args[0] == "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
    assert kwargs["headers"]["apikey"] == "test-only-key"
    assert kwargs["json"] == {"toolQuery": "沪深300指数最新点位"}


@pytest.mark.parametrize("status,expected_state", [
    ({"auth": {"current_user": {"is_expired": True, "expires_at": "2026-01-01"}}}, "expired"),
    ({"auth": {"current_user": {"is_expired": False}}}, "ready"),
    ({"auth": {}}, "login_required"),
])
def test_ttfund_status_exposes_user_action(monkeypatch, status, expected_state):
    from mcp import ttfund_client as mod
    monkeypatch.setattr(mod, "_find_ttskill", lambda: "/test/ttskill")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(status), stderr=""))
    monkeypatch.setattr(mod.subprocess, "run", run)
    result = mod.check_login()
    assert result["state"] == expected_state
    assert result["logged_in"] is (expected_state == "ready")
    if expected_state != "ready":
        assert "扫码" in result["error"]
        assert result["login_endpoint"] == "/api/config/ttfund/login"
    assert run.call_args.args[0] == ["/test/ttskill", "status", "--json"]


def test_ttfund_missing_cli_returns_status_without_install(monkeypatch):
    from mcp import ttfund_client as mod
    def missing():
        raise RuntimeError("ttskill CLI 未安装")
    monkeypatch.setattr(mod, "_find_ttskill", missing)
    result = mod.check_login()
    assert result["state"] == "not_installed"
    assert result["installed"] is False


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("text_method", [False, True])
def test_ttfund_auth_error_is_actionable_even_with_zero_exit(monkeypatch, returncode, text_method):
    from mcp import ttfund_client as mod
    monkeypatch.setattr(mod, "_find_ttskill", lambda: "/test/ttskill")
    run = Mock(return_value=SimpleNamespace(returncode=returncode,
        stdout=json.dumps({"code": "cli_login_required", "message": "token expired"}), stderr=""))
    monkeypatch.setattr(mod.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="系统配置.*天天基金.*扫码登录"):
        client = mod.TtfundClient()
        client.fund_search("110011") if text_method else client.fund_base_info("110011")
    assert run.call_count == 1
