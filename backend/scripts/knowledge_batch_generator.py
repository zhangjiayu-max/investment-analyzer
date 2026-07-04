"""知识库批量生成脚本 — 利用 MiMo 大规模生成投资知识

使用方法：
1. 生成全部：python knowledge_batch_generator.py --all
2. 生成术语：python knowledge_batch_generator.py --terms
3. 生成 FAQ：python knowledge_batch_generator.py --faq
4. 生成原则：python knowledge_batch_generator.py --principles
5. 生成行业知识：python knowledge_batch_generator.py --industry
6. 生成投资者心理：python knowledge_batch_generator.py --psychology
"""

import json
import logging
import sys
import time
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.llm_service import _call_llm
from services.rag import index_to_chroma, _index_document

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── 批量生成 Prompt 模板 ──────────────────────────────────────

TERMS_BATCH_PROMPT = """你是一个金融术语专家。请生成 {count} 个投资相关的专业术语解释。

要求：
1. 术语要覆盖：估值指标、风险指标、交易术语、基金类型、债券知识等
2. 每个术语包含：名称、通俗解释、具体例子、使用场景
3. 解释要让投资新手能理解
4. 输出 JSON 数组

输出格式：
[
  {{
    "term": "市盈率(PE)",
    "definition": "股价除以每股收益，表示投资者愿意为每1元收益支付的价格",
    "example": "PE=20表示投资者愿意为公司每赚1元支付20元",
    "usage": "PE<15通常认为低估，PE>30通常认为高估",
    "category": "估值指标"
  }}
]

请生成 {count} 个不同的术语。"""


FAQ_BATCH_PROMPT = """你是一个理财教育专家。请生成 {count} 条投资新手最常问的问题和专业回答。

要求：
1. 问题要真实（模拟真实用户口吻）
2. 回答要有具体数据支撑
3. 不能是空话套话
4. 覆盖：基金入门、定投策略、估值分析、风险管理等主题
5. 输出 JSON 数组

输出格式：
[
  {{
    "question": "基金净值低是不是更便宜？",
    "answer": "不是。基金净值高低和贵不贵没关系。净值=总资产/总份额。1元的基金和5元的基金，各投1000元，涨跌1%赚亏的钱是一样的。买基金看的是基金经理能力和投资方向，不是看净值。",
    "tags": ["基金", "入门", "误区"],
    "difficulty": "初级"
  }}
]

请生成 {count} 条不同的 FAQ。"""


PRINCIPLES_BATCH_PROMPT = """你是一个价值投资专家。请生成 {count} 条投资核心原则。

要求：
1. 每条原则一句话概括
2. 用 2-3 句话解释为什么重要
3. 给出具体的操作建议
4. 覆盖：安全边际、分散投资、长期持有、逆向思维等
5. 输出 JSON 数组

输出格式：
[
  {{
    "principle": "永远保留安全边际",
    "explanation": "安全边际是你付出的价格低于内在价值的差额。比如一只股票内在价值100元，你80元买入，这20元就是你的安全边际。安全边际越大，你的风险越小，收益潜力越大。",
    "category": "价值投资",
    "action": "买入任何资产前，先估算内在价值，只在价格低于价值70%以下时买入"
  }}
]

请生成 {count} 条不同的原则。"""


INDUSTRY_BATCH_PROMPT = """你是一个行业分析专家。请生成 {count} 个行业的分析框架。

要求：
1. 每个行业包含：核心指标、估值方法、风险因素、投资时机
2. 覆盖：消费、医药、科技、金融、周期等行业
3. 给出具体的估值区间建议
4. 输出 JSON 数组

输出格式：
[
  {{
    "industry": "白酒行业",
    "key_metrics": ["PE", "PB", "ROE", "营收增速", "毛利率"],
    "valuation_method": "PE估值法，历史分位点",
    "risk_factors": ["政策风险", "消费降级", "库存周期"],
    "entry_signal": "PE分位点<30%，库存周期底部",
    "exit_signal": "PE分位点>70%，政策收紧",
    "typical_range": "PE 15-35倍"
  }}
]

请生成 {count} 个不同的行业。"""


PSYCHOLOGY_BATCH_PROMPT = """你是一个行为金融学专家。请生成 {count} 条投资者心理偏误和应对方法。

要求：
1. 每条包含：偏误名称、表现、危害、应对方法
2. 用真实案例说明
3. 给出具体的心理调节建议
4. 输出 JSON 数组

输出格式：
[
  {{
    "bias": "损失厌恶",
    "manifestation": "亏损时不愿意卖出，总想着回本再卖",
    "harm": "小亏变大亏，错过其他投资机会",
    "solution": "设定止损线，到点就卖，不犹豫",
    "example": "股票跌了20%不舍得卖，结果跌到50%，损失更大"
  }}
]

请生成 {count} 条不同的心理偏误。"""


