"""akshare 调用统计 API — 暴露成功率与失败分类给前端。

G-akshare-stats（2026-07-24）：解决"成功率无统计、失败降级不知情"问题。
"""
from fastapi import APIRouter, Query

from infra.akshare_stats import get_stats, reset

router = APIRouter()


@router.get("/api/admin/akshare-stats")
def get_akshare_stats(
    window: int = Query(3600, ge=60, le=86400, description="统计窗口秒数，默认 1h，最大 24h"),
):
    """获取 akshare 调用统计。

    返回最近 window 秒内：
    - 整体成功率
    - 按 akshare 函数分组的成功/超时/失败/反爬次数
    - 最近一次失败的错误信息

    用途：定位 conv#136 等场景下"akshare 持续降级"的根因接口。
    """
    return get_stats(window_seconds=window)


@router.post("/api/admin/akshare-stats/reset")
def reset_akshare_stats():
    """重置 akshare 统计（运维排障后清空计数）。"""
    reset()
    return {"ok": True, "message": "akshare 统计已清零"}
