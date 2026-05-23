"""测试脚本 — 验证公众号文章抓取 + LLM 分析"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))
from article_reader import fetch_article, extract_stock_codes
from llm_service import analyze_article

TEST_URL = "https://mp.weixin.qq.com/s/PWG2iJ0y7-WNH7rp1enJKw"


def test_fetch_article():
    """测试文章抓取。"""
    print("=" * 60)
    print("1. 测试文章抓取")
    print("=" * 60)

    article = fetch_article(TEST_URL)

    print(f"  标题: {article['title']}")
    print(f"  作者: {article['author']}")
    print(f"  发布时间: {article['publish_time']}")
    print(f"  图片数: {article['image_count']}")
    print(f"  正文长度: {len(article['content_text'])} 字符")
    print(f"  正文前 300 字:")
    print(f"  {article['content_text'][:300]}")
    print()

    assert article["title"], "标题不能为空"
    assert len(article["content_text"]) > 50, "正文太短"

    return article


def test_extract_codes(article):
    """测试股票代码提取。"""
    print("=" * 60)
    print("2. 测试股票代码提取")
    print("=" * 60)

    codes = extract_stock_codes(article["content_text"])
    print(f"  识别到的代码: {codes}")
    print()
    return codes


def test_llm_analysis(article, codes):
    """测试 LLM 分析。"""
    print("=" * 60)
    print("3. 测试 LLM 分析")
    print("=" * 60)

    from config import get_llm_config, LLM_PROVIDER
    api_key, base_url, model = get_llm_config()

    if not api_key:
        print("  [SKIP] 未配置 API Key，跳过 LLM 测试")
        print(f"  请在 .env 中配置 {'MIMO_API_KEY' if LLM_PROVIDER == 'mimo' else 'DEEPSEEK_API_KEY'}")
        return

    print(f"  使用模型: {LLM_PROVIDER} / {model}")
    print(f"  正在调用 LLM 分析文章...")

    market_data = None
    if codes:
        market_data = json.dumps({"识别到的代码": codes}, ensure_ascii=False)

    result = analyze_article(
        title=article["title"],
        content=article["content_text"],
        market_data=market_data,
    )

    print()
    print("  LLM 分析结果:")
    print("-" * 40)
    print(result)
    print("-" * 40)

    assert len(result) > 100, "LLM 输出太短"
    return result


def main():
    print("\n投资分析助手 — 功能测试\n")

    article = test_fetch_article()
    codes = test_extract_codes(article)
    test_llm_analysis(article, codes)

    print("\n全部测试通过!")


if __name__ == "__main__":
    main()
