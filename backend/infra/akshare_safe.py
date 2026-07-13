"""akshare 安全封装 — 带熔断器和超时保护。"""

import logging
import concurrent.futures
from infra.circuit_breaker import with_circuit_breaker, get_breaker, CircuitOpenError

logger = logging.getLogger(__name__)

# akshare 全局熔断器：连续失败 5 次后熔断 5 分钟
_ak_breaker = get_breaker("akshare", failure_threshold=5, recovery_timeout=300)


def safe_call(fn, *args, default=None, timeout=30, **kwargs):
    """安全调用 akshare 函数，超时或熔断时返回默认值。

    2026-07-13 修复：原实现 timeout 参数未被使用，现已通过 ThreadPoolExecutor 生效。

    用法:
        from infra.akshare_safe import safe_call
        import akshare as ak
        df = safe_call(ak.stock_zh_a_spot_em, default=[], timeout=15)
    """
    try:
        # 先检查熔断器状态（避免在线程池中等待后才触发熔断）
        if _ak_breaker.state == "open":
            import time
            if time.time() - _ak_breaker.last_failure_time > _ak_breaker.recovery_timeout:
                _ak_breaker.state = "half_open"
            else:
                raise CircuitOpenError(f"熔断器开启: {getattr(fn, '__name__', 'call')}")

        # 用线程池 + 超时保护调用（akshare 同步阻塞，可能无限挂起）
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn, *args, **kwargs)
            try:
                result = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning(f"akshare 超时({timeout}s): {getattr(fn, '__name__', 'call')}")
                # 超时也计入失败，可能触发熔断
                _ak_breaker.failures += 1
                _ak_breaker.last_failure_time = __import__('time').time()
                if _ak_breaker.failures >= _ak_breaker.failure_threshold:
                    _ak_breaker.state = "open"
                    logger.warning(f"熔断器开启: {getattr(fn, '__name__', 'call')} 连续失败 {_ak_breaker.failures} 次")
                return default

        # 成功：重置熔断器
        _ak_breaker.failures = 0
        _ak_breaker.state = "closed"
        return result
    except CircuitOpenError:
        logger.warning(f"akshare 熔断中，返回默认值: {getattr(fn, '__name__', 'call')}")
        return default
    except Exception as e:
        # 调用失败：计入熔断器
        _ak_breaker.failures += 1
        _ak_breaker.last_failure_time = __import__('time').time()
        if _ak_breaker.failures >= _ak_breaker.failure_threshold:
            _ak_breaker.state = "open"
            logger.warning(f"熔断器开启: {getattr(fn, '__name__', 'call')} 连续失败 {_ak_breaker.failures} 次")
        logger.warning(f"akshare 调用失败: {getattr(fn, '__name__', 'call')} — {e}")
        return default


def is_open() -> bool:
    """akshare 熔断器是否开启。"""
    return _ak_breaker.state == "open"


def reset():
    """手动重置熔断器。"""
    _ak_breaker.reset()
