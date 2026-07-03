from db.config import get_config_int, get_config_float
"""公众号文章抓取模块 — 用 Playwright 异步抓取微信公众号文章

优化：
- 过滤带水印图片（URL含 watermark=1）
- 按图片key去重，避免重复下载
- 只保留无水印原图
"""

import os
import re
import time
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

import requests
from playwright.async_api import async_playwright


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://mp.weixin.qq.com/",
}


def _extract_image_key(url: str) -> str:
    """从微信图片URL提取唯一key（去掉查询参数差异）。"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _has_watermark(url: str) -> bool:
    """判断URL是否指向带水印的图片。"""
    return "watermark=1" in url


def _is_prefetch(url: str) -> bool:
    """判断是否为浏览器预加载图（带 wxfrom=12 等参数）。"""
    return "wxfrom=" in url and "from=appmsg" not in url


async def fetch_article(url: str) -> dict:
    """
    异步抓取微信公众号文章（使用 Playwright 渲染 JS）。

    返回:
        {
            "title": str,
            "author": str,
            "publish_time": str,
            "content_text": str,
            "content_html": str,
            "images": list[str],
            "image_count": int,
        }
    """
    async with async_playwright() as p:
        # 使用系统已安装的 Chrome，避免 playwright 下载浏览器失败
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        browser = await p.chromium.launch(
            headless=True,
            executable_path=chrome_path,
        )
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
            await page.wait_for_selector("h1#activity-name", timeout=25000)
            await page.wait_for_timeout(3000)
        except Exception:
            pass

        try:
            readmore = await page.query_selector("button:has-text('轻触阅读原文')")
            if readmore:
                await readmore.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                await readmore.click(force=True, timeout=5000)
                await page.wait_for_timeout(3000)
        except Exception:
            pass

        try:
            await page.wait_for_selector("#js_content, #js_image_content", timeout=15000)
        except Exception:
            pass

        try:
            await page.wait_for_selector('[data-src*="mmbiz"]', timeout=15000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        # 标题
        title = ""
        title_el = await page.query_selector("h1#activity-name") or await page.query_selector("h1")
        if title_el:
            title = (await title_el.inner_text()).strip()

        # 作者
        author = ""
        author_el = await page.query_selector("span.rich_media_meta_nickname a") or \
                    await page.query_selector("span.rich_media_meta_nickname")
        if author_el:
            author = (await author_el.inner_text()).strip()

        # 发布时间
        publish_time = ""
        em_el = await page.query_selector("em#publish_time")
        if em_el:
            publish_time = (await em_el.inner_text()).strip()
        if not publish_time:
            ct = await page.evaluate("() => { try { return ct } catch(e) { return '' } }")
            if ct and str(ct).isdigit():
                publish_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ct)))

        # 正文
        content_el = await page.query_selector("#js_content")
        content_html = ""
        content_text = ""
        if content_el:
            content_html = await content_el.inner_html()
            content_text = (await content_el.inner_text()).strip()
        else:
            desc_el = await page.query_selector("meta[name=description]")
            if desc_el:
                content_text = (await desc_el.get_attribute("content") or "").strip()

        # 图片提取（去重 + 过滤水印 + 过滤预加载）
        images = []
        seen_keys = set()

        # 1. <img> 标签
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

        # 2. 页面级 div[data-src*="mmbiz"]
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

    # 去掉第一张图（通常是封面/占位图）
    if len(images) > 1:
        images = images[1:]

    return {
        "title": title,
        "author": author,
        "publish_time": publish_time,
        "content_text": content_text,
        "content_html": content_html,
        "images": images,
        "image_count": len(images),
    }


async def download_images(image_urls: list, save_dir: str) -> list:
    """异步下载图片到本地。"""
    import aiohttp

    os.makedirs(save_dir, exist_ok=True)
    local_paths = []

    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(headers=HEADERS, connector=conn) as session:
        # 并发下载图片提升速度
        import asyncio

        async def _download_one(i: int, url: str) -> str | None:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning(f"图片 {i} HTTP {resp.status}: {url[:60]}")
                        return None
                    ct = resp.headers.get("Content-Type", "image/jpeg")
                    ext = ".jpg"
                    if "png" in ct:
                        ext = ".png"
                    elif "gif" in ct:
                        ext = ".gif"
                    elif "webp" in ct:
                        ext = ".webp"

                    filename = f"img_{i:03d}{ext}"
                    filepath = os.path.join(save_dir, filename)
                    data = await resp.read()
                    with open(filepath, "wb") as f:
                        f.write(data)
                    return filepath
            except Exception as e:
                logger.warning(f"下载失败 [{i}]: {e} ({url[:60]})")
                return None

        tasks = [_download_one(i, url) for i, url in enumerate(image_urls)]
        results = await asyncio.gather(*tasks)
        local_paths = [p for p in results if p is not None]

        logger.info(f"图片下载完成: {len(local_paths)}/{len(image_urls)}")
        return local_paths


def extract_stock_codes(text: str) -> list:
    """
    从文章文本中提取可能的股票/基金代码。
    """
    prefixed = re.findall(r"(?:sh|sz|SH|SZ)(\d{6})", text)
    standalone = re.findall(r"(?<!\d)(\d{6})(?!\d)", text)
    codes = []
    for code in set(prefixed + standalone):
        if code.startswith(("6", "0", "1", "3", "5")):
            codes.append(code)
    return sorted(set(codes))


def extract_article_structure(title: str, content: str) -> dict:
    """用 LLM 提取文章的结构化信息（核心观点/标的/操作建议/时效性/偏见）。

    替代纯正则提取，提供更深度的文章解读。供文章解读专家使用。

    返回:
        {
            "core_viewpoints": [{"point", "evidence"}],
            "mentioned_targets": [{"name", "code", "sentiment"}],
            "action_suggestions": [{"action", "target", "reason"}],
            "timeliness": str,
            "bias": str,
        }
    """
    import json as _json
    import logging
    from llm_service import _call_llm, MODEL

    prompt = f"""分析以下投资相关文章，提取结构化信息。

