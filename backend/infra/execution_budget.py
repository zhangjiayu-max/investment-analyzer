"""有界执行与共享截止时间；超时返回不代表已杀死工作线程。"""
import contextvars
import functools
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

_DEADLINE = contextvars.ContextVar('execution_deadline', default=None)
_CANCEL = contextvars.ContextVar('execution_cancel', default=None)


class ExecutionCancelled(Exception):
    pass


@contextmanager
def execution_budget(seconds, cancel_event=None):
    parent = _DEADLINE.get()
    deadline = time.monotonic() + max(0,seconds)
    deadline_token = _DEADLINE.set(min(parent,deadline) if parent else deadline)
    cancel_token = _CANCEL.set(cancel_event or _CANCEL.get())
    try:
        yield
    finally:
        _CANCEL.reset(cancel_token)
        _DEADLINE.reset(deadline_token)


def remaining_seconds(default=180):
    cancel = _CANCEL.get()
    if cancel and cancel.is_set():
        raise ExecutionCancelled('执行已取消')
    deadline = _DEADLINE.get()
    remaining = min(default,deadline-time.monotonic()) if deadline else default
    if remaining <= 0:
        raise TimeoutError('执行预算已耗尽')
    return remaining


def wait_result(future, timeout, cancel_event=None):
    deadline = time.monotonic()+max(0,timeout)
    while True:
        if cancel_event and cancel_event.is_set():
            raise ExecutionCancelled('执行已取消')
        remaining = min(deadline-time.monotonic(),remaining_seconds(timeout))
        if remaining <= 0:
            raise TimeoutError('执行等待超时')
        try:
            return future.result(timeout=min(.05,remaining))
        except FutureTimeout:
            if future.done():
                raise


class BoundedExecutor:
    """限制已提交工作；工作未结束前不释放槽，不接受无界排队。"""
    def __init__(self, max_workers, prefix):
        self.pool = ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix=prefix)
        self.slots = threading.BoundedSemaphore(max_workers)

    def submit(self, func, *args, **kwargs):
        if not self.slots.acquire(blocking=False):
            raise TimeoutError('执行器已满，请稍后重试')
        context = contextvars.copy_context()
        def run():
            return context.run(functools.partial(func,*args,**kwargs))
        try:
            future = self.pool.submit(run)
            future.add_done_callback(lambda _:self.slots.release())
            return future
        except BaseException:
            self.slots.release()
            raise

    def shutdown(self):
        self.pool.shutdown(wait=False,cancel_futures=True)


SPECIALIST_EXECUTOR = BoundedExecutor(12,'specialist')
DATA_EXECUTOR = BoundedExecutor(8,'market-data')
