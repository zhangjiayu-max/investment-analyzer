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
    # 注：此配置仅影响 _get_model_for_agent() 路径（DeepSeek 模式下的仲裁模型选择）。
    # 实际仲裁调用走 run_arbitration -> call_arbitration_llm -> ARBITRATION_MODEL 环境变量
    # （默认 deepseek-v4-pro，见 config.py），不经过 _get_model_for_agent。
    # 在 LLM_PROVIDER=mimo 模式下，此配置会被 _is_model_compatible 拒绝（装饰性），
    # 但硬约束"arbitrator 必须用 DeepSeek"仍通过环境变量满足。
    ('cost_routing.arbitrator_model', 'deepseek-v4-pro'),
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
