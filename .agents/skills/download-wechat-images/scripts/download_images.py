"""公众号图片下载工具 — 独立脚本，支持 manifest 去重"""

import asyncio
import json
import os
import re
import sys
import ssl
from datetime import datetime
from pathlib import Path

from urllib.parse import urlparse

import aiohttp
from playwright.async_api import async_playwright


def _extract_image_key(url: str) -> str:
    """从微信图片URL提取唯一key（去掉查询参数差异）。"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _has_watermark(url: str) -> bool:
    """判断URL是否指向带水印的图片。"""
    return "watermark=1" in url


def _is_prefetch(url: str) -> bool:
    """判断是否为浏览器预加载图。"""
    return "wxfrom=" in url and "from=appmsg" not in url


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

MANIFEST_FILENAME = "manifest.json"


def load_manifest(manifest_path: str) -> dict:
    """加载已有的 manifest.json，不存在则返回空结构。"""
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"url": "", "title": "", "fetched_at": "", "images": []}


def save_manifest(manifest_path: str, manifest: dict):
    """保存 manifest.json。"""
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def extract_date_from_title(title: str) -> str | None:
    """从标题中提取日期，返回 YYYY-MM-DD 格式。

    支持常见格式：
    - 2026-05-23 / 2026.05.23 / 2026/05/23
    - 20260523
    - 5月23日 / 05月23日（默认当前年）
    """
    # 完整日期：2026-05-23 / 2026.05.23 / 2026/05/23
    m = re.search(r'(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})', title)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 紧凑格式：20260523
    m = re.search(r'(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])', title)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 中文格式：5月23日（默认当前年）
    m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', title)
    if m:
        year = datetime.now().year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    return None


async def fetch_article(url: str) -> dict:
    """异步抓取微信公众号文章，返回标题、正文、图片列表。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector("h1, #js_content", timeout=25000)
            await page.wait_for_timeout(3000)
        except Exception:
            pass

        # 展开全文
        try:
            readmore = await page.query_selector("button:has-text('轻触阅读原文')")
            if readmore:
                await readmore.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                await readmore.click(force=True, timeout=5000)
                await page.wait_for_timeout(3000)
        except Exception:
            pass

        # 等待懒加载图片
        try:
            await page.wait_for_selector('[data-src*="mmbiz"]', timeout=15000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        title = ""
        title_el = await page.query_selector("h1#activity-name") or await page.query_selector("h1")
        if title_el:
            title = (await title_el.inner_text()).strip()

        content_el = await page.query_selector("#js_content")
        content_text = ""
        if content_el:
            content_text = (await content_el.inner_text()).strip()

        # 图片提取（去重 + 过滤水印 + 过滤预加载）
        images = []
        seen_keys = set()

        # 1. #js_content 内的 <img>（文本文章）
        if content_el:
            img_els = await content_el.query_selector_all("img")
            for img in img_els:
                alt = await img.get_attribute("alt") or ""
                if "赞赏" in alt or "二维码" in alt:
                    continue
                src = await img.get_attribute("data-src") or await img.get_attribute("src") or ""
                if not src or src.startswith("data:"):
                    continue
                if _has_watermark(src) or _is_prefetch(src):
                    continue
                key = _extract_image_key(src)
                if key in seen_keys:
                    continue
                images.append(src)
                seen_keys.add(key)

        # 2. 页面级 div[data-src*="mmbiz"]（图片轮播，含懒加载）
        div_imgs = await page.query_selector_all('[data-src*="mmbiz"]')
        for div in div_imgs:
            src = await div.get_attribute("data-src") or ""
            if not src or not src.startswith("http"):
                continue
            if _has_watermark(src) or _is_prefetch(src):
                continue
            key = _extract_image_key(src)
            if key in seen_keys:
                continue
            images.append(src)
            seen_keys.add(key)

        await browser.close()

    # 去掉第一张封面图
    if len(images) > 1:
        images = images[1:]

    return {"title": title, "content_text": content_text, "images": images}


async def download_single(session, url: str, save_dir: str, index: int) -> tuple[str, str] | None:
    """下载单张图片，返回 (url, local_path) 或 None。"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            ct = resp.headers.get("Content-Type", "image/jpeg")
            ext = ".jpg"
            if "png" in ct:
                ext = ".png"
            elif "gif" in ct:
                ext = ".gif"
            elif "webp" in ct:
                ext = ".webp"

            filename = f"img_{index:03d}{ext}"
            filepath = os.path.join(save_dir, filename)
            data = await resp.read()
            with open(filepath, "wb") as f:
                f.write(data)
            return (url, filepath)
    except Exception as e:
        print(f"  下载失败 [{index}]: {e}")
        return None


async def main(url: str, output_dir: str = None):
    """主流程：抓取文章 → 去重 → 下载新图片 → 更新 manifest。"""
    print(f"正在抓取: {url}")

    article = await fetch_article(url)
    title = article["title"]
    all_images = article["images"]

    if not all_images:
        print("未找到图片")
        return

    print(f"找到 {len(all_images)} 张图片")

    # 提取标题中的日期
    article_date = extract_date_from_title(title)

    # 确定保存目录
    if output_dir:
        save_dir = Path(output_dir)
    else:
        today = article_date or datetime.now().strftime("%Y-%m-%d")
        safe_title = "".join(c for c in title[:20] if c.isalnum() or c in " _-").strip()
        if not safe_title:
            safe_title = "article"
        save_dir = Path("data") / "images" / today / safe_title

    save_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = str(save_dir / MANIFEST_FILENAME)
    manifest = load_manifest(manifest_path)

    manifest["url"] = url
    manifest["title"] = title
    manifest["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    if article_date:
        manifest["article_date"] = article_date

    # 去重：分离已下载和待下载
    existing_map = {}
    for img in manifest.get("images", []):
        if img.get("local_path") and os.path.exists(img["local_path"]):
            existing_map[img["url"]] = img

    to_download = []
    skipped = 0
    for img_url in all_images:
        if img_url in existing_map:
            skipped += 1
        else:
            to_download.append(img_url)

    if skipped:
        print(f"已存在 {skipped} 张，跳过")

    if not to_download:
        print("所有图片已下载，无需重复拉取")
    else:
        print(f"需要下载 {len(to_download)} 张新图片")

    # 下载新图片
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)

    new_entries = []
    if to_download:
        max_idx = -1
        for img in manifest.get("images", []):
            lp = img.get("local_path", "")
            m = re.search(r'img_(\d+)\.', lp)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
        start_idx = max_idx + 1

        async with aiohttp.ClientSession(headers=HEADERS, connector=conn) as session:
            tasks = []
            for i, img_url in enumerate(to_download):
                tasks.append(download_single(session, img_url, str(save_dir), start_idx + i))
            results = await asyncio.gather(*tasks)

        for result in results:
            if result:
                img_url, local_path = result
                entry = {
                    "url": img_url,
                    "local_path": local_path,
                    "analyzed": False,
                }
                new_entries.append(entry)
                print(f"  已下载: {os.path.basename(local_path)}")

    # 合并
    url_to_entry = {}
    for img in manifest.get("images", []):
        url_to_entry[img["url"]] = img
    for entry in new_entries:
        url_to_entry[entry["url"]] = entry

    manifest["images"] = []
    for img_url in all_images:
        if img_url in url_to_entry:
            manifest["images"].append(url_to_entry[img_url])

    save_manifest(manifest_path, manifest)

    total = len(manifest["images"])
    downloaded = len(new_entries)
    print(f"\n完成: 共 {total} 张图片（本次下载 {downloaded} 张，跳过 {skipped} 张）")
    print(f"文章标题: {title}")
    if article_date:
        print(f"文章日期: {article_date}")
    print(f"保存位置: {save_dir}")
    print(f"manifest: {manifest_path}")

    return {
        "title": title,
        "article_date": article_date,
        "image_count": total,
        "save_dir": str(save_dir),
        "manifest": manifest_path,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python download_images.py <公众号链接> [保存目录]")
        sys.exit(1)

    url = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(main(url, output_dir))
