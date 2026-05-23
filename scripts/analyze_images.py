"""图片批量分析 — 读取 manifest.json，调用视觉模型识别估值数据，结果存回 manifest 并可选入库

用法:
    python analyze_images.py <manifest.json或图片目录>
    python analyze_images.py <manifest.json> --save        # 分析后自动入库
    python analyze_images.py <manifest.json> --save-only   # 只入库已分析的数据，不调 API
    python analyze_images.py <manifest.json> --dry-run     # 只看有哪些待分析，不调 API
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from openai import OpenAI


# ── 配置 ──────────────────────────────────────────

def get_client():
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("VISION_API_KEY") or os.environ.get("MIMO_API_KEY", "")
    if not api_key:
        raise ValueError("未设置 VISION_API_KEY 或 MIMO_API_KEY")
    base_url = os.environ.get("VISION_BASE_URL", os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"))
    model = os.environ.get("VISION_MODEL", "mimo-v2-omni")
    return OpenAI(api_key=api_key, base_url=base_url), model


# ── Prompt ──────────────────────────────────────────

PARSE_PROMPT = """请完整提取这张图片中的所有指数估值数据，不要遗漏任何字段。

提取内容：
1. 指数名称、指数代码、当前点位、涨跌幅
2. 判断指标类型（市盈率/市净率/股息率/风险溢价），只填一个有数据的
3. 提取对应的：当前值、分位点/百分位、危险值、中位数、机会值、最大值、最小值、平均值
4. 图片中有哪些就填哪些，没有的填 null。数字保持原样

输出格式（严格 JSON，不要其他文字）：
{
  "index_name": "稀土产业",
  "index_code": "930598.CSI",
  "current_point": 3222.96,
  "change_pct": -3.05,
  "metric_type": "市净率",
  "current_value": 3.59,
  "percentile": 96.28,
  "danger_value": 3.0,
  "median": 2.29,
  "opportunity_value": 1.97,
  "max_value": 4.22,
  "min_value": 1.49,
  "avg_value": 2.45
}"""


# ── 核心函数 ──────────────────────────────────────────

def analyze_image(client: OpenAI, model: str, image_path: str) -> dict:
    """分析单张图片，返回结构化数据。"""
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    ext = image_path.rsplit(".", 1)[-1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")

    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": PARSE_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
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
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw}


def analyze_manifest(manifest_path: str, force: bool = False) -> list[dict]:
    """分析 manifest 中所有未分析的图片。返回结果列表。"""
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest_dir = os.path.dirname(manifest_path)
    client, model = get_client()

    results = []
    for img in manifest.get("images", []):
        # 跳过已分析的
        if not force and img.get("analyzed") and img.get("parsed_data"):
            continue

        local_path = img.get("local_path", "")
        if not os.path.isabs(local_path):
            # 先试原路径（相对项目根目录），再试拼接 manifest 目录
            if os.path.exists(local_path):
                pass  # 直接用
            else:
                local_path = os.path.join(manifest_dir, local_path)

        if not os.path.exists(local_path):
            print(f"  跳过: {local_path} (文件不存在)")
            continue

        print(f"  分析中: {os.path.basename(local_path)} ...", end=" ", flush=True)
        try:
            data = analyze_image(client, model, local_path)
            img["parsed_data"] = data
            img["analyzed"] = True
            img["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
            results.append({"image_path": local_path, "data": data})
            name = data.get("index_name", data.get("指数名称", "?"))
            code = data.get("index_code", data.get("指数代码", "?"))
            print(f"OK — {name} ({code})")
        except Exception as e:
            img["analyze_error"] = str(e)
            print(f"失败: {e}")

    # 写回 manifest
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return results


# ── CLI ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="用视觉模型分析 manifest 中的图片")
    parser.add_argument("path", help="manifest.json 路径或图片目录")
    parser.add_argument("--save", action="store_true", help="分析后自动入库")
    parser.add_argument("--save-only", action="store_true", help="只入库已分析的数据，不调 API")
    parser.add_argument("--force", action="store_true", help="重新分析已分析过的图片")
    parser.add_argument("--dry-run", action="store_true", help="只列出待分析图片，不调 API")
    args = parser.parse_args()

    # 定位 manifest.json
    manifest_path = args.path
    if os.path.isdir(manifest_path):
        manifest_path = os.path.join(manifest_path, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"找不到 manifest.json: {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    title = manifest.get("title", "")
    images = manifest.get("images", [])

    # --save-only: 只入库已分析的数据，不调 API
    if args.save_only:
        analyzed = [img for img in images if img.get("analyzed") and img.get("parsed_data")]
        if not analyzed:
            print("没有已分析的数据可入库")
            return

        print(f"文章: {title}")
        print(f"已分析图片: {len(analyzed)} 张，正在入库...\n")

        sys.path.insert(0, str(Path(__file__).parent.parent / ".claude" / "skills" / "save-valuation" / "scripts"))
        from save_valuation import init_db, process_single

        init_db()
        article_date = manifest.get("article_date", "")
        count = 0
        for img in analyzed:
            d = img["parsed_data"]
            code = d.get("index_code") or d.get("指数代码")
            if not code or code == "?" or code == "UNKNOWN":
                print(f"  跳过: {d.get('index_name', '?')} (无指数代码)")
                continue
            try:
                vid = process_single(d, manifest_path=manifest_path, snapshot_date=article_date)
                count += 1
                print(f"  入库: {code} (id={vid})")
            except Exception as e:
                print(f"  失败: {code} — {e}")
        print(f"\n入库完成: {count}/{len(analyzed)} 条")
        return

    pending = [img for img in images if not img.get("analyzed") or args.force]

    print(f"文章: {title}")
    print(f"图片总数: {len(images)}, 待分析: {len(pending)}")

    if args.dry_run:
        for i, img in enumerate(pending, 1):
            lp = img.get("local_path", "")
            print(f"  {i}. {os.path.basename(lp)}")
        return

    if not pending:
        print("所有图片已分析，无需处理")
        return

    print(f"\n开始分析 {len(pending)} 张图片...\n")
    results = analyze_manifest(manifest_path, force=args.force)

    print(f"\n分析完成: {len(results)}/{len(pending)} 张")

    # 可选：自动入库
    if args.save and results:
        print("\n正在入库...")
        # 动态导入 save_valuation
        sys.path.insert(0, str(Path(__file__).parent.parent / ".claude" / "skills" / "save-valuation" / "scripts"))
        from save_valuation import init_db, process_single

        init_db()
        article_date = manifest.get("article_date", "")
        count = 0
        for r in results:
            d = r["data"]
            code = d.get("index_code") or d.get("指数代码")
            if not code or code == "?" or code == "UNKNOWN":
                print(f"  跳过: {d.get('index_name', '?')} (无指数代码)")
                continue
            vid = process_single(d, manifest_path=manifest_path, snapshot_date=article_date)
            count += 1
            print(f"  入库: {code} (id={vid})")
        print(f"\n入库完成: {count} 条")


if __name__ == "__main__":
    main()
