"""公众号图片下载工具 — 下载文章中的所有图片到本地"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))
from article_reader import fetch_article, download_images


# 图片存储根目录
IMAGES_ROOT = Path(__file__).parent / "data" / "images"


async def download_article_images(url: str) -> dict:
    """
    下载公众号文章中的所有图片。

    参数:
        url: 公众号文章链接

    返回:
        {
            "title": 文章标题,
            "date": 日期目录,
            "image_count": 图片数量,
            "save_dir": 保存目录,
            "images": [图片路径列表]
        }
    """
    print(f"正在抓取文章: {url}")

    # 1. 抓取文章
    article = await fetch_article(url)
    title = article["title"]
    images = article["images"]

    if not images:
        print("未找到图片")
        return {"title": title, "date": "", "image_count": 0, "save_dir": "", "images": []}

    print(f"找到 {len(images)} 张图片")

    # 2. 创建日期目录
    today = datetime.now().strftime("%Y-%m-%d")
    # 用标题前20个字符作为子目录（去除特殊字符）
    safe_title = "".join(c for c in title[:20] if c.isalnum() or c in " _-").strip()
    if not safe_title:
        safe_title = "article"

    save_dir = IMAGES_ROOT / today / safe_title
    save_dir.mkdir(parents=True, exist_ok=True)

    print(f"保存目录: {save_dir}")

    # 3. 下载图片
    local_paths = await download_images(images, str(save_dir))

    print(f"下载完成: {len(local_paths)}/{len(images)} 张")

    return {
        "title": title,
        "date": today,
        "image_count": len(local_paths),
        "save_dir": str(save_dir),
        "images": local_paths,
    }


def main():
    """命令行入口。"""
    if len(sys.argv) < 2:
        print("用法: python download_images.py <公众号文章链接>")
        print("示例: python download_images.py https://mp.weixin.qq.com/s/xxx")
        sys.exit(1)

    url = sys.argv[1]

    if "mp.weixin.qq.com" not in url and "weixin.qq.com" not in url:
        print("警告: 链接可能不是微信公众号文章")

    result = asyncio.run(download_article_images(url))

    print("\n" + "=" * 50)
    print(f"文章标题: {result['title']}")
    print(f"日期目录: {result['date']}")
    print(f"图片数量: {result['image_count']}")
    print(f"保存位置: {result['save_dir']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
