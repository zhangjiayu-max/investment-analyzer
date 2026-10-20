"""用独立 OCR 抄录表头八位日期，避免大模型格式化时丢失数字。"""

import io
import logging
import re
import shutil
import subprocess
from datetime import datetime

from PIL import Image

logger = logging.getLogger(__name__)


def read_table_header_date(image_path: str) -> str | None:
    """只接受表头中唯一、日历有效的 YYYYMMDD；不可核实时不猜日期。"""
    executable = shutil.which('tesseract')
    if not executable:
        logger.warning('表头日期未核验：未安装 tesseract')
        return None
    try:
        with Image.open(image_path) as image:
            height = min(image.height, max(80, min(320, int(image.width * 0.3))))
            with image.crop((0, 0, image.width, height)).convert('RGB') as header:
                scale = min(3, max(1, 1200 / header.width))
                with header.resize((round(header.width * scale), round(header.height * scale)),
                                   Image.Resampling.LANCZOS) as enlarged:
                    buffer = io.BytesIO()
                    enlarged.save(buffer, format='PNG')
        # stdin 避免临时文件及 macOS /tmp 符号链接导致的 Leptonica 路径问题。
        process = subprocess.run(
            [executable, 'stdin', 'stdout', '-l', 'eng', '--psm', '11'],
            input=buffer.getvalue(), capture_output=True, timeout=10, check=True,
        )
        candidates = set()
        for digits in re.findall(r'(?<!\d)(20\d{6})(?!\d)', process.stdout.decode('utf-8', errors='replace')):
            try:
                datetime.strptime(digits, '%Y%m%d')
            except ValueError:
                continue
            candidates.add(digits)
        if len(candidates) == 1:
            return candidates.pop()
        logger.warning('表头日期未核验：未识别到唯一有效的八位日期')
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning('表头日期 OCR 不可用：%s', exc)
    return None
