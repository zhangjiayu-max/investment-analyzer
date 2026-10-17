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


def _wrap_payload(data, status):
    if isinstance(data, dict) and "code" in data and "message" in data:
        return data, status
    if status >= 400:
        message = next((data.get(k) for k in ('detail','message','error','msg') if data.get(k)), '请求失败') if isinstance(data, dict) else '请求失败'
        return {'code':status,'message':message,'data':None}, status
    if isinstance(data, dict) and data.get('ok') is False:
        message = next((data.get(k) for k in ('error','msg','message') if data.get(k)), '操作失败')
        return {'code':400,'message':message,'data':None}, 400
    if isinstance(data, dict) and 'ok' in data:
        data = {k:v for k,v in data.items() if k != 'ok'} or None
    return {'code':0,'message':'ok','data':data}, status


class ResponseWrapperMiddleware:
    """仅包装业务 JSON；原生 ASGI 直通 SSE 和 OpenAPI，不消费断连接收通道。"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path','').startswith('/api/'):
            return await self.app(scope, receive, send)
        response_start = None
        chunks = []
        buffering = False
        async def send_wrapped(message):
            nonlocal response_start, buffering
            if message['type'] == 'http.response.start':
                headers = dict(message.get('headers', []))
                buffering = (b'application/json' in headers.get(b'content-type', b'')
                             and not headers.get(b'content-encoding'))
                if buffering:
                    response_start = dict(message)
                    return
            elif message['type'] == 'http.response.body' and buffering:
                chunks.append(message.get('body', b''))
                if message.get('more_body', False):
                    return
                body = b''.join(chunks)
                try:
                    data, status = _wrap_payload(json.loads(body), response_start['status'])
                    body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
                    response_start['status'] = status
                except (ValueError, TypeError):
                    pass
                response_start['headers'] = [(k,v) for k,v in response_start.get('headers', [])
                                             if k.lower() != b'content-length'] + [(b'content-length',str(len(body)).encode())]
                await send(response_start)
                await send({**message,'body':body})
                return
            await send(message)
        await self.app(scope, receive, send_wrapped)
