#!/usr/bin/env python3
"""PDF 书籍知识蒸馏脚本。

从 PDF 书籍中提取核心知识点，存储到知识库。

用法:
    python3 scripts/distill_pdf.py /path/to/book.pdf
"""

import sys
import json
import re
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from PyPDF2 import PdfReader
from db.knowledge import add_knowledge


def extract_pdf_text(pdf_path: str) -> str:
    """提取 PDF 全文。"""
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text


def split_into_chapters(text: str) -> list[dict]:
    """将文本按章节分割。"""
    # 匹配中文章节标题（如 "第一章"、"第1章"、"第一部分"）
    chapter_pattern = r'(第[一二三四五六七八九十百千\d]+[章部篇节])'

    chapters = []
    current_title = "前言"
    current_content = []

    for line in text.split('\n'):
        if re.match(chapter_pattern, line.strip()):
            # 保存上一章
            if current_content:
                chapters.append({
                    "title": current_title,
                    "content": '\n'.join(current_content)
                })
            current_title = line.strip()
            current_content = []
        else:
            current_content.append(line)

    # 保存最后一章
    if current_content:
        chapters.append({
            "title": current_title,
            "content": '\n'.join(current_content)
        })

    return chapters


def extract_key_concepts(chapter_text: str, chapter_title: str) -> list[dict]:
    """从章节文本中提取核心概念。"""
    concepts = []

    # 简单的关键词匹配提取
    # 后续可以用 LLM 增强

    # 提取定义性内容（通常包含"是指"、"是..."、"定义"等）
    definition_patterns = [
        r'(.{2,20})(?:是指|是|定义为|指的是)(.{10,100})',
        r'所谓(.{2,20})[，,](.{10,100})',
    ]

    for pattern in definition_patterns:
        matches = re.findall(pattern, chapter_text)
        for match in matches[:5]:  # 每章最多提取 5 个
            concept_name = match[0].strip()
            concept_def = match[1].strip()
            if len(concept_name) >= 2 and len(concept_def) >= 10:
                concepts.append({
                    "title": concept_name,
                    "content": f"## {concept_name}\n\n来源：{chapter_title}\n\n{concept_def}",
                    "keywords": [concept_name]
                })

    return concepts


def distill_pdf(pdf_path: str, book_title: str = None):
    """蒸馏 PDF 书籍。"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"错误: 文件不存在 {pdf_path}")
        return

    if not book_title:
        book_title = pdf_path.stem

    print(f"=" * 50)
    print(f"开始蒸馏: {book_title}")
    print(f"=" * 50)

    # 1. 提取全文
    print("\n[1/4] 提取 PDF 全文...")
    full_text = extract_pdf_text(str(pdf_path))
    print(f"  总字数: {len(full_text)}")

    # 2. 按章节分割
    print("\n[2/4] 按章节分割...")
    chapters = split_into_chapters(full_text)
    print(f"  章节数: {len(chapters)}")

    # 3. 提取核心概念
    print("\n[3/4] 提取核心概念...")
    all_concepts = []
    for i, chapter in enumerate(chapters):
        concepts = extract_key_concepts(chapter["content"], chapter["title"])
        all_concepts.extend(concepts)
        if concepts:
            print(f"  第 {i+1} 章 '{chapter['title']}': 提取 {len(concepts)} 个概念")

    print(f"\n  总计提取: {len(all_concepts)} 个概念")

    # 4. 存储到知识库
    print("\n[4/4] 存储到知识库...")
    saved_count = 0
    for concept in all_concepts:
        try:
            add_knowledge(
                category="book",
                title=f"[{book_title}] {concept['title']}",
                content=concept["content"],
                subcategory="concept",
                source=book_title,
                keywords=concept.get("keywords", []),
                importance=7
            )
            saved_count += 1
        except Exception as e:
            print(f"  保存失败: {concept['title']} - {e}")

    print(f"\n  成功保存: {saved_count} 条")

    print(f"\n{'=' * 50}")
    print(f"蒸馏完成!")
    print(f"{'=' * 50}")

    return {
        "book_title": book_title,
        "total_chars": len(full_text),
        "chapters": len(chapters),
        "concepts_extracted": len(all_concepts),
        "concepts_saved": saved_count
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python3 distill_pdf.py /path/to/book.pdf [书名]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    book_title = sys.argv[2] if len(sys.argv) > 2 else None

    distill_pdf(pdf_path, book_title)


if __name__ == "__main__":
    main()
