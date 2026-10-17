from datetime import datetime, timedelta

from db import (
    cleanup_stale_async_tasks,
    create_async_task,
    get_latest_async_task,
    get_running_async_task,
    update_async_task,
)
from db._conn import _get_conn


def test_stale_running_task_is_marked_error(tmp_db):
    task_id = create_async_task("daily_report", caller="test")
    old = (datetime.now() - timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.execute("UPDATE async_tasks SET created_at = ?, updated_at = ? WHERE id = ?", (old, old, task_id))
    conn.commit()
    conn.close()

    assert get_running_async_task("daily_report") is None
    latest = get_latest_async_task("daily_report")
    assert latest["status"] == "error"
    assert "自动标记失败" in latest["error_msg"]


def test_recent_running_task_remains_running(tmp_db):
    task_id = create_async_task("hotspots_analysis", caller="test")

    assert cleanup_stale_async_tasks() == 0
    running = get_running_async_task("hotspots_analysis")
    assert running["id"] == task_id

    update_async_task(task_id, status="done", result={"ok": True})
