"""估值图片解析服务。"""

import logging
import re
from datetime import date
from pathlib import Path

from config import IMAGES_DIR, VALUATION_IMAGES_DIR, DD_IMAGES_DIR

logger = logging.getLogger(__name__)
from db._conn import _get_conn
from db.valuations import save_valuation, save_dd_valuation
from image_parser import ImageParser, DDImageParser


def parse_single_valuation(path: str, model_type: str = "mimo", source_url: str | None = None, snapshot_date: str | None = None) -> dict:
    """解析单张估值图片并存储结果（同步函数，供并发调用）。自动识别图片类型。"""
    img_path = Path(path)
    if not img_path.is_absolute():
        for base in [IMAGES_DIR, VALUATION_IMAGES_DIR, DD_IMAGES_DIR]:
            candidate = base / img_path
            if candidate.exists():
                img_path = candidate
                break
        else:
            img_path = IMAGES_DIR / path
    if not path or not img_path.exists():
        return {"ok": False, "error": f"图片路径无效: {img_path}", "path": path}

    # 自动判断图片类型：如果路径包含 dd_images，使用 DDImageParser
    is_dd_image = 'dd_images' in str(img_path)

    if is_dd_image:
        # 螺丝钉估值表解析
        dd_parser = DDImageParser(model_type=model_type)
        result = dd_parser.parse(str(img_path))
        result["source_path"] = str(img_path)

        if result.get("ok"):
            # 保存到 dd_valuations 表
            try:
                rel_path = f"data/dd_images/{img_path.relative_to(DD_IMAGES_DIR)}"
            except ValueError:
                rel_path = f"data/dd_images/{path}"
            image_url = f"/static/dd_images/{img_path.relative_to(DD_IMAGES_DIR)}"
            dd_id = save_dd_valuation(result, rel_path, image_url)
            result["dd_id"] = dd_id
            result["type"] = "dd"

            # 更新 analysis_records
            conn = _get_conn()
            existing = conn.execute("SELECT id FROM analysis_records WHERE image_path = ?", (rel_path,)).fetchone()
            if existing:
                conn.execute("""UPDATE analysis_records SET status='success', updated_at=datetime('now','localtime') WHERE id=?""", (existing[0],))
            else:
                conn.execute("""INSERT INTO analysis_records (image_path, image_url, status) VALUES (?, ?, 'success')""",
                             (rel_path, image_url))
            conn.commit()
            conn.close()

        return {"ok": result.get("ok", False), "data": result, "path": path}
    else:
        # 普通估值图片解析
        parser = ImageParser(model_type=model_type)
        result = parser.parse(str(img_path))

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
            else:
                img_path = new_path
            try:
                new_rel_path = str(img_path.relative_to(IMAGES_DIR))
            except ValueError:
                new_rel_path = None

        valuation_id = save_valuation(result, source_image=str(img_path), source_url=source_url, snapshot_date=snapshot_date)
        result["id"] = valuation_id
        result["type"] = "valuation"

        # 自动触发 RAG 索引（增量索引）
        try:
            from rag import index_valuation
            index_valuation(
                result.get("index_code", ""),
                result.get("index_name", ""),
                result
            )
            logger.info(f"已索引估值数据到 RAG: {result.get('index_name', '')}")
        except Exception as e:
            logger.warning(f"索引估值数据失败: {e}")

        # 创建/更新 analysis_record
        image_rel = new_rel_path or path
        image_full_path = f"data/images/{image_rel}" if not image_rel.startswith("data/") else image_rel
        snap_date = snapshot_date or date.today().isoformat()
        conn = _get_conn()
        existing = conn.execute("SELECT id FROM analysis_records WHERE image_path = ?", (image_full_path,)).fetchone()
        if existing:
            conn.execute("""UPDATE analysis_records SET
                index_name=?, index_code=?, metric_type=?, status='success',
                updated_at=datetime('now','localtime') WHERE id=?""",
                (result.get("index_name"), result.get("index_code"), result.get("metric_type"), existing[0]))
        else:
            conn.execute("""INSERT INTO analysis_records
                (article_id, image_index, image_path, image_url, index_name, index_code, metric_type, status)
                VALUES (NULL, 0, ?, ?, ?, ?, ?, 'success')""",
                (image_full_path, f"/static/images/{image_rel}",
                 result.get("index_name"), result.get("index_code"), result.get("metric_type")))
        conn.commit()
        conn.close()

        result["new_path"] = new_rel_path
        result["new_name"] = img_path.name if new_rel_path else None
        return {"ok": True, "data": result, "path": path}
