"""请求级追踪 — request_id 全链路贯穿。"""

import uuid
import time
import logging
import threading

logger = logging.getLogger(__name__)

# 线程本地存储保存当前请求上下文
_request_ctx = threading.local()


def get_request_id() -> str:
    """获取当前请求的 request_id。"""
    return getattr(_request_ctx, "request_id", "unknown")


def set_request_id(request_id: str):
    """设置当前请求的 request_id。"""
    _request_ctx.request_id = request_id


def get_request_start() -> float:
    """获取当前请求的开始时间。"""
    return getattr(_request_ctx, "start_time", time.time())


def set_request_start(t: float):
    _request_ctx.start_time = t


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
            set_request_id(request_id)
            set_request_start(time.time())

            # 注入到响应头
            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    headers = dict(message.get("headers", {}))
                    headers[b"x-request-id"] = request_id.encode()
                    message["headers"] = list(headers.items())
                await send(message)

            await self.app(scope, receive, send_with_header)
        else:
            await self.app(scope, receive, send)


def log_with_request_id(msg: str, level: str = "info"):
    """带 request_id 的日志。"""
    rid = get_request_id()
    elapsed = time.time() - get_request_start()
    full_msg = f"[{rid}] {msg} ({elapsed:.1f}s)"
    getattr(logger, level)(full_msg)
