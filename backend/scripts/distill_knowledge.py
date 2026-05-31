"""理财知识蒸馏脚本 — 从书籍、文章、FAQ 中提取结构化知识

使用方法：
1. 从书籍蒸馏：python distill_knowledge.py --book "聪明的投资者"
2. 从文章蒸馏：python distill_knowledge.py --article 123
3. 从 FAQ 蒸馏：python distill_knowledge.py --faq faq_data.json
4. 批量蒸馏：python distill_knowledge.py --batch books/
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


FAQ_DISTILL_PROMPT = """你是一个理财教育专家。请基于以下投资知识，生成 {count} 条新手投资者最常问的问题和回答。

要求：
- 问题用真实用户的口吻（可以口语化、不专业）
- 回答必须有具体数据或数字支撑
- 不能是"建议长期持有"这种废话
- 每条 QA 200-400 字
- 输出 JSON：[{{"question": "...", "answer": "...", "tags": ["基金", "定投", ...]}}]

参考知识：
{content}"""


TERM_DISTILL_PROMPT = """你是一个金融术语专家。请解释以下投资术语，要求：
1. 用通俗易懂的语言解释（假设读者是投资新手）
2. 给出一个具体的例子或数字
3. 说明这个指标怎么用（多少算高/低）
4. 输出 JSON：[{{"term": "...", "definition": "...", "example": "...", "usage": "..." }}]

术语列表：
{terms}"""


ARTICLE_DISTILL_PROMPT = """你是一个投资知识提炼专家。请从以下文章中提取投资原则和知识点。

要求：
1. 提取 3-5 个核心观点
2. 每个观点包含：观点本身、论据、适用场景
3. 如果有具体数据或案例，保留
4. 输出 JSON：[{{"viewpoint": "...", "evidence": "...", "scenario": "...", "data": "..."}}]

