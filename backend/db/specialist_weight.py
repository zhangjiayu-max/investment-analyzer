"""专家权重调整管理 — P5-19 评估反哺专家权重

评估低分专家累计 N 次后，路由时降权（带恢复机制）。

规则：
- 低分阈值：eval.low_score_threshold（默认 60）
- 降权阈值次数：eval.demotion_threshold（默认 3）
- 每次降权：weight_multiplier *= 0.7（eval.demotion_factor）
- 最低权重：0.3（eval.min_weight，低于此值不再降）
- 自动恢复：连续 N 次高分(>=80)后恢复权重 * 1.3（上限 1.0）
"""

import logging
from datetime import datetime

from db._conn import _get_conn
from db.config import (
    get_config_bool,
    get_config_float,
    get_config_int,
)

logger = logging.getLogger(__name__)

# 默认参数（与 system_config 默认值保持一致）
_DEFAULT_LOW_SCORE_THRESHOLD = 60.0
_DEFAULT_DEMOTION_THRESHOLD = 3
_DEFAULT_DEMOTION_FACTOR = 0.7
_DEFAULT_MIN_WEIGHT = 0.3
_DEFAULT_RECOVERY_HIGH_SCORE = 80.0
_DEFAULT_RECOVERY_CONSECUTIVE_COUNT = 2
_DEFAULT_RECOVERY_FACTOR = 1.3


