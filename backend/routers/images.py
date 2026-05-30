"""图片管理路由 — /api/dd-images/*, /api/valuation-images/*

两类图片：
  - dd-images: 螺丝钉估值图片
  - valuation-images: 用户上传的估值截图
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from config import DD_IMAGES_DIR, IMAGES_DIR
from db._conn import _get_conn

router = APIRouter(tags=["images"])


# ══════════════════════════════════════════════════════
# 螺丝钉估值图片 API
# ══════════════════════════════════════════════════════

@router.post("/api/dd-images/upload")
async def upload_dd_image(file: UploadFile):
    """上传螺丝钉估值图片，按日期目录存储。"""
    ext = Path(file.filename).suffix.lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
        raise HTTPException(status_code=400, detail=f"不支持的图片类型: {ext}")

    from datetime import datetime
    date_dir = datetime.now().strftime("%Y-%m-%d")
    save_dir = DD_IMAGES_DIR / date_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    # 用时间戳命名避免冲突
    ts = datetime.now().strftime("%H%M%S")
    safe_name = f"{ts}_{file.filename}"
    save_path = save_dir / safe_name
    content = await file.read()
    save_path.write_bytes(content)

    relative_path = f"{date_dir}/{safe_name}"
    return {"ok": True, "path": relative_path, "url": f"/static/dd_images/{relative_path}"}


@router.get("/api/dd-images")
async def list_dd_images(date: str = None):
    """列出螺丝钉估值图片，可按日期筛选。已解析的图片会标注 parsed=true。"""
    # 查询已解析的 DD 图片路径
    conn = _get_conn()
    parsed_paths = set()
    for row in conn.execute("SELECT image_path FROM dd_valuations").fetchall():
        parsed_paths.add(row[0])
    conn.close()

    images = []
    if date:
        date_dir = DD_IMAGES_DIR / date
        if date_dir.is_dir():
            for f in sorted(date_dir.iterdir()):
                if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                    rel_path = f"data/dd_images/{date}/{f.name}"
                    images.append({
                        "name": f.name,
                        "date": date,
                        "url": f"/static/dd_images/{date}/{f.name}",
                        "path": f"{date}/{f.name}",
                        "parsed": rel_path in parsed_paths,
                    })
    else:
        # 列出所有日期目录下的图片
        for d in sorted(DD_IMAGES_DIR.iterdir(), reverse=True):
            if d.is_dir() and len(d.name) == 10 and d.name[4] == '-' and d.name[7] == '-':
                for f in sorted(d.iterdir()):
                    if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                        rel_path = f"data/dd_images/{d.name}/{f.name}"
                        images.append({
                            "name": f.name,
                            "date": d.name,
                            "url": f"/static/dd_images/{d.name}/{f.name}",
                            "path": f"{d.name}/{f.name}",
                            "parsed": rel_path in parsed_paths,
                        })
    return {"images": images}


@router.get("/api/dd-images/dates")
async def list_dd_image_dates():
    """列出所有有图片的日期。"""
    dates = []
    if DD_IMAGES_DIR.is_dir():
        for d in sorted(DD_IMAGES_DIR.iterdir(), reverse=True):
            if d.is_dir() and len(d.name) == 10 and d.name[4] == '-' and d.name[7] == '-':
                count = sum(1 for f in d.iterdir() if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'))
                if count > 0:
                    dates.append({"date": d.name, "count": count})
    return {"dates": dates}


@router.delete("/api/dd-images/{path:path}")
async def delete_dd_image(path: str):
    """删除螺丝钉估值图片。path 格式: YYYY-MM-DD/filename.ext"""
    file_path = DD_IMAGES_DIR / path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    # 安全检查：确保路径在 DD_IMAGES_DIR 下
    if not str(file_path.resolve()).startswith(str(DD_IMAGES_DIR.resolve())):
        raise HTTPException(status_code=400, detail="无效路径")
    file_path.unlink()
    # 如果日期目录为空则删除
    parent = file_path.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    return {"ok": True}


# ══════════════════════════════════════════════════════
# 估值图片 API（用户上传的估值截图，与螺丝钉估值分开）
# ══════════════════════════════════════════════════════

@router.post("/api/valuation-images/upload")
async def upload_valuation_image(file: UploadFile):
    """上传估值图片，存到 data/images/ 日期目录下。"""
    ext = Path(file.filename).suffix.lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
        raise HTTPException(status_code=400, detail=f"不支持的图片类型: {ext}")

    from datetime import datetime
    date_dir = datetime.now().strftime("%Y-%m-%d")
    save_dir = IMAGES_DIR / date_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%H%M%S")
    safe_name = f"{ts}_{file.filename}"
    save_path = save_dir / safe_name
    content = await file.read()
    save_path.write_bytes(content)

    relative_path = f"{date_dir}/{safe_name}"
    return {"ok": True, "path": relative_path, "url": f"/static/images/{relative_path}"}


@router.get("/api/valuation-images")
async def list_valuation_images(date: str = None):
    """列出待解析的估值图片（仅列出未被 analysis_records 关联的图片）。"""
    images = []
    conn = _get_conn()
    # 获取已关联 analysis_records 的图片路径
    known = set()
    for row in conn.execute("SELECT image_path FROM analysis_records WHERE image_path LIKE 'data/images/%'").fetchall():
        known.add(row[0])
    conn.close()

    def _scan_dir(base_dir: Path, date_filter: str = None):
        if date_filter:
            dirs = [base_dir / date_filter] if (base_dir / date_filter).is_dir() else []
        else:
            dirs = sorted(
                [d for d in base_dir.iterdir() if d.is_dir() and len(d.name) == 10 and d.name[4] == '-' and d.name[7] == '-'],
                reverse=True
            )
        for d in dirs:
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                    rel = f"data/images/{d.name}/{f.name}"
                    if rel not in known:
                        images.append({
                            "name": f.name,
                            "date": d.name,
                            "url": f"/static/images/{d.name}/{f.name}",
                            "path": f"{d.name}/{f.name}",
                        })

    _scan_dir(IMAGES_DIR, date)
    return {"images": images}


@router.get("/api/valuation-images/dates")
async def list_valuation_image_dates():
    """列出有待解析图片的日期。"""
    conn = _get_conn()
    known = set()
    for row in conn.execute("SELECT image_path FROM analysis_records WHERE image_path LIKE 'data/images/%'").fetchall():
        known.add(row[0])
    conn.close()

    dates = []
    if IMAGES_DIR.is_dir():
        for d in sorted(IMAGES_DIR.iterdir(), reverse=True):
            if d.is_dir() and len(d.name) == 10 and d.name[4] == '-' and d.name[7] == '-':
                count = 0
                for f in d.iterdir():
                    if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                        rel = f"data/images/{d.name}/{f.name}"
                        if rel not in known:
                            count += 1
                if count > 0:
                    dates.append({"date": d.name, "count": count})
    return {"dates": dates}


@router.delete("/api/valuation-images/{path:path}")
async def delete_valuation_image(path: str):
    """删除估值图片。path 格式: YYYY-MM-DD/filename.ext"""
    file_path = IMAGES_DIR / path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not str(file_path.resolve()).startswith(str(IMAGES_DIR.resolve())):
        raise HTTPException(status_code=400, detail="无效路径")
    file_path.unlink()
    parent = file_path.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    return {"ok": True}
