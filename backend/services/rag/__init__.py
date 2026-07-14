"""rag 子包 — 向后兼容 ``services.rag`` 的旧导入路径。

旧代码 ``from services.rag import xxx`` 通过本文件的 ``__getattr__``
懒转发到 ``services.rag.rag`` 模块（即原 ``services/rag.py``）。
"""

import importlib

_rag_module = None


def _get_rag_module():
    global _rag_module
    if _rag_module is None:
        _rag_module = importlib.import_module("services.rag.rag")
    return _rag_module


def __getattr__(name):
    return getattr(_get_rag_module(), name)
