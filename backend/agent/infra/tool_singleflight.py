"""同一请求的并发只读工具共享执行；等待遵守调用方截止时间和取消。"""
from concurrent.futures import Future
from threading import Lock

from infra.execution_budget import wait_result


class SharedToolCalls:
    def __init__(self):
        self._lock = Lock()
        self._pending = {}

    def run(self, key, fn, timeout):
        with self._lock:
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = self._pending[key] = Future()
        if not owner:
            return wait_result(future, timeout)
        try:
            result = fn()
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)


shared_tool_calls = SharedToolCalls()
