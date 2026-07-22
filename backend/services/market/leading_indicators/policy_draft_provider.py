"""L1-政策征求意见稿 Provider — 发改委/工信部/证监会政策征求意见。

领先性：强领先（6-24 月）
数据源：发改委/工信部/证监会官网"政策征求意见"栏目 web 抓取
逻辑：
  1. 抓取发改委/工信部/证监会政策征求意见页面
  2. 关键词过滤（芯片/新能源/AI/储能/半导体等产业相关）
  3. 板块映射 + 方向判断
  4. 容错处理：页面结构变更时不崩溃，降级返回空列表

注意：官网可能反爬，使用 requests + 随机 UA + 超时保护。
"""
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 政策征求意见栏目 URL
_POLICY_URLS = [
    # 发改委 — 政策征求意见
    {
        "name": "发改委",
        "url": "https://www.ndrc.gov.cn/xxgk/zcfb/zhuyi/",
        "encoding": "utf-8",
    },
    # 工信部 — 政策文件征求意见
    {
        "name": "工信部",
        "url": "https://www.miit.gov.cn/jgsj/zfs/zhengce/index.html",
        "encoding": "utf-8",
    },
    # 证监会 — 征求意见
    {
        "name": "证监会",
        "url": "http://www.csrc.gov.cn/pub/newsite/zjhxwcfzcqyj/",
        "encoding": "utf-8",
    },
]

# 产业政策关键词（标题命中其一即可）
_INDUSTRY_KEYWORDS = [
    "芯片", "半导体", "集成电路", "人工智能", "AI", "算力", "大模型",
    "新能源", "光伏", "锂电", "储能", "电池", "充电桩",
    "机器人", "军工", "航天", "航空",
    "医药", "医疗", "生物",
    "数据", "数字", "算力", "5G", "6G",
    "汽车", "智能驾驶",
    "化工", "材料", "有色",
]

# 正面方向关键词
_POSITIVE_KEYWORDS = ["支持", "促进", "发展", "鼓励", "补贴", "扶持", "推动", "加快"]
# 负面方向关键词
_NEGATIVE_KEYWORDS = ["限制", "禁止", "淘汰", "收紧", "规范", "整顿", "整改"]


class PolicyDraftProvider:
    """政策征求意见稿 Provider。"""

    @property
    def provider_name(self) -> str:
        return "policy_draft"

    @property
    def leading_level(self) -> str:
        return "strong"

    def fetch(self, lookback_days: int = 7) -> list:
        """拉取近 N 天政策征求意见稿。"""
        from services.market.leading_indicators.base import LeadingSignal

        try:
            import requests
        except ImportError:
            logger.warning("[policy_draft] requests 未安装")
            return []

        all_items = []
        for source in _POLICY_URLS:
            try:
                items = self._fetch_one_source(source, requests, lookback_days)
                all_items.extend(items)
            except Exception as e:
                logger.warning(f"[policy_draft] 抓取 {source['name']} 失败: {e}")
                continue

        # 按日期降序
        all_items.sort(key=lambda x: x.publish_date, reverse=True)
        # 限制最多 20 条
        all_items = all_items[:20]
        logger.info(f"[policy_draft] 抓取 {len(all_items)} 条政策征求意见稿")
        return all_items

    def _fetch_one_source(self, source: dict, requests, lookback_days: int) -> list:
        """抓取单个来源的政策征求意见列表。"""
        from services.market.leading_indicators.base import LeadingSignal

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        try:
            resp = requests.get(source["url"], headers=headers, timeout=15, verify=False)
            resp.encoding = source.get("encoding", "utf-8")
            if resp.status_code != 200:
                logger.debug(f"[policy_draft] {source['name']} 返回 {resp.status_code}")
                return []
        except Exception as e:
            logger.debug(f"[policy_draft] {source['name']} 请求失败: {e}")
            return []

        # 提取标题+链接+日期
        items = self._parse_html(resp.text, source)

        # 日期过滤
        start_date = datetime.now() - timedelta(days=lookback_days)
        result = []
        for item in items:
            title = item.get("title", "")
            url = item.get("url", "")
            pub_date = item.get("date", "")

            # 日期解析
            parsed_date = self._parse_date(pub_date)
            if not parsed_date:
                continue
            try:
                pub_dt = datetime.strptime(parsed_date, "%Y-%m-%d")
                if pub_dt < start_date:
                    continue
            except ValueError:
                continue

            # 产业关键词过滤
            if not any(kw in title for kw in _INDUSTRY_KEYWORDS):
                continue

            # 方向判断
            direction = "neutral"
            if any(kw in title for kw in _POSITIVE_KEYWORDS):
                direction = "positive"
            elif any(kw in title for kw in _NEGATIVE_KEYWORDS):
                direction = "negative"

            # 板块映射
            sectors = self.map_to_sectors(title)

            result.append(LeadingSignal(
                signal_type="policy_draft",
                leading_level=self.leading_level,
                title=title[:200],
                summary=f"{source['name']}征求意见: {title}",
                source_url=url,
                publish_date=parsed_date,
                affected_sectors=sectors,
                affected_themes=[],
                direction=direction,
                confidence=0.8,  # 政策草案置信度较高
                raw_data={"source": source["name"]},
            ))

        return result

    def _parse_html(self, html: str, source: dict) -> list:
        """解析 HTML 提取标题+链接+日期。容错处理，结构变更返回空列表。"""
        items = []
        # 通用正则：提取 <a href="...">标题</a> 和附近日期
        # 匹配模式：<a ... href="xxx" ...>标题</a> ... 日期
        try:
            # 方案1：提取所有 <li> 或 <tr> 中的链接+文本
            link_pattern = re.compile(
                r'<a[^>]*href="([^"]*)"[^>]*>([^<]+)</a>',
                re.IGNORECASE,
            )
            date_pattern = re.compile(
                r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})',
            )

            # 找到所有链接
            links = link_pattern.findall(html)
            # 找到所有日期
            dates = date_pattern.findall(html)

            # 简化匹配：链接和日期按顺序对应
            for i, (url, title) in enumerate(links):
                title = title.strip()
                if not title or len(title) < 4:
                    continue
                # 跳过非内容链接（导航/图片等）
                if any(skip in title for skip in ["首页", "下一页", "上一页", "更多", "登录", "注册"]):
                    continue
                # 补全 URL
                if url.startswith("/"):
                    # 从 source url 提取域名
                    from urllib.parse import urlparse
                    parsed = urlparse(source["url"])
                    url = f"{parsed.scheme}://{parsed.netloc}{url}"
                elif not url.startswith("http"):
                    continue

                # 日期：尝试匹配同位置
                date_str = dates[i] if i < len(dates) else ""

                items.append({"title": title, "url": url, "date": date_str})

        except Exception as e:
            logger.debug(f"[policy_draft] HTML 解析失败 {source['name']}: {e}")

        return items

    def _parse_date(self, raw: str) -> str:
        """解析日期字符串为 YYYY-MM-DD。"""
        if not raw:
            return ""
        raw = raw.strip()
        # 处理中文日期
        raw = raw.replace("年", "-").replace("月", "-").replace("日", "")
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""

    def map_to_sectors(self, text: str) -> list:
        """复用基类板块映射。"""
        from services.market.leading_indicators.base import LeadingIndicatorProvider
        return LeadingIndicatorProvider.map_to_sectors(self, text)
