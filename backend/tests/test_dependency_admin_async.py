"""扫码 CLI 等待期间不得阻塞后端事件循环。"""
import asyncio
import threading
import pytest


@pytest.mark.parametrize('action', ['status', 'login', 'install'])
def test_cli_dependency_admin_runs_outside_event_loop(monkeypatch, action):
    from routers.admin import config as route
    from mcp import ttfund_client as client
    event_loop_thread = threading.get_ident()
    seen = []
    def cli_call():
        seen.append(threading.get_ident())
        return {'ok': True, 'installed': True, 'logged_in': False}
    monkeypatch.setattr(client, 'is_installed', lambda: True)
    monkeypatch.setattr(client, 'check_login' if action == 'status' else action, cli_call)
    asyncio.run(getattr(route, 'ttfund_' + action)())
    assert seen and seen[0] != event_loop_thread
