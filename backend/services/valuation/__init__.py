"""valuation 子包 — 向后兼容 ``services.valuation`` 的旧导入路径。

旧代码 ``from services.valuation import xxx`` 通过本文件的 ``__getattr__``
懒转发到 ``services.valuation.valuation`` 模块（即原 ``services/valuation.py``）。
"""

import importlib

_valuation_module = None


def _get_valuation_module():
    global _valuation_module
    if _valuation_module is None:
        _valuation_module = importlib.import_module("services.valuation.valuation")
    return _valuation_module


def __getattr__(name):
    return getattr(_get_valuation_module(), name)
