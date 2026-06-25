"""Shadow Mode 模块

在生产环境静默运行候选 Prompt，不影响用户体验，自动评分对比。

数据流：
1. 用户请求 → 当前 Prompt 执行 → 返回结果给用户
2. 同时 → 候选 Prompt 静默执行 → 记录结果 → LLM-as-Judge 评分 → 存入 shadow_runs
3. Dashboard 对比：Shadow vs Production 的分数分布

表结构：
- shadow_configs: 配置（哪个 agent/prompt 类型，候选 prompt 内容）
- shadow_runs: 每次执行记录
"""

import json
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)


def init_shadow_db():
    """初始化 Shadow Mode 数据库表。"""
    from db._conn import _get_conn

    conn = _get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            current_prompt TEXT NOT NULL,
            candidate_prompt TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            traffic_pct REAL DEFAULT 0.1,
            prompt_version_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 兼容已有数据库：如果列不存在则添加
    try:
        conn.execute("ALTER TABLE shadow_configs ADD COLUMN prompt_version_id INTEGER")
    except Exception:
        pass  # 列已存在

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_id INTEGER REFERENCES shadow_configs(id),
            agent_type TEXT NOT NULL,
            input_data TEXT,
            production_output TEXT,
            shadow_output TEXT,
            production_score REAL,
            shadow_score REAL,
            score_reason TEXT,
            duration_ms INTEGER DEFAULT 0,
            token_usage INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_runs_config ON shadow_runs(config_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_runs_type ON shadow_runs(agent_type)")

    conn.commit()
    conn.close()


def create_shadow_config(name: str, agent_type: str, current_prompt: str,
                         candidate_prompt: str, traffic_pct: float = 0.1,
                         prompt_version_id: int = None) -> int:
    """创建 Shadow 配置。"""
    from db._conn import _get_conn

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO shadow_configs (name, agent_type, current_prompt, candidate_prompt, traffic_pct, prompt_version_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, agent_type, current_prompt, candidate_prompt, traffic_pct, prompt_version_id))
    config_id = cur.lastrowid
    conn.commit()
    conn.close()
    return config_id