# ── 生成函数 ──────────────────────────────────────

def generate_terms(count: int = 10) -> list[dict]:
    """批量生成投资术语（分批生成避免截断）"""
    all_terms = []
    batch_size = 10  # 每批生成 10 个

    for i in range(0, count, batch_size):
        batch_count = min(batch_size, count - i)
        try:
            prompt = TERMS_BATCH_PROMPT.format(count=batch_count)
            resp = _call_llm(
                caller="batch_terms",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=3000
            )
            result = resp.choices[0].message.content
            # 尝试修复截断的 JSON
            if not result.rstrip().endswith(']'):
                result = result.rstrip().rstrip(',') + ']'
            terms = json.loads(result)
            all_terms.extend(terms)
            logger.info(f"第 {i//batch_size + 1} 批生成 {len(terms)} 个术语")
            time.sleep(1)  # 避免请求过快
        except Exception as e:
            logger.error(f"第 {i//batch_size + 1} 批生成失败: {e}")

    return all_terms


def generate_faqs(count: int = 10) -> list[dict]:
    """批量生成 FAQ（分批生成避免截断）"""
    all_faqs = []
    batch_size = 10

    for i in range(0, count, batch_size):
        batch_count = min(batch_size, count - i)
        try:
            prompt = FAQ_BATCH_PROMPT.format(count=batch_count)
            resp = _call_llm(
                caller="batch_faqs",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                max_tokens=3000
            )
            result = resp.choices[0].message.content
            if not result.rstrip().endswith(']'):
                result = result.rstrip().rstrip(',') + ']'
            faqs = json.loads(result)
            all_faqs.extend(faqs)
            logger.info(f"第 {i//batch_size + 1} 批生成 {len(faqs)} 条 FAQ")
            time.sleep(1)
        except Exception as e:
            logger.error(f"第 {i//batch_size + 1} 批生成失败: {e}")

    return all_faqs


def generate_principles(count: int = 10) -> list[dict]:
    """批量生成投资原则（分批生成避免截断）"""
    all_principles = []
    batch_size = 10

    for i in range(0, count, batch_size):
        batch_count = min(batch_size, count - i)
        try:
            prompt = PRINCIPLES_BATCH_PROMPT.format(count=batch_count)
            resp = _call_llm(
                caller="batch_principles",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=3000
            )
            result = resp.choices[0].message.content
            if not result.rstrip().endswith(']'):
                result = result.rstrip().rstrip(',') + ']'
            principles = json.loads(result)
            all_principles.extend(principles)
            logger.info(f"第 {i//batch_size + 1} 批生成 {len(principles)} 条原则")
            time.sleep(1)
        except Exception as e:
            logger.error(f"第 {i//batch_size + 1} 批生成失败: {e}")

    return all_principles


def generate_industry(count: int = 10) -> list[dict]:
    """批量生成行业分析框架（分批生成避免截断）"""
    all_industries = []
    batch_size = 5

    for i in range(0, count, batch_size):
        batch_count = min(batch_size, count - i)
        try:
            prompt = INDUSTRY_BATCH_PROMPT.format(count=batch_count)
            resp = _call_llm(
                caller="batch_industry",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=3000
            )
            result = resp.choices[0].message.content
            if not result.rstrip().endswith(']'):
                result = result.rstrip().rstrip(',') + ']'
            industries = json.loads(result)
            all_industries.extend(industries)
            logger.info(f"第 {i//batch_size + 1} 批生成 {len(industries)} 个行业框架")
            time.sleep(1)
        except Exception as e:
            logger.error(f"第 {i//batch_size + 1} 批生成失败: {e}")

    return all_industries


def generate_psychology(count: int = 10) -> list[dict]:
    """批量生成投资者心理偏误（分批生成避免截断）"""
    all_biases = []
    batch_size = 10

    for i in range(0, count, batch_size):
        batch_count = min(batch_size, count - i)
        try:
            prompt = PSYCHOLOGY_BATCH_PROMPT.format(count=batch_count)
            resp = _call_llm(
                caller="batch_psychology",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=3000
            )
            result = resp.choices[0].message.content
            if not result.rstrip().endswith(']'):
                result = result.rstrip().rstrip(',') + ']'
            biases = json.loads(result)
            all_biases.extend(biases)
            logger.info(f"第 {i//batch_size + 1} 批生成 {len(biases)} 条心理偏误")
            time.sleep(1)
        except Exception as e:
            logger.error(f"第 {i//batch_size + 1} 批生成失败: {e}")

    return all_biases


# ── 入库函数 ──────────────────────────────────────

def save_terms_to_rag(terms: list[dict]):
    """保存术语到 RAG"""
    for i, term in enumerate(terms):
        title = f"术语: {term.get('term', '')}"
        body = f"定义: {term.get('definition', '')}\n\n例子: {term.get('example', '')}\n\n用法: {term.get('usage', '')}"
        _index_document("skill", title, body, f"term_{i}")
        index_to_chroma("skill", f"term_{i}", title, body[:8000])
    logger.info(f"已保存 {len(terms)} 个术语到 RAG")


