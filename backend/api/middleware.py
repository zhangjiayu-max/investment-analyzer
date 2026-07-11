"""全局异常处理器 + 响应包装中间件

功能：
1. 注册 4 类异常处理器（BizError / HTTPException / RequestValidationError / Exception）
2. 响应包装中间件自动将裸 dict/list 包装为 {code, message, data} 标准格式
"""
import json
import logging

from fastapi import Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .response import ApiResponse, BizError

logger = logging.getLogger(__name__)


def register_exception_handlers(app):
    """注册全局异常处理器，统一所有错误响应为 {code, message, data} 格式"""

    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        return JSONResponse(
            status_code=exc.code,
            content=ApiResponse.error(exc.code, exc.message, exc.data),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.error(exc.status_code, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = " → ".join(str(x) for x in first.get("loc", []))
            msg = first.get("msg", "参数校验失败")
            message = f"{loc}: {msg}" if loc else msg
        else:
            message = "参数校验失败"
        return JSONResponse(
            status_code=422,
            content={"code": 422, "message": message, "data": {"errors": errors}},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception(f"[API] 未捕获异常: {request.url.path}")
        return JSONResponse(
            status_code=500,
            content=ApiResponse.error(500, "服务器内部错误"),
        )


class ResponseWrapperMiddleware(BaseHTTPMiddleware):
    """自动将路由返回的裸 dict/list 包装为标准 {code, message, data} 格式。

    排除规则：
    - 非 application/json 响应（文件下载、HTML、静态资源）
    - text/event-stream（SSE 流式响应）
    - 已是标准格式（含 code + message 字段）
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        content_type = response.headers.get("content-type", "")

        # 排除非 JSON 响应（文件下载、HTML、SSE 等）
        if "application/json" not in content_type:
            return response

        # 排除 SSE 流式响应
        if "text/event-stream" in content_type:
            return response

        # 读取响应体
        try:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
        except Exception:
            return response

        if not body:
            return response

        # 解析 JSON
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            # 非 JSON 内容，原样返回
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                media_type=response.media_type,
                headers=headers,
            )

        # 已是标准格式（含 code + message 字段），不重复包装
        if isinstance(data, dict) and "code" in data and "message" in data:
            # 清除 content-length（可能与 body 不匹配）
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                media_type="application/json",
                headers=headers,
            )

        # 统一包装
        if response.status_code >= 400:
            # 错误响应：提取错误消息（兼容 detail/message/error/msg 四种字段名）
            message = (
                data.get("detail")
                or data.get("message")
                or data.get("error")
                or data.get("msg")
                or "请求失败"
            ) if isinstance(data, dict) else "请求失败"
            new_body = {"code": response.status_code, "message": message, "data": None}
        elif isinstance(data, dict) and data.get("ok") is False:
            # HTTP 200 但业务失败（ok=False 反模式）→ 转为错误响应
            message = (
                data.get("error")
                or data.get("msg")
                or data.get("message")
                or "操作失败"
            )
            new_body = {"code": 400, "message": message, "data": None}
        elif isinstance(data, dict) and "ok" in data:
            # {"ok": True, ...} 格式：提取非 ok 字段作为 data
            payload = {k: v for k, v in data.items() if k != "ok"}
            new_body = {
                "code": 0,
                "message": "ok",
                "data": payload if payload else None,
            }
        else:
            # 裸 dict / 裸 list / 其他格式
            new_body = {"code": 0, "message": "ok", "data": data}

        # 清除 content-length（新 body 长度已变化）
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=json.dumps(new_body, ensure_ascii=False, default=str),
            status_code=200 if new_body["code"] == 0 else new_body["code"],
            media_type="application/json",
            headers=headers,
        )
