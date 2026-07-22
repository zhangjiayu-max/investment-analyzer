"""主题规则管理路由 — /api/admin/theme-rules/*

O-2（2026-07-22）：将 opportunity_engine.py 硬编码 THEME_RULES 配置化，
支持后台动态增删改，DB 优先 + 硬编码兜底。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db.theme_rules import (
    list_theme_rules,
    get_theme_rule,
    create_theme_rule,
    update_theme_rule,
    delete_theme_rule,
)
from services.advisor.opportunity_engine import _invalidate_theme_rules_cache

router = APIRouter(tags=["theme-rules"])


def _invalidate_cache():
    """更新/删除/新增后清空 opportunity_engine 的主题规则缓存。"""
    try:
        _invalidate_theme_rules_cache()
    except Exception:
        pass  # 缓存失效失败不阻塞主流程


class CreateThemeRuleRequest(BaseModel):
    theme: str
    keywords: list[str] = []
    policy_terms: list[str] = []
    funds: list[dict] = []
    future_direction: str = ""
    index_code: str = ""
    sector: str = ""
    priority: int = 100


class UpdateThemeRuleRequest(BaseModel):
    keywords: list[str] | None = None
    policy_terms: list[str] | None = None
    funds: list[dict] | None = None
    future_direction: str | None = None
    index_code: str | None = None
    sector: str | None = None
    active: int | None = None
    priority: int | None = None


@router.get("/api/admin/theme-rules")
async def list_theme_rules_api(active_only: bool = False):
    """列出所有主题规则。active_only=true 时只返回启用的。"""
    rules = list_theme_rules(active_only=active_only)
    return {"rules": rules, "total": len(rules)}


@router.get("/api/admin/theme-rules/{theme}")
async def get_theme_rule_api(theme: str):
    """获取单个主题规则。"""
    rule = get_theme_rule(theme)
    if not rule:
        raise HTTPException(404, f"主题 '{theme}' 不存在")
    return rule


@router.post("/api/admin/theme-rules")
async def create_theme_rule_api(req: CreateThemeRuleRequest):
    """新增主题规则。"""
    try:
        rule_id = create_theme_rule(
            theme=req.theme,
            keywords=req.keywords,
            policy_terms=req.policy_terms,
            funds=req.funds,
            future_direction=req.future_direction,
            index_code=req.index_code,
            sector=req.sector,
            priority=req.priority,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    _invalidate_cache()
    return {"ok": True, "id": rule_id, "theme": req.theme}


@router.put("/api/admin/theme-rules/{theme}")
async def update_theme_rule_api(theme: str, req: UpdateThemeRuleRequest):
    """更新主题规则（部分字段）。"""
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "未提供任何更新字段")
    ok = update_theme_rule(theme, **fields)
    if not ok:
        raise HTTPException(404, f"主题 '{theme}' 不存在")
    _invalidate_cache()
    return {"ok": True, "theme": theme, "updated_fields": list(fields.keys())}


@router.delete("/api/admin/theme-rules/{theme}")
async def delete_theme_rule_api(theme: str, soft: bool = True):
    """删除主题规则。soft=true（默认）时软删除（active=0）。"""
    ok = delete_theme_rule(theme, soft=soft)
    if not ok:
        raise HTTPException(404, f"主题 '{theme}' 不存在")
    _invalidate_cache()
    return {"ok": True, "theme": theme, "soft_delete": soft}