文章标题：{title}
文章正文（前 6000 字）：{content[:6000]}

请提取并输出 JSON（只输出 JSON，不要其他文字）：
{{
  "core_viewpoints": [
    {{"point": "核心观点1", "evidence": "原文支撑片段"}}
  ],
  "mentioned_targets": [
    {{"name": "标的名称", "code": "代码（如有，否则空）", "sentiment": "positive|negative|neutral"}}
  ],
  "action_suggestions": [
    {{"action": "加仓|减仓|观望|买入|卖出|关注", "target": "标的", "reason": "理由"}}
  ],
  "timeliness": "时效性评估（如'当前有效'、'已过时'、'长期参考'）",
  "bias": "潜在偏见分析（如'过度乐观'、'利益相关'、'客观中立'）"
}}

要求：
- 核心观点 2-5 个，必须有原文支撑
- 标的包括股票/基金/指数/行业，标注情感倾向
- 操作建议只提取作者明确提出的，不臆测
- 无相应内容则返回空数组"""

    try:
        response = _call_llm(
            caller="article_structure",
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是投资文章分析助手。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=get_config_float('llm.temperature_tool', 0.2),
            max_tokens=get_config_int('llm.max_tokens_tool', 1500),
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = _json.loads(raw)
        return {
            "core_viewpoints": result.get("core_viewpoints", []),
            "mentioned_targets": result.get("mentioned_targets", []),
            "action_suggestions": result.get("action_suggestions", []),
            "timeliness": result.get("timeliness", ""),
            "bias": result.get("bias", ""),
        }
    except Exception as e:
        logging.warning(f"文章结构化提取失败: {e}")
        return {
            "core_viewpoints": [],
            "mentioned_targets": [],
            "action_suggestions": [],
            "timeliness": "",
            "bias": "",
        }
