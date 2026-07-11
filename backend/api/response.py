"""统一 API 响应协议

所有 API 响应遵循标准格式：
    {"code": 0, "message": "ok", "data": <any>}

- code=0 表示成功，非 0 表示业务错误（与 HTTP status code 对齐）
- message 为人类可读消息
- data 为实际数据（dict / list / null）
"""
from typing import Any


class ApiResponse:
    """标准响应封装 — 新增路由应使用此类返回标准格式"""

    @staticmethod
    def success(data: Any = None, message: str = "ok") -> dict:
        """成功响应"""
        return {"code": 0, "message": message, "data": data}

    @staticmethod
    def error(code: int = 400, message: str = "操作失败", data: Any = None) -> dict:
        """错误响应"""
        return {"code": code, "message": message, "data": data}

    @staticmethod
    def paginate(items: list, total: int, page: int = 1, page_size: int = 20) -> dict:
        """分页响应"""
        return {
            "code": 0,
            "message": "ok",
            "data": {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }


class BizError(Exception):
    """业务异常基类 — 抛出后由全局异常处理器转为标准错误响应。

    用法：
        raise BizError("基金代码不能为空")
        raise NotFoundError("对话不存在")
        raise ConflictError("对话状态不允许此操作")
    """

    def __init__(self, message: str, code: int = 400, data: Any = None):
        self.message = message
        self.code = code
        self.data = data
        super().__init__(message)


class NotFoundError(BizError):
    """资源不存在（404）"""

    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, code=404)


class ConflictError(BizError):
    """状态冲突（409）"""

    def __init__(self, message: str = "状态冲突"):
        super().__init__(message, code=409)


class ValidationError(BizError):
    """参数校验失败（422）"""

    def __init__(self, message: str = "参数校验失败"):
        super().__init__(message, code=422)


class UpstreamError(BizError):
    """上游服务错误（502）"""

    def __init__(self, message: str = "上游服务错误"):
        super().__init__(message, code=502)
