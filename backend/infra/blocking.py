"""阻塞任务的有界线程入口；取消等待不会提前释放仍运行的工作槽。"""
import asyncio
import contextvars
import functools
import threading
from concurrent.futures import ThreadPoolExecutor

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix='business-io')
_SLOTS = threading.BoundedSemaphore(8)


async def run_blocking(func, *args, **kwargs):
    """无界提交队列前先等待空闲槽，保持事件循环可响应。"""
    while not _SLOTS.acquire(blocking=False):
        await asyncio.sleep(.01)
    context = contextvars.copy_context()
    def work():
        try:
            return context.run(functools.partial(func, *args, **kwargs))
        finally:
            _SLOTS.release()
    try:
        future = _EXECUTOR.submit(work)
    except BaseException:
        _SLOTS.release()
        raise
    # 不取消底层future，避免排队期间取消导致槽永不释放。
    return await asyncio.shield(asyncio.wrap_future(future))
