#!/usr/bin/env python3
"""扫描版 PDF OCR 蒸馏脚本。

使用 OCR 提取扫描版 PDF 文本，然后用 LLM 蒸馏知识。

依赖:
    brew install tesseract tesseract-lang
    pip install pytesseract pdf2image Pillow
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def check_dependencies():
    """检查依赖是否安装。"""
    missing = []
    try:
        import pytesseract
    except ImportError:
        missing.append("pytesseract")
    try:
        from pdf2image import convert_from_path
    except ImportError:
        missing.append("pdf2image")
    try:
        from PIL import Image
    except ImportError:
        missing.append("Pillow")

    if missing:
        print(f"缺少依赖: {', '.join(missing)}")
        print(f"安装: pip install {' '.join(missing)}")
        return False

    # 检查 tesseract
    try:
        import subprocess
        result = subprocess.run(["tesseract", "--version"], capture_output=True, timeout=5)
        if result.returncode != 0:
            print("tesseract 未安装: brew install tesseract tesseract-lang")
            return False
    except FileNotFoundError:
        print("tesseract 未安装: brew install tesseract tesseract-lang")
        return False

    return True


def ocr_pdf(pdf_path: str, start_page: int = 0, end_page: int = None,
            dpi: int = 200, lang: str = "chi_sim+eng") -> str:
    """对 PDF 进行 OCR 提取文本。"""
    import pytesseract
    from pdf2image import convert_from_path

    print(f"  转换 PDF 页面 (DPI={dpi})...")
    images = convert_from_path(
        pdf_path,
        dpi=dpi,
        first_page=start_page + 1,  # pdf2image 使用 1-based 索引
        last_page=end_page,
    )

    print(f"  OCR 识别中 ({len(images)} 页, 语言={lang})...")
    full_text = ""
    for i, img in enumerate(images):
        text = pytesseract.image_to_string(img, lang=lang)
        full_text += text + "\n"
        if (i + 1) % 10 == 0:
            print(f"    已处理 {i+1}/{len(images)} 页")

    return full_text


def distill_with_llm(text: str, book_title: str, chunk_id: int,
                     api_key: str, base_url: str) -> list:
    """使用 LLM 蒸馏文本。"""
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
每个知识点：title, category(concept/principle/strategy/case), content(markdown), keywords[], importance(1-10)
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

        # 提取 JSON
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
        print(f"  LLM 调用失败: {e}")
        return []


def distill_scan_pdf(pdf_path: str, book_title: str = None,
                     pages_per_chunk: int = 20, max_pages: int = None,
                     dpi: int = 200, api_key: str = "", base_url: str = ""):
    """蒸馏扫描版 PDF。"""
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
    print(f"OCR 蒸馏: {book_title}")
    print(f"总页数: {total_pages}, 每块 {pages_per_chunk} 页")
    print(f"{'='*50}")

    all_concepts = []
    chunk_id = 0

    for start in range(0, total_pages, pages_per_chunk):
        end = min(start + pages_per_chunk, total_pages)
        chunk_id += 1

        print(f"\n[{chunk_id}] 处理第 {start+1}-{end} 页...")

        # OCR 提取
        text = ocr_pdf(str(pdf_path), start, end, dpi=dpi)
        if len(text.strip()) < 100:
            print(f"  跳过（OCR 内容太少）")
            continue

        # LLM 蒸馏
        concepts = distill_with_llm(text, book_title, chunk_id, api_key, base_url)
        all_concepts.extend(concepts)

        print(f"  提取 {len(concepts)} 个知识点")

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
    parser = argparse.ArgumentParser(description="扫描版 PDF OCR 蒸馏")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--title", help="书籍标题")
    parser.add_argument("--pages", type=int, default=20, help="每块页数")
    parser.add_argument("--max-pages", type=int, help="最大处理页数")
    parser.add_argument("--dpi", type=int, default=200, help="OCR DPI")
    parser.add_argument("--api-key", default="", help="API Key")
    parser.add_argument("--base-url", default="", help="API Base URL")
    args = parser.parse_args()

    if not check_dependencies():
        sys.exit(1)

    distill_scan_pdf(
        args.pdf_path, args.title, args.pages,
        args.max_pages, args.dpi, args.api_key, args.base_url
    )


if __name__ == "__main__":
    main()
