#!/usr/bin/env python3
"""PDF 视觉蒸馏脚本 — 用 LLM 图片理解能力提取扫描版 PDF 知识。

原理：PDF 每页 → 图片 → base64 → mimo-v2.5 图片理解 → 提取文字 → 蒸馏知识

优点：不依赖 OCR 工具，识别准确率高
缺点：每页都要调 API，token 消耗较大

用法:
    python3 scripts/distill_pdf_vision.py /path/to/book.pdf --max-pages 20
"""

import sys
import json
import base64
from pathlib import Path
from io import BytesIO

sys.path.insert(0, str(Path(__file__).parent.parent))


def pdf_page_to_base64(pdf_path: str, page_num: int, dpi: int = 150) -> str:
    """将 PDF 某页转为 base64 图片。"""
    from pdf2image import convert_from_path

    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=page_num + 1,  # 1-based
        last_page=page_num + 1,
    )

    if not images:
        return None

    img = images[0]
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def extract_text_from_image(base64_img: str, api_key: str, base_url: str) -> str:
    """用 LLM 从图片中提取文字。"""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)

    response = client.chat.completions.create(
        model="mimo-v2.5",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_img}"
                        }
                    },
                    {
                        "type": "text",
                        "text": "请提取这张图片中的所有文字内容，保持原文格式。如果是书籍页面，提取正文内容，忽略页码和页眉页脚。只输出文字内容，无其他说明。"
                    }
                ]
            }
        ],
        max_tokens=4000,
    )

    return response.choices[0].message.content.strip()


def distill_text(text: str, book_title: str, chunk_id: int,
                 api_key: str, base_url: str) -> list:
    """用 LLM 从文本中蒸馏知识。"""
    from openai import OpenAI

    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars]

    prompt = """你是一位专业的投资知识提取专家。请从以下书籍内容中提取核心知识点。

## 提取要求
1. 概念定义：关键投资概念及其定义
2. 核心原则：投资原则/理念
3. 策略方法：具体投资策略
4. 数据规律：重要数据、规律、案例

## 输出格式（JSON 数组）
每个知识点：title, category(concept/principle/strategy/case), content(markdown, 200-500字), keywords[], importance(1-10)
只输出 JSON，无其他文字。

## 内容
""" + text

    client = OpenAI(api_key=api_key, base_url=base_url)

    try:
        response = client.chat.completions.create(
            model="mimo-v2.5",
            messages=[
                {"role": "system", "content": "你是投资知识提取专家，只输出 JSON。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=4000,
        )
        result_text = response.choices[0].message.content.strip()

        if "```" in result_text:
            parts = result_text.split("```")
            result_text = parts[1] if len(parts) > 1 else parts[0]
            if result_text.startswith("json"):
                result_text = result_text[4:]

        import re
        try:
            return json.loads(result_text)
        except json.JSONDecodeError:
            match = re.search(r'\[.*\]', result_text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return []
    except Exception as e:
        print(f"  LLM 蒸馏失败: {e}")
        return []


def distill_scan_pdf(pdf_path: str, book_title: str = None,
                     pages_per_chunk: int = 10, max_pages: int = None,
                     dpi: int = 150, api_key: str = "", base_url: str = ""):
    """蒸馏扫描版 PDF（使用 LLM 图片理解）。"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"错误: 文件不存在 {pdf_path}")
        return

    if not book_title:
        book_title = pdf_path.stem

    from PyPDF2 import PdfReader
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    print(f"{'='*50}")
    print(f"视觉蒸馏: {book_title}")
    print(f"总页数: {total_pages}, 每块 {pages_per_chunk} 页")
    print(f"预计 API 调用: {total_pages // pages_per_chunk + 1} 次蒸馏 + {total_pages} 次图片识别")
    print(f"{'='*50}")

    all_concepts = []
    chunk_id = 0

    for start in range(0, total_pages, pages_per_chunk):
        end = min(start + pages_per_chunk, total_pages)
        chunk_id += 1

        print(f"\n[{chunk_id}] 处理第 {start+1}-{end} 页...")

        # 逐页 OCR
        chunk_text = ""
        for page_num in range(start, end):
            print(f"  第 {page_num+1} 页: 转换图片...")
            base64_img = pdf_page_to_base64(str(pdf_path), page_num, dpi=dpi)
            if not base64_img:
                print(f"  第 {page_num+1} 页: 转换失败，跳过")
                continue

            print(f"  第 {page_num+1} 页: 识别文字...")
            text = extract_text_from_image(base64_img, api_key, base_url)
            chunk_text += text + "\n"

        if len(chunk_text.strip()) < 100:
            print(f"  跳过（内容太少）")
            continue

        # LLM 蒸馏
        print(f"  蒸馏知识点...")
        concepts = distill_text(chunk_text, book_title, chunk_id, api_key, base_url)
        all_concepts.extend(concepts)

        print(f"  提取 {len(concepts)} 个知识点")
        for c in concepts:
            print(f"    - {c.get('title', '未知')} [{c.get('category', '未知')}]")

    # 存储到知识库
    from db.knowledge import add_knowledge
    saved_count = 0
    for concept in all_concepts:
        try:
            add_knowledge(
                category="book",
                title=f"[{book_title}] {concept.get('title', '未知')}",
                content=concept.get("content", ""),
                subcategory=concept.get("category", "concept"),
                source=book_title,
                keywords=concept.get("keywords", []),
                importance=concept.get("importance", 7)
            )
            saved_count += 1
        except Exception as e:
            print(f"  保存失败: {concept.get('title')} - {e}")

    print(f"\n{'='*50}")
    print(f"蒸馏完成! 提取: {len(all_concepts)}, 保存: {saved_count}")
    print(f"{'='*50}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PDF 视觉蒸馏（LLM 图片理解）")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--title", help="书籍标题")
    parser.add_argument("--pages", type=int, default=10, help="每块页数")
    parser.add_argument("--max-pages", type=int, help="最大处理页数")
    parser.add_argument("--dpi", type=int, default=150, help="图片 DPI")
    parser.add_argument("--api-key", required=True, help="API Key")
    parser.add_argument("--base-url", default="https://token-plan-cn.xiaomimimo.com/v1", help="API Base URL")
    args = parser.parse_args()

    distill_scan_pdf(
        args.pdf_path, args.title, args.pages,
        args.max_pages, args.dpi, args.api_key, args.base_url
    )


if __name__ == "__main__":
    main()
