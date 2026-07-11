# API 标准化设计稿

> **日期**：2026-07-10
> **目标**：统一 500 条 API 路由的响应格式和异常处理，实现标准协议

---

## 现状问题

| 问题 | 现状 |
|------|------|
| 返回格式 | 7 种风格并存（裸 dict / `{"ok":True,"id":...}` / `{"ok":True,"data":...}` / 裸 list / `{"error":...}` 等）|
| 错误字段名 | 4 种混用（error / msg / message / detail）|
| 列表键名 | 9 种变体（items / results / records / data / list / configs / runs / backups / tools）|
| 异常处理 | 313 处 HTTPException 两种调用风格混用，仅 1 个全局兜底处理器 |
| 统一响应类 | 0 个 |
| 分页规范 | 无 |

---

## 标准响应协议

### 成功响应（HTTP 200）

```json
{
  "code": 0,
  "message": "ok",
  "data": <any>
}
```

- `data` 可以是 dict / list / null
- 列表类型统一用 `data` 数组，不再用 items/results/records 等变体

### 分页响应

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "items": [...],
    "total": 100,
    "page": 1,
    "page_size": 20
  }
}
```

### 错误响应（HTTP 4xx/5xx）

```json
{
  "code": 400,
  "message": "基金代码不能为空",
  "data": null
}
```

- `code` = HTTP status code
- `message` = 人类可读错误消息

### code 编码规范

| code | 含义 |
|------|------|
| 0 | 成功 |
| 400 | 参数错误 / 业务规则失败 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 409 | 状态冲突 |
| 422 | 请求体校验失败 |
| 500 | 服务器内部错误 |
| 502 | 上游服务错误 |
| 504 | 超时 |

---

## 技术方案：中间件自动包装 + 异常处理器

### 核心思路

**不改 500 条路由的函数体**，通过中间件自动包装响应 + 异常处理器统一错误格式。

```
路由函数返回裸 dict/list
        ↓
响应包装中间件自动识别并包装为 {code, message, data}
        ↓
前端拦截器自动解包 response.data.data → 组件代码无感知
```

### 优势

1. **零侵入**：500 条路由代码不用改
2. **一次性覆盖**：中间件 + 异常处理器覆盖所有路由
3. **前端无感**：拦截器解包后组件代码不变

---

## Layer 1：统一响应类 + 异常体系

### 文件：`backend/api/response.py`（新建）

```python
"""统一 API 响应协议"""
from typing import Any, Optional


class ApiResponse:
    """标准响应封装"""
    
    @staticmethod
    def success(data: Any = None, message: str = "ok") -> dict:
        return {"code": 0, "message": message, "data": data}
    
    @staticmethod
    def error(code: int = 400, message: str = "操作失败", data: Any = None) -> dict:
        return {"code": code, "message": message, "data": data}
    
    @staticmethod
    def paginate(items: list, total: int, page: int = 1, page_size: int = 20) -> dict:
        return {
            "code": 0, 
            "message": "ok", 
            "data": {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size
            }
        }


class BizError(Exception):
    """业务异常基类 — 抛出后由全局异常处理器转为标准错误响应"""
    def __init__(self, message: str, code: int = 400, data: Any = None):
        self.message = message
        self.code = code
        self.data = data
        super().__init__(message)


class NotFoundError(BizError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, code=404)


class ConflictError(BizError):
    def __init__(self, message: str = "状态冲突"):
        super().__init__(message, code=409)


class ValidationError(BizError):
    def __init__(self, message: str = "参数校验失败"):
        super().__init__(message, code=422)


class UpstreamError(BizError):
    def __init__(self, message: str = "上游服务错误"):
        super().__init__(message, code=502)
```

---

## Layer 2：全局异常处理器 + 响应包装中间件

### 文件：`backend/api/middleware.py`（新建）

#### 异常处理器

```python
"""全局异常处理器 + 响应包装中间件"""
import json
import logging
from fastapi import Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from .response import ApiResponse, BizError

logger = logging.getLogger(__name__)


def register_exception_handlers(app):
    """注册全局异常处理器"""
    
    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        return JSONResponse(
            status_code=exc.code,
            content=ApiResponse.error(exc.code, exc.message, exc.data)
        )
    
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.error(exc.status_code, str(exc.detail))
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        # 提取第一个错误的可读消息
        if errors:
            first = errors[0]
            loc = " → ".join(str(x) for x in first.get("loc", []))
            msg = first.get("msg", "参数校验失败")
            message = f"{loc}: {msg}" if loc else msg
        else:
            message = "参数校验失败"
        return JSONResponse(
            status_code=422,
            content={"code": 422, "message": message, "data": {"errors": errors}}
        )
    
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception(f"[API] 未捕获异常: {request.url.path}")
        return JSONResponse(
            status_code=500,
            content=ApiResponse.error(500, "服务器内部错误")
        )
