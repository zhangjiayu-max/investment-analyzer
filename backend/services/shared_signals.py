"""跨页面共享信号聚合。

兼容层：对外继续提供 build_shared_signals，但内部改为复用统一证据层。
"""

from __future__ import annotations

from services.unified_evidence import build_unified_evidence


def build_shared_signals(user_id: str = "default", limit: int = 4) -> dict:
    """返回跨页面共享信号摘要。"""
    return build_unified_evidence(user_id=user_id, limit=limit)

