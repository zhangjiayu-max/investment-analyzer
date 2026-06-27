"""螺丝钉图片解析后台任务。"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from config import DD_IMAGES_DIR, IMAGES_DIR, VALUATION_IMAGES_DIR
from db.dd_tasks import update_dd_parse_task, get_dd_parse_task
from db.valuations import save_dd_valuation
from db._conn import _get_conn
from image_parser import DDImageParser, ImageParser

logger = logging.getLogger(__name__)


def _resolve_image_path(path: str) -> Path:
    """解析图片路径，尝试多个基础目录。"""
    img_path = Path(path)
    if img_path.is_absolute():
        return img_path
    for base in [DD_IMAGES_DIR, IMAGES_DIR, VALUATION_IMAGES_DIR]:
        candidate = base / img_path
        if candidate.exists():
            return candidate
    return DD_IMAGES_DIR / path


async def run_dd_parse(task_id: int, image_path: str, parse_type: str = "dd"):
    """后台任务：解析图片，保存结果，更新任务状态。"""
    update_dd_parse_task(task_id, status="parsing")

    try:
        loop = asyncio.get_event_loop()
        trace_id = uuid.uuid4().hex[:12]

        if parse_type == "dd":
            parser = DDImageParser(trace_id=trace_id)
            result = await loop.run_in_executor(None, parser.parse, image_path)
        else:
            parser = ImageParser(trace_id=trace_id)
            result = await loop.run_in_executor(None, parser.parse, image_path)

        if parse_type == "dd" and result.get("ok"):
            # 保存到 dd_valuations 表
            img_path = Path(image_path)
            try:
                rel_path = f"data/dd_images/{img_path.relative_to(DD_IMAGES_DIR)}"
            except ValueError:
                rel_path = f"data/dd_images/{img_path.name}"
            image_url = f"/static/dd_images/{img_path.relative_to(DD_IMAGES_DIR)}"
            dd_id = save_dd_valuation(result, rel_path, image_url)
            result["dd_id"] = dd_id

            # 更新 analysis_records
            conn = _get_conn()
            existing = conn.execute(
                "SELECT id FROM analysis_records WHERE image_path = ?", (rel_path,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE analysis_records SET status='success', updated_at=datetime('now','localtime') WHERE id=?",
                    (existing[0],),
                )
            else:
                conn.execute(
                    "INSERT INTO analysis_records (image_path, image_url, status) VALUES (?, ?, 'success')",
                    (rel_path, image_url),
                )
            conn.commit()
            conn.close()

            update_dd_parse_task(
                task_id,
                status="done",
                result_json=result,
                dd_id=dd_id,
            )
        elif parse_type == "dd":
            update_dd_parse_task(
                task_id,
                status="error",
                error_msg=result.get("error", "解析失败"),
                result_json=result,
            )
        else:
            # single 类型
            update_dd_parse_task(
                task_id,
                status="done",
                result_json=result,
            )

        logger.info(f"DD 解析任务 {task_id} 完成: {image_path}")

    except Exception as e:
        logger.error(f"DD 解析任务 {task_id} 失败: {e}")
        update_dd_parse_task(task_id, status="error", error_msg=str(e))
