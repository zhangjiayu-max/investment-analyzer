"""请求级追踪 — request_id 全链路贯穿。"""

import uuid
import time
import logging
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# asyncio 任务之间隔离，to_thread/copy_context 可传播给阻塞工作。
_request_id = ContextVar("request_id", default="unknown")
_request_start = ContextVar("request_start", default=None)


def get_request_id() -> str:
    """获取当前请求的 request_id。"""
    return _request_id.get()


def set_request_id(request_id: str):
    """设置当前请求的 request_id。"""
    return _request_id.set(request_id)


def get_request_start() -> float:
    """获取当前请求的开始时间。"""
    return _request_start.get() or time.time()


def set_request_start(t: float):
    return _request_start.set(t)


def generate_request_id() -> str:
    """生成新的 request_id（短格式）。"""
    return uuid.uuid4().hex[:12]


class RequestTracingMiddleware:
    """FastAPI 中间件：为每个请求注入 request_id。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request_id = generate_request_id()
            id_token = set_request_id(request_id)
            start_token = set_request_start(time.time())

            # 注入到响应头
            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    headers = [(k,v) for k,v in message.get("headers", []) if k.lower() != b"x-request-id"]
                    message["headers"] = headers + [(b"x-request-id", request_id.encode())]
                await send(message)

            try:
                await self.app(scope, receive, send_with_header)
            finally:
                _request_start.reset(start_token)
                _request_id.reset(id_token)
        else:
            await self.app(scope, receive, send)


def log_with_request_id(msg: str, level: str = "info"):
    """带 request_id 的日志。"""
    rid = get_request_id()
    elapsed = time.time() - get_request_start()
    full_msg = f"[{rid}] {msg} ({elapsed:.1f}s)"
    getattr(logger, level)(full_msg)
