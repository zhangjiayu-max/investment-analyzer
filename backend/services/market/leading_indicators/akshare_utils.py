"""akshare 统一超时保护 — 防止 zombie 线程卡死。

提取自 event_radar._call_akshare_with_timeout，供所有 Provider 复用。
解决 conv#133 曾因 akshare zombie 线程卡死 6 分钟的问题。
"""
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

logger = logging.getLogger(__name__)
_AKSHARE_TIMEOUT = 15  # 秒，与 event_radar 一致


def call_akshare_with_timeout(fn, *args, timeout: int = _AKSHARE_TIMEOUT, **kwargs):
    """在 ThreadPoolExecutor 中调用 akshare 函数，超时返回 None。

    Args:
        fn: akshare 函数（如 ak.stock_notice_report）
        timeout: 超时秒数
        *args, **kwargs: 传给 fn 的参数

    Returns:
        fn 的返回值，超时或异常返回 None
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            fn_name = getattr(fn, "__name__", str(fn))
            logger.warning(f"[akshare] 超时 {timeout}s: {fn_name}")
            return None
        except Exception as e:
            fn_name = getattr(fn, "__name__", str(fn))
            logger.warning(f"[akshare] 调用失败 {fn_name}: {e}")
            return None
