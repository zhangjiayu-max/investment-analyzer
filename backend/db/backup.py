"""SQLite 自动备份 — 每日收盘后增量备份数据库。"""

import os
import shutil
import sqlite3
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backups")
MAX_BACKUPS = 30  # 保留最近 30 天


def backup_database(db_path: str | None = None) -> str | None:
    """备份数据库到 backups/ 目录。

    使用 SQLite 的 .backup() API，保证一致性快照。
    返回备份文件路径，失败返回 None。
    """
    if db_path is None:
        from db._conn import DB_PATH
        db_path = str(DB_PATH)

    if not os.path.exists(db_path):
        logger.warning(f"数据库不存在: {db_path}")
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_name = os.path.splitext(os.path.basename(db_path))[0]
    backup_path = os.path.join(BACKUP_DIR, f"{db_name}_{timestamp}.db")

    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()
        logger.info(f"数据库备份完成: {backup_path} ({os.path.getsize(backup_path) / 1024:.0f} KB)")

        # 清理旧备份
        _cleanup_old_backups(db_name)
        return backup_path
    except Exception as e:
        logger.error(f"数据库备份失败: {e}")
        return None


def _cleanup_old_backups(db_name: str):
    """删除超过 MAX_BACKUPS 的旧备份。"""
    try:
        files = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.startswith(db_name) and f.endswith(".db")],
        )
        while len(files) > MAX_BACKUPS:
            old = files.pop(0)
            os.remove(os.path.join(BACKUP_DIR, old))
            logger.info(f"清理旧备份: {old}")
    except Exception as e:
        logger.warning(f"清理旧备份失败: {e}")


def list_backups(db_name: str = "valuations") -> list[dict]:
    """列出所有备份。"""
    if not os.path.exists(BACKUP_DIR):
        return []
    result = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if f.startswith(db_name) and f.endswith(".db"):
            path = os.path.join(BACKUP_DIR, f)
            result.append({
                "file": f,
                "size_kb": round(os.path.getsize(path) / 1024, 1),
                "created_at": datetime.fromtimestamp(os.path.getctime(path)).isoformat(),
            })
    return result
