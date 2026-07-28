"""P5-19 专家权重调整管理路由 — /api/admin/specialist-weights/*

查看所有专家的权重调整状态，手动重置降权专家。
"""

import logging

from fastapi import APIRouter, HTTPException

from db.specialist_weight import (
    get_all_weight_adjustments,
    reset_weight,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["specialist-weights"])


@router.get("/api/admin/specialist-weights")
async def get_specialist_weights_api():
    """查看所有专家权重调整状态。

    返回按权重升序排列的专家列表（低权重的排前面，便于关注）。
    """
    weights = get_all_weight_adjustments()
    return {
        "weights": weights,
        "total": len(weights),
        "demoted_count": sum(1 for w in weights if w.get("auto_demoted") == 1),
    }


@router.post("/api/admin/specialist-weights/{agent_key}/reset")
async def reset_specialist_weight_api(agent_key: str):
    """手动重置指定专家的权重为 1.0。

    清零累计低分次数和连续高分计数，标记为手动重置。
    """
    if not agent_key:
        raise HTTPException(400, "agent_key 不能为空")

    try:
        reset_weight(agent_key)
        return {"ok": True, "agent_key": agent_key, "weight_multiplier": 1.0}
    except Exception as e:
        logger.error(f"重置专家 {agent_key} 权重失败: {e}")
        raise HTTPException(500, f"重置失败: {e}")
