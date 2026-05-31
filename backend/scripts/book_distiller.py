"""书籍蒸馏脚本 — 支持多种输入方式

使用方法：
1. 从文本文件蒸馏：python book_distiller.py --file chapter.txt
2. 从 PDF 蒸馏：python book_distiller.py --pdf book.pdf
3. 从手动输入蒸馏：python book_distiller.py --text "这里是书籍内容..."
4. 交互式蒸馏：python book_distiller.py --interactive
"""

import json
import logging
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_service import _call_llm
from rag import index_to_chroma, _index_document

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── 蒸馏 Prompt 模板 ──────────────────────────────────────

BOOK_DISTILL_PROMPT = """你是一个投资知识提炼专家。请从以下投资书籍内容中提取核心原则。

要求：
1. 每条原则一句话概括观点
2. 紧接着用 2-3 句话解释为什么这个原则重要
3. 如果原文有具体数据或例子，保留
4. 每条原则 200-400 字
5. 输出 JSON 数组：[{"principle": "...", "category": "价值投资|资产配置|风险控制|行为金融|...", "importance": "high|medium|low"}]

内容：
{content}"""


FAQ_PROMPT = """你是一个理财教育专家。请基于以下投资知识，生成 {count} 条新手投资者最常问的问题和回答。

要求：
- 问题用真实用户的口吻（可以口语化、不专业）
- 回答必须有具体数据或数字支撑
- 不能是"建议长期持有"这种废话
- 每条 QA 200-400 字
- 输出 JSON：[{{"question": "...", "answer": "...", "tags": ["基金", "定投", ...]}}]

参考知识：
{content}"""


TERM_PROMPT = """你是一个金融术语专家。请解释以下投资术语，要求：
1. 用通俗易懂的语言解释（假设读者是投资新手）
2. 给出一个具体的例子或数字
3. 说明这个指标怎么用（多少算高/低）
4. 输出 JSON：[{{"term": "...", "definition": "...", "example": "...", "usage": "..." }}]

术语列表：
{terms}"""


# ── 蒸馏函数 ──────────────────────────────────────

def distill_content(content: str, content_type: str = "book") -> list[dict]:
    """从内容中蒸馏投资原则。

    Args:
        content: 内容文本
        content_type: 内容类型 (book/article/faq)

    Returns:
        蒸馏后的原则列表
    """
    try:
        prompt = BOOK_DISTILL_PROMPT.format(content=content[:4000])
        resp = _call_llm(
            caller=f"distill_{content_type}",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2000
        )
        result = resp.choices[0].message.content

        # 解析 JSON
        principles = json.loads(result)
        logger.info(f"蒸馏出 {len(principles)} 条原则")
        return principles
    except Exception as e:
        logger.error(f"蒸馏失败: {e}")
        return []


def generate_faqs(content: str, count: int = 10) -> list[dict]:
    """从内容生成 FAQ 问答对。

    Args:
        content: 内容文本
        count: 生成 FAQ 数量

    Returns:
        FAQ 列表
    """
    try:
        prompt = FAQ_PROMPT.format(content=content[:4000], count=count)
        resp = _call_llm(
            caller="generate_faqs",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=3000
        )
        result = resp.choices[0].message.content

        faqs = json.loads(result)
        logger.info(f"生成 {len(faqs)} 条 FAQ")
        return faqs
    except Exception as e:
        logger.error(f"FAQ 生成失败: {e}")
        return []


def explain_terms(terms: list[str]) -> list[dict]:
    """解释金融术语。

    Args:
        terms: 术语列表

    Returns:
        术语解释列表
    """
    try:
        prompt = TERM_PROMPT.format(terms="\n".join(terms))
        resp = _call_llm(
            caller="explain_terms",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=3000
        )
        result = resp.choices[0].message.content

        explanations = json.loads(result)
        logger.info(f"解释 {len(explanations)} 个术语")
        return explanations
    except Exception as e:
        logger.error(f"术语解释失败: {e}")
        return []


# ── 入库函数 ──────────────────────────────────────

def save_to_rag(items: list[dict], content_type: str = "principle", source: str = "book"):
    """将蒸馏结果保存到 RAG 知识库。

    Args:
        items: 蒸馏结果列表
        content_type: 内容类型 (principle/faq/term)
        source: 来源标识
    """
    for i, item in enumerate(items):
        if content_type == "principle":
            title = f"{source}原则 {i+1}: {item.get('category', '投资原则')}"
            body = f"原则: {item.get('principle', '')}"
        elif content_type == "faq":
            title = f"FAQ: {item.get('question', '')[:50]}"
            body = f"问题: {item.get('question', '')}\n\n回答: {item.get('answer', '')}"
        elif content_type == "term":
            title = f"术语: {item.get('term', '')}"
            body = f"定义: {item.get('definition', '')}\n\n例子: {item.get('example', '')}\n\n用法: {item.get('usage', '')}"
        else:
            continue

        # 存入 FTS
        _index_document("skill", title, body, f"{source}_{content_type}_{i}")

        # 存入 ChromaDB
        index_to_chroma("skill", f"{source}_{content_type}_{i}", title, body[:8000])

    logger.info(f"已保存 {len(items)} 条 {content_type} 到 RAG")


# ── 文件读取函数 ──────────────────────────────────────