def save_faqs_to_rag(faqs: list[dict]):
    """保存 FAQ 到 RAG"""
    for i, faq in enumerate(faqs):
        title = f"FAQ: {faq.get('question', '')[:50]}"
        body = f"问题: {faq.get('question', '')}\n\n回答: {faq.get('answer', '')}"
        _index_document("skill", title, body, f"faq_{i}")
        index_to_chroma("skill", f"faq_{i}", title, body[:8000])
    logger.info(f"已保存 {len(faqs)} 条 FAQ 到 RAG")


def save_principles_to_rag(principles: list[dict]):
    """保存原则到 RAG"""
    for i, p in enumerate(principles):
        title = f"投资原则: {p.get('principle', '')[:50]}"
        body = f"原则: {p.get('principle', '')}\n\n解释: {p.get('explanation', '')}\n\n操作: {p.get('action', '')}"
        _index_document("skill", title, body, f"principle_{i}")
        index_to_chroma("skill", f"principle_{i}", title, body[:8000])
    logger.info(f"已保存 {len(principles)} 条原则到 RAG")


def save_industry_to_rag(industries: list[dict]):
    """保存行业框架到 RAG"""
    for i, ind in enumerate(industries):
        title = f"行业分析: {ind.get('industry', '')}"
        body = f"""行业: {ind.get('industry', '')}
核心指标: {', '.join(ind.get('key_metrics', []))}
估值方法: {ind.get('valuation_method', '')}
风险因素: {', '.join(ind.get('risk_factors', []))}
买入信号: {ind.get('entry_signal', '')}
卖出信号: {ind.get('exit_signal', '')}
典型估值区间: {ind.get('typical_range', '')}"""
        _index_document("skill", title, body, f"industry_{i}")
        index_to_chroma("skill", f"industry_{i}", title, body[:8000])
    logger.info(f"已保存 {len(industries)} 个行业框架到 RAG")


def save_psychology_to_rag(biases: list[dict]):
    """保存心理偏误到 RAG"""
    for i, bias in enumerate(biases):
        title = f"心理偏误: {bias.get('bias', '')}"
        body = f"""偏误: {bias.get('bias', '')}
表现: {bias.get('manifestation', '')}
危害: {bias.get('harm', '')}
应对: {bias.get('solution', '')}
案例: {bias.get('example', '')}"""
        _index_document("skill", title, body, f"psychology_{i}")
        index_to_chroma("skill", f"psychology_{i}", title, body[:8000])
    logger.info(f"已保存 {len(biases)} 条心理偏误到 RAG")


# ── 主函数 ──────────────────────────────────────

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="知识库批量生成")
    parser.add_argument("--all", action="store_true", help="生成全部")
    parser.add_argument("--terms", action="store_true", help="生成术语")
    parser.add_argument("--faq", action="store_true", help="生成 FAQ")
    parser.add_argument("--principles", action="store_true", help="生成原则")
    parser.add_argument("--industry", action="store_true", help="生成行业框架")
    parser.add_argument("--psychology", action="store_true", help="生成心理偏误")
    parser.add_argument("--count", type=int, default=20, help="每类生成数量")

    args = parser.parse_args()

    if args.all or args.terms:
        logger.info(f"=== 生成投资术语 ({args.count} 个) ===")
        terms = generate_terms(args.count)
        if terms:
            save_terms_to_rag(terms)
            print(f"✅ 已生成并保存 {len(terms)} 个术语")

    if args.all or args.faq:
        logger.info(f"=== 生成 FAQ ({args.count} 条) ===")
        faqs = generate_faqs(args.count)
        if faqs:
            save_faqs_to_rag(faqs)
            print(f"✅ 已生成并保存 {len(faqs)} 条 FAQ")

    if args.all or args.principles:
        logger.info(f"=== 生成投资原则 ({args.count} 条) ===")
        principles = generate_principles(args.count)
        if principles:
            save_principles_to_rag(principles)
            print(f"✅ 已生成并保存 {len(principles)} 条原则")

    if args.all or args.industry:
        logger.info(f"=== 生成行业框架 ({args.count} 个) ===")
        industries = generate_industry(args.count)
        if industries:
            save_industry_to_rag(industries)
            print(f"✅ 已生成并保存 {len(industries)} 个行业框架")

    if args.all or args.psychology:
        logger.info(f"=== 生成心理偏误 ({args.count} 条) ===")
        psychology = generate_psychology(args.count)
        if psychology:
            save_psychology_to_rag(psychology)
            print(f"✅ 已生成并保存 {len(psychology)} 条心理偏误")

    print("\n🎉 知识库扩充完成！")


if __name__ == "__main__":
    main()
