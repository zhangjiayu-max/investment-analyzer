"""批量处理：下载图片 → 分析 → 入库"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from openai import OpenAI

# 确保能找到项目 modules
_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_root, "backend"))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from article_reader import fetch_article, download_images
from config import get_vision_config
from db import init_db, save_valuation

ARTICLES_FILE = "doc/articles.json"
PROGRESS_FILE = "doc/processed_articles.json"
IMAGES_ROOT = Path("data/images")
MAX_ARTICLES = 5

api_key, base_url, model = get_vision_config()

PARSE_PROMPT = """提取这张指数估值图的数据。图片可能显示市盈率、市净率、市销率、市现率、股息率、风险溢价中的一种。

输出JSON（没有的字段设为null）：
{
  "指数名称": "...", "指数代码": "...",
  "当前点位": 12345.67, "涨跌幅": 1.23,
  "市盈率TTM统计指标": { "当前值": 13.96, "分位点": 3.27, "危险值": 21.83, "中位数": 18.88, "机会值": 16.07, "最大值": 35.86, "最小值": 13.23, "平均值": 19.43, "z分数": -1.28 },
  "市净率统计指标": { "当前值": 2.5, "分位点": 50.0, "危险值": 3.5, "中位数": 3.0, "机会值": 2.0, "最大值": 5.0, "最小值": 1.0, "平均值": 2.8, "z分数": 1.5 },
  "市销率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "市现率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "股息率统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null },
  "风险溢价统计指标": { "当前值": null, "分位点": null, "危险值": null, "中位数": null, "机会值": null, "最大值": null, "最小值": null, "平均值": null, "z分数": null }
}
只输出JSON。"""


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"processed_urls": [], "last_seq": 0}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def extract_date(title: str) -> str | None:
    m = re.search(r"(\d{4}[-.]\d{1,2}[-.]\d{1,2})", title)
    return m.group(1).replace(".", "-") if m else None


def safe_dirname(title: str, max_len: int = 20) -> str:
    name = re.sub(r"[^一-鿿\w-]", "", title)
    return name[:max_len].strip() or "article"


def analyze_image(img_path: str) -> dict:
    """调用 AI vision 模型分析估值图片。"""
    with open(img_path, "rb") as f:
        img_b64 = f.read()

    ext = img_path.rsplit(".", 1)[-1].lower()
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "jpeg")

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": PARSE_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{base64.b64encode(img_b64).decode()}"}},
            ],
        }],
        temperature=0.1,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw) if raw else {}


def normalize(data: dict) -> dict:
    """将 AI 输出标准化为 DB 字段。自动检测指标类型。"""
    # 按优先级检测各指标类型
    metric_configs = [
        ("市现率", "市现率统计指标"),
        ("市销率", "市销率统计指标"),
        ("市净率", "市净率统计指标"),
        ("股息率", "股息率统计指标"),
        ("风险溢价", "风险溢价统计指标"),
        ("市盈率", "市盈率TTM统计指标"),
    ]

    metric_type = "市盈率"
    s = data.get("市盈率TTM统计指标") or {}

    for mt_name, mt_key in metric_configs:
        stats = data.get(mt_key) or {}
        if stats.get("当前值") is not None:
            metric_type = mt_name
            s = stats
            break

    return {
        "index_code": data.get("指数代码"),
        "index_name": data.get("指数名称"),
        "current_point": data.get("当前点位"),
        "change_pct": data.get("涨跌幅"),
        "metric_type": metric_type,
        "current_value": s.get("当前值"),
        "percentile": s.get("分位点"),
        "danger_value": s.get("危险值"),
        "median": s.get("中位数"),
        "opportunity_value": s.get("机会值"),
        "max_value": s.get("最大值"),
        "min_value": s.get("最小值"),
        "avg_value": s.get("平均值"),
        "zscore": s.get("z分数"),
    }


async def process_article(article: dict) -> int:
    """处理一篇文章，返回入库图片数。"""
    seq, url, title = article["seq"], article["url"], article["title"]
    article_date = extract_date(title) or article.get("publish_time", "")[:10] or time.strftime("%Y-%m-%d")

    print(f'\n{"=" * 60}')
    print(f"[{seq}] {title}")
    print(f"  日期: {article_date}")

    # 1. 抓取文章 + 下载图片
    print(f"  抓取文章...", end=" ", flush=True)
    result = await fetch_article(url)
    images = result["images"]
    if not images:
        print(f"无图片")
        return 0
    print(f"{len(images)} 张图片")

    save_dir = IMAGES_ROOT / article_date / safe_dirname(title)
    save_dir.mkdir(parents=True, exist_ok=True)

    print(f"  下载图片...", end=" ", flush=True)
    local_paths = await download_images(images, str(save_dir))
    print(f"{len(local_paths)}/{len(images)} 张")

    if not local_paths:
        return 0

    # 2. 写 manifest.json
    manifest = {
        "url": url,
        "title": title,
        "article_date": article_date,
        "images": [
            {"index": i, "url": url, "local_path": str(lp)}
            for i, (url, lp) in enumerate(zip(images, local_paths))
        ],
    }
    manifest_path = save_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  manifest: {manifest_path}")

    # 3. 分析 + 入库
    total = len(local_paths)
    success = 0
    for idx, lp in enumerate(local_paths):
        fname = os.path.basename(lp)
        print(f"  [{idx + 1}/{total}] {fname} ...", end=" ", flush=True)

        try:
            raw = analyze_image(lp)
            if not raw or not raw.get("指数代码"):
                print("跳过（无数据）")
                continue

            norm = normalize(raw)
            vid = save_valuation(
                norm,
                source_image=str(lp),
                source_url=url,
                snapshot_date=article_date,
            )
            code = norm.get("index_code", "?")
            mt = norm.get("metric_type", "?")
            cv = norm.get("current_value", "?")
            print(f"OK → {code} ({mt}={cv})")
            success += 1
        except Exception as e:
            print(f"失败: {e}")

        time.sleep(0.3)

    print(f"  文章完成: {success}/{total} 张")
    return success


async def main():
    init_db()
    print("DB 初始化完成")

    with open(ARTICLES_FILE) as f:
        all_articles = json.load(f)

    progress = load_progress()
    print(f"已处理: {len(progress['processed_urls'])} 篇")

    pending = [a for a in all_articles if a["url"] not in progress["processed_urls"]]
    to_process = pending[:MAX_ARTICLES]

    if not to_process:
        print("没有待处理的文章")
        return

    print(f"本次处理: {len(to_process)} 篇\n")

    total = 0
    for article in to_process:
        try:
            total += await process_article(article)
            progress["processed_urls"].append(article["url"])
            progress["last_seq"] = article["seq"]
            save_progress(progress)
        except Exception as e:
            print(f"  出错: {e}")
            import traceback
            traceback.print_exc()

    print(f'\n{"=" * 60}')
    print(f"全部完成: {len(to_process)} 篇, {total} 张入库")
    print(f"进度: {PROGRESS_FILE}")


if __name__ == "__main__":
    import base64  # needed in analyze_image
    asyncio.run(main())
