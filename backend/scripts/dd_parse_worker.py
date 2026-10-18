"""螺丝钉图片解析后台任务。"""

import asyncio
import logging
import uuid
from pathlib import Path

from db.dd_tasks import update_dd_parse_task
from db.valuations import save_dd_valuation, save_liuyi_valuation
from services.valuation.image_paths import resolve_valuation_image, valuation_image_location
from services.image_parser import DDImageParser, LiuyiImageParser, ImageParser

logger = logging.getLogger(__name__)


def _resolve_image_path(path: str) -> Path:
    """解析图片路径，尝试多个基础目录。"""
    return resolve_valuation_image(path, "dd_images")


async def run_dd_parse(task_id: int, image_path: str, parse_type: str = "dd"):
    """后台任务：解析图片，保存结果，更新任务状态。"""
    update_dd_parse_task(task_id, status="parsing")

    try:
        loop = asyncio.get_event_loop()
        trace_id = uuid.uuid4().hex[:12]

        if parse_type == "dd":
            parser = DDImageParser(trace_id=trace_id)
            result = await loop.run_in_executor(None, parser.parse, image_path)
        elif parse_type == "liuyi":
            parser = LiuyiImageParser(trace_id=trace_id)
            result = await loop.run_in_executor(None, parser.parse, image_path)
        else:
            parser = ImageParser(trace_id=trace_id)
            result = await loop.run_in_executor(None, parser.parse, image_path)

        if parse_type == "dd" and result.get("ok"):
            # 保存到 dd_valuations 表
            rel_path, image_url = valuation_image_location(Path(image_path))
            dd_id = save_dd_valuation(result, rel_path, image_url)
            result["dd_id"] = dd_id

            update_dd_parse_task(
                task_id,
                status="done",
                result_json=result,
                dd_id=dd_id,
            )
        elif parse_type == "liuyi" and result.get("ok"):
            # 保存到 liuyi_valuations 表
            rel_path, image_url = valuation_image_location(Path(image_path))
            liuyi_id = save_liuyi_valuation(result, rel_path, image_url)
            result["liuyi_id"] = liuyi_id

            # 六亿估值不写入 analysis_records（那是雷牛牛单指数估值的表）
            update_dd_parse_task(
                task_id,
                status="done",
                result_json=result,
                dd_id=liuyi_id,  # 复用 dd_id 字段存储 liuyi_id
            )
        elif parse_type in ("dd", "liuyi"):
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

        logger.info(f"DD/六亿 解析任务 {task_id} 完成: {image_path}")

    except Exception as e:
        logger.error(f"DD/六亿 解析任务 {task_id} 失败: {e}")
        update_dd_parse_task(task_id, status="error", error_msg=str(e))
