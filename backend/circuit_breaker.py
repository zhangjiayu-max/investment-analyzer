"""熔断器 — 连续失败 N 次后自动跳过调用。"""

import time
import logging
import functools

logger = logging.getLogger(__name__)


class CircuitOpenError(Exception):
    """熔断器开启异常。"""
    pass


class CircuitBreaker:
    """简单熔断器实现。

    - failure_threshold: 连续失败多少次后开启熔断
    - recovery_timeout: 熔断多少秒后尝试恢复
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0
        self.state = "closed"  # closed | open | half_open

    def call(self, fn, *args, **kwargs):
        """执行调用，带熔断保护。"""
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half_open"
                logger.info(f"熔断器半开: {fn.__name__}")
            else:
                raise CircuitOpenError(
                    f"熔断器开启: {fn.__name__} 已连续失败 {self.failures} 次，"
                    f"将在 {self.recovery_timeout - (time.time() - self.last_failure_time):.0f}s 后重试"
                )

        try:
            result = fn(*args, **kwargs)
            if self.state == "half_open":
                logger.info(f"熔断器恢复: {fn.__name__}")
            self.failures = 0
            self.state = "closed"
            return result
        except Exception as e:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.failure_threshold:
                self.state = "open"
                logger.warning(f"熔断器开启: {fn.__name__} 连续失败 {self.failures} 次")
            raise

    def reset(self):
        self.failures = 0
        self.state = "closed"


# 全局熔断器实例
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """获取或创建命名熔断器。"""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(**kwargs)
    return _breakers[name]


def with_circuit_breaker(name: str, **kwargs):
    """装饰器：为函数添加熔断保护。"""
    def decorator(fn):
        breaker = get_breaker(name, **kwargs)

        @functools.wraps(fn)
        def wrapper(*args, **kw):
            return breaker.call(fn, *args, **kw)

        wrapper.breaker = breaker
        return wrapper
    return decorator
