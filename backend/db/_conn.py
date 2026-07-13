"""数据库连接管理 — 全局唯一的 DB 入口。

═══════════════════════════════════════════════════════════════
数据库使用规范（所有模块必须遵守）：
═══════════════════════════════════════════════════════════════

1. 数据库文件路径：
   - 默认: backend/data/valuations.db
   - 可通过环境变量 DB_PATH 覆盖（绝对路径）
   - 所有模块统一从 `from db._conn import DB_PATH` 获取，禁止独立定义

2. 获取连接：
   - ✅ 正确: `from db._conn import _get_conn; conn = _get_conn()`
   - ❌ 错误: `sqlite3.connect('data/valuations.db')` — 绕过了 WAL 配置
   - ❌ 错误: 在模块中独立定义 `DB_PATH = ...` — 路径可能不一致

3. WAL 模式说明：
   - 数据库使用 WAL (Write-Ahead Logging) 模式
   - 写入先进 WAL 文件，再 checkpoint 到主数据库文件
   - 直接用新 sqlite 连接查询主文件可能看不到 WAL 中未 checkpoint 的数据
   - 使用 _get_conn() 可正确读取 WAL 数据（自动恢复 journal_mode）

4. 脚本/调试查询：
   - 脚本中同样使用 `from db._conn import _get_conn`
   - 临时调试也应用 _get_conn()，避免查不到最新数据
═══════════════════════════════════════════════════════════════
"""

import json
import os
import sqlite3
from pathlib import Path

# 全局唯一数据库路径定义
# 1. 优先环境变量 DB_PATH（绝对路径）
# 2. 默认: backend/data/valuations.db
_ENV_DB_PATH = os.getenv("DB_PATH")
if _ENV_DB_PATH:
    DB_PATH = Path(_ENV_DB_PATH)
else:
    DB_PATH = Path(__file__).parent.parent.parent / "data" / "valuations.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接 — 所有模块统一使用此函数。

    自动配置：
    - WAL 模式：允许读写并发，新连接可正确读取 WAL 中已提交的数据
    - busy_timeout=30s：由 connect(timeout=30) 设置，友好处理锁竞争
    - row_factory=Row：支持按列名访问
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 2026-07-13 性能优化：timeout=30 已等价于 PRAGMA busy_timeout=30000，
    # 不再需要单独执行 PRAGMA busy_timeout（省一条 SQL）
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    # 启用 WAL 模式：允许读写并发，减少锁等待
    # 新连接打开时会自动恢复 WAL，确保能读到 WAL 中已提交的数据
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("codes_found", "market_data", "local_images"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
