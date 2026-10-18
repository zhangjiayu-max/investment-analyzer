"""估值图片路径与静态地址转换，兼容上传路径和数据库相对路径。"""

from pathlib import Path

import config


def _image_roots():
    return {
        "images": config.IMAGES_DIR,
        "dd_images": config.DD_IMAGES_DIR,
        "liuyi_images": config.LIUYI_IMAGES_DIR,
        "valuation_images": config.VALUATION_IMAGES_DIR,
    }


def resolve_valuation_image(path: str, preferred: str = "images") -> Path:
    image = Path(path)
    if image.is_absolute():
        return image
    roots = _image_roots()
    # 数据库存储 data/<目录>/<文件>，必须保留目录含义，避免同名图片串源。
    for name, root in roots.items():
        prefix = f"data/{name}/"
        if path.startswith(prefix):
            return root / path[len(prefix):]
    for root in [roots[preferred], *[p for key, p in roots.items() if key != preferred]]:
        candidate = root / image
        if candidate.is_file():
            return candidate
    return roots[preferred] / image


def valuation_image_location(image: Path) -> tuple[str, str | None]:
    """按图片实际目录生成数据库路径和可访问的静态地址。"""
    for name, root in _image_roots().items():
        try:
            relative = image.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        return f"data/{name}/{relative}", f"/static/{name}/{relative}"
    return str(image), None
