#!/usr/bin/env python3
"""PDF 书籍 LLM 增强蒸馏脚本。

使用 LLM 从 PDF 书籍中智能提取核心知识点。

用法:
    python3 scripts/distill_pdf_llm.py /path/to/book.pdf [--pages 50]
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from PyPDF2 import PdfReader
from openai import OpenAI
from db.knowledge import add_knowledge

# 小米 mimo 模型配置（从环境变量读取）
MIMO_API_KEY = os.environ.get("MIMO_API_KEY", "")
MIMO_BASE_URL = os.environ.get("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
MIMO_MODEL = "mimo-v2.5"


EXTRACT_PROMPT = """你是一位专业的投资知识提取专家。请从以下书籍内容中提取核心知识点。

## 提取要求

1. **概念定义**：提取关键投资概念及其定义
2. **核心原则**：提取作者的核心投资原则/理念
3. **策略方法**：提取具体的投资策略或方法
4. **数据规律**：提取重要的数据、规律、历史案例

## 输出格式（JSON 数组）

每个知识点格式：
- title: 知识点标题（简洁）
- category: concept|principle|strategy|case
- content: 详细内容（markdown 格式，200-500字）
- keywords: 关键词数组
- importance: 重要性 1-10

## 重要规则
- 每个知识点独立完整，不依赖上下文
- 保留原文的核心观点和数据
- 用通俗易懂的语言重新组织
- 每次提取 3-8 个知识点
- 只输出 JSON 数组，无其他文字

## 书籍内容

"""


def extract_pdf_text(pdf_path: str, start_page: int = 0, end_page: int = None) -> str:
    """提取 PDF 指定页范围的文本。"""
    reader = PdfReader(pdf_path)
    if end_page is None:
        end_page = len(reader.pages)

    text = ""
    for i in range(start_page, min(end_page, len(reader.pages))):
        page_text = reader.pages[i].extract_text()
        if page_text:
            text += page_text + "\n"
    return text


def distill_with_llm(text: str, book_title: str, chunk_id: int) -> list[dict]:
    """使用小米 mimo 模型蒸馏文本内容。"""
    # 截断到合适长度（避免 token 超限）
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars]

    prompt = EXTRACT_PROMPT + text

    try:
        client = OpenAI(api_key=MIMO_API_KEY, base_url=MIMO_BASE_URL)

        response = client.chat.completions.create(
            model=MIMO_MODEL,
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

        # 尝试解析 JSON
        try:
            concepts = json.loads(result_text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 数组
            import re
            match = re.search(r'\[.*\]', result_text, re.DOTALL)
            if match:
                concepts = json.loads(match.group())
            else:
                print(f"  警告: 无法解析 LLM 输出 (chunk {chunk_id})")
                return []

        # 添加来源信息
        for concept in concepts:
            concept["source"] = book_title
            concept["book_chunk"] = chunk_id

        return concepts

    except Exception as e:
        print(f"  错误: LLM 调用失败 (chunk {chunk_id}) - {e}")
        return []


def distill_pdf(pdf_path: str, book_title: str = None, pages_per_chunk: int = 30,
                max_pages: int = None):
    """蒸馏 PDF 书籍（LLM 增强版）。"""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"错误: 文件不存在 {pdf_path}")
        return

    if not book_title:
        book_title = pdf_path.stem

    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    print(f"{'=' * 50}")
    print(f"开始 LLM 蒸馏: {book_title}")
    print(f"总页数: {total_pages}, 每块 {pages_per_chunk} 页")
    print(f"{'=' * 50}")

    all_concepts = []
    chunk_id = 0

    for start in range(0, total_pages, pages_per_chunk):
        end = min(start + pages_per_chunk, total_pages)
        chunk_id += 1

        print(f"\n[{chunk_id}] 处理第 {start+1}-{end} 页...")

        # 提取文本
        text = extract_pdf_text(str(pdf_path), start, end)
        if len(text.strip()) < 100:
            print(f"  跳过（内容太少）")
            continue

        # LLM 蒸馏
        concepts = distill_with_llm(text, book_title, chunk_id)
        all_concepts.extend(concepts)

        print(f"  提取 {len(concepts)} 个知识点")
        for c in concepts:
            print(f"    - {c.get('title', '未知')} [{c.get('category', '未知')}]")

    # 存储到知识库
    print(f"\n{'=' * 50}")
    print(f"存储到知识库...")
    saved_count = 0
    for concept in all_concepts:
        try:
            add_knowledge(
                category="book",
                title=f"[{book_title}] {concept.get('title', '未知')}",
                content=concept.get("content", ""),
                subcategory=concept.get("category", "concept"),
                source=concept.get("source", book_title),
                keywords=concept.get("keywords", []),
                importance=concept.get("importance", 7)
            )
            saved_count += 1
        except Exception as e:
            print(f"  保存失败: {concept.get('title')} - {e}")

    print(f"\n{'=' * 50}")
    print(f"蒸馏完成!")
    print(f"  提取知识点: {len(all_concepts)}")
    print(f"  成功保存: {saved_count}")
    print(f"{'=' * 50}")

    return {
        "book_title": book_title,
        "total_pages": total_pages,
        "chunks": chunk_id,
        "concepts_extracted": len(all_concepts),
        "concepts_saved": saved_count
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PDF 书籍 LLM 蒸馏")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--title", help="书籍标题", default=None)
    parser.add_argument("--pages", type=int, help="每块页数", default=30)
    parser.add_argument("--max-pages", type=int, help="最大处理页数", default=None)

    args = parser.parse_args()
    distill_pdf(args.pdf_path, args.title, args.pages, args.max_pages)


if __name__ == "__main__":
    main()
