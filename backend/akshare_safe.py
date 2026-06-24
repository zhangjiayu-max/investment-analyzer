"""akshare 安全封装 — 带熔断器和超时保护。"""

import logging
from circuit_breaker import with_circuit_breaker, get_breaker, CircuitOpenError

logger = logging.getLogger(__name__)

# akshare 全局熔断器：连续失败 5 次后熔断 5 分钟
_ak_breaker = get_breaker("akshare", failure_threshold=5, recovery_timeout=300)


def safe_call(fn, *args, default=None, timeout=30, **kwargs):
    """安全调用 akshare 函数，超时或熔断时返回默认值。

    用法:
        from akshare_safe import safe_call
        import akshare as ak
        df = safe_call(ak.stock_zh_a_spot_em, default=[])
    """
    try:
        return _ak_breaker.call(fn, *args, **kwargs)
    except CircuitOpenError:
        logger.warning(f"akshare 熔断中，返回默认值: {fn.__name__}")
        return default
    except Exception as e:
        logger.warning(f"akshare 调用失败: {fn.__name__} — {e}")
        return default


def is_open() -> bool:
    """akshare 熔断器是否开启。"""
    return _ak_breaker.state == "open"


def reset():
    """手动重置熔断器。"""
    _ak_breaker.reset()