```

#### 响应包装中间件

```python
class ResponseWrapperMiddleware(BaseHTTPMiddleware):
    """自动将路由返回的裸 dict/list 包装为标准 {code, message, data} 格式"""
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # 排除非 JSON 响应（文件下载、HTML、SSE 等）
        content_type = response.headers.get("content-type", "")
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
            return Response(
                content=body,
                status_code=response.status_code,
                media_type=response.media_type,
                headers=dict(response.headers)
            )
        
        # 已是标准格式（含 code 字段），不重复包装
        if isinstance(data, dict) and "code" in data and "message" in data:
            return Response(
                content=body,
                status_code=response.status_code,
                media_type="application/json",
                headers=dict(response.headers)
            )
        
        # 统一包装
        if response.status_code >= 400:
            # 错误响应：提取错误消息
            message = (
                data.get("detail") or 
                data.get("message") or 
                data.get("error") or 
                data.get("msg") or 
                "请求失败"
            ) if isinstance(data, dict) else "请求失败"
            new_body = {"code": response.status_code, "message": message, "data": None}
        elif isinstance(data, dict) and data.get("ok") is False:
            # HTTP 200 但业务失败（ok=False 反模式）
            message = data.get("error") or data.get("msg") or data.get("message") or "操作失败"
            new_body = {"code": 400, "message": message, "data": None}
        elif isinstance(data, dict) and "ok" in data:
            # {"ok": True, ...} 格式：提取非 ok 字段作为 data
            payload = {k: v for k, v in data.items() if k != "ok"}
            new_body = {"code": 0, "message": "ok", "data": payload if payload else None}
        else:
            # 裸 dict / 裸 list / 其他格式
            new_body = {"code": 0, "message": "ok", "data": data}
        
        return Response(
            content=json.dumps(new_body, ensure_ascii=False, default=str),
            status_code=200 if new_body["code"] == 0 else new_body["code"],
            media_type="application/json",
            headers=dict(response.headers)
        )
```

### 注册到 app.py

在 `app.py` 中添加：

```python
from api.middleware import register_exception_handlers, ResponseWrapperMiddleware

# 中间件（在 CORS 之前注册，确保响应包装生效）
app.add_middleware(ResponseWrapperMiddleware)

# 异常处理器（替换原有全局兜底）
register_exception_handlers(app)
```

**关键**：移除原有的 `@app.exception_handler(Exception)` 全局兜底（line 75-81），由新的 `register_exception_handlers` 统一管理。

---

## Layer 3：前端适配

### 文件：`frontend/src/api/interceptors.js`

```javascript
api.interceptors.response.use(
  (response) => {
    // 标准协议解包：{code, message, data} → data
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) {
        // 成功：解包 data，组件代码无感知
        response.data = body.data
        return response
      } else {
        // 业务失败（HTTP 200 但 code != 0）
        const msg = body.message || '操作失败'
        console.error('[API] Business error:', body.code, msg)
        return Promise.reject(new Error(msg))
      }
    }
    return response
  },
  (error) => {
    if (!error.response) {
      const msg = error.message || '网络连接失败，请检查网络'
      console.error('[API Error]', msg)
      return Promise.reject(error)
    }

    const { status, data } = error.response
    
    // 标准协议错误：{code, message, data}
    if (data && typeof data === 'object' && 'code' in data) {
      const msg = data.message || `请求失败 (${status})`
      console.error(`[API] ${status} Error:`, msg)
      return Promise.reject(new Error(msg))
    }

    // 兼容旧格式（过渡期）
    const msg = data?.detail || data?.message || data?.error || data?.msg || `请求失败 (${status})`
    return Promise.reject(new Error(msg))
  }
)
```

### 前端组件代码

**不需要改**。拦截器解包后 `response.data` 还是原来的数据结构。

---

## 中间件排除清单

以下响应类型**不包装**，保持原样：

| 类型 | 判断条件 | 原因 |
|------|---------|------|
| SSE 流式 | `content-type` 含 `text/event-stream` | SSE 有自己的事件格式 |
| 文件下载 | `content-type` 非 `application/json` | 二进制文件 |
| HTML 页面 | `content-type` 含 `text/html` | 前端页面 |
| 静态资源 | `content-type` 含 `application/javascript` 等 | JS/CSS |
| CSV 导出 | `content-disposition` header | 文件下载 |

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 中间件读取 body 消耗流 | 排除 SSE 和非 JSON 响应 |
| 已有 `{"ok":False}` 被误判 | 中间件识别 `ok===false` 转为错误响应 |
| 前端组件依赖 `ok` 字段 | 拦截器解包后组件拿到的是 `data` 字段值 |
| 性能损耗 | 仅 JSON 响应多一次序列化，影响可忽略 |
| SSE 事件格式被破坏 | content-type 排除规则 |

---

## 执行顺序

1. 新建 `backend/api/response.py`（统一响应类 + 异常体系）
2. 新建 `backend/api/middleware.py`（异常处理器 + 响应包装中间件）
3. 修改 `backend/app.py`（注册中间件 + 异常处理器，移除旧全局兜底）
4. 修改 `frontend/src/api/interceptors.js`（解包标准协议）
5. 构建前端 + 重启后端验证
