"""投资知识爬虫 — 从公开平台爬取投资文章并蒸馏

支持平台：
1. 雪球 - 投资文章、讨论
2. 知乎 - 投资问答、专栏
3. 东方财富 - 财经新闻
4. 公众号 - 投资文章（需配置）

使用方法：
1. 爬取雪球热门文章：python investment_crawler.py --xueqiu --limit 10
2. 爬取知乎投资问答：python investment_crawler.py --zhihu --limit 10
3. 爬取东方财富新闻：python investment_crawler.py --eastmoney --limit 10
4. 爬取并蒸馏：python investment_crawler.py --xueqiu --limit 5 --distill
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── 爬虫基类 ──────────────────────────────────────

class BaseCrawler:
    """爬虫基类"""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self.client = httpx.Client(headers=self.headers, timeout=30, follow_redirects=True)

    def get_page(self, url: str) -> Optional[str]:
        """获取页面内容"""
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error(f"获取页面失败 {url}: {e}")
            return None

    def close(self):
        """关闭客户端"""
        self.client.close()


# ── 雪球爬虫 ──────────────────────────────────────

class XueqiuCrawler(BaseCrawler):
    """雪球投资文章爬虫"""

    def __init__(self):
        super().__init__()
        self.base_url = "https://xueqiu.com"

    def get_hot_articles(self, limit: int = 10) -> list[dict]:
        """获取雪球热门文章"""
        articles = []

        # 雪球热门话题 API
        url = f"{self.base_url}/statuses/hot/listV2.json?since_id=-1&max_id=-1&size={limit}"

        try:
            resp = self.client.get(url)
            data = resp.json()

            for item in data.get("items", []):
                article = {
                    "title": item.get("title", ""),
                    "content": item.get("text", ""),
                    "author": item.get("user", {}).get("screen_name", ""),
                    "source": "雪球",
                    "url": f"{self.base_url}/statuses/{item.get('id', '')}",
                    "created_at": item.get("created_at", ""),
                }

                # 清理 HTML 标签
                if article["content"]:
                    soup = BeautifulSoup(article["content"], "html.parser")
                    article["content"] = soup.get_text()

                if article["title"] and article["content"]:
                    articles.append(article)

                if len(articles) >= limit:
                    break

        except Exception as e:
            logger.error(f"爬取雪球失败: {e}")

        return articles

    def search_articles(self, keyword: str, limit: int = 10) -> list[dict]:
        """搜索雪球文章"""
        articles = []

        url = f"{self.base_url}/query/v1/search/status.json?q={keyword}&count={limit}&sort=time&source=all"

        try:
            resp = self.client.get(url)
            data = resp.json()

            for item in data.get("list", []):
                article = {
                    "title": item.get("title", ""),
                    "content": item.get("description", ""),
                    "author": item.get("user", {}).get("screen_name", ""),
                    "source": "雪球",
                    "url": f"{self.base_url}/statuses/{item.get('id', '')}",
                }

                if article["content"]:
                    soup = BeautifulSoup(article["content"], "html.parser")
                    article["content"] = soup.get_text()

                if article["title"] and article["content"]:
                    articles.append(article)

        except Exception as e:
            logger.error(f"搜索雪球失败: {e}")

        return articles


# ── 知乎爬虫 ──────────────────────────────────────

class ZhihuCrawler(BaseCrawler):
    """知乎投资问答爬虫"""

    def __init__(self):
        super().__init__()
        self.base_url = "https://www.zhihu.com"

    def get_hot_questions(self, limit: int = 10) -> list[dict]:
        """获取知乎热门投资问题"""
        questions = []

        # 知乎热榜 API
        url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50"

        try:
            resp = self.client.get(url)
            data = resp.json()

            for item in data.get("data", []):
                target = item.get("target", {})
                if target.get("type") == "question":
                    question = {
                        "title": target.get("title", ""),
                        "content": target.get("excerpt", ""),
                        "source": "知乎",
                        "url": f"{self.base_url}/question/{target.get('id', '')}",
                        "answer_count": target.get("answer_count", 0),
                    }

                    # 过滤投资相关问题
                    invest_keywords = ["投资", "基金", "股票", "理财", "估值", "收益", "风险"]
                    if any(kw in question["title"] for kw in invest_keywords):
                        questions.append(question)

                if len(questions) >= limit:
                    break

        except Exception as e:
            logger.error(f"爬取知乎失败: {e}")

        return questions

    def search_questions(self, keyword: str, limit: int = 10) -> list[dict]:
        """搜索知乎问题"""
        questions = []

        url = f"{self.base_url}/api/v4/search_v3?t=general&q={keyword}&correction=1&offset=0&limit={limit}"

        try:
            resp = self.client.get(url)
            data = resp.json()

            for item in data.get("data", []):
                if item.get("type") == "search_result":
                    obj = item.get("object", {})
                    question = {
                        "title": obj.get("title", ""),
                        "content": obj.get("excerpt", ""),
                        "source": "知乎",
                        "url": f"{self.base_url}/question/{obj.get('id', '')}",
                    }

                    if question["content"]:
                        soup = BeautifulSoup(question["content"], "html.parser")
                        question["content"] = soup.get_text()

                    if question["title"]:
                        questions.append(question)

        except Exception as e:
            logger.error(f"搜索知乎失败: {e}")

        return questions


# ── 东方财富爬虫 ──────────────────────────────────────

class EastMoneyCrawler(BaseCrawler):
    """东方财富财经新闻爬虫"""

    def __init__(self):
        super().__init__()
        self.base_url = "https://finance.eastmoney.com"

    def get_news(self, limit: int = 10) -> list[dict]:
        """获取东方财富财经新闻"""
        news_list = []

        # 东方财富新闻 API - 使用更稳定的接口
        url = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_101_ajaxResult_50_1_.html"

        try:
            resp = self.client.get(url)
            # 解析 JSONP 响应
            text = resp.text
            # 提取 JSON 部分
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                for item in data.get("LivesList", [])[:limit]:
                    news = {
                        "title": item.get("title", ""),
                        "content": item.get("digest", item.get("content", "")),
                        "source": "东方财富",
                        "url": item.get("url_w", ""),
                        "published_at": item.get("showtime", ""),
                    }

                    if news["content"]:
                        soup = BeautifulSoup(news["content"], "html.parser")
                        news["content"] = soup.get_text()

                    if news["title"] and news["content"]:
                        news_list.append(news)

        except Exception as e:
            logger.error(f"爬取东方财富失败: {e}")

        return news_list


# ── 通用网页爬虫 ──────────────────────────────────────

class WebArticleCrawler(BaseCrawler):
    """通用网页文章爬虫"""

    def crawl_url(self, url: str) -> Optional[dict]:
        """爬取单个网页文章"""
        html = self.get_page(url)
        if not html:
            return None

        try:
            soup = BeautifulSoup(html, "html.parser")

            # 提取标题
            title = ""
            for tag in ["h1", "title", ".article-title", ".post-title"]:
                elem = soup.select_one(tag)
                if elem:
                    title = elem.get_text().strip()
                    break

            # 提取正文
            content = ""
            # 尝试常见的正文容器
            for selector in ["article", ".article-content", ".post-content",
                           ".content", "#content", ".main-content", "main"]:
                elem = soup.select_one(selector)
                if elem:
                    content = elem.get_text()
                    break

            # 如果没找到，用 body
            if not content:
                body = soup.find("body")
                if body:
                    content = body.get_text()

            # 清理
            content = re.sub(r'\s+', ' ', content).strip()

            if title and content and len(content) > 100:
                return {
                    "title": title,
                    "content": content[:5000],  # 限制长度
                    "source": "网页",
                    "url": url,
                }

        except Exception as e:
            logger.error(f"解析网页失败 {url}: {e}")

        return None

    def crawl_urls(self, urls: list[str]) -> list[dict]:
        """批量爬取网页"""
        articles = []
        for url in urls:
            article = self.crawl_url(url)
            if article:
                articles.append(article)
            time.sleep(1)  # 避免请求过快
        return articles


# ── 统一爬虫接口 ──────────────────────────────────────

class InvestmentCrawler:
    """投资知识爬虫统一接口"""

    def __init__(self):
        self.xueqiu = XueqiuCrawler()
        self.zhihu = ZhihuCrawler()
        self.eastmoney = EastMoneyCrawler()
        self.web = WebArticleCrawler()

    def crawl_all(self, limit: int = 10) -> list[dict]:
        """从所有平台爬取投资内容"""
        all_articles = []

        logger.info("开始爬取雪球...")
        articles = self.xueqiu.get_hot_articles(limit)
        all_articles.extend(articles)
        logger.info(f"雪球: {len(articles)} 篇")

        logger.info("开始爬取知乎...")
        questions = self.zhihu.get_hot_questions(limit)
        all_articles.extend(questions)
        logger.info(f"知乎: {len(questions)} 篇")

        logger.info("开始爬取东方财富...")
        news = self.eastmoney.get_news(limit)
        all_articles.extend(news)
        logger.info(f"东方财富: {len(news)} 篇")

        logger.info(f"总计爬取 {len(all_articles)} 篇")
        return all_articles

    def crawl_xueqiu(self, limit: int = 10) -> list[dict]:
        """爬取雪球"""
        return self.xueqiu.get_hot_articles(limit)

    def crawl_zhihu(self, limit: int = 10) -> list[dict]:
        """爬取知乎"""
        return self.zhihu.get_hot_questions(limit)

    def crawl_eastmoney(self, limit: int = 10) -> list[dict]:
        """爬取东方财富"""
        return self.eastmoney.get_news(limit)

    def crawl_url(self, url: str) -> Optional[dict]:
        """爬取单个网页"""
        return self.web.crawl_url(url)

    def crawl_urls(self, urls: list[str]) -> list[dict]:
        """批量爬取网页"""
        return self.web.crawl_urls(urls)

    def search(self, keyword: str, platform: str = "all", limit: int = 10) -> list[dict]:
        """搜索投资内容"""
        results = []

        if platform in ("all", "xueqiu"):
            articles = self.xueqiu.search_articles(keyword, limit)
            results.extend(articles)

        if platform in ("all", "zhihu"):
            questions = self.zhihu.search_questions(keyword, limit)
            results.extend(questions)

        return results

    def close(self):
        """关闭所有爬虫"""
        self.xueqiu.close()
        self.zhihu.close()
        self.eastmoney.close()
        self.web.close()


# ── 蒸馏集成 ──────────────────────────────────────

def distill_and_save(articles: list[dict], source: str = "crawler"):
    """爬取内容并蒸馏保存到 RAG"""
    from scripts.distill_knowledge import distill_content, save_to_rag

    for article in articles:
        content = article.get("content", "")
        title = article.get("title", "")

        if not content or len(content) < 100:
            continue

        logger.info(f"蒸馏: {title[:50]}...")

        # 蒸馏投资原则
        principles = distill_content(content, "article")
        if principles:
            save_to_rag(principles, "principle", f"{source}_{title[:20]}")

        time.sleep(1)  # 避免请求过快


# ── 主函数 ──────────────────────────────────────

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="投资知识爬虫")
    parser.add_argument("--xueqiu", action="store_true", help="爬取雪球")
    parser.add_argument("--zhihu", action="store_true", help="爬取知乎")
    parser.add_argument("--eastmoney", action="store_true", help="爬取东方财富")
    parser.add_argument("--all", action="store_true", help="爬取所有平台")
    parser.add_argument("--search", type=str, help="搜索关键词")
    parser.add_argument("--platform", type=str, default="all", help="指定平台")
    parser.add_argument("--limit", type=int, default=10, help="爬取数量")
    parser.add_argument("--distill", action="store_true", help="爬取后自动蒸馏")
    parser.add_argument("--output", type=str, help="输出文件路径")

    args = parser.parse_args()

    crawler = InvestmentCrawler()

    try:
        articles = []

        if args.search:
            # 搜索模式
            logger.info(f"搜索: {args.search}")
            articles = crawler.search(args.search, args.platform, args.limit)
        elif args.xueqiu:
            articles = crawler.crawl_xueqiu(args.limit)
        elif args.zhihu:
            articles = crawler.crawl_zhihu(args.limit)
        elif args.eastmoney:
            articles = crawler.crawl_eastmoney(args.limit)
        elif args.all:
            articles = crawler.crawl_all(args.limit)
        else:
            parser.print_help()
            return

        logger.info(f"共爬取 {len(articles)} 篇")

        # 输出到文件
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(articles, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存到 {args.output}")

        # 蒸馏
        if args.distill and articles:
            logger.info("开始蒸馏...")
            distill_and_save(articles)
            logger.info("蒸馏完成")

        # 打印摘要
        for i, article in enumerate(articles[:5], 1):
            print(f"\n{i}. [{article.get('source', '')}] {article.get('title', '')[:60]}")
            print(f"   {article.get('content', '')[:80]}...")

    finally:
        crawler.close()


if __name__ == "__main__":
    main()