def list_shadow_configs(active_only: bool = True) -> list:
    """列出 Shadow 配置。"""
    from db._conn import _get_conn

    conn = _get_conn()
    where = "WHERE is_active = 1" if active_only else ""
    rows = conn.execute(f"""
        SELECT sc.*,
               COUNT(sr.id) as run_count,
               AVG(sr.production_score) as avg_prod_score,
               AVG(sr.shadow_score) as avg_shadow_score
        FROM shadow_configs sc
        LEFT JOIN shadow_runs sr ON sc.id = sr.config_id
        {where}
        GROUP BY sc.id
        ORDER BY sc.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_shadow_runs(config_id: int = None, limit: int = 100) -> list:
    """列出 Shadow 执行记录。"""
    from db._conn import _get_conn

    conn = _get_conn()
    if config_id:
        rows = conn.execute("""
            SELECT * FROM shadow_runs WHERE config_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (config_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM shadow_runs ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_shadow_stats(config_id: int = None) -> dict:
    """获取 Shadow Mode 统计信息。"""
    from db._conn import _get_conn

    conn = _get_conn()

    if config_id:
        where = "WHERE config_id = ?"
        params = (config_id,)
    else:
        where = ""
        params = ()

    rows = conn.execute(f"""
        SELECT
            COUNT(*) as total_runs,
            AVG(production_score) as avg_prod_score,
            AVG(shadow_score) as avg_shadow_score,
            AVG(CASE WHEN shadow_score > production_score THEN 1 ELSE 0 END) as shadow_win_rate,
            AVG(duration_ms) as avg_duration_ms,
            SUM(token_usage) as total_tokens
        FROM shadow_runs {where}
    """, params).fetchone()

    result = dict(rows) if rows else {}

    # 分数分布
    rows2 = conn.execute(f"""
        SELECT
            CASE
                WHEN shadow_score > production_score + 1 THEN 'shadow_better'
                WHEN production_score > shadow_score + 1 THEN 'production_better'
                ELSE 'similar'
            END as comparison,
            COUNT(*) as cnt
        FROM shadow_runs {where}
        GROUP BY comparison
    """, params).fetchall()

    result["comparison"] = {r["comparison"]: r["cnt"] for r in rows2}

    conn.close()
    return result


async def run_shadow(config_id: int, input_data: dict, production_output: str,
                     agent_type: str = "") -> dict | None:
    """执行一次 Shadow 运行。

    在后台静默执行候选 Prompt，不阻塞主流程。
    """
    from db._conn import _get_conn

    conn = _get_conn()
    row = conn.execute("SELECT * FROM shadow_configs WHERE id = ? AND is_active = 1",
                       (config_id,)).fetchone()
    conn.close()

    if not row:
        return None

    config = dict(row)

    try:
        start_time = time.time()

        # 用候选 prompt 执行
        from llm_service import _call_llm
        candidate_output = _call_llm(
            caller=f"shadow_{config['agent_type']}",
            model=None,
            messages=[
                {"role": "system", "content": config["candidate_prompt"]},
                {"role": "user", "content": json.dumps(input_data, ensure_ascii=False)[:2000]},
            ],
            temperature=0.3,
            max_tokens=3000,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        # LLM-as-Judge 评分对比
        scores = await _compare_outputs(
            input_data, production_output, candidate_output, config["agent_type"]
        )

        # 写入记录
        conn = _get_conn()
        conn.execute("""
            INSERT INTO shadow_runs
            (config_id, agent_type, input_data, production_output, shadow_output,
             production_score, shadow_score, score_reason, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            config_id, config["agent_type"],
            json.dumps(input_data, ensure_ascii=False)[:2000],
            production_output[:3000],
            candidate_output[:3000],
            scores.get("production_score", 0),
            scores.get("shadow_score", 0),
            scores.get("reason", ""),
            duration_ms,
        ))
        conn.commit()
        conn.close()

        return {
            "production_score": scores.get("production_score", 0),
            "shadow_score": scores.get("shadow_score", 0),
            "reason": scores.get("reason", ""),
            "duration_ms": duration_ms,
        }

    except Exception as e:
        logger.error(f"Shadow 执行失败 (config={config_id}): {e}")
        return None


async def _compare_outputs(input_data: dict, prod_output: str, shadow_output: str,
                           agent_type: str) -> dict:
    """用 LLM-as-Judge 对比两个输出的质量。"""
    from llm_service import _call_llm

    prompt = f"""你是投资分析质量评审专家。请对比以下两个分析输出的质量。

## 用户输入
{json.dumps(input_data, ensure_ascii=False)[:500]}

## 当前版本输出（Production）
{prod_output[:1500]}

## 候选版本输出（Shadow）
{shadow_output[:1500]}

## 评分维度（每项 1-10 分）
1. 数据准确性：引用的数据是否正确、最新
2. 逻辑推理：分析逻辑是否清晰、无矛盾
3. 可操作性：建议是否具体、可执行
4. 风险提示：是否充分提示风险

请严格按 JSON 格式返回：
```json
{{
  "production_score": 7.5,
  "shadow_score": 8.0,
  "production_breakdown": {{"data": 8, "logic": 7, "action": 7, "risk": 7}},
  "shadow_breakdown": {{"data": 8, "logic": 8, "action": 8, "risk": 8}},
  "reason": "候选版本在逻辑推理和可操作性上更优"
}}
```
只返回 JSON。"""

    try:
        response = _call_llm(
            caller="shadow_judge",
            model=None,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
        )

        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        start = response.find('{')
        end = response.rfind('}')
        if start != -1 and end != -1:
            return json.loads(response[start:end + 1])

    except Exception as e:
        logger.error(f"Shadow 评分失败: {e}")

    return {"production_score": 5, "shadow_score": 5, "reason": "评分失败"}


def toggle_shadow_config(config_id: int, is_active: bool) -> bool:
    """启用/禁用 Shadow 配置。"""
    from db._conn import _get_conn

    conn = _get_conn()
    conn.execute("UPDATE shadow_configs SET is_active = ? WHERE id = ?",
                 (1 if is_active else 0, config_id))
    conn.commit()
    conn.close()
    return True


def delete_shadow_config(config_id: int) -> bool:
    """删除 Shadow 配置。"""
    from db._conn import _get_conn

    conn = _get_conn()
    conn.execute("DELETE FROM shadow_runs WHERE config_id = ?", (config_id,))
    conn.execute("DELETE FROM shadow_configs WHERE id = ?", (config_id,))
    conn.commit()
    conn.close()
    return True


# ═══════════════════════════════════════════════════════════════
# 胶水4: Shadow 自动晋升
# ═══════════════════════════════════════════════════════════════


async def auto_promote_shadows() -> list[dict]:
    """自动检查所有活跃 shadow config，符合条件的自动晋升。

    晋升条件：
    - 至少 30 次对比（足够样本量）
    - shadow 胜率 > 60%
    - shadow 平均分 > production 平均分 + 0.5
    """
    from db.eval import activate_prompt_version

    configs = list_shadow_configs(active_only=True)
    promoted = []

    for cfg in configs:
        stats = get_shadow_stats(cfg["id"])

        total_runs = stats.get("total_runs", 0)
        if total_runs < 30:
            continue

        win_rate = stats.get("shadow_win_rate", 0) or 0
        avg_prod = stats.get("avg_prod_score", 0) or 0
        avg_shadow = stats.get("avg_shadow_score", 0) or 0

        # 检查晋升条件
        if win_rate > 0.6 and (avg_shadow - avg_prod) > 0.5:
            prompt_version_id = cfg.get("prompt_version_id")
            if not prompt_version_id:
                logger.warning(
                    f"Shadow #{cfg['id']} 符合晋升条件但无 prompt_version_id，跳过"
                )
                continue

            # 激活对应的 prompt 版本
            try:
                activate_prompt_version(prompt_version_id, cfg["agent_type"])
            except Exception as e:
                logger.error(f"激活 prompt 版本失败: {e}")
                continue

            # 停用 shadow config
            toggle_shadow_config(cfg["id"], is_active=False)

            logger.info(
                f"Shadow 自动晋升: {cfg['agent_type']} #{cfg['id']} "
                f"({total_runs} runs, win_rate={win_rate:.1%}, "
                f"shadow={avg_shadow:.1f} > prod={avg_prod:.1f})"
            )

            promoted.append({
                "config_id": cfg["id"],
                "agent_type": cfg["agent_type"],
                "runs": total_runs,
                "win_rate": round(win_rate, 3),
                "shadow_score": round(avg_shadow, 2),
                "prod_score": round(avg_prod, 2),
                "action": "promoted",
            })

    return promoted
