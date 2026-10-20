"""应用常驻后台任务注册；关闭时只取消自身任务，不触碰 ASGI 生命周期。"""
import asyncio

_tasks = set()


def create_background_task(coro):
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def cancel_background_tasks():
    tasks = [task for task in _tasks if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return len(tasks)