文章内容：
{content}"""


# ── 蒸馏函数 ──────────────────────────────────────

def distill_book_chapter(chapter_content: str, chapter_title: str = "") -> list[dict]:
    """从书籍章节中蒸馏投资原则。

    Args:
        chapter_content: 章节内容
        chapter_title: 章节标题

    Returns:
        蒸馏后的原则列表
    """
    try:
        prompt = BOOK_DISTILL_PROMPT.format(content=chapter_content[:4000])
        resp = _call_llm(caller="distill_book", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=2000)
        result = resp.choices[0].message.content

        # 解析 JSON
        principles = json.loads(result)
        logger.info(f"从 '{chapter_title}' 蒸馏出 {len(principles)} 条原则")
        return principles
    except Exception as e:
        logger.error(f"蒸馏失败: {e}")
        return []


def distill_to_faq(knowledge_content: str, count: int = 10) -> list[dict]:
    """从知识内容生成 FAQ 问答对。

    Args:
        knowledge_content: 知识内容
        count: 生成 FAQ 数量

    Returns:
        FAQ 列表
    """
    try:
        prompt = FAQ_DISTILL_PROMPT.format(content=knowledge_content[:4000], count=count)
        resp = _call_llm(caller="distill_faq", messages=[{"role": "user", "content": prompt}], temperature=0.5, max_tokens=3000)
        result = resp.choices[0].message.content

        faqs = json.loads(result)
        logger.info(f"生成 {len(faqs)} 条 FAQ")
        return faqs
    except Exception as e:
        logger.error(f"FAQ 生成失败: {e}")
        return []


def distill_terms(terms: list[str]) -> list[dict]:
    """蒸馏金融术语解释。

    Args:
        terms: 术语列表

    Returns:
        术语解释列表
    """
    try:
        prompt = TERM_DISTILL_PROMPT.format(terms="\n".join(terms))
        resp = _call_llm(caller="distill_terms", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=3000)
        result = resp.choices[0].message.content

        explanations = json.loads(result)
        logger.info(f"解释 {len(explanations)} 个术语")
        return explanations
    except Exception as e:
        logger.error(f"术语解释失败: {e}")
        return []


def distill_article(article_content: str, article_title: str = "") -> list[dict]:
    """从文章中蒸馏投资观点。

    Args:
        article_content: 文章内容
        article_title: 文章标题

    Returns:
        观点列表
    """
    try:
        prompt = ARTICLE_DISTILL_PROMPT.format(content=article_content[:4000])
        resp = _call_llm(caller="distill_article", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=2000)
        result = resp.choices[0].message.content

        viewpoints = json.loads(result)
        logger.info(f"从 '{article_title}' 蒸馏出 {len(viewpoints)} 个观点")
        return viewpoints
    except Exception as e:
        logger.error(f"文章蒸馏失败: {e}")
        return []


# ── 入库函数 ──────────────────────────────────────

def save_principles_to_rag(principles: list[dict], source: str = "book"):
    """将蒸馏的原则保存到 RAG 知识库。

    Args:
        principles: 原则列表
        source: 来源标识
    """
    for i, p in enumerate(principles):
        title = f"{source}原则 {i+1}: {p.get('category', '投资原则')}"
        body = f"原则: {p.get('principle', '')}\n\n解释: {p.get('explanation', p.get('principle', ''))}"

        # 存入 FTS
        _index_document("skill", title, body, f"{source}_principle_{i}")

        # 存入 ChromaDB
        index_to_chroma("skill", f"{source}_principle_{i}", title, body[:8000])

    logger.info(f"已保存 {len(principles)} 条原则到 RAG")


def save_faqs_to_rag(faqs: list[dict]):
    """将 FAQ 保存到 RAG 知识库。

    Args:
        faqs: FAQ 列表
    """
    for i, faq in enumerate(faqs):
        title = f"FAQ: {faq.get('question', '')[:50]}"
        body = f"问题: {faq.get('question', '')}\n\n回答: {faq.get('answer', '')}"
        tags = ", ".join(faq.get('tags', []))

        # 存入 FTS
        _index_document("skill", title, body, f"faq_{i}")

        # 存入 ChromaDB
        index_to_chroma("skill", f"faq_{i}", title, body[:8000])

    logger.info(f"已保存 {len(faqs)} 条 FAQ 到 RAG")


def save_terms_to_rag(terms: list[dict]):
    """将术语解释保存到 RAG 知识库。

    Args:
        terms: 术语列表
    """
    for i, term in enumerate(terms):
        title = f"术语: {term.get('term', '')}"
        body = f"定义: {term.get('definition', '')}\n\n例子: {term.get('example', '')}\n\n用法: {term.get('usage', '')}"

        # 存入 FTS
        _index_document("skill", title, body, f"term_{i}")

        # 存入 ChromaDB
        index_to_chroma("skill", f"term_{i}", title, body[:8000])

    logger.info(f"已保存 {len(terms)} 个术语到 RAG")


def save_viewpoints_to_rag(viewpoints: list[dict], source: str = "article"):
    """将观点保存到 RAG 知识库。

    Args:
        viewpoints: 观点列表
        source: 来源标识
    """
    for i, vp in enumerate(viewpoints):
        title = f"{source}观点 {i+1}: {vp.get('viewpoint', '')[:50]}"
        body = f"观点: {vp.get('viewpoint', '')}\n\n论据: {vp.get('evidence', '')}\n\n场景: {vp.get('scenario', '')}"

        if vp.get('data'):
            body += f"\n\n数据: {vp['data']}"

        # 存入 FTS
        _index_document("skill", title, body, f"{source}_viewpoint_{i}")

        # 存入 ChromaDB
        index_to_chroma("skill", f"{source}_viewpoint_{i}", title, body[:8000])

    logger.info(f"已保存 {len(viewpoints)} 个观点到 RAG")


# ── 主函数 ──────────────────────────────────────

def main():
    """主函数：根据参数执行不同的蒸馏任务。"""
    import argparse

    parser = argparse.ArgumentParser(description="理财知识蒸馏脚本")
    parser.add_argument("--book", type=str, help="书籍名称或路径")
    parser.add_argument("--article", type=str, help="文章 ID 或内容")
    parser.add_argument("--faq", type=str, help="FAQ 数据文件路径")
    parser.add_argument("--terms", type=str, help="术语列表（逗号分隔）")
    parser.add_argument("--count", type=int, default=10, help="生成 FAQ 数量")

    args = parser.parse_args()

    if args.book:
        # 从书籍蒸馏
        logger.info(f"开始从书籍 '{args.book}' 蒸馏...")
        # TODO: 读取书籍内容
        print("请使用 distill_book_chapter() 函数手动蒸馏")

    elif args.article:
        # 从文章蒸馏
        logger.info(f"开始从文章 '{args.article}' 蒸馏...")
        # TODO: 读取文章内容
        print("请使用 distill_article() 函数手动蒸馏")

    elif args.faq:
        # 从文件生成 FAQ
        logger.info(f"开始从 '{args.faq}' 生成 FAQ...")
        with open(args.faq, 'r', encoding='utf-8') as f:
            content = f.read()
        faqs = distill_to_faq(content, args.count)
        if faqs:
            save_faqs_to_rag(faqs)
            print(f"已生成并保存 {len(faqs)} 条 FAQ")

    elif args.terms:
        # 解释术语
        logger.info(f"开始解释术语...")
        terms_list = [t.strip() for t in args.terms.split(",")]
        terms = distill_terms(terms_list)
        if terms:
            save_terms_to_rag(terms)
            print(f"已解释并保存 {len(terms)} 个术语")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
