# backend/scripts/sync_config.py
"""强制更新陈旧的 system_config 值（INSERT OR IGNORE 不会更新已有 key）。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db._conn import _get_conn

# 需要强制更新的配置（key, value）
FORCE_UPDATE = [
    ('agent.reuse_recent_conclusions', 'true'),
    ('agent.reuse_conclusions_hours', '48'),
    ('agent.conflict_detect_cache', 'true'),
    ('agent.cross_review_opinion_mode', 'true'),
    ('agent.react_tool_result_max_chars', '1500'),
    ('agent.react_compress_history', 'true'),
]

def main():
    conn = _get_conn()
    for key, value in FORCE_UPDATE:
        cur = conn.execute("UPDATE system_config SET value = ? WHERE key = ?", (value, key))
        if cur.rowcount > 0:
            print(f"✅ 已更新 {key} = {value}")
        else:
            print(f"⚠️  {key} 不存在（将在 init_default_configs 时创建）")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