def init_specialist_weight_table(conn):
    """初始化专家权重调整表（在 init_db 中调用）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS specialist_weight_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_key TEXT NOT NULL UNIQUE,
            weight_multiplier REAL DEFAULT 1.0,
            low_score_count INTEGER DEFAULT 0,
            high_score_streak INTEGER DEFAULT 0,
            last_low_score_at TEXT,
            last_adjusted_at TEXT,
            auto_demoted INTEGER DEFAULT 0,
            notes TEXT,
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_specialist_weight_key "
        "ON specialist_weight_adjustments(agent_key)"
    )


def _is_enabled() -> bool:
    """检查权重反馈总开关是否启用。"""
    return get_config_bool("eval.weight_feedback_enabled", True)


def get_specialist_weight(agent_key: str) -> float:
    """获取专家权重乘数（默认 1.0）。

    供路由器在路由时读取，决定是否降低该专家的优先级。
    """
    if not _is_enabled():
        return 1.0
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT weight_multiplier FROM specialist_weight_adjustments WHERE agent_key = ?",
            (agent_key,),
        ).fetchone()
        return float(row["weight_multiplier"]) if row else 1.0
    except Exception:
        return 1.0
    finally:
        conn.close()


def adjust_weight_on_low_score(agent_key: str, score: float) -> None:
    """评估低分时累计计数，达到阈值自动降权。

    - 低分阈值：eval.low_score_threshold（默认 60）
    - 降权阈值次数：eval.demotion_threshold（默认 3）
    - 每次降权：weight_multiplier *= 0.7
    - 最低权重：0.3（低于此值不再降）
    """
    if not _is_enabled():
        return

    threshold = get_config_float(
        "eval.low_score_threshold", _DEFAULT_LOW_SCORE_THRESHOLD
    )
    if score >= threshold:
        return  # 非低分，不累计

    demotion_threshold = get_config_int(
        "eval.demotion_threshold", _DEFAULT_DEMOTION_THRESHOLD
    )
    demotion_factor = get_config_float(
        "eval.demotion_factor", _DEFAULT_DEMOTION_FACTOR
    )
    min_weight = get_config_float("eval.min_weight", _DEFAULT_MIN_WEIGHT)

    conn = _get_conn()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id, weight_multiplier, low_score_count "
            "FROM specialist_weight_adjustments WHERE agent_key = ?",
            (agent_key,),
        ).fetchone()

        if row:
            new_count = int(row["low_score_count"]) + 1
            current_weight = float(row["weight_multiplier"])

            # 达到降权阈值次数且权重未到最低 → 降权
            if new_count >= demotion_threshold and current_weight > min_weight:
                new_weight = max(min_weight, round(current_weight * demotion_factor, 4))
                conn.execute(
                    """UPDATE specialist_weight_adjustments
                       SET low_score_count = ?, last_low_score_at = ?,
                           weight_multiplier = ?, last_adjusted_at = ?,
                           auto_demoted = 1, high_score_streak = 0,
                           updated_at = ?
                       WHERE agent_key = ?""",
                    (new_count, now, new_weight, now, now, agent_key),
                )
                logger.info(
                    f"[P5-19] 专家 {agent_key} 低分累计 {new_count} 次，"
                    f"权重 {current_weight:.2f} → {new_weight:.2f}"
                )
            else:
                # 仅累计次数，不降权
                conn.execute(
                    """UPDATE specialist_weight_adjustments
                       SET low_score_count = ?, last_low_score_at = ?,
                           high_score_streak = 0, updated_at = ?
                       WHERE agent_key = ?""",
                    (new_count, now, now, agent_key),
                )
                logger.debug(
                    f"[P5-19] 专家 {agent_key} 低分累计 {new_count}/{demotion_threshold}"
                )
        else:
            # 首次记录
            conn.execute(
                """INSERT INTO specialist_weight_adjustments
                   (agent_key, weight_multiplier, low_score_count,
                    high_score_streak, last_low_score_at, auto_demoted, updated_at)
                   VALUES (?, 1.0, 1, 0, ?, 0, ?)""",
                (agent_key, now, now),
            )
            logger.debug(f"[P5-19] 专家 {agent_key} 首次低分记录 (score={score:.0f})")

        conn.commit()
    except Exception as e:
        logger.error(f"[P5-19] adjust_weight_on_low_score 失败 ({agent_key}): {e}")
    finally:
        conn.close()


def recover_weight_on_high_score(agent_key: str, score: float) -> None:
    """评估高分时恢复权重。

    自动恢复：连续 N 次高分(>=80)后恢复权重 * 1.3（上限 1.0）。
    """
    if not _is_enabled():
        return

    recovery_score = get_config_float(
        "eval.recovery_high_score", _DEFAULT_RECOVERY_HIGH_SCORE
    )
    if score < recovery_score:
        return  # 非高分，不累计

    recovery_count = get_config_int(
        "eval.recovery_consecutive_count", _DEFAULT_RECOVERY_CONSECUTIVE_COUNT
    )
    recovery_factor = get_config_float(
        "eval.recovery_factor", _DEFAULT_RECOVERY_FACTOR
    )

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, weight_multiplier, high_score_streak "
            "FROM specialist_weight_adjustments WHERE agent_key = ?",
            (agent_key,),
        ).fetchone()

        if not row:
            return  # 无降权记录，无需恢复

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_streak = int(row["high_score_streak"] or 0)
        new_streak = current_streak + 1
        current_weight = float(row["weight_multiplier"])

        if new_streak >= recovery_count:
            if current_weight < 1.0:
                new_weight = min(1.0, round(current_weight * recovery_factor, 4))
                conn.execute(
                    """UPDATE specialist_weight_adjustments
                       SET weight_multiplier = ?, high_score_streak = 0,
                           last_adjusted_at = ?, auto_demoted = 0, updated_at = ?
                       WHERE agent_key = ?""",
                    (new_weight, now, now, agent_key),
                )
                logger.info(
                    f"[P5-19] 专家 {agent_key} 连续 {new_streak} 次高分，"
                    f"权重恢复 {current_weight:.2f} → {new_weight:.2f}"
                )
            else:
                # 权重已是 1.0，仅重置计数
                conn.execute(
                    """UPDATE specialist_weight_adjustments
                       SET high_score_streak = 0, updated_at = ?
                       WHERE agent_key = ?""",
                    (now, agent_key),
                )
        else:
            conn.execute(
                """UPDATE specialist_weight_adjustments
                   SET high_score_streak = ?, updated_at = ?
                   WHERE agent_key = ?""",
                (new_streak, now, agent_key),
            )
            logger.debug(
                f"[P5-19] 专家 {agent_key} 高分连续 {new_streak}/{recovery_count} 次"
            )

        conn.commit()
    except Exception as e:
        logger.error(f"[P5-19] recover_weight_on_high_score 失败 ({agent_key}): {e}")
    finally:
        conn.close()


def get_all_weight_adjustments() -> list[dict]:
    """获取所有专家权重调整（供路由器和前端使用）。

    按权重升序排列（低权重的专家排前面，便于关注）。
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM specialist_weight_adjustments
               ORDER BY weight_multiplier ASC, low_score_count DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def reset_weight(agent_key: str) -> None:
    """手动重置专家权重（前端管理操作）。

    将权重恢复为 1.0，清零累计计数。
    """
    conn = _get_conn()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO specialist_weight_adjustments
               (agent_key, weight_multiplier, low_score_count, high_score_streak,
                last_low_score_at, last_adjusted_at, auto_demoted, notes, updated_at)
               VALUES (?, 1.0, 0, 0, NULL, ?, 0, 'manual reset', ?)
               ON CONFLICT(agent_key) DO UPDATE SET
                   weight_multiplier = 1.0,
                   low_score_count = 0,
                   high_score_streak = 0,
                   last_low_score_at = NULL,
                   last_adjusted_at = excluded.last_adjusted_at,
                   auto_demoted = 0,
                   notes = 'manual reset',
                   updated_at = excluded.updated_at""",
            (agent_key, now, now),
        )
        conn.commit()
        logger.info(f"[P5-19] 专家 {agent_key} 权重已手动重置为 1.0")
    finally:
        conn.close()
