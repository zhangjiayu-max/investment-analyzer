"""文章路由 — /api/article/* (规范化版本)

路径规范：
  - /api/article/list                    - 文章列表
  - /api/article/create                  - 创建文章
  - /api/article/{article_id}            - 文章操作（获取/删除）
  - /api/article/{article_id}/download   - 下载文章
  - /api/article/{article_id}/analyze    - 分析文章
  - /api/article/author/*                - 作者文章
  - /api/article/linked/*                - 关联文章
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import (
    list_articles, get_article, create_article, delete_article,
    list_author_articles, get_author_article, delete_author_article,
    list_linked_articles, get_linked_article, delete_linked_article,
)

router = APIRouter(prefix="/api/article", tags=["article"])


class CreateArticleRequest(BaseModel):
    url: str
    title: Optional[str] = None


# ── 文章管理 ──────────────────────────────────────

@router.get("/list")
async def list_articles_api(limit: int = 50, status: str = None):
    """文章列表。"""
    return {"articles": list_articles(status)}


@router.post("/create")
async def create_article_api(req: CreateArticleRequest):
    """创建文章。"""
    article_id = create_article(req.url, req.title)
    return {"ok": True, "id": article_id}


@router.get("/{article_id}")
async def get_article_api(article_id: int):
    """获取文章详情（含分析记录）。"""
    from db import get_analysis_records
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    # 补充分析记录
    article["analysis_records"] = get_analysis_records(article_id)
    return article


@router.delete("/{article_id}")
async def delete_article_api(article_id: int):
    """删除文章。"""
    delete_article(article_id)
    return {"ok": True}


# ── 作者文章 ──────────────────────────────────────

@router.get("/author/list")
async def list_author_articles_api(limit: int = 50):
    """列出作者文章。"""
    return {"articles": list_author_articles(limit)}


@router.get("/author/{article_id}")
async def get_author_article_api(article_id: int):
    """获取作者文章详情。"""
    article = get_author_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    return article


@router.delete("/author/{article_id}")
async def delete_author_article_api(article_id: int):
    """删除作者文章。"""
    delete_author_article(article_id)
    return {"ok": True}


# ── 关联文章 ──────────────────────────────────────

@router.get("/linked/list")
async def list_linked_articles_api(limit: int = 50):
    """列出关联文章。"""
    return {"articles": list_linked_articles(limit)}


@router.get("/linked/{article_id}")
async def get_linked_article_api(article_id: int):
    """获取关联文章详情。"""
    article = get_linked_article(article_id)
    if not article:
        raise HTTPException(404, "文章不存在")
    return article


@router.delete("/linked/{article_id}")
async def delete_linked_article_api(article_id: int):
    """删除关联文章。"""
    delete_linked_article(article_id)
    return {"ok": True}
