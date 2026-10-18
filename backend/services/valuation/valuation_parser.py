"""估值图片解析服务。"""

import logging
import math
import re
import uuid

from config import IMAGES_DIR, IMAGE_PARSER_MODEL_TYPE

logger = logging.getLogger(__name__)
from db._conn import _get_conn
from db.valuations import save_valuation, save_dd_valuation
from services.image_parser import ImageParser, DDImageParser
from services.valuation.image_paths import resolve_valuation_image, valuation_image_location


def parse_single_valuation(path: str, model_type: str = "", source_url: str | None = None, snapshot_date: str | None = None) -> dict:
    """解析单张估值图片并存储结果（同步函数，供并发调用）。自动识别图片类型。"""
    model_type = model_type or IMAGE_PARSER_MODEL_TYPE
    trace_id = uuid.uuid4().hex[:12]
    img_path = resolve_valuation_image(path)
    if not path or not img_path.is_file():
        return {"ok": False, "error": f"图片路径无效: {img_path}", "path": path}

    # 自动判断图片类型：如果路径包含 dd_images，使用 DDImageParser
    is_dd_image = 'dd_images' in str(img_path)
    # 六亿估值图片不应走普通估值流程，避免写入 analysis_records 污染雷牛牛 Tab
    is_liuyi_image = 'liuyi_images' in str(img_path)

    if is_liuyi_image:
        return {"ok": False, "error": "六亿估值图片请通过六亿估值上传入口处理", "path": path}

    if is_dd_image:
        # 螺丝钉估值表解析
        dd_parser = DDImageParser(model_type=model_type, trace_id=trace_id)
        result = dd_parser.parse(str(img_path))
        result["source_path"] = str(img_path)

        if result.get("ok"):
            rel_path, image_url = valuation_image_location(img_path)
            dd_id = save_dd_valuation(result, rel_path, image_url)
            result["dd_id"] = dd_id
            result["type"] = "dd"

        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "估值表解析失败"), "data": result, "path": path, "trace_id": trace_id}
        return {"ok": result.get("ok", False), "data": result, "path": path, "trace_id": trace_id}
    else:
        # 普通估值图片解析
        parser = ImageParser(model_type=model_type, trace_id=trace_id)
        result = parser.parse(str(img_path))
        if not isinstance(result, dict):
            return {"ok": False, "error": "未识别到有效的指数及估值数据", "path": path, "trace_id": trace_id}
        index_code = str(result.get("index_code") or "").strip()
        index_name = str(result.get("index_name") or "").strip()
        value = result.get("current_value")
        valid_value = value is None
        try:
            if value is not None:
                value = float(str(value).strip().replace(",", "").removesuffix("%").strip())
                valid_value = math.isfinite(value)
        except (TypeError, ValueError):
            valid_value = False
        if result.get("ok") is False or not index_code or index_code.upper() == "UNKNOWN" or not index_name or not valid_value:
            return {"ok": False, "error": result.get("error") or "未识别到有效的指数及估值数据", "data": result, "path": path, "trace_id": trace_id}
        result["index_code"] = index_code
        result["index_name"] = index_name
        result["current_value"] = value
        original_image_path, _ = valuation_image_location(img_path)

        # 先重命名为指数名_指标类型，再存 DB（确保 source_image 指向最终路径）
        index_name = result.get("index_name", "")
        metric_type = result.get("metric_type", "")
        new_rel_path = None
        if index_name:
            ext = img_path.suffix
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', f"{index_name}_{metric_type}{ext}")
            new_path = img_path.parent / safe_name
            if not new_path.exists():
                img_path.rename(new_path)
                img_path = new_path
            # 同名目标已存在时保留本次图片，防止把新估值指向旧图片。
            try:
                new_rel_path = str(img_path.relative_to(IMAGES_DIR))
            except ValueError:
                new_rel_path = None

        valuation_id = save_valuation(result, source_image=str(img_path), source_url=source_url, snapshot_date=snapshot_date)
        result["id"] = valuation_id
        result["type"] = "valuation"

        # 自动触发 RAG 索引（增量索引）
        try:
            from services.rag import index_valuation
            index_valuation(
                result.get("index_code", ""),
                result.get("index_name", ""),
                result
            )
            logger.info(f"已索引估值数据到 RAG: {result.get('index_name', '')}")
        except Exception as e:
            logger.warning(f"索引估值数据失败: {e}")

        # 创建/更新 analysis_record
        image_full_path, image_url = valuation_image_location(img_path)
        conn = _get_conn()
        existing = conn.execute(
            "SELECT id FROM analysis_records WHERE image_path IN (?, ?) ORDER BY id LIMIT 1",
            (original_image_path, image_full_path),
        ).fetchone()
        if existing:
            conn.execute("""UPDATE analysis_records SET
                image_path=?, image_url=?, index_name=?, index_code=?, metric_type=?, status='success',
                updated_at=datetime('now','localtime') WHERE id=?""",
                (image_full_path, image_url, result.get("index_name"), result.get("index_code"), result.get("metric_type"), existing[0]))
        else:
            conn.execute("""INSERT INTO analysis_records
                (article_id, image_index, image_path, image_url, index_name, index_code, metric_type, status)
                VALUES (NULL, 0, ?, ?, ?, ?, ?, 'success')""",
                (image_full_path, image_url,
                 result.get("index_name"), result.get("index_code"), result.get("metric_type")))
        conn.commit()
        conn.close()

        result["new_path"] = new_rel_path
        result["new_name"] = img_path.name if new_rel_path else None
        return {"ok": True, "data": result, "path": path, "trace_id": trace_id}
