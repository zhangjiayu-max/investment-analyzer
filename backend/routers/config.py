"""系统配置路由 — /api/system-config/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import list_configs, get_config, update_config, reset_configs

router = APIRouter(tags=["system-config"])


class UpdateConfigRequest(BaseModel):
    value: str


@router.get("/api/system-config")
async def list_configs_api(category: str = None):
    """获取所有系统配置（支持 category 过滤）。"""
    configs = list_configs(category)
    return {"configs": configs}


@router.get("/api/system-config/{key:path}")
async def get_config_api(key: str):
    """获取单个配置。"""
    val = get_config(key)
    if val == '' and key not in [c[0] for c in []]:
        # 检查 key 是否存在
        configs = list_configs()
        exists = any(c['key'] == key for c in configs)
        if not exists:
            raise HTTPException(404, f"配置项 '{key}' 不存在")
    return {"key": key, "value": val}


@router.put("/api/system-config/{key:path}")
async def update_config_api(key: str, req: UpdateConfigRequest):
    """更新单个配置。"""
    success = update_config(key, req.value)
    if not success:
        raise HTTPException(404, f"配置项 '{key}' 不存在")
    return {"ok": True, "key": key, "value": req.value}


@router.post("/api/system-config/reset")
async def reset_configs_api():
    """重置所有配置为默认值。"""
    count = reset_configs()
    return {"ok": True, "reset_count": count}
