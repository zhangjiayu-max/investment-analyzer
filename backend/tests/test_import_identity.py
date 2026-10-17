"""旧路径与重组后路径必须共享模块状态和 mock。"""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("agent_first", [False, True])
@pytest.mark.parametrize("legacy_first", [False, True])
@pytest.mark.parametrize("legacy,canonical", [
    ("services.smart_add_metrics", "services.advisor.smart_add_metrics"),
    ("services.dragon_tiger", "services.market.dragon_tiger"),
    ("agent.cache", "agent.infra.cache"),
])
def test_import_alias_preserves_identity(agent_first, legacy_first, legacy, canonical):
    # 新进程隔离 import 顺序；禁止连接，且只导入无 DB 副作用的模块。
    script = f"""
import importlib, socket, sys
def no_network(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket.connect = no_network
socket.create_connection = no_network
importlib.import_module({'agent' if agent_first else 'services'!r})
first = importlib.import_module({legacy if legacy_first else canonical!r})
second = importlib.import_module({canonical if legacy_first else legacy!r})
assert first is second
assert first.__spec__.name == {canonical!r}, first.__spec__.name
first._identity_test_value = object()
assert second._identity_test_value is first._identity_test_value
parent, _, leaf = {legacy!r}.rpartition('.')
assert getattr(importlib.import_module(parent), leaf) is first
import agent, services
assert not any(type(f).__name__ == '_AgentRelocationFinder' and
               f.find_spec('services.smart_add_metrics', None) is not None
               for f in sys.meta_path)
before = len(sys.meta_path)
importlib.reload(agent)
importlib.reload(services)
assert len(sys.meta_path) == before
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
