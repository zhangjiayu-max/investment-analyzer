"""L1-资本开支公告 Provider — 龙头公司重大项目/扩产/投资协议公告。

领先性：强领先（3-12 月）
数据源：akshare ak.stock_notice_report（巨潮资讯网公告）
逻辑：
  1. 拉取近 N 天"重大项目投资/扩产/投资协议"类公告
  2. 关键词过滤（投资/扩产/产能/基地/项目/签约等）
  3. 板块映射 + 方向判断（positive 扩产 / negative 缩减）
  4. 金额提取（正则匹配"XX亿元"）
"""
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 资本开支关键词（标题/摘要命中其一即可）
_CAPEX_KEYWORDS = [
    "投资", "扩产", "产能", "基地", "项目", "签约", "投产",
    "开工", "建设", "落户", "合作", "协议", "框架",
    "增资", "设子公司", "设立", "收购",
]

# 缩减/负面关键词
_NEGATIVE_KEYWORDS = ["缩减", "关停", "出售", "退出", "注销", "破产", "裁员"]

# 金额提取正则
_AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*亿[元币]")


class CapexProvider:
    """资本开支公告 Provider。"""

    @property
    def provider_name(self) -> str:
        return "capex"

    @property
    def leading_level(self) -> str:
        return "strong"

    def fetch(self, lookback_days: int = 7) -> list:
        """拉取近 N 天资本开支公告。"""
        from services.market.leading_indicators.base import LeadingSignal
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

        try:
            import akshare as ak
        except ImportError:
            logger.warning("[capex] akshare 未安装")
            return []

        # ak.stock_notice_report 接口参数：symbol(股票代码/全部), date(公告日期)
        # 拉取最近 lookback_days 天的公告
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)

        signals = []
        # 按天拉取（接口不支持日期范围，需逐天拉取或拉取最新）
        # 实际策略：拉取最新公告，过滤日期
        try:
            df = call_akshare_with_timeout(
                ak.stock_notice_report,
                symbol="全部",
                date=end_date.strftime("%Y%m%d"),
                timeout=15,
            )
            if df is None or df.empty:
                # 尝试前一天
                prev_date = end_date - timedelta(days=1)
                df = call_akshare_with_timeout(
                    ak.stock_notice_report,
                    symbol="全部",
                    date=prev_date.strftime("%Y%m%d"),
                    timeout=15,
                )
        except Exception as e:
            logger.warning(f"[capex] stock_notice_report 调用失败: {e}")
            return []

        if df is None or df.empty:
            logger.info("[capex] 无公告数据")
            return []

        # 标准化列名
        df.columns = [str(c).strip() for c in df.columns]

        for _, row in df.iterrows():
            try:
                title = str(row.get("标题", "") or row.get("title", "") or "")
                # 只处理含资本开支关键词的公告
                if not any(kw in title for kw in _CAPEX_KEYWORDS):
                    continue

                # 日期解析
                raw_date = str(row.get("公告日期", "") or row.get("date", "") or "")
                pub_date = self._parse_date(raw_date)
                if not pub_date:
                    continue
                # 过滤超出回看范围的
                pub_dt = datetime.strptime(pub_date, "%Y-%m-%d")
                if pub_dt < start_date:
                    continue

                # 代码和名称
                code = str(row.get("代码", "") or row.get("股票代码", "") or "")
                name = str(row.get("名称", "") or row.get("股票简称", "") or "")

                # 金额提取
                amount_match = _AMOUNT_PATTERN.search(title)
                amount_str = f"{amount_match.group(1)}亿元" if amount_match else ""

                # 方向判断
                direction = "positive"
                if any(kw in title for kw in _NEGATIVE_KEYWORDS):
                    direction = "negative"

                # 板块映射
                full_text = f"{title} {name}"
                sectors = self.map_to_sectors(full_text)

                summary = f"{name}({code}) {title}"
                if amount_str:
                    summary += f" 投资金额: {amount_str}"

                signals.append(LeadingSignal(
                    signal_type="capex_announcement",
                    leading_level=self.leading_level,
                    title=title[:200],
                    summary=summary,
                    source_url=f"https://www.cninfo.com.cn/new/disclosure/detail?stockCode={code}" if code else "",
                    publish_date=pub_date,
                    affected_sectors=sectors,
                    affected_themes=[],
                    direction=direction,
                    confidence=0.7,
                    raw_data={
                        "stock_code": code,
                        "stock_name": name,
                        "amount": amount_str,
                    },
                    metric_value=float(amount_match.group(1)) if amount_match else None,
                    metric_unit="亿元",
                ))
            except Exception as e:
                logger.debug(f"[capex] 解析公告行失败: {e}")
                continue

        # 限制单次最多 30 条
        signals = signals[:30]
        logger.info(f"[capex] 抓取 {len(signals)} 条资本开支公告")
        return signals

    def _parse_date(self, raw: str) -> str:
        """解析公告日期字符串为 YYYY-MM-DD。"""
        if not raw:
            return ""
        raw = raw.strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        # 尝试截取前10位
        if len(raw) >= 10:
            return raw[:10]
        return ""

    def map_to_sectors(self, text: str) -> list:
        """复用基类板块映射。"""
        from services.market.leading_indicators.base import LeadingIndicatorProvider
        return LeadingIndicatorProvider.map_to_sectors(self, text)
