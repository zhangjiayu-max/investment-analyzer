"""akshare 统一超时保护 — 防止 zombie 线程卡死。

提取自 event_radar._call_akshare_with_timeout，供所有 Provider 复用。
解决 conv#133 曾因 akshare zombie 线程卡死 6 分钟的问题。

F-akshare（2026-07-23）：修复 shutdown(wait=True) 无限等待 zombie 线程的致命 bug。
原实现 `with ThreadPoolExecutor(max_workers=1) as executor:` 退出时调用
shutdown(wait=True)，会等待 pending future 完成。但 akshare 内部 requests 无超时，
网络异常时 future 永不完成，导致 shutdown 无限等待 → 整个调用链卡死。
修复：手动管理 executor + shutdown(wait=False, cancel_futures=True)，
不等待 zombie 线程，让其在后台自行结束或被 GC 回收。
"""
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

logger = logging.getLogger(__name__)
_AKSHARE_TIMEOUT = 15  # 秒，与 event_radar 一致


def call_akshare_with_timeout(fn, *args, timeout: int = _AKSHARE_TIMEOUT, **kwargs):
    """在 ThreadPoolExecutor 中调用 akshare 函数，超时返回 None。

    F-akshare 修复：不再使用 `with` 语句，手动管理 executor 生命周期，
    超时后用 shutdown(wait=False, cancel_futures=True) 立即释放，不等待 zombie 线程。

    Args:
        fn: akshare 函数（如 ak.stock_notice_report）
        timeout: 超时秒数
        *args, **kwargs: 传给 fn 的参数

    Returns:
        fn 的返回值，超时或异常返回 None
    """
    executor = ThreadPoolExecutor(max_workers=1)
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
    finally:
        # 关键修复：wait=False 不等待 zombie 线程，cancel_futures=True 取消未开始的 future
        executor.shutdown(wait=False, cancel_futures=True)