def read_text_file(file_path: str) -> str:
    """从文本文件读取内容。

    Args:
        file_path: 文件路径

    Returns:
        文件内容
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"读取文件失败: {e}")
        return ""


def read_pdf(file_path: str) -> str:
    """从 PDF 文件提取文字。

    Args:
        file_path: PDF 文件路径

    Returns:
        提取的文字
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as e:
        logger.error(f"读取 PDF 失败: {e}")
        return ""


# ── 交互式蒸馏 ──────────────────────────────────────

def interactive_distill():
    """交互式蒸馏模式。"""
    print("=" * 60)
    print("📚 书籍蒸馏工具 - 交互模式")
    print("=" * 60)
    print()
    print("请选择操作：")
    print("1. 蒸馏投资原则")
    print("2. 生成 FAQ 问答对")
    print("3. 解释金融术语")
    print("4. 退出")
    print()

    choice = input("请输入选项 (1-4): ").strip()

    if choice == "1":
        print("\n📝 请输入书籍内容（输入 END 结束）：")
        lines = []
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
        content = "\n".join(lines)

        if content:
            print("\n⏳ 正在蒸馏...")
            principles = distill_content(content, "book")
            if principles:
                print(f"\n✅ 蒸馏出 {len(principles)} 条原则：")
                for i, p in enumerate(principles, 1):
                    print(f"\n{i}. [{p.get('category', '')}] {p.get('principle', '')[:100]}...")

                save = input("\n💾 是否保存到 RAG 知识库？(y/n): ").strip().lower()
                if save == 'y':
                    save_to_rag(principles, "principle", "手动输入")
                    print("✅ 已保存到 RAG 知识库")

    elif choice == "2":
        print("\n📝 请输入知识内容（输入 END 结束）：")
        lines = []
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
        content = "\n".join(lines)

        if content:
            count = input("\n🔢 生成 FAQ 数量 (默认 5): ").strip()
            count = int(count) if count.isdigit() else 5

            print("\n⏳ 正在生成 FAQ...")
            faqs = generate_faqs(content, count)
            if faqs:
                print(f"\n✅ 生成 {len(faqs)} 条 FAQ：")
                for i, faq in enumerate(faqs, 1):
                    print(f"\n{i}. Q: {faq.get('question', '')[:60]}...")
                    print(f"   A: {faq.get('answer', '')[:60]}...")

                save = input("\n💾 是否保存到 RAG 知识库？(y/n): ").strip().lower()
                if save == 'y':
                    save_to_rag(faqs, "faq", "手动输入")
                    print("✅ 已保存到 RAG 知识库")

    elif choice == "3":
        print("\n📝 请输入术语（用逗号分隔）：")
        terms_input = input().strip()
        terms = [t.strip() for t in terms_input.split(",") if t.strip()]

        if terms:
            print("\n⏳ 正在解释术语...")
            explanations = explain_terms(terms)
            if explanations:
                print(f"\n✅ 解释 {len(explanations)} 个术语：")
                for i, exp in enumerate(explanations, 1):
                    print(f"\n{i}. {exp.get('term', '')}: {exp.get('definition', '')[:80]}...")

                save = input("\n💾 是否保存到 RAG 知识库？(y/n): ").strip().lower()
                if save == 'y':
                    save_to_rag(explanations, "term", "手动输入")
                    print("✅ 已保存到 RAG 知识库")

    elif choice == "4":
        print("👋 再见！")
        return

    else:
        print("❌ 无效选项")

    # 递归继续
    interactive_distill()


# ── 主函数 ──────────────────────────────────────

def main():
    """主函数：根据参数执行蒸馏任务。"""
    import argparse

    parser = argparse.ArgumentParser(description="书籍蒸馏脚本")
    parser.add_argument("--file", type=str, help="文本文件路径")
    parser.add_argument("--pdf", type=str, help="PDF 文件路径")
    parser.add_argument("--text", type=str, help="直接输入文本")
    parser.add_argument("--terms", type=str, help="术语列表（逗号分隔）")
    parser.add_argument("--interactive", action="store_true", help="交互式模式")
    parser.add_argument("--count", type=int, default=5, help="生成 FAQ 数量")

    args = parser.parse_args()

    if args.interactive:
        interactive_distill()
        return

    # 读取内容
    content = ""
    source = "手动输入"

    if args.file:
        content = read_text_file(args.file)
        source = Path(args.file).stem
    elif args.pdf:
        content = read_pdf(args.pdf)
        source = Path(args.pdf).stem
    elif args.text:
        content = args.text
    elif args.terms:
        terms = [t.strip() for t in args.terms.split(",") if t.strip()]
        if terms:
            explanations = explain_terms(terms)
            if explanations:
                save_to_rag(explanations, "term", "术语解释")
                print(f"✅ 已解释并保存 {len(explanations)} 个术语")
        return
    else:
        parser.print_help()
        return

    if not content:
        print("❌ 没有内容可蒸馏")
        return

    # 蒸馏
    print(f"\n⏳ 正在从 '{source}' 蒸馏...")
    principles = distill_content(content, "book")
    if principles:
        print(f"\n✅ 蒸馏出 {len(principles)} 条原则")
        save_to_rag(principles, "principle", source)
        print(f"✅ 已保存到 RAG 知识库")


if __name__ == "__main__":
    main()
