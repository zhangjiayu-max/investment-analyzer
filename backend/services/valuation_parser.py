"""估值图片解析服务。"""

import re
from datetime import date
from pathlib import Path

from config import IMAGES_DIR, VALUATION_IMAGES_DIR, DD_IMAGES_DIR
from db._conn import _get_conn
from db.valuations import save_valuation
from image_parser import ImageParser


def parse_single_valuation(path: str, model_type: str = "mimo", source_url: str | None = None, snapshot_date: str | None = None) -> dict:
    """解析单张估值图片并存储结果（同步函数，供并发调用）。"""
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

    parser = ImageParser(model_type=model_type)
    result = parser.parse(str(img_path))

    valuation_id = save_valuation(result, source_image=str(img_path), source_url=source_url, snapshot_date=snapshot_date)
    result["id"] = valuation_id

    # 重命名为指数名_指标类型
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
