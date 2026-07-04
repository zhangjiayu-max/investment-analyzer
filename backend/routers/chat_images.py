"""对话图片上传路由 — /api/chat-images/upload"""

import logging
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from config import ROOT

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat-images"])

CHAT_IMAGES_DIR = ROOT / "data" / "chat_images"

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


class ImageUploadResponse(BaseModel):
    image_id: str
    url: str
    parse_status: str  # "success" | "failed"
    parse_result: dict | None = None


@router.post("/api/chat-images/upload", response_model=ImageUploadResponse)
async def upload_chat_image(file: UploadFile = File(...)):
    """上传对话图片，立即解析，返回解析结果。"""

    # 1. 校验扩展名
    filename = file.filename or "unknown.jpg"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的图片格式: .{ext}，仅支持 jpg/jpeg/png/webp/gif")

    # 2. 读取并校验大小
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"图片大小超过限制: {len(content) / 1024 / 1024:.1f}MB > 10MB")

    # 3. 保存到 data/chat_images/YYYY-MM-DD/{uuid}.{ext}
    today = date.today().isoformat()
    day_dir = CHAT_IMAGES_DIR / today
    day_dir.mkdir(parents=True, exist_ok=True)

    image_id = str(uuid.uuid4())[:12]
    saved_name = f"{image_id}.{ext}"
    saved_path = day_dir / saved_name

    with open(saved_path, "wb") as f:
        f.write(content)

    # 4. 立即调用 analyze_image 解析
    parse_status = "success"
    parse_result = None
    try:
        from services.llm_service import analyze_image
        parse_result = analyze_image(str(saved_path))
        if parse_result and parse_result.get("error"):
            parse_status = "failed"
    except Exception as e:
        logger.warning(f"图片解析失败 {saved_path}: {e}")
        parse_status = "failed"
        parse_result = {"error": str(e)}

    # 5. 构造访问 URL
    url = f"/static/chat_images/{today}/{saved_name}"

    return ImageUploadResponse(
        image_id=image_id,
        url=url,
        parse_status=parse_status,
        parse_result=parse_result,
    )
